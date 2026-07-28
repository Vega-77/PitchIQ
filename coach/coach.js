import {
    onUser, signOut, resolveAccess, rememberTeam, configWarning,
} from '../assets/auth.js';
import {
    createTeam, getTeam, listPlayers, addPlayer, removePlayer, invitePlayer,
    listMatches, getMatch, createMatch, listMatchRoster, listLog,
    aggregateMatch, publishReports, EVENT_TYPES,
} from '../assets/db.js';

const $ = (id) => document.getElementById(id);

const state = {
    user: null,
    team: null,
    players: [],
    matches: [],
    match: null,
};

let toastTimer;
function toast(message, isError = false) {
    const el = $('toast');
    el.textContent = message;
    el.classList.toggle('error', isError);
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2800);
}

function show(view) {
    for (const v of ['view-noteam', 'view-main', 'view-match']) {
        $(v).classList.toggle('hidden', v !== view);
    }
    $('loading').classList.add('hidden');
}

const label = (type) => type.replace(/_/g, ' ');

function clock(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

// ---------------------------------------------------------------- team setup

async function doCreateTeam() {
    const name = $('input-team-name').value.trim();
    if (!name) return toast('Give your team a name', true);

    const button = $('btn-create-team');
    button.disabled = true;
    try {
        const teamId = await createTeam(state.user, name);
        await rememberTeam(state.user, teamId);
        state.team = await getTeam(teamId);
        await loadTeamData();
        show('view-main');
        toast('Team created');
    } catch (err) {
        button.disabled = false;
        toast(
            err?.code === 'permission-denied'
                ? 'This account is not on the coach allowlist. Ask the project owner to add it.'
                : err.message || 'Could not create the team.',
            true
        );
    }
}

// ---------------------------------------------------------------- roster

async function loadTeamData() {
    $('team-name').textContent = state.team.name;
    [state.players, state.matches] = await Promise.all([
        listPlayers(state.team.id),
        listMatches(state.team.id),
    ]);
    renderRoster();
    renderMatches();
}

function renderRoster() {
    const list = $('roster-list');
    list.innerHTML = '';

    if (!state.players.length) {
        list.innerHTML = '<div class="empty">No players yet.</div>';
        return;
    }

    for (const player of state.players) {
        const row = document.createElement('div');
        row.className = 'list-item';
        row.innerHTML = `
            <span class="jersey"></span>
            <div class="grow">
                <div class="title"></div>
                <div class="sub"></div>
            </div>
            <span class="pill"></span>
            <button class="btn small ghost" data-act="invite">Invite</button>
            <button class="btn small danger" data-act="remove">Remove</button>`;

        row.querySelector('.jersey').textContent = player.jerseyNumber ?? '—';
        row.querySelector('.title').textContent = player.name;
        row.querySelector('.sub').textContent = player.emailLower || 'no email';

        const pill = row.querySelector('.pill');
        const linked = Boolean(player.linkedUid);
        pill.textContent = linked ? 'linked' : 'not signed in';
        pill.classList.toggle('done', linked);

        row.querySelector('[data-act="invite"]').addEventListener('click', async (e) => {
            e.target.disabled = true;
            try {
                await invitePlayer(state.user, state.team, player);
                toast(`Invitation sent to ${player.emailLower}`);
            } catch (err) {
                toast(err.message || 'Could not send the invitation.', true);
            } finally {
                e.target.disabled = false;
            }
        });

        row.querySelector('[data-act="remove"]').addEventListener('click', async () => {
            if (!confirm(`Remove ${player.name} from the roster?`)) return;
            try {
                await removePlayer(state.team.id, player.id);
                state.players = state.players.filter((p) => p.id !== player.id);
                renderRoster();
                toast('Player removed');
            } catch (err) {
                toast(err.message || 'Could not remove the player.', true);
            }
        });

        list.appendChild(row);
    }
}

async function doAddPlayer() {
    const name = $('input-player-name').value.trim();
    const email = $('input-player-email').value.trim().toLowerCase();
    const numberRaw = $('input-player-number').value.trim();

    if (!name) return toast('Enter a name', true);
    if (!email) return toast('Enter the school email — it links them to their report', true);

    try {
        await addPlayer(state.team.id, {
            name,
            jerseyNumber: numberRaw ? Number(numberRaw) : null,
            email,
        });
        $('input-player-name').value = '';
        $('input-player-number').value = '';
        $('input-player-email').value = '';
        state.players = await listPlayers(state.team.id);
        renderRoster();
        toast(`${name} added`);
    } catch (err) {
        toast(err.message || 'Could not add the player.', true);
    }
}

// ---------------------------------------------------------------- matches

function renderMatches() {
    const list = $('match-list');
    list.innerHTML = '';

    if (!state.matches.length) {
        list.innerHTML = '<div class="empty">No matches yet.</div>';
        return;
    }

    for (const match of state.matches) {
        const row = document.createElement('button');
        row.className = 'list-item';
        row.style.textAlign = 'left';
        row.style.cursor = 'pointer';
        row.innerHTML = `
            <div class="grow">
                <div class="title"></div>
                <div class="sub"></div>
            </div>
            <span class="pill"></span>`;

        row.querySelector('.title').textContent = `vs ${match.opponentName || '—'}`;
        row.querySelector('.sub').textContent = match.date || 'no date';

        const pill = row.querySelector('.pill');
        if (match.finalized) {
            pill.textContent = 'published';
            pill.classList.add('done');
        } else if (['first_half', 'second_half'].includes(match.status)) {
            pill.textContent = 'live';
            pill.classList.add('live');
        } else {
            pill.textContent = label(match.status || 'scheduled');
        }

        row.addEventListener('click', () => openMatch(match.id));
        list.appendChild(row);
    }
}

async function doCreateMatch() {
    const opponentName = $('input-opponent').value.trim();
    const date = $('input-date').value || new Date().toISOString().slice(0, 10);
    if (!opponentName) return toast('Who are you playing?', true);

    try {
        await createMatch(state.user, state.team.id, { opponentName, date });
        $('input-opponent').value = '';
        state.matches = await listMatches(state.team.id);
        renderMatches();
        toast('Match created');
    } catch (err) {
        toast(err.message || 'Could not create the match.', true);
    }
}

// ---------------------------------------------------------------- one match

async function openMatch(matchId) {
    $('loading').classList.remove('hidden');

    try {
        const [match, roster, log] = await Promise.all([
            getMatch(state.team.id, matchId),
            listMatchRoster(state.team.id, matchId),
            listLog(state.team.id, matchId),
        ]);

        const stats = aggregateMatch(log, roster);
        state.match = { ...match, stats, log };

        $('match-title').textContent = `vs ${match.opponentName || '—'}`;
        $('match-sub').textContent =
            `${match.date || 'no date'} · ${label(match.status || 'scheduled')}` +
            (match.finalized ? ' · reports published' : '');

        renderTeamStats(stats);
        renderPlayerTable(stats.players);
        renderTimeline(log);

        $('btn-publish').disabled = false;
        $('btn-publish').textContent = match.finalized
            ? 'Re-publish player reports'
            : 'Publish player reports';

        show('view-match');
        window.scrollTo(0, 0);
    } catch (err) {
        toast(err.message || 'Could not open that match.', true);
        show('view-main');
    }
}

function renderTeamStats(stats) {
    const grid = $('team-stats');
    grid.innerHTML = '';

    const cards = [
        ['Goals for', stats.counts.us.goal],
        ['Goals against', stats.counts.them.goal],
        ['Corners', stats.counts.us.corner],
        ['Fouls', stats.counts.us.foul],
        ['Fouls against', stats.counts.them.foul],
        ['Cards', stats.counts.us.card],
        ['Offsides', stats.counts.us.offside],
        ['Substitutions', stats.subs],
    ];

    for (const [labelText, value] of cards) {
        const el = document.createElement('div');
        el.className = 'stat';
        el.innerHTML = `<div class="value"></div><div class="label"></div>`;
        el.querySelector('.value').textContent = value ?? 0;
        el.querySelector('.label').textContent = labelText;
        grid.appendChild(el);
    }
}

function renderPlayerTable(players) {
    const body = $('player-table').querySelector('tbody');
    body.innerHTML = '';

    if (!players.length) {
        body.innerHTML =
            '<tr><td colspan="5" class="muted">No lineup was set for this match.</td></tr>';
        return;
    }

    for (const player of players) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td class="num"></td><td></td>
            <td class="num"></td><td class="num"></td><td class="num"></td>`;
        const cells = tr.querySelectorAll('td');
        cells[0].textContent = player.jerseyNumber ?? '—';
        cells[1].textContent = player.playerName;
        cells[2].textContent = player.minutesPlayed;
        cells[3].textContent = player.goals;
        cells[4].textContent = player.cards;
        body.appendChild(tr);
    }
}

function renderTimeline(log) {
    const list = $('timeline');
    list.innerHTML = '';

    if (!log.length) {
        list.innerHTML = '<div class="empty">Nothing was tagged for this match.</div>';
        return;
    }

    for (const entry of log) {
        const row = document.createElement('div');
        row.className = 'list-item';
        row.innerHTML = `
            <span class="jersey"></span>
            <div class="grow"><div class="title"></div></div>
            <span class="muted"></span>`;
        row.querySelector('.jersey').textContent = clock(entry.matchClockS);
        row.querySelector('.title').textContent =
            entry.kind === 'sub' ? 'substitution' : label(entry.type);
        row.querySelector('.muted').textContent =
            entry.kind === 'period' ? '' : entry.side === 'them' ? 'opponent' : 'us';
        list.appendChild(row);
    }
}

async function doPublish() {
    const button = $('btn-publish');
    button.disabled = true;

    try {
        await publishReports(
            state.team.id, state.match.id, state.match, state.team,
            state.match.stats.players
        );
        toast('Player reports published');
        state.matches = await listMatches(state.team.id);
        renderMatches();
    } catch (err) {
        toast(err.message || 'Could not publish reports.', true);
    } finally {
        button.disabled = false;
    }
}

// ---------------------------------------------------------------- init

function initTabs() {
    for (const tab of document.querySelectorAll('.tab')) {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active', 'primary'));
            tab.classList.add('active', 'primary');
            $('tab-matches').classList.toggle('hidden', tab.dataset.tab !== 'matches');
            $('tab-roster').classList.toggle('hidden', tab.dataset.tab !== 'roster');
        });
    }
    document.querySelector('.tab').classList.add('primary');
}

async function onSignedIn(user) {
    state.user = user;

    const access = await resolveAccess(user);
    if (access.role !== 'coach') {
        if (access.role === 'player') { location.href = '../player/'; return; }
        location.href = '../';
        return;
    }

    const wanted = new URLSearchParams(location.search).get('team');
    state.team = access.teams.find((t) => t.id === wanted) || access.teams[0];

    if (!state.team) { show('view-noteam'); return; }

    await loadTeamData();
    show('view-main');
}

function init() {
    const warning = configWarning();
    if (warning) $('config-slot').appendChild(warning);

    initTabs();

    $('btn-signout').addEventListener('click', () => signOut().then(() => {
        location.href = '../';
    }));
    $('btn-create-team').addEventListener('click', doCreateTeam);
    $('btn-add-player').addEventListener('click', doAddPlayer);
    $('btn-create-match').addEventListener('click', doCreateMatch);
    $('btn-back').addEventListener('click', () => show('view-main'));
    $('btn-publish').addEventListener('click', doPublish);
    $('input-date').value = new Date().toISOString().slice(0, 10);

    onUser((user) => {
        if (!user) { location.href = '../'; return; }
        onSignedIn(user).catch((err) => {
            toast(err.message || 'Could not load your team.', true);
            $('loading').classList.add('hidden');
        });
    });
}

init();
