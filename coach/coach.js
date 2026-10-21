// The coach page, and its entry point.
//
// The page is split by what is on screen, and this file loads the pieces and
// wires their buttons up. From the bottom of the stack up: `shell.js` (state
// and the view switch), `review.js`, then the match view in `footage.js`,
// `match-overview.js`, `match-play.js`, `match-shots.js`, `mapping.js`,
// `excluded.js` and `match.js`, and the dashboard in `season.js`, `staff.js`,
// `matches.js`, `player-view.js`, `roster.js`, `team.js` and `publish.js`.
// Each imports only modules below it, which tests/test_browser_graph.py holds.

import { onUser, signOut, resolveAccess, configWarning } from '../shared/auth.js?v=147';
import { startMotion } from '../shared/motion.js?v=147';
import { byId, toast, localDate } from '../shared/ui.js?v=147';
import { show, state, initTabs } from './shell.js?v=147';
import {
    REVIEW_TYPES, doDownloadLabels, doRecordMiss, leaveReview, onReviewChange, useVideoPosition,
} from './review.js?v=147';
import { closeKit, toggleKit, doSaveKit, doInviteCoach } from './staff.js?v=147';
import { updateVideoHint, renderClockMap, leaveMatchVideo, doSaveVideo } from './footage.js?v=147';
import { renderMatchRail } from './match-overview.js?v=147';
import { redrawShotViews } from './match-shots.js?v=147';
import { toggleSample, doDownloadLog } from './match.js?v=147';
import { doCreateMatch } from './matches.js?v=147';
import { doAddPlayer } from './roster.js?v=147';
import {
    showCreateTeam, cancelCreateTeam, doCreateTeam, loadTeamData, switchTeam,
} from './team.js?v=147';
import { doPublish } from './publish.js?v=147';

// ---------------------------------------------------------------- init

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
    // review.js owns the verdicts; these two are how the rest of the page hears
    // about them. Set before any listener is wired, so no verdict can land
    // before there is somewhere for it to be reported.
    onReviewChange({ ledger: redrawShotViews, progress: renderMatchRail });

    const warning = configWarning();
    if (warning) byId('config-slot').append(warning);

    initTabs();

    byId('btn-signout').addEventListener('click', () =>
        signOut().then(() => { location.href = '../'; }));
    byId('btn-create-team').addEventListener('click', doCreateTeam);
    byId('btn-new-team').addEventListener('click', showCreateTeam);
    byId('btn-cancel-team').addEventListener('click', cancelCreateTeam);
    byId('btn-save-kit').addEventListener('click', doSaveKit);
    byId('btn-kit-open').addEventListener('click', toggleKit);
    byId('btn-kit-cancel').addEventListener('click', closeKit);
    byId('team-switch').addEventListener('change', (e) => switchTeam(e.target.value));
    byId('btn-invite-coach').addEventListener('click', doInviteCoach);
    byId('btn-add-player').addEventListener('click', doAddPlayer);
    byId('btn-create-match').addEventListener('click', doCreateMatch);
    byId('btn-print').addEventListener('click', () => window.print());
    // Paper cannot click a folded group open, so every one opens for print,
    // from the button or from the browser's own menu.
    window.addEventListener('beforeprint', () => {
        for (const group of document.querySelectorAll('details.stat-group')) {
            group.open = true;
        }
    });
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


    const missedType = byId('input-missed-type');
    for (const type of REVIEW_TYPES) {
        const option = document.createElement('option');
        option.value = type;
        option.textContent = type;
        missedType.append(option);
    }
    // The coach's own calendar day. `toISOString` is UTC, which in an American
    // evening is already tomorrow, and every match made after dinner was dated
    // a day late.
    byId('input-date').value = localDate();

    onUser((user) => {
        if (!user) { location.href = '../'; return; }
        onSignedIn(user).catch((err) => {
            toast(err.message || 'Could not load your team.', true);
            byId('loading').classList.add('hidden');
        });
    });
}

init();
startMotion();
