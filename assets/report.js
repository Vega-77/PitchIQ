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
    // Bursts add across fragments the way sprints do. A cluster that could not
    // answer publishes null and is skipped below rather than adding zero, so a
    // player whose fragments were all too short or too noisy ends with no
    // figure instead of with a figure of none.
    'accelerations',
];
// Taken at the worst fragment, not averaged: a player assembled from a clean
// track and a jittery one is only as trustworthy as the jittery one, and an
// average would hide it behind the clean one.
const MAXED = ['top_speed_kmh', 'position_noise_m'];

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
export function cvStatsByPlayer(tracks, byCluster, options = {}) {
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

    // The coach's verdicts, applied last. See `reviewedCorrections` for what
    // moves and what deliberately does not — and note the order: accuracy is
    // recomputed after the corrections, because a rejected pass changes both
    // halves of that fraction.
    const deltas = reviewedCorrections(
        options.events, options.review, options.clusters, byCluster,
    );
    for (const [playerId, delta] of Object.entries(deltas)) {
        const acc = out[playerId];
        // A correction for somebody with no mapped cluster has nowhere to land.
        // Making one up would invent a player's whole video record out of one
        // retyped tackle, which is worse than losing the correction.
        if (!acc) continue;
        for (const [key, change] of Object.entries(delta)) {
            if (acc[key] == null) continue;
            acc[key] = Math.max(0, acc[key] + change);
        }
        acc.reviewed = true;
        acc.passAccuracy = acc.passes_attempted
            ? acc.passes_completed / acc.passes_attempted
            : null;
    }
    return out;
}

// Which published counter each kind of event feeds. `shot` is deliberately
// absent: a shot's fate is already decided by the ledger and carried by
// `correctedShotMarks`, and a second subtraction here would take it off twice.
// `clearance` and `duel` are absent because nothing publishes them — a retype
// into one removes the original and adds nothing, which is the truth.
const EVENT_COUNTERS = {
    pass: 'passes_attempted',
    carry: 'carries',
    tackle: 'tackles',
    interception: 'interceptions',
    recovery: 'recoveries',
};

/**
 * What the coach's review does to each player's video figures.
 *
 * The review tool's whole purpose is to correct the pipeline, and until now its
 * corrections reached the scorecard and the xG check and **nothing a player
 * ever saw**. A coach could reject thirty phantom passes, watch precision fall,
 * and still publish a report crediting the player with all thirty.
 *
 *     What moves.
 *
 * The event counts, and only the event counts. A rejected pass is a pass that
 * did not happen. A pass retyped as a tackle is one fewer pass and one more
 * tackle. A tackle reassigned to another player moves whole — which is the
 * correction that matters most, because it is how a coach fixes an identity the
 * cluster mapping got wrong without re-doing the mapping.
 *
 *     What does not, and why.
 *
 * Distance, top speed, sprints and bursts come from the *track*, not from the
 * event list. No verdict about an event is a verdict about where a player ran,
 * and subtracting metres because a pass was imaginary would be inventing a
 * correction nobody made. Touches are left alone for the same reason: a touch
 * is a moment the ball's motion changed near a player, and rejecting the event
 * derived from it does not prove the ball never moved.
 *
 *     What stays counted.
 *
 * Everything unreviewed. The review is partial by nature — twelve events out of
 * five hundred — so this starts from the pipeline's totals and subtracts what a
 * human contradicted, rather than starting from nothing and adding what a human
 * confirmed. The other way round, a coach who checked ten events would wipe out
 * the match.
 */
