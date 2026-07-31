import {
    onUser, signOut, resolveAccess, rememberTeam, saveStaffProfile, configWarning,
} from '../assets/auth.js?v=14';
import {
    createTeam, getTeam, listPlayers, addPlayer, removePlayer, invitePlayer,
    listMatches, getMatch, createMatch, listMatchRoster, listLog,
    aggregateMatch, publishReports, seasonSummary, playerSeason, seasonTotals,
    listStaff, inviteCoach, removeCoach,
} from '../assets/db.js?v=14';
import { CARD_COLOURS, describeEvent, timelineTone } from '../assets/events.js?v=14';
import { mountPitchBackdrop } from '../assets/pitch-backdrop.js?v=14';
import {
    byId, setText, toast, showOnly, clockText, signed, plural,
    statCard, figure, cardChips, timelineRow, minutesChart,
} from '../assets/ui.js?v=14';

const VIEWS = ['view-noteam', 'view-main', 'view-match', 'view-player'];

const state = {
    user: null,
    team: null,
    // Every squad this account coaches. A head coach with varsity and JV has
    // more than one, and the switcher moves between them.
    teams: [],
    players: [],
    matches: [],
    staff: [],
    match: null,
};

const show = (view) => showOnly(view, VIEWS);

const teamLabels = () => ({
    usName: state.team?.name || 'Us',
    themName: state.match?.opponentName || 'Them',
});

// ---------------------------------------------------------------- team setup

/** Show the create form, either on first run or for an extra squad. */
function showCreateTeam() {
    const hasTeams = state.teams.length > 0;
    setText('noteam-title', hasTeams ? 'Create another squad' : 'Create your team');
    setText('noteam-lede', hasTeams
        ? 'A separate squad keeps its own roster, matches and player reports. '
          + 'You stay the coach of both and can switch between them at any time.'
        : "Set this up once, then add your roster. You'll need to be on the coach "
          + "allowlist — if this fails, that's why.");

    byId('btn-cancel-team').classList.toggle('hidden', !hasTeams);
    byId('input-team-name').value = '';
    show('view-noteam');
}

async function doCreateTeam() {
    const name = byId('input-team-name').value.trim();
    if (!name) return toast('Give your team a name', true);

    const button = byId('btn-create-team');
    button.disabled = true;
    try {
        const teamId = await createTeam(state.user, name);
        await rememberTeam(state.user, teamId);
        await saveStaffProfile(state.user, teamId).catch(() => {});

        state.team = await getTeam(teamId);
        state.teams = [...state.teams, state.team]
            .sort((a, b) => (a.name || '').localeCompare(b.name || ''));
        history.replaceState(null, '', `?team=${encodeURIComponent(teamId)}`);

        await loadTeamData();
        show('view-main');
        toast(`${name} created`);
    } catch (err) {
        toast(
            err?.code === 'permission-denied'
                ? 'This email has not been approved to create a team yet. Ask Alex to add it, then try again.'
                : err.message || 'Could not create the team.',
            true,
        );
    } finally {
        button.disabled = false;
    }
}

// ---------------------------------------------------------------- season hero

async function loadTeamData() {
    renderTeamSwitcher();

    [state.players, state.matches, state.staff] = await Promise.all([
        listPlayers(state.team.id),
        listMatches(state.team.id),
        listStaff(state.team),
    ]);

    renderHero();
    renderRoster();
    renderMatches();
    renderStaff();

    // Teams created before the staff directory existed have no entry for their
    // own coach, which would show them to a new assistant as an unnamed uid.
    // Writing it on load backfills that without a migration.
    if (!state.staff.some((s) => s.uid === state.user.uid && !s.unknown)) {
        saveStaffProfile(state.user, state.team.id)
            .then(async () => { state.staff = await listStaff(state.team); renderStaff(); })
            .catch(() => { /* display only — never worth interrupting the page */ });
    }
}

