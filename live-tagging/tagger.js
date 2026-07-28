'use strict';

const $ = (id) => document.getElementById(id);

const PERIOD_SEQUENCE = [
    { period: 'kickoff_2nd', label: '2nd half', button: 'Full time' },
    { period: 'full_time', label: 'Full time', button: null },
];

const state = {
    matchId: null,
    homeTeamId: null,
    awayTeamId: null,
    ourTeamId: null,
    roster: [],
    selectedTeam: 'home',
    kickoffAt: null,     // epoch ms of the most recent kickoff tap
    clockOffset: 0,      // seconds already elapsed before that kickoff
    running: false,
    periodIndex: 0,
    sub: { team: 'home', outId: null, inId: null },
};

// ---------------------------------------------------------------- api
async function api(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    if (!response.ok) {
        let message = `${response.status} ${response.statusText}`;
        try {
            const body = await response.json();
            if (body.detail) message = body.detail;
        } catch { /* non-JSON error body */ }
        throw new Error(message);
    }
    return response.status === 204 ? null : response.json();
}

let toastTimer;
function toast(message, isError = false) {
    const el = $('toast');
    el.textContent = message;
    el.classList.toggle('error', isError);
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2200);
}

function showView(name) {
    document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
    $(`view-${name}`).classList.add('active');
}

// ---------------------------------------------------------------- clock
function matchClock() {
    if (!state.running) return state.clockOffset;
    return state.clockOffset + (Date.now() - state.kickoffAt) / 1000;
}

function renderClock() {
    const total = Math.floor(matchClock());
    const mm = String(Math.floor(total / 60)).padStart(2, '0');
    const ss = String(total % 60).padStart(2, '0');
    $('clock').textContent = `${mm}:${ss}`;
}
setInterval(renderClock, 250);

// ---------------------------------------------------------------- setup
async function loadTeams() {
    const teams = await api('/teams');
    for (const id of ['select-home', 'select-away']) {
        const select = $(id);
        select.innerHTML = '<option value="">— select —</option>';
        for (const team of teams) {
            const option = document.createElement('option');
            option.value = team.id;
            option.textContent = team.name;
            select.appendChild(option);
        }
    }
}

async function loadMatches() {
    const matches = await api('/matches');
    const select = $('select-match');
    select.innerHTML = '<option value="">— new match —</option>';
    for (const match of matches) {
        const option = document.createElement('option');
        option.value = match.id;
        option.textContent = `#${match.id} · ${match.kickoff_date || 'no date'} · ${match.status}`;
        select.appendChild(option);
    }
}

async function resolveTeam(selectId, inputId) {
    const typed = $(inputId).value.trim();
    if (typed) {
        const team = await api('/teams', {
            method: 'POST',
            body: JSON.stringify({ name: typed }),
        });
        return team.id;
    }
    const chosen = $(selectId).value;
    return chosen ? Number(chosen) : null;
}

async function createMatch() {
    try {
        const homeId = await resolveTeam('select-home', 'input-new-home');
        const awayId = await resolveTeam('select-away', 'input-new-away');

        if (!homeId || !awayId) return toast('Pick or name both teams', true);
        if (homeId === awayId) return toast('Teams must be different', true);

        const match = await api('/matches', {
            method: 'POST',
            body: JSON.stringify({
                home_team_id: homeId,
                away_team_id: awayId,
                kickoff_date: new Date().toISOString().slice(0, 10),
            }),
        });

        state.matchId = match.id;
        state.homeTeamId = homeId;
        state.awayTeamId = awayId;
        state.ourTeamId = homeId;

        $('input-new-home').value = '';
        $('input-new-away').value = '';
        await loadTeams();
        await refreshRoster();
        $('lineup-section').classList.remove('hidden');
        toast(`Match #${match.id} created`);
    } catch (err) {
        toast(err.message, true);
    }
}

async function refreshRoster() {
    const team = await api(`/teams/${state.ourTeamId}`);
    // Preserve starter ticks across re-renders.
    const starters = new Set(state.roster.filter((p) => p.isStarter).map((p) => p.id));
    state.roster = team.players.map((p) => ({ ...p, isStarter: starters.has(p.id) }));
    renderRoster();
}

