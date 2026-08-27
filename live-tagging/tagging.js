// Match-day tagging, backed by Firestore.
//
// Two constraints shape this file:
//
//   * It has to work with no connectivity. Every write is a batch (which queues
//     offline) rather than a transaction (which fails offline), and undo works
//     by direct document reference so it needs no query.
//   * A tag has to be unambiguous a month later. "Corner, us" does not say
//     whether we won it or conceded it, so the sheet asks the question in the
//     wording that belongs to that event type — awarded to, committed by,
//     shown to, scored by — rather than showing a bare team toggle.
//
// Ordering never uses createdAt: serverTimestamp() reads as null locally until
// acknowledged and then resolves to sync time, not tap time.

import { onUser, signIn, resolveAccess, configWarning } from '../assets/auth.js?v=106';
import {
    listMatches, getMatch, listPlayers, setLineup, listMatchRoster, listLog,
    writeEvent, writePeriod, writeSubstitution, undoEntry, watchSync,
    logId, PERIOD_STATUS,
} from '../assets/db.js?v=106';
import {
    EVENTS, CARD_COLOURS, describeEvent, timelineTone, PERIOD_LABELS,
} from '../assets/events.js?v=106';
import { syncState, safeToClose } from '../assets/report.js?v=106';
import { mountPitchBackdrop } from '../assets/pitch-backdrop.js?v=106';
import { byId, toast, clockText, timelineRow, tally, groupHead }
    from '../assets/ui.js?v=106';

/** Stable per-device id, so two taggers cannot collide on log document ids. */
function deviceId() {
    let id = localStorage.getItem('pitchiq.deviceId');
    if (!id) {
        id = Math.random().toString(36).slice(2, 8);
        localStorage.setItem('pitchiq.deviceId', id);
    }
    return id;
}

const state = {
    user: null,
    teamId: null,
    teamName: 'Us',
    opponentName: 'Them',
    matchId: null,
    match: null,
    // Every squad this account coaches; the picker appears when there is more
    // than one.
    teams: [],
    players: [],
    roster: [],
    device: deviceId(),
    seq: 0,
    myEntries: [],
    kickoffAt: null,
    clockOffset: 0,
    running: false,
    score: { us: 0, them: 0 },
    // The event currently being described in the sheet.
    draft: null,
    sub: { outId: null, inId: null },
    // What Firestore says has actually reached the server, and the listener
    // saying it. A null count is "not asked yet" rather than "nothing queued":
    // there can be writes sitting in IndexedDB from a session that ended badly,
    // and opening on a confident zero would say they were safe.
    sync: { pending: null, fromCache: true, total: 0 },
    stopSync: null,
};

// ---------------------------------------------------------------- ui

/**
 * Setup, kickoff and live are full-screen states rather than sections of a
 * scrolling page — a tagger glancing down from the pitch has to find the same
 * buttons in the same places every time.
 */
function showView(name) {
    document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
    byId(`view-${name}`).classList.add('active');
}

const teamName = (side) => (side === 'them' ? state.opponentName : state.teamName);

function matchClock() {
    if (!state.running) return state.clockOffset;
    return state.clockOffset + (Date.now() - state.kickoffAt) / 1000;
}

// ---------------------------------------------------------------- clock

// Matches the ceiling firestore.rules puts on matchClockS.
const MAX_CLOCK_S = 12000;

/** Is the ball supposed to be in play? Half-time and full time are not. */
const inPlay = () => ['first_half', 'second_half'].includes(state.match?.status);

/**
 * Start or stop the clock, keeping the elapsed reading continuous.
 *
 * Every tag is stamped with matchClock(), so a stopped clock stamps everything
 * with the same second. That is why the paused state is shouted about below
 * rather than left to a toast that scrolls away.
 */
function setClockRunning(running) {
    state.clockOffset = matchClock();
    state.kickoffAt = Date.now();
    state.running = running;
    renderClockChrome();
}

function setClock(seconds) {
    const clamped = Math.max(0, Math.min(MAX_CLOCK_S, seconds));
    state.clockOffset = clamped;
    state.kickoffAt = Date.now();
    renderClockChrome();
}

const adjustClock = (delta) => setClock(matchClock() + delta);

/**
 * Make a stopped clock impossible to miss while the half is running.
 *
 * The dangerous case is not a deliberate pause — it is coming back after a
 * reload, where the clock looks like an ordinary clock that happens to read
 * 34:12, and stays reading 34:12 for the rest of the half.
 */
function renderClockChrome() {
    const stalled = inPlay() && !state.running;
    byId('btn-clock').classList.toggle('stalled', stalled);
    byId('clock-state').classList.toggle('hidden', !stalled);

    const toggle = byId('btn-clock-toggle');
    if (toggle) toggle.textContent = state.running ? 'Pause the clock' : 'Start the clock';

    // Redraw immediately rather than waiting for the next tick. A quarter of a
    // second of nothing after tapping "+1 min" reads as a dead button, and the
    // reflex is to tap it again.
    paintClock();
}

function paintClock() {
    const text = clockText(matchClock());
    byId('clock').textContent = text;
    byId('clock-big').textContent = text;
}

function openClockSheet() {
    renderClockChrome();
    byId('overlay-clock').classList.add('open');
}

setInterval(() => {
    if (byId('view-live').classList.contains('active')) paintClock();
}, 250);

/**
 * The sync chip, from what Firestore reports rather than what the browser
 * guesses.
 *
 * This used to be `navigator.onLine` and a tooltip, and both halves were wrong
 * for the place this tool is used. `onLine` reports a link rather than a
 * reachable server, so a school Wi-Fi with a captive portal reads as connected
 * and the dot said "Saved" while nothing had been saved. And a tooltip on a
 * tablet is a tooltip nobody can open — the count has to be on the screen.
 *
 * Quiet when everything is up: a nine-pixel green dot and no words. The chip
 * only grows text when there is something to say, so the words appearing is
 * itself the signal.
 */
function updateSyncIndicator() {
    const chip = byId('sync-dot');
    if (!chip) return;

    const view = syncState({ ...state.sync, online: navigator.onLine });
    chip.className = `sync-chip is-${view.tone}`;
    chip.title = view.detail;

    let label = chip.querySelector('b');
    if (!label) {
        chip.innerHTML = '<i></i><b></b>';
        label = chip.querySelector('b');
    }
    label.textContent = view.tone === 'ok' ? '' : view.label;
}

