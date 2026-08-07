// One match's worth of made-up numbers, shaped exactly like a real published
// run.
//
// Every CV block on this site hides itself when there is nothing to draw, which
// is right — an empty pitch reads as a player who never moved. The cost is that
// until footage exists, most of what has been built is invisible, and a wiring
// mistake between the pipeline and a renderer cannot be seen at all. This module
// is the fixture that makes those blocks appear on demand, so the path from a
// published document to a drawn pitch is exercised before there is anything real
// to put through it.
//
//     It is a fixture, not a demo.
//
// The shapes below are copied from `cv/publish.py` — `summary_payload` and
// `player_report_fields` — key for key. That is the whole point: when the first
// real run lands, nothing needs to change for it to render, and if something
// does, this file was wrong and the tests that read it should have said so.
//
//     Honest about what a real run looks like.
//
// The tempting thing is a flawless match: ball seen in every frame, one track
// per player, no disagreements. That would make the preview useless, because it
// would set an expectation the footage will not meet and hide every caveat the
// pages exist to show. So the numbers here are drawn from what this pipeline has
// actually measured — 83% ball coverage, 3.4 tracks per player, a couple of
// officials it could not rule out, one goal the two records disagree about. The
// preview shows the warnings because a real run will have them.
//
// The one place it is generous is the calibration: 0.42m, inside the band
// `FOOTAGE_DAY.md` asks for. A looser fit would be just as realistic but would
// flatten the shot map through `xgTrust`, and then the preview would not show
// the sized version at all. It is set at the good end deliberately, and this
// sentence is why.
//
// One block of figures is not invented at all. Every `xg` below is what the
// real model returns for a freeze frame written down in tests/test_sample_xg.py,
// and that test fails if the two ever disagree — so the totals a coach sees in
// the preview are the totals the pipeline would publish for those shots. See
// the note above OUR_SHOTS.
//
//     Nothing here is ever written.
//
// No function in this file touches Firestore, and nothing that consumes it may
// pass it to a write. Every object carries `isSample: true` so a check is
// cheap and a mistake is greppable.

/** Said wherever sample data is on screen. Short enough to fit in a banner. */
export const SAMPLE_NOTICE = 'Sample data — invented numbers in the real layout, '
    + 'so this page can be checked before there is footage to fill it.';

/** Whether a value came from this module. Nothing sampled may be saved. */
export function isSample(value) {
    return Boolean(value && value.isSample === true);
}

// ------------------------------------------------------------ the heatmap
//
// Generated rather than typed out: a 12x8 grid is 96 numbers and a wall of
// literals is unreadable and unmaintainable. Deterministic, so two loads of the
// preview draw the same pitch and a screenshot diff means something.

const COLS = 12;
const ROWS = 8;

/**
 * A plausible occupancy grid for a player who lived in one part of the pitch.
 *
 * Two Gaussian blobs — a main station and a secondary one — because that is what
 * a real heatmap looks like: a left-back has a home and an overlap, not one
 * smooth hill. Normalised to sum to 1, which is the contract every consumer of
 * `mergeHeatmaps` relies on.
 *
 * `cx`/`cy` are in grid cells, not metres. The grid is in absolute pitch
 * coordinates and knows nothing about direction; `attackingEnd` is what makes it
 * readable, and it travels beside this.
 *
 * The defaults put this player in the attacking half, because the player they
 * belong to takes both of their shots from inside the box. The first version
 * centred them on the halfway line and it looked wrong the moment it was drawn
 * next to their own shot map — a fixture whose two plots describe different
 * players teaches whoever reads it to stop expecting them to agree.
 */
export function sampleHeatmap(
    { cx = 7.6, cy = 3.2, spread = 1.15, cx2 = 9.6, cy2 = 4.4, weight2 = 0.4 } = {},
) {
    // The spread is tuned by looking at it, not picked. At 1.15 the busiest cell
    // is about eight times an evenly-covered pitch and only 35 of the 96 cells
    // clear heatmap.js's FLOOR, so the plot is a station with a run off it and
    // two thirds of the pitch stays dark.
    //
    // The first attempt used 1.5 and rendered as a single teal mass across the
    // middle of the pitch — which passed every assertion about peaks and sums
    // and still looked like "they were everywhere", the exact wash the FLOOR
    // exists to prevent. Worth remembering that the numbers agreed with it.
    const values = new Array(COLS * ROWS).fill(0);
    const blob = (x, y, ax, ay, s) =>
        Math.exp(-(((x - ax) ** 2) + ((y - ay) ** 2)) / (2 * s * s));

    let total = 0;
    for (let x = 0; x < COLS; x += 1) {
        for (let y = 0; y < ROWS; y += 1) {
            const v = blob(x, y, cx, cy, spread)
                + weight2 * blob(x, y, cx2, cy2, spread * 0.85);
            // Column-major: values[x * rows + y]. Matching the np.histogram2d
            // layout in cv/metrics.py, which is the only layout in the system.
            values[x * ROWS + y] = v;
            total += v;
        }
    }

    return {
        cols: COLS,
        rows: ROWS,
        values: values.map((v) => Number((v / total).toFixed(5))),
    };
}