/**
 * The squad picker. Hidden entirely for a coach with one team, so the common
 * case carries no extra chrome.
 */
function renderTeamSwitcher() {
    const wrap = byId('team-switch-wrap');
    const select = byId('team-switch');
    const single = state.teams.length < 2;

    wrap.classList.toggle('hidden', single);
    byId('team-name').classList.toggle('hidden', !single);

    if (single) {
        setText('team-name', state.team.name);
        return;
    }

    select.innerHTML = '';
    for (const team of state.teams) {
        const option = document.createElement('option');
        option.value = team.id;
        option.textContent = team.name;
        option.selected = team.id === state.team.id;
        select.appendChild(option);
    }
}

async function switchTeam(teamId) {
    const team = state.teams.find((t) => t.id === teamId);
    if (!team || team.id === state.team.id) return;

    state.team = team;
    // Keep the URL honest so a reload, or a bookmark, lands on the same squad.
    history.replaceState(null, '', `?team=${encodeURIComponent(team.id)}`);

    byId('loading').classList.remove('hidden');
    try {
        await loadTeamData();
        show('view-main');
        toast(`Switched to ${team.name}`);
    } catch (err) {
        toast(err.message || 'Could not load that squad.', true);
        show('view-main');
    }
}

function renderHero() {
    mountPitchBackdrop(byId('team-hero'), { opacity: 0.16 });

    const summary = seasonSummary(state.matches);
    setText('hero-team', state.team.name);
    setText('hero-record', summary.record);

    const gd = byId('hero-gd');
    gd.textContent = signed(summary.goalDifference);
    gd.classList.toggle('pos', summary.goalDifference > 0);
    gd.classList.toggle('neg', summary.goalDifference < 0);

    const grid = byId('hero-stats');
    grid.innerHTML = '';
    grid.append(
        statCard(summary.played, 'Played'),
        statCard(summary.scored, 'Scored', summary.scored ? 'is-good' : 'is-muted'),
        statCard(summary.conceded, 'Conceded', 'is-muted'),
        statCard(state.players.length, 'Squad', 'is-muted'),
    );

    setText('count-matches', state.matches.length);
    setText('count-roster', state.players.length);
    setText('count-staff', state.staff.length);

    const open = state.matches.filter((m) => !m.finalized).length;
    setText('action-tag-sub', open
        ? `${plural(open, 'match', 'matches')} ready to tag`
        : 'Create a match first');

    renderGettingStarted();
}

/** Tick off setup steps as they're actually completed, and hide once done. */
function renderGettingStarted() {
    const done = {
        'step-players': state.players.length > 0,
        'step-invite': state.players.some((p) => p.linkedUid),
        'step-match': state.matches.length > 0,
        'step-tag': state.matches.some((m) => m.status && m.status !== 'scheduled'),
    };

    for (const [id, complete] of Object.entries(done)) {
        byId(id)?.classList.toggle('done', complete);
    }

    // Once every step is done the checklist is clutter, so it goes away.
    const allDone = Object.values(done).every(Boolean);
    byId('getting-started').classList.toggle('hidden', allDone);
}

// ---------------------------------------------------------------- roster

function renderRoster() {
    const list = byId('roster-list');
    list.innerHTML = '';

    if (!state.players.length) {
        list.innerHTML = '<div class="empty">No players yet. Add your squad above.</div>';
        return;
    }

    for (const player of state.players) {
        list.append(rosterRow(player));
    }
}