// ------------------------------------------------------------- tally rail

/**
 * What the rail shows, in the order it shows it.
 *
 * Goals are not here — they are the score, in 40px digits at the top of the
 * same screen, and a second copy of a number that large would only ever be a
 * chance for the two to disagree. `out_of_bounds` is not here either: it is the
 * generic restart, and every specific one of them (corner, throw-in, goal kick)
 * already has its own row.
 *
 * The second value is `better`, which decides which side — if either —
 * gets coloured as ahead, and it is not a style choice. It comes straight out
 * of what each type’s `sideMeans` in events.js says picking a team asserts:
 *
 *   foul     "Committed by"    — so fewer is better        -> low
 *   card     "Shown to"        — so fewer is better        -> low
 *   offside  "Called against"  — so fewer is better        -> low
 *   corner   "Awarded to"      — a corner won is an attack -> high
 *   free kick "Awarded to"     — likewise                  -> high
 *
 * The last two get `null`, meaning no verdict, and that is the honest answer
 * rather than a gap. A throw-in awarded to us says only that somebody put the
 * ball out, and at this level that is nearer a coin toss than a claim. A goal
 * kick awarded to us is worse than neutral if anything — it means the ball
 * went dead behind our own line — so calling it a lead for us would be
 * plainly backwards, and calling it a lead for them reads as a judgement
 * nobody measured. The counts are still shown. Only the colour is withheld.
 */
const TALLY_GROUPS = [
    { title: 'Discipline', rows: [['foul', 'low'], ['card', 'low'], ['offside', 'low']] },
    { title: 'Set pieces', rows: [['corner', 'high'], ['free_kick', 'high'],
                                  ['throw_in', null], ['goal_kick', null]] },
];

/** type -> the row element currently on screen for it. */
let tallyRows = null;
/** type -> the counts that row was drawn from, as 'us:them'. */
let tallyShown = {};

const tallyTypes = () => TALLY_GROUPS.flatMap((g) => g.rows);

/** Empty rows and headings, once, so later updates are a swap and not a rebuild. */
function buildTallyRail() {
    byId('tally-us').textContent = state.teamName || 'Us';
    byId('tally-them').textContent = state.opponentName || 'Them';

    const list = byId('tally-rows');
    list.textContent = '';
    tallyRows = new Map();
    tallyShown = {};

    for (const group of TALLY_GROUPS) {
        list.append(groupHead(group.title));
        for (const [type, better] of group.rows) {
            const row = tally(EVENTS[type].label, 0, 0, better);
            list.append(row);
            tallyRows.set(type, row);
            tallyShown[type] = '0:0';
        }
    }
}

/** Count the tracked types out of a log, per side. */
function countTally(entries) {
    const counts = {};
    for (const [type] of tallyTypes()) counts[type] = { us: 0, them: 0 };
    for (const entry of entries) {
        if (entry.kind !== 'event') continue;
        const row = counts[entry.type];
        if (!row) continue;
        row[entry.side === 'them' ? 'them' : 'us'] += 1;
    }
    return counts;
}

/**
 * Redraw the rail from a log, and only the rows that moved.
 *
 * The "only" matters twice. A bar redrawing replays `bar-grow`, so leaving it
 * to rebuild all seven would mean every tag animated every count on the
 * screen, which says "seven things happened" when one did. Kept to the row
 * that changed, the same animation becomes the feedback: the length you just
 * altered is the length that draws itself.
 *
 * Second, `tally()` is the report’s component, not a copy of it. The bars a
 * coach reads at half-time and the bars the tagger watches during the half are
 * the same function over the same log, so there is no version of this where
 * the touchline and the report disagree about how many fouls there were.
 */
function renderTally(entries) {
    if (!tallyRows) buildTallyRail();
    const counts = countTally(entries);

    for (const [type, better] of tallyTypes()) {
        const seen = `${counts[type].us}:${counts[type].them}`;
        if (tallyShown[type] === seen) continue;
        tallyShown[type] = seen;

        const row = tally(EVENTS[type].label, counts[type].us, counts[type].them, better);
        tallyRows.get(type).replaceWith(row);
        tallyRows.set(type, row);
    }
}

/** Follow one match's log until the page moves on. */
function watchMatchSync() {
    buildTallyRail();
    state.stopSync?.();
    state.stopSync = watchSync(state.teamId, state.matchId, (next) => {
        state.sync = next;
        updateSyncIndicator();

        // Absent, not empty, when the listener has failed — see `watchSync`.
        // A dead listener knows nothing about what was tagged, and redrawing
        // the rail to zeroes would be the tool asserting the half never
        // happened. Leave the last true counts on the screen.
        if (next.entries) renderTally(next.entries);
    });
}

// `fromCache` is the authority and these are only a hint, but they arrive
// faster than a listener notices — a tablet that goes into a dead spot should
// say so immediately rather than at the next snapshot.
window.addEventListener('online', updateSyncIndicator);
window.addEventListener('offline', updateSyncIndicator);

const nextSeq = () => ++state.seq;

/**
 * Send a write and stop waiting for the server.
 *
 * **This is the rule the whole tool turns on, and it was broken in five
 * places.** Firestore's offline cache holds the write the moment it is issued
 * and replays it whenever the connection returns; the promise is about the
 * *server*, which on a touchline is routinely minutes away and sometimes a bus
 * ride away. Every handler here used to `await` that promise before touching
 * the screen, so in a dead spot:
 *
 *   * "Just recorded" kept showing the last tag made while online, and the tag
 *     you had just made looked like it had not registered — so you tapped it
 *     again;
 *   * nothing reached `state.myEntries`, so **Undo could not reach anything
 *     tagged offline** and would remove something from before the outage;
 *   * the substitution sheet never closed and its Confirm button stayed live —
 *     measured, three presses queued three substitutions;
 *   * and kick-off did nothing at all: no clock, no view change, on the one tap
 *     the whole match hangs off.
 *
 * The sync chip is the honest answer to "has the server got this", it is on
 * screen at all times, and it was already telling the truth throughout — see
 * `updateSyncIndicator`. Nothing else needs to wait.
 *
 * `undo` runs only when a write is genuinely **rejected** — malformed data or a
 * rules failure — which is a correction rather than a delay, and worth
 * shouting about, because until it runs there is something on the screen that
 * is not in the record.
 */
