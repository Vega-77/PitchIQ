// What a player is allowed to see of a match they played in.
//
// This module has no imports, which is deliberate. It decides which names and
// which events cross from the coach's record into a document a sixteen-year-old
// can read, and that decision should be testable on its own — not reachable
// only through a module that opens a Firestore connection at import time.
//
// The rule it enforces: a player sees everything they did, plus the goals.
// Goals get named because they are shouted across a pitch and announced to
// everyone present; that justification does not extend to anything else. No
// teammate's minutes, no teammate's fouls, and above all no teammate's cards —
// one minor's disciplinary record is not something to publish to another minor.
//
// Labels are built here rather than on the player's page. That is the
// mechanism, not a convenience: the portal never receives the roster, so the
// only name that can reach it is one this function chose to write.

// A player's report may carry at most this many timeline entries. A tagged
// match produces a few dozen; the cap exists so a runaway log cannot push a
// document towards Firestore's 1MB limit and fail the whole publish batch —
// which would block every player's report, not just one.
export const MAX_TIMELINE = 120;

// Touch timestamps kept per player. A full match is thousands; both the
// document size limit and the readability of a timeline strip run out long
// before that.
export const MAX_TOUCH_TIMES = 400;

// Counting stats add up across a player's clusters. Speed does not — a player
// who hit 31 km/h in one fragment did not hit 62 across two.
// The answer to "who is this tracked figure?" when the answer is "nobody" — a
// referee, a substitute warming up, somebody's dad on the touchline.
//
// It lives in `byCluster` alongside real player ids rather than in a field of
// its own because the cvMapping rules pin that document to exactly three keys
// (`firestore.rules`), and a fourth would need a rules change to store the same
// judgement. It is a real answer to the same question, not a missing one, which
// is why it is not just left blank: blank means "not looked at yet", and a
// coach who has ruled a figure out should not be asked again.
//
// Every reader has to skip it. There are two, and both are marked.
export const NOT_A_PLAYER = '__not_a_player';

const SUMMED = [
    'touches', 'passes_attempted', 'passes_completed', 'carries',
    'tackles', 'interceptions', 'recoveries', 'shots', 'goals', 'xg',
    'distance_m', 'sprint_count', 'sprint_distance_m', 'minutes_tracked',
];
const MAXED = ['top_speed_kmh'];

/**
 * Roll the video's tracked figures up into the players a coach named them as.
 *
 * A player can legitimately be several clusters. The tracker loses people when
 * they leave frame, and `cv/identity.py` only re-joins fragments separated by
 * a couple of seconds — anyone who went off and came back later stays split.
 * That is the safe failure: two clusters mapped to one player still sum
 * correctly, whereas a wrong automatic merge would credit one player with
 * another's work and could not be undone.
 *
 * So this is many-to-one on purpose, and the arithmetic has to respect what
 * each stat means. Touches add. Top speed does not.
 */
export function cvStatsByPlayer(tracks, byCluster) {
    const byId = new Map((tracks || []).map((t) => [String(t.cluster_id), t]));
    const out = {};

    for (const [clusterId, playerId] of Object.entries(byCluster || {})) {
        if (!playerId || playerId === NOT_A_PLAYER) continue;
        const track = byId.get(String(clusterId));
        if (!track) continue;

        const acc = out[playerId] ||= { clusters: [], touchTimes: [], shotMap: [] };
        acc.clusters.push(Number(clusterId));

        for (const key of SUMMED) {
            const value = track[key];
            if (value == null) continue;
            acc[key] = (acc[key] ?? 0) + value;
        }
        for (const key of MAXED) {
            const value = track[key];
            if (value == null) continue;
            acc[key] = Math.max(acc[key] ?? 0, value);
        }
        if (Array.isArray(track.touch_times_s)) acc.touchTimes.push(...track.touch_times_s);
        // Their own shots, so a publish can carry the coach's body-part
        // corrections into the player's report. Without these the coach's page
        // would show a corrected total and the player's would show the
        // uncorrected one for the same match.
        if (Array.isArray(track.shot_map)) acc.shotMap.push(...track.shot_map);
    }

    for (const acc of Object.values(out)) {
        // Clusters arrive in whatever order the mapping was written, so the
        // combined touch list has to be re-sorted before it means anything as
        // a timeline.
        acc.touchTimes.sort((a, b) => a - b);
        acc.shotMap.sort((a, b) => (a.video_s ?? 0) - (b.video_s ?? 0));
        acc.touchTimes = acc.touchTimes.slice(0, MAX_TOUCH_TIMES);
        acc.clusters.sort((a, b) => a - b);
        acc.passAccuracy = acc.passes_attempted
            ? acc.passes_completed / acc.passes_attempted
            : null;
    }
    return out;
}

// Everything a player's report can hold from the video, including the four
// fields `cv/publish.py` writes and this module does not. Used to blank them
// all when a player has no confirmed mapping.
//
// It has to be a list rather than "whatever this function returns", because the
// publish is a merge now: a key the payload leaves out is a key that keeps
// whatever it had. Before the merge, an unmapped player was cleaned up by the
// overwrite; the same write was also silently deleting the pipeline's heatmaps
// and shot maps on every re-publish, which is the bug that forced the merge.
/**
 * Total xG over a set of marks, or null when not one of them carries a figure.
 *
 * Null rather than zero, matching `shotSummary`: a run from before the model was
 * wired in has no expected goals, which is not the same as no chances. A header
 * the run cannot score is also null and drops out here, which is the point —
 * the player's total then leaves it out exactly as the coach's map does.
 */
function sumXg(marks) {
    const scored = (marks || []).filter((m) => m.xg != null);
    if (!scored.length) return null;
    return Math.round(scored.reduce((sum, m) => sum + m.xg, 0) * 1e4) / 1e4;
}

const CV_REPORT_KEYS = [
    'cvMinutesOnPitch', 'cvMinutesFilmed', 'cvTrackedShare', 'cvTouches',
    'cvPassesAttempted', 'cvPassesCompleted', 'cvCarries', 'cvTackles',
    'cvInterceptions', 'cvRecoveries', 'cvShots', 'cvXg', 'cvDistanceM',
    'cvTopSpeedKmh', 'cvSprintCount', 'cvMinutesTracked', 'cvTouchTimes',
    'cvClusterCount', 'cvShotMap',
    // Written only by the pipeline, cleared only here.
    'cvHeatmap', 'cvAttackingEnd', 'cvCalibrationErrorM',
];

/**
 * The `cv`-prefixed fields for one player's match report.
 *
 * `coverage` is optional and comes from the roster rather than the pipeline —
 * Python has no sub log and cannot compute it, which is why those fields have
 * no twin in `cv/publish.py::player_report_fields`. Without it the report is
 * exactly what it was before, with the totals unqualified.
 *
 * `shotRows` is the coach's shot ledger. It is what carries a body-part tag
 * through to the player: without it the coach's own page would show a corrected
 * total for a match and the player's page would show the uncorrected one.
 *
 * With no stats at all, every video field is explicitly nulled rather than
 * omitted. Omitting them was right while this was an overwrite and is wrong now
 * that it is a merge — a player whose cluster a coach un-mapped would otherwise
 * keep the numbers from before.
 */
export function cvReportFields(stats, coverage = null, shotRows = null) {
    if (!stats) return Object.fromEntries(CV_REPORT_KEYS.map((k) => [k, null]));
    const num = (v) => (v == null ? null : v);
    const marks = correctedShotMarks(stats.shotMap || [], shotRows);
    return {
        // How much of this player's own minutes the figures above rest on, so
        // the player's page can say it without re-reading the roster and the
        // window — neither of which a player is allowed to see.
        cvMinutesOnPitch: coverage?.onPitchS == null ? null : coverage.onPitchS / 60,
        cvMinutesFilmed: coverage?.watchedS == null ? null : coverage.watchedS / 60,
        cvTrackedShare: coverage?.share ?? null,
        cvTouches: num(stats.touches),
        cvPassesAttempted: num(stats.passes_attempted),
        cvPassesCompleted: num(stats.passes_completed),
        cvCarries: num(stats.carries),
        cvTackles: num(stats.tackles),
        cvInterceptions: num(stats.interceptions),
        cvRecoveries: num(stats.recoveries),
        cvShots: num(stats.shots),
        // Summed off the corrected marks rather than off the pipeline's own
        // per-track total, so the header tags reach it. Falls back to the
        // pipeline's figure when there are no marks to sum — a report from
        // before shot maps existed still has an xG.
        cvXg: marks.length ? sumXg(marks) : num(stats.xg),
        cvShotMap: marks,
        cvDistanceM: num(stats.distance_m),
        cvTopSpeedKmh: num(stats.top_speed_kmh),
        cvSprintCount: num(stats.sprint_count),
        cvMinutesTracked: num(stats.minutes_tracked),
        cvTouchTimes: stats.touchTimes || [],
        // How many tracked fragments this player was assembled from. Shown to
        // the coach, because a player stitched out of nine pieces is a weaker
        // claim than one tracked cleanly throughout.
        cvClusterCount: (stats.clusters || []).length,
    };
}

