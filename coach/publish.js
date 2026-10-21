// Publishing a match's player reports.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import { listMatches, publishReports } from '../shared/db.js?v=147';
import { shotLedger } from '../shared/report.js?v=147';
import { byId, toast } from '../shared/ui.js?v=147';
import { state } from './shell.js?v=147';
import { renderHero } from './season.js?v=147';
import { renderMatchStatus } from './match.js?v=147';
import { renderMatches } from './matches.js?v=147';

export async function doPublish() {
    const button = byId('btn-publish');
    button.disabled = true;

    try {
        await publishReports({
            teamId: state.team.id,
            matchId: state.match.id,
            match: state.match,
            team: state.team,
            players: state.match.stats.players,
            score: {
                us: state.match.stats.counts.us.goal ?? 0,
                them: state.match.stats.counts.them.goal ?? 0,
            },
            // The log and roster go in so each report can carry that player's
            // own timeline. They are read here and never reach the player —
            // publishReports turns them into labels first.
            extra: {
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
        });
        toast('Player reports published');
        // The open match, not just the list behind it. `publishReports` sets
        // `finalized` and the score on the match document; without mirroring
        // that here the page a coach is actually looking at goes on saying the
        // reports have not been sent.
        state.match.finalized = true;
        state.match.scoreUs = state.match.stats.counts.us.goal ?? 0;
        state.match.scoreThem = state.match.stats.counts.them.goal ?? 0;
        renderMatchStatus();

        state.matches = await listMatches(state.team.id);
        renderHero();
        renderMatches();
    } catch (err) {
        toast(err.message || 'Could not publish reports.', true);
    } finally {
        button.disabled = false;
    }
}