function sendWrite(promise, { undo, message } = {}) {
    promise.catch((err) => {
        undo?.();
        toast(`${message || 'That did not save'} — ${err?.message || 'the server refused it'}`, true);
    });
}

function renderScore() {
    byId('score-us').textContent = state.score.us;
    byId('score-them').textContent = state.score.them;
}

function setLast(text, fresh = true) {
    const el = byId('last-event');
    el.textContent = text;
    el.classList.toggle('fresh', fresh);
}

// ---------------------------------------------------------------- setup

/**
 * Which squad is being tagged. Shown only when the account coaches more than
 * one — with a single squad there is nothing to decide.
 */
function renderTeamPicker() {
    const wrap = byId('team-cards');
    wrap.innerHTML = '';

    for (const team of state.teams) {
        const card = document.createElement('button');
        card.className = 'match-card';
        card.innerHTML = `
            <div class="grow">
                <div class="vs">squad</div>
                <div class="opp"></div>
            </div>
            <span class="pill">choose</span>`;
        card.querySelector('.opp').textContent = team.name || 'Unnamed squad';
        card.addEventListener('click', () => {
            chooseTeam(team).catch((err) =>
                toast(err.message || 'Could not load that squad.', true));
        });
        wrap.appendChild(card);
    }
}

async function chooseTeam(team) {
    state.teamId = team.id;
    state.teamName = team.name || 'Us';
    byId('setup-sub').textContent = state.teamName;

    byId('team-block').classList.add('hidden');
    byId('match-block').classList.remove('hidden');
    byId('btn-change-team').classList.toggle('hidden', state.teams.length < 2);

    await loadMatches();
}

function backToTeamPicker() {
    byId('match-block').classList.add('hidden');
    byId('team-block').classList.remove('hidden');
    state.teamId = null;
    state.matchId = null;
    state.match = null;
}

async function loadMatches() {
    const matches = (await listMatches(state.teamId)).filter((m) => !m.finalized);
    const wrap = byId('match-cards');
    wrap.innerHTML = '';

    if (!matches.length) {
        wrap.innerHTML =
            '<div class="empty">No open matches.<br>'
            + '<span class="muted">Create one in the coach dashboard first.</span><br><br>'
            + '<a class="btn small" href="../coach/">Open coach dashboard</a></div>';
        return;
    }

    for (const match of matches) {
        const inProgress = ['first_half', 'halftime', 'second_half'].includes(match.status);

        const card = document.createElement('button');
        card.className = `match-card ${inProgress ? 'resumable' : ''}`;
        card.innerHTML = `
            <div class="grow">
                <div class="vs">versus</div>
                <div class="opp"></div>
                <div class="when"></div>
            </div>
            <span class="pill"></span>`;

        card.querySelector('.opp').textContent = match.opponentName || '—';
        card.querySelector('.when').textContent = match.date || 'no date';

        const pill = card.querySelector('.pill');
        if (inProgress) {
            pill.textContent = 'resume';
            pill.classList.add('live');
        } else {
            pill.textContent = 'new';
        }

        card.addEventListener('click', () => {
            onMatchChosen(match.id).catch((err) =>
                toast(err.message || 'Could not load that match.', true));
        });

        wrap.appendChild(card);
    }
}

async function onMatchChosen(matchId) {
    if (!matchId) return;
    state.matchId = matchId;
    byId('link-report').href =
        `../halftime/?team=${encodeURIComponent(state.teamId)}`
        + `&match=${encodeURIComponent(matchId)}`;

    const [match, players, roster] = await Promise.all([
        getMatch(state.teamId, matchId),
        listPlayers(state.teamId),
        listMatchRoster(state.teamId, matchId),
    ]);

    state.match = match;
    state.opponentName = match.opponentName || 'Them';
    // Anyone who has left the team is not offered for a lineup. Their past
    // matches are untouched — this is about who can be named in the next one,
    // and a leaver in the starters list is a mis-tap waiting to happen at the
    // one moment nobody has time to check.
    state.players = players.filter((p) => p.active !== false);
    state.roster = roster;

    if (roster.length) {
        // A lineup already saved. Put it back in the picker before resuming,
        // so that the screen behind kick-off is the eleven this match actually
        // has rather than the match list. Until this line the picker painted
        // from `player._starter` alone — an in-memory flag set by tapping —
        // and a reload lost it: the XI was safe in Firestore and unreachable
        // on the tablet, on a screen that promises "you can change this
        // later". A coach who spotted a wrong name after a reload had no way
        // back to it, and the only remaining move was to kick off and
        // substitute at the first second of the match.
        //
        // This is the one reader `isStarter` has, and the reason it is not
        // derivable from `stints`: that workaround writes a stint opening at
        // zero for a player who never started, which is indistinguishable from
        // a starter's.
        if (scheduled()) restoreLineup(roster);
        await resumeMatch();
        return;
    }

    byId('match-picker').classList.add('hidden');
    byId('lineup-block').classList.remove('hidden');
    renderLineupPicker();
}

/** Before the whistle, and only then, is the lineup still a lineup. */
const scheduled = () => (state.match?.status || 'scheduled') === 'scheduled';

/**
 * Repaint the picker from the saved roster.
 *
 * Guarded by `scheduled()` at every call site, and it has to be: after
 * kick-off `stints` is a record of who was on the pitch and when, and a
 * re-save would flatten it back to one stint opening at zero. The picker is
 * unreachable once the match is live, so this is a guard against a future
 * caller rather than against a button that exists today.
 */
function restoreLineup(roster) {
    const starters = new Set(roster.filter((r) => r.isStarter).map((r) => r.id));
    for (const player of state.players) player._starter = starters.has(player.id);

    byId('match-picker').classList.add('hidden');
    byId('lineup-block').classList.remove('hidden');
    renderLineupPicker();
}

function backToMatchPicker() {
    byId('lineup-block').classList.add('hidden');
    byId('match-picker').classList.remove('hidden');
    state.matchId = null;
    state.match = null;
    for (const p of state.players) delete p._starter;
}

