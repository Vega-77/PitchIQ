import {
    onUser, signOut, resolveAccess, rememberTeam, saveStaffProfile, configWarning,
} from '../assets/auth.js?v=17';
import {
    createTeam, getTeam, listPlayers, addPlayer, removePlayer, invitePlayer,
    listMatches, getMatch, createMatch, updateMatch, listMatchRoster, listLog,
    aggregateMatch, publishReports, seasonSummary, playerSeason, seasonTotals,
    listStaff, inviteCoach, removeCoach, readCvStats, cvConfidence, mappingConfirmed,
} from '../assets/db.js?v=17';
import { CARD_COLOURS, describeEvent, timelineTone } from '../assets/events.js?v=17';
import { mountPitchBackdrop } from '../assets/pitch-backdrop.js?v=17';
import { videoKind } from '../assets/video.js?v=17';
import {
    byId, setText, toast, showOnly, clockText, signed, plural,
    statCard, figure, cardChips, timelineRow, minutesChart,
} from '../assets/ui.js?v=17';

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

        stats.append(...cvSeasonCards(totals));

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
        const [match, roster, log, cv] = await Promise.all([
            getMatch(state.team.id, matchId),
            listMatchRoster(state.team.id, matchId),
            listLog(state.team.id, matchId),
            // Absent for any match nobody filmed, which is most of them, so a
            // failure here must not take the tagged report down with it.
            readCvStats(state.team.id, matchId).catch(() => null),
        ]);

        const stats = aggregateMatch(log, roster);
        state.match = { ...match, stats, log, roster, cv };

        // Merge the video's per-player figures onto the tagged ones, so the
        // table can be one table. Only where a coach has confirmed which
        // cluster is which player — otherwise these are guesses with names on.
        if (cv && mappingConfirmed(cv)) mergeCvPlayers(stats.players, cv);

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

        // Rebuilt rather than appended: openMatch runs again every time a coach
        // goes back and picks another match, and a stacking banner is the
        // classic way that goes unnoticed.
        document.querySelector('.cv-note')?.remove();
        const note = cvNote();
        if (note) byId('team-stats').before(note);

        renderPlayerTable(stats.players);
        renderTimeline(log, roster);
        renderClusterMapping();

        byId('input-video-url').value = match.videoUrl || '';
        byId('input-video-offset').value = match.videoOffsetS ?? 0;

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

    // Video-derived figures join the same grid, each marked. Same grid because
    // a coach wants one picture of the match; marked because "17 tackles" read
    // off footage and "2 goals" somebody tapped are different kinds of claim.
    for (const [value, label, confidence] of cvTeamRows()) {
        grid.append(statCard(value, label, 'is-muted', confidence));
    }
}

/** Team figures the pipeline derived, as [value, label, confidence] rows. */
function cvTeamRows() {
    const cv = state.match?.cv;
    const ours = cv?.teams?.team_a;
    if (!ours) return [];

    const quality = cv.quality || {};
    const events = cvConfidence(quality, 'events');
    const possession = cvConfidence(quality, 'possession');

    const rows = [
        [ours.possession_pct == null ? null : `${Math.round(ours.possession_pct * 100)}%`,
            'Possession', possession],
        [ours.passes_attempted, 'Passes attempted', events],
        [ours.pass_accuracy == null ? null : `${Math.round(ours.pass_accuracy * 100)}%`,
            'Pass accuracy', events],
        [ours.progressive_passes, 'Progressive passes', events],
        [ours.final_third_entries, 'Final-third entries', events],
        [ours.box_entries, 'Entries into the box', events],
        [ours.crosses, 'Crosses', events],
        [ours.switches, 'Switches of play', events],
        [ours.shots, 'Shots', events],
        [ours.shots_on_target, 'Shots on target', events],
        [ours.xg == null ? null : ours.xg.toFixed(2), 'Expected goals', events],
        [ours.tackles, 'Tackles', events],
        [ours.interceptions, 'Interceptions', events],
        [ours.recoveries, 'Recoveries', events],
        [ours.duels, 'Ground duels', events],
        [ours.ppda == null ? null : ours.ppda.toFixed(1), 'PPDA', events],
    ];

    // A null is "not measured", usually for want of a calibration. Printing a
    // zero instead would claim the pipeline looked and found none.
    return rows.filter(([value]) => value != null);
}

/** The banner over the estimated section, naming what limited it. */
function cvNote() {
    const cv = state.match?.cv;
    if (!cv) return null;

    const quality = cv.quality || {};
    const note = document.createElement('div');
    note.className = 'cv-note';

    // Seen, not "has a position for" — the rest were drawn in between
    // sightings, and saying a straight line was "visible" would overstate what
    // the video actually showed.
    const coverage = quality.ball_seen_share;
    const bits = [];
    if (coverage != null) bits.push(`the ball was visible in ${Math.round(coverage * 100)}% of frames`);
    if (!cv.calibrated) bits.push('no pitch calibration, so nothing is in metres');
    if (quality.tracks_per_cluster > 2) {
        bits.push(`tracking broke each player into about ${Math.round(quality.tracks_per_cluster)} pieces`);
    }

    note.innerHTML = '<span></span>';
    note.querySelector('span').textContent = bits.length
        ? `Measured from video: ${bits.join('; ')}. Treat these as estimates.`
        : 'Measured from video. Treat these as estimates.';

    const strong = document.createElement('strong');
    strong.textContent = 'Estimated ';
    note.prepend(strong);
    return note;
}