// -------------------------------------------------------------- the shots
//
// Positions are in metres on a 105x68 pitch, already mirrored to attack right,
// which is what `cv/report_json.py::shot_marks` guarantees to every renderer.
//
//     The xG figures came out of the model. They are not invented.
//
// They used to be, and it showed: numbers picked to look like a plausible shot
// map, which is a fixture teaching whoever reads it that the shape of the
// output is the thing to check rather than the output. Each shot below now has
// a freeze frame behind it — where the keeper was standing, who was between the
// ball and the goal — written down in tests/test_sample_xg.py, and each `xg` is
// what `cv/xg_bridge.predict_xg` returns for that frame against the real
// xg_model8.onnx. That test re-runs the model and fails if a figure here drifts
// from what the model says, so this is a fixture that cannot quietly become
// fiction.
//
// The frames are still invented, and that is the honest limit of this: it shows
// what the model does with a set of positions, not what a match looked like.
//
// Two of them are worth reading twice. The 6-metre miss at 908.7 is the best
// chance in the match at 0.479, and it went off target — which is the whole
// argument for having xG on the page at all. The block from 29 metres at 1204.2
// is **0.0**, not null: two defenders in front of it and a calibrated model says
// a shot from there is worth nothing. Absent is not zero, and this is the zero.

const OUR_SHOTS = [
    { video_s: 412.4, x_m: 92.1, y_m: 33.8, xg: 0.098, outcome: 'goal', on_target: true, track_id: 7 },
    { video_s: 631.0, x_m: 84.6, y_m: 27.2, xg: 0.013, outcome: 'saved', on_target: true, track_id: 11 },
    { video_s: 908.7, x_m: 99.2, y_m: 38.4, xg: 0.479, outcome: 'off_target', on_target: false, track_id: 7 },
    { video_s: 1204.2, x_m: 76.3, y_m: 41.9, xg: 0.0, outcome: 'blocked', on_target: false, track_id: 4 },
    { video_s: 1655.8, x_m: 95.8, y_m: 30.1, xg: 0.109, outcome: 'saved', on_target: true, track_id: 9 },
    { video_s: 2210.5, x_m: 88.4, y_m: 34.6, xg: 0.099, outcome: 'goal', on_target: true, track_id: 11 },
];

const THEIR_SHOTS = [
    { video_s: 520.3, x_m: 81.7, y_m: 24.5, xg: 0.009, outcome: 'off_target', on_target: false, track_id: 23 },
    { video_s: 1420.9, x_m: 97.4, y_m: 35.2, xg: 0.390, outcome: 'goal', on_target: true, track_id: 19 },
    { video_s: 1888.1, x_m: 90.2, y_m: 44.8, xg: 0.025, outcome: 'saved', on_target: true, track_id: 23 },
    { video_s: 2402.6, x_m: 72.9, y_m: 31.4, xg: 0.008, outcome: 'off_target', on_target: false, track_id: 27 },
];

/** The team total, so it is the sum of the map and cannot drift from it. */
const totalXg = (shots) =>
    Number(shots.reduce((sum, shot) => sum + shot.xg, 0).toFixed(3));

// ------------------------------------------------------- the team document
//
// Key for key with `cv/publish.py::summary_payload`. A key that exists there and
// not here is a block this preview silently cannot check.

/**
 * A `cvStats/summary` document, as the coach's match view and the half-time page
 * read it.
 *
 * The numbers are internally consistent on purpose: possession, passes and
 * territory describe one side that had the ball a lot and spent too much of it
 * in its own third, which is what makes the pinned-back read in `cvReads` fire.
 * A preview whose figures contradict each other would train whoever reads it to
 * stop checking whether the figures agree.
 */