/**
 * One tappable player, in any of this tool's five pickers.
 *
 * A real `<button>` inside the `<li>`, not a click handler on the `<li>`. Every
 * one of these lists was a styled list item with a listener on it: not
 * focusable, not reachable from a keyboard, and announced by a screen reader as
 * a line of text rather than as something you can press. `assets/timeline.js`
 * states the opposite standard for its own marks — "real buttons rather than
 * styled spans, so the whole thing is reachable from a keyboard" — and the tool
 * every number in this system comes from was the one place that did not follow
 * it.
 *
 * `toggle` is the difference between the two kinds of picker here. Choosing a
 * starter or the player coming off is a state you can change your mind about,
 * so it is `aria-pressed`; choosing the scorer closes the step, so it is a
 * plain button and a pressed state would be a lie about a control that is
 * already gone.
 */
function pickRow({ number, name, tag = '', toggle = false, onPick }) {
    const li = document.createElement('li');

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'pick';
    if (toggle) button.setAttribute('aria-pressed', 'false');

    const num = document.createElement('span');
    num.className = 'num';
    num.textContent = number ?? '—';

    const nm = document.createElement('span');
    nm.className = 'nm';
    nm.textContent = name;

    const badge = document.createElement('span');
    badge.className = 'tag';
    badge.textContent = tag;

    button.append(num, nm, badge);
    button.addEventListener('click', () => onPick?.());
    li.append(button);

    return { li, button, badge };
}

/** A list with nothing in it, said in the list rather than beside it. */
function emptyRow(text) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = text;
    return li;
}

function renderLineupPicker() {
    const list = byId('roster-list');
    list.innerHTML = '';

    if (!state.players.length) {
        list.append(emptyRow('No players on the roster yet'));
        return;
    }

    for (const player of state.players) {
        const { li, button, badge } = pickRow({
            number: player.jerseyNumber,
            name: player.name,
            toggle: true,
        });

        // Repainted in place rather than by rebuilding the list. Rebuilding
        // threw away the button that was just pressed, which takes the keyboard
        // focus with it — invisible while these were unfocusable list items,
        // and the first thing anyone tabbing through them would have hit.
        const paint = () => {
            button.classList.toggle('is-on', Boolean(player._starter));
            button.setAttribute('aria-pressed', player._starter ? 'true' : 'false');
            // Only the chosen say so. Fourteen grey BENCH labels is fourteen
            // repetitions of the default, and the heading above already says
            // that everyone you don't tap becomes a substitute — while the
            // width they took was pushing half the names onto a second line.
            badge.textContent = player._starter ? 'starting' : '';
        };
        button.addEventListener('click', () => {
            player._starter = !player._starter;
            paint();
            countStarters();
        });

        paint();
        list.append(li);
    }

    countStarters();
}

function countStarters() {
    byId('starter-count').textContent =
        state.players.filter((p) => p._starter).length;
}

async function saveLineupAndContinue() {
    const starters = state.players.filter((p) => p._starter).map((p) => p.id);
    if (!starters.length) return toast('Pick your starters first', true);

    sendWrite(
        setLineup(
            state.teamId, state.matchId, state.players, starters, state.roster
        ),
        { message: 'The lineup was not saved' },
    );

    // The read is still awaited, and safely: a read resolves from the local
    // cache whether or not there is a connection, and that cache already has
    // the lineup this line just wrote. It is the *write* that waits for a
    // server, which is why the tool sat on this screen in a dead spot.
    state.roster = await listMatchRoster(state.teamId, state.matchId);
    showView('kickoff');
}

async function resumeMatch() {
    const log = await listLog(state.teamId, state.matchId);
    const last = log.length ? log[log.length - 1].matchClockS : 0;

    // Resume paused: real elapsed time cannot be recovered after a reload, and
    // inventing it would corrupt every timestamp that follows.
    state.clockOffset = last;
    state.running = false;
    state.kickoffAt = Date.now();

    recountScore(log);

    const mine = log.filter((e) => e.deviceId === state.device);
    state.seq = mine.reduce((max, e) => Math.max(max, e.seq || 0), 0);
    state.myEntries = mine.map((e) => ({
        id: e.id, kind: e.kind, type: e.type, revert: e.revert,
    }));

    const status = state.match?.status || 'scheduled';
    byId('period-label').textContent =
        status === 'second_half' ? '2nd half'
        : status === 'halftime' ? 'half-time'
        : status === 'full_time' ? 'full time'
        : '1st half';

    updatePeriodButton();
    renderScore();
    setLast(log.length ? describeEvent(log[log.length - 1], labels()) : 'nothing yet', false);

    if (status === 'scheduled') {
        showView('kickoff');
        return;
    }

    showView('live');
    watchMatchSync();
    renderClockChrome();

    // Real elapsed time cannot be recovered after a reload, so the clock comes
    // back stopped at the last tag. Say what to do about it, not just what
    // happened — leaving it stopped stamps the rest of the half identically.
    if (inPlay()) {
        toast('Clock stopped at your last tag — tap it to set and restart', true, 6000);
    }
}

function recountScore(log) {
    state.score = { us: 0, them: 0 };
    for (const e of log) {
        if (e.kind === 'event' && e.type === 'goal') {
            state.score[e.side === 'them' ? 'them' : 'us'] += 1;
        }
    }
}

const labels = () => ({ usName: state.teamName, themName: state.opponentName });

// ---------------------------------------------------------------- kickoff

function doKickoff() {
    const seq = nextSeq();
    const before = state.match?.status || 'scheduled';

    // The clock starts on the tap. It has to: this is the moment the match
    // begins and the moment a clap was made for the camera, and both of those
    // happened whether or not a server heard about it.
    sendWrite(
        writePeriod(state.user, state.teamId, state.matchId, {
            deviceId: state.device, seq, period: 'kickoff_1st', matchClockS: 0,
            prevStatus: before,
        }),
        { message: 'Kick-off was not saved' },
    );

    state.myEntries.push({
        id: logId(state.device, seq), kind: 'period', type: 'kickoff_1st',
        revert: { prevStatus: before },
    });
    state.match.status = 'first_half';

    state.clockOffset = 0;
    state.kickoffAt = Date.now();
    state.running = true;
    byId('period-label').textContent = '1st half';
    updatePeriodButton();
    renderClockChrome();
    renderScore();
    setLast('kick-off');
    showView('live');
    watchMatchSync();
}

// ---------------------------------------------------------------- event sheet

const activeRoster = () => state.roster.filter((r) => r.isActive);

