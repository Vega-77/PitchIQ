import {
    onUser, signOut, resolveAccess, rememberTeam, saveStaffProfile, configWarning,
} from '../assets/auth.js?v=62';
import {
    createTeam, getTeam, listPlayers, addPlayer, invitePlayer,
    setPlayerActive, setPlayerPosition, playerFootprint, erasePlayer, clearThumbs,
    listMatches, getMatch, createMatch, updateMatch, listMatchRoster, listLog,
    aggregateMatch, publishReports, seasonSummary, playerSeason, seasonTotals,
    listStaff, inviteCoach, removeCoach, readCvStats, cvConfidence,
    readCvMapping, saveCvMapping, cvStatsByPlayer, cvReportFields,
    readCvEvents, readCvReview, saveCvReview, pushVideoToReports,
} from '../assets/db.js?v=62';
import { renderStrip, timelineEnd, nowIndex } from '../assets/timeline.js?v=62';
import { renderShotMap, shotSummary } from '../assets/shot-map.js?v=62';
import { renderMatchVideo, teamMarks } from '../assets/match-video.js?v=62';
import {
    sampleCvSummary, SAMPLE_NOTICE, isSample,
    samplePassEvents, samplePassMapping,
} from '../assets/sample-report.js?v=62';
import {
    playersByTrack, passingNetwork, foldEdges, strongestLink, networkNote,
} from '../assets/passing.js?v=62';
import { renderPassMap } from '../assets/pass-map.js?v=62';
import { seasonForms, formNote, MIN_FORM_POINTS } from '../assets/season.js?v=62';
import { renderForms } from '../assets/form-chart.js?v=62';
import {
    NOT_A_PLAYER, rankRosterForCluster, sameFigureCandidates, SAME_KIT_CHROMA,
    cvQualityNotes, roughDuration, reviewScore, reviewLabels, xgTrust,
    erasureNote,
    groupStats, teamStatRows, trackedCoverage, metresPerMinute,
    TRACKED_SHARE_FLOOR, SHOT_RESULTS, shotLedger, xgTally, sumXgTallies,
    xgCalibration, calibrationNote, headerCorrection, headerNote,
    correctedShotMarks, pressingTrend, pressingNote, pressingRead,
    clockFromMatch, clockMapNote, HALF_TIME, SECOND_HALF, blindSplit,
    reviewFeed, FROM_VIDEO, FROM_TAGGED, printStamp,
    POSITIONS, positionOf, positionLabel, isKeeper, groupByPosition,
} from '../assets/report.js?v=62';
import {
    CARD_COLOURS, EVENTS, describeEvent, timelineTone,
} from '../assets/events.js?v=62';
import { mountPitchBackdrop } from '../assets/pitch-backdrop.js?v=62';
import { mount as mountVideo, videoKind } from '../assets/video.js?v=62';
import {
    byId, setText, toast, showOnly, clockText, signed, plural,
    statCard, statGroup, figure, cardChips, timelineRow, minutesChart,
    confidenceMark, stackBar,
} from '../assets/ui.js?v=62';

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
    // Whether the video-derived blocks are being previewed with invented
    // numbers. Never persisted and never sent anywhere; it resets every time a
    // match is opened, so nobody can arrive at a page that is quietly lying.
    cvPreview: false,
};

const show = (view) => showOnly(view, VIEWS);

/**
 * The video-derived document the read-only blocks should render.
 *
 * The sample goes through the same renderers as a real run rather than a
 * preview path of its own, which is the only version of this worth building: a
 * second path would prove that the second path works.
 *
 * Deliberately not used by the cluster picker or the review tool. Both write
 * back to Firestore, and a confirm tapped against an invented event id would
 * put a decision about nothing into a real document. The sample carries no
 * `identity` and no events, so those blocks stay empty on their own — but they
 * read `state.match.cv` directly regardless, so the boundary does not depend on
 * the fixture staying that way.
 */
const activeCv = () => (state.cvPreview ? sampleCvSummary() : state.match?.cv);

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

    // Anyone who has left the team goes to the bottom rather than out of the
    // list. They keep their reports, and a coach who took the wrong row off
    // needs somewhere to find them again.
    const ordered = [...state.players].sort(
        (a, b) => (a.active === false ? 1 : 0) - (b.active === false ? 1 : 0),
    );
    for (const player of ordered) {
        list.append(rosterRow(player));
    }
}

/**
 * The four lines of a team, plus the honest blank.
 *
 * The blank reads "Position" rather than "None" or "—": an empty select on a
 * roster is a question nobody has answered yet, and both of those words look
 * like answers.
 */
function fillPositionSelect(select, chosen) {
    select.innerHTML = '';
    const none = document.createElement('option');
    none.value = '';
    none.textContent = 'Position';
    select.append(none);

    for (const pos of POSITIONS) {
        const option = document.createElement('option');
        option.value = pos.id;
        option.textContent = pos.label;
        select.append(option);
    }
    select.value = positionOf(chosen) || '';
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
        <label class="pos-pick">
            <span class="sr-only"></span>
            <select></select>
        </label>
        <span class="pill"></span>
        <button class="btn small" data-act="invite">Invite</button>
        <button class="btn small danger" data-act="remove">Remove</button>
        <span class="open-hint">&rsaquo;</span>`;

    row.querySelector('.jersey').textContent = player.jerseyNumber ?? '—';
    row.querySelector('.title').textContent = player.name;
    row.querySelector('.sub').textContent = player.emailLower
        || 'No email yet — they cannot see their report without one';

    const gone = player.active === false;
    row.classList.toggle('has-left', gone);

    const linked = Boolean(player.linkedUid);
    const pill = row.querySelector('.pill');
    // "Left the team" outranks whether they can see reports, because it is the
    // reason the row is greyed and the other pill would read as the cause.
    pill.textContent = gone
        ? 'Left the team'
        : (linked ? 'Can see reports' : 'Not joined yet');
    pill.classList.toggle('done', linked && !gone);

    const remove = row.querySelector('[data-act="remove"]');
    if (gone) {
        remove.textContent = 'Back on the roster';
        remove.classList.remove('danger');
    }

    // Saved on change, with no confirm and no undo, because there is nothing to
    // undo: a position is a heading over figures rather than an input to one,
    // so getting it wrong costs a wrong word and never a wrong number. That is
    // also why it can be filled in months later, or never.
    const pick = row.querySelector('.pos-pick select');
    fillPositionSelect(pick, player.position);
    row.querySelector('.pos-pick .sr-only').textContent = `Position for ${player.name}`;
    pick.addEventListener('change', async () => {
        const chosen = pick.value || null;
        try {
            await setPlayerPosition(state.team.id, player.id, chosen);
            player.position = chosen;
        } catch (err) {
            fillPositionSelect(pick, player.position);
            toast(err.message || 'Could not save the position.', true);
        }
    });

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

    remove.addEventListener('click', async () => {
        if (gone) {
            try {
                await setPlayerActive(state.team.id, player.id, true);
                player.active = true;
                renderRoster();
                toast(`${player.name} is back on the roster.`);
            } catch (err) {
                toast(err.message || 'Could not put them back.', true);
            }
            return;
        }
        openRemoveChoice(row, player);
    });

    return row;
}

// ------------------------------------------------ what "remove" is going to do
//
// It used to be one `confirm()` and one deleted document — the squad entry —
// while the student's name, shirt number, minutes, distance and published
// report stayed in every match they had played, and their email address stayed
// on as an invite key. The coach was told "Player removed".
//
// Two intentions wear that word. *They left the team* has to keep the match
// reports, because a report is a record of a match that happened and deleting
// it would change the team's own results. *A guardian asked* has to keep
// nothing. One button could not do both, and the one that existed did neither
// — so the choice is put to the coach in the words of the consequence rather
// than the words of the action.

/** Only one open at a time; two irreversible choices on screen is one too many. */
let removeChoiceFor = null;

function openRemoveChoice(row, player) {
    if (removeChoiceFor === player.id) return closeRemoveChoice();
    closeRemoveChoice();
    removeChoiceFor = player.id;

    const panel = document.createElement('div');
    panel.className = 'remove-choice';
    panel.innerHTML = `
        <p class="rc-lead"></p>
        <div class="rc-options">
            <button class="btn small" data-act="left">They left the team</button>
            <button class="btn small danger" data-act="erase">Erase everything</button>
            <button class="btn small ghost" data-act="cancel">Cancel</button>
        </div>
        <div class="rc-detail"></div>`;
    row.append(panel);

    panel.querySelector('.rc-lead').textContent =
        `${player.name} — what should happen to what we hold about them?`;

    panel.querySelector('[data-act="cancel"]').addEventListener('click', closeRemoveChoice);

    panel.querySelector('[data-act="left"]').addEventListener('click', async () => {
        try {
            await setPlayerActive(state.team.id, player.id, false);
            player.active = false;
            closeRemoveChoice();
            renderRoster();
            toast(`${player.name} has left the team. Their reports are kept.`);
        } catch (err) {
            toast(err.message || 'Could not update the roster.', true);
        }
    });

    const erase = panel.querySelector('[data-act="erase"]');
    const detail = panel.querySelector('.rc-detail');

    erase.addEventListener('click', async () => {
        // Read now rather than guessed, because the coach is about to be shown
        // a number and asked to act on it. Reading it only on the press keeps a
        // season of gets off every roster render.
        erase.disabled = true;
        erase.textContent = 'Checking\u2026';
        let footprint;
        try {
            footprint = await playerFootprint(state.team.id, player.id);
        } catch (err) {
            toast(err.message || 'Could not check what is stored.', true);
            erase.disabled = false;
            erase.textContent = 'Erase everything';
            return;
        }
        if (removeChoiceFor !== player.id) return;   // cancelled while reading
        erase.classList.add('hidden');
        renderErasePlan(detail, player, footprint);
    });
}

function renderErasePlan(host, player, footprint) {
    const { lines } = erasureNote(footprint);

    host.innerHTML = '';
    const list = document.createElement('ul');
    list.className = 'rc-lines';
    for (const line of lines) {
        const li = document.createElement('li');
        li.textContent = line;
        list.append(li);
    }
    host.append(list);

    const confirmRow = document.createElement('div');
    confirmRow.className = 'rc-confirm';
    confirmRow.innerHTML = `
        <label class="rc-type">
            <span>Type <strong>ERASE</strong> to confirm. This cannot be undone.</span>
            <input type="text" autocomplete="off" spellcheck="false">
        </label>
        <button class="btn small danger" disabled></button>`;
    host.append(confirmRow);

    const input = confirmRow.querySelector('input');
    const go = confirmRow.querySelector('button');
    go.textContent = `Erase ${player.name}`;

    // Typed, not clicked. Every other destructive control in this app is one
    // press and every other one can be undone by entering again what was lost.
    // This one cannot, and the difference is worth four seconds of a coach's
    // time.
    input.addEventListener('input', () => {
        go.disabled = input.value.trim().toUpperCase() !== 'ERASE';
    });

    go.addEventListener('click', async () => {
        go.disabled = true;
        go.textContent = 'Erasing\u2026';
        try {
            await erasePlayer(state.user, state.team.id, player.id, footprint);
            state.players = state.players.filter((p) => p.id !== player.id);
            closeRemoveChoice();
            renderRoster();
            toast(`Everything about ${player.name} has been deleted.`);
        } catch (err) {
            // Deliberately not "nothing happened". The erase runs a batch per
            // match, so a failure partway leaves whole matches done and whole
            // matches not, and a coach who is told it failed and assumes the
            // data is intact has been told the opposite of the truth.
            toast(err.message || 'That did not finish. Some matches may already '
                + 'be cleared — open this again to see what is left.', true);
            go.disabled = false;
            go.textContent = `Erase ${player.name}`;
        }
    });
}

function closeRemoveChoice() {
    removeChoiceFor = null;
    document.querySelectorAll('.remove-choice').forEach((el) => el.remove());
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
            position: byId('input-player-position').value || null,
        });
        for (const id of ['input-player-name', 'input-player-number', 'input-player-email']) {
            byId(id).value = '';
        }
        // The position is *not* cleared. A coach adding a squad works through it
        // in lines — four defenders, then the midfield — and re-picking the same
        // answer eleven times is the kind of small friction that ends with the
        // field left empty for the whole roster.
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
        setText('pv-sub', [positionLabel(player.position),
            player.emailLower || 'no email on file'].filter(Boolean).join(' · '));

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
        renderPlayerForm(reports);
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

/**
 * The video-derived figures as rates across the season.
 *
 * Same module and same arithmetic as the player's own page, deliberately. This
 * is the one number a coach and a player look at together, and the conversation
 * goes badly if the two screens worked it out differently.
 */
function renderPlayerForm(reports) {
    const block = byId('pv-form-block');
    if (!block) return;

    const forms = seasonForms(reports);
    const drawn = renderForms(byId('pv-form-charts'), forms, {
        minPoints: MIN_FORM_POINTS,
    });
    block.classList.toggle('hidden', !drawn);
    if (drawn) setText('pv-form-note', formNote(forms, { measured: 'were filmed' }));
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

    renderSeasonXgCheck();

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
        // Off on every open. A preview that survived navigating to another match
        // would show one match's invented numbers under another match's title.
        state.cvPreview = false;
        // Likewise: figure 3 of this match is not figure 3 of the last one.
        sameAsOpen = null;

        // Merge the video's per-player figures onto the tagged ones, so the
        // table can be one table. Only for figures a coach has matched to a
        // player — anything else is a guess with a name attached.
        mergeCvPlayers(stats.players, state.match, stats.matchEndS);

        setText('match-title', `vs ${match.opponentName || '—'}`);
        setText('match-sub',
            `${match.date || 'no date'} · ${(match.status || 'scheduled').replace(/_/g, ' ')}`
            + (match.finalized ? ' · reports published' : ''));

        setText('match-print-stamp', printStamp({
            subject: state.team?.name || null,
            matchLine: `vs ${match.opponentName || '—'}`
                + (match.date ? ` · ${match.date}` : ''),
            estimated: Boolean(activeCv()),
        }));

        setText('score-us', stats.counts.us.goal ?? 0);
        setText('score-them', stats.counts.them.goal ?? 0);

        byId('link-halftime').href =
            `../halftime/?team=${encodeURIComponent(state.team.id)}`
            + `&match=${encodeURIComponent(matchId)}`;

        renderTeamStats(stats);

        // The banner, the shot maps and the shot log all read the review
        // document, so they go through the one function that draws all three.
        // It also rebuilds the banner rather than appending — openMatch runs
        // again every time a coach goes back and picks another match, and a
        // stacking banner is the classic way that goes unnoticed.
        redrawShotViews();
        // Not on that redraw: the press is counted by the pipeline and no
        // verdict a coach records can move it. Drawing it there would be
        // harmless and would also say it depends on the review, which it does
        // not — the block that never changes should not be in the list of
        // blocks that go stale.
        renderPressing();

        renderPlayerTable(stats.players);
        renderMatchVideoBlock();
        renderTimeline(log, roster);
        renderClusterMapping();
        renderExcluded();
        renderReview();
        renderSampleToggle();
        // Last, once every block above has decided whether it is on screen.
        renderMatchRail();

        byId('input-video-url').value = match.videoUrl || '';
        byId('input-video-offset').value = match.videoOffsetS ?? 0;
        byId('input-second-half').value = match.secondHalfVideoS ?? '';
        updateVideoHint();
        renderClockMap();

        const publish = byId('btn-publish');
        publish.disabled = false;
        publish.textContent = match.finalized
            ? 'Re-publish player reports'
            : 'Publish player reports';

        show('view-match');
        window.scrollTo(0, 0);
        // Again, and only now. `renderMatchRail` above built the buttons, but
        // it measured them while `#view-match` was still hidden — every block
        // reads as a zero-height box at the origin, so nothing could be marked
        // as current and the rail opened dead until the first scroll.
        markRailPosition();
    } catch (err) {
        toast(err.message || 'Could not open that match.', true);
        show('view-main');
    }
}

