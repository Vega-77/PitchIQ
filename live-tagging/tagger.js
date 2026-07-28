// Match-day tagging, backed by Firestore.
//
// Two things shape this file:
//   - It has to work with no connectivity. Every write is a batch (which queues
//     offline), never a transaction (which fails offline), and undo works by
//     direct document reference so it needs no query.
//   - Ordering never uses createdAt. serverTimestamp() reads as null locally
//     until acknowledged and then resolves to sync time, not tap time.

import { onUser, signIn, resolveAccess, configWarning } from '../assets/auth.js';
import {
    listMatches, getMatch, listPlayers, setLineup, listMatchRoster, listLog,
    writeEvent, writePeriod, writeSubstitution, undoEntry, updateMatch,
    logId, PERIOD_STATUS,
} from '../assets/db.js';

const $ = (id) => document.getElementById(id);

/** Stable per-device ID: log doc IDs embed it so two taggers can't collide. */
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
    matchId: null,
    match: null,
    players: [],
    roster: [],
    device: deviceId(),
    seq: 0,
    myEntries: [],       // ids I wrote this session, for undo
    side: 'us',
    kickoffAt: null,
    clockOffset: 0,
    running: false,
    periodIndex: 0,
    pendingEvent: null,
    sub: { outId: null, inId: null },
};

// ---------------------------------------------------------------- ui helpers

let toastTimer;
function toast(message, isError = false) {
    const el = $('toast');
    el.textContent = message;
    el.classList.toggle('error', isError);
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2400);
}

function showView(name) {
    document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
    $(`view-${name}`).classList.add('active');
}

const label = (type) => type.replace(/_/g, ' ');