// ------------------------------------------------- who was on, and when
//
// A tracked figure appears between two moments in the video, and the roster
// knows who was on the pitch between two moments of the match. Those are the
// same question asked on two different clocks, and lining them up turns a
// picker offering all twenty players into one offering the four it could be.
//
// Nothing here filters. It ranks, and the picker groups by the ranking — see
// `clusterRow` in coach.js for why that distinction is load-bearing.

/** Everyone on the pitch at a given match-clock moment. */
export function onPitchAt(roster, clockS) {
    const out = new Set();
    for (const entry of roster || []) {
        for (const stint of entry.stints || []) {
            const end = stint.outS ?? Infinity;
            if (stint.inS <= clockS && clockS < end) {
                out.add(entry.id);
                break;
            }
        }
    }
    return out;
}

/**
 * How many seconds of a player's stints overlap a window.
 *
 * `matchEndS` closes an open stint. A stint with `outS === null` means the
 * player was still on when the log ended, not that they played forever.
 */
export function stintOverlapS(stints, startS, endS, matchEndS) {
    if (endS <= startS) return 0;
    let total = 0;
    for (const stint of stints || []) {
        const end = stint.outS ?? matchEndS;
        total += Math.max(0, Math.min(end, endS) - Math.max(stint.inS, startS));
    }
    return total;
}

/**
 * Rank a roster by how well each player's time on the pitch fits a cluster's.
 *
 * The cluster's first and last sightings are in video time and the stints are
 * in match clock, related by the offset the coach typed in beside the video
 * link: `videoS = clockS + offsetS`, per `videoTime` in video.js.
 *
 * Returned in full and sorted, never filtered. If that offset is wrong — and it
 * is the single most fiddly number in the app — then every overlap here is
 * wrong too, and a picker that had hidden the non-overlapping players would
 * have hidden the right answer.
 */
export function rankRosterForCluster(roster, cluster, options = {}) {
    const { videoOffsetS = 0, matchEndS = 0 } = options;
    const startS = (cluster?.first_seen_s ?? 0) - videoOffsetS;
    const endS = (cluster?.last_seen_s ?? 0) - videoOffsetS;
    const span = Math.max(0, endS - startS);

    return (roster || [])
        .map((entry) => {
            const overlapS = stintOverlapS(entry.stints, startS, endS, matchEndS);
            return {
                entry,
                overlapS,
                // What fraction of the time this figure was on screen the
                // player was actually playing. The useful number for a human:
                // "on for all of it" beats "on for 40 seconds" when the figure
                // was only tracked for 40 seconds.
                overlapShare: span > 0 ? overlapS / span : 0,
            };
        })
        .sort((a, b) => (
            b.overlapS - a.overlapS
            || (a.entry.jerseyNumber ?? 999) - (b.entry.jerseyNumber ?? 999)
        ));
}

// --------------------------------------------- how much of a match was watched
//
// A player's card puts "Minutes 71" next to "km covered 1.9". Those are two
// different denominators printed as one row. The first is the sub log — the
// whole time they were on the pitch. The second is the video: however much of
// that the tracker kept hold of, inside whatever window of footage was actually
// processed. At 3.4 tracks per player the gap between them is not small, and a
// coach ranking a roster by the second column is mostly ranking who the tracker
// happened to follow.
//
// So every per-player figure from the video carries the share of that player's
// own minutes it was measured over. Nothing here scales a figure up to what it
// would have been: a total measured over a third of someone's match is a third
// of a total, and tripling it would be inventing the other two thirds. The
// comparable figure is a rate, and it is offered as its own number.

/**
 * Below this share of a player's minutes, a video total is a sample, not a total.
 *
 * Set where the arithmetic stops being a rounding difference and starts being a
 * different claim: at 0.8 a distance reads a fifth low, which is wrong but still
 * recognisably the player's match. At 0.5 it is half their match with the label
 * of a whole one.
 */
export const TRACKED_SHARE_FLOOR = 0.7;

/**
 * The stretch of footage that was processed, in match-clock seconds.
 *
 * `window` is in video seconds, because it is a pair of seek positions in a
 * file. Stints are in match clock, because that is what a tablet on the
 * touchline records. The offset the coach typed in beside the video link is the
 * only thing relating them — `videoS = clockS + offsetS`, per `videoTime` in
 * video.js — and getting this backwards would score a substitute against the
 * warm-up.
 *
 * An absent bound means the pipeline ran to that edge of the file, so it is
 * answered from the match rather than guessed at: kick-off for a missing start,
 * and the last thing the log recorded for a missing end. With neither the window
 * nor a match end there is nothing to answer from, and this returns null —
 * which is a different thing from a window of zero length.
 */
export function windowClockRange(window, options = {}) {
    const { videoOffsetS = 0, matchEndS = null } = options;
    const startVideoS = window?.start_s ?? window?.startS ?? null;
    const endVideoS = window?.end_s ?? window?.endS ?? null;

    const endS = endVideoS == null ? matchEndS : endVideoS - videoOffsetS;
    if (endS == null) return null;

    const startS = startVideoS == null ? 0 : startVideoS - videoOffsetS;
    return { startS: Math.max(0, startS), endS };
}

/**
 * How one player's video figures relate to the match they actually played.
 *
 * Three different spans, and the difference between them is the whole point:
 *
 *   - `onPitchS` — what the sub log says they played.
 *   - `watchedS` — how much of that fell inside the processed footage. On a
 *     three-minute clip this is three minutes for everyone, which is why the
 *     window has to be intersected rather than assumed away: without it every
 *     player on a short clip would show a coverage of about 3%, and the figure
 *     would be a statement about the clip rather than about them.
 *   - `trackedS` — how much of *that* the tracker held on to. The shortfall is
 *     fragmentation, and it is the number nobody has been able to see per
 *     player until now.
 *
 * Every field is null rather than zero when it cannot be answered. A player
 * whose stints were never recorded did not play no minutes.
 */
export function trackedCoverage(minutesTracked, stints, context = {}) {
    const { window, videoOffsetS = 0, matchEndS = null } = context;

    const trackedS = minutesTracked == null ? null : minutesTracked * 60;
    const known = Array.isArray(stints) && stints.length > 0 && matchEndS != null;
    const range = windowClockRange(window, { videoOffsetS, matchEndS });

    const onPitchS = known ? stintOverlapS(stints, 0, matchEndS, matchEndS) : null;
    const watchedS = known && range
        ? stintOverlapS(stints, range.startS, range.endS, matchEndS)
        : null;

    // `watchedS` of zero is falsy on purpose. A player who was not on the pitch
    // for any of the processed footage has no share — not a share of nothing.
    const share = trackedS != null && watchedS ? trackedS / watchedS : null;
    return { trackedS, onPitchS, watchedS, share };
}

/**
 * Metres per minute of the time the video actually held a player.
 *
 * The one movement figure that survives fragmentation. A total is a fraction of
 * the truth when the tracker only had someone for half of their match; a rate
 * over the half it did have is still a rate. It is also what makes a substitute
 * who played twenty minutes comparable to a starter who played eighty, which a
 * column of kilometres never was.
 *
 * Not unbiased, and worth saying so: the tracker loses people in the congested,
 * fast passages where they are working hardest, so this reads low rather than
 * randomly. Low and comparable beats a total that is neither.
 */
export function metresPerMinute(distanceM, minutesTracked) {
    if (distanceM == null || !minutesTracked) return null;
    return distanceM / minutesTracked;
}

/**
 * One sentence on how much of a player's match the video was able to measure.
 *
 * The fraction is against `watchedS`, not against the minutes they played, and
 * the difference matters on any run short of a full match: a three-minute clip
 * of a player who was on for seventy would otherwise report 4% and read as a
 * tracker that lost them, when in fact nobody filmed the other sixty-seven
 * minutes. Filmed and played are named separately whenever they differ, because
 * they are two different shortfalls and only one of them is the software's.
 *
 * Null when there is nothing to say — no sub log, no window, no confirmed
 * cluster — since a sentence hedging about a number that is not on screen is
 * noise, and this sits under figures that are often all a player has.
 */