/**
 * The whole match as boxes, sorted into the questions they answer.
 *
 * Tagged counts and video-derived figures go into the same set of groups rather
 * than into a tagged block and a video block. A coach asking "did we pass it
 * forward" does not care which half of the system knew the answer, and the
 * confidence mark on the video rows is what says which is which — that
 * distinction belongs on the row, not in the page layout.
 */
function renderTeamStats(stats) {
    const host = byId('team-stats');
    host.innerHTML = '';

    const cv = activeCv();
    const quality = cv?.quality || {};

    const rows = [
        ...taggedStatRows(stats),
        ...teamStatRows(cv, {
            events: cvConfidence(quality, 'events'),
            possession: cvConfidence(quality, 'possession'),
        }),
    ];

    for (const group of groupStats(rows)) host.append(statGroup(group));

    // The key only earns its place once there are bars to key. Half the rows
    // here are ours alone — goals we scored, fouls we conceded — and a legend
    // over a column of single-sided cards would be pointing at nothing.
    const key = byId('tally-key');
    if (key) {
        const { usName, themName } = teamLabels();
        setText('tk-us', usName);
        setText('tk-them', themName);
        key.hidden = !host.querySelector('.tally');
    }
}

/**
 * The figures somebody tapped on a tablet.
 *
 * Corners and free kicks are counted for whoever was awarded them; fouls, cards
 * and offside are recorded against the offender, so "our fouls" means fouls we
 * committed. The labels say so, because a column of numbers cannot.
 */
function taggedStatRows(stats) {
    const { us, them } = stats.counts;
    return [
        { type: 'match', label: 'Goals for', value: us.goal ?? 0, tone: 'is-good' },
        { type: 'match', label: 'Goals against', value: them.goal ?? 0, tone: '' },
        { type: 'match', label: 'Corners won', value: us.corner ?? 0, tone: '' },
        { type: 'match', label: 'Corners conceded', value: them.corner ?? 0, tone: 'is-muted' },
        { type: 'match', label: 'Fouls committed', value: us.foul ?? 0, tone: '' },
        { type: 'match', label: 'Fouls won', value: them.foul ?? 0, tone: 'is-muted' },
        {
            type: 'match', label: 'Our cards', value: us.card ?? 0,
            tone: us.card ? 'is-warn' : 'is-muted',
        },
        {
            type: 'match', label: 'Offsides against us',
            value: us.offside ?? 0, tone: 'is-muted',
        },
        { type: 'match', label: 'Substitutions', value: stats.subs ?? 0, tone: 'is-muted' },
    ];
}

/**
 * Both sides' shots, side by side.
 *
 * Each map is mirrored so that team attacks right, which is what makes them
 * comparable: two maps facing each other would be a diagram of the pitch, and
 * the question a coach has is "how good were the chances", not "which end".
 * The mirroring is done in cv/report_json.py so no renderer can forget it.
 *
 * Clicking a shot seeks the match video, which is the whole reason to place
 * them rather than count them.
 */
function renderShots() {
    const block = byId('cv-shots-block');
    const cv = activeCv();

    const sides = [
        ['us', 'team_a', 'cv-shots-us', 'cv-shots-us-cap'],
        ['them', 'team_b', 'cv-shots-them', 'cv-shots-them-cap'],
    ];

    const trust = xgTrust(cv?.calibrationErrorM);
    // Any shot the coach has tagged as a header is redrawn and re-totalled at
    // its header xG. Applied here rather than only in the log below so the map,
    // its caption and the check underneath all say the same thing — two totals
    // for the same shots, one corrected and one not, is worse than neither.
    const ledger = shotLedger(
        state.match?.cvEvents?.events || [], state.match?.cvReview,
    );

    let any = false;
    for (const [label, key, hostId, capId] of sides) {
        const marks = correctedShotMarks(cv?.teams?.[key]?.shot_map || [], ledger);
        const drawn = renderShotMap(byId(hostId), marks, {
            onPick: (mark) => seekMatchVideo(toMatchClock(mark.video_s)),
            label: `${label === 'us' ? 'Our' : 'Their'} shots on the pitch`,
            xgTrust: trust,
        });
        any = any || drawn;

        const totals = shotSummary(marks, trust);
        const who = label === 'us'
            ? (state.team?.name || 'Us')
            : (state.match?.opponent || 'Them');
        setText(capId, drawn
            ? `${who} — ${totals.goals} from ${plural(totals.shots, 'shot')}`
                + (totals.xg != null ? `, ${totals.xg.toFixed(2)} xG` : '')
            : `${who} — no shots placed`);
        byId(hostId).classList.toggle('is-empty', !drawn);
        // Marked on the plot itself, not only in the banner above it. These get
        // screenshotted and pasted somewhere with no banner in the crop.
        byId(hostId).classList.toggle('is-sample-plot', drawn && isSample(cv));
    }

    block.classList.toggle('hidden', !any);
    if (!any) return;

    // The sentence about circle size only holds while the circles have one.
    const corrected = headerNote(headerCorrection(ledger));
    setText('cv-shots-note',
        'Both halves are drawn attacking right, so the two maps can be compared. '
        + (trust === 'shot'
            ? 'Bigger circles were better chances. '
            : 'Every shot is drawn the same size — the calibration is too loose to '
                + 'rank them against each other. ')
        + 'Click one to jump the video there.'
        + (corrected ? ` ${corrected}` : ''));
}

// ------------------------------------------------------ how the ball moved
//
// Everything else video-derived on this page is a total. This is the first
// thing that is a shape: who the ball actually travelled between, and which
// pairs never connected at all.
//
// It reads the event list rather than the summary document, which puts two
// limits on it that the note underneath has to carry. Only players a coach has
// named can appear — a line drawn to an unnamed figure looks exactly like a
// fact — and only a calibrated run has the metres to place anyone.

/**
 * Which team's network to draw, and who each figure is.
 *
 * Under the preview this hands back invented events and an invented mapping,
 * because a diagram writes nothing back and this is the only way anyone sees
 * the feature before there is footage. The review tool and the shot log stay
 * empty under the same preview for the opposite reason: they write.
 */
function passingSource() {
    if (state.cvPreview) {
        const { byTrack, nameOf } = samplePassMapping();
        return { events: samplePassEvents(), byTrack, nameOf, truncated: false };
    }

    const byTrack = playersByTrack(
        state.match?.cv?.identity?.clusters || [],
        state.match?.cvMapping || {},
        NOT_A_PLAYER,
    );
    const nameById = new Map(
        (state.match?.roster || []).map((p) => [p.id, p.playerName]),
    );
    return {
        events: state.match?.cvEvents?.events || [],
        byTrack,
        nameOf: (id) => nameById.get(id) || 'a player',
        truncated: Boolean(state.match?.cvEvents?.truncated),
    };
}

function renderPassing() {
    const block = byId('cv-passing-block');
    if (!block) return;

    const { events, byTrack, nameOf, truncated } = passingSource();
    const cv = activeCv();
    const network = passingNetwork(events, {
        byTrack,
        team: 'team_a',
        // The same flip the shot maps do, so our side always plays left to
        // right and two matches can be compared without re-reading which end.
        attackingEnd: cv?.teams?.team_a?.attacking_end || 'right',
    });

    const drawn = renderPassMap(byId('cv-passing'), network, {
        nameOf,
        label: 'Our players, placed where they passed from, and the passes between them',
    });
    block.classList.toggle('hidden', !drawn);
    byId('cv-passing').classList.toggle('is-sample-plot', drawn && state.cvPreview);
    if (!drawn) return;

    setText('cv-passing-note', networkNote(network, { truncated }));
    setText('cv-passing-link',
        strongestLink(foldEdges(network.edges), nameOf) || '');
}

// ------------------------------------------------- how long the press lasted
//
// The first thing on this page that is about *time*. Every other video block
// answers "how much" over the whole window, and a press is the statistic where
// that hides the most: pressing is exhausting, so the question is never whether
// a side pressed but how long they managed it for.
//
// The arithmetic and every refusal in it are in assets/report.js; the pipeline
// has already decided which blocks have a denominator worth dividing by. What
// this does is lay them out so the two kinds of silence stay distinguishable —
// a block where nobody challenged at all is the loudest thing the chart can
// say, and it looks identical to missing data unless it is labelled.

