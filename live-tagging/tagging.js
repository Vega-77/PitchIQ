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

import { onUser, signIn, resolveAccess, configWarning } from '../assets/auth.js';
import {
    listMatches, getMatch, listPlayers, setLineup, listMatchRoster, listLog,
    writeEvent, writePeriod, writeSubstitution, undoEntry,
    logId, PERIOD_STATUS,
} from '../assets/db.js';
import {
    EVENTS, CARD_COLOURS, describeEvent, timelineTone, PERIOD_LABELS,
} from '../assets/events.js';
import { mountPitchBackdrop } from '../assets/pitch-backdrop.js';
import { byId, toast, clockText, timelineRow } from '../assets/ui.js';

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

setInterval(() => {
    if (byId('view-live').classList.contains('active')) {
        byId('clock').textContent = clockText(matchClock());
    }
}, 250);

function updateOnlineIndicator() {
    const dot = byId('sync-dot');
    if (!dot) return;
    const offline = !navigator.onLine;
    dot.classList.toggle('offline', offline);
    dot.title = offline ? 'No internet — your taps are saved and will upload later' : 'Saved';
}
window.addEventListener('online', updateOnlineIndicator);
window.addEventListener('offline', updateOnlineIndicator);

const nextSeq = () => ++state.seq;

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

    const [match, players, roster] = await Promise.all([
        getMatch(state.teamId, matchId),
        listPlayers(state.teamId),
        listMatchRoster(state.teamId, matchId),
    ]);

    state.match = match;
    state.opponentName = match.opponentName || 'Them';
    state.players = players;
    state.roster = roster;

    if (roster.length) {
        await resumeMatch();
        return;
    }

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

function renderLineupPicker() {
    const list = byId('roster-list');
    list.innerHTML = '';

    if (!state.players.length) {
        list.innerHTML = '<li><span class="tag">No players on the roster yet</span></li>';
        return;
    }

    for (const player of state.players) {
        const li = document.createElement('li');
        li.className = player._starter ? 'starter' : '';
        li.innerHTML = `<span class="num"></span><span class="nm"></span><span class="tag"></span>`;
        li.querySelector('.num').textContent = player.jerseyNumber ?? '—';
        li.querySelector('.nm').textContent = player.name;
        li.querySelector('.tag').textContent = player._starter ? 'starting' : 'bench';
        li.addEventListener('click', () => {
            player._starter = !player._starter;
            renderLineupPicker();
        });
        list.appendChild(li);
    }

    byId('starter-count').textContent = state.players.filter((p) => p._starter).length;
}

async function saveLineupAndContinue() {
    const starters = state.players.filter((p) => p._starter).map((p) => p.id);
    if (!starters.length) return toast('Pick your starters first', true);

    try {
        await setLineup(state.teamId, state.matchId, state.players, starters);
        state.roster = await listMatchRoster(state.teamId, state.matchId);
        showView('kickoff');
    } catch (err) {
        toast(err.message || 'Could not save the lineup.', true);
    }
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
    state.myEntries = mine.map((e) => ({ id: e.id, kind: e.kind, revert: e.revert }));

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
    } else {
        showView('live');
        toast('Resumed — the clock is paused where the last tag left it');
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

async function doKickoff() {
    try {
        const seq = nextSeq();
        await writePeriod(state.user, state.teamId, state.matchId, {
            deviceId: state.device, seq, period: 'kickoff_1st', matchClockS: 0,
            prevStatus: state.match?.status || 'scheduled',
        });
        state.myEntries.push({
            id: logId(state.device, seq), kind: 'period',
            revert: { prevStatus: state.match?.status || 'scheduled' },
        });
        state.match.status = 'first_half';

        state.clockOffset = 0;
        state.kickoffAt = Date.now();
        state.running = true;
        byId('period-label').textContent = '1st half';
        updatePeriodButton();
        renderScore();
        setLast('kick-off');
        showView('live');
    } catch (err) {
        toast(err.message || 'Could not start the match.', true);
    }
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
        const li = document.createElement('li');
        li.innerHTML = `<span class="num"></span><span class="nm"></span>`;
        li.querySelector('.num').textContent = entry.jerseyNumber ?? '—';
        li.querySelector('.nm').textContent = entry.playerName;
        li.addEventListener('click', () => {
            state.draft.playerId = entry.id;
            byId('step-player').classList.add('hidden');
            if (spec.needsAssist) showAssistStep();
            else commitDraft();
        });
        list.appendChild(li);
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
        const li = document.createElement('li');
        li.innerHTML = `<span class="num"></span><span class="nm"></span>`;
        li.querySelector('.num').textContent = entry.jerseyNumber ?? '—';
        li.querySelector('.nm').textContent = entry.playerName;
        li.addEventListener('click', () => {
            state.draft.assistPlayerId = entry.id;
            commitDraft();
        });
        list.appendChild(li);
    }

    byId('step-assist').classList.remove('hidden');
}