export function coverageNote(coverage, options = {}) {
    const { second = false } = options;
    const { trackedS, onPitchS, watchedS, share } = coverage || {};
    if (share == null || trackedS == null) return null;

    const they = second ? 'you' : 'they';
    const mins = (seconds) => Math.round(seconds / 60);

    // Above one means two figures mapped to this player were on screen at once,
    // which one player cannot be — so it is evidence the mapping double-counts,
    // and every total resting on it is inflated. Said outright rather than
    // clamped to a tidy 100%, which would hide the only symptom.
    if (share > 1.15) {
        return `Two of the tracked figures matched to ${second ? 'you' : 'this player'}`
            + ' were on screen at the same time, so these totals count part of'
            + ' the match twice.';
    }

    const tracked = mins(trackedS);
    const played = onPitchS == null ? null : mins(onPitchS);
    const filmed = mins(watchedS);

    // Within a minute they are the same span, and saying it twice would invite
    // the reader to look for a distinction that is not there.
    const partial = played != null && Math.abs(played - filmed) >= 1;
    const head = partial
        ? `The video covered ${filmed} of the ${played} minutes ${they} played,`
            + ` and measured ${tracked} of those`
        : `The video measured ${tracked}`
            + (played == null ? ' minutes' : ` of the ${played} minutes ${they} played`);

    if (share >= TRACKED_SHARE_FLOOR) return `${head}.`;
    return `${head} — so the totals here are part of the match rather than all`
        + ' of it, and the rate is the fairer comparison.';
}

// ------------------------------------------------- what the figures rest on
//
// Both the coach's match view and the half-time view print a line under the
// video-derived numbers saying what limited them. They used to build that line
// separately from two of the available figures, which is how the two pages
// drifted apart and how four new quality fields ended up reaching neither.
// Built here instead, with no imports, so the wording is one thing and can be
// tested against a quality block directly.

/**
 * Whether the pipeline was told when the ball was out of play.
 *
 * `live_share` is null — not zero — when no tagged log reached the run, and
 * that difference changes what the possession figure beside it *means*. With a
 * log, dead time is out of the denominator and the share is a share of
 * football. Without one, a player standing over the ball waiting to take a
 * throw-in is a very clear holder, and every second of that counts as
 * possession. Same arithmetic, two different claims, so the label has to know
 * which one it is making.
 */
export function possessionIsInPlay(quality) {
    return (quality?.live_share ?? quality?.liveShare) != null;
}

/** Seconds as "4m 20s", or "20s" under a minute. For prose, not for a table. */
export function roughDuration(seconds) {
    const total = Math.round(seconds || 0);
    if (total < 60) return `${total}s`;
    const mins = Math.floor(total / 60);
    const rest = total % 60;
    return rest ? `${mins}m ${rest}s` : `${mins}m`;
}

/**
 * How much to trust a figure measured in metres.
 *
 * Everything else on a match page is graded by `cvConfidence` on ball coverage
 * and touch confidence, because everything else is built from finding the ball.
 * Team shape is not: it is the players' own positions run through the
 * homography, so it stands or falls on the calibration and grading it on ball
 * coverage would answer a question nobody asked about it.
 *
 * The bands are the calibrate page's own, not new ones — `renderQuality` there
 * calls a fit good at 0.5m mean error, and a second standard for the same
 * number would mean a coach could be told the fit is good on one page and
 * doubted on another.
 */
export function shapeConfidence(calibrationErrorM) {
    if (calibrationErrorM == null) return 'low';
    if (calibrationErrorM <= 0.5) return 'high';
    if (calibrationErrorM <= 1.5) return 'medium';
    return 'low';
}

// Metres of mean calibration error at which per-shot xG, and then xG at all,
// stop being worth printing. Both come off the measured noise table in
// tests/test_xg_noise.py rather than from taste.
//
// The per-shot limit tightened from 1.0m to 0.5m on 2026-08-06, when the model
// was retrained without `shot_height`. Dropping a feature made the rest carry
// more of the answer, so position error moves the result further: a single
// shot's 95th-percentile shift is half the quantity at 0.5m and 89% of it at
// 1.0m. A number whose error bar is nearly as wide as itself is not a number.
const XG_PER_SHOT_LIMIT_M = 0.5;
const XG_TOTAL_LIMIT_M = 4.0;

/**
 * How much of the xG on a run is worth showing: `'shot'`, `'total'` or `'none'`.
 *
 * The model was measured against deliberately noisy positions, and the answer
 * was not flattering. On a 0.188 baseline, half a metre of position error moves
 * a single shot by 0.030 on average and 0.095 at the 95th percentile — half the
 * quantity; at one metre the p95 shift is 89% of it and at two metres it is
 * **larger than the quantity itself**.
 *
 * Both bands are measured rather than chosen, and they are measured on
 * different things, because they are claims about different numbers:
 *
 *   - `'shot'` — up to **0.5m**, which is also the fit `calibrate/` calls good.
 *     Good enough for "that was a decent chance". Not good enough to rank two
 *     shots 0.1 apart, which is why the caveat stays.
 *   - `'total'` — up to **4m**. Per-shot errors are independent and mostly
 *     cancel: simulated over a half's six shots, the *total* lands within 8% of
 *     the truth at 0.5m, 12% at 1m, 18% at 2m and 26% at 4m, while a single
 *     shot at 1m is already carrying an error bar nearly as wide as itself.
 *   - `'none'` — beyond four metres, when even the total is not worth printing.
 *     Printing it with a warning attached would still leave a specific-looking
 *     figure on screen, which is what people remember.
 *
 * A null error is not a good error and is not treated as one — but it is also
 * not evidence of a bad fit, so it lands on `'total'`: the reading that stays
 * true across the whole band it might be in.
 */
export function xgTrust(calibrationErrorM) {
    if (calibrationErrorM == null) return 'total';
    if (calibrationErrorM <= XG_PER_SHOT_LIMIT_M) return 'shot';
    if (calibrationErrorM <= XG_TOTAL_LIMIT_M) return 'total';
    return 'none';
}

/**
 * What limited a video-derived run, worst news first, as plain sentences.
 *
 * Caveats and denominators only. Nothing here congratulates the run on what
 * went well — a coach reading this line is being told what not to trust, and
 * padding it with good news is how the bad news stops being read. The one line
 * that is not a complaint is the live share, and that is there because it says
 * what the possession figure above was divided by.
 */
export function cvQualityNotes(quality, options = {}) {
    const q = quality || {};
    const { calibrated = false } = options;
    const pct = (value) => `${Math.round(value * 100)}%`;
    const notes = [];

    // Seen, not "has a position for" — the rest were drawn in between
    // sightings, and calling a straight line "visible" would overstate what the
    // video actually showed. The seconds say the same thing as the percentage,
    // in the unit a coach can picture, so they travel together rather than as
    // two sentences that would read as two separate problems.
    const seen = q.ball_seen_share ?? q.ballSeenShare;
    const noBall = q.no_ball_s ?? q.noBallS;
    if (seen != null) {
        notes.push(`the ball was visible in ${pct(seen)} of frames`
            + (noBall ? ` — ${roughDuration(noBall)} of the clip with none in sight` : ''));
    } else if (noBall) {
        notes.push(`${roughDuration(noBall)} of the clip with no ball in sight`);
    }

    if (!calibrated) notes.push('no pitch calibration, so nothing is in metres');

    // Absent is not zero, again. No log means stoppages are still being counted
    // as football, and saying so is the difference between a coach reading the
    // possession split as a fact and reading it as an estimate with a known
    // bias in a known direction.
    const live = q.live_share ?? q.liveShare;
    const stoppages = q.stoppages ?? null;
    if (live == null) {
        notes.push('no tagged log reached this run, so stoppages still count as play');
    } else {
        notes.push(`the tagged log puts ${pct(live)} of it in play`
            + (stoppages ? ` across ${stoppages} stoppage${stoppages === 1 ? '' : 's'}` : ''));
    }

    // Two records of the same match, and how often they told the same story.
    // Not an accuracy: both can be wrong about the same moment in the same
    // direction, and this would call that agreement. What it is good for is the
    // trend — a run of matches where the two drift apart means something
    // changed, and it is worth knowing which before believing the rest.
    const rec = options.reconciliation;
    const goalRate = rec?.goal_agreement ?? rec?.goalAgreement;
    const goals = rec?.goals || {};
    const compared = (goals.agreed || 0) + (goals.cv_only || 0) + (goals.tag_only || 0);
    if (goalRate != null && compared) {
        notes.push(`the video and the tagged log agree on ${goals.agreed || 0} `
            + `of ${compared} goal${compared === 1 ? '' : 's'}`);
    }

    // Carried in the counts above, not removed from them. Without a
    // calibration there is no goalmouth to measure anyone against, so a referee
    // and a goalkeeper are identical on every feature the classifier has, and
    // it keeps both rather than risk deleting a player. That is the right
    // trade, and it is still a caveat: somebody who is not playing may be
    // inside these numbers.
    const officials = q.flagged_officials ?? q.flaggedOfficials;
    if (officials) {
        notes.push(`${officials} figure${officials === 1 ? '' : 's'} matching `
            + 'neither kit still counted — a referee, or your goalkeeper');
    }

    // Only once there is an xG figure on screen to caveat. Both facts below are
    // biases with a known direction, which is worth more to a coach than a
    // vague warning: headers are scored generously, and a loose calibration
    // widens every shot's number without moving the total much.
    //
    // The 0.5m figure is not a guess. Measured against the real model
    // (tests/test_xg_noise.py): half a metre of position error moves one shot's
    // xG by ~0.030 on a 0.188 baseline, and by 2m the spread exceeds the number
    // itself.
    if (options.shots) {
        const error = options.calibrationErrorM;
        const trust = xgTrust(error);
        const at = error == null ? '' : ` at ${error.toFixed(1)}m of calibration error`;

        if (trust === 'none') {
            notes.push(`xG is not shown${at} — the model moves further than the `
                + 'number is worth when the positions are that loose');
        } else {
            // Named as an assumption with an owner rather than a flat
            // limitation, now that there is something to be done about it. The
            // count is what stops the caveat reading as unfixed on a match
            // where it has been fixed.
            const tagged = options.headersTagged || 0;
            notes.push('xG counts every shot as struck with the foot'
                + (tagged
                    ? ` except the ${tagged} you tagged as `
                        + `${tagged === 1 ? 'a header' : 'headers'}`
                    : ' until somebody says otherwise')
                + ' — one camera cannot see the ball\'s height'
                + (trust === 'total'
                    ? `, and${at} only the total is shown: each shot on its own is `
                        + 'looser than the differences a shot map would draw'
                    : ''));
        }
    }

    const perCluster = q.tracks_per_cluster ?? q.tracksPerCluster;
    if (perCluster > 2) {
        notes.push(
            `tracking broke each player into about ${Math.round(perCluster)} pieces`,
        );
    }

    return notes;
}

