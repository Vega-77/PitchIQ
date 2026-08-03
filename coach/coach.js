import {
    onUser, signOut, resolveAccess, rememberTeam, saveStaffProfile, configWarning,
} from '../assets/auth.js?v=21';
import {
    createTeam, getTeam, listPlayers, addPlayer, removePlayer, invitePlayer,
    listMatches, getMatch, createMatch, updateMatch, listMatchRoster, listLog,
    aggregateMatch, publishReports, seasonSummary, playerSeason, seasonTotals,
    listStaff, inviteCoach, removeCoach, readCvStats, cvConfidence,
    readCvMapping, saveCvMapping, cvStatsByPlayer, cvReportFields,
    readCvEvents, readCvReview, saveCvReview, pushVideoToReports,
} from '../assets/db.js?v=21';
import { renderStrip, timelineEnd } from '../assets/timeline.js?v=21';
import { renderMatchVideo, teamMarks } from '../assets/match-video.js?v=21';
import {
    NOT_A_PLAYER, rankRosterForCluster, possessionIsInPlay, cvQualityNotes,
    roughDuration, shapeConfidence, reviewScore, reviewLabels,
} from '../assets/report.js?v=21';
import { CARD_COLOURS, describeEvent, timelineTone } from '../assets/events.js?v=21';
import { mountPitchBackdrop } from '../assets/pitch-backdrop.js?v=21';
import { mount as mountVideo, videoKind, videoTime } from '../assets/video.js?v=21';
import {
    byId, setText, toast, showOnly, clockText, signed, plural,
    statCard, figure, cardChips, timelineRow, minutesChart, confidenceMark,
} from '../assets/ui.js?v=21';

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
        const [match, roster, log, cv, cvMapping, cvEvents, cvReview] =
            await Promise.all([
                getMatch(state.team.id, matchId),
                listMatchRoster(state.team.id, matchId),
                listLog(state.team.id, matchId),
                // Absent for any match nobody filmed, which is most of them, so
                // a failure here must not take the tagged report down with it.
                readCvStats(state.team.id, matchId).catch(() => null),
                readCvMapping(state.team.id, matchId).catch(() => ({})),
                readCvEvents(state.team.id, matchId).catch(() => null),
                readCvReview(state.team.id, matchId)
                    .catch(() => ({ byEvent: {}, missed: [] })),
            ]);

        const stats = aggregateMatch(log, roster);
        state.match = {
            ...match, stats, log, roster, cv, cvMapping, cvEvents, cvReview,
        };

        // Merge the video's per-player figures onto the tagged ones, so the
        // table can be one table. Only for figures a coach has matched to a
        // player — anything else is a guess with a name attached.
        mergeCvPlayers(stats.players, state.match);

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
        renderMatchVideoBlock();
        renderTimeline(log, roster);
        renderClusterMapping();
        renderExcluded();
        renderReview();

        byId('input-video-url').value = match.videoUrl || '';
        byId('input-video-offset').value = match.videoOffsetS ?? 0;
        updateVideoHint();

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
        // The label carries the denominator, because the denominator changed.
        // With a tagged log the dead time is out of it and this is possession
        // of a ball that was in play; without one it is the older, weaker
        // figure and must not claim otherwise.
        [ours.possession_pct == null ? null : `${Math.round(ours.possession_pct * 100)}%`,
            possessionIsInPlay(quality) ? 'Possession, ball in play' : 'Possession',
            possession],
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
        ...shapeRows(ours.shape, cv.calibrationErrorM),
    ];

    // A null is "not measured", usually for want of a calibration. Printing a
    // zero instead would claim the pipeline looked and found none.
    return rows.filter(([value]) => value != null);
}

/**
 * How spread out we played, in metres — ours only.
 *
 * `report_json` used to publish one shape built from every track on the pitch,
 * both teams and the referee together, and label it Team A's. It is now built
 * per team, and this reads the team's own. Reading `cv.teams.team_a.shape`
 * rather than a top-level field is what makes that hold: there is no longer a
 * single number that could be handed to the wrong side.
 *
 * Empty until a calibration exists, which is every run today — width in metres
 * is not something a pixel can answer. The null filter below drops all three
 * rows in that case rather than printing three zeroes.
 */
function shapeRows(shape, calibrationErrorM) {
    if (!shape || shape.width_m == null) return [];
    const band = shapeConfidence(calibrationErrorM);
    const metres = (value) => (value == null ? null : `${Math.round(value)}m`);

    return [
        [metres(shape.width_m), 'Average width', band],
        [metres(shape.depth_m), 'Average depth', band],
        // Mean distance from each player to the team's own centre. Deliberately
        // not coloured good or bad: a compact side is well-drilled or it is
        // pinned in its own half, and this number cannot tell the difference.
        [metres(shape.compactness_m), 'Compactness', band],
    ];
}

/** The banner over the estimated section, naming what limited it. */
function cvNote() {
    const cv = state.match?.cv;
    if (!cv) return null;

    const quality = cv.quality || {};
    const note = document.createElement('div');
    note.className = 'cv-note';

    const bits = cvQualityNotes(quality, {
        calibrated: cv.calibrated,
        // So the xG caveat appears only once there is an xG row to caveat.
        shots: cv.teams?.team_a?.shots,
        calibrationErrorM: cv.calibrationErrorM,
        reconciliation: cv.reconciliation,
    });

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

    const mapping = state.match.cvMapping || {};
    // Biggest first — a figure tracked through most of the match is both the
    // easiest to recognise and the one worth the coach's attention. The tail
    // of two-second fragments can be left unassigned without losing much.
    const ordered = [...clusters].sort((a, b) => (b.sightings || 0) - (a.sightings || 0));

    host.innerHTML = '';
    updateMappingNote();

    for (const cluster of ordered) {
        host.append(clusterRow(cluster, mapping));
    }
}

