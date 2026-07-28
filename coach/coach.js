import {
    onUser, signOut, resolveAccess, rememberTeam, configWarning,
} from '../assets/auth.js';
import {
    createTeam, getTeam, listPlayers, addPlayer, removePlayer, invitePlayer,
    listMatches, getMatch, createMatch, listMatchRoster, listLog,
    aggregateMatch, publishReports, seasonSummary, playerSeason, seasonTotals,
} from '../assets/db.js';
import { EVENT_TYPES, CARD_COLOURS, describeEvent } from '../assets/events.js';
import { mountPitchBackdrop } from '../assets/pitch-backdrop.js';

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
    for (const v of ['view-noteam', 'view-main', 'view-match', 'view-player']) {
        $(v).classList.toggle('hidden', v !== view);
    }
    $('loading').classList.add('hidden');
}

function clock(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

const labels = () => ({
    usName: state.team?.name || 'Us',
    themName: state.match?.opponentName || 'Them',
});

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
                ? 'This email has not been approved to create a team yet. Ask Alex to add it, then try again.'
                : err.message || 'Could not create the team.',
            true,
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
    renderHero();
    renderRoster();
    renderMatches();
}

function renderHero() {
    mountPitchBackdrop($('team-hero'), { opacity: 0.16 });

    const summary = seasonSummary(state.matches);
    $('hero-team').textContent = state.team.name;
    $('hero-record').textContent = summary.record;

    const gd = $('hero-gd');
    gd.textContent = summary.goalDifference > 0 ? `+${summary.goalDifference}` : summary.goalDifference;
    gd.classList.toggle('pos', summary.goalDifference > 0);
    gd.classList.toggle('neg', summary.goalDifference < 0);

    const grid = $('hero-stats');
    grid.innerHTML = '';
    grid.appendChild(statCard(summary.played, 'Played'));
    grid.appendChild(statCard(summary.scored, 'Scored', summary.scored ? 'is-good' : 'is-muted'));
    grid.appendChild(statCard(summary.conceded, 'Conceded', 'is-muted'));
    grid.appendChild(statCard(state.players.length, 'Squad', 'is-muted'));

    $('count-matches').textContent = state.matches.length;
    $('count-roster').textContent = state.players.length;

    const open = state.matches.filter((m) => !m.finalized).length;
    $('action-tag-sub').textContent = open
        ? `${open} match${open === 1 ? '' : 'es'} ready to tag`
        : 'Create a match first';

    renderGettingStarted();
}

/** Tick off setup steps as they're actually completed, and hide once done. */
function renderGettingStarted() {
    const hasPlayers = state.players.length > 0;
    const hasInvited = state.players.some((p) => p.linkedUid);
    const hasMatch = state.matches.length > 0;
    const hasTagged = state.matches.some(
        (m) => m.status && m.status !== 'scheduled'
    );

    const done = { 'step-players': hasPlayers, 'step-invite': hasInvited,
                   'step-match': hasMatch, 'step-tag': hasTagged };
    for (const [id, complete] of Object.entries(done)) {
        $(id)?.classList.toggle('done', complete);
    }

    // Once every step is done the checklist is clutter, so it goes away.
    $('getting-started').classList.toggle(
        'hidden', hasPlayers && hasInvited && hasMatch && hasTagged
    );
}

function renderRoster() {
    const list = $('roster-list');
    list.innerHTML = '';

    if (!state.players.length) {
        list.innerHTML = '<div class="empty">No players yet. Add your squad above.</div>';
        return;
    }

    for (const player of state.players) {
        const row = document.createElement('div');
        row.className = 'list-item roster-row';
        row.innerHTML = `
            <span class="jersey"></span>
            <button class="grow" data-act="open"
                    style="background:none;border:none;text-align:left;cursor:pointer;color:inherit;font:inherit;padding:0">
                <div class="title"></div>
                <div class="sub"></div>
            </button>
            <span class="pill"></span>
            <button class="btn small" data-act="invite">Invite</button>
            <button class="btn small danger" data-act="remove">Remove</button>
            <span class="open-hint">&rsaquo;</span>`;

        row.querySelector('[data-act="open"]').addEventListener('click', () =>
            openPlayer(player));

        row.querySelector('.jersey').textContent = player.jerseyNumber ?? '—';
        row.querySelector('.title').textContent = player.name;
        row.querySelector('.sub').textContent = player.emailLower || 'No email yet — they cannot see their report without one';

        const pill = row.querySelector('.pill');
        const linked = Boolean(player.linkedUid);
        pill.textContent = linked ? 'Can see reports' : 'Not joined yet';
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
        for (const id of ['input-player-name', 'input-player-number', 'input-player-email']) {
            $(id).value = '';
        }
        state.players = await listPlayers(state.team.id);
        renderRoster();
        toast(`${name} added`);
    } catch (err) {
        toast(err.message || 'Could not add the player.', true);
    }
}

