/**
 * What every block on the coach page shares.
 *
 * The page is three files: this one, `review.js` and `coach.js`. This one sits
 * at the bottom of that stack and draws nothing at all — deliberately, so
 * that importing it can never drag a page’s worth of rendering along behind
 * it. It holds the state the whole page reads, the view switch, and the two
 * helpers that would otherwise have to be duplicated or exported from whichever
 * renderer happened to define them first.
 */

import { xgTally, xgTrust, shotLedger } from '../assets/report.js?v=105';
import { sampleCvSummary } from '../assets/sample-report.js?v=105';
import { showOnly } from '../assets/ui.js?v=105';

export const VIEWS = ['view-noteam', 'view-main', 'view-match', 'view-player'];

export const state = {
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
    // Whose season the player view is showing. The rail beside it reads
    // from here rather than from a captured argument — see renderPlayerRail.
    openPlayer: null,
};

export const show = (view) => showOnly(view, VIEWS);

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
export const activeCv = () => (state.cvPreview ? sampleCvSummary() : state.match?.cv);

export const teamLabels = () => ({
    usName: state.team?.name || 'Us',
    themName: state.match?.opponentName || 'Them',
});

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
export function matchXgTally() {
    if (xgTrust(state.match?.cv?.calibrationErrorM) === 'none') return null;
    return xgTally(shotLedger(
        state.match?.cvEvents?.events || [], state.match?.cvReview,
    ));
}

/**
 * Hand the browser a file.
 *
 * Down here because two blocks of this page export JSON — the tag log and
 * the review labels — and each of them had a copy of this. Only one copy
 * carried the deferred revoke below, so one of the two exports was broken in
 * Safari and there was no way to tell that by reading either of them.
 */
export function download(filename, text) {
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