function rosterRow(player) {
    const row = document.createElement('div');
    row.className = 'list-item roster-row';
    row.innerHTML = `
        <span class="jersey"></span>
        <button class="grow open-player">
            <div class="title"></div>
            <div class="sub"></div>
        </button>
        <span class="pill"></span>
        <button class="btn small" data-act="invite">Invite</button>
        <button class="btn small danger" data-act="remove">Remove</button>
        <span class="open-hint">&rsaquo;</span>`;

    row.querySelector('.jersey').textContent = player.jerseyNumber ?? '—';
    row.querySelector('.title').textContent = player.name;
    row.querySelector('.sub').textContent = player.emailLower
        || 'No email yet — they cannot see their report without one';

    const linked = Boolean(player.linkedUid);
    const pill = row.querySelector('.pill');
    pill.textContent = linked ? 'Can see reports' : 'Not joined yet';
    pill.classList.toggle('done', linked);

    row.querySelector('.open-player').addEventListener('click', () => openPlayer(player));

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

    return row;
}

async function doAddPlayer() {
    const name = byId('input-player-name').value.trim();
    const email = byId('input-player-email').value.trim().toLowerCase();
    const numberRaw = byId('input-player-number').value.trim();

    if (!name) return toast('Enter a name', true);
    if (!email) return toast('Enter the school email — it links them to their report', true);

    try {
        await addPlayer(state.team.id, {
            name,
            jerseyNumber: numberRaw ? Number(numberRaw) : null,
            email,
        });
        for (const id of ['input-player-name', 'input-player-number', 'input-player-email']) {
            byId(id).value = '';
        }
        state.players = await listPlayers(state.team.id);
        renderRoster();
        toast(`${name} added`);
    } catch (err) {
        toast(err.message || 'Could not add the player.', true);
    }
}

// ---------------------------------------------------------------- staff

function renderStaff() {
    const list = byId('staff-list');
    list.innerHTML = '';

    // Only the coach who created the squad can remove anyone — the rules say so
    // too, so hiding the button just keeps the UI from offering a dead action.
    const isOwner = state.team.createdBy === state.user.uid;

    for (const member of state.staff) {
        const row = document.createElement('div');
        row.className = 'list-item';
        row.innerHTML = `
            <span class="staff-initial"></span>
            <div class="grow">
                <div class="title"></div>
                <div class="sub"></div>
            </div>
            <span class="pill"></span>
            <button class="btn small danger" data-act="remove">Remove</button>`;

        const isMe = member.uid === state.user.uid;
        const name = isMe ? `${member.displayName} (you)` : member.displayName;

        row.querySelector('.staff-initial').textContent =
            (member.displayName || '?').trim()[0]?.toUpperCase() ?? '?';
        row.querySelector('.title').textContent = name;
        row.querySelector('.sub').textContent = member.unknown
            ? "Hasn't opened the dashboard yet"
            : member.emailLower;

        const pill = row.querySelector('.pill');
        const isTeamOwner = member.uid === state.team.createdBy;
        pill.textContent = isTeamOwner ? 'Head coach' : 'Coach';
        if (isTeamOwner) pill.classList.add('done');

        const remove = row.querySelector('[data-act="remove"]');
        if (!isOwner || isMe || isTeamOwner) {
            remove.remove();
        } else {
            remove.addEventListener('click', () => doRemoveCoach(member));
        }

        list.append(row);
    }
}

async function doInviteCoach() {
    const input = byId('input-coach-email');
    const email = input.value.trim().toLowerCase();
    if (!email) return toast('Enter their email address', true);

    const button = byId('btn-invite-coach');
    button.disabled = true;
    try {
        await inviteCoach(state.user, state.team, email);
        input.value = '';
        toast(`Invitation sent to ${email}`);
    } catch (err) {
        toast(
            err?.code === 'permission-denied'
                ? "That address doesn't look like a valid email."
                : err.message || 'Could not send the invitation.',
            true,
        );
    } finally {
        button.disabled = false;
    }
}

async function doRemoveCoach(member) {
    const name = member.displayName || member.emailLower || 'this coach';
    if (!confirm(
        `Remove ${name} from ${state.team.name}?\n\n`
        + 'They lose access to the roster, matches and reports for this squad.'
    )) return;

    try {
        await removeCoach(state.team, member.uid);
        state.team = await getTeam(state.team.id);
        state.teams = state.teams.map((t) => (t.id === state.team.id ? state.team : t));
        state.staff = await listStaff(state.team);
        renderStaff();
        renderHero();
        toast(`${name} removed`);
    } catch (err) {
        toast(err.message || 'Could not remove that coach.', true);
    }
}