// --------------------------------------------------------- stats, by kind
//
// The coach's match view was one grid of twenty-five boxes in the order they
// happened to be written, tagged counts and video-derived figures interleaved,
// and the half-time page was a flat list built separately from the same
// document. Both grew a row at a time and neither was ever laid out.
//
// A coach does not read twenty-five numbers. They ask a question — did we keep
// the ball, did we pass it forward, did we make chances, did we defend — and
// look for the two or three numbers that answer it. Grouping is what makes that
// possible without reading every box, and it is the only reason several
// published figures below could be added at all: territory, giveaways by third
// and the passing breakdowns have been in the document since they were
// computed, and there was nowhere to put them that would not have made the pile
// worse.
//
// Both pages group by this list rather than each keeping their own, because the
// interesting failure is not a missing heading — it is the two pages quietly
// disagreeing about whether a switch of play is passing or attacking, in a
// project whose whole premise is that the half-time page and the full report
// describe the same match.

export const STAT_TYPES = [
    { id: 'match', title: 'The match' },
    {
        id: 'possession',
        title: 'Possession',
        note: 'Thirds are shares of your own time on the ball, not of the match.',
    },
    { id: 'passing', title: 'Passing' },
    { id: 'attacking', title: 'Attacking' },
    { id: 'defending', title: 'Defending' },
    {
        id: 'shape',
        title: 'Shape',
        note: 'Averaged across the run, and only as good as the calibration.',
    },
];

/**
 * Rows into `[{ id, title, note, rows }]`, in the order above, empties dropped.
 *
 * A group's note is only kept when a row that needs it survived. Both notes
 * above explain a denominator that some rows have and others do not — the
 * thirds, and the shape figures — and a caption explaining a denominator for
 * figures that are not on screen is worse than no caption, because a reader
 * will attach it to whatever is.
 *
 * A row with an unrecognised type is kept, in a group of its own at the end.
 * Dropping it would be the worse failure by a distance: a typo in a type name
 * would silently delete a measured number from a coach's screen and leave the
 * page looking complete. This way it is visibly wrong, and
 * `every row carries a type this module knows` catches it in the test suite
 * before anyone sees it.
 */
export function groupStats(rows) {
    const known = new Map(STAT_TYPES.map((type) => [type.id, { ...type, rows: [] }]));
    const extra = new Map();

    for (const row of rows || []) {
        // Absent is not zero, one more time. A null here is a figure the
        // pipeline could not measure — usually for want of a calibration — and
        // a box reading 0 would say it looked and found none.
        if (row == null || row.value == null) continue;
        const group = known.get(row.type)
            || extra.get(row.type)
            || extra.set(row.type, { id: row.type, title: row.type, rows: [] })
                .get(row.type);
        group.rows.push(row);
    }

    return [...known.values(), ...extra.values()]
        .filter((group) => group.rows.length)
        .map((group) => (
            group.note && !group.rows.some((row) => row.explained)
                ? { ...group, note: '' }
                : group
        ));
}

const share = (value) => (value == null ? null : `${Math.round(value * 100)}%`);

/**
 * The video-derived figures for the coach's own side, typed and labelled.
 *
 * `confidence` carries the marks the caller worked out from the quality block —
 * passed in rather than computed here because grading them lives in db.js,
 * which this module cannot import and stay testable.
 *
 * Reads `teams.team_a` only, which is always the coach's own side.
 */
export function teamStatRows(cv, confidence = {}) {
    const ours = cv?.teams?.team_a;
    if (!ours) return [];

    const quality = cv.quality || {};
    const events = confidence.events || null;
    const territory = ours.territory || {};
    const attempted = ours.passes_attempted || 0;
    const byLength = ours.passes_by_length || {};
    const byDirection = ours.passes_by_direction || {};
    const lost = ours.turnovers_by_third || {};

    // A breakdown is only worth a percentage if it is a share of something
    // this run actually counted. Without the total these are bare counts with
    // no denominator, and a bare 142 says nothing about how direct a side was.
    const ofAttempted = (count) =>
        (attempted && count != null ? count / attempted : null);

    return [
        {
            type: 'possession',
            // The label carries the denominator, because the denominator
            // changed. With a tagged log the dead time is out of it and this is
            // possession of a ball that was in play; without one it is the
            // older, weaker figure and must not claim otherwise.
            label: possessionIsInPlay(quality) ? 'Possession, ball in play' : 'Possession',
            value: share(ours.possession_pct),
            confidence: confidence.possession || null,
        },
        { type: 'possession', label: 'Touches', value: ours.touches, confidence: events },
        { type: 'possession', label: 'Carries', value: ours.carries, confidence: events },
        {
            type: 'possession', label: 'In your own third', explained: true,
            value: share(territory.defensive), confidence: confidence.possession || null,
        },
        {
            type: 'possession', label: 'In the middle third', explained: true,
            value: share(territory.middle), confidence: confidence.possession || null,
        },
        {
            type: 'possession', label: 'In their third', explained: true,
            value: share(territory.attacking), confidence: confidence.possession || null,
        },

        { type: 'passing', label: 'Passes attempted', value: attempted || null, confidence: events },
        {
            type: 'passing', label: 'Pass accuracy',
            value: share(ours.pass_accuracy), confidence: events,
        },
        {
            type: 'passing', label: 'Progressive passes',
            value: ours.progressive_passes, confidence: events,
        },
        // How direct a side was, which is the question the buckets exist to
        // answer and which the raw counts do not. Both are shares of what they
        // attempted, so a side that passed less does not look less direct.
        {
            type: 'passing', label: 'Played forward',
            value: share(ofAttempted(byDirection.forward)), confidence: events,
        },
        {
            type: 'passing', label: 'Played long',
            value: share(ofAttempted(byLength.long)), confidence: events,
        },
        { type: 'passing', label: 'Switches of play', value: ours.switches, confidence: events },

        {
            type: 'attacking', label: 'Final-third entries',
            value: ours.final_third_entries, confidence: events,
        },
        {
            type: 'attacking', label: 'Entries into the box',
            value: ours.box_entries, confidence: events,
        },
        { type: 'attacking', label: 'Crosses', value: ours.crosses, confidence: events },
        { type: 'attacking', label: 'Shots', value: ours.shots, confidence: events },
        {
            type: 'attacking', label: 'Shots on target',
            value: ours.shots_on_target, confidence: events,
        },
        // Withheld, not zeroed, when the calibration is too loose to support it.
        // A team total averages a lot of per-shot noise away, which is why it
        // survives a band that per-shot xG does not — but not every band.
        {
            type: 'attacking', label: 'Expected goals',
            value: (ours.xg == null || xgTrust(cv.calibrationErrorM) === 'none')
                ? null : ours.xg.toFixed(2),
            confidence: events,
        },

        { type: 'defending', label: 'Tackles', value: ours.tackles, confidence: events },
        {
            type: 'defending', label: 'Interceptions',
            value: ours.interceptions, confidence: events,
        },
        { type: 'defending', label: 'Recoveries', value: ours.recoveries, confidence: events },
        { type: 'defending', label: 'Ground duels', value: ours.duels, confidence: events },
        {
            type: 'defending', label: 'PPDA',
            value: ours.ppda == null ? null : ours.ppda.toFixed(1), confidence: events,
        },
        // The giveaways that turn straight into a chance against you. A single
        // turnover count cannot say this, which is why it is counted by third.
        {
            type: 'defending', label: 'Lost in your own third',
            value: lost.defensive ?? null, confidence: events,
        },

        ...shapeStatRows(ours.shape, cv.calibrationErrorM),
    ];
}