function clusterRow(cluster, mapping) {
    const row = document.createElement('div');
    row.className = 'list-item cluster-row';
    row.innerHTML = `
        <span class="cluster-swatch"></span>
        <div class="grow">
            <div class="title"></div>
            <div class="sub"></div>
        </div>
        <div class="figures"></div>
        <label class="cluster-pick">
            <span class="sr-only">Who is this?</span>
            <select></select>
        </label>`;

    const swatch = row.querySelector('.cluster-swatch');
    if (cluster.colour) swatch.style.background = labToCss(cluster.colour);
    else swatch.classList.add('unknown');

    const offset = state.match.videoOffsetS ?? 0;
    const fromS = (cluster.first_seen_s ?? 0) - offset;
    const toS = (cluster.last_seen_s ?? 0) - offset;

    row.querySelector('.title').textContent = `Figure ${cluster.cluster_id + 1}`;
    row.querySelector('.sub').textContent = [
        cluster.team === 'unknown' ? 'kit unclear' : cluster.team.replace('_', ' '),
        plural(cluster.track_ids?.length || 1, 'fragment'),
        // On the match clock, not the video's, so it can be read against the
        // substitutions below it.
        `${clockText(Math.max(0, fromS))}–${clockText(Math.max(0, toS))}`,
    ].join(' · ');

    row.querySelector('.figures').append(figure(cluster.sightings ?? 0, 'frames'));

    const select = row.querySelector('select');
    const key = String(cluster.cluster_id);

    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = 'Not matched';
    select.append(blank);

    const notPlayer = document.createElement('option');
    notPlayer.value = NOT_A_PLAYER;
    notPlayer.textContent = 'Not a player (referee, bench)';
    select.append(notPlayer);

    // Grouped by who was on the pitch while this figure was on screen, and
    // never filtered by it. The offset relating the two clocks is the fiddliest
    // number in the app, and if it is wrong then so is every overlap here —
    // hiding the players who do not fit would hide the right answer.
    //
    // Every rostered player stays pickable however many times they have been
    // used. A player who left the frame and came back is genuinely several
    // figures; cv/identity.py only rejoins fragments seconds apart.
    const ranked = rankRosterForCluster(state.match.roster, cluster, {
        videoOffsetS: offset,
        matchEndS: state.match.stats?.matchEndS ?? 0,
    });

    const onPitch = document.createElement('optgroup');
    onPitch.label = 'On the pitch then';
    const others = document.createElement('optgroup');
    others.label = 'Everyone else';

    for (const { entry, overlapS, overlapShare } of ranked) {
        const option = document.createElement('option');
        option.value = entry.id;
        const name = entry.jerseyNumber != null
            ? `${entry.jerseyNumber} · ${entry.playerName}`
            : entry.playerName;
        option.textContent = overlapS > 0
            ? `${name} — on for ${Math.round(overlapShare * 100)}% of it`
            : name;
        (overlapS > 0 ? onPitch : others).append(option);
    }

    if (onPitch.children.length) select.append(onPitch);
    if (others.children.length) select.append(others);

    select.value = mapping[key] || '';

    select.addEventListener('change', () => {
        const next = { ...(state.match.cvMapping || {}) };
        if (select.value) next[key] = select.value;
        else delete next[key];
        state.match.cvMapping = next;

        row.classList.toggle('unmatched', !select.value);
        updateMappingNote();
        queueMappingSave();
    });

    row.classList.toggle('unmatched', !select.value);
    return row;
}

function updateMappingNote() {
    const clusters = state.match?.cv?.identity?.clusters || [];
    const answers = Object.values(state.match?.cvMapping || {});
    // Ruled out is an answer, but it is not a player, and counting it as one
    // would tell a coach their stats are further along than they are.
    const matched = answers.filter((id) => id && id !== NOT_A_PLAYER).length;
    const ruledOut = answers.filter((id) => id === NOT_A_PLAYER).length;
    const tail = ruledOut ? ` ${plural(ruledOut, 'figure')} ruled out.` : '';

    setText('cv-clusters-note', matched
        ? `${matched} of ${clusters.length} tracked figures matched.${tail} `
          + 'Publish the reports again to send these numbers to the players.'
        : `The video tracked ${clusters.length} figures and cannot tell who is `
          + 'who — shirt numbers are a few pixels tall at this distance. Match '
          + 'the big ones to a player and their stats appear. A figure left '
          + 'unmatched is simply not counted.');
}

/**
 * Save shortly after the coach stops changing things.
 *
 * Mapping fifteen figures is fifteen changes in quick succession, and writing
 * on each one would be fifteen round trips racing each other to be last.
 */
let mappingSaveTimer = null;
function queueMappingSave() {
    clearTimeout(mappingSaveTimer);
    mappingSaveTimer = setTimeout(saveMappingNow, 600);
}