export function sampleCvSummary() {
    return {
        isSample: true,
        schemaVersion: 5,
        // Which half, and what decided it. `log` is the good case on purpose:
        // the caveated version of this is a one-line change and the preview
        // already carries plenty of caveats, whereas nobody has yet seen what
        // the confident version reads like.
        period: 'first_half',
        periodSource: 'log',
        source: 'sample',
        window: { start_s: 0, end_s: 2700 },
        durationS: 2700,
        calibrated: true,
        calibrationErrorM: 0.42,

        quality: {
            // Measured on the real spike, not invented: the ball is the hard
            // part and the preview should say so.
            ball_seen_share: 0.83,
            no_ball_s: 459,
            live_share: 0.71,
            stoppages: 34,
            // Kept, never dropped — see cv/participants.py. Two figures the
            // classifier could not rule out are inside the counts above.
            flagged_officials: 2,
            // 3.4 was the measured figure after cv/identity.py merges. Above
            // the threshold that adds the "broke each player into pieces" note,
            // which is correct: it is true today and will be true on day one.
            tracks_per_cluster: 3.4,
            touch_confidence: 0.62,
        },

        warnings: [
            'the tagged log has a goal at 24:10 that the pipeline did not find',
        ],
        trustworthy: false,

        teams: {
            team_a: {
                team: 'team_a',
                possession_pct: 0.58,
                touches: 604,
                passes_attempted: 341,
                passes_completed: 246,
                pass_accuracy: 0.721,
                // Buckets named by cv/events.py::_bucket and ::_direction. They
                // sum to passes_attempted, which is the check worth having: a
                // breakdown that does not add up to its own total is the kind of
                // thing a preview should never model as acceptable.
                passes_by_length: { short: 198, medium: 109, long: 34 },
                passes_by_direction: { forward: 142, sideways: 131, backward: 68 },
                progressive_passes: 74,
                final_third_entries: 31,
                box_entries: 12,
                switches: 9,
                crosses: 14,
                carries: 118,
                shots: OUR_SHOTS.length,
                shots_on_target: 4,
                goals: 2,
                xg: totalXg(OUR_SHOTS),
                tackles: 17,
                interceptions: 22,
                recoveries: 39,
                duels: 44,
                // 145 passes allowed to 21 challenges across the 45 minutes.
                // The blocks below are that total split three ways and they
                // have to agree with it, because the chart sits under the row
                // that prints this number.
                ppda: 6.9,
                // A side that pressed hard, eased off, and stopped. The last
                // block is deliberately too thin to divide: three challenges is
                // a number about three moments, and the preview should show
                // what the page does with one rather than only the happy case.
                pressing_segments: [
                    { start_s: 0, end_s: 900, allowed: 48, actions: 12, ppda: 4.0 },
                    { start_s: 900, end_s: 1800, allowed: 52, actions: 6, ppda: 8.67 },
                    { start_s: 1800, end_s: 2700, allowed: 45, actions: 3, ppda: null },
                ],
                shape: { width_m: 41.2, depth_m: 33.8, compactness_m: 14.6 },
                // Fires the pinned-back read: more than PINNED_BACK_SHARE of
                // possession spent in their own third.
                territory: { defensive: 0.47, middle: 0.36, attacking: 0.17 },
                shape_drift: {
                    change: { width_m: 4.3, depth_m: -3.6, compactness_m: 1.1 },
                },
                attacking_end: 'right',
                shot_map: OUR_SHOTS,
                turnovers_by_third: { defensive: 9, middle: 14, attacking: 11 },
            },
            team_b: {
                team: 'team_b',
                possession_pct: 0.42,
                touches: 471,
                passes_attempted: 268,
                passes_completed: 171,
                pass_accuracy: 0.638,
                passes_by_length: { short: 141, medium: 88, long: 39 },
                passes_by_direction: { forward: 118, sideways: 96, backward: 54 },
                progressive_passes: 61,
                final_third_entries: 24,
                box_entries: 9,
                switches: 6,
                crosses: 11,
                carries: 96,
                shots: THEIR_SHOTS.length,
                shots_on_target: 2,
                goals: 1,
                xg: totalXg(THEIR_SHOTS),
                tackles: 21,
                interceptions: 18,
                recoveries: 33,
                duels: 44,
                // 130 to 15, and unlike ours it holds all the way through —
                // the contrast is the point of carrying the opponent's blocks
                // at all, even though nothing draws them today.
                ppda: 8.67,
                pressing_segments: [
                    { start_s: 0, end_s: 900, allowed: 40, actions: 5, ppda: 8.0 },
                    { start_s: 900, end_s: 1800, allowed: 46, actions: 5, ppda: 9.2 },
                    { start_s: 1800, end_s: 2700, allowed: 44, actions: 5, ppda: 8.8 },
                ],
                shape: { width_m: 37.9, depth_m: 30.2, compactness_m: 13.1 },
                territory: { defensive: 0.31, middle: 0.38, attacking: 0.31 },
                shape_drift: null,
                attacking_end: 'left',
                shot_map: THEIR_SHOTS,
                turnovers_by_third: { defensive: 12, middle: 17, attacking: 8 },
            },
        },

        keepers: [{ track_id: 2, team: 'team_a' }, { track_id: 18, team: 'team_b' }],

        // Two figures the classifier acted on, each with the sentence its own
        // guess produced. An exclusion a coach cannot question looks like the
        // pipeline knowing something it does not.
        participants: [
            {
                trackId: 31, role: 'official',
                reason: 'kit matched neither team, and stayed away from both goals',
                screenTimeS: 2280,
            },
            {
                trackId: 44, role: 'offfield',
                reason: 'never entered the pitch bounds',
                screenTimeS: 640,
            },
        ],

        // One goal the two records disagree about, which is the highest-value
        // thing on the page: a moment where two independent records conflict
        // beats a moment where only one of them spoke.
        reconciliation: {
            goal_agreement: 0.75,
            exit_agreement: 0.62,
            goals: { agreed: 3, cv_only: 0, tag_only: 1 },
            exits: { agreed: 21, cv_only: 7, tag_only: 6 },
            exits_checked: true,
            disagreements: [
                {
                    kind: 'goal', status: 'tag_only',
                    cv_s: null, tag_s: 1450.0, tag_type: 'goal', detail: null,
                },
            ],
        },
    };
}