function renderRoster() {
    const list = $('roster-list');
    list.innerHTML = '';

    if (state.roster.length === 0) {
        const li = document.createElement('li');
        li.innerHTML = '<span class="tag">No players yet — add them above</span>';
        list.appendChild(li);
        return;
    }

    for (const player of state.roster) {
        const li = document.createElement('li');
        li.className = player.isStarter ? 'starter' : '';
        li.innerHTML = `
            <span class="num">${player.jersey_number ?? '—'}</span>
            <span class="nm"></span>
            <span class="tag">${player.isStarter ? 'starting' : 'bench'}</span>`;
        li.querySelector('.nm').textContent = player.name;
        li.addEventListener('click', () => {
            player.isStarter = !player.isStarter;
            renderRoster();
        });
        list.appendChild(li);
    }
}

async function addPlayer() {
    const name = $('input-player-name').value.trim();
    if (!name) return toast('Enter a name', true);
    const numberRaw = $('input-player-number').value.trim();

    try {
        await api(`/teams/${state.ourTeamId}/players`, {
            method: 'POST',
            body: JSON.stringify({
                name,
                jersey_number: numberRaw ? Number(numberRaw) : null,
            }),
        });
        $('input-player-name').value = '';
        $('input-player-number').value = '';
        await refreshRoster();
    } catch (err) {
        toast(err.message, true);
    }
}

async function saveLineup() {
    if (state.roster.length === 0) return toast('Add some players first', true);

    try {
        await api(`/matches/${state.matchId}/lineup`, {
            method: 'POST',
            body: JSON.stringify({
                team_id: state.ourTeamId,
                entries: state.roster.map((p) => ({
                    player_id: p.id,
                    is_starter: p.isStarter,
                })),
            }),
        });
        toast('Lineup saved');
        showView('kickoff');
    } catch (err) {
        toast(err.message, true);
    }
}

async function resumeMatch() {
    const chosen = $('select-match').value;
    if (!chosen) return toast('Pick a match to resume', true);

    try {
        const match = await api(`/matches/${chosen}`);
        state.matchId = match.id;
        state.homeTeamId = match.home_team_id;
        state.awayTeamId = match.away_team_id;
        state.ourTeamId = match.home_team_id;

        const log = await api(`/matches/${match.id}/log`);
        const last = log.length ? log[log.length - 1].match_clock_s : 0;

        // Resuming picks the clock up where the log left off, paused. A running
        // clock would silently invent time that nobody actually tagged.
        state.clockOffset = last;
        state.running = match.status === 'first_half' || match.status === 'second_half';
        state.kickoffAt = Date.now();
        state.periodIndex = match.status === 'second_half' ? 1 : 0;

        applyTeamLabels();
        updatePeriodButton();
        renderClock();
        showView(match.status === 'scheduled' ? 'kickoff' : 'live');
        toast(`Resumed match #${match.id}`);
    } catch (err) {
        toast(err.message, true);
    }
}

// ---------------------------------------------------------------- live
async function doKickoff() {
    try {
        await api(`/matches/${state.matchId}/period`, {
            method: 'POST',
            body: JSON.stringify({ period: 'kickoff_1st', match_clock_s: 0 }),
        });
        state.clockOffset = 0;
        state.kickoffAt = Date.now();
        state.running = true;
        state.periodIndex = 0;
        applyTeamLabels();
        updatePeriodButton();
        showView('live');
        toast('Match started');
    } catch (err) {
        toast(err.message, true);
    }
}

function currentTeamId() {
    return state.selectedTeam === 'home' ? state.homeTeamId : state.awayTeamId;
}

async function applyTeamLabels() {
    try {
        const [home, away] = await Promise.all([
            api(`/teams/${state.homeTeamId}`),
            api(`/teams/${state.awayTeamId}`),
        ]);
        $('btn-team-home').textContent = home.name;
        $('btn-team-away').textContent = away.name;
        document.querySelector('[data-subteam="home"]').textContent = home.name;
        document.querySelector('[data-subteam="away"]').textContent = away.name;
    } catch { /* labels are cosmetic; ignore */ }
}