async function saveMappingNow() {
    const badge = byId('cv-save-state');
    try {
        if (badge) badge.textContent = 'Saving…';
        await saveCvMapping(
            state.user, state.team.id, state.match.id, state.match.cvMapping,
        );
        if (badge) badge.textContent = 'Saved';
        // The per-player table reads the mapping, so it has to be rebuilt.
        mergeCvPlayers(state.match.stats.players, state.match);
        renderPlayerTable(state.match.stats.players);
    } catch (err) {
        if (badge) badge.textContent = '';
        toast(err.message || 'Could not save that match-up.', true);
    }
}

// ------------------------------------------------------------- the match video
//
// The same strip the player portal puts a teenager's own touches on, with the
// whole team's moments instead. Goals, cards and substitutions only — see
// teamMarks() for why the restarts and the fouls are left off.
//
// Hidden without a playable link, unlike the portal's version, which shows the
// moment list regardless. Here the moments are already listed in full further
// down the page, so a video block with no video would be the same information
// twice with the interesting half missing.

let matchVideo = null;

function renderMatchVideoBlock() {
    const block = byId('match-video-block');
    const url = state.match?.videoUrl;

    matchVideo?.destroy?.();
    matchVideo = null;

    if (!url) {
        block.classList.add('hidden');
        return;
    }
    block.classList.remove('hidden');

    const { usName, themName } = teamLabels();
    const nameById = new Map((state.match.roster || []).map((r) => [r.id, r.playerName]));
    const marks = teamMarks(state.match.log || [], (entry) => describeEvent(entry, {
        usName, themName, playerName: nameById.get(entry.playerId),
    }));

    matchVideo = renderMatchVideo(
        {
            video: byId('match-video'),
            strip: byId('match-scrubber'),
            list: byId('match-moments'),
            note: byId('match-video-note'),
        },
        {
            url,
            offsetS: state.match.videoOffsetS ?? 0,
            marks,
            clockText,
            // So a mark tagged in stoppage time does not fall off the end of a
            // bar drawn to ninety minutes.
            extraTimes: (state.match.log || []).map((e) => e.matchClockS || 0),
            emptyText: 'No goals, cards or substitutions were tagged.',
            notes: {
                embed: `${plural(marks.length, 'moment')} marked. Tap one to jump `
                    + 'straight to it. Only goals, cards and substitutions are '
                    + 'marked — the restarts would bury them.',
                link: 'That link cannot be played inside PitchIQ, so the times '
                    + 'below are match-clock readings rather than buttons.',
                none: '',
            },
        },
    );
}

/**
 * Say what we made of the link, as it is typed.
 *
 * The rule this exists for is invisible otherwise: a Google Drive share link is
 * a perfectly good link to footage and PitchIQ will not embed it, because the
 * page behind it is a Drive page rather than a video and putting an arbitrary
 * page in an iframe is not worth the convenience. A coach who pastes one and
 * gets no feedback until they open a player's report has been let down by the
 * form, not by the rule.
 */
function updateVideoHint() {
    const hint = byId('video-kind-hint');
    const url = byId('input-video-url').value.trim();
    const kind = url ? videoKind(url) : null;

    hint.classList.remove('is-good', 'is-warn');
    if (!url) {
        hint.textContent = '';
        return;
    }

    if (kind === 'youtube') {
        hint.classList.add('is-good');
        hint.textContent = 'YouTube — plays here, and every moment becomes a button.';
    } else if (kind === 'file') {
        hint.classList.add('is-good');
        hint.textContent = 'A video file — plays here, and every moment becomes a button.';
    } else {
        hint.classList.add('is-warn');
        hint.textContent = 'This will be saved as a link players can open, but it '
            + 'will not play inside PitchIQ, so nothing will be tappable. A '
            + 'YouTube link, or a direct link to an .mp4, does both. A Google '
            + 'Drive or Hudl share page cannot be embedded.';
    }
}

/** Tear the match video down. Called when the match view is left. */
function leaveMatchVideo() {
    matchVideo?.destroy?.();
    matchVideo = null;
}

// --------------------------------------------------- who was left out, and why
//
// The detector finds people, not players. A referee, a substitute warming up
// and somebody's dad on a folding chair all arrive as tracks, and
// cv/participants.py drops or flags them before any number above is counted.
//
// That correction is invisible in the stats it corrects — a possession figure
// looks the same whether nine spectators were removed from it or none were. So
// it is shown here in full, with the sentence that produced each decision,
// because every threshold in that module is a guess that has never met a real
// touchline. A coach who can read "never moved more than 0.4 of a body length
// in 41 minutes" can tell a stationary parent from a wrongly-dropped keeper.
// A coach shown only "9 excluded" cannot.

const ROLE_TEXT = {
    offfield: ['Left out', 'Nothing above counts this figure.'],
    official: ['Kept, but unsure', 'Counted in everything above.'],
};

