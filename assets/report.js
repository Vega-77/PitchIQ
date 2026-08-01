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
        if (!playerId) continue;
        const track = byId.get(String(clusterId));
        if (!track) continue;

        const acc = out[playerId] ||= { clusters: [], touchTimes: [] };
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
    }

    for (const acc of Object.values(out)) {
        // Clusters arrive in whatever order the mapping was written, so the
        // combined touch list has to be re-sorted before it means anything as
        // a timeline.
        acc.touchTimes.sort((a, b) => a - b);
        acc.touchTimes = acc.touchTimes.slice(0, MAX_TOUCH_TIMES);
        acc.clusters.sort((a, b) => a - b);
        acc.passAccuracy = acc.passes_attempted
            ? acc.passes_completed / acc.passes_attempted
            : null;
    }
    return out;
}

/** The `cv`-prefixed fields for one player's match report. */
export function cvReportFields(stats) {
    if (!stats) return {};
    const num = (v) => (v == null ? null : v);
    return {
        cvTouches: num(stats.touches),
        cvPassesAttempted: num(stats.passes_attempted),
        cvPassesCompleted: num(stats.passes_completed),
        cvCarries: num(stats.carries),
        cvTackles: num(stats.tackles),
        cvInterceptions: num(stats.interceptions),
        cvRecoveries: num(stats.recoveries),
        cvShots: num(stats.shots),
        cvXg: num(stats.xg),
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