async function tagEvent(eventType, button) {
    button.classList.add('flash');
    setTimeout(() => button.classList.remove('flash'), 160);

    try {
        await api(`/matches/${state.matchId}/events`, {
            method: 'POST',
            body: JSON.stringify({
                event_type: eventType,
                match_clock_s: matchClock(),
                team_id: currentTeamId(),
            }),
        });
        toast(eventType.replace(/_/g, ' '));
    } catch (err) {
        toast(err.message, true);
    }
}

async function undoLast() {
    try {
        const result = await api(`/matches/${state.matchId}/undo`, { method: 'POST' });
        toast(result.description, !result.undone);
    } catch (err) {
        toast(err.message, true);
    }
}

function updatePeriodButton() {
    const next = PERIOD_SEQUENCE[state.periodIndex];
    const button = $('btn-period');
    if (!next) {
        button.disabled = true;
        button.textContent = 'Match over';
        return;
    }
    button.disabled = false;
    button.textContent = state.periodIndex === 0 ? 'Halftime' : next.button || 'Full time';
}

async function advancePeriod() {
    // Halftime pauses the clock; the 2nd-half kickoff resumes it from 45:00.
    const isHalftime = state.periodIndex === 0 && state.running;

    try {
        if (isHalftime) {
            const now = matchClock();
            await api(`/matches/${state.matchId}/period`, {
                method: 'POST',
                body: JSON.stringify({ period: 'halftime', match_clock_s: now }),
            });
            state.clockOffset = now;
            state.running = false;
            $('period-label').textContent = 'halftime';
            $('btn-period').textContent = 'Start 2nd half';
            toast('Halftime');
            return;
        }

        if (state.periodIndex === 0) {
            await api(`/matches/${state.matchId}/period`, {
                method: 'POST',
                body: JSON.stringify({ period: 'kickoff_2nd', match_clock_s: state.clockOffset }),
            });
            state.kickoffAt = Date.now();
            state.running = true;
            state.periodIndex = 1;
            $('period-label').textContent = '2nd half';
            updatePeriodButton();
            toast('Second half');
            return;
        }

        await api(`/matches/${state.matchId}/period`, {
            method: 'POST',
            body: JSON.stringify({ period: 'full_time', match_clock_s: matchClock() }),
        });
        state.clockOffset = matchClock();
        state.running = false;
        state.periodIndex = 2;
        $('period-label').textContent = 'full time';
        updatePeriodButton();
        toast('Full time');
    } catch (err) {
        toast(err.message, true);
    }
}

// ---------------------------------------------------------------- substitutions
function subTeamId() {
    return state.sub.team === 'home' ? state.homeTeamId : state.awayTeamId;
}

async function openSubSheet() {
    state.sub.outId = null;
    state.sub.inId = null;
    $('overlay-sub').classList.add('open');
    await renderSubLists();
}

async function renderSubLists() {
    const offList = $('sub-off-list');
    const onList = $('sub-on-list');
    offList.innerHTML = '';
    onList.innerHTML = '';

    let data;
    try {
        data = await api(`/matches/${state.matchId}/onfield?team_id=${subTeamId()}`);
    } catch (err) {
        offList.innerHTML = `<li class="empty">${err.message}</li>`;
        return;
    }

    const row = (entry, selectedId, onPick, isUsed = false) => {
        const li = document.createElement('li');
        li.className = [
            entry.player_id === selectedId ? 'selected' : '',
            isUsed ? 'used' : '',
        ].filter(Boolean).join(' ');
        li.innerHTML = `<span class="num">${entry.player.jersey_number ?? '—'}</span><span class="nm"></span>`;
        li.querySelector('.nm').textContent = entry.player.name;
        if (!isUsed) li.addEventListener('click', () => onPick(entry.player_id));
        return li;
    };

    if (data.on_field.length === 0) {
        offList.innerHTML = '<li class="empty">Nobody on the field — set a lineup first.</li>';
    }
    for (const entry of data.on_field) {
        offList.appendChild(row(entry, state.sub.outId, (id) => {
            state.sub.outId = id;
            renderSubLists();
        }));
    }

    if (data.available.length === 0 && data.used.length === 0) {
        onList.innerHTML = '<li class="empty">No substitutes available.</li>';
    }
    for (const entry of data.available) {
        onList.appendChild(row(entry, state.sub.inId, (id) => {
            state.sub.inId = id;
            renderSubLists();
        }));
    }
    // Already-subbed-off players stay visible but dimmed, so a used sub never
    // looks identical to one who hasn't played.
    for (const entry of data.used) {
        onList.appendChild(row(entry, null, () => {}, true));
    }

    $('btn-sub-confirm').disabled = !(state.sub.outId && state.sub.inId);
}