// ---------------------------------------------------------------- one player

function pvFigure(value, label) {
    const el = document.createElement('div');
    el.className = 'pv-figure';
    el.innerHTML = `<div class="n"></div><div class="k"></div>`;
    const n = el.querySelector('.n');
    n.textContent = value;
    if (!value) n.classList.add('zero');
    el.querySelector('.k').textContent = label;
    return el;
}

async function openPlayer(player) {
    $('loading').classList.remove('hidden');

    try {
        const reports = await playerSeason(state.team.id, state.matches, player.id);
        const totals = seasonTotals(reports);

        $('pv-number').textContent = player.jerseyNumber ?? '—';
        $('pv-name').textContent = player.name;
        $('pv-sub').textContent = player.emailLower || 'no email on file';

        const linked = $('pv-linked');
        const isLinked = Boolean(player.linkedUid);
        linked.textContent = isLinked ? 'Can see reports' : 'Not joined yet';
        linked.classList.toggle('done', isLinked);

        const stats = $('pv-stats');
        stats.innerHTML = '';
        stats.appendChild(statCard(totals.matches, 'Matches'));
        stats.appendChild(statCard(totals.minutes, 'Minutes'));
        stats.appendChild(statCard(totals.goals, 'Goals', totals.goals ? 'is-good' : 'is-muted'));
        stats.appendChild(statCard(totals.assists, 'Assists', totals.assists ? 'is-good' : 'is-muted'));
        stats.appendChild(statCard(totals.fouls, 'Fouls', 'is-muted'));

        const cards = totals.yellowCards + totals.redCards;
        stats.appendChild(statCard(cards, 'Cards', cards ? 'is-warn' : 'is-muted'));

        // Per 90 is the number that survives uneven minutes, which is exactly
        // the comparison a coach wants between a starter and a squad player.
        if (totals.minutes >= 45) {
            const per90 = ((totals.goals + totals.assists) / totals.minutes * 90).toFixed(2);
            stats.appendChild(statCard(per90, 'G+A per 90', 'is-muted'));
        }

        renderPlayerMatches(reports);
        show('view-player');
        window.scrollTo(0, 0);
    } catch (err) {
        toast(err.message || 'Could not load that player.', true);
        show('view-main');
    }
}

function renderPlayerMatches(reports) {
    const list = $('pv-matches');
    list.innerHTML = '';

    if (!reports.length) {
        list.innerHTML =
            '<div class="empty">Nothing published for this player yet.<br>'
            + '<span class="muted">Open a match and publish its reports.</span></div>';
        return;
    }

    for (const report of reports) {
        const row = document.createElement('div');
        row.className = 'list-item pv-row';
        row.innerHTML = `
            <div class="grow">
                <div class="title"></div>
                <div class="sub"></div>
            </div>
            <div class="pv-figures"></div>`;

        row.querySelector('.title').textContent = `vs ${report.opponentName || '—'}`;

        const sub = row.querySelector('.sub');
        sub.textContent = report.matchDate || '';

        const chips = [
            ...Array(report.yellowCards || 0).fill('yellow'),
            ...Array(report.redCards || 0).fill('red'),
        ];
        for (const colour of chips) {
            const chip = document.createElement('span');
            chip.className = `card-chip ${colour}`;
            chip.style.marginLeft = '6px';
            chip.title = CARD_COLOURS[colour]?.label ?? colour;
            sub.appendChild(chip);
        }

        const figures = row.querySelector('.pv-figures');
        figures.appendChild(pvFigure(report.minutesPlayed ?? 0, 'min'));
        figures.appendChild(pvFigure(report.goals ?? 0, 'goals'));
        figures.appendChild(pvFigure(report.assists ?? 0, 'assists'));

        list.appendChild(row);
    }
}