/**
 * Open the detail sheet for an event type. Simple events need one tap (which
 * team); goals and cards need a little more. Every step can be skipped so a
 * tagger is never stuck mid-play with an unfinished tag.
 */
function openEventSheet(type, button) {
    button.classList.add('flash');
    setTimeout(() => button.classList.remove('flash'), 150);

    const spec = EVENTS[type];
    if (!spec) return;

    state.draft = {
        type,
        matchClockS: matchClock(),
        side: null,
        playerId: null,
        assistPlayerId: null,
        cardColor: null,
    };

    byId('sheet-title').textContent = spec.label;
    byId('sheet-question').textContent = spec.sideMeans;

    for (const id of ['step-card', 'step-player', 'step-assist']) {
        byId(id).classList.add('hidden');
    }
    byId('side-choices').parentElement.querySelector('#sheet-question').classList.remove('hidden');
    byId('side-choices').classList.remove('hidden');

    renderSideChoices();
    byId('overlay-event').classList.add('open');
}

function renderSideChoices() {
    const wrap = byId('side-choices');
    wrap.innerHTML = '';

    for (const side of ['us', 'them']) {
        const button = document.createElement('button');
        button.className = `choice ${side === 'us' ? 'us' : ''}`;
        button.innerHTML = `<span class="nm"></span><span class="sub"></span>`;
        button.querySelector('.nm').textContent = teamName(side);
        button.querySelector('.sub').textContent = side === 'us' ? 'our team' : 'opponent';
        button.addEventListener('click', () => onSideChosen(side));
        wrap.appendChild(button);
    }
}

function onSideChosen(side) {
    state.draft.side = side;
    const spec = EVENTS[state.draft.type];

    byId('sheet-question').classList.add('hidden');
    byId('side-choices').classList.add('hidden');

    if (spec.needsCard) return showCardStep();
    // We do not hold the opposition roster, so player detail only applies to us.
    if (spec.needsPlayer && side === 'us' && activeRoster().length) return showPlayerStep();

    commitDraft();
}

function showCardStep() {
    const wrap = byId('card-choices');
    wrap.innerHTML = '';

    for (const [key, meta] of Object.entries(CARD_COLOURS)) {
        const button = document.createElement('button');
        button.className = `choice ${key}`;
        button.innerHTML = `<span class="card-chip ${key}"></span><span class="nm"></span>`;
        button.querySelector('.nm').textContent = meta.label;
        button.addEventListener('click', () => {
            state.draft.cardColor = key;
            byId('step-card').classList.add('hidden');
            const spec = EVENTS[state.draft.type];
            if (spec.needsPlayer && state.draft.side === 'us' && activeRoster().length) {
                showPlayerStep();
            } else {
                commitDraft();
            }
        });
        wrap.appendChild(button);
    }

    byId('step-card').classList.remove('hidden');
}

function showPlayerStep() {
    const spec = EVENTS[state.draft.type];
    byId('player-question').textContent = spec.needsPlayer;

    const list = byId('player-choices');
    list.innerHTML = '';

    for (const entry of activeRoster()) {
        const { li } = pickRow({
            number: entry.jerseyNumber,
            name: entry.playerName,
            onPick: () => {
                state.draft.playerId = entry.id;
                byId('step-player').classList.add('hidden');
                if (spec.needsAssist) showAssistStep();
                else commitDraft();
            },
        });
        list.append(li);
    }

    byId('step-player').classList.remove('hidden');
}

function showAssistStep() {
    const list = byId('assist-choices');
    list.innerHTML = '';

    // The scorer cannot assist their own goal; the rules reject it too.
    const options = activeRoster().filter((e) => e.id !== state.draft.playerId);

    if (!options.length) return commitDraft();

    for (const entry of options) {
        const { li } = pickRow({
            number: entry.jerseyNumber,
            name: entry.playerName,
            onPick: () => {
                state.draft.assistPlayerId = entry.id;
                commitDraft();
            },
        });
        list.append(li);
    }

    byId('step-assist').classList.remove('hidden');
}

function commitDraft() {
    const draft = state.draft;
    state.draft = null;
    byId('overlay-event').classList.remove('open');
    if (!draft) return;

    const seq = nextSeq();
    const entry = {
        id: logId(state.device, seq), kind: 'event', type: draft.type, side: draft.side,
    };
    const side = draft.side === 'them' ? 'them' : 'us';

    sendWrite(
        writeEvent(state.user, state.teamId, state.matchId, {
            deviceId: state.device,
            seq,
            type: draft.type,
            matchClockS: draft.matchClockS,
            side: draft.side,
            playerId: draft.playerId,
            assistPlayerId: draft.assistPlayerId,
            cardColor: draft.cardColor,
        }),
        {
            message: 'That tag was not saved',
            undo: () => {
                dropEntry(entry.id);
                if (draft.type === 'goal') {
                    state.score[side] = Math.max(0, state.score[side] - 1);
                    renderScore();
                }
            },
        },
    );

    // `state.seq` is never wound back on a rejection. It used to be, and that
    // was a document-id collision waiting to happen: offline, several taps are
    // in flight at once, so a rejection arriving after three more tags would
    // hand the next tap a sequence number one of them had already used — and
    // the log is written with `setDoc`, which overwrites.
    state.myEntries.push(entry);

    if (draft.type === 'goal') {
        state.score[side] += 1;
        renderScore();
    }

    const player = state.roster.find((r) => r.id === draft.playerId);
    setLast(describeEvent(
        { kind: 'event', ...draft }, { ...labels(), playerName: player?.playerName }
    ));
}

/** Take one entry back out of the undo stack, wherever it has ended up. */
function dropEntry(id) {
    const at = state.myEntries.findIndex((e) => e.id === id);
    if (at >= 0) state.myEntries.splice(at, 1);
}

function cancelEventSheet() {
    state.draft = null;
    byId('overlay-event').classList.remove('open');
}

// ---------------------------------------------------------------- undo

/**
 * Everything undoing `entry` is about to change, as a function that puts it
 * back.
 *
 * `sendWrite`'s compensator exists to make the screen match the record when a
 * write is refused, and it can only do that for the changes it is told about.
 * Undo changes four things - the entry list, the score, the roster and the
 * period - and the compensator restored the two that are easy to see. The
 * other two are the ones a tagger acts on next. `activeRoster()` is who the
 * event sheet offers to attribute a tag to, so a substitution left reverted
 * against a record that still holds it means the next goal is attributed to a
 * player who is on the bench; `state.match.status` is what the clock, the
 * period button and `inPlay()` all read.
 */