function renderExcluded() {
    const section = byId('cv-excluded-block');
    if (!section) return;

    const cv = state.match?.cv;
    const notes = cv?.participants || [];
    const quality = cv?.quality || {};
    // Both spellings, for the same reason cvConfidence takes both: the quality
    // block arrives snake_cased from Python, and the emulator fixtures write it
    // camelCased.
    const excluded = quality.excluded_tracks ?? quality.excludedTracks ?? 0;
    const officials = quality.flagged_officials ?? quality.flaggedOfficials ?? 0;

    // Hidden when there is nothing to report, and *also* when the run predates
    // this field — an empty list under a heading saying figures were left out
    // reads as a claim that none were, which is not something an older report
    // can support.
    if (!notes.length && !excluded && !officials) {
        section.classList.add('hidden');
        return;
    }
    section.classList.remove('hidden');

    setText('cv-excluded-note', excludedNote(excluded, officials, notes.length));

    const host = byId('cv-excluded');
    host.innerHTML = '';
    for (const note of notes) host.append(excludedRow(note));
}

function excludedNote(excluded, officials, shown) {
    const parts = [];
    if (excluded) {
        parts.push(`${plural(excluded, 'tracked figure')} left out of every `
            + 'number above — the pipeline judged them not to be playing.');
    }
    if (officials) {
        // The one that needs saying out loud. Without a calibration there is no
        // goalmouth to measure anyone against, so a referee and a goalkeeper
        // look identical on every feature available, and the pipeline keeps
        // both rather than risk deleting a player. That means a referee may be
        // inside the counts above.
        parts.push(`${plural(officials, 'figure')} moving like a player but `
            + 'matching neither kit — a referee, or your goalkeeper. They are '
            + 'still counted, because dropping a keeper is the worse mistake.');
    }
    if (!parts.length) parts.push('Nothing was left out of this run.');
    if (shown && shown < excluded + officials) {
        parts.push(`Showing the ${shown} that were on screen longest.`);
    }
    return parts.join(' ');
}

function excludedRow(note) {
    const [badge, effect] = ROLE_TEXT[note.role] || ['Left out', ''];

    const row = document.createElement('div');
    row.className = `list-item excluded-row is-${note.role}`;
    row.innerHTML = `
        <span class="excluded-badge"></span>
        <div class="grow">
            <div class="title"></div>
            <div class="sub"></div>
        </div>`;

    row.querySelector('.excluded-badge').textContent = badge;
    // "Track", never "Figure". The numbered figures above are *clusters*, and
    // an excluded track never became one — it is dropped before clustering
    // runs. Sharing the word would invite a coach to match "Figure 3" here to
    // "Figure 3" there, and they are two different numbering schemes that
    // happen to start at the same place.
    row.querySelector('.title').textContent = `Track ${note.trackId}`;
    // The pipeline's own sentence, unedited. Rewording it here would put a
    // second author between the threshold and the person judging it.
    row.querySelector('.sub').textContent = [
        note.reason,
        note.screenTimeS ? `on screen for ${roughDuration(note.screenTimeS)}` : '',
        effect,
    ].filter(Boolean).join(' · ');

    return row;
}

// ------------------------------------------------------- checking the video
//
// The pipeline produces candidates and this is where a human says whether they
// are real. Two halves, and both are needed:
//
//   - judging what it found gives precision. Of the 84 passes it claims, how
//     many happened?
//   - recording what it missed gives recall. Of the passes that happened, how
//     many did it find? Nothing in the pipeline's own output can answer that,
//     because a thing it never saw leaves no trace to disagree with.
//
// Recall is the number that decides whether the ball detector is good enough,
// so the "record a miss" half is not an extra.

const REVIEW_TYPES = [
    'pass', 'carry', 'shot', 'tackle', 'interception', 'recovery',
    'clearance', 'duel',
];

const CONFIRMED = 'confirmed';
const REJECTED = 'rejected';
const EDITED = 'edited';

const reviewState = {
    filter: 'all', unreviewedOnly: false, inPlayOnly: false, video: null,
};

/**
 * How many candidates fell inside a stoppage the tagged log knows about.
 *
 * Dead-ball events are stamped, never dropped — a throw-in is a real pass and a
 * coach counts it. But they are the events most likely to be junk: the ball is
 * stationary, players are walking, and the touch detector has nothing moving to
 * key on. So they are worth being able to set aside while reviewing, and worth
 * being able to look at on their own.
 *
 * Zero when no tagged log reached the run, because `inPlay` then defaults to
 * true on every event — which is an absence of information, not a match with no
 * stoppages in it. The filter hides itself in that case rather than offering a
 * control that cannot do anything.
 */
function deadBallCount() {
    const events = state.match?.cvEvents?.events || [];
    return events.filter((e) => e.inPlay === false).length;
}

function renderReview() {
    const block = byId('cv-review-block');
    const events = state.match?.cvEvents?.events || [];

    // Absent for every match published before this tool existed. Hidden rather
    // than shown empty: an empty list reads as "the video found nothing".
    if (!events.length) {
        block.classList.add('hidden');
        return;
    }
    block.classList.remove('hidden');

    renderReviewVideo();
    renderConflicts();
    renderReviewFilters();
    renderReviewList();
    updateReviewProgress();
}

/**
 * The moments the tagged log and the video analysis contradict each other.
 *
 * Goals only, and above everything else in this block. Two independent records
 * of the same match disagreeing about a goal is the strongest signal either of
 * them produces — far stronger than a low-confidence pass the pipeline is
 * merely unsure about — and it takes a reviewer twenty seconds to settle.
 *
 * Hidden when the two agree, and hidden when there was no tagged log to compare
 * against. Those are different facts, but neither of them is something to put
 * on screen: the first is silence because nothing is wrong, and the second is
 * already said in the quality note above.
 */