// ------------------------------------------------------ the player document
//
// Key for key with `cv/publish.py::player_report_fields`.

// ------------------------------------------------------ the passing network
//
// A 4-3-3 that built down its own left, which is what the picture should show:
// the left-back and left midfielder are the busiest pair on the pitch, and the
// right winger is barely connected to anyone. Positions are mean pass origins
// in metres, attacking right, so they read as a shape.
//
// Deliberately *not* eleven tidy players. Two of the passers are unnamed on
// purpose (tracks 60 and 61 map to no cluster in the sample) and a handful of
// passes go nowhere, because the note under the diagram is most of what it is
// for — a network drawn from two thirds of the passes looks exactly like one
// drawn from all of them.

const SAMPLE_SHAPE = [
    // [trackId, playerId, x_m, y_m, passes, completed]
    [1, 'gk', 8, 34, 24, 21],
    [2, 'rb', 38, 58, 31, 24],
    [3, 'rcb', 27, 41, 40, 36],
    [4, 'lcb', 27, 27, 44, 40],
    [5, 'lb', 41, 11, 52, 43],
    [6, 'dm', 46, 34, 58, 50],
    [7, 'rcm', 58, 45, 41, 32],
    [8, 'lcm', 61, 20, 55, 46],
    [9, 'rw', 76, 57, 19, 11],
    [10, 'st', 79, 34, 27, 17],
    [11, 'lw', 80, 13, 34, 24],
];

// Who fed whom. Weighted down the left, and the right winger connects to one
// player only — the thing a coach is meant to see at a glance.
const SAMPLE_LINKS = [
    ['lb', 'lcm', 17], ['lcm', 'lb', 12], ['lcb', 'lb', 14], ['lb', 'lcb', 9],
    ['lcm', 'lw', 11], ['lw', 'lcm', 7], ['dm', 'lcb', 10], ['lcb', 'dm', 9],
    ['rcb', 'rb', 12], ['rb', 'rcb', 8], ['dm', 'rcm', 9], ['rcm', 'dm', 8],
    ['gk', 'rcb', 8], ['gk', 'lcb', 9], ['rcm', 'rw', 6], ['rw', 'rcm', 5],
    ['lcm', 'st', 7], ['st', 'lcm', 4], ['dm', 'lcm', 8], ['lcm', 'dm', 9],
    ['rcb', 'lcb', 6], ['lcb', 'rcb', 7], ['lw', 'st', 5], ['st', 'lw', 3],
];