// ---------------------------------------------------------------- one player

async function openPlayer(player) {
    byId('loading').classList.remove('hidden');

    try {
        const reports = await playerSeason(state.team.id, state.matches, player.id);
        const totals = seasonTotals(reports);

        setText('pv-number', player.jerseyNumber ?? '—');
        setText('pv-name', player.name);
        setText('pv-sub', player.emailLower || 'no email on file');

        const linked = byId('pv-linked');
        const isLinked = Boolean(player.linkedUid);
        linked.textContent = isLinked ? 'Can see reports' : 'Not joined yet';
        linked.classList.toggle('done', isLinked);

        const stats = byId('pv-stats');
        stats.innerHTML = '';
        stats.append(
            statCard(totals.matches, 'Matches'),
            statCard(totals.minutes, 'Minutes'),
            statCard(totals.goals, 'Goals', totals.goals ? 'is-good' : 'is-muted'),
            statCard(totals.assists, 'Assists', totals.assists ? 'is-good' : 'is-muted'),
            statCard(totals.fouls, 'Fouls', 'is-muted'),
        );

        const cards = totals.yellowCards + totals.redCards;
        stats.append(statCard(cards, 'Cards', cards ? 'is-warn' : 'is-muted'));

        // Per 90 is the number that survives uneven minutes, which is exactly
        // the comparison a coach wants between a starter and a squad player.
        if (totals.minutes >= 45) {
            const per90 = ((totals.goals + totals.assists) / totals.minutes * 90).toFixed(2);
            stats.append(statCard(per90, 'G+A per 90', 'is-muted'));
        }

        renderPlayerChart(reports);
        renderPlayerMatches(reports);
        show('view-player');
        window.scrollTo(0, 0);
    } catch (err) {
        toast(err.message || 'Could not load that player.', true);
        show('view-main');
    }
}

/** One player's minutes across the season, same chart the player sees. */
function renderPlayerChart(reports) {
    const host = byId('pv-chart');
    host.innerHTML = '';

    if (!reports.length) {
        host.innerHTML = '<div class="empty">No published matches yet.</div>';
        setText('pv-chart-note', '');
        return;
    }

    host.append(minutesChart(reports));

    const involved = reports.filter((r) => (r.goals || 0) + (r.assists || 0)).length;
    setText('pv-chart-note', involved
        ? `The line is 90 minutes, oldest match on the left. Highlighted bars are `
          + `matches they scored or assisted in — ${plural(involved, 'match', 'matches')}.`
        : 'The line is 90 minutes, oldest match on the left.');
}

function renderPlayerMatches(reports) {
    const list = byId('pv-matches');
    list.innerHTML = '';

    if (!reports.length) {
        list.innerHTML =
            '<div class="empty">Nothing published for this player yet.<br>'
            + '<span class="muted">Open a match and publish its reports.</span></div>';
        return;
    }

    for (const report of reports) {
        list.append(matchReportRow(report));
    }
}

/** One finished match on a player's season: opponent, date, and their numbers. */
function matchReportRow(report) {
    const row = document.createElement('div');
    row.className = 'list-item pv-row';
    row.innerHTML = `
        <div class="grow">
            <div class="title"></div>
            <div class="sub"></div>
        </div>
        <div class="figures"></div>`;

    row.querySelector('.title').textContent = `vs ${report.opponentName || '—'}`;

    const sub = row.querySelector('.sub');
    sub.textContent = report.matchDate || '';
    for (const chip of cardChips(report.yellowCards, report.redCards, CARD_COLOURS)) {
        chip.style.marginLeft = '6px';
        sub.append(chip);
    }

    row.querySelector('.figures').append(
        figure(report.minutesPlayed ?? 0, 'min'),
        figure(report.goals ?? 0, 'goals'),
        figure(report.assists ?? 0, 'assists'),
    );

    return row;
}