function undoRestorePoint(entry) {
    const roster = entry.kind === 'sub' && entry.revert
        ? ['out', 'in']
            .map((side) => rosterSnapshot(entry.revert[side]?.id))
            .filter(Boolean)
        : [];
    const status = state.match?.status;
    const halfTimeClockS = state.match?.halfTimeClockS ?? null;

    return () => {
        for (const snapshot of roster) putBack(snapshot);
        if (entry.kind === 'period' && entry.revert?.prevStatus) {
            state.match.status = status;
            state.match.halfTimeClockS = halfTimeClockS;
            updatePeriodButton();
        }
    };
}

function undoLast() {
    const entry = state.myEntries[state.myEntries.length - 1];
    if (!entry) return toast('Nothing of yours to undo', true);

    // Captured before anything moves, because by the time a refusal comes back
    // the screen has moved on and this is the only record of where it was.
    const restore = undoRestorePoint(entry);

    // Same rule as everywhere else here: a mis-tap has to come off the screen
    // now, not when a server three miles away agrees. Undo is the control a
    // tagger reaches for immediately after a wrong tap, so waiting on the
    // network is the one thing it cannot do.
    sendWrite(
        undoEntry(state.teamId, state.matchId, entry),
        {
            message: 'That undo was not saved',
            undo: () => {
                state.myEntries.push(entry);
                restore();
                if (entry.type === 'goal') {
                    const key = entry.side === 'them' ? 'them' : 'us';
                    state.score[key] += 1;
                    renderScore();
                }
            },
        },
    );

    state.myEntries.pop();

    if (entry.kind === 'sub' && entry.revert) {
        // Contents from before the substitution, version from after taking it
        // back: `undoEntry` writes version + 2, and a tablet left two behind
        // would have every later substitution of either player refused by the
        // lock, with no way to catch up short of reopening the sheet.
        putBack(entry.revert.out, (entry.revert.out.version ?? 0) + 2);
        putBack(entry.revert.in, (entry.revert.in.version ?? 0) + 2);
    }
    if (entry.kind === 'period' && entry.revert?.prevStatus) {
        state.match.status = entry.revert.prevStatus;
        // `undoEntry` nulls the clock reading alongside the status, for the
        // reason spelled out there; leaving it here would let a later payload
        // anchor the second half to a break the log no longer says happened.
        if (entry.type === 'halftime') state.match.halfTimeClockS = null;
        updatePeriodButton();
    }
    if (entry.type === 'goal') {
        const key = entry.side === 'them' ? 'them' : 'us';
        state.score[key] = Math.max(0, state.score[key] - 1);
        renderScore();
    }

    setLast('undone', false);
    toast('Undone');
}

// ---------------------------------------------------------------- periods

function updatePeriodButton() {
    const button = byId('btn-period');
    const status = state.match?.status;

    if (status === 'full_time') {
        button.disabled = true;
        button.textContent = 'Match over';
    } else if (status === 'halftime') {
        button.textContent = 'Start 2nd half';
    } else if (status === 'second_half') {
        button.textContent = 'Full time';
    } else {
        button.textContent = 'Half-time';
    }
}

function advancePeriod() {
    const status = state.match?.status || 'first_half';
    const next =
        status === 'first_half' ? 'halftime'
        : status === 'halftime' ? 'kickoff_2nd'
        : 'full_time';

    const now = matchClock();
    const seq = nextSeq();
    const entry = {
        // `type` so undo knows a half-time tap from any other period tap: that
        // one also wrote the clock reading the second half is anchored to, and
        // undoing it has to take that with it.
        id: logId(state.device, seq), kind: 'period', type: next,
        revert: { prevStatus: status },
    };

    // The status is the smallest thing this tap changes. It also freezes or
    // restarts the clock, and half-time writes the reading the second half is
    // anchored to - so a compensator that restores only the button leaves a
    // clock running against a record that says the half never ended, and every
    // timestamp after it is wrong by the length of the break.
    const before = {
        clockOffset: state.clockOffset,
        running: state.running,
        kickoffAt: state.kickoffAt,
        label: byId('period-label').textContent,
        halfTimeClockS: state.match?.halfTimeClockS ?? null,
    };

    sendWrite(
        writePeriod(state.user, state.teamId, state.matchId, {
            deviceId: state.device, seq, period: next, matchClockS: now, prevStatus: status,
        }),
        {
            message: `${PERIOD_LABELS[next] || next} was not saved`,
            undo: () => {
                dropEntry(entry.id);
                state.match.status = status;
                state.match.halfTimeClockS = before.halfTimeClockS;
                state.clockOffset = before.clockOffset;
                state.running = before.running;
                state.kickoffAt = before.kickoffAt;
                byId('period-label').textContent = before.label;
                updatePeriodButton();
                renderClockChrome();
            },
        },
    );

    state.myEntries.push(entry);
    state.match.status = PERIOD_STATUS[next];
    if (next === 'halftime') state.match.halfTimeClockS = now;

    if (next === 'halftime') {
        // Freeze: the break must never be counted as match time.
        state.clockOffset = now;
        state.running = false;
        byId('period-label').textContent = 'half-time';
    } else if (next === 'kickoff_2nd') {
        state.kickoffAt = Date.now();
        state.running = true;
        byId('period-label').textContent = '2nd half';
    } else {
        state.clockOffset = now;
        state.running = false;
        byId('period-label').textContent = 'full time';
    }

    updatePeriodButton();
    renderClockChrome();
    setLast(PERIOD_LABELS[next] || next);
}

// ---------------------------------------------------------------- subs

async function openSubSheet() {
    state.sub = { outId: null, inId: null };
    state.roster = await listMatchRoster(state.teamId, state.matchId);
    byId('overlay-sub').classList.add('open');
    renderSubLists();
}