/**
 * Pass events shaped exactly like `cvStats/events`, for previewing the network.
 *
 * A separate export from `sampleCvSummary` on purpose. That fixture carries no
 * events at all so the review tool and the shot log stay empty under the
 * preview — both write back to Firestore, and a verdict tapped against an
 * invented id would put a decision about nothing into a real document. Drawing
 * a diagram writes nothing, so this one is safe to hand over, and it is the
 * only way anybody sees this feature before there is footage.
 */
export function samplePassEvents() {
    const trackOf = new Map(SAMPLE_SHAPE.map(([t, id]) => [id, t]));
    const events = [];
    let n = 0;

    const push = (trackId, receiverTrackId, xy, outcome) => {
        events.push({
            id: `sample-pass-${n}`,
            type: 'pass',
            timestampS: 30 + n * 7,
            trackId,
            receiverTrackId,
            team: 'team_a',
            confidence: 0.72,
            inPlay: true,
            outcome,
            startM: xy,
        });
        n += 1;
    };

    const posOf = new Map(SAMPLE_SHAPE.map(([, id, x, y]) => [id, [x, y]]));
    for (const [from, to, count] of SAMPLE_LINKS) {
        for (let i = 0; i < count; i += 1) {
            // Scattered a little around the mean, so the average this comes
            // back out at is an average of something rather than one point
            // repeated — which is what a real run produces.
            const [x, y] = posOf.get(from);
            const jitter = ((i % 5) - 2) * 1.6;
            push(trackOf.get(from), trackOf.get(to), [x + jitter, y - jitter], 'completed');
        }
    }

    // The passes that found nobody. In each player's own count, on no line.
    for (const [trackId, id, x, y, passes, completed] of SAMPLE_SHAPE) {
        const made = SAMPLE_LINKS
            .filter(([from]) => from === id)
            .reduce((sum, [, , count]) => sum + count, 0);
        for (let i = 0; i < Math.max(0, passes - made); i += 1) {
            push(trackId, null, [x, y], i < completed - made ? 'completed' : 'incomplete');
        }
    }

    // Two figures nobody has named, so the note has something real to report.
    for (const trackId of [60, 61]) {
        for (let i = 0; i < 4; i += 1) push(trackId, null, [52, 34], 'completed');
    }

    return events;
}

const SAMPLE_NAMES = {
    gk: 'Rae Nkemelu', rb: 'Sam Iyer', rcb: 'Jo Marchetti', lcb: 'Kit Osei',
    lb: 'Dee Okafor', dm: 'Ada Fenwick', rcm: 'Cass Ide', lcm: 'Noor Haddad',
    rw: 'Bo Lindqvist', st: 'Ren Achebe', lw: 'Val Sorensen',
};

/**
 * Which invented figure is which invented player, and what to call them.
 *
 * Returned together because the preview needs both and pairing them anywhere
 * else would let a name drift away from the track it belongs to.
 */
export function samplePassMapping() {
    return {
        byTrack: new Map(SAMPLE_SHAPE.map(([trackId, playerId]) => [trackId, playerId])),
        nameOf: (playerId) => SAMPLE_NAMES[playerId] || playerId,
    };
}

/**
 * The `cv*` fields one player's match report carries.
 *
 * Consistent with the team document above rather than freshly invented: this is
 * track 7, who took two of the six shots on `team_a`'s map and scored one of
 * them. A preview where a player's shots do not appear on their own team's map
 * would be a preview of a bug.
 */
export function samplePlayerReport() {
    const mine = OUR_SHOTS.filter((s) => s.track_id === 7);

    return {
        isSample: true,
        cvTouches: 61,
        cvPassesAttempted: 38,
        cvPassesCompleted: 29,
        cvPassAccuracy: 0.763,
        cvCarries: 17,
        cvTackles: 3,
        cvInterceptions: 4,
        cvRecoveries: 6,
        cvShots: mine.length,
        cvXg: mine.reduce((sum, s) => sum + s.xg, 0),
        cvDistanceM: 9340,
        cvTopSpeedKmh: 27.4,
        cvSprintCount: 21,
        cvMinutesTracked: 71.5,
        // Played the whole 90, all of it filmed, and the tracker held them for
        // 71.5 of those minutes — a share of 0.79. Above the floor on purpose:
        // the preview should show the clean version of the coverage sentence,
        // and the caveated one is already covered by the team preview's 3.4
        // tracks per player. A fixture where every caveat fires at once teaches
        // nobody which caveat means what.
        cvMinutesOnPitch: 90,
        cvMinutesFilmed: 90,
        cvTrackedShare: 71.5 / 90,
        cvTouchTimes: OUR_SHOTS.map((s) => s.video_s),
        // Three fragments. Below the threshold that adds the "lost and refound
        // you" caveat, so the clean version of the note is what shows — the
        // caveated one is already covered by the team preview's 3.4.
        cvClusterCount: 3,
        cvHeatmap: sampleHeatmap(),
        cvAttackingEnd: 'right',
        cvShotMap: mine,
        cvCalibrationErrorM: 0.42,
    };
}

