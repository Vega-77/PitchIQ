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
// xg_model7.onnx. That test re-runs the model and fails if a figure here drifts
// from what the model says, so this is a fixture that cannot quietly become
// fiction.
//
// The frames are still invented, and that is the honest limit of this: it shows
// what the model does with a set of positions, not what a match looked like.
//
// Two of them are worth reading twice. The 6-metre miss at 908.7 is the best
// chance in the match at 0.535, and it went off target — which is the whole
// argument for having xG on the page at all. The block from 29 metres at 1204.2
// is **0.0**, not null: two defenders in front of it and a calibrated model says
// a shot from there is worth nothing. Absent is not zero, and this is the zero.

const OUR_SHOTS = [
    { video_s: 412.4, x_m: 92.1, y_m: 33.8, xg: 0.216, outcome: 'goal', on_target: true, track_id: 7 },
    { video_s: 631.0, x_m: 84.6, y_m: 27.2, xg: 0.033, outcome: 'saved', on_target: true, track_id: 11 },
    { video_s: 908.7, x_m: 99.2, y_m: 38.4, xg: 0.535, outcome: 'off_target', on_target: false, track_id: 7 },
    { video_s: 1204.2, x_m: 76.3, y_m: 41.9, xg: 0.0, outcome: 'blocked', on_target: false, track_id: 4 },
    { video_s: 1655.8, x_m: 95.8, y_m: 30.1, xg: 0.327, outcome: 'saved', on_target: true, track_id: 9 },
    { video_s: 2210.5, x_m: 88.4, y_m: 34.6, xg: 0.165, outcome: 'goal', on_target: true, track_id: 11 },
];

const THEIR_SHOTS = [
    { video_s: 520.3, x_m: 81.7, y_m: 24.5, xg: 0.003, outcome: 'off_target', on_target: false, track_id: 23 },
    { video_s: 1420.9, x_m: 97.4, y_m: 35.2, xg: 0.461, outcome: 'goal', on_target: true, track_id: 19 },
    { video_s: 1888.1, x_m: 90.2, y_m: 44.8, xg: 0.083, outcome: 'saved', on_target: true, track_id: 23 },
    { video_s: 2402.6, x_m: 72.9, y_m: 31.4, xg: 0.003, outcome: 'off_target', on_target: false, track_id: 27 },
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
        schemaVersion: 3,
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
                ppda: 11.3,
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
                ppda: 8.7,
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