function renderPlayerTable(players) {
    const body = byId('player-table').querySelector('tbody');
    body.innerHTML = '';

    const columns = byId('player-table').querySelectorAll('thead th').length;
    if (!players.length) {
        body.innerHTML =
            `<tr><td colspan="${columns}" class="muted">No lineup was set for this match.</td></tr>`;
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

    // Tagged columns first, then the video ones, so the trustworthy numbers are
    // the ones a coach reads without scrolling sideways on a phone.
    const tagged = [player.minutesPlayed, player.goals, player.assists, player.fouls];
    const derived = [
        player.cvTouches, player.cvPassesCompleted, player.cvTackles,
        player.cvDistanceM == null ? null : (player.cvDistanceM / 1000).toFixed(2),
    ];

    tr.innerHTML = `
        <td class="num"></td>
        <td></td>
        ${'<td class="num"></td>'.repeat(tagged.length)}
        <td><span class="cards-cell"></span></td>
        ${'<td class="num cv"></td>'.repeat(derived.length)}`;

    const cells = tr.querySelectorAll('td');
    cells[0].textContent = player.jerseyNumber ?? '—';
    cells[1].textContent = player.playerName;

    tagged.forEach((value, i) => {
        const cell = cells[i + 2];
        cell.textContent = value;
        // Zeroes recede so the meaningful numbers carry the eye.
        if (!value) cell.classList.add('zero');
    });

    const cardsCell = tr.querySelector('.cards-cell');
    const chips = cardChips(player.yellowCards, player.redCards, CARD_COLOURS);
    if (chips.length) cardsCell.append(...chips);
    else cardsCell.innerHTML = '<span class="none">—</span>';

    const cvCells = tr.querySelectorAll('td.cv');
    cvCells[0]?.classList.add('cv-first');
    derived.forEach((value, i) => {
        const cell = cvCells[i];
        // An em dash, not a zero: this player's video stats do not exist until
        // a coach has confirmed which tracked figure on the pitch was them.
        cell.textContent = value == null ? '—' : value;
        if (value == null || !Number(value)) cell.classList.add('zero');
    });

    return tr;
}

/**
 * Season cards from video, shown only for the matches that were actually
 * filmed.
 *
 * `cvMatches` rather than `matches` is the divisor throughout: averaging
 * touches over a whole season would divide by matches nobody pointed a camera
 * at, and quietly halve every figure.
 */
function cvSeasonCards(totals) {
    if (!totals.cvMatches) return [];

    const cards = [
        statCard(totals.cvMatches, 'Matches filmed', 'is-muted'),
        statCard(totals.cvTouches, 'Touches', 'is-muted', 'medium'),
        statCard(totals.cvTackles, 'Tackles', 'is-muted', 'medium'),
        statCard(totals.cvInterceptions, 'Interceptions', 'is-muted', 'medium'),
    ];

    if (totals.cvPassesAttempted) {
        const accuracy = Math.round(
            (totals.cvPassesCompleted / totals.cvPassesAttempted) * 100,
        );
        cards.push(statCard(`${accuracy}%`, 'Pass accuracy', 'is-muted', 'medium'));
    }
    if (totals.cvDistanceM) {
        cards.push(statCard(
            (totals.cvDistanceM / 1000).toFixed(1), 'km covered', 'is-muted', 'medium',
        ));
    }
    if (totals.cvTopSpeedKmh) {
        cards.push(statCard(
            totals.cvTopSpeedKmh.toFixed(1), 'Top speed km/h', 'is-muted', 'medium',
        ));
    }

    return cards;
}

// ---------------------------------------------------------------- who is who

/**
 * Put names to the figures the video tracked.
 *
 * The tracker cannot tell one player from another — it loses people behind
 * opponents and when the camera pans, and gives each reappearance a new
 * identity. cv/identity.py stitches those fragments into clusters, but which
 * cluster is which teenager is not something footage at this distance can
 * answer: shirt numbers are a few pixels tall.
 *
 * So this is the step that turns tracked motion into a player's stats, and
 * nothing per-player is written until it is done. Read-only here: confirming a
 * mapping writes to cvStats, which every client is denied — the coach's choices
 * go back through cv/publish.py. What this view does is make the mapping
 * possible to work out.
 */
function renderClusterMapping() {
    const host = byId('cv-clusters');
    if (!host) return;

    const cv = state.match?.cv;
    const clusters = cv?.identity?.clusters || [];
    const section = byId('cv-clusters-block');

    if (!clusters.length) {
        if (section) section.classList.add('hidden');
        return;
    }
    if (section) section.classList.remove('hidden');

    const mapping = cv.identity?.playerByCluster || {};
    const nameById = new Map(state.match.roster.map((r) => [r.id, r.playerName]));

    host.innerHTML = '';
    setText('cv-clusters-note', mappingConfirmed(cv)
        ? `${Object.keys(mapping).length} of ${clusters.length} tracked figures `
          + 'have been matched to a player.'
        : `The video tracked ${clusters.length} figures but cannot tell who is `
          + 'who. Until each is matched to a player, the per-player columns stay '
          + 'empty — a wrong match would credit one player with another\'s work.');

    for (const cluster of clusters) {
        const row = document.createElement('div');
        row.className = 'list-item cluster-row';
        row.innerHTML = `
            <span class="cluster-swatch"></span>
            <div class="grow">
                <div class="title"></div>
                <div class="sub"></div>
            </div>
            <div class="figures"></div>`;

        const swatch = row.querySelector('.cluster-swatch');
        if (cluster.colour) swatch.style.background = labToCss(cluster.colour);
        else swatch.classList.add('unknown');

        const named = mapping[String(cluster.cluster_id)];
        row.querySelector('.title').textContent = named
            ? (nameById.get(named) || 'Matched player')
            : `Figure ${cluster.cluster_id + 1}`;

        row.querySelector('.sub').textContent = [
            cluster.team === 'unknown' ? 'kit unclear' : cluster.team.replace('_', ' '),
            plural(cluster.track_ids?.length || 1, 'fragment'),
            `${Math.round((cluster.minutes_tracked || 0) * 60)}s on screen`,
        ].join(' · ');

        row.querySelector('.figures').append(
            figure(cluster.sightings ?? 0, 'frames'),
        );

        if (!named) row.classList.add('unmatched');
        host.append(row);
    }
}

/**
 * A Lab colour from the pipeline to something a browser can paint.
 *
 * Approximate on purpose: this is a swatch to help a coach recognise a kit at a
 * glance, not a colour-managed reproduction. Lightness is fixed at mid-grey
 * because the clustering drops it — two sightings of one shirt in sun and shade
 * differ in lightness far more than two different kits differ in chroma.
 */
function labToCss(lab) {
    const [, a = 0, b = 0] = lab;
    const y = 0.6;
    const r = Math.max(0, Math.min(255, Math.round(255 * (y + a / 128 * 0.5))));
    const g = Math.max(0, Math.min(255, Math.round(255 * (y - a / 256 - b / 256))));
    const bl = Math.max(0, Math.min(255, Math.round(255 * (y - b / 128 * 0.5))));
    return `rgb(${r}, ${g}, ${bl})`;
}

function mergeCvPlayers(players, cv) {
    const mapping = cv.identity?.playerByCluster || {};
    const byCluster = new Map(
        (cv.identity?.tracks || []).map((t) => [String(t.cluster_id), t]),
    );

    const byPlayer = new Map();
    for (const [clusterId, playerId] of Object.entries(mapping)) {
        const track = byCluster.get(String(clusterId));
        if (track) byPlayer.set(playerId, track);
    }

    for (const player of players) {
        const track = byPlayer.get(player.id);
        if (!track) continue;
        player.cvTouches = track.touches;
        player.cvPassesCompleted = track.passes_completed;
        player.cvPassesAttempted = track.passes_attempted;
        player.cvTackles = track.tackles;
        player.cvInterceptions = track.interceptions;
        player.cvDistanceM = track.distance_m;
        player.cvTopSpeedKmh = track.top_speed_kmh;
    }
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

/**
 * Attach footage to a match, so players can jump to their own moments.
 *
 * The offset is the fiddly part and the reason it gets its own field rather
 * than being assumed zero: the match clock starts at kick-off and a recording
 * almost never does. Without it every marker lands during the warm-up, which
 * reads as the whole feature being broken rather than as one number being
 * unset.
 */
async function doSaveVideo() {
    const button = byId('btn-save-video');
    const url = byId('input-video-url').value.trim();
    const offset = Number(byId('input-video-offset').value) || 0;

    if (url && !videoKind(url)) {
        // Saved anyway — a Drive or Hudl link is still worth giving a player,
        // it just cannot be embedded and seeked. Say so rather than refusing.
        toast('Saved, but that link cannot be played inside PitchIQ.');
    }

    button.disabled = true;
    try {
        await updateMatch(state.team.id, state.match.id, {
            videoUrl: url || null,
            videoOffsetS: offset,
        });
        state.match.videoUrl = url || null;
        state.match.videoOffsetS = offset;
        if (!url || videoKind(url)) toast('Video link saved');
    } catch (err) {
        toast(err.message || 'Could not save the video link.', true);
    } finally {
        button.disabled = false;
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
            // The log and roster go in so each report can carry that player's
            // own timeline. They are read here and never reach the player —
            // publishReports turns them into labels first.
            {
                log: state.match.log,
                roster: state.match.roster,
                counts: state.match.stats.counts,
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
    byId('btn-save-video').addEventListener('click', doSaveVideo);
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