function clockText(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

function matchClock() {
    if (!state.running) return state.clockOffset;
    return state.clockOffset + (Date.now() - state.kickoffAt) / 1000;
}

setInterval(() => {
    if ($('view-live').classList.contains('active')) {
        $('clock').textContent = clockText(matchClock());
    }
}, 250);

function updateOnlineIndicator() {
    const offline = !navigator.onLine;
    $('offline-note')?.classList.toggle('hidden', !offline);
    const dot = $('sync-dot');
    if (dot) {
        dot.classList.toggle('offline', offline);
        dot.title = offline ? 'Offline — taps are queued' : 'Synced';
    }
}
window.addEventListener('online', updateOnlineIndicator);
window.addEventListener('offline', updateOnlineIndicator);

function nextSeq() {
    state.seq += 1;
    return state.seq;
}

// ---------------------------------------------------------------- setup

async function loadMatches() {
    const matches = await listMatches(state.teamId);
    const select = $('select-match');
    select.innerHTML = '<option value="">— choose —</option>';

    for (const match of matches) {
        if (match.finalized) continue;
        const option = document.createElement('option');
        option.value = match.id;
        option.textContent = `${match.date || 'no date'} · vs ${match.opponentName || '—'}`;
        select.appendChild(option);
    }

    if (!select.querySelector('option[value]:not([value=""])')) {
        toast('No open matches — create one in the coach dashboard first.', true);
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
    state.players = players;
    state.roster = roster;

    // Resume rather than restart if this match already has a lineup.
    if (roster.length) {
        await resumeMatch();
        return;
    }

    renderLineupPicker();
}

function renderLineupPicker() {
    const list = $('roster-list');
    list.innerHTML = '';

    if (!state.players.length) {
        list.innerHTML = '<li><span class="tag">No players on the roster yet</span></li>';
        return;
    }

    for (const player of state.players) {
        const li = document.createElement('li');
        li.className = player._starter ? 'starter' : '';
        li.innerHTML = `
            <span class="num">${player.jerseyNumber ?? '—'}</span>
            <span class="nm"></span>
            <span class="tag">${player._starter ? 'starting' : 'bench'}</span>`;
        li.querySelector('.nm').textContent = player.name;
        li.addEventListener('click', () => {
            player._starter = !player._starter;
            renderLineupPicker();
        });
        list.appendChild(li);
    }
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

    // Pick up paused at the last tagged moment: real elapsed time can't be
    // recovered after a reload, and inventing it would corrupt every timestamp
    // that follows.
    state.clockOffset = last;
    state.running = false;
    state.kickoffAt = Date.now();

    const status = state.match?.status || 'scheduled';
    state.periodIndex = status === 'second_half' || status === 'full_time' ? 1 : 0;

    // Continue our own sequence past anything this device already wrote.
    const mine = log.filter((e) => e.deviceId === state.device);
    state.seq = mine.reduce((max, e) => Math.max(max, e.seq || 0), 0);
    state.myEntries = mine.map((e) => ({ id: e.id, kind: e.kind, revert: e.revert }));

    $('period-label').textContent = label(
        status === 'second_half' ? '2nd half' : status === 'halftime' ? 'halftime' : '1st half'
    );
    updatePeriodButton();

    if (status === 'scheduled') {
        showView('kickoff');
    } else {
        showView('live');
        toast('Resumed — clock is paused, tap the clock area to adjust');
    }
}

// ---------------------------------------------------------------- live

async function doKickoff() {
    try {
        await writePeriod(state.user, state.teamId, state.matchId, {
            deviceId: state.device,
            seq: nextSeq(),
            period: 'kickoff_1st',
            matchClockS: 0,
            prevStatus: state.match?.status || 'scheduled',
        });
        state.myEntries.push({
            id: logId(state.device, state.seq),
            kind: 'period',
            revert: { prevStatus: state.match?.status || 'scheduled' },
        });

        state.clockOffset = 0;
        state.kickoffAt = Date.now();
        state.running = true;
        state.periodIndex = 0;
        updatePeriodButton();
        showView('live');
        toast('Match started');
    } catch (err) {
        toast(err.message || 'Could not start the match.', true);
    }
}

function activeRoster() {
    return state.roster.filter((r) => r.isActive);
}

/** Goals, cards and fouls by our side are worth attributing to a player. */
const ATTRIBUTABLE = new Set(['goal', 'card', 'foul']);

async function tagEvent(type, button) {
    button.classList.add('flash');
    setTimeout(() => button.classList.remove('flash'), 160);

    if (state.side === 'us' && ATTRIBUTABLE.has(type) && activeRoster().length) {
        state.pendingEvent = { type, matchClockS: matchClock() };
        openWhoSheet(type);
        return;
    }

    await commitEvent(type, matchClock(), null);
}

async function commitEvent(type, matchClockS, playerId) {
    const seq = nextSeq();
    try {
        await writeEvent(state.user, state.teamId, state.matchId, {
            deviceId: state.device,
            seq,
            type,
            matchClockS,
            side: state.side,
            playerId,
        });
        state.myEntries.push({ id: logId(state.device, seq), kind: 'event' });
        toast(label(type));
    } catch (err) {
        state.seq -= 1;
        toast(err.message || 'Could not save that tap.', true);
    }
}

function openWhoSheet(type) {
    $('who-title').textContent = `Who — ${label(type)}?`;
    const list = $('who-list');
    list.innerHTML = '';

    for (const entry of activeRoster()) {
        const li = document.createElement('li');
        li.innerHTML = `<span class="num">${entry.jerseyNumber ?? '—'}</span><span class="nm"></span>`;
        li.querySelector('.nm').textContent = entry.playerName;
        li.addEventListener('click', async () => {
            $('overlay-who').classList.remove('open');
            const pending = state.pendingEvent;
            state.pendingEvent = null;
            if (pending) await commitEvent(pending.type, pending.matchClockS, entry.id);
        });
        list.appendChild(li);
    }

    $('overlay-who').classList.add('open');
}

async function undoLast() {
    const entry = state.myEntries[state.myEntries.length - 1];
    if (!entry) return toast('Nothing of yours to undo', true);

    try {
        await undoEntry(state.teamId, state.matchId, entry);
        state.myEntries.pop();

        if (entry.kind === 'sub' && entry.revert) {
            state.roster = await listMatchRoster(state.teamId, state.matchId);
        }
        if (entry.kind === 'period' && entry.revert?.prevStatus) {
            state.match.status = entry.revert.prevStatus;
        }

        toast('Undone');
    } catch (err) {
        toast(err.message || 'Could not undo.', true);
    }
}

// ---------------------------------------------------------------- periods

const PERIOD_FLOW = ['halftime', 'kickoff_2nd', 'full_time'];

function updatePeriodButton() {
    const button = $('btn-period');
    const status = state.match?.status;

    if (status === 'full_time') {
        button.disabled = true;
        button.textContent = 'Match over';
    } else if (status === 'halftime') {
        button.textContent = 'Start 2nd half';
    } else if (status === 'second_half') {
        button.textContent = 'Full time';
    } else {
        button.textContent = 'Halftime';
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
            deviceId: state.device,
            seq,
            period: next,
            matchClockS: now,
            prevStatus: status,
        });
        state.myEntries.push({
            id: logId(state.device, seq), kind: 'period', revert: { prevStatus: status },
        });
        state.match.status = PERIOD_STATUS[next];

        if (next === 'halftime') {
            // Freeze: the break must never be counted as match time.
            state.clockOffset = now;
            state.running = false;
            $('period-label').textContent = 'halftime';
        } else if (next === 'kickoff_2nd') {
            state.kickoffAt = Date.now();
            state.running = true;
            $('period-label').textContent = '2nd half';
        } else {
            state.clockOffset = now;
            state.running = false;
            $('period-label').textContent = 'full time';
        }

        updatePeriodButton();
        toast(label(next));
    } catch (err) {
        state.seq -= 1;
        toast(err.message || 'Could not change period.', true);
    }
}