function pressRow(block) {
    const row = document.createElement('div');
    row.className = 'press-row';
    if (block.unchallenged) row.classList.add('is-unchallenged');

    const when = block.startMin == null ? '' : `${block.startMin}–${block.endMin}'`;
    const counts = `${plural(block.allowed, 'pass', 'passes')} allowed, `
        + `${plural(block.actions, 'challenge')}`;

    // A bar only where there is a ratio behind it. The two blocks that get no
    // bar say why in words rather than showing an empty track the eye reads as
    // a small number.
    const bar = block.share == null
        ? `<div class="press-none">${block.unchallenged
            ? 'never challenged' : 'too few to divide'}</div>`
        : `<div class="press-bar"><span style="width:${
            Math.round(block.share * 100)}%"></span></div>`;

    row.innerHTML = `
        <div class="press-when">${when}</div>
        ${bar}
        <div class="press-value">${
            block.ppda == null ? '—' : block.ppda.toFixed(1)}</div>
        <div class="press-counts">${counts}</div>`;
    return row;
}

function renderPressing() {
    const block = byId('cv-pressing-block');
    if (!block) return;

    const cv = activeCv();
    const trend = pressingTrend(cv?.teams?.team_a?.pressing_segments, {
        clock: clockMap(),
    });
    block.classList.toggle('hidden', !trend);
    if (!trend) return;

    const host = byId('cv-pressing');
    host.innerHTML = '';
    host.classList.toggle('is-sample-plot', state.cvPreview);
    for (const row of trend.blocks) host.appendChild(pressRow(row));

    // The lede carries the direction of the bars, because a reader who takes
    // "longer is worse" from the chart alone will take it wrong.
    const lede = ['Passes the opposition strung together for every challenge you '
        + 'made in their half, quarter-hour by quarter-hour. A longer bar is a '
        + 'softer press.'];
    const faded = pressingRead(trend);
    if (faded) lede.push(faded.detail);
    setText('cv-pressing-lede', lede.join(' '));
    setText('cv-pressing-note', pressingNote(trend));
}

// ---------------------------------------------- marking the model's homework
//
// Every xG on the map above is a prediction, and nothing in this app has ever
// checked one. The model was fitted on professional shots; a high school pitch
// is a different game played by different bodies, and whether the number
// transfers is an empirical question nobody has asked.
//
// So a coach says what each detected shot did, and the arithmetic in
// assets/report.js compares that to what the model said. The two design rules
// live there; what matters here is the second one, because it decides the
// layout: on a dozen shots the answer is always "cannot tell", and a card that
// only ever says that would be switched off. Hence the season line on the
// matches tab. The per-match card exists to be filled in, not to be believed.

/**
 * Every shot the pipeline found, with what a coach says became of it.
 *
 * Built from the event list rather than the shot map. The map has positions and
 * no ids, and an id is what lets a verdict outlive the page — but it also means
 * a shot dropped from a truncated event list is missing here, which is said out
 * loud rather than left to be discovered.
 *
 * Real runs only. The sample carries no events, so this stays hidden under the
 * preview on its own — the same boundary the picker and the review tool keep,
 * and for the same reason: a verdict tapped against an invented id would write a
 * decision about nothing into a real document.
 */
function renderShotLog() {
    const block = byId('cv-shotlog-block');
    const rows = shotLedger(
        state.match?.cvEvents?.events || [], state.match?.cvReview,
    );

    block.classList.toggle('hidden', !rows.length);
    if (!rows.length) return;

    // Counted over the shots that still are ones, so this reads the same as the
    // card below it. Two counts with different denominators sitting a line apart
    // is one claim as far as anyone reading them is concerned.
    const live = rows.filter((r) => r.counted);
    const marked = live.filter((r) => r.result != null).length;
    const dropped = rows.length - live.length;
    const truncated = state.match?.cvEvents?.truncated;
    setText('cv-shotlog-note',
        `${marked} of ${plural(live.length, 'shot')} marked. `
        + 'What the video made of each one is printed beside it and is not an '
        + 'answer to this — it is read off a ball the pipeline only sees in '
        + 'some frames, and grading the model against another guess measures '
        + 'nothing. '
        // The other half of the row, and the one that changes a published
        // figure rather than adding to it.
        + 'Tap Header on any that were headed: one fixed camera cannot see the '
        + 'ball\'s height, so every shot above is scored as if it were struck '
        + 'with the foot, and that overstates a headed chance by a third to '
        + 'three times over.'
        + (dropped
            ? ` ${plural(dropped, 'candidate')} struck out below: you rejected `
              + 'them as something other than a shot, so they count for nothing '
              + 'here.'
            : '')
        + (truncated
            ? ' The event list was trimmed to the most confident candidates, so '
              + 'some shots may not be here.'
            : ''));

    const host = byId('cv-shotlog');
    host.innerHTML = '';
    for (const row of rows) host.append(shotLogRow(row));

    renderXgCheck();
}

function shotLogRow(row) {
    const clockS = toMatchClock(row.timestampS);
    const trust = xgTrust(state.match?.cv?.calibrationErrorM);

    const item = document.createElement('div');
    item.className = 'list-item shot-row';
    // Struck through rather than removed. A row that vanishes the moment you
    // reject it in the block below looks like a bug, and this is the only thing
    // on screen that explains why the tally just moved.
    item.classList.toggle('is-out', !row.counted);
    item.classList.toggle('is-header', row.header);
    item.innerHTML = `
        <button type="button" class="shot-seek">
            <span class="shot-clock"></span>
            <span class="shot-side"></span>
            <span class="shot-xg"></span>
            <span class="shot-guess muted"></span>
        </button>
        <div class="shot-results"></div>
        <div class="shot-part">
            <button type="button" class="btn tiny shot-header-btn"
                    title="Score this as a header instead of a foot shot">Header</button>
        </div>`;

    item.querySelector('.shot-clock').textContent = clockAt(row.timestampS);
    item.querySelector('.shot-side').textContent = row.team === 'team_b'
        ? (state.match?.opponentName || 'Them')
        : (state.team?.name || 'Us');
    // Withheld on exactly the bands the shot map withholds a radius. A per-shot
    // number this page has stopped drawing but still prints beside the buttons
    // would be the same claim made quietly.
    item.querySelector('.shot-xg').textContent =
        (trust === 'shot' && row.xg != null) ? `${row.xg.toFixed(2)} xG` : '';
    item.querySelector('.shot-guess').textContent = row.counted
        ? shotAside(row)
        : 'not a shot, per the review below';

    item.querySelector('.shot-seek')
        .addEventListener('click', () => seekMatchVideo(clockS));

    const headerButton = item.querySelector('.shot-header-btn');
    headerButton.classList.toggle('on', row.header);
    headerButton.addEventListener('click', () => toggleHeader(row.id));

    const results = item.querySelector('.shot-results');
    for (const { value, label } of SHOT_RESULTS) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn tiny';
        button.textContent = label;
        button.classList.toggle('on', row.result === value);
        button.addEventListener('click', () => markShot(row.id, value));
        results.append(button);
    }

    return item;
}

/**
 * The quiet line beside each shot: what the video read, and what a header tag
 * did to the number.
 *
 * The movement is shown rather than only the new figure. A coach who taps
 * "Header" and watches 0.72 become 0.43 has learnt something real about how the
 * model sees the game; one who just sees 0.43 has watched a number change for
 * no stated reason.
 */
function shotAside(row) {
    if (row.header) {
        if (row.xgHeader == null) {
            // Deliberately dropped rather than scored as a foot shot. Doing
            // that anyway is precisely the error the tag exists to fix.
            return 'scored as a header — this run predates that reading, so it '
                + 'is left out of the check below';
        }
        const was = row.xgFoot == null ? null : row.xgFoot.toFixed(2);
        return `scored as a header${was ? ` — ${was} off the foot` : ''}`;
    }
    return row.guessed ? `video read it as ${row.guessed.replace(/_/g, ' ')}` : '';
}

/**
 * Say a shot was headed, or take it back.
 *
 * The one thing on this page that changes a number the pipeline produced, which
 * is why it is stored in cvReview beside the coach's other judgements and never
 * written back over `cvStats`. A correction has to stay distinguishable from a
 * measurement; the shot map applies it at render time instead.
 */
function toggleHeader(eventId) {
    const byEvent = { ...state.match.cvReview.byEvent };
    const before = byEvent[eventId] || {};
    const next = { ...before, header: !before.header };

    if (!next.header && next.result == null && !next.status) delete byEvent[eventId];
    else byEvent[eventId] = next;

    state.match.cvReview = { ...state.match.cvReview, byEvent };
    queueReviewSave();
    // The map, its caption and the quality banner are all drawn from these
    // tags, so they move with them.
    redrawShotViews();
}

/**
 * Record — or clear — what a shot did.
 *
 * Merged into whatever verdict the review tool already holds for this event
 * rather than replacing it. The two are different questions: "was that a shot"
 * and "what did it do", and answering one must not silently unanswer the other.
 */
function markShot(eventId, result) {
    const byEvent = { ...state.match.cvReview.byEvent };
    const before = byEvent[eventId] || {};
    // Tapping the same answer again clears it, matching the review buttons —
    // a mis-tap is one tap to fix rather than a mark that cannot be taken back.
    const next = before.result === result
        ? { ...before, result: null }
        : { ...before, result };

    if (next.result == null && !next.status && !next.header) delete byEvent[eventId];
    else byEvent[eventId] = next;

    state.match.cvReview = { ...state.match.cvReview, byEvent };
    queueReviewSave();
    renderShotLog();
}

/**
 * The tally this match contributes to the check, or null if it contributes
 * nothing.
 *
 * Null at `xgTrust` 'none' even when shots have been marked, and that is the
 * point of putting the gate here rather than in the renderer: this is also what
 * gets stored on the match document and summed across the season, and a run
 * whose positions are too loose for the app to print a total is too loose to
 * quietly become a season's evidence. The marks themselves are still kept —
 * they are worth having, and worth re-checking against a better calibration.
 */
function matchXgTally() {
    if (xgTrust(state.match?.cv?.calibrationErrorM) === 'none') return null;
    return xgTally(shotLedger(
        state.match?.cvEvents?.events || [], state.match?.cvReview,
    ));
}

function renderXgCheck() {
    const host = byId('cv-xg-check');
    const cal = xgCalibration(matchXgTally());

    host.innerHTML = '';
    host.classList.toggle('hidden', !cal.shots);
    if (!cal.shots) return;

    host.className = `xg-check is-${cal.verdict}`;

    const figures = document.createElement('div');
    figures.className = 'xg-check-figures';
    const pairs = [
        [cal.predicted.toFixed(2), 'the model expected'],
        [String(cal.scored), cal.scored === 1 ? 'goal went in' : 'goals went in'],
        [`±${cal.band.toFixed(1)}`, 'chance alone, on this many shots'],
    ];
    for (const [value, label] of pairs) {
        const cell = document.createElement('div');
        cell.innerHTML = '<b></b><span></span>';
        cell.querySelector('b').textContent = value;
        cell.querySelector('span').textContent = label;
        figures.append(cell);
    }
    host.append(figures);

    const note = document.createElement('p');
    note.className = 'xg-check-note';
    note.textContent = calibrationNote(cal);
    host.append(note);
}

/**
 * The same check across every match, from tallies already in hand.
 *
 * `listMatches` returns whole match documents, so this costs nothing beyond the
 * read the list needed anyway. That is the whole reason the tally is stored
 * there rather than recomputed: the alternative is reading every match's review
 * document on every dashboard load, to answer a question that changes once a
 * fortnight.
 */
function renderSeasonXgCheck() {
    const host = byId('season-xg');
    if (!host) return;

    const cal = xgCalibration(sumXgTallies(state.matches.map((m) => m.xgCheck)));
    host.classList.toggle('hidden', !cal.shots);
    if (!cal.shots) return;

    setText('season-xg-note',
        calibrationNote(cal, { over: 'marked across the season' }));
}