function renderConflicts() {
    const host = byId('cv-conflicts');
    const entries = state.match?.cv?.reconciliation?.disagreements || [];
    host.innerHTML = '';
    host.classList.toggle('hidden', !entries.length);
    if (!entries.length) return;

    const heading = document.createElement('p');
    heading.className = 'conflicts-head';
    heading.textContent = plural(entries.length, 'goal')
        + ' the tagged log and the video disagree about';
    host.append(heading);

    for (const entry of entries) {
        const seconds = entry.status === 'tag_only' ? entry.tag_s : entry.cv_s;
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'conflict-row';

        const when = document.createElement('span');
        when.className = 'conflict-clock';
        when.textContent = clockText(toMatchClock(seconds));

        const what = document.createElement('span');
        what.textContent = entry.status === 'tag_only'
            ? 'tagged as a goal, but the video found no shot going in'
            : 'the video has a goal here that nobody tagged';

        row.append(when, what);
        row.addEventListener('click', () => seekReview(toMatchClock(seconds)));
        host.append(row);
    }
}

function renderReviewVideo() {
    const host = byId('cv-review-video');
    const url = state.match.videoUrl;

    reviewState.video?.destroy?.();
    reviewState.video = null;
    host.innerHTML = '';

    if (url && videoKind(url)) {
        reviewState.video = mountVideo(host, url);
        setText('cv-review-note',
            'Tap any row to jump the video there.');
    } else {
        setText('cv-review-note', url
            ? 'That video link cannot be played inside PitchIQ, so the times '
              + 'below are readings rather than something to tap.'
            : 'Add a video link above and every row below becomes tappable.');
    }
}

/** Tear the embedded video down. Called when the match view is left. */
function leaveReview() {
    reviewState.video?.destroy?.();
    reviewState.video = null;
}

function reviewSeek(clockS) {
    if (!reviewState.video) return;
    reviewState.video.seek(videoTime(clockS, state.match.videoOffsetS ?? 0));
    byId('cv-review-video').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/** Video time to match clock — the inverse of videoTime(). */
function toMatchClock(timestampS) {
    return Math.max(0, (timestampS || 0) - (state.match.videoOffsetS ?? 0));
}

function visibleEvents() {
    const events = state.match?.cvEvents?.events || [];
    const decided = state.match?.cvReview?.byEvent || {};
    return events.filter((e) => {
        if (reviewState.filter !== 'all' && e.type !== reviewState.filter) return false;
        if (reviewState.unreviewedOnly && decided[e.id]) return false;
        if (reviewState.inPlayOnly && e.inPlay === false) return false;
        return true;
    });
}

function renderReviewFilters() {
    const host = byId('cv-review-filters');
    host.innerHTML = '';

    const counts = state.match?.cvEvents?.counts || {};
    const options = [
        ['all', `Everything (${(state.match.cvEvents.events || []).length})`],
        ...REVIEW_TYPES
            .filter((type) => counts[type])
            .map((type) => [type, `${type} (${counts[type]})`]),
    ];

    for (const [value, label] of options) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'chip';
        chip.textContent = label;
        chip.classList.toggle('on', reviewState.filter === value);
        chip.addEventListener('click', () => {
            reviewState.filter = value;
            renderReviewFilters();
            renderReviewList();
        });
        host.append(chip);
    }

    const toggles = [
        ['Not checked yet', 'unreviewedOnly', true],
        // Only offered when the log actually marked some of these dead. See
        // deadBallCount() for why an absent log is not the same as none.
        [`Hide the ${deadBallCount()} in stoppages`, 'inPlayOnly', deadBallCount() > 0],
    ];

    for (const [label, key, show] of toggles) {
        if (!show) continue;
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'chip';
        chip.textContent = label;
        chip.classList.toggle('on', reviewState[key]);
        chip.addEventListener('click', () => {
            reviewState[key] = !reviewState[key];
            renderReviewFilters();
            renderReviewList();
        });
        host.append(chip);
    }
}

function renderReviewList() {
    const host = byId('cv-review-list');
    host.innerHTML = '';

    const events = visibleEvents();
    const marks = events.map((e) => ({
        id: e.id,
        clockS: toMatchClock(e.timestampS),
        type: e.type,
        label: `${e.type}${e.outcome ? ` (${e.outcome})` : ''}`,
    }));

    renderStrip(byId('cv-review-scrubber'), {
        marks,
        endS: timelineEnd([
            ...marks,
            ...(state.match.cvReview?.missed || []).map((m) => ({ clockS: m.clockS })),
        ]),
        onSeek: reviewSeek,
        clockText,
    });

    if (!events.length) {
        host.innerHTML = '<div class="empty">Nothing matches that filter.</div>';
        return;
    }

    // A half can produce hundreds of these and the DOM cost of all of them at
    // once is real. The filters above are how you get to the rest.
    for (const event of events.slice(0, 200)) host.append(reviewRow(event));

    if (events.length > 200) {
        const more = document.createElement('div');
        more.className = 'empty';
        more.textContent =
            `Showing the first 200 of ${events.length}. Filter to see the rest.`;
        host.append(more);
    }
}