/**
 * A season of published reports for one player, newest first.
 *
 * `playerSeason` hands them back in that order and the season chart reverses
 * them itself, so a fixture in any other order would preview a season running
 * backwards and every assertion about it would still pass.
 *
 * Eight matches, and deliberately not eight clean ones. Two were never filmed
 * and one was filmed and tracked for six minutes, because those are the two
 * things a real season is full of and they are exactly what the chart has to
 * refuse to draw through. A fixture of eight good matches would preview a
 * feature that does not exist.
 *
 * The **order** of the gaps was chosen rather than sprinkled. The chart joins
 * consecutive measured matches and never draws across a gap, so a fixture with
 * a gap between every measured pair renders as five loose dots and proves only
 * half the rule. This one has two runs of adjacent filmed matches and two
 * separate breaks, so both halves of that behaviour show up in the preview.
 *
 * The story in the numbers is a player whose work rate climbed across the
 * season while her passing wobbled and then held — a shape worth being able to
 * see, and the reason for drawing rates rather than adding totals up.
 */
export function sampleSeason() {
    const played = (n, opponent, date, extra) => ({
        isSample: true,
        matchId: `sample-${n}`,
        opponentName: opponent,
        matchDate: date,
        minutesPlayed: extra.minutesPlayed ?? 90,
        goals: extra.goals || 0,
        assists: extra.assists || 0,
        yellowCards: 0,
        redCards: 0,
        fouls: extra.fouls || 0,
        ...extra,
    });

    // Oldest first here because that is the order the story reads in; reversed
    // on the way out so the fixture matches what the database returns.
    const season = [
        // A run of two, so the chart has a segment to draw.
        played(1, 'Hillsborough', '2026-03-14', {
            goals: 0, cvMinutesTracked: 42, cvDistanceM: 3700, cvTouches: 48,
            cvPassesAttempted: 31, cvPassesCompleted: 24, cvTopSpeedKmh: 25.9,
        }),
        played(2, 'Montgomery', '2026-03-21', {
            goals: 1, cvMinutesTracked: 48, cvDistanceM: 4290, cvTouches: 55,
            cvPassesAttempted: 38, cvPassesCompleted: 29, cvTopSpeedKmh: 26.3,
        }),
        // Nobody brought a camera. Not a zero, and not a missing match.
        played(3, 'Princeton', '2026-03-28', { goals: 0, assists: 1, minutesPlayed: 78 }),
        // And a second run, either side of the break.
        played(4, 'West Windsor', '2026-04-04', {
            goals: 0, cvMinutesTracked: 58, cvDistanceM: 5300, cvTouches: 71,
            cvPassesAttempted: 44, cvPassesCompleted: 33, cvTopSpeedKmh: 26.8,
        }),
        played(5, 'Notre Dame', '2026-04-11', {
            goals: 1, cvMinutesTracked: 66, cvDistanceM: 6250, cvTouches: 84,
            cvPassesAttempted: 52, cvPassesCompleted: 41, cvTopSpeedKmh: 27.1,
        }),
        // Filmed, and the tracker lost her after six minutes. Every figure in
        // this row is real and none of them is a match.
        played(6, 'Steinert', '2026-04-18', {
            goals: 0, cvMinutesTracked: 6, cvDistanceM: 520, cvTouches: 8,
            cvPassesAttempted: 5, cvPassesCompleted: 4, cvTopSpeedKmh: 24.1,
        }),
        played(7, 'Allentown', '2026-04-25', { goals: 2 }),
        played(8, 'Robbinsville', '2026-05-02', {
            goals: 0, assists: 2, cvMinutesTracked: 74, cvDistanceM: 7350,
            cvTouches: 101, cvPassesAttempted: 66, cvPassesCompleted: 54,
            cvTopSpeedKmh: 28.0,
        }),
    ];

    return season.reverse();
}