/**
 * The control that turns the sample on, and what it says.
 *
 * Offered only when this match has no real run. A preview button sitting beside
 * genuine figures is an invitation to mix the two up, and the case it exists
 * for — "there is nothing here and I cannot tell whether that is right" — only
 * arises when there is nothing here.
 */
function renderSampleToggle() {
    const row = byId('cv-sample-toggle');
    if (!row) return;

    const offerable = !state.match?.cv;
    row.classList.toggle('hidden', !offerable);
    row.classList.toggle('is-on', state.cvPreview);
    if (!offerable) return;

    byId('btn-cv-sample').textContent = state.cvPreview
        ? 'Hide the sample'
        : 'Preview with sample data';
    setText('cv-sample-hint', state.cvPreview
        ? SAMPLE_NOTICE
        : 'See what the video-derived sections look like, using made-up numbers.');
}

// ------------------------------------------------------------- the section rail
//
// A match report is about fifteen blocks. Stacked one to a row it ran to roughly
// ten screens on a laptop, which is where this page is actually read — a coach
// opens it at a desk the evening after the game, not on the touchline. The
// layout itself is CSS (`.match-layout` in app.css); this is the part that
// cannot be: which blocks exist on *this* match, and what is still outstanding
// in each.
//
// Only the blocks that are on screen are listed. Half of them appear only when
// a match was filmed, and a rail offering to jump to a section that is not
// there is a menu of dead ends.

/** Numbers worth carrying up to the rail — outstanding work, not totals. */
function railCounts() {
    const counts = {};

    // Figures still waiting for a name. The count that actually decides whether
    // the video columns in the table mean anything, and it lives eight screens
    // down the page.
    const clusters = state.match?.cv?.identity?.clusters || [];
    const answered = Object.entries(state.match?.cvMapping || {})
        .filter(([, id]) => id).length;
    const unnamed = clusters.length - answered;
    if (unnamed > 0) {
        counts['cv-clusters-block'] = {
            n: unnamed,
            title: `${plural(unnamed, 'tracked figure')} still unmatched`,
        };
    }

    const events = (state.match?.cvEvents?.events || []).length;
    const checked = Object.keys(state.match?.cvReview?.byEvent || {}).length;
    if (events - checked > 0) {
        counts['cv-review-block'] = {
            n: events - checked,
            title: `${plural(events - checked, 'event')} not checked yet`,
        };
    }

    return counts;
}

/** The blocks currently on screen, in page order. */
const railBlocks = () => [
    ...(byId('match-body')?.querySelectorAll('section.block[data-rail]') || []),
].filter((block) => !block.classList.contains('hidden'));

function renderMatchRail() {
    const rail = byId('match-rail');
    if (!rail) return;

    const blocks = railBlocks();
    const signature = blocks.map((block) => block.id).join('|');

    // Rebuilt only when the set of sections changes, not on every save. The
    // badges below tick over constantly as a coach names figures, and throwing
    // the buttons away each time would take the keyboard focus with them —
    // which is the same bug the "same figure" strip already had to fix once.
    if (rail.dataset.signature !== signature) {
        rail.dataset.signature = signature;
        rail.innerHTML = '';

        const head = document.createElement('div');
        head.className = 'rail-head';
        head.textContent = 'This report';
        rail.append(head);

        for (const block of blocks) rail.append(railLink(block));
    }

    const counts = railCounts();
    for (const link of rail.querySelectorAll('.rail-link')) {
        const count = counts[link.dataset.target];
        const tag = link.querySelector('.rail-tag');
        tag.textContent = count ? count.n : '';
        tag.hidden = !count;
        // On the button rather than the badge: the badge is 20px of pill and a
        // tooltip you have to hit it exactly to see is not a tooltip.
        if (count) link.title = count.title;
        else link.removeAttribute('title');
    }

    markRailPosition();
}

function railLink(block) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'rail-link';
    button.dataset.target = block.id;

    const label = document.createElement('span');
    label.textContent = block.dataset.rail;
    const tag = document.createElement('span');
    tag.className = 'rail-tag';
    tag.hidden = true;
    button.append(label, tag);

    button.addEventListener('click', () => {
        block.scrollIntoView({
            // Somebody who has turned animation off is telling us that a page
            // sliding under them is unpleasant, not that they want it slower.
            behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
                ? 'auto' : 'smooth',
            block: 'start',
        });
    });

    return button;
}

/**
 * Light the rail entries for whatever is being read.
 *
 * More than one can be lit, and that is the honest answer rather than a
 * shortcut: this layout puts two blocks side by side, so at most heights "the
 * section you are looking at" really is two sections. Picking one of them would
 * be choosing a winner between two things in the same glance.
 */
function markRailPosition() {
    const rail = byId('match-rail');
    if (!rail) return;
    const links = [...rail.querySelectorAll('.rail-link')];
    // `offsetParent` is null when the rail is display:none — every width below
    // the breakpoint, where there is no rail to mark.
    if (!links.length || !rail.offsetParent) return;

    // A line across the upper third, and whatever crosses it is what you are
    // reading. Tried a band first — everything overlapping the top 45% — and it
    // lit four entries out of nine, which is a highlight that has stopped
    // pointing at anything. A line can only be crossed by one block per column,
    // so it says at most two.
    const line = window.innerHeight * 0.3;

    let lit = false;
    for (const link of links) {
        const box = byId(link.dataset.target)?.getBoundingClientRect();
        const here = Boolean(box) && box.top <= line && box.bottom > line;
        link.classList.toggle('is-here', here);
        lit = lit || here;
    }

    // Above the first block, or in a gap between rows: point at what is coming
    // rather than going dark, which reads as the rail having lost track.
    if (!lit) {
        const next = links.find(
            (link) => (byId(link.dataset.target)?.getBoundingClientRect().top ?? -1) > 0,
        );
        (next || links[links.length - 1]).classList.add('is-here');
    }
}

let railPending = false;
function onRailScroll() {
    if (railPending) return;
    railPending = true;
    requestAnimationFrame(() => {
        railPending = false;
        markRailPosition();
    });
}

/** Put the quality banner back, from whatever the state now says. */
function redrawCvNote() {
    document.querySelector('.cv-note')?.remove();
    document.querySelector('.cv-warnings')?.remove();

    const note = cvNote();
    if (note) byId('team-stats').before(note);
    // Above the note, because the two say different things. The note is what
    // these numbers rest on and is always true; a warning is something that
    // went wrong in this particular run.
    const warnings = cvWarnings();
    if (warnings) (note || byId('team-stats')).before(warnings);
}

/**
 * What the pipeline itself flagged about this run, or null.
 *
 * `report_json` has published a `warnings` list since the pipeline existed and
 * **nothing has ever drawn it**. `trustworthy` is defined as `not warnings`
 * (cv/report_json.py), so every one of them was a judgement the pipeline made
 * about its own output that no coach could see — which also means the rule
 * "a warning is only ever about data quality, never a cosmetic limit" was being
 * enforced against a reader who did not exist.
 *
 * Separate from the quality note on purpose. The note is a standing set of
 * caveats about how these figures are made; this is a list of things that were
 * wrong with this run and might be fixable — an untagged restart, a goal the
 * two records disagree about, a window that ran through half-time.
 */
function cvWarnings() {
    const list = activeCv()?.warnings || [];
    if (!list.length) return null;

    const block = document.createElement('div');
    block.className = 'cv-warnings';
    block.innerHTML = '<strong></strong><ul></ul>';
    block.querySelector('strong').textContent = list.length === 1
        ? 'One thing to know about this run'
        : `${list.length} things to know about this run`;

    const ul = block.querySelector('ul');
    for (const text of list) {
        const li = document.createElement('li');
        // textContent, not innerHTML: these strings are assembled in Python and
        // interpolate a source filename among other things.
        li.textContent = text;
        ul.append(li);
    }
    return block;
}

/**
 * Everything the review feeds, redrawn together.
 *
 * Five surfaces read it — the shot maps, their caption, the quality banner, the
 * log with its check, and now the player table — and they are spread from the
 * top of the page to the bottom. Redrawing a subset is how they end up
 * contradicting each other, which is exactly what happened twice before:
 * rejecting a tagged header in the review block left the map still showing the
 * correction, and tagging one left the banner still saying nothing had been
 * tagged. It happened a third time the day the player table started reading the
 * review — a rejected tackle vanished from the scorecard and stayed in the
 * player's row.
 *
 * Cheap enough to do wholesale — a match has a dozen shots and a squad, not a
 * thousand of either.
 */
function redrawShotViews() {
    redrawCvNote();
    renderShots();
    renderShotLog();
    // Not shots, but drawn from the same mapping the picker writes and the same
    // event list the review tool judges — so it goes stale in exactly the same
    // places, and belongs on the same redraw.
    renderPassing();
    // The per-player figures now carry the coach's verdicts, so the table is a
    // view of the review like every other surface above.
    if (state.match?.stats?.players) {
        mergeCvPlayers(
            state.match.stats.players, state.match, state.match.stats.matchEndS,
        );
        renderPlayerTable(state.match.stats.players);
    }
}

/** Flip the preview and redraw only the blocks it can reach. */
function toggleSample() {
    state.cvPreview = !state.cvPreview;

    renderTeamStats(state.match.stats);
    redrawCvNote();

    renderShots();
    renderPassing();
    renderPressing();
    renderExcluded();
    renderSampleToggle();
    // The preview turns whole sections on and off, so the rail has to be
    // rebuilt rather than merely re-counted.
    renderMatchRail();
}

/** The banner over the estimated section, naming what limited it. */
function cvNote() {
    const cv = activeCv();
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
        // Which half, and what decided it. Every pitch picture below this
        // banner is drawn from the answer, and a wrong one mirrors all of them
        // without changing how any of them look.
        period: cv.period,
        periodSource: cv.periodSource,
        // So the foot-shot caveat stops claiming to be unfixed on a match where
        // the coach has already fixed it.
        headersTagged: headerCorrection(shotLedger(
            state.match?.cvEvents?.events || [], state.match?.cvReview,
        ))?.headers || 0,
    });

    // "Measured from video" is a claim, and on the preview it is false. The
    // banner is the one element guaranteed to sit above every estimated block,
    // which makes it the right place to carry the correction.
    const sampled = isSample(cv);
    if (sampled) note.classList.add('is-sample');

    note.innerHTML = '<span></span>';
    note.querySelector('span').textContent = sampled
        ? `${SAMPLE_NOTICE} Nothing here was measured: ${bits.join('; ')}.`
        : (bits.length
            ? `Measured from video: ${bits.join('; ')}. Treat these as estimates.`
            : 'Measured from video. Treat these as estimates.');

    const strong = document.createElement('strong');
    strong.textContent = sampled ? 'Sample ' : 'Estimated ';
    note.prepend(strong);

    // What the run never saw, drawn rather than described. The sentence above
    // can carry one figure out of the split; the bar is the only thing that
    // shows the proportion, which is the whole reason the split exists — a
    // coach needs to know at a glance whether the missing time was stoppages
    // or football.
    const blind = blindSplit(quality);
    if (blind?.segments?.length) {
        // The worst single stretch beside the total, because the same lost
        // minutes as one blackout and as a hundred flickers are different
        // failures and only one of them is worth scrubbing to. In video time,
        // which is what somebody would type into the player.
        const worst = blind.worst
            ? ` · longest ${roughDuration(blind.worst.durationS)} `
                + `at ${clockText(blind.worst.startS)}`
            : '';
        note.append(stackBar(
            blind.segments.map((part) => ({
                ...part, text: roughDuration(part.seconds),
            })),
            { label: `No ball found — ${roughDuration(blind.totalS)} in total${worst}` },
        ));
    }
    return note;
}