function reviewRow(event) {
    const decided = state.match.cvReview.byEvent[event.id];
    const clockS = toMatchClock(event.timestampS);

    const row = document.createElement('div');
    row.className = 'list-item review-row';
    if (decided) row.classList.add(`is-${decided.status}`);
    row.innerHTML = `
        <button type="button" class="review-seek">
            <span class="review-clock"></span>
            <span class="review-what"></span>
            <span class="review-dead hidden">dead ball</span>
            <span class="review-who muted"></span>
        </button>
        <div class="review-mark"></div>
        <div class="review-acts">
            <button type="button" class="btn tiny" data-act="confirmed" title="Really happened">✓</button>
            <button type="button" class="btn tiny" data-act="edited" title="Wrong player or type">✎</button>
            <button type="button" class="btn tiny" data-act="rejected" title="Did not happen">✗</button>
        </div>
        <div class="review-edit hidden"></div>`;

    row.querySelector('.review-clock').textContent = clockText(clockS);
    row.querySelector('.review-what').textContent =
        decided?.type || event.type;
    row.querySelector('.review-who').textContent = whoIs(event.trackId);
    // Marked, not hidden. A pass from a throw-in is a real pass, and the coach
    // deciding whether this one happened should know the ball was not moving
    // when the detector claimed it did — that is the case it gets wrong most.
    if (event.inPlay === false) {
        row.querySelector('.review-dead').classList.remove('hidden');
    }
    row.querySelector('.review-mark').append(
        confidenceMark(confidenceBand(event.confidence)),
    );

    row.querySelector('.review-seek').addEventListener('click', () => reviewSeek(clockS));

    for (const button of row.querySelectorAll('[data-act]')) {
        button.classList.toggle('on', decided?.status === button.dataset.act);
        button.addEventListener('click', () => {
            if (button.dataset.act === EDITED) {
                toggleReviewEdit(row, event);
                return;
            }
            decide(event.id, { status: button.dataset.act });
            renderReviewList();
            updateReviewProgress();
        });
    }

    return row;
}

/**
 * Which player a tracked figure belongs to, going through the mapping above.
 *
 * Unmapped is the common case and says so plainly. Naming a guess here would
 * put a real student's name against an event nobody has agreed they were part
 * of, which is the one thing this whole feature is built to avoid.
 */
function whoIs(trackId) {
    const clusters = state.match?.cv?.identity?.clusters || [];
    const cluster = clusters.find((c) => (c.track_ids || []).includes(trackId));
    if (!cluster) return 'unknown figure';

    const playerId = state.match.cvMapping?.[String(cluster.cluster_id)];
    if (playerId === NOT_A_PLAYER) return 'ruled out as not a player';
    if (!playerId) return `figure ${cluster.cluster_id + 1}, unmatched`;

    const player = state.match.roster.find((p) => p.id === playerId);
    return player ? player.playerName : `figure ${cluster.cluster_id + 1}`;
}

function confidenceBand(value) {
    if (value >= 0.7) return 'high';
    if (value >= 0.45) return 'medium';
    return 'low';
}

function toggleReviewEdit(row, event) {
    const host = row.querySelector('.review-edit');
    if (!host.classList.contains('hidden')) {
        host.classList.add('hidden');
        host.innerHTML = '';
        return;
    }

    host.classList.remove('hidden');
    host.innerHTML = `
        <label class="field"><span>It was really a</span><select class="edit-type"></select></label>
        <label class="field"><span>by</span><select class="edit-who"></select></label>
        <button type="button" class="btn tiny edit-save">Save</button>`;

    const decided = state.match.cvReview.byEvent[event.id] || {};
    const typeSelect = host.querySelector('.edit-type');
    for (const type of REVIEW_TYPES) {
        const option = document.createElement('option');
        option.value = type;
        option.textContent = type;
        typeSelect.append(option);
    }
    typeSelect.value = decided.type || event.type;

    const whoSelect = host.querySelector('.edit-who');
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = 'not sure';
    whoSelect.append(blank);
    for (const player of state.match.roster) {
        const option = document.createElement('option');
        option.value = player.id;
        option.textContent = player.jerseyNumber != null
            ? `${player.jerseyNumber} · ${player.playerName}`
            : player.playerName;
        whoSelect.append(option);
    }
    whoSelect.value = decided.playerId || '';

    host.querySelector('.edit-save').addEventListener('click', () => {
        decide(event.id, {
            status: EDITED,
            type: typeSelect.value,
            playerId: whoSelect.value || null,
        });
        renderReviewList();
        updateReviewProgress();
    });
}

function decide(eventId, verdict) {
    const next = { ...state.match.cvReview.byEvent };
    // Tapping the same verdict again clears it, so a mis-tap is one tap to fix
    // rather than a decision that cannot be taken back.
    if (next[eventId]?.status === verdict.status && verdict.status !== EDITED) {
        delete next[eventId];
    } else {
        next[eventId] = verdict;
    }
    state.match.cvReview = { ...state.match.cvReview, byEvent: next };
    queueReviewSave();
}

function updateReviewProgress() {
    const total = (state.match?.cvEvents?.events || []).length;
    const decided = Object.values(state.match?.cvReview?.byEvent || {});
    const missed = (state.match?.cvReview?.missed || []).length;

    const real = decided.filter((d) => d.status !== REJECTED).length;
    const parts = [`${decided.length} of ${total} checked`];
    if (decided.length) {
        parts.push(`${Math.round((real / decided.length) * 100)}% of those were real`);
    }
    parts.push(missed
        ? `${plural(missed, 'miss', 'misses')} recorded`
        : 'no misses recorded yet');

    setText('cv-review-progress', parts.join(' · '));
    renderScorecard();
}