/**
 * How spread out we played, in metres — ours only.
 *
 * `report_json` used to publish one shape built from every track on the pitch,
 * both teams and the referee together, and label it Team A's. It is now built
 * per team, and this reads the team's own.
 *
 * Empty until a calibration exists, which is every run today — width in metres
 * is not something a pixel can answer.
 */
export function shapeStatRows(shape, calibrationErrorM) {
    if (!shape || shape.width_m == null) return [];
    const band = shapeConfidence(calibrationErrorM);
    const metres = (value) => (value == null ? null : `${Math.round(value)}m`);

    return [
        {
            type: 'shape', label: 'Average width', explained: true,
            value: metres(shape.width_m), confidence: band,
        },
        {
            type: 'shape', label: 'Average depth', explained: true,
            value: metres(shape.depth_m), confidence: band,
        },
        // Mean distance from each player to the team's own centre. Deliberately
        // not coloured good or bad: a compact side is well-drilled or it is
        // pinned in its own half, and this number cannot tell the difference.
        {
            type: 'shape', label: 'Compactness', explained: true,
            value: metres(shape.compactness_m), confidence: band,
        },
    ];
}

const PERIOD_TEXT = {
    kickoff_1st: 'Kick-off',
    halftime: 'Half-time',
    kickoff_2nd: 'Second half',
    full_time: 'Full time',
};

const MINE_TEXT = {
    foul: 'You conceded a foul',
    offside: 'You were caught offside',
};

/**
 * The moments from a match that belong to `playerId`, plus the goals.
 *
 * `log` and `roster` are the coach's full record. Nothing from either reaches
 * the caller except through the labels built below.
 */
export function playerTimeline(log, roster, playerId) {
    const nameById = new Map((roster || []).map((r) => [r.id, r.playerName]));
    const entries = [];

    for (const e of log || []) {
        const mine = e.playerId === playerId
            || e.assistPlayerId === playerId
            || e.subInId === playerId
            || e.subOutId === playerId;

        if (e.kind === 'period') {
            entries.push({
                clockS: e.matchClockS, type: e.type, mine: false,
                label: PERIOD_TEXT[e.type] || e.type,
            });
            continue;
        }

        if (e.kind === 'sub') {
            if (!mine) continue;
            entries.push({
                clockS: e.matchClockS, type: 'sub', mine: true,
                label: e.subInId === playerId ? 'Came on' : 'Came off',
            });
            continue;
        }

        if (e.kind !== 'event') continue;

        if (e.type === 'goal') {
            const ours = e.side !== 'them';
            const scorer = nameById.get(e.playerId);
            let label;
            if (e.playerId === playerId) label = 'You scored';
            else if (e.assistPlayerId === playerId) label = `You assisted ${scorer || 'the goal'}`;
            else if (!ours) label = 'Goal conceded';
            else label = scorer ? `${scorer} scored` : 'Goal';
            entries.push({ clockS: e.matchClockS, type: 'goal', mine, label });
            continue;
        }

        // Everything else belongs to whoever it happened to, and is shown only
        // to them. This `continue` is the privacy boundary.
        if (!mine) continue;

        const label = e.type === 'card'
            ? `You were booked${e.cardColor === 'red' ? ' — red' : ''}`
            : MINE_TEXT[e.type] || `You: ${e.type.replace(/_/g, ' ')}`;
        entries.push({ clockS: e.matchClockS, type: e.type, mine: true, label });
    }

    return entries
        .sort((a, b) => a.clockS - b.clockS)
        .slice(0, MAX_TIMELINE);
}

// ------------------------------------------------------ scoring the reviewer
//
// The review tool has been collecting verdicts since it shipped and computing
// nothing from them beyond "84 of 512 checked". The two numbers it exists to
// produce are precision and recall, and they need different halves of the data:
// precision comes from judging what the pipeline claimed, recall only from
// recording what it never claimed at all. A detector that finds six passes a
// half and gets all six right has perfect precision and is useless.
//
// The awkward case is an edit. A reviewer who changes a "tackle" to an
// "interception" has said two things at once: the pipeline was wrong to call it
// a tackle, and it was right that *something* happened there. Both matter, and
// collapsing them either way produces a flattering number. So an edit that
// changes the type counts against the type it claimed and in favour of the type
// it should have been.

const CONFIRMED_STATUS = 'confirmed';
const REJECTED_STATUS = 'rejected';
const EDITED_STATUS = 'edited';

function emptyScore() {
    return {
        truePositives: 0,   // claimed this type, and it was
        falsePositives: 0,  // claimed this type, and it was not
        detected: 0,        // really this type, and the moment was found
        missed: 0,          // really this type, and nothing was found
        unreviewed: 0,
        precision: null,
        recall: null,
    };
}

function ratio(numerator, denominator) {
    // Null, not zero. Zero is a measurement; this is the absence of one, and a
    // scorecard reading 0% for a type nobody has looked at would be read as a
    // detector that gets everything wrong.
    return denominator ? numerator / denominator : null;
}

/**
 * Precision and recall per event type, from a coach's review decisions.
 *
 * `events` is `cvStats/events`'s list, `review` is the `cvReview/decisions`
 * document: `{ byEvent: {id: {status, type?, playerId?}}, missed: [{clockS,
 * type}] }`.
 *
 * Everything here describes **the events actually reviewed**, and the caller
 * must say so on screen. Precision over twelve of five hundred events is a fact
 * about those twelve, and a reviewer who checked the twelve most obvious ones
 * has not measured the detector.
 */
export function reviewScore(events, review) {
    const byEvent = review?.byEvent || {};
    const byType = {};
    const at = (type) => (byType[type] = byType[type] || emptyScore());

    for (const event of events || []) {
        const claimed = event.type;
        const decision = byEvent[event.id];

        // A verdict, specifically. The same map also holds what a coach said a
        // shot *did*, and an entry carrying only that has said nothing about
        // whether the pipeline was right to call it a shot — counting it as a
        // confirmation would let the xG log quietly inflate this scorecard.
        if (!decision?.status) {
            at(claimed).unreviewed += 1;
            continue;
        }

        // An edit that only reassigns the player leaves the type standing, and
        // is a success for the type — the pipeline found the right kind of
        // thing and pinned it on the wrong person. Identity is a separate
        // problem with its own separate fix.
        const truth = decision.status === EDITED_STATUS && decision.type
            ? decision.type
            : claimed;

        if (decision.status === REJECTED_STATUS) {
            at(claimed).falsePositives += 1;
            continue;
        }

        if (truth === claimed) {
            at(claimed).truePositives += 1;
            at(claimed).detected += 1;
        } else {
            at(claimed).falsePositives += 1;
            // Found, but called something else. Still found, which is the only
            // question recall asks.
            at(truth).detected += 1;
        }
    }

    for (const miss of review?.missed || []) {
        if (miss?.type) at(miss.type).missed += 1;
    }

    for (const score of Object.values(byType)) {
        score.precision = ratio(
            score.truePositives, score.truePositives + score.falsePositives,
        );
        score.recall = ratio(score.detected, score.detected + score.missed);
    }

    const overall = emptyScore();
    for (const score of Object.values(byType)) {
        for (const key of [
            'truePositives', 'falsePositives', 'detected', 'missed', 'unreviewed',
        ]) {
            overall[key] += score[key];
        }
    }
    overall.precision = ratio(
        overall.truePositives, overall.truePositives + overall.falsePositives,
    );
    overall.recall = ratio(overall.detected, overall.detected + overall.missed);

    return { byType, overall };
}

/**
 * The reviewed set as a file, for tuning a detector on later.
 *
 * This is the reason the review tool is worth using on footage whose numbers
 * mean nothing yet: it produces labelled data as a side effect, and labelled
 * data is what a fine-tune needs. Until now those labels could only be read
 * back out of the Firestore console one document at a time.
 *
 * `meta` carries whatever says which match this is. A labels file that cannot
 * say what it belongs to is worthless in a month, which is exactly when someone
 * will open it.
 *
 * The two halves are on **different clocks**, and the field names say so rather
 * than leaving it to be discovered. A pipeline event is stamped in video
 * seconds; a recorded miss is typed by a human off the match clock. They are
 * related by `videoOffsetS`, which is why it travels in the file — converting
 * here would bake in whatever the offset happened to be at export time, and the
 * offset is the number in this app most likely to be corrected later.
 */