function renderPlayerTable(players) {
    const body = byId('player-table').querySelector('tbody');
    body.innerHTML = '';

    // The *last* header row. There are two now — the upper one groups the
    // columns by where their numbers came from — and counting every th in the
    // head would make this colspan two columns too wide.
    const columns = byId('player-table')
        .querySelectorAll('thead tr:last-child th').length;
    if (!players.length) {
        body.innerHTML =
            `<tr><td colspan="${columns}" class="muted">No lineup was set for this match.</td></tr>`;
        return;
    }

    // Most involved first — a coach scanning this wants the standouts on top.
    // Kept as the order *within* a line rather than replaced by it, so the
    // grouping below adds a heading without taking the ranking away.
    const compare = (a, b) => (b.goals + b.assists) - (a.goals + a.assists)
        || b.minutesPlayed - a.minutesPlayed;

    // A position is a fact about the squad, not about this match: nothing in
    // this system records what a player actually played on a given day, so a
    // report opened today groups by where they play *now*. Snapshotting it into
    // the match roster would look more careful and would be worse — it is
    // written when the lineup is set, and most positions get filled in long
    // afterwards, so the snapshot would be empty for every match that mattered.
    const squad = new Map((state.players || []).map((p) => [p.id, p.position]));
    const ordered = players.map((p) => ({ ...p, position: squad.get(p.id) ?? null }));

    // What the minutes bar is a share of. The match's own length, not the
    // longest shift — a squad nobody rotated would otherwise draw itself with
    // eleven full bars and one stub, which is a claim about rotation rather
    // than about minutes. Falls back to the longest shift only when nothing
    // knows how long the match ran, where the two are the same thing anyway.
    const full = Math.max(
        (state.match?.stats?.matchEndS || 0) / 60,
        ...players.map((p) => p.minutesPlayed || 0),
    );

    for (const group of groupByPosition(ordered, compare)) {
        // No heading at all until somebody has set a position — `groupByPosition`
        // returns a single untitled group, and the table is exactly the one that
        // was there before.
        if (group.title) body.append(positionHeadRow(group, columns));
        for (const player of group.players) body.append(playerTableRow(player, full));
    }

    setText('player-table-note',
        [coverageSummary(players), keeperNote(ordered)].filter(Boolean).join(' '));
}

/**
 * What the video column is worth across the roster, in one line.
 *
 * Per-player coverage is four figures a coach has no reason to read one by one.
 * What they need to know before comparing the last column is whether it rests on
 * most of each player's match or on scraps of it, and which players are the
 * scraps — so the ones below the floor are named, and nobody else is mentioned.
 */
function coverageSummary(players) {
    const measured = players.filter((p) => p.cvTrackedShare != null);
    if (!measured.length) return '';

    // A number that moved between two visits looks like a bug unless something
    // says a person moved it. Named as a count of players rather than of
    // events, because the table is a list of players.
    const corrected = measured.filter((p) => p.cvReviewed).length;
    const reviewed = corrected
        ? ` Your review has adjusted the video columns for `
            + `${plural(corrected, 'player')}.`
        : '';

    const thin = measured
        .filter((p) => p.cvTrackedShare < TRACKED_SHARE_FLOOR)
        .sort((a, b) => a.cvTrackedShare - b.cvTrackedShare);

    const head = `Metres a minute is measured over the time the video held each`
        + ` player, not the time they played — ${measured.length} of`
        + ` ${players.length} were matched to a tracked figure at all.`;

    if (!thin.length) return head + reviewed;

    // Named rather than counted. "Four players are thin" sends a coach hunting;
    // the names are what makes the caveat actionable, and past three it becomes
    // a list nobody reads, so the rest are a count.
    const names = thin.slice(0, 3).map((p) => p.playerName).join(', ');
    const rest = thin.length > 3 ? ` and ${thin.length - 3} more` : '';
    const pct = Math.round(TRACKED_SHARE_FLOOR * 100);
    return `${head} It held ${names}${rest} for under ${pct}% of their filmed`
        + ' minutes, so those rows are a sample of the match rather than the'
        + ' whole of it.' + reviewed;
}

/** A line of the team, as a full-width row across the table. */
function positionHeadRow(group, columns) {
    const tr = document.createElement('tr');
    tr.className = 'pos-head';
    const th = document.createElement('th');
    th.colSpan = columns;
    th.scope = 'rowgroup';
    th.textContent = group.title;
    tr.append(th);
    return tr;
}

/**
 * Why a keeper's row is not to be read down the column with the rest.
 *
 * This is the whole reason the position field exists. A goalkeeper covers
 * roughly a third the ground of a midfielder, so in a single ranked table their
 * metres a minute reads as the least mobile player on the pitch — which is not
 * a finding, it is the job. The grouping above already separates them; this
 * says out loud why they are separated, once, and only when there is a keeper
 * on the sheet to separate.
 */
function keeperNote(players) {
    if (!players.some(isKeeper)) return '';
    return 'Goalkeepers are listed on their own line: a keeper covers a fraction'
        + ' of the ground an outfielder does, so metres a minute compares within'
        + ' a line and not across them.';
}

function playerTableRow(player, fullMinutes) {
    const tr = document.createElement('tr');

    // Tagged columns first, then the video ones, so the trustworthy numbers are
    // the ones a coach reads without scrolling sideways on a phone.
    const tagged = [player.minutesPlayed, player.goals, player.assists, player.fouls];
    const rate = metresPerMinute(player.cvDistanceM, player.cvMinutesTracked);
    const derived = [
        player.cvTouches, player.cvPassesCompleted, player.cvTackles,
        rate == null ? null : Math.round(rate),
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

    // Minutes, drawn as well as written. The table is sorted by goals and
    // assists, so nothing in the row order says who actually played the match —
    // and a column of two-digit numbers is exactly where a length reads faster
    // than a figure. Only the minutes: a bar behind every column would be a
    // wall, and these are the only ones that share a scale.
    if (fullMinutes > 0) {
        const cell = cells[2];
        cell.classList.add('bar-cell');
        const bar = document.createElement('i');
        bar.className = 'cell-bar';
        bar.style.setProperty(
            '--w', Math.min(1, (player.minutesPlayed || 0) / fullMinutes).toFixed(3),
        );
        cell.append(bar);
    }

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

    // The rate is the only column whose meaning changes with coverage: the
    // counts beside it are undercounts either way, but a rate over a fifth of
    // someone's minutes is a claim about a fifth of their match while looking
    // exactly like a claim about all of it.
    const share = player.cvTrackedShare;
    if (share != null && share < TRACKED_SHARE_FLOOR && derived[3] != null) {
        const cell = cvCells[3];
        cell.classList.add('cv-partial');
        cell.title = `Measured over ${Math.round(share * 100)}% of the minutes`
            + ' this player was on screen for';
    }

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
    renderThumbControl();

    for (const cluster of ordered) {
        host.append(clusterRow(cluster, mapping));
    }
    restoreFocus();
}

/**
 * The control to put the cursor back on after the list is rebuilt.
 *
 * Naming a figure now rebuilds every row, because one answer changes what the
 * other rows can suggest. That is fine for a mouse and quietly hostile to a
 * keyboard: the `<select>` the coach just used no longer exists, focus falls
 * back to the body, and the next Tab starts again from the top of the page —
 * fifteen times over.
 */
let pendingFocus = null;

function restoreFocus() {
    if (!pendingFocus) return;
    const { clusterId, control } = pendingFocus;
    pendingFocus = null;
    byId('cv-clusters')
        ?.querySelector(`[data-cluster="${clusterId}"] ${control}`)
        ?.focus();
}

// A crop below this many pixels tall in the original footage has no face, no
// number and no hair in it. Matching cv/thumbs.py's own floor for storing one
// at all; this second, higher line is about how much to believe what you see.
const FAINT_THUMB_PX = 40;

/**
 * What a tracked figure looked like — the picture, or an honest substitute.
 *
 * This is the control the whole mapping step turns on. Everything else in the
 * row (a time span, a fragment count, a kit swatch) describes the figure
 * without answering the only question being asked, which is visual.
 *
 * The kit swatch survives as a strip along the bottom rather than being
 * replaced. On the wide framing this pipeline runs on, a crop is often too small
 * to read a shirt colour off, and which side somebody was on is the one thing
 * the clustering is genuinely confident about.
 */
function clusterFace(cluster) {
    const face = document.createElement('div');
    face.className = 'cluster-thumb';

    if (cluster.thumb) {
        const img = document.createElement('img');
        // Not from a URL: the crop is a data: URI written by the pipeline, so
        // there is no request, no storage bucket and no third party involved in
        // showing a photograph of a minor.
        img.src = cluster.thumb;
        img.alt = `Tracked figure ${cluster.cluster_id + 1}`;
        img.loading = 'lazy';
        face.append(img);

        const seen = cluster.thumb_height_px;
        if (seen != null && seen < FAINT_THUMB_PX) {
            face.classList.add('faint');
            face.title = `Only ${Math.round(seen)} pixels tall in the footage`;
        }
    } else {
        // Not an empty box: with no text, "the tracker never saw this figure
        // cleanly" and "the image has not loaded yet" look exactly the same.
        face.classList.add('none');
        face.textContent = 'no clear view';
    }

    const kit = document.createElement('span');
    kit.className = 'kit';
    if (cluster.colour) kit.style.background = labToCss(cluster.colour);
    else kit.style.background = 'var(--line)';
    face.append(kit);

    return face;
}

function clusterRow(cluster, mapping) {
    const row = document.createElement('div');
    row.className = 'list-item cluster-row';
    row.dataset.cluster = String(cluster.cluster_id);
    row.innerHTML = `
        <div class="grow">
            <div class="title"></div>
            <div class="sub"></div>
        </div>
        <div class="figures"></div>
        <label class="cluster-pick">
            <span class="sr-only">Who is this?</span>
            <select></select>
        </label>`;
    row.prepend(clusterFace(cluster));

    const clock = clockMap();
    const fromS = clock.toClock(cluster.first_seen_s ?? 0).clockS;
    const toS = clock.toClock(cluster.last_seen_s ?? 0).clockS;

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
        clock,
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

        // The suggestions are about the player just named, so a change of name
        // is a change of question. Opening the strip on the row the coach is
        // working on is also the only moment it is worth their attention.
        sameAsOpen = select.value && select.value !== NOT_A_PLAYER
            ? cluster.cluster_id
            : null;

        pendingFocus = { clusterId: cluster.cluster_id, control: 'select' };
        updateMappingNote();
        queueMappingSave();
        renderClusterMapping();
    });

    row.classList.toggle('unmatched', !select.value);

    const strip = sameAsStrip(cluster, mapping);
    if (strip) row.append(strip);

    return row;
}

// ------------------------------------------------ the same player, seen twice
//
// `cv/identity.py` will not bridge an absence longer than two seconds, so a
// player who goes off, or who leaves frame while the camera pans, comes back as
// a second figure. Naming both is already the whole fix — the picker is
// many-to-one and `cvStatsByPlayer` sums across every cluster mapped to a name.
//
// What was missing is the saying-so. The list is ordered by how long each
// figure was tracked, so the two halves of one player's match sit nowhere near
// each other, and finding the second one means recognising a face in a forty-row
// list you have already scrolled past. This puts the shortlist under the row
// you are on, with the reasoning shown rather than implied.

/** The row whose suggestions are open. One at a time; a wall of them is noise. */
let sameAsOpen = null;

/**
 * What to say about a candidate, in the order the objections matter.
 *
 * A ruled-out row keeps its reason on it. It is there to be seen and dismissed
 * — an empty space where a figure used to be reads as a bug, and the coach who
 * wonders "why isn't Figure 12 offered?" deserves the answer on screen.
 */
function sameAsWhy(row, clock) {
    if (row.overlapS > 0 && row.ruledOut) {
        return `on screen at the same time for ${roughDuration(row.overlapS)}`;
    }
    if (row.sameTeam === false) return 'the other team’s kit';

    const bits = [];
    const at = clock.toClock(row.cluster.first_seen_s ?? 0).clockS;
    bits.push(row.gapS > 0
        ? `${roughDuration(row.gapS)} later, from ${clockText(Math.max(0, at))}`
        : `from ${clockText(Math.max(0, at))}`);
    if (row.kitS != null && row.kitS > SAME_KIT_CHROMA) bits.push('a different shirt');
    // Zero is the case worth naming: the player this would join was on the
    // bench for every second of it, so either the suggestion or the sub log is
    // wrong. A partial share is ordinary — figures straddle a substitution.
    // Set off with a dash rather than another dot, because it is an objection
    // rather than one more fact in the row.
    return bits.join(' · ')
        + (row.playedShare === 0 ? ' — but they were off the pitch' : '');
}