async function commitDraft() {
    const draft = state.draft;
    state.draft = null;
    byId('overlay-event').classList.remove('open');
    if (!draft) return;

    const seq = nextSeq();
    try {
        await writeEvent(state.user, state.teamId, state.matchId, {
            deviceId: state.device,
            seq,
            type: draft.type,
            matchClockS: draft.matchClockS,
            side: draft.side,
            playerId: draft.playerId,
            assistPlayerId: draft.assistPlayerId,
            cardColor: draft.cardColor,
        });

        state.myEntries.push({ id: logId(state.device, seq), kind: 'event', type: draft.type, side: draft.side });

        if (draft.type === 'goal') {
            state.score[draft.side === 'them' ? 'them' : 'us'] += 1;
            renderScore();
        }

        const player = state.roster.find((r) => r.id === draft.playerId);
        setLast(describeEvent(
            { kind: 'event', ...draft }, { ...labels(), playerName: player?.playerName }
        ));
    } catch (err) {
        state.seq -= 1;
        toast(err.message || 'Could not save that tag.', true);
    }
}

function cancelEventSheet() {
    state.draft = null;
    byId('overlay-event').classList.remove('open');
}

// ---------------------------------------------------------------- undo

async function undoLast() {
    const entry = state.myEntries[state.myEntries.length - 1];
    if (!entry) return toast('Nothing of yours to undo', true);

    try {
        await undoEntry(state.teamId, state.matchId, entry);
        state.myEntries.pop();

        if (entry.kind === 'sub') {
            state.roster = await listMatchRoster(state.teamId, state.matchId);
        }
        if (entry.kind === 'period' && entry.revert?.prevStatus) {
            state.match.status = entry.revert.prevStatus;
            updatePeriodButton();
        }
        if (entry.type === 'goal') {
            const key = entry.side === 'them' ? 'them' : 'us';
            state.score[key] = Math.max(0, state.score[key] - 1);
            renderScore();
        }

        setLast('undone', false);
        toast('Undone');
    } catch (err) {
        toast(err.message || 'Could not undo.', true);
    }
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

async function advancePeriod() {
    const status = state.match?.status || 'first_half';
    const next =
        status === 'first_half' ? 'halftime'
        : status === 'halftime' ? 'kickoff_2nd'
        : 'full_time';

    const now = matchClock();
    const seq = nextSeq();

    try {
        await writePeriod(state.user, state.teamId, state.matchId, {
            deviceId: state.device, seq, period: next, matchClockS: now, prevStatus: status,
        });
        state.myEntries.push({
            id: logId(state.device, seq), kind: 'period', revert: { prevStatus: status },
        });
        state.match.status = PERIOD_STATUS[next];

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
        setLast(PERIOD_LABELS[next] || next);
    } catch (err) {
        state.seq -= 1;
        toast(err.message || 'Could not change period.', true);
    }
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

    const row = (entry, selectedId, onPick, dim = false) => {
        const li = document.createElement('li');
        li.className = [entry.id === selectedId ? 'selected' : '', dim ? 'used' : '']
            .filter(Boolean).join(' ');
        li.innerHTML = `<span class="num"></span><span class="nm"></span>`;
        li.querySelector('.num').textContent = entry.jerseyNumber ?? '—';
        li.querySelector('.nm').textContent = entry.playerName;
        li.addEventListener('click', () => onPick(entry.id));
        return li;
    };

    const onField = state.roster.filter((r) => r.isActive);
    const fresh = state.roster.filter((r) => !r.isActive && !(r.stints || []).length);
    // High school rules allow re-entry, so a player who came off is still
    // eligible — just dimmed so they don't look identical to an unused sub.
    const used = state.roster.filter((r) => !r.isActive && (r.stints || []).length);

    if (!onField.length) off.innerHTML = '<li class="empty">Nobody on the field.</li>';
    for (const e of onField) {
        off.appendChild(row(e, state.sub.outId, (id) => { state.sub.outId = id; renderSubLists(); }));
    }

    if (!fresh.length && !used.length) on.innerHTML = '<li class="empty">No substitutes.</li>';
    for (const entry of [...fresh, ...used]) {
        const alreadyPlayed = (entry.stints || []).length > 0;
        on.appendChild(row(
            entry,
            state.sub.inId,
            (id) => { state.sub.inId = id; renderSubLists(); },
            alreadyPlayed,
        ));
    }

    byId('btn-sub-confirm').disabled = !(state.sub.outId && state.sub.inId);
}

async function confirmSub() {
    const outEntry = state.roster.find((r) => r.id === state.sub.outId);
    const inEntry = state.roster.find((r) => r.id === state.sub.inId);
    if (!outEntry || !inEntry) return;

    const seq = nextSeq();
    try {
        await writeSubstitution(state.user, state.teamId, state.matchId, {
            deviceId: state.device, seq, outEntry, inEntry, matchClockS: matchClock(),
        });

        state.myEntries.push({
            id: logId(state.device, seq),
            kind: 'sub',
            revert: {
                out: { id: outEntry.id, isActive: outEntry.isActive, stints: outEntry.stints || [], version: outEntry.version ?? 0 },
                in: { id: inEntry.id, isActive: inEntry.isActive, stints: inEntry.stints || [], version: inEntry.version ?? 0 },
            },
        });

        state.roster = await listMatchRoster(state.teamId, state.matchId);
        byId('overlay-sub').classList.remove('open');
        setLast(`${inEntry.playerName} on for ${outEntry.playerName}`);
    } catch (err) {
        state.seq -= 1;
        toast(err.message || 'Could not save the substitution.', true);
    }
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
        list.innerHTML = `<div class="empty">${err.message}</div>`;
    }
}

