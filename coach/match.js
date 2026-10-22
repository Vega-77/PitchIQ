// Opening a match: reading its documents and drawing every block of the
// view, the sample preview, and the status line that says whether the
// reports went out.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import {
    getMatch, listMatchRoster, listLog, aggregateMatch, readCvStats, readCvMapping, readCvEvents,
    readCvReview,
} from '../shared/db.js?v=154';
import { taggedCount, printStamp } from '../shared/report.js?v=154';
import { byId, setText, toast, plural } from '../shared/ui.js?v=154';
import { activeCv, download, show, state } from './shell.js?v=154';
import { renderReview } from './review.js?v=154';
import {
    renderMatchVideoBlock, updateVideoHint, renderClockMap, renderTimeline,
} from './footage.js?v=154';
import {
    renderTeamStats, matchRail, renderMatchRail, redrawCvNote, renderPlayerTable, mergeCvPlayers,
} from './match-overview.js?v=154';
import { renderShape, renderPassing, renderPressing, renderSubs } from './match-play.js?v=154';
import { renderShots, renderSampleToggle, redrawShotViews } from './match-shots.js?v=154';
import { renderClusterMapping, closeSameAs } from './mapping.js?v=154';
import { renderExcluded } from './excluded.js?v=154';

// ---------------------------------------------------------------- one match

export async function openMatch(matchId) {
    byId('loading').classList.remove('hidden');

    try {
        const [match, roster, log, cv, cvMapping, cvEvents, cvReview] =
            await readMatchDocs(matchId);

        const stats = aggregateMatch(log, roster);
        state.match = {
            ...match, stats, log, roster, cv, cvMapping, cvEvents, cvReview,
        };
        // Off on every open. A preview that survived navigating to another match
        // would show one match's invented numbers under another match's title.
        state.cvPreview = false;
        // Likewise the section being read: a coach deep in one game's review
        // who opens another should land where every match opens.
        matchRail?.reset();
        // Likewise: figure 3 of this match is not figure 3 of the last one.
        closeSameAs();

        // Merge the video's per-player figures onto the tagged ones, so the
        // table can be one table. Only for figures a coach has matched to a
        // player — anything else is a guess with a name attached.
        mergeCvPlayers(stats.players, state.match, stats.matchEndS);

        drawMatch({ match, matchId, stats, log, roster });

        byId('btn-publish').disabled = false;

        show('view-match');
        window.scrollTo(0, 0);
    } catch (err) {
        toast(err.message || 'Could not open that match.', true);
        show('view-main');
    }
}

/** Everything one match view reads, in the order `openMatch` unpacks it. */
function readMatchDocs(matchId) {
    return Promise.all([
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
}

/** Fill every block of the match view from a freshly opened match. */
function drawMatch({ match, matchId, stats, log, roster }) {
    setText('match-title', `vs ${match.opponentName || '—'}`);
    renderMatchStatus();

    setText('match-print-stamp', printStamp({
        subject: state.team?.name || null,
        matchLine: `vs ${match.opponentName || '—'}`
            + (match.date ? ` · ${match.date}` : ''),
        estimated: Boolean(activeCv()),
    }));

    // Em dashes, not zeroes, on a match nobody tagged. See `taggedCount`.
    setText('score-us', taggedCount(stats.counts.us.goal, log));
    setText('score-them', taggedCount(stats.counts.them.goal, log));

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
    // Same reasoning, and one more: this block is built from the roster's
    // stints and the tag log's clock, neither of which the review touches.
    renderSubs();
    // Reads the mapping and the roster's positions. Neither is something a
    // verdict in the review can move, so it lives on `redrawMappedViews`
    // and is drawn once from here.
    renderShape();

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
}

/** Flip the preview and redraw only the blocks it can reach. */
export function toggleSample() {
    state.cvPreview = !state.cvPreview;

    renderTeamStats(state.match.stats);
    redrawCvNote();

    renderShots();
    renderPassing();
    renderShape();
    renderPressing();
    renderSubs();
    renderExcluded();
    renderSampleToggle();
    // The preview turns whole sections on and off, so the rail has to be
    // rebuilt rather than merely re-counted.
    renderMatchRail();
}

/**
 * Hand the tagged log to the video pipeline, as a file.
 *
 * A file rather than the pipeline reading Firestore itself. The service account
 * pitchiq/publish_to_firestore.py uses bypasses every security rule, so the less it is allowed to
 * touch the better — it writes CV stats and nothing else, and giving it a read
 * path into a match would widen that for no gain. This runs as the coach, under
 * the rules, and the pipeline gets a plain list of events.
 *
 * The ids ride along so a file found later can say which match it came from.
 */
export function doDownloadLog() {
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

/**
 * The two things on this page that say whether the reports have gone out.
 *
 * Its own function because publishing has to redraw them and used to not.
 * `doPublish` refreshed the dashboard *behind* the match view and left the view
 * itself reading `finalized: false` — so after the most consequential thing a
 * coach does, the button still said "Publish player reports" and the subtitle
 * still omitted it. The only sign anything had happened was a toast that clears
 * itself in under three seconds, and a coach who came back to the tab a minute
 * later had no way to tell whether every student had their report.
 */
export function renderMatchStatus() {
    const match = state.match;
    if (!match) return;

    setText('match-sub',
        `${match.date || 'no date'} · ${(match.status || 'scheduled').replace(/_/g, ' ')}`
        + (match.finalized ? ' · reports published' : ''));

    // The match list and the players' reports show the score stored when the
    // reports went out; this page counts the tags as they are now. When a goal
    // was tagged or deleted since, the two disagree and neither page can see
    // the other, so this one says so, and says how to put it right.
    const stale = byId('match-stale');
    if (stale) {
        const us = match.stats?.counts.us.goal ?? 0;
        const them = match.stats?.counts.them.goal ?? 0;
        const differs = match.finalized && match.scoreUs != null
            && (match.scoreUs !== us || (match.scoreThem ?? 0) !== them);
        stale.classList.toggle('hidden', !differs);
        setText('match-stale-text', differs
            ? `Reports went out at ${match.scoreUs}–${match.scoreThem ?? 0}, but `
                + (match.log?.length
                    ? `the tags now come to ${us}–${them}.`
                    : 'nothing is tagged on this match now.')
                + ' Re-publish to bring the match list and the players’ reports '
                + 'into line.'
            : '');
    }

    const publish = byId('btn-publish');
    if (publish) {
        publish.textContent = match.finalized
            ? 'Re-publish player reports'
            : 'Publish player reports';
    }
}