function renderSubLists() {
    const off = byId('sub-off-list');
    const on = byId('sub-on-list');
    off.innerHTML = '';
    on.innerHTML = '';

    // Each side's buttons, kept so a pick can repaint the pair rather than
    // rebuild both lists. Rebuilding destroys the button that was just pressed,
    // and with real buttons in here that is a keyboard reader losing their
    // place mid-substitution — while the clock is running.
    const painters = [];

    const row = (entry, side, dim = false) => {
        const { li, button } = pickRow({
            number: entry.jerseyNumber,
            name: entry.playerName,
            tag: dim ? 'been on' : '',
            toggle: true,
            onPick: () => {
                state.sub[side] = entry.id;
                repaint();
            },
        });
        if (dim) button.classList.add('is-dim');

        painters.push(() => {
            const here = state.sub[side] === entry.id;
            button.classList.toggle('is-on', here);
            button.setAttribute('aria-pressed', here ? 'true' : 'false');
        });
        return li;
    };

    function repaint() {
        for (const paint of painters) paint();
        byId('btn-sub-confirm').disabled = !(state.sub.outId && state.sub.inId);
    }

    const onField = state.roster.filter((r) => r.isActive);
    const fresh = state.roster.filter((r) => !r.isActive && !(r.stints || []).length);
    // High school rules allow re-entry, so a player who came off is still
    // eligible — just dimmed so they don't look identical to an unused sub.
    const used = state.roster.filter((r) => !r.isActive && (r.stints || []).length);

    if (!onField.length) off.append(emptyRow('Nobody on the field.'));
    for (const entry of onField) off.append(row(entry, 'outId'));

    if (!fresh.length && !used.length) on.append(emptyRow('No substitutes.'));
    for (const entry of [...fresh, ...used]) {
        on.append(row(entry, 'inId', (entry.stints || []).length > 0));
    }

    repaint();
}

function confirmSub() {
    const outEntry = state.roster.find((r) => r.id === state.sub.outId);
    const inEntry = state.roster.find((r) => r.id === state.sub.inId);
    if (!outEntry || !inEntry) return;

    const clock = matchClock();
    const seq = nextSeq();
    const before = {
        out: {
            id: outEntry.id, isActive: outEntry.isActive,
            stints: outEntry.stints || [], version: outEntry.version ?? 0,
        },
        in: {
            id: inEntry.id, isActive: inEntry.isActive,
            stints: inEntry.stints || [], version: inEntry.version ?? 0,
        },
    };
    const entry = { id: logId(state.device, seq), kind: 'sub', revert: before };

    sendWrite(
        writeSubstitution(state.user, state.teamId, state.matchId, {
            deviceId: state.device, seq, outEntry, inEntry, matchClockS: clock,
        }),
        {
            message: 'That substitution was not saved',
            undo: () => {
                dropEntry(entry.id);
                putBack(before.out);
                putBack(before.in);
            },
        },
    );

    // Closed on the tap, and the roster moved in memory rather than re-read.
    //
    // Both of those are the fix for a measured bug: this used to close the
    // sheet only after the server had acknowledged the write, so in a dead spot
    // the sheet stayed open with Confirm still live. Three presses queued three
    // substitutions — each one closing and reopening a stint, which is exactly
    // the arithmetic the minutes column depends on.
    byId('overlay-sub').classList.remove('open');
    applySub(outEntry, inEntry, clock);
    state.myEntries.push(entry);
    state.sub = { outId: null, inId: null };
    setLast(`${inEntry.playerName} on for ${outEntry.playerName}`);
}

/**
 * Move one player off and another on, in `state.roster`.
 *
 * Mirrors exactly what `writeSubstitution` sends, because the point is that the
 * next press of Confirm sees the roster the write produced — the guard there is
 * `if (!outEntry.isActive) throw`, and it is what stops the same change being
 * recorded twice.
 */
function applySub(outEntry, inEntry, clock) {
    const outStints = (outEntry.stints || []).map((s) => ({ ...s }));
    const open = outStints[outStints.length - 1];
    if (open && open.outS === null) open.outS = clock;

    outEntry.stints = outStints;
    outEntry.isActive = false;
    outEntry.version = (outEntry.version ?? 0) + 1;

    inEntry.stints = [...(inEntry.stints || []), { inS: clock, outS: null }];
    inEntry.isActive = true;
    inEntry.version = (inEntry.version ?? 0) + 1;
}

/**
 * Put one roster entry back the way it was, after a rejected write.
 *
 * The version is separable from the contents because the two do not always
 * travel together. A refused write left the record untouched, so the prior
 * version is still the record's; an undo that *succeeded* moved the record on
 * two — one for the substitution, one for taking it back — while restoring the
 * contents to what they were before either.
 */
function putBack(snapshot, version = snapshot.version) {
    const entry = state.roster.find((r) => r.id === snapshot.id);
    if (!entry) return;
    entry.isActive = snapshot.isActive;
    entry.stints = snapshot.stints;
    entry.version = version;
}

/** What one roster entry looks like right now, in the shape `putBack` takes. */
function rosterSnapshot(id) {
    const entry = state.roster.find((r) => r.id === id);
    return entry && {
        id: entry.id, isActive: entry.isActive,
        stints: entry.stints, version: entry.version,
    };
}

// ---------------------------------------------------------------- log

async function openLog() {
    const list = byId('log-list');
    list.innerHTML = '<div class="empty">Loading…</div>';
    byId('overlay-log').classList.add('open');

    try {
        const log = await listLog(state.teamId, state.matchId);
        list.innerHTML = '';
        if (!log.length) {
            list.innerHTML = '<div class="empty">Nothing logged yet.</div>';
            return;
        }

        const nameById = new Map(state.roster.map((r) => [r.id, r.playerName]));

        for (const entry of log.slice().reverse()) {
            list.append(timelineRow({
                clock: clockText(entry.matchClockS),
                text: describeEvent(entry, {
                    ...labels(), playerName: nameById.get(entry.playerId),
                }),
                sideLabel: entry.kind === 'period' ? '' : teamName(entry.side),
                tone: timelineTone(entry),
            }));
        }
    } catch (err) {
        // textContent, not innerHTML. Every other message on this page is a
        // string this file wrote; this one comes back from Firestore, and the
        // one place in the app that interpolated a value it did not author into
        // markup should not be the error path nobody looks at.
        list.innerHTML = '';
        const failed = document.createElement('div');
        failed.className = 'empty';
        failed.textContent = err.message;
        list.append(failed);
    }
}

// ---------------------------------------------------------------- init