// ---------------------------------------------------------------- init

function init() {
    const warning = configWarning();
    if (warning) byId('config-slot').appendChild(warning);

    mountPitchBackdrop(byId('tag-header'), { opacity: 0.14 });
    updateOnlineIndicator();

    // Reached synchronously from the click, or iPad Safari blocks the popup.
    byId('btn-signin').addEventListener('click', () => {
        signIn().catch((err) => toast(err.message || 'Sign-in failed.', true));
    });

    byId('btn-change-match').addEventListener('click', backToMatchPicker);
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
        const running = state.running;
        byId('exit-note').textContent = running
            ? `Everything you've tapped is saved. The clock is at ${clockText(matchClock())} `
              + 'and will be paused there when you come back, so check it against '
              + "the referee's before carrying on."
            : "Everything you've tapped is saved. The clock is already paused.";
        byId('overlay-exit').classList.add('open');
    });
    byId('btn-exit-stay').addEventListener('click', () =>
        byId('overlay-exit').classList.remove('open'));
    byId('btn-exit-go').addEventListener('click', () => { location.href = '../'; });

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
            state.teamId = access.teams[0].id;
            state.teamName = access.teams[0].name || 'Us';
            byId('setup-sub').textContent = state.teamName;
            byId('signin-block').classList.add('hidden');
            byId('match-block').classList.remove('hidden');
            await loadMatches();
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