// ---------------------------------------------------------------- matches

function renderMatches() {
    const list = byId('match-list');
    list.innerHTML = '';

    if (!state.matches.length) {
        list.innerHTML =
            '<div class="empty">No matches yet.<br>'
            + '<span class="muted">Add one on the right, then tag it live from the touchline.</span></div>';
        return;
    }

    for (const match of state.matches) {
        list.append(matchRow(match));
    }
}

function matchRow(match) {
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

    const pill = row.querySelector('.pill');

    // A finished match shows its result, colour-coded, instead of just a status
    // word — that is the thing a coach is actually scanning for.
    if (match.finalized) {
        const us = match.scoreUs ?? 0;
        const them = match.scoreThem ?? 0;
        const score = row.querySelector('.match-score');
        score.textContent = `${us}–${them}`;
        score.classList.remove('hidden');
        score.classList.add(us > them ? 'win' : us < them ? 'loss' : 'draw');

        pill.textContent = 'published';
        pill.classList.add('done');
    } else if (['first_half', 'second_half'].includes(match.status)) {
        pill.textContent = 'live';
        pill.classList.add('live');
    } else {
        pill.textContent = (match.status || 'scheduled').replace(/_/g, ' ');
    }

    row.addEventListener('click', () => openMatch(match.id));
    return row;
}

async function doCreateMatch() {
    const opponentName = byId('input-opponent').value.trim();
    const date = byId('input-date').value || new Date().toISOString().slice(0, 10);
    if (!opponentName) return toast('Who are you playing?', true);

    try {
        await createMatch(state.user, state.team.id, { opponentName, date });
        byId('input-opponent').value = '';
        state.matches = await listMatches(state.team.id);
        renderMatches();
        toast('Match created');
    } catch (err) {
        toast(err.message || 'Could not create the match.', true);
    }
}

// ---------------------------------------------------------------- one match