// ---------------------------------------------------------------- matches

function renderMatches() {
    const list = $('match-list');
    list.innerHTML = '';

    if (!state.matches.length) {
        list.innerHTML =
            '<div class="empty">No matches yet.<br>'
            + '<span class="muted">Add one on the right, then tag it live from the touchline.</span></div>';
        return;
    }

    for (const match of state.matches) {
        const row = document.createElement('button');
        row.className = 'list-item';
        row.innerHTML = `
            <div class="grow">
                <div class="title"></div>
                <div class="sub"></div>
            </div>
            <span class="match-score hidden"></span>
            <span class="pill"></span>`;

        row.querySelector('.title').textContent = `vs ${match.opponentName || '—'}`;
        row.querySelector('.sub').textContent = match.date || 'no date';

        // A finalized match shows its result, colour-coded, instead of just a
        // status word — that is the thing a coach is actually scanning for.
        if (match.finalized) {
            const us = match.scoreUs ?? 0;
            const them = match.scoreThem ?? 0;
            const score = row.querySelector('.match-score');
            score.textContent = `${us}–${them}`;
            score.classList.remove('hidden');
            score.classList.add(us > them ? 'win' : us < them ? 'loss' : 'draw');
        }

        const pill = row.querySelector('.pill');
        if (match.finalized) {
            pill.textContent = 'published';
            pill.classList.add('done');
        } else if (['first_half', 'second_half'].includes(match.status)) {
            pill.textContent = 'live';
            pill.classList.add('live');
        } else {
            pill.textContent = (match.status || 'scheduled').replace(/_/g, ' ');
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
        state.match = { ...match, stats, log, roster };

        $('match-title').textContent = `vs ${match.opponentName || '—'}`;
        $('match-sub').textContent =
            `${match.date || 'no date'} · ${(match.status || 'scheduled').replace(/_/g, ' ')}`
            + (match.finalized ? ' · reports published' : '');

        $('score-us').textContent = stats.counts.us.goal ?? 0;
        $('score-them').textContent = stats.counts.them.goal ?? 0;

        renderTeamStats(stats);
        renderPlayerTable(stats.players);
        renderTimeline(log, roster);

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

function statCard(value, label, tone = '') {
    const el = document.createElement('div');
    el.className = `stat ${tone}`;
    el.innerHTML = `<div class="value"></div><div class="label"></div>`;
    el.querySelector('.value').textContent = value ?? 0;
    el.querySelector('.label').textContent = label;
    return el;
}

function renderTeamStats(stats) {
    const grid = $('team-stats');
    grid.innerHTML = '';

    const us = stats.counts.us;
    const them = stats.counts.them;

    // Corners and free kicks are counted for whoever was awarded them; fouls,
    // cards and offside are recorded against the offender, so "our fouls" means
    // fouls we committed. Labels say so explicitly.
    const cards = [
        [us.goal, 'Goals for', 'is-good'],
        [them.goal, 'Goals against', ''],
        [us.corner, 'Corners won', ''],
        [them.corner, 'Corners conceded', 'is-muted'],
        [us.foul, 'Fouls committed', ''],
        [them.foul, 'Fouls won', 'is-muted'],
        [us.card, 'Our cards', us.card ? 'is-warn' : 'is-muted'],
        [us.offside, 'Offsides against us', 'is-muted'],
        [stats.subs, 'Substitutions', 'is-muted'],
    ];

    for (const [value, label, tone] of cards) grid.appendChild(statCard(value, label, tone));
}

function renderPlayerTable(players) {
    const body = $('player-table').querySelector('tbody');
    body.innerHTML = '';

    if (!players.length) {
        body.innerHTML = '<tr><td colspan="7" class="muted">No lineup was set for this match.</td></tr>';
        return;
    }

    // Most involved first — a coach scanning this wants the standouts on top.
    const ordered = [...players].sort(
        (a, b) => (b.goals + b.assists) - (a.goals + a.assists)
            || b.minutesPlayed - a.minutesPlayed,
    );

    for (const player of ordered) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td class="num jersey-cell"></td>
            <td class="name-cell"></td>
            <td class="num"></td>
            <td class="num"></td>
            <td class="num"></td>
            <td class="num"></td>
            <td><span class="cards-cell"></span></td>`;

        const cells = tr.querySelectorAll('td');
        cells[0].textContent = player.jerseyNumber ?? '—';
        cells[1].textContent = player.playerName;

        const numbers = [player.minutesPlayed, player.goals, player.assists, player.fouls];
        numbers.forEach((value, i) => {
            const cell = cells[i + 2];
            cell.textContent = value;
            // Zeroes recede so the meaningful numbers carry the eye.
            if (!value) cell.classList.add('zero');
        });

        const cardsCell = tr.querySelector('.cards-cell');
        const chips = [
            ...Array(player.yellowCards || 0).fill('yellow'),
            ...Array(player.redCards || 0).fill('red'),
        ];
        if (!chips.length) {
            cardsCell.innerHTML = '<span class="none">—</span>';
        } else {
            for (const colour of chips) {
                const chip = document.createElement('span');
                chip.className = `card-chip ${colour}`;
                chip.title = CARD_COLOURS[colour]?.label ?? colour;
                cardsCell.appendChild(chip);
            }
        }

        body.appendChild(tr);
    }
}

function renderTimeline(log, roster) {
    const list = $('timeline');
    list.innerHTML = '';

    if (!log.length) {
        list.innerHTML = '<div class="empty">Nothing was tagged for this match.</div>';
        return;
    }

    const nameById = new Map(roster.map((r) => [r.id, r.playerName]));

    for (const entry of log.slice().reverse()) {
        const spec = EVENT_TYPES[entry.type];
        const row = document.createElement('div');
        row.className = 'tl-row';
        row.innerHTML = `
            <span class="tl-clock"></span>
            <span class="tl-marker"><span class="tl-dot"></span></span>
            <span class="tl-text"></span>
            <span class="tl-side"></span>`;

        row.querySelector('.tl-clock').textContent = clock(entry.matchClockS);

        const text = row.querySelector('.tl-text');
        text.textContent = describeEvent(entry, {
            ...labels(),
            playerName: nameById.get(entry.playerId),
        });

        if (entry.assistPlayerId && nameById.has(entry.assistPlayerId)) {
            const assist = document.createElement('span');
            assist.className = 'who';
            assist.textContent = ` (assist: ${nameById.get(entry.assistPlayerId)})`;
            text.appendChild(assist);
        }

        row.querySelector('.tl-side').textContent =
            entry.kind === 'period' ? '' : (entry.side === 'them' ? labels().themName : labels().usName);

        const dot = row.querySelector('.tl-dot');
        if (entry.kind === 'period') dot.classList.add('period');
        else if (spec?.tone === 'good') dot.classList.add('good');
        else if (spec?.tone === 'warn') dot.classList.add('warn');

        list.appendChild(row);
    }
}

async function doPublish() {
    const button = $('btn-publish');
    button.disabled = true;

    try {
        await publishReports(
            state.team.id, state.match.id, state.match, state.team,
            state.match.stats.players,
            {
                us: state.match.stats.counts.us.goal ?? 0,
                them: state.match.stats.counts.them.goal ?? 0,
            },
        );
        toast('Player reports published');
        state.matches = await listMatches(state.team.id);
        renderHero();
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
            document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
            tab.classList.add('active');
            $('tab-matches').classList.toggle('hidden', tab.dataset.tab !== 'matches');
            $('tab-roster').classList.toggle('hidden', tab.dataset.tab !== 'roster');
        });
    }
}

async function onSignedIn(user) {
    state.user = user;

    const access = await resolveAccess(user);
    // A coach with no team yet resolves as 'none' — there is nothing proving
    // they coach anything until a team exists, so let them through to create one.
    if (access.role === 'player') { location.href = '../player/'; return; }

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

    $('btn-signout').addEventListener('click', () =>
        signOut().then(() => { location.href = '../'; }));
    $('btn-create-team').addEventListener('click', doCreateTeam);
    $('btn-add-player').addEventListener('click', doAddPlayer);
    $('btn-create-match').addEventListener('click', doCreateMatch);
    $('btn-back').addEventListener('click', () => show('view-main'));
    $('btn-back-roster').addEventListener('click', () => show('view-main'));
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