function sameAsCard(row, clusterId, player, playerName) {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'same-as-card';
    card.dataset.same = String(row.cluster.cluster_id);
    const joined = row.takenBy === null
        && (state.match.cvMapping || {})[String(row.cluster.cluster_id)] === player;
    card.classList.toggle('joined', joined);
    card.classList.toggle('ruled-out', !!row.ruledOut);
    // Not merely dimmed. Two figures on screen together are two people, and
    // that is the one thing here that is a certainty rather than evidence — so
    // it is the one thing the control refuses to let a coach do by mistake.
    card.disabled = !!row.ruledOut;

    card.append(clusterFace(row.cluster));

    const text = document.createElement('span');
    text.className = 'same-as-text';
    const title = document.createElement('strong');
    title.textContent = `Figure ${row.cluster.cluster_id + 1}`;
    const why = document.createElement('small');
    why.textContent = row.takenBy
        ? `already ${nameOf(row.takenBy)}`
        : sameAsWhy(row, clockMap());
    text.append(title, why);
    card.append(text);

    const verb = document.createElement('span');
    verb.className = 'same-as-verb';
    verb.textContent = joined ? '✓ joined' : 'Same player';
    card.append(verb);

    card.addEventListener('click', () => {
        const next = { ...(state.match.cvMapping || {}) };
        const at = String(row.cluster.cluster_id);
        if (joined) delete next[at];
        else next[at] = player;
        state.match.cvMapping = next;
        toast(joined
            ? `Figure ${row.cluster.cluster_id + 1} is no longer ${playerName}.`
            : `Figure ${row.cluster.cluster_id + 1} counts towards ${playerName} too.`);
        // Back on the card just pressed, not on the row it belongs to: joining
        // two fragments is often three cards in a row, and the answer to "was
        // that right?" is the card changing under the cursor.
        pendingFocus = { clusterId, control: `[data-same="${row.cluster.cluster_id}"]` };
        updateMappingNote();
        queueMappingSave();
        renderClusterMapping();
    });

    return card;
}

function nameOf(playerId) {
    if (playerId === NOT_A_PLAYER) return 'ruled out';
    const entry = (state.match.roster || []).find((r) => r.id === playerId);
    return entry?.playerName || 'someone else';
}

/**
 * The strip of other figures that could be this same player.
 *
 * Ruled-out rows are kept and shown last rather than dropped. Only one thing
 * here is a certainty — two figures on screen together are two people — and
 * everything else is evidence; hiding a poor fit on evidence would hide the
 * right answer on the day the kit colour or the video offset is wrong, which is
 * the same rule the picker above already follows.
 */
function sameAsStrip(cluster, mapping) {
    const player = mapping[String(cluster.cluster_id)];
    if (!player || player === NOT_A_PLAYER) return null;

    const clusters = state.match?.cv?.identity?.clusters || [];
    const rows = sameFigureCandidates(clusters, cluster, {
        mapping,
        player,
        roster: state.match.roster || [],
        clock: clockMap(),
        matchEndS: state.match.stats?.matchEndS ?? 0,
    });
    if (!rows.length) return null;

    const playerName = nameOf(player);
    const open = sameAsOpen === cluster.cluster_id;
    const offered = rows.filter((r) => !r.ruledOut && !r.takenBy).length;
    const joined = rows.filter(
        (r) => !r.takenBy && mapping[String(r.cluster.cluster_id)] === player,
    ).length;

    const wrap = document.createElement('div');
    wrap.className = 'same-as';

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'same-as-toggle';
    toggle.setAttribute('aria-expanded', String(open));
    toggle.textContent = joined
        ? `${playerName} is ${plural(joined + 1, 'figure')} · change`
        : `${offered ? plural(offered, 'other figure') : 'No other figure'} `
          + `could also be ${playerName}`;
    toggle.addEventListener('click', () => {
        sameAsOpen = open ? null : cluster.cluster_id;
        renderClusterMapping();
    });
    wrap.append(toggle);

    if (!open) return wrap;

    const list = document.createElement('div');
    list.className = 'same-as-list';
    for (const row of rows) {
        list.append(sameAsCard(row, cluster.cluster_id, player, playerName));
    }
    wrap.append(list);

    const note = document.createElement('p');
    note.className = 'same-as-note';
    note.textContent = 'A player who left the frame and came back is genuinely '
        + 'two figures, and both count towards them. The ones greyed out were on '
        + 'screen at the same time, so they are somebody else.';
    wrap.append(note);

    return wrap;
}

function updateMappingNote() {
    const clusters = state.match?.cv?.identity?.clusters || [];
    const answers = Object.values(state.match?.cvMapping || {});
    // Ruled out is an answer, but it is not a player, and counting it as one
    // would tell a coach their stats are further along than they are.
    const matched = answers.filter((id) => id && id !== NOT_A_PLAYER).length;
    const ruledOut = answers.filter((id) => id === NOT_A_PLAYER).length;
    const tail = ruledOut ? ` ${plural(ruledOut, 'figure')} ruled out.` : '';

    renderMatchRail();

    setText('cv-clusters-note', matched
        ? `${matched} of ${clusters.length} tracked figures matched.${tail} `
          + 'Publish the reports again to send these numbers to the players.'
        : `The video tracked ${clusters.length} figures and cannot tell who is `
          + 'who — shirt numbers are a few pixels tall at this distance. Match '
          + 'the big ones to a player and their stats appear. A figure left '
          + 'unmatched is simply not counted.');
}

// ------------------------------------------------- the pictures, and dropping them
//
// `cv/thumbs.py` cuts a crop of each tracked figure out of the footage so a
// coach can look at a row and say which of their players it is. They are
// photographs of children, and they exist for that one job.
//
// Once the mapping is done the job is finished, and what is left is pictures of
// minors in a database with no remaining purpose. Nobody could remove them:
// they rode inside `cvStats/identity` next to every per-track statistic, and
// `cvStats` is `allow write: if false` to every client. Now they have their own
// document, a coach may delete that one document, and deleting it costs no
// number at all.
//
// Not done automatically on the last name being picked. A coach may reasonably
// want to check their work the next morning, and a control that quietly
// destroyed evidence the moment it judged you finished would be worse than one
// that waits to be pressed.

function renderThumbControl() {
    const host = byId('cv-thumb-control');
    if (!host) return;

    const clusters = state.match?.cv?.identity?.clusters || [];
    const withPictures = clusters.filter((c) => c.thumb).length;

    host.innerHTML = '';
    if (!withPictures) {
        // Deliberately says the pictures are gone rather than saying nothing.
        // "There are no photographs of your players stored" is the reassuring
        // half of this feature, and it only reassures if it is stated.
        host.textContent = clusters.length
            ? 'No pictures of the tracked figures are stored for this match.'
            : '';
        return;
    }

    const line = document.createElement('p');
    line.className = 'thumb-note';
    line.textContent = `${plural(withPictures, 'picture')} of your players, cut `
        + 'out of the footage so you could match the figures to names. Once the '
        + 'matching is done they are not needed.';
    host.append(line);

    const button = document.createElement('button');
    button.className = 'btn small secondary';
    button.textContent = 'Delete the pictures';
    button.title = 'Deletes only the pictures. Every figure, statistic and name '
        + 'you have matched stays exactly as it is.';
    button.addEventListener('click', () => clearMatchThumbs(button));
    host.append(button);
}

async function clearMatchThumbs(button) {
    if (!confirm(
        'Delete the pictures cut from the footage?\n\n'
        + 'The figures, their statistics and the names you have matched are all '
        + 'kept — only the photographs go. Running the pipeline on this match '
        + 'again would produce them a second time.'
    )) return;

    button.disabled = true;
    try {
        await clearThumbs(state.team.id, state.match.id);
        // Dropped from what is already on screen rather than re-fetched: the
        // rows redraw as "no clear view", which is the same thing they show for
        // a figure the tracker never saw cleanly, and is what they will show on
        // every future load.
        for (const cluster of state.match.cv?.identity?.clusters || []) {
            delete cluster.thumb;
            delete cluster.thumb_height_px;
        }
        renderClusterMapping();
        toast('The pictures have been deleted. Everything else is untouched.');
    } catch (err) {
        toast(err.message || 'Could not delete the pictures.', true);
        button.disabled = false;
    }
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
        mergeCvPlayers(
            state.match.stats.players, state.match, state.match.stats.matchEndS,
        );
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

/**
 * Jump the match video to a match-clock moment.
 *
 * Separate from the review block's `seekReview`, which drives its own player.
 * Two videos on one page is unusual, but the review tool is a working surface
 * and the match video is the one a coach shows somebody — merging them would
 * mean scrolling away from the shot map to see the shot.
 */
function seekMatchVideo(clockS) {
    if (!matchVideo) {
        toast('Add a playable video link to jump to a moment.', true);
        return;
    }
    matchVideo.seek(clockMap().toVideo(clockS));
    byId('match-video').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function renderMatchVideoBlock() {
    const block = byId('match-video-block');
    const url = state.match?.videoUrl;

    matchVideo?.destroy?.();
    matchVideo = null;
    byId('match-now').innerHTML = '';

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
            clock: clockMap(),
            marks,
            clockText,
            // So a mark tagged in stoppage time does not fall off the end of a
            // bar drawn to ninety minutes.
            extraTimes: (state.match.log || []).map((e) => e.matchClockS || 0),
            emptyText: 'No goals, cards or substitutions were tagged.',
            onClock: ({ videoS, clockS, period }) => {
                renderMatchNow(videoS, period);
                setTimelineNow(clockS);
            },
            notes: {
                embed: `${plural(marks.length, 'moment')} marked. Tap one to jump `
                    + 'straight to it, and the bar follows the video as it plays. '
                    + 'Only goals, cards and substitutions are marked — the '
                    + 'restarts would bury them.',
                link: 'That link cannot be played inside PitchIQ, so the times '
                    + 'below are match-clock readings rather than buttons.',
                none: '',
            },
        },
    );
}

/**
 * The match clock under the strip, moving with the video.
 *
 * The half is named rather than assumed. It is the one thing a reading alone
 * cannot say — 3:20 belongs to both halves — and it is also the visible proof
 * that the second-half anchor above is set, since without it `periodAt` has no
 * opinion and this says nothing about which half it is.
 */
