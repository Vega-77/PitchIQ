// What a player's own report may carry.
//
// Part of `report.js`, which re-exports it.

import { count } from './report-format.js?v=152';
import { knownMinutes } from './report-clock.js?v=152';
import { CONFIRMED_STATUS, REJECTED_STATUS, EDITED_STATUS } from './report-review.js?v=152';
import { correctedShotMarks } from './report-shots.js?v=152';

// What a player is allowed to see of a match they played in.
//
// This module imports nothing but the other report-*.js files, which is
// deliberate. It decides which names and which events cross from the coach's
// record into a document a sixteen-year-old can read, and that decision should
// be testable on its own — not reachable only through a module that opens a
// Firestore connection at import time.
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
const MAX_TIMELINE = 120;

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
// The hardest burst joins them for the same reason the top speed does: the
// peak of two fragments is the peak of the player, and a sum would credit
// somebody who was substituted on twice with twice the acceleration.
const MAXED = ['top_speed_kmh', 'position_noise_m', 'top_acceleration_ms2'];

/**
 * Roll the video's tracked figures up into the players a coach named them as.
 *
 * A player can legitimately be several clusters. The tracker loses people when
 * they leave frame, and `pitchiq/identity.py` only re-joins fragments separated by
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

        const acc = out[playerId] ||= {
            clusters: [], touchTimes: [], shotMap: [], heatmaps: [],
        };
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
        // Kept unmerged, and kept out of `cvReportFields`. Combining occupancy
        // grids needs pitch dimensions and this module has none by design, so
        // the entries travel in the shape `mergeHeatmaps` takes and whoever can
        // import it does the merging. `minutes` is this cluster's own rather
        // than the player's total: that weight is what stops an eight-second
        // fragment counting for as much as a half.
        if (track.heatmap) {
            acc.heatmaps.push({ grid: track.heatmap, minutes: track.minutes_tracked });
        }
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
// `duel` and `clearance` are absent for different reasons, and the difference
// matters to anyone tempted to add them. Duels are published per team and not
// per player, so there is no player figure here to move. Clearances are
// published nowhere and the pipeline never produces one — see
// `HUMAN_ONLY_TYPES` in `pitchiq/events.py`. Either way a retype into one removes
// the original and adds nothing, which is the truth.
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
function reviewedCorrections(events, review, clusters, byCluster) {
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
// fields `pitchiq/publish_to_firestore.py` writes and this module does not. Used to blank them
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
    'cvTopSpeedKmh', 'cvSprintCount', 'cvAccelerations', 'cvTopAcceleration',
    'cvPositionNoiseM',
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
 * no twin in `pitchiq/publish_to_firestore.py::player_report_fields`. Without it the report is
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
        // How hard the hardest one was. The count above says a player changed
        // gear; this says whether the change was a jog into a stride or a
        // standing start, which is the half of the pair a coach can act on.
        cvTopAcceleration: num(stats.top_acceleration_ms2),
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