// ---------------------------------------------------------------- subs

async function openSubSheet() {
    state.sub = { outId: null, inId: null };
    state.roster = await listMatchRoster(state.teamId, state.matchId);
    $('overlay-sub').classList.add('open');
    renderSubLists();
}

function renderSubLists() {
    const off = $('sub-off-list');
    const on = $('sub-on-list');
    off.innerHTML = '';
    on.innerHTML = '';

    const row = (entry, selectedId, onPick, dim = false) => {
        const li = document.createElement('li');
        li.className = [
            entry.id === selectedId ? 'selected' : '',
            dim ? 'used' : '',
        ].filter(Boolean).join(' ');
        li.innerHTML = `<span class="num">${entry.jerseyNumber ?? '—'}</span><span class="nm"></span>`;
        li.querySelector('.nm').textContent = entry.playerName;
        if (!dim) li.addEventListener('click', () => onPick(entry.id));
        return li;
    };

    const onField = state.roster.filter((r) => r.isActive);
    const bench = state.roster.filter((r) => !r.isActive && !(r.stints || []).length);
    const used = state.roster.filter((r) => !r.isActive && (r.stints || []).length);

    if (!onField.length) off.innerHTML = '<li class="empty">Nobody on the field.</li>';
    for (const entry of onField) {
        off.appendChild(row(entry, state.sub.outId, (id) => {
            state.sub.outId = id;
            renderSubLists();
        }));
    }

    if (!bench.length && !used.length) on.innerHTML = '<li class="empty">No substitutes.</li>';
    for (const entry of bench) {
        on.appendChild(row(entry, state.sub.inId, (id) => {
            state.sub.inId = id;
            renderSubLists();
        }));
    }
    // Players who already came off stay visible but dimmed — high school rules
    // allow re-entry, so they aren't necessarily done, but they shouldn't look
    // identical to someone who hasn't played.
    for (const entry of used) {
        on.appendChild(row(entry, state.sub.inId, (id) => {
            state.sub.inId = id;
            renderSubLists();
        }));
    }

    $('btn-sub-confirm').disabled = !(state.sub.outId && state.sub.inId);
}