function renderMatchNow(videoS, period) {
    const host = byId('match-now');
    host.innerHTML = '';

    const clock = document.createElement('span');
    clock.className = 'now-clock';
    clock.textContent = clockAt(videoS);

    const what = document.createElement('span');
    what.textContent = period === HALF_TIME
        ? 'the video is inside the interval'
        : (period
            ? `on the match clock — ${period === SECOND_HALF ? 'second' : 'first'} half`
            : 'on the match clock');

    host.append(clock, what);
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

/**
 * Draw what the two timing fields describe, and say what follows from it.
 *
 * The strip is the part worth having. Two numbers in two boxes are two numbers;
 * the same two drawn to scale are a recording with a lead-in, a half, a break
 * and a second half still running — and a break drawn as three quarters of the
 * bar is a typo a coach spots without reading anything.
 *
 * Only the part that is actually known gets drawn. The second half runs to the
 * end of the footage and nothing here knows where that is, so it is an open
 * segment rather than a segment sized by a guess at how long a half lasts.
 */
function renderClockMap() {
    const host = byId('clock-map');
    const note = byId('clock-map-note');
    if (!host || !note) return;

    const inputs = {
        videoOffsetS: Number(byId('input-video-offset').value) || 0,
        secondHalfVideoS: secondHalfInput(),
        halfTimeClockS: state.match?.halfTimeClockS ?? null,
    };

    const { tone, text } = clockMapNote(inputs, clockText);
    note.textContent = text;
    note.classList.toggle('is-warn', tone === 'warn');
    note.classList.toggle('is-good', tone === 'ok');

    host.innerHTML = '';
    host.classList.toggle('hidden', tone !== 'ok');
    if (tone !== 'ok') return;

    const leadInS = Math.max(0, inputs.videoOffsetS);
    const firstHalfS = inputs.halfTimeClockS;
    const breakS = inputs.secondHalfVideoS - leadInS - firstHalfS;

    const segments = [
        ['lead-in', 'Before kick-off', leadInS],
        ['first', 'First half', firstHalfS],
        ['break', 'Half-time', breakS],
    ].filter(([, , seconds]) => seconds > 0);

    const total = segments.reduce((sum, [, , seconds]) => sum + seconds, 0);
    for (const [kind, label, seconds] of segments) {
        const part = document.createElement('span');
        part.className = `cm-part is-${kind}`;
        // Sized by share of the footage up to the restart, so the segments are
        // in proportion to each other and to nothing invented.
        part.style.flex = `${seconds / total}`;
        part.title = `${label} — ${clockText(seconds)}`;
        part.append(labelled('cm-label', label), labelled('cm-time', clockText(seconds)));
        host.append(part);
    }

    const rest = document.createElement('span');
    rest.className = 'cm-part is-second';
    rest.title = 'Second half — runs to the end of the footage';
    rest.append(labelled('cm-label', 'Second half'), labelled('cm-time', 'to the end'));
    host.append(rest);
}

function labelled(className, text) {
    const span = document.createElement('span');
    span.className = className;
    span.textContent = text;
    return span;
}

/** The typed second-half kick-off, or null for a field left blank. */
function secondHalfInput() {
    const raw = byId('input-second-half').value.trim();
    if (!raw) return null;
    const value = Number(raw);
    return Number.isFinite(value) && value >= 0 ? value : null;
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

    const cv = activeCv();
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
    // The strip is rebuilt every time a chip is tapped, so the playhead has to
    // be re-applied afterwards from somewhere. `atS` is the last position the
    // video reported, in footage seconds, or null if it has not said yet.
    strip: null, atS: null, stopClock: null,
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
        when.textContent = clockAt(seconds);

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

    leaveReview();
    host.innerHTML = '';

    if (url && videoKind(url)) {
        reviewState.video = mountVideo(host, url);
        setText('cv-review-note',
            'Tap any row to jump the video there.');
        // The other direction, and the reason the miss form no longer asks a
        // coach to read a number off the player: the video reports where it is,
        // and this is the one place that turns a position in the footage into a
        // reading on the match clock.
        reviewState.stopClock = reviewState.video.onTime((videoS) => {
            reviewState.atS = videoS;
            reviewState.strip?.setNow(toMatchClock(videoS));
            renderReviewNow();
        });
    } else {
        setText('cv-review-note', url
            ? 'That video link cannot be played inside PitchIQ, so the times '
              + 'below are readings rather than something to tap.'
            : 'Add a video link above and every row below becomes tappable.');
    }
    renderReviewNow();
}

/**
 * Where the video is, in the clock the form below it is asking for.
 *
 * Shown rather than only used, because the conversion is the whole point: a
 * coach who can see that 20:07 of footage is 18:07 of football can tell at a
 * glance whether the kick-off offset above is right, and a wrong offset is
 * otherwise invisible until every marker lands in the warm-up.
 */
function renderReviewNow() {
    const host = byId('cv-review-now');
    const button = byId('btn-missed-here');
    host.innerHTML = '';

    const at = reviewState.atS;
    const known = reviewState.video && at != null;
    button.classList.toggle('hidden', !known);
    if (!known) return;

    const { period } = clockMap().toClock(at);
    const clock = document.createElement('span');
    clock.className = 'now-clock';
    clock.textContent = clockAt(at);

    const what = document.createElement('span');
    // "half-time on the match clock" would be a reading that does not exist.
    // The interval has a position in the footage and no position in the match.
    what.textContent = period === HALF_TIME
        ? `— ${clockText(Math.round(at))} into the footage`
        : `on the match clock — ${clockText(Math.round(at))} into the footage`;

    host.append(clock, what);
}

/** Put the video's own position into the miss box, as a clock reading. */
function useVideoPosition() {
    if (reviewState.atS == null) return;
    const { clockS, period } = clockMap().toClock(reviewState.atS);
    // Half-time is a real answer to "where is the video" and a useless one to
    // "when did this happen": every second of the interval reads as the same
    // second, so a miss recorded here would be filed at a moment the match was
    // not being played. Refuse rather than write down the frozen reading.
    if (period === HALF_TIME) {
        toast('The video is inside half-time — nothing to record there.', true);
        return;
    }
    byId('input-missed-clock').value = clockText(clockS);
}

/** Tear the embedded video down. Called when the match view is left. */
function leaveReview() {
    reviewState.stopClock?.();
    reviewState.stopClock = null;
    reviewState.video?.destroy?.();
    reviewState.video = null;
    reviewState.atS = null;
    reviewState.strip = null;
}

function reviewSeek(clockS) {
    if (!reviewState.video) return;
    reviewState.video.seek(clockMap().toVideo(clockS));
    byId('cv-review-video').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

/** This match's map between the footage and the clock. See clockFromMatch. */
const clockMap = () => clockFromMatch(state.match);

/** What the clock read at a position in the footage. */
function toMatchClock(timestampS) {
    return clockMap().toClock(timestampS || 0).clockS;
}

/**
 * That reading as a label — except during the break, where there isn't one.
 *
 * The clock froze at half-time, so every moment in the interval shares the
 * second the first half ended on. Printing that second is not wrong so much as
 * meaningless: three things drawn at 45:12 did not happen at the same time.
 * Only ever seen once the second-half anchor is set, since without it nothing
 * knows the break is there at all.
 */
function clockAt(timestampS) {
    const { clockS, period } = clockMap().toClock(timestampS || 0);
    return period === HALF_TIME ? 'half-time' : clockText(clockS);
}

/** Both records of this match, merged and put on the clock. See `reviewFeed`. */
function reviewItems() {
    return reviewFeed(
        state.match?.cvEvents?.events || [],
        state.match?.log || [],
        { clock: clockMap(), missed: state.match?.cvReview?.missed || [] },
    );
}

/**
 * The merged feed, minus whatever the chips are hiding.
 *
 * A tagged row survives `all` and the `tagged` chip and nothing else. Filtering
 * to `pass` means "show me the passes it claims", and a corner is not one of
 * those — leaving the log in would make every type filter a mixed list. The
 * two toggles are about candidates by definition: a tagged entry has no verdict
 * to be missing, and its own `inPlay` is not a thing the log ever recorded.
 */
function visibleItems() {
    const decided = state.match?.cvReview?.byEvent || {};
    return reviewItems().filter((item) => {
        if (item.source === FROM_TAGGED) {
            return reviewState.filter === 'all' || reviewState.filter === FROM_TAGGED;
        }
        if (reviewState.filter === FROM_TAGGED) return false;
        const event = item.event;
        if (reviewState.filter !== 'all' && event.type !== reviewState.filter) return false;
        if (reviewState.unreviewedOnly && decided[event.id]) return false;
        if (reviewState.inPlayOnly && event.inPlay === false) return false;
        return true;
    });
}

function renderReviewFilters() {
    const host = byId('cv-review-filters');
    host.innerHTML = '';

    const counts = state.match?.cvEvents?.counts || {};
    const taggedCount = reviewItems()
        .filter((item) => item.source === FROM_TAGGED).length;
    const options = [
        ['all', `Everything (${(state.match.cvEvents.events || []).length})`],
        ...REVIEW_TYPES
            .filter((type) => counts[type])
            .map((type) => [type, `${type} (${counts[type]})`]),
        // Last, and only when there is a log. These are not a kind of candidate
        // — they are the other record — so they sit apart from the type chips
        // rather than reading as one more thing the detector found.
        ...(taggedCount ? [[FROM_TAGGED, `tagged by hand (${taggedCount})`]] : []),
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

    const items = visibleItems();
    // The strip stays the pipeline's own. It is a picture of what the detector
    // found across the half, and salting it with human taps would make a run
    // that found nothing look busy.
    const marks = items
        .filter((item) => item.source === FROM_VIDEO)
        .map(({ event }) => ({
            id: event.id,
            clockS: toMatchClock(event.timestampS),
            type: event.type,
            label: `${event.type}${event.outcome ? ` (${event.outcome})` : ''}`,
        }));

    reviewState.strip = renderStrip(byId('cv-review-scrubber'), {
        marks,
        endS: timelineEnd([
            ...marks,
            ...(state.match.cvReview?.missed || []).map((m) => ({ clockS: m.clockS })),
        ]),
        onSeek: reviewSeek,
        clockText,
    });
    // A fresh strip knows nothing about where the video got to. Without this
    // the playhead vanishes on every filter tap and comes back a second later,
    // which reads as a flicker rather than as a rebuild.
    if (reviewState.atS != null) {
        reviewState.strip.setNow(toMatchClock(reviewState.atS));
    }

    if (!items.length) {
        host.innerHTML = '<div class="empty">Nothing matches that filter.</div>';
        return;
    }

    // A half can produce hundreds of these and the DOM cost of all of them at
    // once is real. The filters above are how you get to the rest.
    for (const item of items.slice(0, 200)) {
        host.append(item.source === FROM_TAGGED ? taggedRow(item) : reviewRow(item));
    }

    if (items.length > 200) {
        const more = document.createElement('div');
        more.className = 'empty';
        more.textContent =
            `Showing the first 200 of ${items.length}. Filter to see the rest.`;
        host.append(more);
    }
}

/**
 * One entry from the tagged log, sitting where it happened.
 *
 * Read-only on purpose, and visibly a different kind of thing. This is a
 * human's own record made at the time; there is no candidate here to confirm or
 * reject, and offering the buttons would invite a reviewer to "check" a fact
 * that was never in question and quietly imply it had been scored.
 *
 * The exception is a goal with nothing found near it, which is the one place
 * the log can tell the pipeline something: that is a miss, already proved, and
 * one tap records it instead of typing the clock back in from memory.
 */
function taggedRow(item) {
    const row = document.createElement('div');
    row.className = 'list-item review-row is-tagged';
    row.innerHTML = `
        <button type="button" class="review-seek">
            <span class="review-clock"></span>
            <span class="review-what"></span>
            <span class="review-who muted">tagged by hand</span>
        </button>
        <div class="review-acts"></div>`;

    row.querySelector('.review-clock').textContent = clockText(item.clockS);
    // The same wording the match timeline uses, from the same helper. Two
    // strips on one page naming the same team differently would read as two
    // different matches.
    row.querySelector('.review-what').textContent = describeEvent(item.entry, {
        ...teamLabels(),
        playerName: state.match?.roster
            ?.find((p) => p.id === item.entry.playerId)?.playerName,
    });
    row.querySelector('.review-seek')
        .addEventListener('click', () => reviewSeek(item.clockS));

    const acts = row.querySelector('.review-acts');
    if (item.suggestion) {
        row.classList.add('is-missed-goal');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn tiny';
        if (item.suggestion.recorded) {
            button.textContent = 'miss recorded';
            button.disabled = true;
        } else {
            button.textContent = 'the video missed this';
            button.addEventListener('click', () => recordMiss(
                item.suggestion.clockS, item.suggestion.type,
            ));
        }
        acts.append(button);
    }

    return row;
}

function reviewRow(item) {
    const event = item.event;
    const decided = state.match.cvReview.byEvent[event.id];
    const clockS = item.clockS;

    const row = document.createElement('div');
    row.className = 'list-item review-row';
    if (decided) row.classList.add(`is-${decided.status}`);
    row.innerHTML = `
        <button type="button" class="review-seek">
            <span class="review-clock"></span>
            <span class="review-what"></span>
            <span class="review-dead hidden">dead ball</span>
            <span class="review-near hidden"></span>
            <span class="review-who muted"></span>
        </button>
        <div class="review-mark"></div>
        <div class="review-acts">
            <button type="button" class="btn tiny" data-act="confirmed" title="Really happened">✓</button>
            <button type="button" class="btn tiny" data-act="edited" title="Wrong player or type">✎</button>
            <button type="button" class="btn tiny" data-act="rejected" title="Did not happen">✗</button>
        </div>
        <div class="review-edit hidden"></div>`;

    row.querySelector('.review-clock').textContent = clockAt(event.timestampS);
    row.querySelector('.review-what').textContent =
        decided?.type || event.type;
    row.querySelector('.review-who').textContent = whoIs(event.trackId);
    // Marked, not hidden. A pass from a throw-in is a real pass, and the coach
    // deciding whether this one happened should know the ball was not moving
    // when the detector claimed it did — that is the case it gets wrong most.
    if (event.inPlay === false) {
        row.querySelector('.review-dead').classList.remove('hidden');
    }
    // What a human said was happening within a few seconds of this. Almost
    // every hard judgement here is a question about context — a "pass" two
    // seconds after a throw-in is the throw — and until now answering it meant
    // scrolling to a different strip on a different part of the page.
    if (item.nearbyTag) {
        const near = row.querySelector('.review-near');
        near.classList.remove('hidden');
        near.textContent = `${tagLabel(item.nearbyTag.type)} ${gapWords(item.nearbyTag.gapS)}`;
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

/** A tagged type as a coach would say it — "throw-in", not "throw_in". */
function tagLabel(type) {
    return EVENTS[type]?.label?.toLowerCase() || type.replace(/_/g, ' ');
}

/**
 * How far a tagged entry sits from a candidate, in words rather than a signed
 * number. "2s before" and "-2" are the same fact and only one of them can be
 * read at a glance while judging four hundred rows.
 */
function gapWords(gapS) {
    const seconds = Math.round(Math.abs(gapS));
    if (!seconds) return 'at the same moment';
    return `${seconds}s ${gapS < 0 ? 'before' : 'after'}`;
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
    const before = next[eventId];
    // What a shot did, and what it was struck with, are separate answers from
    // whether it was a shot at all. Both survive every verdict here — including
    // a rejection, which takes the shot out of the xG check without throwing
    // away what the coach saw. Undoing a mis-tapped rejection therefore does not
    // mean marking the shot again.
    const kept = {};
    if (before?.result) kept.result = before.result;
    if (before?.header) kept.header = true;

    // Tapping the same verdict again clears it, so a mis-tap is one tap to fix
    // rather than a decision that cannot be taken back.
    if (before?.status === verdict.status && verdict.status !== EDITED) {
        if (kept) next[eventId] = kept;
        else delete next[eventId];
    } else {
        next[eventId] = { ...kept, ...verdict };
    }
    state.match.cvReview = { ...state.match.cvReview, byEvent: next };
    queueReviewSave();
    // Rejecting a candidate up here takes it out of the xG check down there,
    // and off the shot map above if it was a tagged header. One rule, applied
    // at every point that writes to cvReview: the ledger changed, so redraw
    // everything drawn from it.
    redrawShotViews();
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
    renderMatchRail();
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
            secondHalfVideoS: state.match.secondHalfVideoS ?? null,
            halfTimeClockS: state.match.halfTimeClockS ?? null,
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
    if (recordMiss(clockS, byId('input-missed-type').value)) {
        byId('input-missed-clock').value = '';
    }
}

/**
 * Add one thing the video did not find. Returns whether it went in.
 *
 * Shared by the typed form and by the one-tap button on a tagged goal, so both
 * routes produce the same record and hit the same cap. The button is the whole
 * point of merging the two records: the tagger already wrote down that a goal
 * happened at 34:12, and asking a coach to read that off one strip and retype
 * it into another is asking them to be a worse copy of a file that exists.
 */
function recordMiss(clockS, type) {
    const missed = [
        ...(state.match.cvReview.missed || []),
        { clockS, type, playerId: null },
    ].sort((a, b) => a.clockS - b.clockS);

    // The rules cap this at 300. Refuse here rather than letting the save fail
    // with a permission error that says nothing about what went wrong.
    if (missed.length > 300) {
        toast('That is 300 misses recorded — more than enough to judge by.', true);
        return false;
    }

    state.match.cvReview = { ...state.match.cvReview, missed };
    queueReviewSave();
    renderReviewList();
    updateReviewProgress();
    toast(`Recorded a missed ${type} at ${clockText(clockS)}.`);
    return true;
}

let reviewSaveTimer = null;
function queueReviewSave() {
    clearTimeout(reviewSaveTimer);
    reviewSaveTimer = setTimeout(saveReviewNow, 600);
}

async function saveReviewNow() {
    // Two badges, one save. The review block and the shot log write the same
    // document from opposite ends of the page, and whichever one the coach is
    // looking at has to be the one that says it worked.
    const badges = ['cv-review-state', 'cv-shotlog-state']
        .map((id) => byId(id)).filter(Boolean);
    const say = (text) => badges.forEach((b) => { b.textContent = text; });

    try {
        say('Saving…');
        await saveCvReview(
            state.user, state.team.id, state.match.id, state.match.cvReview,
        );
        await saveXgCheck();
        say('Saved');
    } catch (err) {
        say('');
        toast(err.message || 'Could not save that.', true);
    }
}

/**
 * Roll this match's four-number tally onto its own document.
 *
 * Written here rather than at publish time because the season line has to move
 * as the marking is done — a coach who marks six shots and sees nothing change
 * anywhere has been given a button and no reason to press it again.
 *
 * Skipped whenever the tally is unchanged, which is most saves: the review
 * document is written on every verdict, and only the shot buttons move this.
 */
async function saveXgCheck() {
    const tally = matchXgTally();
    const before = state.match.xgCheck ?? null;
    const same = tally == null
        ? before == null
        : before != null
            && before.shots === tally.shots
            && before.scored === tally.scored
            && before.predicted === tally.predicted
            && before.variance === tally.variance;
    if (same) return;

    await updateMatch(state.team.id, state.match.id, { xgCheck: tally });
    state.match.xgCheck = tally;
    // The dashboard's copy of this match, so the season line is right the
    // moment a coach goes back — without re-reading anything.
    const listed = state.matches.find((m) => m.id === state.match.id);
    if (listed) listed.xgCheck = tally;
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
function mergeCvPlayers(players, match, matchEndS = null) {
    // The coach's own verdicts go in here, not just onto the scorecard. Until
    // they did, a coach could reject thirty phantom passes, watch precision
    // fall, and still hand the player a report crediting them with all thirty.
    const stats = cvStatsByPlayer(match.cv?.identity?.tracks, match.cvMapping, {
        events: match.cvEvents?.events,
        review: match.cvReview,
        clusters: match.cv?.identity?.clusters,
    });
    const context = {
        window: match.cv?.window,
        clock: clockFromMatch(match),
        matchEndS,
    };

    for (const player of players) {
        for (const key of Object.keys(player)) {
            if (key.startsWith('cv')) delete player[key];
        }
        Object.assign(player, cvReportFields(
            stats[player.id],
            trackedCoverage(stats[player.id]?.minutes_tracked, player.stints, context),
        ));
    }
}

// The rows of the timeline in the order they were rendered, so the one the
// video is inside can be picked out. Newest first, which `nowIndex` does not
// care about — it is given the same array it is asked about.
let timelineRows = [];

/**
 * Light up the tagged entry the match video is currently inside.
 *
 * Driven by the match video rather than by the review tool's player, because
 * this list sits with the match video and a highlight that answered to a
 * different player would be a second opinion about where "now" is.
 */
function setTimelineNow(clockS) {
    const at = nowIndex(timelineRows, clockS);
    timelineRows.forEach((row, i) => row.el.classList.toggle('is-now', i === at));
}

function renderTimeline(log, roster) {
    const list = byId('timeline');
    list.innerHTML = '';
    timelineRows = [];

    if (!log.length) {
        list.innerHTML = '<div class="empty">Nothing was tagged for this match.</div>';
        return;
    }

    const nameById = new Map(roster.map((r) => [r.id, r.playerName]));
    const { usName, themName } = teamLabels();

    for (const entry of log.slice().reverse()) {
        const clockS = entry.matchClockS;
        const row = timelineRow({
            clock: clockText(clockS),
            text: describeEvent(entry, {
                usName, themName, playerName: nameById.get(entry.playerId),
            }),
            sideLabel: entry.kind === 'period'
                ? ''
                : (entry.side === 'them' ? themName : usName),
            tone: timelineTone(entry),
            // Every tagged entry, not just the ones marked on the strip above.
            // The strip is deliberately thin — goals, cards and subs — because
            // eighty ticks is a texture; this list is where the restarts and
            // the fouls live, and each of them is a moment on the video too.
            onSeek: matchVideo ? () => seekMatchVideo(clockS) : undefined,
        });
        timelineRows.push({ clockS, el: row });

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
    const secondHalfVideoS = secondHalfInput();

    button.disabled = true;
    try {
        await updateMatch(state.team.id, state.match.id, {
            videoUrl: url || null,
            videoOffsetS: offset,
            secondHalfVideoS,
        });
        state.match.videoUrl = url || null;
        state.match.videoOffsetS = offset;
        state.match.secondHalfVideoS = secondHalfVideoS;

        // The players' copies. `publishReports` takes them at publish time, and
        // the ordinary order of events — publish after the match, upload the
        // footage that evening, paste the link — leaves every one of them
        // saying "no video for this match yet" forever. See pushVideoToReports.
        const reached = await pushVideoToReports(
            state.team.id, state.match.id, url || null, {
                videoOffsetS: offset,
                secondHalfVideoS,
                halfTimeClockS: state.match.halfTimeClockS ?? null,
            },
        );

        // The review tool below and the block above both embed this same video
        // and read this same offset. Without these they keep the old ones until
        // the page is reloaded, which reads as the link not having saved.
        renderMatchVideoBlock();
        // And the timeline, whose rows are only buttons when there is a player
        // to seek — pasting the first link is exactly the moment that changes.
        renderTimeline(state.match.log || [], state.match.roster || []);
        renderClockMap();
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
        // The numbers typed in above. The log runs on the match clock and the
        // footage does not, and these are what relate them — all three, since
        // the offset alone stops being right at half-time.
        videoOffsetS: state.match.videoOffsetS ?? 0,
        secondHalfVideoS: state.match.secondHalfVideoS ?? null,
        halfTimeClockS: state.match.halfTimeClockS ?? null,
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
                // The review, so a published report carries the same
                // corrections the coach is looking at. Sending the tracks
                // without them would publish the uncorrected figures from the
                // screen that shows the corrected ones.
                cvEvents: state.match.cvEvents?.events,
                cvReview: state.match.cvReview,
                cvClusters: state.match.cv?.identity?.clusters,
                // Both only exist to say what each player's video figures were
                // measured over. Without the window a short clip would score
                // every player against the whole match and read as a tracker
                // that lost everybody.
                cvWindow: state.match.cv?.window,
                matchEndS: state.match.stats.matchEndS,
                // The shots a coach said were headed. Without these the same
                // match would read one way on this page and another on the
                // player's, which is the worst kind of disagreement — neither
                // side can see the other to know there is one.
                cvShotRows: shotLedger(
                    state.match.cvEvents?.events || [], state.match.cvReview,
                ),
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
    byId('btn-print').addEventListener('click', () => window.print());
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
    // Redrawn as it is typed, not on save. The strip is there to catch a wrong
    // number, and catching it after the write is most of a round trip too late.
    byId('input-video-offset').addEventListener('input', renderClockMap);
    byId('input-second-half').addEventListener('input', renderClockMap);
    byId('btn-download-log').addEventListener('click', doDownloadLog);
    byId('btn-missed-here').addEventListener('click', useVideoPosition);
    byId('btn-cv-missed').addEventListener('click', doRecordMiss);
    byId('btn-cv-labels').addEventListener('click', doDownloadLabels);
    byId('btn-cv-sample').addEventListener('click', toggleSample);

    // Where the rail's highlight comes from. Passive because it never calls
    // preventDefault, and a scroll listener that does not say so makes the
    // browser wait for it before every frame of scrolling. Resize matters too:
    // crossing the breakpoint is what decides whether there is a rail at all.
    window.addEventListener('scroll', onRailScroll, { passive: true });
    window.addEventListener('resize', onRailScroll);

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
