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
const XG_PER_SHOT_LIMIT_M = 1.0;
const XG_TOTAL_LIMIT_M = 4.0;

/**
 * How much of the xG on a run is worth showing: `'shot'`, `'total'` or `'none'`.
 *
 * The model was measured against deliberately noisy positions, and the answer
 * was not flattering. On a 0.254 baseline, half a metre of position error moves
 * a single shot by 0.035 on average and 0.084 at the 95th percentile — a
 * seventh and a third of it; at four metres the p95 shift is 0.344, which is
 * **larger than the quantity itself**.
 *
 * The bands are set from those ratios rather than from the absolute shifts,
 * which is why they survived the model being recalibrated on 2026-08-06. Every
 * figure in the table halved and every ratio stayed where it was.
 *
 * So there are three honest states rather than a number and a warning beside it:
 *
 *   - `'shot'` — under a metre. Good enough for "that was a decent chance". Not
 *     good enough to rank two shots 0.1 apart, which is why the caveat stays.
 *   - `'total'` — up to four metres. Individual shots are too loose to compare,
 *     but the errors are independent per shot and a half's worth largely cancel,
 *     so the team total survives what a single number does not.
 *   - `'none'` — beyond that the error bar is wider than the number. There is
 *     nothing to print, and printing it with a warning attached would still
 *     leave a specific-looking figure on screen, which is what people remember.
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
    // xG by ~0.035 on a 0.254 baseline, and at 4m the spread exceeds the number
    // itself.
    if (options.shots) {
        const error = options.calibrationErrorM;
        const trust = xgTrust(error);
        const at = error == null ? '' : ` at ${error.toFixed(1)}m of calibration error`;

        if (trust === 'none') {
            notes.push(`xG is not shown${at} — the model moves further than the `
                + 'number is worth when the positions are that loose');
        } else {
            notes.push('xG counts every shot as struck with the foot — one camera '
                + 'cannot see the ball\'s height'
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

        if (!decision) {
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
        note: 'Only events with a verdict are labelled. Anything absent from '
            + '`labelled` was never looked at, and is not a negative example. '
            + 'labelled[].videoS is a position in the video; missed[].clockS is '
            + 'a match-clock reading a person typed. videoS = clockS + '
            + 'videoOffsetS.',
        labelled: (events || [])
            .filter((event) => byEvent[event.id])
            .map((event) => {
                const decision = byEvent[event.id];
                return {
                    id: event.id,
                    videoS: event.timestampS,
                    claimedType: event.type,
                    verdict: decision.status,
                    actualType: decision.status === REJECTED_STATUS
                        ? null
                        : (decision.type || event.type),
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
 */
export function cvReads(cv) {
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