async function confirmSub() {
    const outEntry = state.roster.find((r) => r.id === state.sub.outId);
    const inEntry = state.roster.find((r) => r.id === state.sub.inId);
    if (!outEntry || !inEntry) return;

    const seq = nextSeq();
    try {
        await writeSubstitution(state.user, state.teamId, state.matchId, {
            deviceId: state.device,
            seq,
            outEntry,
            inEntry,
            matchClockS: matchClock(),
        });

        state.myEntries.push({
            id: logId(state.device, seq),
            kind: 'sub',
            revert: {
                out: {
                    id: outEntry.id, isActive: outEntry.isActive,
                    stints: outEntry.stints || [], version: outEntry.version ?? 0,
                },
                in: {
                    id: inEntry.id, isActive: inEntry.isActive,
                    stints: inEntry.stints || [], version: inEntry.version ?? 0,
                },
            },
        });

        state.roster = await listMatchRoster(state.teamId, state.matchId);
        $('overlay-sub').classList.remove('open');
        toast(`${inEntry.playerName} on for ${outEntry.playerName}`);
    } catch (err) {
        state.seq -= 1;
        toast(err.message || 'Could not save the substitution.', true);
    }
}

// ---------------------------------------------------------------- log

async function openLog() {
    const list = $('log-list');
    list.innerHTML = '<li class="empty">Loading…</li>';
    $('overlay-log').classList.add('open');

    try {
        const log = await listLog(state.teamId, state.matchId);
        list.innerHTML = '';
        if (!log.length) {
            list.innerHTML = '<li class="empty">Nothing logged yet.</li>';
            return;
        }
        for (const entry of log.slice().reverse()) {
            const li = document.createElement('li');
            li.innerHTML = `<span class="t"></span><span class="l"></span>`;
            li.querySelector('.t').textContent = clockText(entry.matchClockS);
            li.querySelector('.l').textContent =
                entry.kind === 'sub' ? 'substitution' : label(entry.type);
            list.appendChild(li);
        }
    } catch (err) {
        list.innerHTML = `<li class="empty">${err.message}</li>`;
    }
}

// ---------------------------------------------------------------- init

function init() {
    const warning = configWarning();
    if (warning) $('config-slot').appendChild(warning);

    updateOnlineIndicator();

    // Synchronous from the click — an await here and iPad Safari blocks the popup.
    $('btn-signin').addEventListener('click', () => {
        signIn().catch((err) => toast(err.message || 'Sign-in failed.', true));
    });

    $('select-match').addEventListener('change', (e) => {
        onMatchChosen(e.target.value).catch((err) =>
            toast(err.message || 'Could not load that match.', true)
        );
    });

    $('btn-save-lineup').addEventListener('click', saveLineupAndContinue);
    $('btn-kickoff').addEventListener('click', doKickoff);
    $('btn-back-setup').addEventListener('click', () => showView('setup'));

    document.querySelectorAll('.ev').forEach((button) => {
        button.addEventListener('click', () => tagEvent(button.dataset.event, button));
    });

    document.querySelectorAll('.team-btn[data-team]').forEach((button) => {
        button.addEventListener('click', () => {
            document.querySelectorAll('.team-btn[data-team]')
                .forEach((b) => b.classList.remove('active'));
            button.classList.add('active');
            state.side = button.dataset.team;
        });
    });

    $('btn-undo').addEventListener('click', undoLast);
    $('btn-period').addEventListener('click', advancePeriod);
    $('btn-sub').addEventListener('click', () => openSubSheet());
    $('btn-sub-cancel').addEventListener('click', () =>
        $('overlay-sub').classList.remove('open'));
    $('btn-sub-confirm').addEventListener('click', confirmSub);
    $('btn-who-skip').addEventListener('click', async () => {
        $('overlay-who').classList.remove('open');
        const pending = state.pendingEvent;
        state.pendingEvent = null;
        if (pending) await commitEvent(pending.type, pending.matchClockS, null);
    });
    $('btn-log').addEventListener('click', openLog);
    $('btn-log-close').addEventListener('click', () =>
        $('overlay-log').classList.remove('open'));

    onUser(async (user) => {
        if (!user) {
            $('signin-block').classList.remove('hidden');
            $('match-block').classList.add('hidden');
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
            $('setup-sub').textContent = access.teams[0].name;
            $('signin-block').classList.add('hidden');
            $('match-block').classList.remove('hidden');
            await loadMatches();
        } catch (err) {
            toast(err.message || 'Could not load your team.', true);
        }
    });
}

init();