/**
 * Precision and recall per event type, from the verdicts recorded so far.
 *
 * These are the two numbers this whole tool exists to produce, and until now
 * nothing computed either of them. They are also the two numbers easiest to
 * read as more than they are, so the caption is not decoration: everything here
 * describes **the events actually checked**. Precision over twelve of five
 * hundred is a fact about those twelve, and somebody who checked the twelve most
 * obvious ones has measured their own eye, not the detector.
 *
 * Recall is the one that decides whether the ball detector is good enough,
 * because a detector that finds six passes a half and gets all six right scores
 * perfectly on precision and is useless.
 */
function renderScorecard() {
    const host = byId('cv-scorecard');
    const events = state.match?.cvEvents?.events || [];
    const { byType, overall } = reviewScore(events, state.match?.cvReview);

    const rows = Object.entries(byType)
        .filter(([, s]) => s.truePositives + s.falsePositives + s.missed > 0)
        .sort((a, b) => b[1].truePositives + b[1].falsePositives
            - (a[1].truePositives + a[1].falsePositives));

    host.innerHTML = '';
    host.classList.toggle('hidden', !rows.length);
    if (!rows.length) return;

    // A dash, not 0%. Nothing has been checked of that type, and a zero would
    // read as a detector that gets everything wrong.
    const rate = (value) => (value == null ? '—' : `${Math.round(value * 100)}%`);

    const head = document.createElement('div');
    head.className = 'scorecard-row is-head';
    for (const text of ['', 'Right', 'Found', 'Checked']) {
        const cell = document.createElement('span');
        cell.textContent = text;
        head.append(cell);
    }
    host.append(head);

    for (const [type, s] of [...rows, ['Everything', overall]]) {
        const row = document.createElement('div');
        row.className = 'scorecard-row';
        row.classList.toggle('is-total', type === 'Everything');

        const checked = s.truePositives + s.falsePositives;
        const cells = [
            type,
            rate(s.precision),
            // Recall's denominator is what really happened, so it only means
            // anything once somebody has recorded a miss. Saying "100%" off the
            // back of no misses at all would be the most flattering possible
            // reading of no data.
            s.missed ? rate(s.recall) : '—',
            s.missed ? `${checked} · ${plural(s.missed, 'miss', 'misses')}`
                : String(checked),
        ];
        for (const text of cells) {
            const cell = document.createElement('span');
            cell.textContent = text;
            row.append(cell);
        }
        host.append(row);
    }

    const caption = document.createElement('p');
    caption.className = 'scorecard-note';
    caption.textContent = `Out of the ${overall.truePositives + overall.falsePositives}`
        + ` you have checked, not the ${events.length} the video found.`
        + (overall.missed
            ? ''
            : ' "Found" stays blank until you record something it missed —'
                + ' that is the half nothing else can tell you.');
    host.append(caption);
}

/**
 * The reviewed set as a file on the coach's machine.
 *
 * Built and downloaded entirely in the browser: the data is already loaded, so
 * this needs no Firestore read, no rules change and no server. It is the same
 * approach as the tag-log download beside it.
 */