function init() {
    const warning = configWarning();
    if (warning) byId('config-slot').appendChild(warning);

    mountPitchBackdrop(byId('tag-header'), { opacity: 0.14 });
    // Renamed with the function it calls. It was not, on 2026-08-07, and
    // `init()` threw a ReferenceError on this line every time the page loaded —
    // so no listener below was ever attached and the whole tool was dead for
    // eight days. Nothing caught it because nothing loads this file: it imports
    // Firebase, so `tests/video.test.js` cannot, and the emulator suites drive
    // a Firestore client rather than this page.
    updateSyncIndicator();

    // Reached synchronously from the click, or iPad Safari blocks the popup.
    byId('btn-signin').addEventListener('click', () => {
        signIn().catch((err) => toast(err.message || 'Sign-in failed.', true));
    });

    byId('btn-change-match').addEventListener('click', backToMatchPicker);
    byId('btn-change-team').addEventListener('click', backToTeamPicker);
    byId('btn-save-lineup').addEventListener('click', saveLineupAndContinue);
    byId('btn-kickoff').addEventListener('click', doKickoff);
    byId('btn-back-setup').addEventListener('click', () => showView('setup'));

    document.querySelectorAll('.ev').forEach((button) => {
        button.addEventListener('click', () => openEventSheet(button.dataset.event, button));
    });

    byId('btn-event-cancel').addEventListener('click', cancelEventSheet);
    byId('btn-skip-player').addEventListener('click', () => {
        byId('step-player').classList.add('hidden');
        const spec = EVENTS[state.draft?.type];
        if (spec?.needsAssist) showAssistStep();
        else commitDraft();
    });
    byId('btn-skip-assist').addEventListener('click', () => {
        byId('step-assist').classList.add('hidden');
        commitDraft();
    });

    // Leaving mid-match is safe — every tap is already written — but the clock
    // cannot be recovered on return, so the sheet says what will actually
    // happen rather than asking a bare "are you sure".
    byId('btn-exit-live').addEventListener('click', () => {
        // "Everything you've tapped is saved" was said here unconditionally,
        // at the exact moment it matters most and with nothing behind it. The
        // listener knows, so it says so — and when it is not true, that leads.
        const safe = safeToClose(state.sync);
        const waiting = state.sync.pending;

        // Three cases, not two. The first version had only "safe" and "not
        // safe", so the not-yet-asked state — where `pending` is null — printed
        // "null taps have not reached the server yet".
        let saved;
        if (safe) {
            saved = "Everything you've tapped is on the server.";
        } else if (waiting == null) {
            saved = 'Still checking what has reached the server. Give it a few '
                + 'seconds before closing this page.';
        } else {
            saved = `${waiting} tap${waiting === 1 ? '' : 's'} `
                + `${waiting === 1 ? 'has' : 'have'} not reached the server yet. `
                + 'Leave this page open somewhere with a signal until the counter '
                + `clears — closing it now keeps ${waiting === 1 ? 'it' : 'them'} `
                + 'on this tablet, and only this tablet.';
        }

        byId('exit-note').textContent = state.running
            ? `${saved} The clock is at ${clockText(matchClock())} and will be `
              + 'paused there when you come back, so check it against the '
              + "referee's before carrying on."
            : `${saved} The clock is already paused.`;

        byId('exit-note').classList.toggle('is-warn', !safe);
        byId('btn-exit-go').textContent = safe ? 'Leave' : 'Leave anyway';
        byId('overlay-exit').classList.add('open');
    });
    byId('btn-exit-stay').addEventListener('click', () =>
        byId('overlay-exit').classList.remove('open'));
    byId('btn-exit-go').addEventListener('click', () => {
        state.stopSync?.();
        location.href = '../';
    });

    byId('btn-clock').addEventListener('click', openClockSheet);
    byId('btn-clock-close').addEventListener('click', () =>
        byId('overlay-clock').classList.remove('open'));
    byId('btn-clock-toggle').addEventListener('click', () => {
        setClockRunning(!state.running);
        if (state.running) byId('overlay-clock').classList.remove('open');
    });
    for (const button of document.querySelectorAll('.clock-adjust [data-delta]')) {
        button.addEventListener('click', () => adjustClock(Number(button.dataset.delta)));
    }

    byId('btn-undo').addEventListener('click', undoLast);
    byId('btn-period').addEventListener('click', advancePeriod);
    byId('btn-sub').addEventListener('click', () => openSubSheet());
    byId('btn-sub-cancel').addEventListener('click', () => byId('overlay-sub').classList.remove('open'));
    byId('btn-sub-confirm').addEventListener('click', confirmSub);
    byId('btn-log').addEventListener('click', openLog);
    byId('btn-log-close').addEventListener('click', () => byId('overlay-log').classList.remove('open'));

    onUser(async (user) => {
        if (!user) {
            byId('signin-block').classList.remove('hidden');
            byId('match-block').classList.add('hidden');
            return;
        }

        state.user = user;
        try {
            const access = await resolveAccess(user);
            if (access.role !== 'coach' || !access.teams.length) {
                toast('This account has no team to tag for.', true);
                return;
            }

            state.teams = access.teams;
            byId('signin-block').classList.add('hidden');

            // A squad named in the link — the home page's "next up" card sends
            // one, so a coach who has just read which team plays today does not
            // have to answer the same question again on the next screen. Only
            // honoured when it matches a squad this account actually coaches;
            // `access.teams` is resolved from authoritative documents, so a
            // hand-edited URL cannot smuggle in a team.
            const wanted = new URLSearchParams(location.search).get('team');
            const named = access.teams.find((t) => t.id === wanted);
            if (named) {
                await chooseTeam(named);
                return;
            }

            // Picking the first squad silently would be a quiet disaster for a
            // coach who runs varsity and JV: a full match tagged onto the wrong
            // roster, discovered at publish time.
            if (access.teams.length > 1) {
                renderTeamPicker();
                byId('team-block').classList.remove('hidden');
                return;
            }
            await chooseTeam(access.teams[0]);
        } catch (err) {
            toast(err.message || 'Could not load your team.', true);
        }
    });
}

init();

// Deliberate test seam: lets the tagging flow be driven from a browser without
// a live match. Exposes UI state only — every write is still gated by
// firestore.rules server-side, so this grants nothing a reader of this file
// could not already do by hand.
window._tagger = { state, openEventSheet, onSideChosen, commitDraft };