export function reviewedCorrections(events, review, clusters, byCluster) {
    const byEvent = review?.byEvent || {};
    if (!events?.length || !Object.keys(byEvent).length) return {};

    // Track id to player, the same walk `whoIs` does on the coach's screen:
    // events carry track ids, the mapping is keyed by cluster.
    const playerOfTrack = new Map();
    for (const cluster of clusters || []) {
        const playerId = (byCluster || {})[String(cluster.cluster_id)];
        if (!playerId || playerId === NOT_A_PLAYER) continue;
        for (const trackId of cluster.track_ids || []) {
            playerOfTrack.set(Number(trackId), playerId);
        }
    }

    const deltas = {};
    const bump = (playerId, key, by) => {
        if (!playerId || !key || !by) return;
        const delta = deltas[playerId] ||= {};
        delta[key] = (delta[key] ?? 0) + by;
    };

    for (const event of events) {
        const decision = byEvent[event.id];
        if (!decision || decision.status === CONFIRMED_STATUS) continue;

        const from = playerOfTrack.get(Number(event.trackId));
        if (!from) continue;

        const claimed = EVENT_COUNTERS[event.type];
        const rejected = decision.status === REJECTED_STATUS;
        const retyped = decision.status === EDITED_STATUS
            && decision.type && decision.type !== event.type;
        const moved = decision.status === EDITED_STATUS
            && decision.playerId && decision.playerId !== from;

        if (!rejected && !retyped && !moved) continue;

        // Off the player it was credited to, under the type it was claimed as.
        bump(from, claimed, -1);
        if (event.type === 'pass' && event.outcome === 'completed') {
            bump(from, 'passes_completed', -1);
        }
        if (rejected) continue;

        // And back on, wherever the coach put it. A pass retyped into a shot
        // adds nothing: the pipeline never computed a position or an xG for it,
        // so it cannot become a shot anything downstream could use.
        const to = moved ? decision.playerId : from;
        const becomes = EVENT_COUNTERS[retyped ? decision.type : event.type];
        bump(to, becomes, +1);
        if (!retyped && event.type === 'pass' && event.outcome === 'completed') {
            bump(to, 'passes_completed', +1);
        }
    }

    return deltas;
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
    'cvTopSpeedKmh', 'cvSprintCount', 'cvAccelerations', 'cvPositionNoiseM',
    'cvMinutesTracked', 'cvTouchTimes',
    'cvClusterCount', 'cvShotMap', 'cvReviewed',
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
    const published = stats.shotMap || [];
    const marks = correctedShotMarks(published, shotRows);
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
        // Counted off the corrected marks for the same reason the xG below is:
        // a shot the coach rejected is not a shot, and a report that dropped it
        // from the map while keeping it in the count would disagree with
        // itself. `published` rather than `marks.length` decides the fallback —
        // otherwise a player whose only shot was rejected would fall back to
        // the pipeline's total and get it handed straight back.
        cvShots: published.length ? marks.length : num(stats.shots),
        // Summed off the corrected marks rather than off the pipeline's own
        // per-track total, so the header tags reach it. Falls back to the
        // pipeline's figure when there are no marks to sum — a report from
        // before shot maps existed still has an xG.
        //
        // Three outcomes, not two, and the middle one is the point. Marks that
        // survive with no xG between them give null: those shots happened and
        // cannot be scored. Marks that were all rejected give **zero**: a coach
        // looked and concluded there were none, which is a measurement rather
        // than an absence, and it is what makes this agree with `cvShots: 0`.
        cvXg: published.length
            ? (marks.length ? sumXg(marks) : 0)
            : num(stats.xg),
        cvShotMap: marks,
        cvDistanceM: num(stats.distance_m),
        cvTopSpeedKmh: num(stats.top_speed_kmh),
        cvSprintCount: num(stats.sprint_count),
        cvAccelerations: num(stats.accelerations),
        cvPositionNoiseM: num(stats.position_noise_m),
        cvMinutesTracked: num(stats.minutes_tracked),
        cvTouchTimes: stats.touchTimes || [],
        // How many tracked fragments this player was assembled from. Shown to
        // the coach, because a player stitched out of nine pieces is a weaker
        // claim than one tracked cleanly throughout.
        cvClusterCount: (stats.clusters || []).length,
        // Whether a human moved any of the counts above. A figure that changed
        // between two visits looks like a bug unless something says a coach
        // changed it — and it travels to the player's own report for the same
        // reason every other caveat does.
        cvReviewed: Boolean(stats.reviewed),
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
// --------------------------------------------------- when the match ended,
//                                                       and how anyone knows
//
// `matchEndS` closes every open stint, so it decides how many minutes each
// player is recorded as having played. It came out of the tag log as
// `max(matchClockS)`, defaulting to **zero** — and zero is not a final whistle,
// it is the absence of one.
//
// That default is reachable in the most ordinary way there is. `setLineup`
// writes a starter's `{inS: 0, outS: null}` the moment a lineup is set, hours
// before kick-off. So a match that was filmed and processed but that nobody ran
// the tablet for has a full roster of open stints and an empty log — and every
// one of those players was recorded as having played **nought minutes**.
//
// It did not stop there. `trackedCoverage` guards on `matchEndS != null`, which
// zero passes, so a starter came out with `onPitchS` of 0 and `watchedS` of 0;
// `share` then fell to null and `coverageNote` — the sentence that exists to
// explain a shortfall — said nothing at all. `cvReportFields` published
// `cvMinutesOnPitch: 0` beside a real `cvDistanceM` of four kilometres. And the
// player portal, reading `minutesPlayed` of 0, described a starter to their own
// face as **"an unused substitute"**.
//
// The fix is provenance rather than a better default. A whistle that was tagged
// and a whistle that was inferred from the last thing anybody tapped are two
// different facts, and no whistle at all is a third.

/** The match was tagged as finished, so this is when it finished. */
export const FROM_WHISTLE = 'whistle';

/**
 * Nobody tagged full time, so this is the last thing anybody tapped.
 *
 * An underestimate by however long the match ran on afterwards, and only in one
 * direction — which makes it usable and makes saying so compulsory.
 */
export const FROM_LAST_TAG = 'last_tag';

/**
 * When the match ended, and how that was arrived at.
 *
 * Returns `{ matchEndS, source }`, with **both** null when the log holds
 * nothing that can date a whistle. Null rather than zero: the callers all treat
 * an absent end as unanswerable and a zero as a real instant, and it is the
 * second reading that turns a full ninety into nothing.
 */
export function whistleFrom(log) {
    const entries = (log || []).filter((e) => num(e?.matchClockS) != null);
    if (!entries.length) return { matchEndS: null, source: null };

    const whistle = entries.find((e) => e.type === 'full_time');
    if (whistle) {
        return { matchEndS: whistle.matchClockS, source: FROM_WHISTLE };
    }
    return {
        matchEndS: entries.reduce((max, e) => Math.max(max, e.matchClockS), 0),
        source: FROM_LAST_TAG,
    };
}

/**
 * How many minutes a player was on the pitch, or null when nobody can say.
 *
 * Lived in `db.js` until 2026-08-15, where `tests/video.test.js` could not
 * reach it — so `tests/flow.test.js` carried a hand-copied second version under
 * a comment reading "mirrors minutesFrom() in assets/db.js", and four tests
 * pinned the copy. Moved here for the same reason `playerTimeline` is here.
 *
 * **A closed stint is still knowable without a whistle.** A player who came off
 * on 60 minutes played sixty of them whether or not anybody tagged full time,
 * so only an *open* stint needs the end of the match. That is most of a
 * substituted squad kept rather than blanked.
 */
export function minutesFrom(stints, matchEndS) {
    if (!stints?.length) return 0;

    let seconds = 0;
    for (const stint of stints) {
        const end = stint.outS ?? matchEndS;
        // Still on the pitch at a whistle nobody recorded. There is no number
        // here, and zero is the one answer that is certainly wrong.
        if (num(end) == null) return null;
        seconds += Math.max(0, end - stint.inS);
    }
    return Math.round(seconds / 60);
}

/**
 * Whether a published report's `minutesPlayed` is a measurement or a stand-in.
 *
 * The document always carries a number — `firestore.rules` requires one, and so
 * does every report written before this question was asked — so the boolean
 * beside it is the only thing that can tell the two apart.
 *
 * **Absent counts as known.** Reports published before `minutesKnown` existed
 * carry real minutes and no flag, and reading that absence as "unknown" would
 * blank whole seasons to fix a handful of matches.
 */
export const knownMinutes = (report) => report?.minutesKnown !== false;

/**
 * What the minutes column is worth, in one sentence, or null when it is fine.
 *
 * Said once under a table rather than per row: on an untagged match every row
 * has the same thing wrong with it, and eleven identical footnotes is a wall a
 * reader learns to skip.
 */
export function minutesNote(source, options = {}) {
    const { second = false, over = true } = options;
    const who = second ? 'you were' : 'anybody was';

    if (source == null) {
        const nobody = second
            ? 'no minutes for you if you were still on at the end'
            : 'no minutes for anyone still on at the end';
        return 'Nobody tagged this match, so there is no clock to say when it '
            + `ended and ${nobody} — not nought minutes, no answer. Anything `
            + 'the video measured was measured all the same.';
    }
    // A match still being played has no final whistle to have tagged, and
    // saying so at every half-time is how a warning becomes wallpaper. The
    // no-log case above is *not* gated on this: a tablet that has recorded
    // nothing by half-time is worth knowing about immediately.
    if (source === FROM_LAST_TAG && !over) return null;

    if (source === FROM_LAST_TAG) {
        return 'Nobody tagged the final whistle, so the match is counted as '
            + `ending at the last thing anybody tapped. If ${who} still on the `
            + 'pitch then, the minutes here are short by however long it ran on '
            + 'afterwards.';
    }
    return null;
}

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
 *
 * **An unknown match end closes an open stint at the end of the window asked
 * about, not at second zero.** Passing zero used to do the latter, and every
 * caller reads the result as time on the pitch: the cluster picker ranked a
 * whole untagged squad at nought seconds of overlap, so the figure a coach was
 * looking at could not be offered its own player. Whoever genuinely cannot
 * answer — `trackedCoverage` — checks for the null itself before asking.
 */
export function stintOverlapS(stints, startS, endS, matchEndS) {
    if (endS <= startS) return 0;
    let total = 0;
    for (const stint of stints || []) {
        const end = stint.outS ?? (num(matchEndS) ?? endS);
        total += Math.max(0, Math.min(end, endS) - Math.max(stint.inS, startS));
    }
    return total;
}

// ------------------------------------------------- video time vs the match clock
//
// Two clocks, and until now one subtraction related them.
//
// The tablet keeps a match clock: zero at the first kick-off, and **frozen for
// the whole of half-time** — `advancePeriod` in tagging.js stops it at the
// break and restarts it from the same second. The video's clock is a position
// in a file and freezes for nothing; it runs straight through the oranges.
//
// So `clockS = videoS - offsetS` is exact for the first half and wrong for the
// entire second half by however long that break lasted, in both directions at
// once. A goal tagged at 52:30 seeks to a video position somewhere in the
// middle of the interval, and a shot the pipeline found at video 68:00 is
// reported as the 68th minute of a match that was 53 minutes old. Ten to
// fifteen minutes of error on every second-half timestamp in the app, and
// invisible, because both numbers come out looking like plausible minutes.
//
// Fixing it needs exactly one fact the offset does not carry: where in the
// video the second half kicks off. Nothing can derive it. The break's length is
// not in the tag log — the tag log is the thing that froze — so the coach
// supplies it the way they already supply the first offset, and the clock the
// second half restarts on comes from the tablet, which knew it at the time and
// now writes it down.
//
// With both anchors the map is piecewise and exact at each of them. With only
// the offset it degrades to precisely today's behaviour and says so, so a
// caller can decline to quote a match minute it cannot stand behind.

export const FIRST_HALF = 'first_half';
export const HALF_TIME = 'half_time';
export const SECOND_HALF = 'second_half';

/**
 * The map between a position in the footage and a reading on the match clock.
 *
 * `secondHalfClockS` is what the tablet's clock read when the second half
 * kicked off — which, because it froze at the break, is also what it read when
 * the first half ended. `secondHalfVideoS` is where that same moment sits in
 * the video.
 *
 * A pair that implies a **negative** break is refused outright rather than
 * used. It means one of the two numbers is mistyped, and honouring it would
 * make the map run backwards: two different moments in the footage would map to
 * one reading on the clock, and the marks on the timeline would cross over each
 * other. Today's one-anchor behaviour is wrong by a known amount in a known
 * direction; a non-monotonic clock is wrong in a way nothing downstream could
 * even describe. `inconsistent` is set so the coach can be told which it is,
 * because a silently ignored number reads as a field that does not work.
 *
 * `period` is `null`, never a guess, whenever only the offset is known. Half of
 * the point here is being able to say "this is a position in the footage, not a
 * match minute".
 */
export function matchClockMap(options = {}) {
    const { videoOffsetS = 0, secondHalfVideoS = null, secondHalfClockS = null } = options;

    const offsetS = Number(videoOffsetS) || 0;
    const kickoffS = num(secondHalfVideoS);
    const restartClockS = num(secondHalfClockS);

    // Both halves of the anchor, and a clock that actually ran before it. A
    // second half restarting at 00:00 is not a second half.
    const paired = kickoffS != null && restartClockS != null && restartClockS > 0;
    const breakS = paired ? (kickoffS - offsetS) - restartClockS : null;
    const inconsistent = paired && breakS < 0;
    const knowsSecondHalf = paired && !inconsistent;

    return {
        videoOffsetS: offsetS,
        knowsSecondHalf,
        inconsistent,
        // How long the video spent on the interval. Null when unknown — which
        // is a different thing from a match played without a break.
        breakS: knowsSecondHalf ? breakS : null,
        secondHalfVideoS: knowsSecondHalf ? kickoffS : null,
        secondHalfClockS: knowsSecondHalf ? restartClockS : null,

        /** What the clock read at a position in the footage. */
        toClock(videoS) {
            const v = Number(videoS) || 0;
            if (!knowsSecondHalf) {
                return { clockS: Math.max(0, v - offsetS), period: null };
            }
            if (v >= kickoffS) {
                return { clockS: restartClockS + (v - kickoffS), period: SECOND_HALF };
            }
            const first = v - offsetS;
            // Inside the break the clock really did read the same second the
            // whole time, so that is what is returned — with the period saying
            // it is a frozen reading rather than a moment in a half.
            if (first >= restartClockS) {
                return { clockS: restartClockS, period: HALF_TIME };
            }
            return { clockS: Math.max(0, first), period: FIRST_HALF };
        },

        /** Where in the footage the clock read this.
         *
         * The restart's own second belongs to the second half: a `kickoff_2nd`
         * tag and the `halftime` tag before it share a clock reading, and of the
         * two positions that could mean, the restart is the one worth seeking
         * to.
         */
        toVideo(clockS) {
            const c = Math.max(0, Number(clockS) || 0);
            if (knowsSecondHalf && c >= restartClockS) {
                return kickoffS + (c - restartClockS);
            }
            return Math.max(0, c + offsetS);
        },

        /** Which half a position in the footage falls in, or null. */
        periodAt(videoS) {
            return this.toClock(videoS).period;
        },
    };
}

const num = (value) => (
    typeof value === 'number' && Number.isFinite(value) ? value : null
);

/**
 * The clock map for a match document, or for a player's copy of one.
 *
 * Both carry the same three fields under the same names — `pushVideoToReports`
 * makes sure of it — so the coach's screen, the half-time view and the player
 * portal cannot end up converting the same moment differently. Which was the
 * previous state of affairs by accident rather than by design: they each did
 * the same subtraction separately, and they were each wrong by the same
 * fifteen minutes, so nothing ever disagreed loudly enough to notice.
 */
/**
 * What the two numbers beside the video link add up to, in a sentence.
 *
 * Four states, and the one worth the most is the first: a coach who has never
 * filled the second field in has no reason to suspect anything is wrong, since
 * the timestamps they get look like ordinary minutes. So the absence is stated
 * as a consequence — moments placed as if the match never stopped — rather than
 * as a blank field.
 *
 * `clockText` is passed in rather than imported: this module has no imports by
 * design so the whole of it can be tested without a DOM, and `renderMatchVideo`
 * already takes its formatter the same way.
 */
export function clockMapNote(inputs = {}, clockText = (s) => `${Math.round(s)}s`) {
    const { videoOffsetS = 0, secondHalfVideoS = null, halfTimeClockS = null } = inputs;

    if (num(secondHalfVideoS) == null) {
        return {
            tone: 'muted',
            text: 'Without this, second-half moments are placed as if the match '
                + 'never stopped — every one of them lands late by however long '
                + 'the break ran.',
        };
    }
    if (num(halfTimeClockS) == null || halfTimeClockS <= 0) {
        return {
            tone: 'warn',
            text: 'Nobody tapped half-time on the tablet for this match, so there '
                + 'is no clock reading to line this up against. Saved, but not '
                + 'used until there is one.',
        };
    }

    const breakS = (secondHalfVideoS - (Number(videoOffsetS) || 0)) - halfTimeClockS;
    if (breakS < 0) {
        return {
            tone: 'warn',
            text: `That puts the second-half kick-off before the first half ended `
                + `— the tablet had the clock at ${clockText(halfTimeClockS)} when `
                + `half-time was tapped. One of these two numbers is wrong, so the `
                + `second half is still being placed without them.`,
        };
    }
    return {
        tone: 'ok',
        text: `Half-time ran ${clockText(breakS)} in the footage. Second-half `
            + 'moments are placed across it.',
    };
}

export function clockFromMatch(match) {
    return matchClockMap({
        videoOffsetS: match?.videoOffsetS ?? 0,
        secondHalfVideoS: match?.secondHalfVideoS ?? null,
        secondHalfClockS: match?.halfTimeClockS ?? null,
    });
}

/**
 * Rank a roster by how well each player's time on the pitch fits a cluster's.
 *
 * The cluster's first and last sightings are in video time and the stints are
 * in match clock, related by `matchClockMap` above — which is piecewise across
 * the break, so a substitute who came on in the second half is scored against
 * the right stretch of footage rather than one shifted by the interval.
 *
 * Returned in full and sorted, never filtered. If that offset is wrong — and it
 * is the single most fiddly number in the app — then every overlap here is
 * wrong too, and a picker that had hidden the non-overlapping players would
 * have hidden the right answer.
 */
export function rankRosterForCluster(roster, cluster, options = {}) {
    const { matchEndS = null } = options;
    const clock = clockOf(options);
    const startS = clock.toClock(cluster?.first_seen_s ?? 0).clockS;
    const endS = clock.toClock(cluster?.last_seen_s ?? 0).clockS;
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

// ----------------------------------------- the same person, tracked twice over
//
// `cv/identity.py` rejoins fragments that are seconds apart and deliberately
// stops there: a player who goes off, or who leaves frame while the camera
// pans and comes back a minute later, stays two figures. That is the right
// failure — two figures named as one player still sum correctly, whereas a
// wrong merge attributes one teenager's match to another and cannot be undone
// downstream — and naming both is already the whole fix, because the picker is
// many-to-one and `cvStatsByPlayer` sums across every cluster mapped to a name.
//
// What is missing is only the saying-so. The list is ordered by how long each
// figure was tracked, so the two halves of one player's match are nowhere near
// each other in it, and finding the second one means recognising a face in a
// forty-row list you have already scrolled past.
//
//     The one thing that can be said with certainty.
//
// Two figures on screen in the same frame are two people, whatever they look
// like. `identity.py` calls that its only certainty and it does most of the
// work there.
//
// The browser has intervals rather than frames — `first_seen_s` and
// `last_seen_s`, not the frame sets — and an interval is normally a much weaker
// thing to reason from. Here it is not, because of an invariant the merging
// gives for free: every merge in `identity.py` joins a pair with a gap between
// 0 and `MAX_BRIDGE_S`, and a cluster is connected through such pairs, so **no
// cluster has an internal hole longer than two seconds**. A cluster's interval
// is therefore very nearly solid, and two intervals that overlap by more than
// that really were on screen together. `tests/test_identity.py` asserts the
// invariant against the merger itself, because this is the load-bearing half of
// the argument and it lives in the other language.
//
// So overlap rules a figure out and nothing else does. Kit colour and the size
// of the gap are evidence, and evidence orders the list rather than shortening
// it, for the same reason `rankRosterForCluster` refuses to filter: the numbers
// underneath are only as good as the video offset, and hiding the poor fits
// would hide the right answer on the day that number is wrong.

// The longest gap `cv/identity.py` will bridge, and so the largest overlap two
// intervals can show while still being one person seen either side of it.
export const BRIDGE_S = 2.0;

// Lab chroma distance beyond which two figures are wearing different shirts.
// Mirrors MAX_CHROMA_DISTANCE in cv/identity.py, which is the source of truth;
// it is looser than team assignment uses because this asks whether two
// sightings are one player, and light changes across a pitch more than kits do.
export const SAME_KIT_CHROMA = 30.0;

/**
 * Lab distance ignoring lightness, matching `_chroma_distance` in identity.py.
 *
 * `null` rather than a number when either colour is missing: no evidence is not
 * the same as agreement, and returning 0 would promote an unknown shirt to a
 * perfect match.
 */
export function kitDistance(a, b) {
    if (!Array.isArray(a) || !Array.isArray(b)) return null;
    if (a.length < 3 || b.length < 3) return null;
    return Math.hypot(a[1] - b[1], a[2] - b[2]);
}

/**
 * The other tracked figures that could be the same person as this one.
 *
 * Returns every other cluster, ranked, each carrying why it is where it is:
 *
 *   - `overlapS` — seconds the two were on screen together. Above `BRIDGE_S`
 *     this is `ruledOut`, the one hard answer available.
 *   - `gapS` — the silence between them, `0` when they touch or overlap. The
 *     strongest ordering signal: a figure that starts eight seconds after this
 *     one ends is a likelier continuation than one an hour later.
 *   - `kitS` — chroma distance, `null` when either has no colour.
 *   - `sameTeam` — `true`, `false`, or `null` when either side is unclear.
 *   - `takenBy` — the player already mapped to it, if that is somebody else.
 *     Not a bar: a coach who mis-named a fragment should be able to see it here
 *     and say so, and picking it just moves the mapping.
 *
 * `player` in the options is who this figure has been called. When given, each
 * candidate also carries `playedShare` — how much of the candidate's own span
 * that player was actually on the pitch for. A figure tracked entirely while
 * the player it would join was on the bench is a poor suggestion, and this is
 * the number that says so.
 */
export function sameFigureCandidates(clusters, cluster, options = {}) {
    const id = cluster?.cluster_id;
    if (id == null) return [];

    const { mapping = {}, player = null, matchEndS = null } = options;
    const clock = clockOf(options);
    const mine = seenSpan(cluster);

    return (clusters || [])
        .filter((other) => other && other.cluster_id !== id)
        .map((other) => {
            const theirs = seenSpan(other);
            const overlapS = Math.max(
                0,
                Math.min(mine.endS, theirs.endS) - Math.max(mine.startS, theirs.startS),
            );
            const gapS = overlapS > 0
                ? 0
                : Math.max(theirs.startS - mine.endS, mine.startS - theirs.endS);
            const kitS = kitDistance(cluster.colour, other.colour);
            const known = cluster.team && cluster.team !== 'unknown'
                && other.team && other.team !== 'unknown';

            const named = mapping[String(other.cluster_id)];
            const takenBy = named && named !== player ? named : null;

            let playedShare = null;
            if (player && player !== NOT_A_PLAYER) {
                const entry = (options.roster || []).find((r) => r.id === player);
                const startS = clock.toClock(theirs.startS).clockS;
                const endS = clock.toClock(theirs.endS).clockS;
                const width = Math.max(0, endS - startS);
                playedShare = width > 0
                    ? stintOverlapS(entry?.stints, startS, endS, matchEndS) / width
                    : null;
            }

            return {
                cluster: other,
                overlapS,
                gapS,
                kitS,
                sameTeam: known ? cluster.team === other.team : null,
                takenBy,
                playedShare,
                ruledOut: overlapS > BRIDGE_S || (known && cluster.team !== other.team),
            };
        })
        .sort((a, b) => (
            (a.ruledOut ? 1 : 0) - (b.ruledOut ? 1 : 0)
            // A figure the coach has already given to somebody else is a poor
            // suggestion but a legitimate one — they may have named it wrongly,
            // and this is where they would find that out.
            || (a.takenBy ? 1 : 0) - (b.takenBy ? 1 : 0)
            // A shirt that does not match is evidence, so it sorts down; it is
            // never a reason to drop a row, because one bad colour sample on a
            // shaded touchline is a thing that happens.
            || farKit(a) - farKit(b)
            || a.gapS - b.gapS
            || (b.cluster.sightings || 0) - (a.cluster.sightings || 0)
        ));
}

function seenSpan(cluster) {
    return {
        startS: cluster?.first_seen_s ?? 0,
        endS: Math.max(cluster?.first_seen_s ?? 0, cluster?.last_seen_s ?? 0),
    };
}

const farKit = (row) => (row.kitS != null && row.kitS > SAME_KIT_CHROMA ? 1 : 0);

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
 * touchline records. `matchClockMap` above is the only thing relating them, and
 * getting this backwards would score a substitute against the warm-up.
 *
 * An absent bound means the pipeline ran to that edge of the file, so it is
 * answered from the match rather than guessed at: kick-off for a missing start,
 * and the last thing the log recorded for a missing end. With neither the window
 * nor a match end there is nothing to answer from, and this returns null —
 * which is a different thing from a window of zero length.
 */
export function windowClockRange(window, options = {}) {
    const { matchEndS = null } = options;
    const clock = clockOf(options);
    const startVideoS = window?.start_s ?? window?.startS ?? null;
    const endVideoS = window?.end_s ?? window?.endS ?? null;

    const endS = endVideoS == null ? matchEndS : clock.toClock(endVideoS).clockS;
    if (endS == null) return null;

    const startS = startVideoS == null ? 0 : clock.toClock(startVideoS).clockS;
    return { startS: Math.max(0, startS), endS };
}

/**
 * The clock map a caller supplied, or the one its offset alone describes.
 *
 * Every one of these options bags used to carry a bare `videoOffsetS`, and a
 * caller that still does gets exactly the behaviour it had. There is only one
 * implementation of the conversion; this is only two ways of naming what is
 * known about it.
 */
function clockOf(options = {}) {
    return options.clock || matchClockMap({ videoOffsetS: options.videoOffsetS ?? 0 });
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
    const { window, matchEndS = null } = context;

    const trackedS = minutesTracked == null ? null : minutesTracked * 60;
    const known = Array.isArray(stints) && stints.length > 0 && matchEndS != null;
    const range = windowClockRange(window, { clock: clockOf(context), matchEndS });

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

/**
 * What a printed sheet says about itself, along its top edge.
 *
 * A page that has left the app has left everything that made it readable: the
 * navigation saying whose account it came from, the tab title saying which
 * match, and the reader's own memory of having clicked through to it. Three
 * months later it is a piece of paper with a teenager's name and some numbers
 * on it, and the two questions somebody will ask of it — *whose is this, and
 * how old is it* — are exactly the two the screen never had to answer.
 *
 * The date is when the sheet was printed, not when the match was played. Both
 * matter and they are not the same fact: a report re-printed after a coach
 * corrected the review says something different from the one printed the night
 * of the game, and only the printing date distinguishes them.
 *
 * `subject` is the person or team the page is about, `matchLine` the fixture.
 * Either may be missing — a page is still worth stamping with the half of the
 * answer it has.
 */
export function printStamp(options = {}) {
    const { subject, matchLine, printedAt = new Date(), estimated = false } = options;

    const parts = [];
    if (subject) parts.push(subject);
    if (matchLine) parts.push(matchLine);

    const day = printedAt instanceof Date ? printedAt : new Date(printedAt);
    const printed = Number.isNaN(day.getTime())
        ? null
        : day.toLocaleDateString(undefined, {
            year: 'numeric', month: 'short', day: 'numeric',
        });
    if (printed) parts.push(`printed ${printed}`);

    // Not a footnote. Whatever caveat block travelled onto the page can be a
    // sheet away by the time a number is read out loud, and this line is on
    // every one of them.
    //
    // It also has to say what the little marks are. On screen `cvMark` carries
    // its explanation in a title attribute; on paper it is three dots beside a
    // number, which is worse than no mark at all — it looks like a footnote
    // reference to a footnote that is not there.
    if (estimated) {
        parts.push('figures marked ··· were estimated from video, not tapped');
    }

    return parts.join(' · ');
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
/** How a half reads in a sentence. */
export const PERIOD_WORDS = {
    first_half: 'the first half',
    second_half: 'the second half',
};

/**
 * The caveat about which half this is — an array, usually empty.
 *
 * Empty when the tagged log settled it, because a correct answer arrived at
 * correctly is not a caveat and a note that fires on every run stops being
 * read. `'flag'` is quiet too: somebody typed it, so somebody knows.
 *
 * `'default'` is the one that speaks. It means nothing said which half this
 * was, so the pipeline assumed the first — and on second-half footage that
 * assumption mirrors every pitch drawing in the report without changing how
 * any of them look.
 */
export function periodNote(period, source) {
    if (source !== 'default') return [];
    const half = PERIOD_WORDS[period] || 'the first half';
    return [`nothing said which half this was, so it is drawn as ${half}`
        + ' — if it was the other one, every pitch picture here is mirrored'];
}

/**
 * Where `cv/metrics.py` stops reporting bursts. Mirrored here only to word the
 * caveat; the pipeline is what actually withholds them, and a browser that
 * disagreed with it would caption a figure that is not on the page.
 */
export const ACCEL_NOISE_CEILING_M = 0.3;

/**
 * Phantom metres a minute, per metre of positional wobble.
 *
 * **Only for reports written before schema 7.** It was a single number because
 * the pipeline smoothed every track over a fixed nine frames; the window is now
 * fitted to each track's own measured wobble, so the phantom rate stopped being
 * proportional to the wobble and stopped being derivable from it. A version 7
 * report publishes `phantom_m_per_minute` and this is not consulted.
 *
 * Kept rather than deleted because reports already in Firestore do not have the
 * new field, and a coach opening last month's match should get the caveat that
 * was true of it — 353σ was measured against the smoothing that produced those
 * numbers, and it still describes them.
 */
export const PHANTOM_M_PER_MINUTE = 353;

/**
 * What the stretches with no ball in them were, as a bar's worth of segments.
 *
 * `no_ball_s` has always been one figure covering two things a coach would
 * never put in the same sentence: the ball spending eleven seconds in a
 * teenager's hands behind the touchline, and twenty seconds of live football
 * nobody could see. Only the second is a hole in the report — nothing in it
 * reached possession, territory or the event list — and added together the
 * first hides it. Worse, it hides it *more* the better the tagging was.
 *
 * Returns null when there was nothing unseen, or when no tagged log reached the
 * run so nothing sorted it. The second case is deliberately not drawn as one
 * undifferentiated bar: a bar whose whole length is "unknown" claims a split
 * was measured and came out that way. The quality note says so in words
 * instead, which is what it is for.
 */
export function blindSplit(quality) {
    const q = quality || {};
    const blind = q.blind || null;
    if (!blind) return null;

    const totalS = blind.total_s ?? blind.totalS ?? 0;
    if (!(totalS > 0)) return null;

    // The longest single stretch nothing accounted for, and where in the
    // footage it starts. The same total lost as one blackout or as a hundred
    // flickers are different failures, and only the first takes a passage of
    // football with it — so the worst one travels beside the split rather than
    // being averaged into it. Published longest-first by cv/blind.py.
    const first = (blind.worst || [])[0] || null;
    const worst = first ? {
        startS: first.start_s ?? first.startS,
        durationS: first.duration_s ?? first.durationS,
    } : null;

    const checked = Boolean(blind.checked);
    if (!checked) return { totalS, checked, worst, unexplainedS: null, segments: [] };

    const pick = (a, b) => blind[a] ?? blind[b] ?? 0;
    const parts = [
        { key: 'dead', label: 'Ball was out of play', seconds: pick('dead_s', 'deadS') },
        {
            key: 'accounted',
            label: 'Tagged something nearby',
            seconds: pick('accounted_s', 'accountedS'),
        },
        {
            key: 'unexplained',
            label: 'Live play, unaccounted for',
            seconds: pick('unexplained_s', 'unexplainedS'),
        },
    ];

    // Shares of the blind time, not of the match. Taken over the parts rather
    // than over `total_s` so the bar always fills its track: the three come
    // from the same partition and should sum to the total, and a rounding
    // difference must not leave a sliver of bare track that reads as a fourth
    // unnamed category.
    const sum = parts.reduce((acc, part) => acc + part.seconds, 0);
    return {
        totalS,
        checked,
        worst,
        unexplainedS: pick('unexplained_s', 'unexplainedS'),
        segments: parts
            .filter((part) => part.seconds > 0)
            .map((part) => ({ ...part, share: sum > 0 ? part.seconds / sum : 0 })),
    };
}

export function cvQualityNotes(quality, options = {}) {
    const q = quality || {};
    const { calibrated = false } = options;
    const pct = (value) => `${Math.round(value * 100)}%`;
    // 29.97 is a real frame rate and "29.97 of 59.94" is not a sentence.
    const round1 = (value) => String(Math.round(value * 10) / 10);
    const notes = [];

    // Seen, not "has a position for" — the rest were drawn in between
    // sightings, and calling a straight line "visible" would overstate what the
    // video actually showed. The seconds say the same thing as the percentage,
    // in the unit a coach can picture, so they travel together rather than as
    // two sentences that would read as two separate problems.
    // The unseen total is worth much less than the part of it that was
    // football. A well-tagged half is full of throw-ins nobody could have
    // filmed the ball through, and quoting the raw figure makes that half look
    // worse than one where the tagger never turned up. So when the run was
    // checked, the sentence ends on the part that is actually a hole.
    const seen = q.ball_seen_share ?? q.ballSeenShare;
    const noBall = q.no_ball_s ?? q.noBallS;
    const blind = blindSplit(q);
    let lost = '';
    if (blind && blind.checked) {
        lost = `of the ${roughDuration(blind.totalS)} with no ball in sight, `
            + `${roughDuration(blind.unexplainedS)} was live play nothing accounts for`;
    } else if (noBall) {
        lost = `${roughDuration(noBall)} of the clip with no ball in sight`;
    }
    if (seen != null) {
        notes.push(`the ball was visible in ${pct(seen)} of frames`
            + (lost ? ` — ${lost}` : ''));
    } else if (lost) {
        notes.push(lost);
    }

    // Only when the run skipped frames. Saying "30 of 30" is noise; saying
    // "15 of 60" is the single most important fact about how everything below
    // was produced, and a report that skipped frames used to look identical to
    // one that did not.
    //
    // Stated flatly rather than as a warning, because it was measured and it
    // costs under a percent of any distance down to six a second — see
    // tests/test_sampling.py. The pipeline raises a real warning below that
    // floor, and this note is not the place to repeat it.
    const sourceFps = q.source_fps ?? q.sourceFps;
    const sampleFps = q.sample_fps ?? q.sampleFps;
    if (sourceFps && sampleFps && sourceFps > sampleFps + 0.01) {
        notes.push(`read at ${round1(sampleFps)} of the footage's `
            + `${round1(sourceFps)} frames a second`);
    }

    // Only when the run was slower than the football, and phrased as the thing
    // a coach actually experienced rather than as a ratio. The half-time
    // whistle is the one deadline in this project; a factor of 1.4 means the
    // report a coach was waiting for at the break arrived eighteen minutes into
    // the second half, and "1.4x real time" does not say that to anyone.
    //
    // Silent when it keeps up. A batch report produced the following morning is
    // not improved by being told it could have been live, and the run that
    // matters here is the one somebody was standing about waiting for.
    const factor = q.realtime_factor ?? q.realtimeFactor;
    if (factor != null && factor >= 1) {
        notes.push('this took longer to work out than the football it watched — '
            + `about ${roughDuration((factor - 1) * 45 * 60)} behind a live half`);
    }

    if (!calibrated) notes.push('no pitch calibration, so nothing is in metres');

    // The camera moving is not one more caveat among the others — it is the
    // precondition for all of them. The homography is fitted from a single
    // frame, so once the camera has moved every distance, speed, shot position
    // and heatmap after that moment describes a pitch it is no longer pointed
    // at. Said with the minute, because "the camera moved" is a fact and "from
    // 34:12 onwards" is something a coach can act on: that is the half of the
    // match to disbelieve, and the tripod to check before the next one.
    const camera = q.camera;
    if (calibrated && camera?.moved && camera.first_s != null) {
        notes.push(`the camera moved ${roughDuration(camera.first_s)} into the footage`
            + ' — everything in metres after that is measured against the wrong pitch');
    }

    // How much of the pitch was ever in frame — the other question about the
    // same homography, and the one that changes how a *share* reads rather than
    // whether a distance is right.
    //
    // Worth saying because an unseen third does not look unseen. Territory
    // divides possession across the thirds, so a band the camera never held
    // contributes no seconds and comes out as a side that did not go there;
    // a heatmap draws it cold. Both are readings a coach would act on.
    //
    // Only when there is something to say: a camera that framed the pitch is
    // the expected case and a line confirming it would be noise in a list whose
    // whole job is caveats.
    const coverage = q.pitch_coverage;
    if (calibrated && coverage && !coverage.complete) {
        const blind = ['left', 'right'].filter(
            (end) => (coverage.goalmouths?.[end] ?? 1) < 0.6,
        );
        notes.push(blind.length
            // The louder failure, and it is not a matter of degree: shots at
            // that end were not undercounted, they were never seen, and the
            // shot map and every xG behind it are missing them entirely.
            ? `${count(blind.length, 'goalmouth')} never in shot — no shot at`
              + ` ${blind.length > 1 ? 'either' : 'that'} end was seen`
            : `the camera framed ${Math.round(coverage.visible_share * 100)}% of`
              + ' the pitch — shares of the pitch are shares of that part of it');
    }

    // Which half, and only when nothing but a default said so.
    //
    // The period decides which goal each side was attacking, and every pitch
    // picture on these pages is drawn from that: get it wrong and the shot
    // maps, the heatmaps, the pressing zone and the passing network are all
    // mirrored, and every one of them still looks right. When the tagged log
    // answered it there is nothing to say. When nothing did, the reader is
    // looking at an assumption and should be told which one.
    notes.push(...periodNote(options.period, options.periodSource));

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

    // How far the tracked position wobbled, and what that costs.
    //
    // Every figure in metres on these pages rests on this, and until now
    // nothing measured it. It is stated as its consequence rather than as a
    // number alone, because 0.14m means nothing to a coach and "a player
    // standing still is credited with running" means a great deal.
    //
    // The rate is measured, not estimated, and it is now measured *there*: the
    // pipeline picks each track's smoothing window from its own wobble, so it
    // is the only place that knows both halves of the arithmetic. Reading the
    // published figure instead of re-deriving it is what stops this file
    // quoting a rate for a window it has no way to know about.
    //
    // A moving player is inflated far less than a still one, because a real
    // step dominates the wobble added to it — +0.5% on a jog at 0.20m against
    // 22m a minute from nothing. So it is quoted against standing still rather
    // than as a percentage of a distance total.
    const noise = q.position_noise_m ?? q.positionNoiseM;
    if (noise != null && noise > 0) {
        const published = q.phantom_m_per_minute ?? q.phantomMPerMinute;
        const phantomM = Math.round(published ?? noise * PHANTOM_M_PER_MINUTE);
        const smoothing = q.smoothing_s ?? q.smoothingS;
        // Named because it is the one figure on this page that describes a
        // decision rather than a measurement, and a coach comparing two matches
        // is entitled to know the second was smoothed harder than the first.
        const over = smoothing ? `, smoothed over ${smoothing.toFixed(1)}s` : '';
        notes.push(`the tracked position wobbles about ${noise.toFixed(2)}m frame `
            + `to frame${over}, which still credits a player standing still with `
            + `about ${phantomM}m a minute`);
        if (noise > ACCEL_NOISE_CEILING_M) {
            notes.push('too much wobble to count bursts — past about '
                + `${ACCEL_NOISE_CEILING_M}m the count is a count of the jitter`);
        }
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

// ---------------------------------------------------------- the section rail
//
// The rail itself is `assets/rail.js`, which needs a DOM. This is the one
// decision in it that does not: which section a rail should be showing.

/**
 * The section to show, given the ones that exist and the one that was asked for.
 *
 * Falls back to the first rather than to nothing. The set of sections changes
 * underneath a reader — turning the sample preview off takes four blocks away,
 * publishing a report adds six — and one of the ones that goes may be the one
 * being read. Landing on the first section is where every report opens anyway;
 * landing on none of them would be a page that had gone blank.
 *
 * Returns null only when there is genuinely nothing to show, which is a report
 * with no blocks on it at all.
 */
export function railTarget(ids, wanted) {
    const available = (ids || []).filter((id) => id);
    if (!available.length) return null;
    return available.includes(wanted) ? wanted : available[0];
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

// ------------------------------------------------ a player's season, grouped
//
// The same season is read on three screens — the player's own page, the coach's
// view of that player, and the coach's roster. It was three piles of boxes
// built three times, and they had already drifted: one called it "Tackles" and
// another "Tackles won", one counted interceptions and another did not, and
// only one of them had ever heard of carries.
//
// Worse than the drift is what a flat pile hides. Thirteen boxes in a row is a
// wall a reader scans left to right to find the one thing they came for, and it
// flattens the distinction this project cares most about: a goal somebody
// pressed a button for and a distance a machine estimated end up as the same
// box with a small dot in the corner.
//
// So the grouping is a decision, made once, here, and tested — and it hands
// back specs rather than elements, so `statGroup` in ui.js can draw them and
// this module can stay importable by a test file with no DOM.

const FULL_MATCH_MIN = 80;

/**
 * A player's season as titled groups of figures.
 *
 * `reports` is needed as well as `totals` for the two figures that are about
 * particular matches rather than about the sum: their best afternoon, and how
 * many full matches they played. The rest is arithmetic over `seasonTotals`.
 *
 * Groups with nothing in them are dropped, so a season nobody filmed comes back
 * as one group rather than one group and three empty headings.
 */
export function seasonGroups(reports, totals, options = {}) {
    // Whose season this is being read as. A player opens their own page and a
    // coach opens the same season about somebody else, so one of the notes has
    // to change person — and doing that by patching the finished string is how
    // "a pass is two touches with a ball between them" became "…between you".
    const { second = false } = options;
    const them = second ? 'you' : 'them';
    const played = reports || [];
    const t = totals || {};
    const involvements = (r) => (r?.goals || 0) + (r?.assists || 0);

    const tagged = [
        { value: t.matches ?? 0, label: 'Matches' },
        {
            // Absent is not zero: a season made of untagged matches has no
            // minutes total, and printing 0 would say they never played.
            value: t.minutes || '—',
            label: 'Minutes played',
            tone: t.minutesUnknown ? 'is-muted' : '',
        },
        { value: t.goals ?? 0, label: 'Goals', tone: t.goals ? 'is-good' : 'is-muted' },
        { value: t.assists ?? 0, label: 'Assists', tone: t.assists ? 'is-good' : 'is-muted' },
    ];

    // Both discipline figures, and always both. A foul count that only appears
    // when somebody has fouled would make an empty column read as a clean
    // record on one screen and as a missing feature on the next.
    tagged.push({ value: t.fouls ?? 0, label: 'Fouls', tone: 'is-muted' });

    const cards = (t.yellowCards || 0) + (t.redCards || 0);
    tagged.push({ value: cards, label: 'Cards', tone: cards ? 'is-warn' : 'is-muted' });

    // The figure that survives uneven minutes, which is exactly the comparison
    // between a starter and a squad player. Below 45 minutes the denominator is
    // too small to divide into.
    if (t.minutes >= 45) {
        tagged.push({
            value: ((t.goals + t.assists) / t.minutes * 90).toFixed(2),
            label: 'G+A per 90',
            tone: 'is-muted',
        });
    }

    // Their best afternoon — the thing a player actually opens their own page
    // to find, and the thing a coach reaches for first in a conversation.
    const best = played.reduce(
        (a, b) => (involvements(b) > involvements(a) ? b : a), played[0],
    );
    if (best && involvements(best) > 0) {
        tagged.push({
            value: involvements(best),
            label: `Best · ${best.opponentName || 'opponent'}`,
            tone: 'is-good',
        });
    }

    // Unknown is not short. A match with no clock cannot count as a full one,
    // and must not count against them either — it is simply not in the
    // denominator, which is why the card says nothing when there are none.
    const full = played.filter(
        (r) => knownMinutes(r) && (r.minutesPlayed || 0) >= FULL_MATCH_MIN,
    ).length;
    if (full) tagged.push({ value: full, label: 'Full matches', tone: 'is-muted' });

    const groups = [{
        id: 'tagged',
        title: 'The season',
        note: 'Tagged during the match.',
        rows: tagged,
    }];

    if (!t.cvMatches) return groups;

    const over = `Estimated from ${count(t.cvMatches, 'filmed match', 'filmed matches')}`;
    const seen = (value, label) => ({
        value, label, tone: 'is-muted', confidence: 'medium',
    });

    const ball = [seen(t.cvTouches ?? 0, 'Touches')];
    if (t.cvPassesAttempted) {
        ball.push(
            seen(`${Math.round((t.cvPassesCompleted / t.cvPassesAttempted) * 100)}%`,
                'Pass accuracy'),
            seen(t.cvPassesCompleted, 'Passes completed'),
        );
    }
    if (t.cvCarries) ball.push(seen(t.cvCarries, 'Carries'));
    if (t.cvShots) ball.push(seen(t.cvShots, 'Shots'));

    const running = [];
    if (t.cvDistanceM) {
        running.push(seen((t.cvDistanceM / 1000).toFixed(1), 'km covered'));
    }
    if (t.cvTopSpeedKmh) {
        running.push(seen(t.cvTopSpeedKmh.toFixed(1), 'Top speed km/h'));
    }
    if (t.cvSprintCount) running.push(seen(t.cvSprintCount, 'Sprints'));
    if (t.cvAccelerations) running.push(seen(t.cvAccelerations, 'Bursts'));

    const defending = [seen(t.cvTackles ?? 0, 'Tackles won')];
    if (t.cvInterceptions) defending.push(seen(t.cvInterceptions, 'Interceptions'));
    if (t.cvRecoveries) defending.push(seen(t.cvRecoveries, 'Recoveries'));

    groups.push(
        {
            id: 'ball',
            title: 'On the ball',
            note: `${over}. Passing is what the pipeline sees most of, because a `
                + 'pass is two touches with a ball between them.',
            rows: ball,
        },
        {
            id: 'running',
            title: 'Running',
            note: `${over}, over the minutes the tracker actually held on to ${them}.`,
            rows: running,
        },
        { id: 'defending', title: 'Defending', note: `${over}.`, rows: defending },
    );

    return groups.filter((group) => group.rows.length);
}

// --------------------------------------------------- the season, at a glance
//
// Two things a squad page can say from documents it has already loaded, and
// said neither of until now: how the last few games went, and what is still
// waiting to be done.
//
// Both are arithmetic over the match list and the roster, so both live here
// rather than in coach.js, and both are covered in tests/video.test.js. The
// second one especially: "what is outstanding" is a claim that a coach will act
// on, and a job that appears when it should not — or worse, does not appear
// when it should — is the kind of thing a browser check would never catch.

/** A finalized match, read as a result. Null when it was never played. */
export function resultOf(match) {
    if (!match?.finalized) return null;
    const us = num(match.scoreUs) ?? 0;
    const them = num(match.scoreThem) ?? 0;
    if (us > them) return 'W';
    if (us < them) return 'L';
    return 'D';
}

/**
 * The last few results, oldest first — the way a form guide is always read.
 *
 * Only finalized matches. A fixture that has not been played is not a blank in
 * the run, it is simply not in it: five results with a gap in the middle would
 * say a match was played and produced nothing.
 *
 * The list arrives newest first (that is the order the matches tab wants), so
 * this takes from the front and reverses.
 */
export function formGuide(matches, limit = 5) {
    const played = (matches || []).filter((m) => m?.finalized);
    return played
        .slice(0, Math.max(0, limit))
        .map((match) => ({
            id: match.id ?? null,
            opponentName: match.opponentName || 'opponent',
            date: match.date || null,
            result: resultOf(match),
            scoreUs: num(match.scoreUs) ?? 0,
            scoreThem: num(match.scoreThem) ?? 0,
        }))
        .reverse();
}

/**
 * Days from one YYYY-MM-DD to another, or null if either is not one.
 *
 * Parsed as UTC noon rather than midnight local. Both ends of this comparison
 * are calendar dates with no time on them, and midnight is exactly where a
 * timezone offset flips one of them to the day before — which would put "today"
 * a day out for every coach west of Greenwich, which is all of them.
 */
export function daysBetween(from, to) {
    const parse = (value) => {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value ?? ''));
        if (!match) return null;
        return Date.UTC(+match[1], +match[2] - 1, +match[3], 12);
    };
    const a = parse(from);
    const b = parse(to);
    if (a == null || b == null) return null;
    return Math.round((b - a) / 86400000);
}

/**
 * The next fixture that has not been played, or null.
 *
 * "Next" means today or later. A match whose date has passed and which nobody
 * tagged is not the next fixture — it is a job, and `seasonJobs` above already
 * says so. Showing it here under "next up" would put a game that has already
 * been played at the top of the page every week until somebody dealt with it.
 *
 * Dateless fixtures are skipped rather than sorted to one end. A match with no
 * date cannot be shown as "in three days", and a card that says "next up" over
 * a blank is worse than no card.
 */
export function nextFixture(matches, today) {
    if (!today) return null;

    let best = null;
    let bestAway = null;
    for (const match of matches || []) {
        if (!match || match.finalized) continue;
        const away = daysBetween(today, match.date);
        if (away == null || away < 0) continue;
        if (bestAway == null || away < bestAway) {
            best = match;
            bestAway = away;
        }
    }

    if (!best) return null;
    return { match: best, daysAway: bestAway };
}

/** "today" / "tomorrow" / "in 4 days", from a day count. */
export function whenLabel(daysAway) {
    if (daysAway == null) return null;
    if (daysAway <= 0) return 'today';
    if (daysAway === 1) return 'tomorrow';
    if (daysAway < 7) return `in ${daysAway} days`;
    if (daysAway < 14) return 'next week';
    return `in ${Math.round(daysAway / 7)} weeks`;
}

/**
 * What is still waiting on the coach, most pressing first.
 *
 * Every one of these is a job with an obvious next action, which is the test
 * for whether it belongs here. "3 matches have no footage" is not a job — most
 * matches never will have — and a panel that lists the shape of the world
 * rather than the work teaches people to stop reading it.
 *
 * `today` is passed in rather than read from the clock, so a test can say what
 * day it is and so a fixture cannot go stale.
 */
export function seasonJobs({ matches = [], players = [], today = null } = {}) {
    const squad = players.filter((p) => p?.active !== false);
    const jobs = [];

    // Played and not published. The most pressing by a distance: the match is
    // over, the numbers exist, and every player is waiting on one press.
    const unpublished = matches.filter(
        (m) => !m?.finalized && m?.status === 'full_time',
    );
    if (unpublished.length) {
        jobs.push({
            id: 'publish',
            count: unpublished.length,
            title: `${count(unpublished.length, 'match', 'matches')} to publish`,
            note: 'Played and tagged. Nobody can see their report until you publish.',
            tab: 'matches',
        });
    }

    // A fixture whose date has passed and which nobody ever started tagging.
    // Only in the past: a match created for Saturday is not outstanding work on
    // Thursday, and listing it as such would make this panel noise every week.
    if (today) {
        const missed = matches.filter(
            (m) => !m?.finalized
                && (!m?.status || m.status === 'scheduled')
                && m?.date && m.date < today,
        );
        if (missed.length) {
            jobs.push({
                id: 'untagged',
                count: missed.length,
                title: `${count(missed.length, 'match', 'matches')} nobody tagged`,
                note: 'The date has passed and the tagging tool was never opened '
                    + 'for it. Minutes cannot be recovered later.',
                tab: 'matches',
            });
        }
    }

    // Players who have never signed in. They have a report and no way to read
    // it, which is the whole point of the portal.
    const unclaimed = squad.filter((p) => !p?.linkedUid);
    if (unclaimed.length) {
        jobs.push({
            id: 'invite',
            count: unclaimed.length,
            title: `${count(unclaimed.length, 'player')} not signed in`,
            note: 'They cannot see their own numbers until they accept an invite.',
            tab: 'roster',
        });
    }

    // A position is not an input to any figure — it is a heading over one — so
    // this is the least pressing of the four and sits last.
    const unplaced = squad.filter((p) => !positionOf(p?.position));
    if (unplaced.length) {
        jobs.push({
            id: 'position',
            count: unplaced.length,
            title: `${count(unplaced.length, 'player')} with no position`,
            note: 'The squad list groups by line, and these sit under '
                + '“No position set”.',
            tab: 'roster',
        });
    }

    return jobs;
}

// ------------------------------------------------------- where they play
//
// Four positions, and no more. Football names positions as finely as you like —
// left-back, holding midfielder, inside forward — and every extra name is
// another judgement a coach has to make about a sixteen-year-old who probably
// played three of them this season. Four is what the figures on these pages can
// actually be read against: a line of the team.
//
// The distinction that does real work is the first one. A goalkeeper covers
// about a third the ground of a midfielder, so their metres a minute belongs on
// a different scale, and the player table ranked them against each other with
// nothing saying so.
//
// `role` was taken. Everywhere else in this repo it means *access* — coach,
// tagger, player — and the two must not be confused: one decides what a person
// may read and the other where they stand on a pitch.
export const POSITIONS = [
    { id: 'gk', short: 'GK', label: 'Goalkeeper', plural: 'Goalkeepers' },
    { id: 'def', short: 'DEF', label: 'Defender', plural: 'Defenders' },
    { id: 'mid', short: 'MID', label: 'Midfielder', plural: 'Midfielders' },
    { id: 'fwd', short: 'FWD', label: 'Forward', plural: 'Forwards' },
];

const POSITION_BY_ID = new Map(POSITIONS.map((p) => [p.id, p]));

/** A position id from anywhere, or null. Anything unrecognised is unset. */
export function positionOf(value) {
    return POSITION_BY_ID.has(value) ? value : null;
}

/** "Midfielder", or null when nobody has said. Never a guess. */
export function positionLabel(value) {
    return POSITION_BY_ID.get(value)?.label ?? null;
}

export const isKeeper = (player) => positionOf(player?.position) === 'gk';

/**
 * A squad split into lines, in team-sheet order, keepers first.
 *
 * **Returns one unnamed group when nobody has a position**, which is the
 * important case rather than a fallback: a coach who has not filled this in
 * gets exactly the table they had before, in exactly the order they had it,
 * instead of a page that has quietly reorganised itself around a field they
 * have never seen. Headings appear the moment the first player is given a line
 * and not before.
 *
 * Players with no position go to a group at the end rather than into a line
 * somebody might read as a claim. `compare` orders within a group, so the
 * existing "most involved first" survives — a line is where you look, and the
 * standouts still rise inside it.
 */
export function groupByPosition(players, compare = null) {
    const list = [...(players || [])];
    const sorted = compare ? list.sort(compare) : list;

    if (!sorted.some((p) => positionOf(p.position))) {
        return [{ id: null, title: null, players: sorted }];
    }

    const groups = POSITIONS.map((pos) => ({
        id: pos.id,
        title: pos.plural,
        players: sorted.filter((p) => positionOf(p.position) === pos.id),
    }));
    groups.push({
        id: null,
        // Named as a question rather than as a category. "Other" would read as
        // a line of the team, and these are players nobody has got to yet.
        title: 'No position set',
        players: sorted.filter((p) => !positionOf(p.position)),
    });

    return groups.filter((g) => g.players.length);
}

export const STAT_TYPES = [
    { id: 'match', title: 'The match' },
    {
        id: 'possession',
        title: 'Possession',
        note: 'Thirds are each side’s share of its own time on the ball, '
            + 'not of the match — so the two rows do not add up to 100%.',
    },
    {
        id: 'phases',
        title: 'Phase of play',
        note: 'Each row is a share of that side’s own possessions, so the two '
            + 'columns do not add up. A possession counts by the furthest point '
            + 'it reached, so one that began in the final third counts as '
            + 'having got there. “Out from the back” is the exception: it is '
            + 'out of the possessions that started there, not out of all of them.',
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
        //
        // A comparison row survives on either side having one. The asymmetric
        // case is real: PPDA is null for a side that made no defensive actions
        // at all, and dropping the row would take the opposition's figure down
        // with it — which is the half a coach most wants when their own is
        // missing for that reason.
        if (row == null) continue;
        if (row.value == null && (!row.kind || row.themValue == null)) continue;
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

// ------------------------------------------------- comparing the two sides
//
// The pipeline has always measured both teams — `report_json.build_report_json`
// runs `team_stats` over TEAM_A and TEAM_B and publishes each — and until now
// the coach's report read only its own side. Twenty-five figures about the
// opposition, computed, published, and rendered nowhere. A possession of 58%
// says very little on its own and everything next to their 42%.
//
// Drawing the pair is where it gets easy to lie, because a bar is read before
// either number beside it. Three different claims live in the same list:
//
//   SHARE — a share of a continuous whole. Possession of time, and little else.
//           Their figure is the rest of ours, the boundary between them is the
//           entire story, and there is no count behind it to be short of.
//
//   COUNT — a share of some number of discrete events. Shots, tackles, corners.
//           Also a split — twelve shots to four really is three quarters of the
//           shots — but with a catch a share cannot express on its own: three
//           shots to one is also three quarters, and it is four shots in a
//           whole match. So a count carries whether its lead is bigger than
//           chance, and a bar that is not says so instead of looking decisive.
//
//   RATE  — each figure is a percentage of its own denominator. Pass accuracy
//           of 84% against 71% is not a 54/46 split of anything; the two do not
//           add up and must not be drawn as though they did. Each runs 0-100 on
//           its own scale.
//
//   LEVEL — a magnitude with no denominator at all. PPDA, and every shape
//           figure in metres. "38 metres wide" is not a share of anything and
//           neither side owns part of the other's, so the two are simply drawn
//           against whichever was larger. Splitting these is the worst of the
//           four mistakes available: a PPDA of 6.9 against 14.8 would read as
//           having had 32% of the pressing, which is not a quantity.
//
// `tally` in ui.js drew every row as a share, which is how the half-time page
// has been showing 84% against 71% as a near dead heat.

/** A share of a continuous whole — their figure is the rest of ours. */
export const SHARE = 'share';
/** A share of some number of discrete events, which may be too few to mean it. */
export const COUNT = 'count';
/** Each figure is a percentage of its own denominator, on a 0-100 scale. */
export const RATE = 'rate';
/** A magnitude with no denominator — metres, or passes per defensive action. */
export const LEVEL = 'level';

/**
 * How much of the track each side's bar fills, and whether to believe it.
 *
 * Returns `{ us, them, mode, tentative }`, or null when there is nothing honest
 * to draw — a pair with a side missing is not a pair, and filling the other
 * half with zero would report a nil return the pipeline never saw.
 *
 * `mode` is what the renderer needs: `'split'` (the two fill the track between
 * them, boundary moving) or `'opposed'` (each grows from a fixed centre against
 * its own full). Shares and counts are both splits; only their honesty differs.
 */
export function comparePair(us, them, kind = COUNT) {
    const a = num(us);
    const b = num(them);
    if (a == null || b == null) return null;

    if (kind === RATE) {
        // Percentages of their own denominators, so full is 100. Both arrive in
        // the unit the row prints — 84, not 0.84 — so the bar and the number
        // beside it cannot come apart.
        return {
            us: clampPct(a), them: clampPct(b), mode: 'opposed', tentative: false,
        };
    }

    if (kind === LEVEL) {
        // No denominator, so full is whichever side was larger and the other is
        // drawn against it. Negative levels are not a thing any of these
        // measure, and a magnitude of nothing on both sides is nothing to draw.
        const scale = Math.max(Math.abs(a), Math.abs(b));
        if (!(scale > 0)) return null;
        return {
            us: clampPct((Math.abs(a) / scale) * 100),
            them: clampPct((Math.abs(b) / scale) * 100),
            mode: 'opposed',
            tentative: false,
        };
    }

    const total = a + b;
    // Two figures that are supposed to be a whole and sum to nothing are not a
    // whole. Half and half would draw a dead heat nobody measured.
    if (!(total > 0)) return null;

    return {
        us: (a / total) * 100,
        them: (b / total) * 100,
        mode: 'split',
        tentative: kind === COUNT && insideNoise(a, b),
    };
}

/**
 * Is this lead smaller than the one chance alone would hand out?
 *
 * Each event is a coin toss between the two sides under a null of no
 * difference, so the count on one side has standard deviation sqrt(n)/2 and the
 * gap between them has sqrt(n). Two of those is the usual bar, which lands at
 * `|a - b| < 2*sqrt(n)`:
 *
 *     3 shots to 1     gap 2   against 4.0   — inside the noise
 *     12 to 4          gap 8   against 8.0   — just outside it
 *     30 to 10         gap 20  against 12.6  — outside it
 *
 * The same reasoning `xgCalibration` uses on marked shots and `pressingRead`
 * uses on scored blocks, and for the same reason: a coach shown 75% of the
 * shots will read it as dominance whether it was four shots or forty.
 */
/**
 * Which side, if either, has earned being called the better one.
 *
 * Lives here rather than in the renderer because it is a judgement and not a
 * layout: it decides whether a coach sees a green number, which is the closest
 * thing these pages have to an opinion. `tests/video.test.js` can only load
 * modules that import nothing, and a rule about when to state a verdict is
 * exactly the kind that should not go untested for want of a DOM.
 *
 * Returns 'ours-good', 'ours-bad' or null, matching the row classes.
 *
 * Two ways to earn nothing:
 *
 * - **the lead is inside the noise.** Handled upstream by `comparePair`, which
 *   is where the count that decides it lives.
 * - **the two print the same thing.** 10 possessions in 88 and 8 in 71 are
 *   different numbers and both print "11%". Colouring one of them puts a
 *   verdict beside its own disproof: the reader is looking at the evidence and
 *   it says the same thing twice.
 */
export function verdict(options = {}) {
    const { ours, theirs, usText, themText, better, tentative = false } = options;
    if (!better || tentative || ours == null || theirs == null) return null;

    const shown = (raw, formatted) => (formatted != null ? formatted : raw);
    if (shown(ours, usText) === shown(theirs, themText)) return null;
    if (ours === theirs) return null;

    const weLead = ours > theirs;
    return (better === 'high') === weLead ? 'ours-good' : 'ours-bad';
}

export function insideNoise(a, b) {
    const n = a + b;
    if (!(n > 0)) return true;
    return Math.abs(a - b) < 2 * Math.sqrt(n);
}

const clampPct = (value) => Math.max(0, Math.min(100, value));

/**
 * The video-derived figures for the coach's own side, typed and labelled.
 *
 * `confidence` carries the marks the caller worked out from the quality block —
 * passed in rather than computed here because grading them lives in db.js,
 * which this module cannot import and stay testable.
 *
 * Reads `teams.team_a` only, which is always the coach's own side.
 */
/**
 * One sentence about a student's afternoon, for the top of their own report.
 *
 * Here rather than in `player.js` because it is the sentence most likely to be
 * read twice by the person it is about, and every branch of it needs a test.
 *
 * Three things it has to get right, all of them learned the hard way:
 *
 * 1. **Every branch carries its own verb.** The phrases used to be spliced in
 *    after a fixed "You played", which produced *"You played an unused
 *    substitute."* — on the report of the one student most likely to reread it.
 * 2. **Zero minutes is two different afternoons.** `minutesFrom` rounds, so a
 *    substitute who came on with twenty-five seconds left comes back as 0 with
 *    a stint against their name, and a player who never left the bench comes
 *    back as 0 with none. Only `stints` separates them, and telling somebody
 *    who came on that they did not is the same failure as the one this whole
 *    sentence was rewritten for once already. See `whistleFrom`.
 * 3. **"1 minutes"** — the count was interpolated with a hard-coded plural.
 */
export function matchLine(report) {
    const bits = [];
    if (report?.goals) bits.push(count(report.goals, 'goal'));
    if (report?.assists) bits.push(count(report.assists, 'assist'));

    const minutes = knownMinutes(report) ? (report?.minutesPlayed ?? 0) : null;
    // Anything they did is evidence they were on, whatever the clock rounded to.
    const wasOn = (report?.stints || []).length > 0 || bits.length > 0;

    const played = minutes == null
        ? 'You played, but nobody kept the clock'
        : (minutes
            ? `You played ${count(minutes, 'minute')}`
            : (wasOn ? 'You were on for under a minute' : 'You did not get on'));

    const got = bits.length ? ` and got ${bits.join(' and ')}` : '';
    if (report?.scoreUs == null) return `${played}${got}.`;

    const result = report.scoreUs > report.scoreThem ? 'Won'
        : report.scoreUs < report.scoreThem ? 'Lost' : 'Drew';
    return `${result}. ${played}${got}.`;
}

/**
 * A tagged count, or an em dash when there is no log to have counted it.
 *
 * The scoreline is the loudest thing on both the match report and the half-time
 * page, and `aggregateMatch` initialises every count to zero — so a match nobody
 * ran the tablet for led with **0–0** in the largest type on the page. A match
 * that was filmed and not tagged is the ordinary case, not a corner one, and
 * nil-all is a result somebody could repeat out loud. Absent is not zero here
 * for the same reason it is not in `whistleFrom`, and louder.
 */
export const taggedCount = (count, log) => ((log || []).length ? (count ?? 0) : '—');

/**
 * The figures somebody tapped, as rows — for the match report and for the
 * half-time page, which used to build them separately and had drifted.
 *
 * The drift was not cosmetic. The coach's full post-match report carried nine
 * one-sided cards and the three-minute half-time read carried five two-sided
 * bars, so **the opponent's cards, the opponent's offsides and free kicks
 * appeared on the touchline page and were missing from the full report
 * altogether** — the fuller document said less than the triage one. Same shape
 * as `teamStatRows`, and for the same reason: one `pick` reads both sides, so
 * our column and theirs can never come off different fields.
 *
 * Throw-ins, goal kicks and out-of-bounds are tagged and deliberately left out
 * of both. They are the noise of a match rather than a read on it, and the
 * restart tags exist to tell the pipeline when the ball was dead.
 *
 * @param counts   `aggregateMatch(...).counts` — `{ us: {...}, them: {...} }`.
 * @param subs     Our substitutions. Never a pair: nobody tags the opposition's.
 * @param goals    Off at half-time, where the scoreline is already the biggest
 *                 thing on the page and that page's whole rule is not to report
 *                 what the coach watched happen.
 * @param dropEmpty Drop a row neither side registered. On at half-time, where
 *                 the reader is standing up; off in the report, where "we
 *                 conceded no corners" is a fact worth being able to look up.
 */
export function taggedTeamRows(counts, { subs = null, goals = true, dropEmpty = false } = {}) {
    const us = counts?.us || {};
    const them = counts?.them || {};

    const row = (label, pick, better) => {
        const usN = pick(us);
        const themN = pick(them);
        return {
            type: 'match', label, kind: COUNT, better,
            usN, themN, value: usN, themValue: themN,
        };
    };
    const of = (key) => (side) => side[key] ?? 0;

    const rows = [
        // Corners and free kicks are counted for whoever was awarded them;
        // fouls, cards and offside against whoever gave them away. The labels
        // have to carry that, because a pair of numbers cannot.
        ...(goals ? [row('Goals', of('goal'), 'high')] : []),
        row('Corners won', of('corner'), 'high'),
        row('Free kicks won', of('free_kick'), 'high'),
        row('Fouls committed', of('foul'), 'low'),
        row('Offside', of('offside'), 'low'),
        row('Cards', of('card'), 'low'),
    ].filter((r) => !dropEmpty || r.usN || r.themN);

    // Null, not zero, on their side: an untagged opposition substitution and no
    // opposition substitution are not the same thing, and this is the one row
    // here where the tablet was never asked the question.
    if (subs != null) {
        rows.push({
            type: 'match', label: 'Substitutions', kind: COUNT, better: null,
            usN: subs, themN: null, value: subs, themValue: null,
        });
    }
    return rows;
}

export function teamStatRows(cv, confidence = {}) {
    const ours = cv?.teams?.team_a;
    if (!ours) return [];

    // The opposition's copy of the same measurements. Absent on a report
    // published before both sides were carried, and absent is not zero: every
    // row below degrades to a one-sided figure rather than drawing a bar that
    // says the other team did nothing.
    const theirs = cv?.teams?.team_b || null;

    const quality = cv.quality || {};
    const events = confidence.events || null;
    const possession = confidence.possession || null;
    const trust = xgTrust(cv.calibrationErrorM);

    /**
     * One comparable row.
     *
     * `pick` reads a figure off one side, so the same function reads both and
     * the two can never drift apart — the failure that matters here is not a
     * wrong number, it is our column and their column being taken from
     * different fields.
     */
    const row = (type, label, kind, pick, options = {}) => {
        const { format = (v) => v, better = null, confidence: mark = events,
                explained = false } = options;
        const usN = pick(ours);
        const themN = theirs ? pick(theirs) : null;
        return {
            type, label, kind, better, explained, confidence: mark,
            value: usN == null ? null : format(usN),
            themValue: themN == null ? null : format(themN),
            usN, themN,
        };
    };

    // Every figure a row carries is in the unit the row prints — 84 for a
    // percentage, not 0.84 — so a bar drawn from it can never disagree with the
    // number written beside it. The pipeline speaks in fractions; this is where
    // that stops.
    const asPct = (fraction) => (fraction == null ? null : fraction * 100);
    const pctText = (value) => `${Math.round(value)}%`;

    // A breakdown is only worth a percentage if it is a share of something this
    // run actually counted, and it has to be each side's own total — dividing
    // their forward passes by our attempts would be a number about nobody.
    const ofAttempted = (side, count) => {
        const total = side.passes_attempted || 0;
        return total && count != null ? asPct(count / total) : null;
    };

    // The funnel, read off the counts the pipeline publishes rather than off
    // percentages it has already worked out. Counts are what survive being
    // combined and what let a browser say a figure rests on too few
    // possessions to mean anything.
    const reached = (side, third) => {
        const phase = side.phase_of_play;
        const total = phase?.total || 0;
        const n = phase?.reached?.[third];
        return total && n != null ? asPct(n / total) : null;
    };
    const endedIn = (side, how) => {
        const phase = side.phase_of_play;
        const total = phase?.total || 0;
        const n = phase?.ended?.[how];
        return total && n != null ? asPct(n / total) : null;
    };
    // Out of the possessions that *started* at the back — not out of all of
    // them. A side that never won the ball in its own third did not fail to
    // play out from it, so this is null there rather than zero.
    const outFromTheBack = (side) => {
        const phase = side.phase_of_play;
        const started = phase?.started?.defensive || 0;
        const escaped = phase?.escaped_defence;
        return started && escaped != null ? asPct(escaped / started) : null;
    };
    const accuracyIn = (side, third) => {
        const phase = side.phase_of_play;
        const attempted = phase?.passes?.[third] || 0;
        const completed = phase?.passes_completed?.[third];
        return attempted && completed != null
            ? asPct(completed / attempted) : null;
    };

    return [
        {
            // The label carries the denominator, because the denominator
            // changed. With a tagged log the dead time is out of it and this is
            // possession of a ball that was in play; without one it is the
            // older, weaker figure and must not claim otherwise.
            ...row('possession',
                possessionIsInPlay(quality) ? 'Possession, ball in play' : 'Possession',
                SHARE, (t) => t.possession_pct,
                { format: share, better: 'high', confidence: possession }),
        },
        row('possession', 'Touches', COUNT, (t) => t.touches, { better: 'high' }),
        row('possession', 'Carries', COUNT, (t) => t.carries, { better: 'high' }),

        // Relabelled from "in your own third" now that the row has two owners:
        // each figure is that side's share of its own time on the ball, in its
        // own end of the pitch, so a possessive would be pointing at one of two
        // teams and the reader would have to guess which.
        row('possession', 'Own third', RATE, (t) => asPct(t.territory?.defensive),
            { format: pctText, confidence: possession, explained: true }),
        row('possession', 'Middle third', RATE, (t) => asPct(t.territory?.middle),
            { format: pctText, confidence: possession, explained: true }),
        row('possession', 'Opposition third', RATE, (t) => asPct(t.territory?.attacking),
            { format: pctText, confidence: possession, explained: true }),

        // --- phase of play ---
        //
        // The funnel. `territory` above says where the ball was; these say what
        // the side was trying to do there and whether it came off, which is the
        // distinction that made phase-of-play worth building separately rather
        // than as another slice of the same time.
        //
        // Every share is out of that side's own possessions, never out of the
        // match: a team with the ball twice as often would otherwise look twice
        // as good at moving it.
        row('phases', 'Possessions', COUNT, (t) => t.phase_of_play?.total || null,
            { confidence: possession }),
        row('phases', 'Reached midfield', RATE, (t) => reached(t, 'middle'),
            { format: pctText, better: 'high', explained: true,
              confidence: possession }),
        row('phases', 'Reached the final third', RATE, (t) => reached(t, 'attacking'),
            { format: pctText, better: 'high', explained: true,
              confidence: possession }),
        row('phases', 'Ended in a shot', RATE, (t) => endedIn(t, 'shot'),
            { format: pctText, better: 'high', explained: true,
              confidence: possession }),
        // Its own denominator, and the row that actually answers "can we play
        // out from the back". The whole-funnel share above is flattered by
        // every possession that began in midfield already.
        row('phases', 'Out from the back', RATE, outFromTheBack,
            { format: pctText, better: 'high', explained: true,
              confidence: possession }),
        // The point of the whole feature. A side at 92% in its own third and
        // 54% in the opposition's is a normal, healthy team; one overall figure
        // of 72% hides both halves, and 60% at the back is a problem nobody
        // would otherwise see.
        row('phases', 'Passing at the back', RATE, (t) => accuracyIn(t, 'defensive'),
            { format: pctText, better: 'high', explained: true }),
        row('phases', 'Passing in midfield', RATE, (t) => accuracyIn(t, 'middle'),
            { format: pctText, better: 'high', explained: true }),
        row('phases', 'Passing up front', RATE, (t) => accuracyIn(t, 'attacking'),
            { format: pctText, better: 'high', explained: true }),

        row('passing', 'Passes attempted', COUNT, (t) => t.passes_attempted || null,
            { better: 'high' }),
        row('passing', 'Pass accuracy', RATE, (t) => asPct(t.pass_accuracy),
            { format: pctText, better: 'high' }),
        row('passing', 'Progressive passes', COUNT, (t) => t.progressive_passes,
            { better: 'high' }),
        // How direct a side was, which is the question the buckets exist to
        // answer and which the raw counts do not. Shares of each side's own
        // attempts, so a side that passed less does not look less direct.
        // Neither direction is good: a long ball is a route one team chose.
        row('passing', 'Played forward', RATE,
            (t) => ofAttempted(t, t.passes_by_direction?.forward), { format: pctText }),
        row('passing', 'Played long', RATE,
            (t) => ofAttempted(t, t.passes_by_length?.long), { format: pctText }),
        row('passing', 'Switches of play', COUNT, (t) => t.switches, { better: 'high' }),

        row('attacking', 'Final-third entries', COUNT, (t) => t.final_third_entries,
            { better: 'high' }),
        row('attacking', 'Entries into the box', COUNT, (t) => t.box_entries,
            { better: 'high' }),
        row('attacking', 'Crosses', COUNT, (t) => t.crosses, { better: 'high' }),
        row('attacking', 'Shots', COUNT, (t) => t.shots, { better: 'high' }),
        row('attacking', 'Shots on target', COUNT, (t) => t.shots_on_target,
            { better: 'high' }),
        // Withheld, not zeroed, when the calibration is too loose to support it.
        // A team total averages a lot of per-shot noise away, which is why it
        // survives a band that per-shot xG does not — but not every band.
        row('attacking', 'Expected goals', COUNT,
            (t) => (trust === 'none' ? null : t.xg),
            { format: (v) => v.toFixed(2), better: 'high' }),

        row('defending', 'Tackles', COUNT, (t) => t.tackles, { better: 'high' }),
        row('defending', 'Interceptions', COUNT, (t) => t.interceptions,
            { better: 'high' }),
        row('defending', 'Recoveries', COUNT, (t) => t.recoveries, { better: 'high' }),
        row('defending', 'Ground duels', COUNT, (t) => t.duels, { better: 'high' }),
        // Passes allowed per defensive action. Not a share of anything — the
        // two sides do not divide a fixed quantity of pressing between them —
        // so it is drawn against whichever was larger. Fewer means pressing
        // harder, so the low side is the good one and the bar has to be told.
        row('defending', 'PPDA', LEVEL, (t) => t.ppda,
            { format: (v) => v.toFixed(1), better: 'low' }),
        // The giveaways that turn straight into a chance against you. A single
        // turnover count cannot say this, which is why it is counted by third.
        row('defending', 'Lost in own third', COUNT,
            (t) => t.turnovers_by_third?.defensive, { better: 'low' }),

        ...shapeStatRows(ours.shape, theirs?.shape, cv.calibrationErrorM),
    ];
}

/**
 * How spread out each side played, in metres.
 *
 * `report_json` used to publish one shape built from every track on the pitch,
 * both teams and the referee together, and label it Team A's. It is now built
 * per team, and both are read here — a side that was 8m narrower than the
 * opposition is a fact about the match, where "38m wide" on its own is a number
 * with nothing to lean on.
 *
 * Empty until a calibration exists, which is every run today — width in metres
 * is not something a pixel can answer. Their shape may be absent while ours is
 * not, and then the rows draw one-sided rather than against a zero.
 *
 * None of these is coloured good or bad. A compact side is well-drilled or it
 * is pinned in its own half, and no number here can tell the difference.
 */
export function shapeStatRows(shape, theirShape, calibrationErrorM) {
    if (!shape || shape.width_m == null) return [];
    const band = shapeConfidence(calibrationErrorM);
    const metres = (value) => `${Math.round(value)}m`;

    const row = (label, key) => {
        const usN = shape[key] ?? null;
        const themN = theirShape?.[key] ?? null;
        return {
            type: 'shape', label, kind: LEVEL, better: null, explained: true,
            confidence: band,
            value: usN == null ? null : metres(usN),
            themValue: themN == null ? null : metres(themN),
            usN, themN,
        };
    };

    return [
        row('Average width', 'width_m'),
        row('Average depth', 'depth_m'),
        // Mean distance from each player to their own team's centre.
        row('Compactness', 'compactness_m'),
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

// -------------------------------------------------- two records, one column
//
// Phase 11 opens by saying the review tool is "pre-populated with Phase 3's
// live-tagged events and subs, plus Phase 10's CV candidates — the reviewer's
// job is verifying/correcting/filling gaps, not labeling from scratch". Half of
// that was true. The list held the pipeline's candidates and nothing else; the
// tagged log sat in a different strip on a different part of the page, and a
// reviewer wanting to know what a human had said about 34:11 had to scroll away
// from the row they were judging and find it by eye.
//
// That is the wrong shape for the actual work. Judging a candidate is almost
// always a question about context — *was the ball even in play, and what had
// just happened?* — and the log is the only record that can answer it. So the
// two records are merged into one column, in time order.
//
//     What a tagged row is, and what it is not.
//
// It is a human's own record of the match, made at the time. There is nothing
// for a reviewer to confirm about it and it carries no verdict buttons. It is
// **never** a candidate: it does not enter precision, it does not enter recall,
// and it does not move the "checked so far" count. A tagged corner is not
// something the pipeline claimed, so counting it as agreement would inflate
// every figure on the scorecard with work nobody did.
//
// The one place the log does more than sit there is a **goal it tapped that no
// candidate stands near**. That is a miss the pipeline made and a human already
// proved, and until now recording it meant typing a clock into a text box from
// memory. One tap is the head start the roadmap is asking for.

export const FROM_VIDEO = 'video';
export const FROM_TAGGED = 'tagged';

// The two ways to work through the feed.
export const BY_CLOCK = 'clock';
export const BY_DOUBT = 'doubt';

/**
 * Reorder the merged feed, without changing what is in it.
 *
 * The Testing Strategy has claimed since it was written that this tool "sorts by
 * lowest confidence first, so a human's limited review time goes to what's
 * likely wrong instead of skimming everything uniformly". It never did — the
 * feed is chronological, and has been since it existed.
 *
 * Chronological is not a bug, though, which is why this is a choice rather than
 * a correction. Every row seeks the video, and going in match order means
 * scrubbing forward through a half once; going by doubt means jumping back and
 * forth across ninety minutes for every verdict. That is the cost the original
 * claim never accounted for.
 *
 * **`BY_DOUBT` buys a better use of an hour and pays for it in what the numbers
 * then mean.** Checking the least sure events first is the fastest way to find
 * what the detector gets wrong, and it makes the reviewed set a deliberately
 * pessimistic sample — precision measured over it is a floor, not an estimate.
 * `reviewScore` already reports "out of the N you have checked"; `orderCaveat`
 * below is what says the N was not drawn evenly.
 *
 * Tagged rows have no confidence — the log is a person, not a detector, and
 * there is no doubt attached to a tap. They keep their place on the clock
 * relative to each other and sort after the candidates, so the other record
 * stays readable rather than being shuffled into a ranking it is not part of.
 */
export function orderFeed(feed, order = BY_CLOCK) {
    const items = [...(feed || [])];
    if (order !== BY_DOUBT) return items;

    const doubt = (item) => (
        item.source === FROM_VIDEO && item.event?.confidence != null
            ? item.event.confidence
            : Infinity
    );
    return items.sort(
        (a, b) => doubt(a) - doubt(b) || a.clockS - b.clockS,
    );
}

/**
 * What reading in doubt order does to the numbers underneath.
 *
 * Only said while it is true, and it is about the sample rather than the tool:
 * the scorecard is unchanged, what changed is how the events in it were chosen.
 */
export function orderCaveat(order, checked = 0) {
    if (order !== BY_DOUBT || !checked) return '';
    return 'You are checking the least sure first, so these are the hardest '
        + `${checked} the video found — precision here is a floor, not an average.`;
}

/**
 * How close a tagged entry has to sit before it is worth showing on a
 * candidate's own row.
 *
 * Six seconds is a restart: the whistle, the walk, the throw. Wider and every
 * pass in a busy passage picks up a foul from further away than it was caused
 * by; narrower and the throw-in itself — the case this exists for, because it
 * is the touch the detector gets wrong most — stops being labelled.
 */
export const NEARBY_TAG_S = 6.0;

/**
 * How far from a tagged goal a candidate shot may sit and still be that goal.
 *
 * Mirrors `GOAL_WINDOW_S` in `cv/reconcile.py`, for the same reason: a tagger
 * taps a goal after the ball crosses the line and usually after the celebration
 * has started. The two numbers have to agree, or the browser would offer to
 * record a miss for a goal the pipeline's own reconciliation counted as found.
 */
export const GOAL_PAIR_S = 15.0;

/** How close a recorded miss has to be to count as the same miss. */
const SAME_MISS_S = 5.0;

const PERIOD_KIND = 'period';

/**
 * The pipeline's candidates and the tagged log, merged in time order.
 *
 * `events` are in **video** seconds and the log in **match clock** seconds, so
 * `clock` does the conversion once here rather than at every comparison — the
 * same rule `cv/phases.py` keeps, and it matters more than usual because the
 * windows above are seconds wide and a half-time interval is fifty times that.
 *
 * Period markers are left out. `halftime` and `kickoff_2nd` share a clock
 * reading with everything else in the interval, so a row for one would claim a
 * position in the order that it does not have.
 */
export function reviewFeed(events, log, options = {}) {
    const {
        clock = matchClockMap({}),
        missed = [],
        nearbyS = NEARBY_TAG_S,
        goalPairS = GOAL_PAIR_S,
    } = options;

    const candidates = (events || []).map((event) => {
        const { clockS, period } = clock.toClock(event.timestampS || 0);
        return { source: FROM_VIDEO, event, id: event.id, type: event.type, clockS, period };
    });

    const tagged = (log || [])
        .filter((e) => e && e.kind !== PERIOD_KIND && typeof e.matchClockS === 'number')
        .map((entry, index) => ({
            source: FROM_TAGGED,
            entry,
            // Not the log document's own id: an entry has one and a sub does
            // not, and a row keyed on undefined would collide with every other
            // sub in the half.
            id: `tag:${index}`,
            type: entry.kind === 'sub' ? 'sub' : entry.type,
            clockS: entry.matchClockS,
            period: null,
        }));

    // Only real ball events give a candidate its context. A substitution says
    // the tagger was busy, not that the ball was anywhere in particular.
    const context = tagged.filter((item) => item.type !== 'sub');
    for (const item of candidates) {
        const near = nearest(context, item.clockS, nearbyS);
        item.nearbyTag = near
            ? { type: near.type, clockS: near.clockS, gapS: near.clockS - item.clockS }
            : null;
    }

    // A goal the log recorded and no candidate stands near. `shot` rather than
    // `goal` because the pipeline's vocabulary has no goal in it — a goal is a
    // shot whose outcome was one — and **any** shot nearby counts as found,
    // even one scored as saved. Recall asks whether the moment was found, which
    // is the same rule `reviewScore` applies to a retyped event.
    const shots = candidates.filter((item) => item.event.type === 'shot');
    for (const item of tagged) {
        item.suggestion = null;
        if (item.type !== 'goal') continue;
        if (nearest(shots, item.clockS, goalPairS)) continue;
        item.suggestion = {
            type: 'shot',
            clockS: item.clockS,
            recorded: (missed || []).some(
                (m) => m && m.type === 'shot'
                    && Math.abs((m.clockS ?? 0) - item.clockS) <= SAME_MISS_S,
            ),
        };
    }

    // Ties go to the video. The tagger taps after the ball crosses the line, so
    // a shot and the goal it became read in the order they happened.
    const rank = (item) => (item.source === FROM_VIDEO ? 0 : 1);
    return [...candidates, ...tagged]
        .sort((a, b) => a.clockS - b.clockS || rank(a) - rank(b));
}

function nearest(items, clockS, windowS) {
    let best = null;
    for (const item of items) {
        const gap = Math.abs(item.clockS - clockS);
        if (gap > windowS) continue;
        if (!best || gap < Math.abs(best.clockS - clockS)) best = item;
    }
    return best;
}

const CONFIRMED_STATUS = 'confirmed';
const REJECTED_STATUS = 'rejected';
const EDITED_STATUS = 'edited';

/**
 * Has anybody actually said whether this event happened?
 *
 * `byEvent` holds three separate answers under one key: whether the pipeline
 * was right to call this a shot (`status`), what the shot did (`result`), and
 * whether it was headed (`header`). Only the first is a verdict. An entry
 * carrying just a result is a coach saying "that one was saved" — a statement
 * about a shot they have not yet agreed was a shot.
 *
 * Every count of how much has been reviewed goes through here, because the
 * alternative is what it replaced: four places each deciding for themselves
 * what an entry means, and a scorecard disagreeing with the line above it.
 */
export const hasVerdict = (decision) => Boolean(decision?.status);

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
        if (!hasVerdict(decision)) {
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
    // Shots the coach said were not shots. Dropped rather than kept with a null
    // xG, because unlike an unscorable header — which happened and cannot be
    // scored — a rejected shot did not happen, and a dot on a shot map is a
    // claim that it did.
    const gone = new Set();
    for (const row of rows || []) {
        if (row?.id == null) continue;
        if (!row.counted) { gone.add(row.id); continue; }
        // `counted` as well as `header`, so this and `headerCorrection` are
        // always describing the same set of shots. A map that has quietly
        // corrected one more shot than the sentence under it claims is the kind
        // of half-a-goal discrepancy nobody ever tracks down.
        if (row.header) byId.set(row.id, row.xg);
    }
    if (!byId.size && !gone.size) return marks || [];

    return (marks || [])
        .filter((mark) => !gone.has(mark.event_id))
        .map((mark) => (byId.has(mark.event_id)
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

// ------------------------------------------ what has actually reached the server
//
// The tablet tags a match at a field, and `firebase-init.js` turns on
// persistent local caching so a tap survives a dead connection. That part
// works. What did not work was telling the person holding the tablet, because
// the indicator read `navigator.onLine` — which reports a **link**, not a
// reachable server. A school Wi-Fi with a captive portal or a dead uplink is
// online by that definition, and the dot said "Saved" while nothing had been
// saved.
//
// Firestore knows the real answer and always did. Every snapshot carries
// `metadata.hasPendingWrites` — this document has local edits the server has
// not acknowledged — and `metadata.fromCache`, meaning the read never reached
// the server. Counting the first is an exact count of what is still on the
// tablet only.
//
//     Two signals, and disconnected wins.
//
// `fromCache` and `navigator.onLine` can disagree, and when they do the
// pessimistic one is taken. Claiming a connection that is not there is the
// error that loses a match; claiming none while one exists costs a person
// twenty seconds of looking at their phone.

/**
 * The sync indicator's state, from what Firestore reports.
 *
 * `pending` is how many entries carry unacknowledged local writes, `fromCache`
 * is whether the last snapshot was served without reaching the server.
 *
 * Returns a `tone` of `'ok'`, `'waiting'` or `'stale'`, a short label for the
 * chip and a sentence for its title. Zero pending while disconnected is a
 * genuinely reassuring answer and is worded like one: everything tapped so far
 * *is* on the server, because that is exactly what an acknowledged write means.
 */
export function syncState({ pending = 0, fromCache = false, online = true } = {}) {
    const connected = !fromCache && online !== false;

    // Nothing heard yet. Explicitly its own state rather than falling into
    // "everything is on the server", which is the one sentence here that must
    // never be said on no evidence.
    if (pending == null) {
        return {
            tone: 'stale',
            label: 'Checking',
            detail: 'Still finding out what has reached the server.',
        };
    }

    if (pending > 0) {
        return {
            tone: 'waiting',
            label: `${pending} waiting`,
            detail: `${pending} tap${pending === 1 ? '' : 's'} ${
                pending === 1 ? 'is' : 'are'} saved on this tablet and ${
                pending === 1 ? 'has' : 'have'} not reached the server yet. `
                + `Keep this page open until ${pending === 1 ? 'it does' : 'they do'}`
                + ` — ${pending === 1 ? 'it uploads' : 'they upload'} on `
                + `${pending === 1 ? 'its' : 'their'} own as soon as there is a `
                + 'connection.',
        };
    }

    if (!connected) {
        return {
            tone: 'stale',
            label: 'No connection',
            detail: 'Everything tapped so far is on the server. New taps will '
                + 'wait on this tablet until the connection is back.',
        };
    }

    return {
        tone: 'ok',
        label: 'Saved',
        detail: 'Every tap has reached the server.',
    };
}

/**
 * Whether it is safe to walk away from this tablet.
 *
 * The one question the indicator exists to answer, split out because the exit
 * path asks it too and asking it twice in two different ways is how the two
 * answers end up disagreeing.
 *
 * A missing or unknown count is **not** safe. Answering "yes, go ahead" on no
 * information is the same failure the `navigator.onLine` indicator made, in the
 * one place where the cost of it is a lost match.
 */
export function safeToClose(state) {
    return state?.pending === 0;
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
    if (!segments?.length) return null;

    const clock = clockOf(options);
    const minute = (s) => Math.max(0, Math.round(clock.toClock(s).clockS / 60));
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

// ------------------------------------------------ the football either side of
//                                                   a substitution
//
// The stats catalog has asked for this since it was written — *"substitution
// impact: team stats in the window before vs. after each sub (exact sub timing
// comes straight from Phase 3's live log)"* — and it is the one thing on that
// list that pays the tablet back. Somebody stands in the rain for ninety
// minutes tapping subs into an iPad; until now the only thing those taps bought
// was a minutes-played column.
//
//     What this is not.
//
// It is **not** substitution impact, and the block does not use the word. A
// coach makes a change *because* of what is already happening: a side being
// overrun brings on a defender, a side chasing brings on a forward. The change
// and the reason for it arrive together and nothing here can pull them apart.
// Add to that the opponent, who also made changes and whose changes are in
// nobody's log, and the scoreline, which moves on its own and changes how both
// sides play.
//
// So every figure below answers "what did the next ten minutes look like next
// to the last ten", and the note says that in as many words. A block that
// promised impact would be promising a causal claim off a sample of one.
//
//     Three ways a window lies, and what is done about each.
//
// **Unequal spans.** Nine minutes of football has more of everything in it than
// six, so counts either side must cover the same length of clock. Both windows
// are cut to the shorter of the two, rather than converted to per-minute rates
// — `insideNoise` needs counts, and a rate would hide that the comparison rests
// on eleven events.
//
// **Another change inside the window.** If two subs come six minutes apart, the
// ten minutes after the first are mostly the ten minutes around the second, and
// reporting both would serve the same passage of football up twice under two
// different headings. Windows are clipped at the neighbouring change.
//
// **Footage that stopped.** Events only exist where the pipeline was looking.
// A window running past the end of a clip finds nothing, which is a fact about
// the clip and reads on screen as a team that stopped playing. Clipped to the
// processed window, and dropped if what is left is too short to say anything.
//
//     Half-time is not a window, it is a wall.
//
// A change at the interval is the most common change there is, and the least
// measurable one: what sits between the two windows is not the substitution, it
// is fifteen minutes and a team talk. Those changes are listed — a coach should
// see that they happened — and deliberately not scored.
//
// The same wall clips ordinary windows. Ten minutes either side of a change on
// 43 minutes would otherwise compare the end of one half against the start of
// the next, with the oranges in the middle.

/**
 * When the match ended, or null when nothing knows.
 *
 * `matchEndS` comes off the tag log, and a match nobody tagged has one of
 * zero — which is not a final whistle, it is the absence of one. Taken at face
 * value it is a whistle before kick-off: every substitute who came off is
 * dropped for having left "after" it, and every window is clipped to nothing.
 */
const whistle = (value) => (num(value) > 0 ? num(value) : null);

/** How far apart two roster moves can be and still be one decision. */
export const CHANGE_GROUP_S = 90;

/** How much football either side of a change, before anything clips it. */
export const CHANGE_WINDOW_S = 600;

/**
 * The shortest window worth counting.
 *
 * Four minutes, set from what is in one: at the event rates this pipeline
 * produces a side touches the ball a couple of dozen times, which is already
 * near the floor at which `insideNoise` will ever answer anything but "cannot
 * tell". Below it the honest output is a blank, not a percentage.
 */
export const MIN_CHANGE_WINDOW_S = 240;

/**
 * Did our share of the ball really move, or is this the sample size talking?
 *
 * A two-proportion test at the same two-sigma bar `insideNoise` uses, and it
 * has to be that rather than `insideNoise` itself: the claim on screen is about
 * a **share before against a share after**, and `insideNoise` answers a
 * different question — whether one window's two sides differ. Comparing our own
 * count either side would come closer, and would still be wrong, because the
 * total volume of events is not fixed between the two windows either.
 *
 * The bar this sets is high, and knowing how high is the point of writing it
 * down. At an even split, the smallest swing that clears it:
 *
 *      20 events a window   32 points
 *      60                   18
 *     100                   14
 *     150                   12
 *     400                    7
 *
 * Ten minutes of football is somewhere in the low hundreds of on-ball events
 * per side at best, so **this will call most changes a draw, and it is right
 * to.** A tool that flagged a ten-point swing off a hundred events would be
 * pointing a coach at a coin toss and putting a teenager's name on it.
 */
export function shareShifted(before, after) {
    const n1 = (before?.us ?? 0) + (before?.them ?? 0);
    const n2 = (after?.us ?? 0) + (after?.them ?? 0);
    if (!n1 || !n2) return false;

    const p1 = before.us / n1;
    const p2 = after.us / n2;
    const pooled = (before.us + after.us) / (n1 + n2);
    const se = Math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2));
    // A side that had every event in both windows has no spread to divide by,
    // and no difference either.
    if (!(se > 0)) return false;
    return Math.abs(p1 - p2) >= 2 * se;
}

/**
 * Every moment the eleven changed, from the roster's own stints.
 *
 * Built from stints rather than from the `sub` entries in the log because the
 * stints are the reconciled record — `onPitchAt` and `minutesFrom` already read
 * them, and a player added to a match after the fact has stints without ever
 * having a log entry.
 *
 * A kick-off is not a change, so `inS === 0` is skipped; neither is the final
 * whistle, so an `outS` at `matchEndS` is skipped too. Moves inside
 * `CHANGE_GROUP_S` of each other are one change: a double substitution goes on
 * at one stoppage and arrives as two taps a few seconds apart, and counting it
 * twice would put two headings over one passage of football.
 *
 * Grouping measures from the group's first move, never from its latest, so a
 * steady trickle of changes a minute apart cannot chain into one group that
 * swallows a half.
 */
export function substitutionChanges(roster, options = {}) {
    const { groupS = CHANGE_GROUP_S } = options;
    // Zero is what `matchEndS` is on a match nobody tagged, and it is not the
    // final whistle — it is the absence of one. Taken literally it deletes
    // every player who came off, because no `outS` is below it.
    const matchEndS = whistle(options.matchEndS);

    const moves = [];
    for (const entry of roster || []) {
        const name = entry.playerName || 'Unnamed';
        for (const stint of entry.stints || []) {
            const inS = num(stint?.inS);
            const outS = num(stint?.outS);
            if (inS != null && inS > 0) moves.push({ clockS: inS, name, on: true });
            if (outS != null && (matchEndS == null || outS < matchEndS)) {
                moves.push({ clockS: outS, name, on: false });
            }
        }
    }
    moves.sort((a, b) => a.clockS - b.clockS);

    const changes = [];
    for (const move of moves) {
        let group = changes[changes.length - 1];
        if (!group || move.clockS - group.clockS > groupS) {
            group = { clockS: move.clockS, on: [], off: [] };
            changes.push(group);
        }
        (move.on ? group.on : group.off).push(move.name);
    }
    return changes;
}

/**
 * What the video found either side of each change.
 *
 * `events` is the published `cvEvents.events` list, in **video** seconds;
 * `roster` carries stints on the **match clock**. `clock` relates them, once,
 * here — the rule this module keeps everywhere the two meet.
 *
 * Returns null when nobody was substituted, so a caller hides the block rather
 * than heading an empty list.
 *
 * **Nothing is scored without a placeable clock.** Second-half stints are match
 * minutes and second-half events are video minutes, and without the second-half
 * anchor the offset relates them wrongly by however long the interval lasted —
 * ten to fifteen minutes, in the direction that quietly slides a window off the
 * football it claims to describe. A report on a first-half clip needs no anchor
 * and is scored normally, which is the case the half-time page cares about.
 *
 * Dead-ball events are left out, matching `possessionIsInPlay`: the share below
 * is a share of football, and a throw-in taken twice is not a side on top.
 */
export function substitutionWindows(roster, events, options = {}) {
    const {
        clock = matchClockMap({}),
        period = null,
        window = null,
        truncated = false,
        windowS = CHANGE_WINDOW_S,
        minWindowS = MIN_CHANGE_WINDOW_S,
    } = options;

    const matchEndS = whistle(options.matchEndS);
    const changes = substitutionChanges(roster, { matchEndS });
    if (!changes.length) return null;

    const placeable = Boolean(clock.knowsSecondHalf) || period === FIRST_HALF;
    const breakAt = clock.knowsSecondHalf ? clock.secondHalfClockS : null;

    // The footage, on the match clock. A window is only as long as the part of
    // it somebody actually looked at.
    const seenFrom = num(window?.start_s) == null
        ? null : clock.toClock(window.start_s).clockS;
    const seenTo = num(window?.end_s) == null
        ? null : clock.toClock(window.end_s).clockS;

    const tally = (from, to) => {
        const out = {
            us: 0, them: 0, shots: 0, shotsAgainst: 0, xg: 0, xgAgainst: 0,
        };
        for (const event of events || []) {
            if (event?.inPlay === false) continue;
            const at = num(event?.timestampS);
            if (at == null) continue;
            const read = clock.toClock(at);
            if (read.period === HALF_TIME) continue;
            if (!(read.clockS >= from && read.clockS < to)) continue;

            const ours = event.team === 'team_a';
            if (!ours && event.team !== 'team_b') continue;

            if (ours) out.us += 1; else out.them += 1;
            if (event.type === 'shot') {
                if (ours) {
                    out.shots += 1;
                    out.xg += num(event.xg) || 0;
                } else {
                    out.shotsAgainst += 1;
                    out.xgAgainst += num(event.xg) || 0;
                }
            }
        }
        return out;
    };

    const share = (w) => (w.us + w.them > 0 ? w.us / (w.us + w.them) : null);

    const rows = changes.map((change, i) => {
        const at = change.clockS;
        const prev = i > 0 ? changes[i - 1].clockS : null;
        const next = i < changes.length - 1 ? changes[i + 1].clockS : null;
        const atBreak = breakAt != null && Math.abs(at - breakAt) <= CHANGE_GROUP_S;

        const halfStart = breakAt != null && at >= breakAt ? breakAt : 0;
        const halfEnd = breakAt != null && at < breakAt
            ? breakAt : (matchEndS ?? Infinity);

        const lo = Math.max(
            at - windowS, halfStart,
            prev ?? -Infinity, seenFrom ?? -Infinity,
        );
        const hi = Math.min(
            at + windowS, halfEnd,
            next ?? Infinity, seenTo ?? Infinity,
        );
        const spanS = Math.min(at - lo, hi - at);

        const row = {
            clockS: at,
            on: change.on,
            off: change.off,
            atBreak,
            spanS: spanS > 0 ? spanS : 0,
            scored: false,
            reason: null,
            before: null,
            after: null,
            shareBefore: null,
            shareAfter: null,
            swing: null,
            tentative: true,
        };

        // Ordered by which answer is the more useful to read. "It was at the
        // break" explains itself; "no clock" is a field the coach can fill in;
        // "another change" and "the footage ends" are facts about this match.
        if (!placeable) row.reason = 'clock';
        else if (atBreak) row.reason = 'break';
        else if (spanS < minWindowS) {
            const crowded = (prev != null && at - prev < minWindowS)
                || (next != null && next - at < minWindowS);
            // Only blame the footage where the footage is genuinely the
            // tighter limit. A clip that runs to the final whistle ends at the
            // same second the match does, and "the processed footage runs out"
            // would send a coach looking for a problem with their video.
            const cut = (seenFrom != null && seenFrom > halfStart
                    && at - seenFrom < minWindowS)
                || (seenTo != null && seenTo < halfEnd && seenTo - at < minWindowS);
            row.reason = crowded ? 'crowded' : (cut ? 'footage' : 'edge');
        }
        if (row.reason) return row;

        row.scored = true;
        row.spanS = spanS;
        row.before = tally(at - spanS, at);
        row.after = tally(at, at + spanS);
        row.shareBefore = share(row.before);
        row.shareAfter = share(row.after);
        row.swing = row.shareBefore == null || row.shareAfter == null
            ? null : row.shareAfter - row.shareBefore;
        row.tentative = !shareShifted(row.before, row.after);
        return row;
    });

    return {
        rows,
        placeable,
        truncated: Boolean(truncated),
        scored: rows.filter((r) => r.scored).length,
        atBreak: rows.filter((r) => r.atBreak).length,
    };
}

/**
 * The one change worth a sentence, if any of them are.
 *
 * The largest swing that survived `insideNoise`, and nothing at all otherwise —
 * which is the common answer and the correct one. Ten minutes of football is a
 * small sample and most changes will not move it detectably; a read that fired
 * every match would be measuring the noise and would be believed anyway.
 *
 * Worded as a coincidence in time, never as a consequence. See the block
 * comment above.
 */
export function substitutionRead(result, clockText = (s) => `${Math.round(s / 60)}'`) {
    const scored = (result?.rows || []).filter(
        (r) => r.scored && !r.tentative && r.swing != null,
    );
    if (!scored.length) return null;

    const biggest = scored.reduce(
        (a, b) => (Math.abs(b.swing) > Math.abs(a.swing) ? b : a),
    );
    const pct = (v) => Math.round(v * 100);
    const who = biggest.on.length ? biggest.on.join(' and ') : 'the change';
    const minutes = Math.round(biggest.spanS / 60);

    return {
        title: biggest.swing > 0
            ? `You saw more of the ball after ${who} came on`
            : `You saw less of the ball after ${who} came on`,
        detail: `${pct(biggest.shareBefore)}% of the ball in the ${minutes} minutes `
            + `before ${clockText(biggest.clockS)}, ${pct(biggest.shareAfter)}% in `
            + 'the same span after. What changed alongside the substitution, not '
            + 'because of it.',
    };
}

/** Why a change has no figures beside it, in the coach's words. */
export const CHANGE_REASONS = {
    clock: 'no second-half kick-off saved, so the clock cannot place this',
    break: 'made at half-time, so the team talk is in the middle of it',
    crowded: 'another change too close to it',
    footage: 'the processed footage runs out',
    edge: 'too near kick-off or the final whistle',
};

/**
 * The caveats under the list. Always returns the causal one.
 *
 * That one is not decoration and is not conditional: it is the only thing
 * standing between a table of before-and-after percentages and a coach
 * concluding that a sixteen-year-old won them twenty minutes of possession.
 */
export function substitutionNote(result) {
    if (!result) return null;
    const parts = [];

    if (!result.placeable) {
        parts.push('Nothing here is measured, because the second-half kick-off '
            + 'has not been saved against the video — without it a match minute '
            + 'and a video minute are a whole interval apart.');
    } else if (!result.scored) {
        parts.push('None of these changes has enough uninterrupted football '
            + 'either side of it to compare.');
    } else {
        parts.push('Each pair counts the same length of clock either side of the '
            + 'change, cut to whichever side was shorter, and stops at the next '
            + 'change, at half-time and at the ends of the processed footage.');
    }

    if (result.atBreak) {
        parts.push(`${count(result.atBreak, 'change')} at half-time `
            + `${result.atBreak === 1 ? 'is' : 'are'} listed and not measured: `
            + 'what sits between those two windows is fifteen minutes and a team '
            + 'talk, not a substitution.');
    }

    if (result.truncated) {
        parts.push('The event list was truncated to its most confident, so it is '
            + 'not an even sample of the match and these shares lean towards the '
            + 'passages the video read best.');
    }

    if (result.scored) {
        const told = result.rows.filter((r) => r.scored && !r.tentative).length;
        parts.push(told
            ? 'A swing marked "within chance" is smaller than one this many '
                + 'events would hand out on their own.'
            : 'Every swing here is smaller than one this many events would hand '
                + 'out on their own — at a hundred events a window it takes '
                + 'about fourteen percentage points before a difference is '
                + 'worth reading, and most changes will not move it that far.');
    }

    parts.push('Shots are shown as counts because ten minutes holds one or two '
        + 'of them and a comparison of one against two is not a comparison.');

    parts.push('A change is made because of what is already happening, the '
        + 'opposition made changes nobody logged, and the scoreline moved on its '
        + 'own. This says what the football either side looked like — it does '
        + 'not say the substitution did it.');

    return parts.join(' ');
}

// ------------------------------------------------------- taking a player off
//
// "Remove" was one button doing one thing: deleting `teams/{t}/players/{p}`,
// the squad-list document. Everything else about the student stayed exactly
// where it was — their name, shirt number and stints in the roster of every
// match they had played, their whole published report including minutes,
// distance, heatmap and shot map, their email as an invite key, and their id in
// `cvMapping.byCluster`, which is what ties a person to a cropped photograph cut
// out of the footage. The coach was shown "Player removed".
//
// The rules were never the problem: a coach has always been allowed to delete
// every one of those. Nothing asked them to.
//
//     Two intentions wearing one word.
//
// A coach who says "remove Jordan" may mean *they have left the team* — in
// which case the match reports must stay, because a report is a record of a
// match that happened and deleting it would falsify the team's own results —
// or they may mean *a guardian asked us to hold nothing*, in which case
// keeping any of it is the failure. A single button cannot serve both, and the
// one that existed served neither.
//
//     What an erase can and cannot reach.
//
// The match log stays. It records substitutions by player id, never by name, so
// with the named documents gone it holds an id that resolves to nobody — and
// the log is also the arithmetic behind *every other player's* minutes. Deleting
// the entry that put Jordan on would silently take time off whoever came off
// for them. Pseudonymous and load-bearing is a good reason to leave something
// alone; it is not a good reason to pretend it was deleted, so the coach is told.

// `ui.js` has a `plural`, and this module imports nothing on purpose so that
// tests/video.test.js can load it. Two words is a cheaper duplication than an
// import that would take the whole test file's reason for existing with it.
const count = (n, word, plural = `${word}s`) => `${n} ${n === 1 ? word : plural}`;

/**
 * The cluster mapping with every reference to one player taken out.
 *
 * A new object, never the one passed in: the caller holds this in `state` and
 * mutating it in place would leave the screen agreeing with a write that has
 * not happened yet. Other players' figures are untouched — the whole point is
 * that erasing one student does not quietly re-attribute another's match.
 */
export function mappingWithout(byCluster, playerId) {
    const out = {};
    for (const [cluster, id] of Object.entries(byCluster || {})) {
        if (id !== playerId) out[cluster] = id;
    }
    return out;
}

/**
 * What is about to be deleted, in the coach's words, before they confirm it.
 *
 * `footprint` is `{ matches: [{ label, hasRoster, hasReport, clusters }],
 * hasInvite }` — what was actually found by reading, not what ought to exist.
 * A match the player was named in but never played still holds their name in
 * its roster, and a coach who is told "2 matches" when the answer is 5 has been
 * given a number they cannot act on.
 *
 * Returns `{ lines, matchCount, reportCount }`. Zero matches is a real and
 * ordinary answer — a player added last week and removed today — and it reads
 * as "nothing but their squad entry", not as an empty list.
 */
export function erasureNote(footprint = {}) {
    const matches = footprint.matches || [];
    const named = matches.filter((m) => m.hasRoster || m.hasReport);
    const reports = matches.filter((m) => m.hasReport);
    const clusters = matches.reduce((n, m) => n + (m.clusters || 0), 0);

    const lines = [];
    lines.push(named.length
        ? `Their name and shirt number come out of ${count(named.length, 'match', 'matches')}.`
        : 'They have not been named in a match yet, so there is nothing but '
          + 'their squad entry.');

    if (reports.length) {
        lines.push(`${count(reports.length, 'published report')} `
            + `${reports.length === 1 ? 'is' : 'are'} deleted — minutes, goals, `
            + 'and anything measured from video. If they have signed in, it '
            + 'disappears from their account too.');
    }
    if (clusters) {
        lines.push(`${count(clusters, 'tracked figure')} stop pointing at them, `
            + 'so the pictures cut from the footage are no longer tied to a name.');
    }
    if (footprint.hasInvite) {
        lines.push('Their invitation goes, which is the last place their email '
            + 'address is stored.');
    }

    // Only when they were actually in a match. Said unconditionally it would
    // imply something of theirs survives an erase that in fact takes
    // everything \u2014 a player added last week and removed today leaves nothing at
    // all, and a caveat about what is kept would be a caveat about nothing.
    if (named.length) {
        lines.push('The substitutions stay in the match log. They record who came '
            + 'on by an internal id rather than by name, and they are what every '
            + 'other player\u2019s minutes are counted from.');
    }

    return { lines, matchCount: named.length, reportCount: reports.length };
}