export function reviewLabels(events, review, meta = {}) {
    const byEvent = review?.byEvent || {};

    return {
        format: 'pitchiq-review-labels',
        version: 1,
        exportedAt: new Date().toISOString(),
        ...meta,
        // Stated rather than implied. A consumer that treats the unreviewed
        // events as negatives would be training on the pipeline's own guesses.
        note: 'Only events a human touched are labelled. Anything absent from '
            + '`labelled` was never looked at, and is not a negative example. '
            + 'labelled[].videoS is a position in the video; missed[].clockS is '
            + 'a match-clock reading a person typed. videoS = clockS + '
            + 'videoOffsetS. labelled[].result is what a coach said a shot did, '
            + 'and labelled[].xg is what the model predicted before anyone '
            + 'looked — the pair is the only ground truth this system produces.',
        labelled: (events || [])
            // Either half counts as a label. A shot marked "saved" with no
            // verdict beside it is still a human statement about that moment,
            // and it is the only statement in here a finishing model could
            // ever be trained on.
            .filter((event) => byEvent[event.id]?.status
                || byEvent[event.id]?.result || byEvent[event.id]?.header)
            .map((event) => {
                const decision = byEvent[event.id];
                return {
                    id: event.id,
                    videoS: event.timestampS,
                    claimedType: event.type,
                    verdict: decision.status ?? null,
                    actualType: decision.status === REJECTED_STATUS
                        ? null
                        : (decision.type || event.type),
                    // What the shot did, when somebody said. Null on everything
                    // that is not a shot, and on shots nobody has marked.
                    result: decision.result ?? null,
                    // Whether it was headed. The pipeline cannot see this at
                    // all, so every true here is a label that exists nowhere
                    // else — and body part is exactly what a pose model would
                    // need to be trained on.
                    header: decision.header === true,
                    xg: event.xg ?? null,
                    xgHeader: event.xgHeader ?? null,
                    playerId: decision.playerId ?? null,
                    trackId: event.trackId ?? null,
                    confidence: event.confidence ?? null,
                    inPlay: event.inPlay ?? null,
                };
            }),
        // The other half, and the one nothing else in the system can supply: a
        // thing the pipeline never saw leaves no record to disagree with.
        missed: (review?.missed || []).map((miss) => ({
            clockS: miss.clockS,
            type: miss.type,
            playerId: miss.playerId ?? null,
        })),
        counts: {
            events: (events || []).length,
            labelled: Object.keys(byEvent).length,
            missed: (review?.missed || []).length,
        },
    };
}

// -------------------------------------------- marking the model's predictions
//
// The xG model was fitted on a hundred thousand-odd StatsBomb shots taken by
// professionals. Nothing in it has ever seen a high school pitch, and until
// somebody records what a shot actually did there is no evidence either way:
// every number on the shot map is a prediction nobody has ever marked.
//
// Two rules make the marking worth doing.
//
// **The verdict has to come from a person.** `Shot.outcome` is already in the
// report, inferred from a ball the pipeline sees in roughly 60% of frames, and
// grading one model against another model's guess measures the agreement of two
// guesses. So the pipeline's own reading is shown beside the buttons and never
// preselects one — a prefilled answer clicked past is an unmarked shot with a
// signature on it.
//
// **Goals are rare and a match is small.** Ten shots worth 1.2 xG between them
// will produce anywhere from none to four goals with nothing wrong anywhere, so
// a bare "predicted 1.2, scored 3" reads as a broken model and is not evidence
// of one. Everything below therefore carries the size of the gap this many
// shots could actually have detected, and refuses to call anything smaller.
//
// The tally is deliberately four numbers rather than a verdict. A verdict is
// only worth reading over a season, and a season is these four summed.

export const SHOT_RESULTS = [
    { value: 'goal', label: 'Goal' },
    { value: 'saved', label: 'Saved' },
    { value: 'blocked', label: 'Blocked' },
    { value: 'off_target', label: 'Off target' },
    { value: 'woodwork', label: 'Woodwork' },
];

const SHOT_RESULT_VALUES = new Set(SHOT_RESULTS.map((r) => r.value));

// The smallest miscalibration worth being able to see, as a share of the goals
// predicted.
//
// A share, not a number of goals, and this is the easy thing to get backwards.
// The band grows with the square root of the shot count and the prediction grows
// with the count itself, so a threshold measured in goals would call three shots
// conclusive and a season inconclusive — precisely inverted. At half, the
// question is "could this sample tell a good model from one that is 50% out",
// which on typical chances takes about 150 shots: a season of both teams' shots,
// and honest about being one.
const RESOLVABLE_SHARE = 0.5;

// How many goals a sample has to expect before the band is allowed to accuse
// the model of anything.
//
// The band is a normal approximation to a sum of coin flips, and that
// approximation is poor when the expected count is tiny — the real distribution
// is sharply skewed, so two standard deviations is nowhere near the 95% it
// implies. Without this gate, two half-chances and one lucky finish come out as
// "the model is rating these too low", which is precisely the over-reading every
// other line here exists to prevent. The usual np ≥ 5 rule of thumb, relaxed a
// little and applied from both ends.
const MIN_EXPECTED = 4;

const SHOT_TYPE = 'shot';

const round4 = (value) => Math.round(value * 1e4) / 1e4;

/**
 * Every detected shot, with whatever a coach has said happened to it.
 *
 * Built from `cvStats/events` rather than the shot map, because the map carries
 * positions and no ids — and an id is what lets a verdict outlive the page.
 *
 * Rejected and retyped shots stay in the list, marked rather than removed. A row
 * that disappears the moment you reject it looks like a bug, and the struck-out
 * row is the only thing that explains why the tally below just moved.
 */
export function shotLedger(events, review) {
    const byEvent = review?.byEvent || {};
    const rows = [];

    for (const event of events || []) {
        if (event.type !== SHOT_TYPE) continue;
        const decision = byEvent[event.id] || {};

        // Two ways a shot stops being one, and they have to be handled
        // together: rejected outright, or edited into some other kind of event.
        // Either way the moment is no longer a shot, and a chance the model was
        // never asked about cannot be evidence about the model.
        const retyped = decision.status === EDITED_STATUS
            && decision.type && decision.type !== SHOT_TYPE;

        // Which xG counts depends on a thing the camera cannot see, so the
        // pipeline sent both and the coach picks. No fallback when a header was
        // tagged on a run that predates the second reading: scoring it as a foot
        // shot anyway is exactly the error being corrected, so it drops out of
        // the check with a null instead.
        const header = decision.header === true;
        const xgFoot = event.xg ?? null;
        const xgHeader = event.xgHeader ?? null;

        rows.push({
            id: event.id,
            timestampS: event.timestampS ?? null,
            team: event.team ?? null,
            trackId: event.trackId ?? null,
            xgFoot,
            xgHeader,
            header,
            xg: header ? xgHeader : xgFoot,
            // What the pipeline made of it. Shown, never used as an answer.
            guessed: event.outcome ?? null,
            result: SHOT_RESULT_VALUES.has(decision.result) ? decision.result : null,
            counted: decision.status !== REJECTED_STATUS && !retyped,
        });
    }

    rows.sort((a, b) => (a.timestampS ?? 0) - (b.timestampS ?? 0));
    return rows;
}

/**
 * What the coach's header tags did to the total, or null if they tagged none.
 *
 * Worth stating as a movement rather than just showing a corrected figure. A
 * total that quietly drops from 1.42 to 1.05 between two visits looks like a
 * bug; the same drop with "three headers" beside it is the tool working.
 *
 * `unscorable` counts headers on runs old enough to carry only the foot
 * reading. Those leave the total rather than staying in it wrong, which is the
 * right answer and a surprising one, so it is said out loud.
 */
export function headerCorrection(rows) {
    let headers = 0;
    let unscorable = 0;
    let from = 0;
    let to = 0;

    for (const row of rows || []) {
        if (!row?.counted || !row.header) continue;
        headers += 1;
        if (row.xgHeader == null) {
            unscorable += 1;
            continue;
        }
        from += row.xgFoot ?? 0;
        to += row.xgHeader;
    }

    if (!headers) return null;
    return { headers, unscorable, from: round4(from), to: round4(to) };
}

/**
 * The header tags as a sentence, or null when there are none.
 *
 * The two halves are separate sentences because they are separate facts, and
 * running them together produced the reading this exists to stop: a match where
 * every tagged header was unscorable said "which took 0.00 xG down to 0.00",
 * which is arithmetically true and says the opposite of what happened.
 */