async function openMatch(matchId) {
    byId('loading').classList.remove('hidden');

    try {
        const [match, roster, log] = await Promise.all([
            getMatch(state.team.id, matchId),
            listMatchRoster(state.team.id, matchId),
            listLog(state.team.id, matchId),
        ]);

        const stats = aggregateMatch(log, roster);
        state.match = { ...match, stats, log, roster };

        setText('match-title', `vs ${match.opponentName || '—'}`);
        setText('match-sub',
            `${match.date || 'no date'} · ${(match.status || 'scheduled').replace(/_/g, ' ')}`
            + (match.finalized ? ' · reports published' : ''));

        setText('score-us', stats.counts.us.goal ?? 0);
        setText('score-them', stats.counts.them.goal ?? 0);

        byId('link-halftime').href =
            `../halftime/?team=${encodeURIComponent(state.team.id)}`
            + `&match=${encodeURIComponent(matchId)}`;

        renderTeamStats(stats);
        renderPlayerTable(stats.players);
        renderTimeline(log, roster);

        const publish = byId('btn-publish');
        publish.disabled = false;
        publish.textContent = match.finalized
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
    const grid = byId('team-stats');
    grid.innerHTML = '';

    const { us, them } = stats.counts;

    // Corners and free kicks are counted for whoever was awarded them; fouls,
    // cards and offside are recorded against the offender, so "our fouls" means
    // fouls we committed. Labels say so explicitly.
    const rows = [
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

    for (const [value, label, tone] of rows) grid.append(statCard(value, label, tone));
}

function renderPlayerTable(players) {
    const body = byId('player-table').querySelector('tbody');
    body.innerHTML = '';

    if (!players.length) {
        body.innerHTML =
            '<tr><td colspan="7" class="muted">No lineup was set for this match.</td></tr>';
        return;
    }

    // Most involved first — a coach scanning this wants the standouts on top.
    const ordered = [...players].sort(
        (a, b) => (b.goals + b.assists) - (a.goals + a.assists)
            || b.minutesPlayed - a.minutesPlayed,
    );

    for (const player of ordered) body.append(playerTableRow(player));
}

function playerTableRow(player) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
        <td class="num"></td>
        <td></td>
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
    const chips = cardChips(player.yellowCards, player.redCards, CARD_COLOURS);
    if (chips.length) cardsCell.append(...chips);
    else cardsCell.innerHTML = '<span class="none">—</span>';

    return tr;
}

function renderTimeline(log, roster) {
    const list = byId('timeline');
    list.innerHTML = '';

    if (!log.length) {
        list.innerHTML = '<div class="empty">Nothing was tagged for this match.</div>';
        return;
    }

    const nameById = new Map(roster.map((r) => [r.id, r.playerName]));
    const { usName, themName } = teamLabels();

    for (const entry of log.slice().reverse()) {
        const row = timelineRow({
            clock: clockText(entry.matchClockS),
            text: describeEvent(entry, {
                usName, themName, playerName: nameById.get(entry.playerId),
            }),
            sideLabel: entry.kind === 'period'
                ? ''
                : (entry.side === 'them' ? themName : usName),
            tone: timelineTone(entry),
        });

        if (entry.assistPlayerId && nameById.has(entry.assistPlayerId)) {
            const assist = document.createElement('span');
            assist.className = 'who';
            assist.textContent = ` (assist: ${nameById.get(entry.assistPlayerId)})`;
            row.querySelector('.tl-text').append(assist);
        }

        list.append(row);
    }
}

async function doPublish() {
    const button = byId('btn-publish');
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

const TABS = ['matches', 'roster', 'staff'];

function initTabs() {
    for (const tab of document.querySelectorAll('.tab')) {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
            tab.classList.add('active');
            for (const name of TABS) {
                byId(`tab-${name}`).classList.toggle('hidden', name !== tab.dataset.tab);
            }
        });
    }
}

async function onSignedIn(user) {
    state.user = user;

    const access = await resolveAccess(user);
    // A coach with no team yet resolves as 'none' — there is nothing proving
    // they coach anything until a team exists, so let them through to create one.
    if (access.role === 'player') { location.href = '../player/'; return; }

    state.teams = access.teams;

    const wanted = new URLSearchParams(location.search).get('team');
    state.team = access.teams.find((t) => t.id === wanted) || access.teams[0];

    if (!state.team) { showCreateTeam(); return; }

    await loadTeamData();
    show('view-main');
}

function init() {
    const warning = configWarning();
    if (warning) byId('config-slot').append(warning);

    initTabs();

    byId('btn-signout').addEventListener('click', () =>
        signOut().then(() => { location.href = '../'; }));
    byId('btn-create-team').addEventListener('click', doCreateTeam);
    byId('btn-new-team').addEventListener('click', showCreateTeam);
    byId('btn-cancel-team').addEventListener('click', () => show('view-main'));
    byId('team-switch').addEventListener('change', (e) => switchTeam(e.target.value));
    byId('btn-invite-coach').addEventListener('click', doInviteCoach);
    byId('btn-add-player').addEventListener('click', doAddPlayer);
    byId('btn-create-match').addEventListener('click', doCreateMatch);
    byId('btn-back').addEventListener('click', () => show('view-main'));
    byId('btn-back-roster').addEventListener('click', () => show('view-main'));
    byId('btn-publish').addEventListener('click', doPublish);
    byId('input-date').value = new Date().toISOString().slice(0, 10);

    onUser((user) => {
        if (!user) { location.href = '../'; return; }
        onSignedIn(user).catch((err) => {
            toast(err.message || 'Could not load your team.', true);
            byId('loading').classList.add('hidden');
        });
    });
}

init();