function doDownloadLabels() {
    const labels = reviewLabels(
        state.match?.cvEvents?.events || [],
        state.match?.cvReview,
        {
            teamId: state.team.id,
            matchId: state.match.id,
            opponent: state.match.opponent || null,
            playedOn: state.match.playedOn || null,
            videoOffsetS: state.match.videoOffsetS ?? 0,
        },
    );

    if (!labels.labelled.length && !labels.missed.length) {
        toast('Nothing reviewed yet, so there is nothing to export.', true);
        return;
    }

    const blob = new Blob([JSON.stringify(labels, null, 2)],
        { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `pitchiq-labels-${state.match.id}.json`;
    link.click();
    URL.revokeObjectURL(link.href);

    toast(`Exported ${plural(labels.labelled.length, 'label')}`
        + ` and ${plural(labels.missed.length, 'miss', 'misses')}.`);
}

/** "12:30" or "750" to seconds. Returns null for anything else. */
function parseClock(text) {
    const trimmed = (text || '').trim();
    if (!trimmed) return null;

    const parts = trimmed.split(':');
    if (parts.length > 2 || parts.some((p) => !/^\d+$/.test(p))) return null;

    const seconds = parts.length === 2
        ? Number(parts[0]) * 60 + Number(parts[1])
        : Number(parts[0]);
    return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

function doRecordMiss() {
    const clockS = parseClock(byId('input-missed-clock').value);
    if (clockS === null) {
        toast('Give the time as minutes and seconds, like 12:30.', true);
        return;
    }

    const missed = [
        ...(state.match.cvReview.missed || []),
        { clockS, type: byId('input-missed-type').value, playerId: null },
    ].sort((a, b) => a.clockS - b.clockS);

    // The rules cap this at 300. Refuse here rather than letting the save fail
    // with a permission error that says nothing about what went wrong.
    if (missed.length > 300) {
        toast('That is 300 misses recorded — more than enough to judge by.', true);
        return;
    }

    state.match.cvReview = { ...state.match.cvReview, missed };
    byId('input-missed-clock').value = '';
    queueReviewSave();
    renderReviewList();
    updateReviewProgress();
    toast(`Recorded a missed ${missed.at(-1).type} at ${clockText(clockS)}.`);
}

let reviewSaveTimer = null;
function queueReviewSave() {
    clearTimeout(reviewSaveTimer);
    reviewSaveTimer = setTimeout(saveReviewNow, 600);
}

async function saveReviewNow() {
    const badge = byId('cv-review-state');
    try {
        if (badge) badge.textContent = 'Saving…';
        await saveCvReview(
            state.user, state.team.id, state.match.id, state.match.cvReview,
        );
        if (badge) badge.textContent = 'Saved';
    } catch (err) {
        if (badge) badge.textContent = '';
        toast(err.message || 'Could not save that.', true);
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

/**
 * Attach the video's numbers to the players a coach matched them to.
 *
 * Recomputed from scratch each time rather than patched, so unmatching a
 * figure actually clears the row it was feeding — otherwise a mistake would
 * stay on screen until the page was reloaded.
 */
function mergeCvPlayers(players, match) {
    const stats = cvStatsByPlayer(match.cv?.identity?.tracks, match.cvMapping);

    for (const player of players) {
        for (const key of Object.keys(player)) {
            if (key.startsWith('cv')) delete player[key];
        }
        Object.assign(player, cvReportFields(stats[player.id]));
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

    button.disabled = true;
    try {
        await updateMatch(state.team.id, state.match.id, {
            videoUrl: url || null,
            videoOffsetS: offset,
        });
        state.match.videoUrl = url || null;
        state.match.videoOffsetS = offset;

        // The players' copies. `publishReports` takes them at publish time, and
        // the ordinary order of events — publish after the match, upload the
        // footage that evening, paste the link — leaves every one of them
        // saying "no video for this match yet" forever. See pushVideoToReports.
        const reached = await pushVideoToReports(
            state.team.id, state.match.id, url || null, offset,
        );

        // The review tool below and the block above both embed this same video
        // and read this same offset. Without these they keep the old ones until
        // the page is reloaded, which reads as the link not having saved.
        renderMatchVideoBlock();
        if (!byId('cv-review-block').classList.contains('hidden')) renderReview();

        // One message, after the write rather than before it. A link we will
        // not embed is still saved — a Drive or Hudl link is worth giving a
        // player, it just cannot be seeked — so this says what happened rather
        // than refusing, and it says it once the writing is actually done.
        const added = reached
            ? `, and added to ${plural(reached, 'published report')}`
            : '';
        toast(url && !videoKind(url)
            ? `Saved${added}, but that link cannot be played inside PitchIQ.`
            : `Video link saved${added}`);
    } catch (err) {
        toast(err.message || 'Could not save the video link.', true);
    } finally {
        button.disabled = false;
    }
}

/**
 * Hand the tagged log to the video pipeline, as a file.
 *
 * A file rather than the pipeline reading Firestore itself. The service account
 * cv/publish.py uses bypasses every security rule, so the less it is allowed to
 * touch the better — it writes CV stats and nothing else, and giving it a read
 * path into a match would widen that for no gain. This runs as the coach, under
 * the rules, and the pipeline gets a plain list of events.
 *
 * The ids ride along so a file found later can say which match it came from.
 */
function doDownloadLog() {
    const payload = {
        teamId: state.team.id,
        matchId: state.match.id,
        opponentName: state.match.opponentName ?? null,
        matchDate: state.match.date ?? null,
        // The same number typed in above. The log runs on the match clock and
        // the footage does not, and this is what relates them.
        videoOffsetS: state.match.videoOffsetS ?? 0,
        entries: state.match.log ?? [],
    };

    const stamp = (state.match.date || 'match').replace(/[^\w-]/g, '');
    download(`pitchiq-log-${stamp}-${state.match.id}.json`,
             JSON.stringify(payload, null, 2));
    toast(`Downloaded ${plural(payload.entries.length, 'tagged event')}.`);
}

function download(filename, text) {
    const url = URL.createObjectURL(
        new Blob([text], { type: 'application/json' })
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    // Revoked on the next tick rather than immediately: Safari has not started
    // reading the blob by the time click() returns.
    setTimeout(() => URL.revokeObjectURL(url), 1000);
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
                // Only reaches a player's report where a coach matched the
                // figure to them. No mapping, no cv* fields.
                cvTracks: state.match.cv?.identity?.tracks,
                cvMapping: state.match.cvMapping,
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
    byId('btn-back').addEventListener('click', () => {
        // Leaving the match has to take the video with it. A hidden iframe
        // keeps playing, and a coach walking away from a page that is still
        // talking has no obvious way to stop it.
        leaveReview();
        leaveMatchVideo();
        show('view-main');
    });
    byId('btn-back-roster').addEventListener('click', () => show('view-main'));
    byId('btn-publish').addEventListener('click', doPublish);
    byId('btn-save-video').addEventListener('click', doSaveVideo);
    byId('input-video-url').addEventListener('input', updateVideoHint);
    byId('btn-download-log').addEventListener('click', doDownloadLog);
    byId('btn-cv-missed').addEventListener('click', doRecordMiss);
    byId('btn-cv-labels').addEventListener('click', doDownloadLabels);

    const missedType = byId('input-missed-type');
    for (const type of REVIEW_TYPES) {
        const option = document.createElement('option');
        option.value = type;
        option.textContent = type;
        missedType.append(option);
    }
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