export function headerNote(correction) {
    if (!correction?.headers) return null;
    const { headers, unscorable, from, to } = correction;
    const scored = headers - unscorable;
    const parts = [];

    if (scored) {
        parts.push(`${scored} shot${scored === 1 ? '' : 's'} you tagged as a`
            + ` header ${scored === 1 ? 'is' : 'are'} scored as one, which took`
            + ` ${from.toFixed(2)} xG down to ${to.toFixed(2)}.`);
    }
    if (unscorable) {
        const it = unscorable === 1 ? 'it is' : 'they are';
        parts.push(`${unscorable}${scored ? ' more' : ''} came from a run made`
            + ' before the header figure existed, so'
            + ` ${it} left out of these totals rather than counted as foot`
            + ' shots. Re-running the pipeline brings'
            + ` ${unscorable === 1 ? 'it' : 'them'} back.`);
    }
    return parts.join(' ');
}

/**
 * Shot-map marks with the coach's body-part tags applied.
 *
 * Joined on `event_id`, which is why the pipeline started emitting one onto each
 * mark: a rounded timestamp is not an identity, and two shots inside the same
 * second would swap their corrections without anything looking wrong.
 *
 * Marks the ledger says nothing about pass through untouched, so a report from
 * before any of this existed renders exactly as it did.
 *
 * A header the run cannot score comes out with a **null** xG rather than its
 * foot figure. That is what makes the map agree with everything around it:
 * `shotSummary` skips a null and `markRadius` draws it at the floor, so the dot
 * stays on the pitch — the shot happened — while the caption's total, the
 * sentence under it and the check below all leave it out together. Keeping the
 * foot figure here would have left the map counting a shot the note beside it
 * said had been dropped.
 */
export function correctedShotMarks(marks, rows) {
    const byId = new Map();
    for (const row of rows || []) {
        // `counted` as well as `header`, so this and `headerCorrection` are
        // always describing the same set of shots. A map that has quietly
        // corrected one more shot than the sentence under it claims is the kind
        // of half-a-goal discrepancy nobody ever tracks down.
        if (row?.id != null && row.header && row.counted) byId.set(row.id, row.xg);
    }
    if (!byId.size) return marks || [];

    return (marks || []).map((mark) => (byId.has(mark.event_id)
        ? { ...mark, xg: byId.get(mark.event_id), is_header: true }
        : mark));
}

/**
 * The four numbers a match contributes to the check, or null if it contributes
 * none.
 *
 * `variance` travels rather than a standard deviation because variances add and
 * standard deviations do not — a season is the sum of these, and storing the
 * root would make the season figure quietly wrong in the safe-looking direction.
 *
 * A shot with no xG is skipped on both sides at once. Counting its goal without
 * its prediction would credit the team with a goal the model was never asked to
 * predict, which is the one arithmetic mistake here that flatters nobody and
 * still ruins the answer.
 */
export function xgTally(rows) {
    let shots = 0;
    let predicted = 0;
    let scored = 0;
    let variance = 0;

    for (const row of rows || []) {
        if (!row?.counted || row.result == null || row.xg == null) continue;
        shots += 1;
        predicted += row.xg;
        // Bernoulli: a shot worth 0.5 is the most uncertain one there is, and a
        // tap-in worth 0.95 barely widens the band at all.
        variance += row.xg * (1 - row.xg);
        if (row.result === 'goal') scored += 1;
    }

    if (!shots) return null;
    return {
        shots,
        predicted: round4(predicted),
        scored,
        variance: round4(variance),
    };
}

/** Several matches' tallies as one. Null when none of them had anything. */
export function sumXgTallies(tallies) {
    const total = { shots: 0, predicted: 0, scored: 0, variance: 0 };
    let any = false;

    for (const tally of tallies || []) {
        if (!tally?.shots) continue;
        any = true;
        total.shots += tally.shots;
        total.predicted += tally.predicted || 0;
        total.scored += tally.scored || 0;
        total.variance += tally.variance || 0;
    }

    if (!any) return null;
    total.predicted = round4(total.predicted);
    total.variance = round4(total.variance);
    return total;
}

/**
 * What a tally says about the model, and how much it is entitled to say.
 *
 * Under the model each shot is its own coin weighted by its xG, so the goals a
 * set of shots should produce has mean `Σ xg` and variance `Σ xg(1−xg)`. Two
 * standard deviations is the gap this sample could have detected; anything
 * inside it is what chance does to a small number of shots, not a finding.
 *
 * `verdict`:
 *  - `model_low`  — more went in than predicted, past what chance covers
 *  - `model_high` — fewer did
 *  - `consistent` — no gap, on a sample that could have found a sizeable one
 *  - `inconclusive` — no gap, on a sample that could not have found one anyway
 *
 * The last two are the pair that matters. Collapsing them into "agrees" is how a
 * model gets declared fit for a season on the strength of nine shots.
 */
export function xgCalibration(tally) {
    if (!tally?.shots) {
        return {
            shots: 0, predicted: null, scored: null,
            sd: null, band: null, gap: null, verdict: null,
        };
    }

    const { shots, predicted, scored, variance } = tally;
    const sd = Math.sqrt(variance);
    const band = 2 * sd;
    const gap = scored - predicted;

    // A gap wider than the band is a real difference however wide the band is —
    // a sample too small to find a 50% error can still find a 300% one, and
    // that is the only unambiguous evidence this tool ever produces. But only
    // once the approximation behind the band holds at all: below `MIN_EXPECTED`
    // the accusation is an artefact of the maths, not a finding about football.
    const trustworthyBand = predicted >= MIN_EXPECTED
        && shots - predicted >= MIN_EXPECTED;

    let verdict;
    if (trustworthyBand && gap > band) verdict = 'model_low';
    else if (trustworthyBand && -gap > band) verdict = 'model_high';
    else if (!trustworthyBand || band >= predicted * RESOLVABLE_SHARE) {
        verdict = 'inconclusive';
    } else verdict = 'consistent';

    return { shots, predicted, scored, sd, band, gap, verdict };
}

/**
 * The calibration as a sentence, because the numbers alone mislead.
 *
 * "Predicted 1.2, scored 3" is the reading a coach will take from a table, and
 * on twelve shots it means nothing whatsoever. The band has to be in the same
 * breath as the difference or it will not be read at all.
 */
export function calibrationNote(cal, options = {}) {
    if (!cal?.shots) return null;
    const { over = 'you have marked' } = options;

    const shots = `${cal.shots} shot${cal.shots === 1 ? '' : 's'}`;
    const head = `The ${shots} ${over} were worth ${cal.predicted.toFixed(2)} xG`
        + ` between them, and ${cal.scored} went in.`;
    const band = cal.band.toFixed(1);
    const gap = Math.abs(cal.gap).toFixed(1);

    if (cal.verdict === 'inconclusive') {
        return `${head} That is inside the ±${band} goals chance alone moves a`
            + ` sample this size — ${shots} could not tell a good model from one`
            + ' that is half out either way. Keep marking.';
    }
    if (cal.verdict === 'consistent') {
        return `${head} That is inside the ±${band} goals chance alone moves a`
            + ' sample this size, and there are now enough shots that a model half'
            + ' out would have shown. So far the model and this pitch agree.';
    }
    const direction = cal.verdict === 'model_low'
        ? 'more than it expected, so it is rating these chances too low'
        : 'fewer than it expected, so it is rating these chances too high';
    return `${head} That is ${gap} goals ${direction} — further than the ±${band}`
        + ' goals chance accounts for on this many shots.';
}

// ------------------------------------------------------- the press, over time
//
// One PPDA for a whole match answers "did they press" and hides the question a
// coach actually asks, which is "for how long". A side that squeezed for twenty
// minutes and then sat off has the same match figure as one that pressed evenly
// throughout, and those are different teams playing differently.
//
// `cv/events.py::pressing_segments` does the counting and decides which blocks
// have a denominator worth a ratio; nothing here second-guesses it. A block
// arrives with `ppda: null` for one of two reasons and the difference is the
// whole reading — `actions: 0` is a spell in which nobody was challenged, which
// is the strongest possible finding, and a small non-zero count is a spell too
// quiet to score. Both draw no bar. Only one of them is news.
//
//     Longer bar, worse press.
//
// PPDA is passes *allowed*, so it runs backwards from every other bar on this
// site. The alternative — plotting its reciprocal, so bigger meant harder —
// reads more naturally and would put a number on screen that disagrees with the
// PPDA row printed directly above it. Two numbers side by side describing one
// thing with different denominators is the failure this whole app keeps
// tripping over, so the direction is stated in words instead.

/**
 * The press block by block, with the bar lengths worked out.
 *
 * `share` is measured against the longest bar and **from zero**, not from the
 * shortest. A bar chart that starts at the minimum makes a 10% difference look
 * total, and the differences here are already inside the noise more often than
 * not.
 *
 * Returns null rather than an empty trend when there is nothing to draw, so a
 * caller hides the block instead of captioning a blank.
 */