async function confirmSub() {
    try {
        await api(`/matches/${state.matchId}/substitutions`, {
            method: 'POST',
            body: JSON.stringify({
                team_id: subTeamId(),
                player_out_id: state.sub.outId,
                player_in_id: state.sub.inId,
                match_clock_s: matchClock(),
            }),
        });
        $('overlay-sub').classList.remove('open');
        toast('Substitution logged');
    } catch (err) {
        toast(err.message, true);
    }
}

// ---------------------------------------------------------------- log
async function openLog() {
    const list = $('log-list');
    list.innerHTML = '<li class="empty">Loading…</li>';
    $('overlay-log').classList.add('open');

    try {
        const entries = await api(`/matches/${state.matchId}/log`);
        list.innerHTML = '';
        if (entries.length === 0) {
            list.innerHTML = '<li class="empty">Nothing logged yet.</li>';
            return;
        }
        for (const entry of entries.slice().reverse()) {
            const mm = String(Math.floor(entry.match_clock_s / 60)).padStart(2, '0');
            const ss = String(Math.floor(entry.match_clock_s % 60)).padStart(2, '0');
            const li = document.createElement('li');
            li.innerHTML = `<span class="t">${mm}:${ss}</span><span class="l"></span>`;
            li.querySelector('.l').textContent = entry.label;
            list.appendChild(li);
        }
    } catch (err) {
        list.innerHTML = `<li class="empty">${err.message}</li>`;
    }
}

// ---------------------------------------------------------------- wiring
function init() {
    $('btn-create-match').addEventListener('click', createMatch);
    $('btn-resume').addEventListener('click', resumeMatch);
    $('btn-add-player').addEventListener('click', addPlayer);
    $('input-player-name').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addPlayer();
    });
    $('btn-save-lineup').addEventListener('click', saveLineup);

    $('btn-kickoff').addEventListener('click', doKickoff);
    $('btn-back-setup').addEventListener('click', () => showView('setup'));

    document.querySelectorAll('.ev').forEach((button) => {
        button.addEventListener('click', () => tagEvent(button.dataset.event, button));
    });

    document.querySelectorAll('.team-toggle .team-btn[data-team]').forEach((button) => {
        button.addEventListener('click', () => {
            document.querySelectorAll('.team-btn[data-team]').forEach((b) => b.classList.remove('active'));
            button.classList.add('active');
            state.selectedTeam = button.dataset.team;
        });
    });

    document.querySelectorAll('.team-btn[data-subteam]').forEach((button) => {
        button.addEventListener('click', () => {
            document.querySelectorAll('.team-btn[data-subteam]').forEach((b) => b.classList.remove('active'));
            button.classList.add('active');
            state.sub.team = button.dataset.subteam;
            state.sub.outId = null;
            state.sub.inId = null;
            renderSubLists();
        });
    });

    $('btn-undo').addEventListener('click', undoLast);
    $('btn-period').addEventListener('click', advancePeriod);
    $('btn-sub').addEventListener('click', openSubSheet);
    $('btn-sub-cancel').addEventListener('click', () => $('overlay-sub').classList.remove('open'));
    $('btn-sub-confirm').addEventListener('click', confirmSub);
    $('btn-log').addEventListener('click', openLog);
    $('btn-log-close').addEventListener('click', () => $('overlay-log').classList.remove('open'));

    Promise.all([loadTeams(), loadMatches()]).catch((err) =>
        toast(`Cannot reach backend: ${err.message}`, true)
    );
}

init();