export function pressingTrend(segments, options = {}) {
    const { videoOffsetS = 0 } = options;
    if (!segments?.length) return null;

    const minute = (s) => Math.max(0, Math.round((s - videoOffsetS) / 60));
    const blocks = segments.map((seg) => ({
        startS: seg.start_s ?? null,
        endS: seg.end_s ?? null,
        allowed: seg.allowed ?? 0,
        actions: seg.actions ?? 0,
        ppda: seg.ppda ?? null,
        startMin: seg.start_s == null ? null : minute(seg.start_s),
        endMin: seg.end_s == null ? null : minute(seg.end_s),
        // Two different silences, and the note tells them apart.
        unchallenged: (seg.ppda ?? null) == null && !(seg.actions ?? 0),
        thin: (seg.ppda ?? null) == null && (seg.actions ?? 0) > 0,
    }));

    const scored = blocks.filter((b) => b.ppda != null);
    const longest = Math.max(0, ...scored.map((b) => b.ppda));
    for (const block of blocks) {
        block.share = block.ppda == null || !longest ? null : block.ppda / longest;
    }

    return {
        blocks,
        scored: scored.length,
        thin: blocks.filter((b) => b.thin).length,
        unchallenged: blocks.filter((b) => b.unchallenged).length,
        hardest: scored.length
            ? scored.reduce((a, b) => (b.ppda < a.ppda ? b : a)) : null,
        softest: scored.length
            ? scored.reduce((a, b) => (b.ppda > a.ppda ? b : a)) : null,
    };
}

// How much worse the last scored block has to be than the first before it is
// worth saying the press dropped off.
//
// Set from the denominator, not from taste. A block holds roughly ten defensive
// actions, which as a count carries about ±32%; the ratio of two such blocks
// therefore moves about ±45% on chance alone, so a factor under about 1.8 is
// indistinguishable from an ordinary quarter-hour. Blocks without a ratio take
// no part in this comparison at all.
const PRESS_DROP_FACTOR = 1.8;

/**
 * What the trend says out loud. Null when it says nothing.
 *
 * Deliberately compares first scored block against last rather than fitting a
 * slope. "You allowed twice as many passes per challenge by the end" is a thing
 * a coach can be told; a gradient in PPDA per minute is not, and on four points
 * a fitted slope is mostly a picture of the noise anyway.
 */
export function pressingRead(trend) {
    const scored = (trend?.blocks || []).filter((b) => b.ppda != null);
    if (scored.length < 2) return null;

    const first = scored[0];
    const last = scored[scored.length - 1];
    if (last.ppda < first.ppda * PRESS_DROP_FACTOR) return null;

    return {
        title: 'Your press faded',
        detail: `Early on you challenged every ${first.ppda.toFixed(1)} passes`
            + ` they made; by minute ${last.startMin}–${last.endMin} it took`
            + ` ${last.ppda.toFixed(1)}.`,
    };
}

/**
 * The caveats under the chart. Always returns at least the foul one.
 *
 * The foul caveat is not decoration. Fouls are a defensive action in the
 * standard definition and are invisible to a bounding box, so every figure here
 * is short a denominator and reads high — a known direction and an unknown size,
 * which is exactly the kind of bias that has to be printed beside the number
 * rather than buried in a Python docstring.
 */
export function pressingNote(trend) {
    if (!trend) return null;
    const parts = [];

    // One counter, so the two sentences cannot end up as "One block" beside
    // "1 block" — which is what the first version did.
    const blocks = (n) => (n === 1 ? 'One block' : `${n} blocks`);

    if (trend.unchallenged) {
        parts.push(`${blocks(trend.unchallenged)} `
            + `${trend.unchallenged === 1 ? 'has' : 'have'} no bar because nobody `
            + 'made a challenge in the pressing zone at all — that is the '
            + 'reading, not a gap.');
    }
    if (trend.thin) {
        parts.push(`${blocks(trend.thin)} had too few challenges to divide by, `
            + 'so only the counts are shown.');
    }

    parts.push('Fouls count as a challenge in the usual definition of this figure '
        + 'and a camera cannot see them, so every bar here runs a little long.');
    return parts.join(' ');
}

// ------------------------------------------------- reading the half out loud
//
// The stats catalog asks for "plain-language flags over raw tables" at
// half-time, and gives the shape of them: *"RB has covered 20% less ground than
// LB"*, *"team compactness dropped in the last 15 minutes"*. A coach has three
// minutes and is standing up. A table of eighteen numbers is not a read on the
// half; three sentences are.
//
// Every threshold below is a guess, none has been checked against a real match,
// and the consequence of setting one too low is worse than it looks: a flag
// that fires every game stops being read, and takes the ones that matter with
// it. So they are set where a difference is large enough that a coach would
// have noticed it themselves — the value here is confirmation and a number to
// say out loud, not detection.

// Share of your own possession spent in your own third before it is worth
// saying. An even spread across three thirds is 33%, so this is already well
// clear of ordinary.
const PINNED_BACK_SHARE = 0.45;

// Metres a shape figure has to move. Matches MIN_DRIFT_M in cv/metrics.py, and
// is about a player's worth of spacing.
const SHAPE_MOVE_M = 3.0;

// Giveaways in your own defensive third before it is a pattern rather than a
// bad minute.
const DANGEROUS_GIVEAWAYS = 5;

const SHAPE_WORDS = {
    width_m: ['wider', 'narrower'],
    depth_m: ['longer front to back', 'shorter front to back'],
    compactness_m: ['more spread out', 'more compact'],
};

/**
 * What the video says about the half, as sentences rather than rows.
 *
 * `cv` is the published `cvStats/summary` document. Returns
 * `[{ title, detail }]`, worst-first-ish and often empty — empty is the correct
 * and common answer, and a caller should render nothing rather than a heading
 * over a blank space.
 *
 * Reads only `teams.team_a`, which is always the coach's own side.
 *
 * `options.videoOffsetS` only affects the minutes quoted in the pressing read.
 * Left out it quotes video minutes, which are the right minutes when nobody has
 * synced the clock — a wrong match minute reads as fact, a video minute reads
 * as a position in the footage.
 */
export function cvReads(cv, options = {}) {
    const ours = cv?.teams?.team_a;
    if (!ours) return [];

    const reads = [];
    const pct = (v) => Math.round(v * 100);

    // Where the ball was, not just how much of it you had. The two come apart
    // exactly where it matters: a side pinned in its own half can hold 60% of
    // the ball and be losing.
    const territory = ours.territory;
    if (territory && territory.defensive >= PINNED_BACK_SHARE) {
        reads.push({
            title: 'You had the ball, but not where it counts',
            detail: `${pct(territory.defensive)}% of your possession was in your `
                + `own third, and ${pct(territory.attacking)}% in theirs.`,
        });
    }

    // Whether the shape held. Deliberately not toned good or bad — a side that
    // tightened up was well-drilled or was pinned back, and this cannot tell
    // the difference.
    const change = ours.shape_drift?.change || {};
    const moved = Object.entries(SHAPE_WORDS)
        .map(([key, [grew, shrank]]) => [key, change[key], grew, shrank])
        .filter(([, value]) => value != null && Math.abs(value) >= SHAPE_MOVE_M);

    if (moved.length) {
        reads.push({
            title: 'Your shape changed during the half',
            detail: moved
                .map(([, value, grew, shrank]) =>
                    `${Math.round(Math.abs(value))}m ${value > 0 ? grew : shrank}`)
                .join(', ') + ' by the end of it.',
        });
    }

    // Giveaways in front of your own goal. A single turnover count cannot say
    // this, which is why it is counted by third.
    const lost = ours.turnovers_by_third;
    if (lost && lost.defensive >= DANGEROUS_GIVEAWAYS) {
        reads.push({
            title: `${lost.defensive} giveaways in your own third`,
            detail: 'Passes lost in front of your own goal — the ones that turn '
                + 'straight into a chance against you.',
        });
    }

    // Whether the press lasted. The half-time page is the one place this read
    // can still be acted on, which is most of why it exists.
    const faded = pressingRead(pressingTrend(ours.pressing_segments, options));
    if (faded) reads.push(faded);

    // Chances created against chances taken. Both directions are worth saying:
    // one is bad luck or bad finishing, the other is a lead that flatters.
    if (ours.xg != null && ours.goals != null && ours.shots) {
        const gap = ours.goals - ours.xg;
        if (gap <= -1.0) {
            reads.push({
                title: 'You have made more than you have taken',
                detail: `${ours.shots} shots worth about ${ours.xg.toFixed(1)} `
                    + `expected goals, and ${ours.goals} scored.`,
            });
        } else if (gap >= 1.0) {
            reads.push({
                title: 'The scoreline is ahead of the chances',
                detail: `${ours.goals} from about ${ours.xg.toFixed(1)} expected `
                    + `goals across ${ours.shots} shots.`,
            });
        }
    }

    return reads;
}
