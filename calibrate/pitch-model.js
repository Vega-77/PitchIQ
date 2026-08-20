// Pitch geometry, mirroring cv/pitch.py.
//
// This is duplicated rather than shared because the site has no build step and
// the CV pipeline is Python. tests/test_pitch_parity.py runs both and fails if
// they ever disagree, so the duplication is checked rather than trusted.

export const GOAL_WIDTH_M = 7.32;
export const GOAL_AREA_LENGTH_M = 5.5;
export const GOAL_AREA_WIDTH_M = 18.32;
export const PENALTY_AREA_LENGTH_M = 16.5;
export const PENALTY_AREA_WIDTH_M = 40.32;
export const PENALTY_SPOT_M = 11.0;
export const CENTRE_CIRCLE_RADIUS_M = 9.15;

/**
 * The Laws values as one object — the defaults, not the truth.
 *
 * Paint on a real field is measured by whoever had the tape that morning, and
 * a school pitch shared with another sport is routinely marked short, narrow
 * or off-centre. Assuming Laws markings on a field that does not have them is
 * not a small error and it is not a random one: every landmark of the
 * mismarked family is displaced the same way, so the homography tilts to split
 * the difference and *every* position on the pitch pays — including the
 * ones nobody clicked. The picker then reports that as the coach's clicking,
 * and no amount of re-clicking can move it. `measureMarkings` below measures
 * the real numbers off the clicks instead.
 */
export const DEFAULT_MARKS = {
    goalWidthM: GOAL_WIDTH_M,
    goalAreaLengthM: GOAL_AREA_LENGTH_M,
    goalAreaWidthM: GOAL_AREA_WIDTH_M,
    penaltyAreaLengthM: PENALTY_AREA_LENGTH_M,
    penaltyAreaWidthM: PENALTY_AREA_WIDTH_M,
    penaltySpotM: PENALTY_SPOT_M,
    centreCircleRadiusM: CENTRE_CIRCLE_RADIUS_M,
};

/**
 * Landmark coordinates in metres for a pitch of the given size and markings.
 *
 * `marks` is merged over `DEFAULT_MARKS`, so passing nothing — or a partial
 * object holding only the dimensions that were actually measured — leaves
 * the rest at the Laws values. Mirrors `Pitch.landmarks` in cv/pitch.py, whose
 * marking fields default the same way.
 */
export function landmarks(lengthM = 105.0, widthM = 68.0, marks = null) {
    const m = marks ? { ...DEFAULT_MARKS, ...marks } : DEFAULT_MARKS;
    const L = lengthM;
    const W = widthM;
    const cy = W / 2;
    const paHalf = m.penaltyAreaWidthM / 2;
    const gaHalf = m.goalAreaWidthM / 2;
    const paLen = m.penaltyAreaLengthM;
    const gaLen = m.goalAreaLengthM;
    const spot = m.penaltySpotM;
    const circle = m.centreCircleRadiusM;
    const goalHalf = m.goalWidthM / 2;

    return {
        corner_bottom_left: [0.0, 0.0],
        corner_top_left: [0.0, W],
        corner_bottom_right: [L, 0.0],
        corner_top_right: [L, W],

        halfway_bottom: [L / 2, 0.0],
        halfway_top: [L / 2, W],
        centre_spot: [L / 2, cy],
        centre_circle_bottom: [L / 2, cy - circle],
        centre_circle_top: [L / 2, cy + circle],

        pen_left_bottom_goalline: [0.0, cy - paHalf],
        pen_left_top_goalline: [0.0, cy + paHalf],
        pen_left_bottom_corner: [paLen, cy - paHalf],
        pen_left_top_corner: [paLen, cy + paHalf],
        pen_spot_left: [spot, cy],

        pen_right_bottom_goalline: [L, cy - paHalf],
        pen_right_top_goalline: [L, cy + paHalf],
        pen_right_bottom_corner: [L - paLen, cy - paHalf],
        pen_right_top_corner: [L - paLen, cy + paHalf],
        pen_spot_right: [L - spot, cy],

        goalarea_left_bottom_corner: [gaLen, cy - gaHalf],
        goalarea_left_top_corner: [gaLen, cy + gaHalf],
        goalarea_right_bottom_corner: [L - gaLen, cy - gaHalf],
        goalarea_right_top_corner: [L - gaLen, cy + gaHalf],

        goalpost_left_bottom: [0.0, cy - goalHalf],
        goalpost_left_top: [0.0, cy + goalHalf],
        goalpost_right_bottom: [L, cy - goalHalf],
        goalpost_right_top: [L, cy + goalHalf],
    };
}

/** Grouped for the picker UI, so the list reads in a findable order. */
export const LANDMARK_GROUPS = [
    {
        name: 'Corners',
        items: [
            ['corner_bottom_left', 'Bottom-left corner'],
            ['corner_bottom_right', 'Bottom-right corner'],
            ['corner_top_right', 'Top-right corner'],
            ['corner_top_left', 'Top-left corner'],
        ],
    },
    {
        name: 'Halfway',
        items: [
            ['halfway_bottom', 'Halfway × bottom touchline'],
            ['halfway_top', 'Halfway × top touchline'],
            ['centre_spot', 'Centre spot'],
            ['centre_circle_bottom', 'Centre circle — bottom'],
            ['centre_circle_top', 'Centre circle — top'],
        ],
    },
    {
        name: 'Left penalty area',
        items: [
            ['pen_left_bottom_goalline', 'Box × goal line (bottom)'],
            ['pen_left_top_goalline', 'Box × goal line (top)'],
            ['pen_left_bottom_corner', 'Box corner (bottom)'],
            ['pen_left_top_corner', 'Box corner (top)'],
            ['pen_spot_left', 'Penalty spot'],
            ['goalarea_left_bottom_corner', '6-yard corner (bottom)'],
            ['goalarea_left_top_corner', '6-yard corner (top)'],
            ['goalpost_left_bottom', 'Goalpost (bottom)'],
            ['goalpost_left_top', 'Goalpost (top)'],
        ],
    },
    {
        name: 'Right penalty area',
        items: [
            ['pen_right_bottom_goalline', 'Box × goal line (bottom)'],
            ['pen_right_top_goalline', 'Box × goal line (top)'],
            ['pen_right_bottom_corner', 'Box corner (bottom)'],
            ['pen_right_top_corner', 'Box corner (top)'],
            ['pen_spot_right', 'Penalty spot'],
            ['goalarea_right_bottom_corner', '6-yard corner (bottom)'],
            ['goalarea_right_top_corner', '6-yard corner (top)'],
            ['goalpost_right_bottom', 'Goalpost (bottom)'],
            ['goalpost_right_top', 'Goalpost (top)'],
        ],
    },
];

// ---------------------------------------------------------------------------
// Homography
// ---------------------------------------------------------------------------

/**
 * Least-squares homography from >= 4 correspondences.
 *
 * Fixes h33 = 1 and solves the resulting 8-unknown system through the normal
 * equations, which avoids needing an SVD in the browser. This drives the
 * on-screen preview only; cv/calibration.py does the authoritative RANSAC fit,
 * which can also reject a bad point rather than averaging it in.
 */
export function fitHomography(pairs) {
    if (pairs.length < 4) throw new Error('need at least 4 points');

    const rows = [];
    const rhs = [];

    for (const { src, dst } of pairs) {
        const [x, y] = src;
        const [u, v] = dst;
        rows.push([x, y, 1, 0, 0, 0, -u * x, -u * y]);
        rhs.push(u);
        rows.push([0, 0, 0, x, y, 1, -v * x, -v * y]);
        rhs.push(v);
    }

    // Normal equations: (AᵀA) h = Aᵀb
    const n = 8;
    const ata = Array.from({ length: n }, () => new Array(n).fill(0));
    const atb = new Array(n).fill(0);

    for (let r = 0; r < rows.length; r++) {
        const row = rows[r];
        for (let i = 0; i < n; i++) {
            atb[i] += row[i] * rhs[r];
            for (let j = 0; j < n; j++) ata[i][j] += row[i] * row[j];
        }
    }

    const h = solve(ata, atb);
    return [
        [h[0], h[1], h[2]],
        [h[3], h[4], h[5]],
        [h[6], h[7], 1],
    ];
}

/** Gaussian elimination with partial pivoting. */
function solve(matrix, vector) {
    const n = vector.length;
    const m = matrix.map((row, i) => [...row, vector[i]]);

    for (let col = 0; col < n; col++) {
        let pivot = col;
        for (let r = col + 1; r < n; r++) {
            if (Math.abs(m[r][col]) > Math.abs(m[pivot][col])) pivot = r;
        }
        if (Math.abs(m[pivot][col]) < 1e-12) {
            throw new Error('points are degenerate — try spreading them out');
        }
        [m[col], m[pivot]] = [m[pivot], m[col]];

        for (let r = 0; r < n; r++) {
            if (r === col) continue;
            const factor = m[r][col] / m[col][col];
            for (let c = col; c <= n; c++) m[r][c] -= factor * m[col][c];
        }
    }

    // Fully reduced, so each row is (pivot · x_i = rhs).
    return m.map((row, i) => row[n] / row[i]);
}

export function applyHomography(H, x, y) {
    const w = H[2][0] * x + H[2][1] * y + H[2][2];
    return [
        (H[0][0] * x + H[0][1] * y + H[0][2]) / w,
        (H[1][0] * x + H[1][1] * y + H[1][2]) / w,
    ];
}


// ------------------------------------------------------- measuring the pitch

// Search bounds, deliberately wider than any pitch anyone will calibrate. The
// interval test below has to be able to run past a plausible answer before it
// can call that answer confident, so the range is not a guess at the pitch EM
// it is the room the test needs.
const MIN_LENGTH_M = 80.0;
const MAX_LENGTH_M = 130.0;
const MIN_WIDTH_M = 44.0;
const MAX_WIDTH_M = 90.0;

const COARSE_STEP_M = 2.5;
const FINE_STEP_M = 0.25;
const PROFILE_STEP_M = 0.5;

// A size is only reported when moving away from it costs error. These two say
// how much: the error has to rise by a fifth (or by a centimetre, whichever is
// larger — the absolute floor keeps perfect clicks, where the best error is
// 0.00, from dividing by nothing) before the interval is allowed to end, and
// the resulting interval has to be narrower than a quarter of the search span.
const INTERVAL_TOL = 1.20;
const INTERVAL_FLOOR_M = 0.01;
const MAX_INTERVAL_SHARE = 0.25;

// Four points map to four points exactly whatever size you assume, so the error
// is identically zero across the whole search and nothing can be measured from
// them. Five is the first count that can disagree with itself.
const MIN_MEASURABLE_POINTS = 5;

/**
 * Mean reprojection error in metres for one assumed pitch size.
 *
 * Metres, not pixels, and the choice matters. Pixels are where the click noise
 * actually lives, so pixels are the better-conditioned objective — but metres
 * are the units this page reports, the units `CalibrationError.is_usable`
 * draws its bar in, and the units every distance downstream is scaled by.
 * Measured on one real miscalibrated frame the two objectives disagreed by
 * about 10% (117x59 against 106x51), and only the metre answer put the page's
 * own mean/worst figures under the bar. Optimising anything other than the
 * number on screen would be picking a size the page then calls bad.
 */
function sizeError(entries, lengthM, widthM, marks = null) {
    const model = landmarks(lengthM, widthM, marks);
    let H;
    try {
        H = fitHomography(entries.map(([name, px]) => ({
            src: px, dst: model[name],
        })));
    } catch {
        return Infinity;
    }

    let total = 0;
    for (const [name, px] of entries) {
        const [x, y] = applyHomography(H, px[0], px[1]);
        const [tx, ty] = model[name];
        const d = Math.hypot(x - tx, y - ty);
        if (!Number.isFinite(d)) return Infinity;
        total += d;
    }
    return total / entries.length;
}

/**
 * The contiguous run of sizes around `best` whose error stays under `limit`.
 *
 * Walked outward from the winner rather than filtered across the whole scan,
 * because a far-off secondary minimum under the limit would otherwise widen the
 * interval and make a well-determined dimension look uncertain.
 */
function profileInterval(at, best, limit, lo, hi) {
    let low = best;
    for (let v = best - PROFILE_STEP_M; v >= lo; v -= PROFILE_STEP_M) {
        if (at(v) > limit) break;
        low = v;
    }
    let high = best;
    for (let v = best + PROFILE_STEP_M; v <= hi; v += PROFILE_STEP_M) {
        if (at(v) > limit) break;
        high = v;
    }
    // Touching a search bound means the scan ran out of room before the error
    // rose, so the data never chose this end — the bound did.
    const bounded = low <= lo + 1e-9 || high >= hi - 1e-9;
    const confident = !bounded
        && (high - low) <= MAX_INTERVAL_SHARE * (hi - lo);
    return { low, high, confident };
}

/**
 * Measure the pitch from the clicks themselves, or refuse to.
 *
 * The clicked landmarks carry their own scale, but only some of them do. A
 * corner is wherever you say the corner is, so a set of corners fits any size
 * exactly; the penalty box, the goal and the penalty spot are fixed distances
 * in the Laws, so one of those in the set pins the size of everything else.
 * That is the whole mechanism, and it is why a dimension can be refused while
 * the other is reported — measured on synthetic cameras, four corners give
 * nothing at all, and adding a single box corner recovers the true size exactly.
 *
 * Returns null when there are too few points, otherwise the best fit plus a
 * `confident` flag per dimension. An unconfident dimension has a real number
 * beside it and must not be shown as an answer: it is the bottom of a valley
 * so flat that the clicks are not choosing it.
 */
export function measureField(points, marks = null) {
    const known = landmarks();
    const entries = [...points].filter(([name]) => name in known);
    if (entries.length < MIN_MEASURABLE_POINTS) return null;

    let best = { lengthM: 105.0, widthM: 68.0, meanM: Infinity };
    const sweep = (loL, hiL, loW, hiW, step) => {
        for (let L = loL; L <= hiL + 1e-9; L += step) {
            for (let W = loW; W <= hiW + 1e-9; W += step) {
                const e = sizeError(entries, L, W, marks);
                if (e < best.meanM) best = { lengthM: L, widthM: W, meanM: e };
            }
        }
    };

    // Coarse then fine, rather than one pass at the fine step: the same answer
    // for about a tenth of the fits, which is what keeps this cheap enough to
    // re-run on every click instead of behind a button.
    sweep(MIN_LENGTH_M, MAX_LENGTH_M, MIN_WIDTH_M, MAX_WIDTH_M, COARSE_STEP_M);
    if (!Number.isFinite(best.meanM)) return null;

    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
    sweep(
        clamp(best.lengthM - COARSE_STEP_M, MIN_LENGTH_M, MAX_LENGTH_M),
        clamp(best.lengthM + COARSE_STEP_M, MIN_LENGTH_M, MAX_LENGTH_M),
        clamp(best.widthM - COARSE_STEP_M, MIN_WIDTH_M, MAX_WIDTH_M),
        clamp(best.widthM + COARSE_STEP_M, MIN_WIDTH_M, MAX_WIDTH_M),
        FINE_STEP_M,
    );

    const limit = Math.max(best.meanM * INTERVAL_TOL,
        best.meanM + INTERVAL_FLOOR_M);
    const lengthRange = profileInterval(
        (L) => sizeError(entries, L, best.widthM, marks),
        best.lengthM, limit, MIN_LENGTH_M, MAX_LENGTH_M,
    );
    const widthRange = profileInterval(
        (W) => sizeError(entries, best.lengthM, W, marks),
        best.widthM, limit, MIN_WIDTH_M, MAX_WIDTH_M,
    );

    return {
        lengthM: best.lengthM,
        widthM: best.widthM,
        meanM: best.meanM,
        points: entries.length,
        lengthConfident: lengthRange.confident,
        widthConfident: widthRange.confident,
        lengthRange: [lengthRange.low, lengthRange.high],
        widthRange: [widthRange.low, widthRange.high],
    };
}


// --------------------------------------------------- measuring the markings

// Where each dimension is allowed to land, and which clicked landmarks are
// evidence for it. The bounds are wide on purpose: a dimension is only
// reported when the error rises on both sides of the winner *inside* these
// bounds, so a range that hugged the plausible answer would hand out
// confidence the data had not earned.
const MARK_SPECS = [
    {
        key: 'penaltyAreaLengthM', label: 'penalty area depth',
        lo: 10.0, hi: 22.0, deps: /^pen_(left|right)_(bottom|top)_corner$/,
    },
    {
        key: 'penaltyAreaWidthM', label: 'penalty area width',
        lo: 24.0, hi: 50.0, deps: /^pen_(left|right)_(bottom|top)_/,
    },
    {
        key: 'goalAreaLengthM', label: 'six-yard box depth',
        lo: 3.0, hi: 9.0, deps: /^goalarea_/,
    },
    {
        key: 'goalAreaWidthM', label: 'six-yard box width',
        lo: 10.0, hi: 26.0, deps: /^goalarea_/,
    },
    {
        key: 'penaltySpotM', label: 'penalty spot',
        lo: 7.0, hi: 15.0, deps: /^pen_spot_/,
    },
    {
        key: 'goalWidthM', label: 'goal width',
        lo: 4.5, hi: 9.0, deps: /^goalpost_/,
    },
    {
        key: 'centreCircleRadiusM', label: 'centre circle radius',
        lo: 5.0, hi: 13.0, deps: /^centre_circle_/,
    },
];

const MARK_COARSE_STEP_M = 0.5;
const MARK_FINE_STEP_M = 0.1;

// Two passes of coordinate descent, not one. The dimensions are not
// independent — the box depth and the penalty spot both push landmarks along
// the same axis near the same goal — so the first pass fits each one against
// the others' starting values and the second against their settled ones. A
// third pass changed nothing measurable on the synthetic cameras.
const MARK_PASSES = 2;

// Paint is about 12cm wide and a coach with a tape is not going to beat that
// by much, so a marking within 30cm of the Laws value is a Laws marking as far
// as anyone can act on it. Below this, "your box is wrong" would be noise
// dressed as a finding.
const MARK_TOL_M = 0.3;

// How many times its own uncertainty a deviation has to be before it is
// allowed to contradict the Laws. See `claimReach`.
const MARK_SIGMA = 2.0;

// A dimension needs two clicked landmarks before it may say anything. This is
// the same identifiability argument as MIN_MEASURABLE_POINTS, one level down:
// a one-parameter family fitted to a single landmark reproduces that landmark
// exactly, whatever the landmark happens to be, so the fit that results is an
// interpolation wearing a measurement's clothes — zero residual, zero-width
// interval, maximum confidence, and no information in it at all.
//
// Measured: with only `pen_spot_left` clicked, dragging it two metres off the
// spot made this module announce a penalty spot at 13.00m with the interval
// [13.00..13.00] and the mean error falling from 0.448m to 0.003m. Every
// number in that sentence is a consequence of the point count, not of the
// paint.
const MIN_MARK_POINTS = 2;

// And how many clicked points there have to be in total before any marking may
// be questioned at all.
//
// Five is enough to fit a homography — that is MIN_MEASURABLE_POINTS, and it is
// the right bar for measuring the pitch's size, where the answer is pinned by
// the corners. It is not enough to argue with the paint. With six clicks the
// fit has barely more information than it has parameters, click noise has
// nowhere to go but into the markings, and the descent obligingly finds a
// mismarked box that is not there: measured over 60 seeds on a regulation
// pitch, six points invented a marking in 6 seeds at 2px of jitter and 15 at
// 6px, and seven points in 5 and 9. At eight it is 1 and 2, and past thirteen
// it is none at all.
//
// Nothing is lost by waiting. A genuinely mismarked pitch was caught in 60 of
// 60 seeds at every count from six to twenty-one, so the two extra clicks cost
// the coach nothing except two clicks — and they are the difference between a
// page that occasionally accuses a correctly painted field and one that does
// not.
const MIN_MARK_TOTAL = 8;

// How much of the error remeasuring the markings has to remove before the
// answer is allowed to stand.
//
// This is the other half of the same problem, and it catches what the point
// count cannot. A mismarked line displaces every landmark of its family the
// same way, so a model that gets the line right removes most of the residual;
// a single bad click is incoherent, and the family cannot absorb it however
// the dimension is set.
//
// The value is swept rather than picked, because the obvious choice is wrong.
// A share is diluted by every landmark that does not depend on the dimension
// in question, so the same mismarked box explains less and less of the total
// as the coach clicks more of the pitch — which would mean punishing them for
// doing the careful thing. Measured over 60 seeds on the synthetic sideline
// camera, a pitch with both boxes painted 1.5m shallow was caught 58 times out
// of 60 at eight clicked points but only ONCE out of 60 at twenty-one, with
// the bar at 0.5. That is the bar failing, not the pitch.
//
// At 0.35, across 8 to 21 points and 4px to 6px of click jitter:
//
//   - a regulation pitch invents a marking in 0 to 3 seeds of 60
//   - a genuinely mismarked one is caught in 39 to 60 of 60, worst case at
//     twenty-one points and 6px — where the failure is silence, not a false
//     accusation
//   - one box corner dragged 2m out of place explains between 0.276 and 0.301
//     depending on how much else was clicked, so it stays below this bar at
//     every point count
//
// Dropping to 0.25 lets that last case through at 8, 10 and 13 points and
// triples the invented markings. This is the widest gap available.
const MARK_EXPLAINED_MIN = 0.35;

// Below this the fit is already as good as the clicking, and the ratio above
// is dividing noise by noise.
const MARK_EXPLAIN_FLOOR_M = 0.05;

// Only the marking families have two ends to compare. Corners and the halfway
// line belong to the pitch rectangle, not to anybody's paint job — and their
// names contain "left" and "right" for reasons that have nothing to do with
// which goal they are near, which is exactly why this test is by prefix.
const MARKING_NAME = /^(pen_|goalarea_|goalpost_)/;

/**
 * How big a deviation this dimension has earned the right to report.
 *
 * A dimension being off the Laws is only a finding if it is bigger than the
 * measurement's own uncertainty, and the honest source for that uncertainty is
 * the interval `profileInterval` already returns: `reachM` is the distance
 * from the fitted value to the interval edge on the side the Laws value lies,
 * which is exactly how far the winner could have been pushed before the error
 * would have objected.
 *
 * A flat metre tolerance is the wrong shape, and the measurements say why. On
 * a regulation pitch clicked to 4px accuracy, the lengthwise dimensions — box
 * depth, six-yard depth, penalty spot — landed within a median of 0.10m of the
 * Laws value, while the widthwise ones — box width, goal width, centre circle
 * — scattered by 0.28m to 0.38m. That is not one of them being measured
 * better than another. It is the camera: a pitch seen from the touchline is
 * three times longer in pixels than it is tall, so a pixel of click error is
 * worth three times more metres across the pitch than along it. Any threshold
 * that does not scale with that will be too tight in one axis and too loose in
 * the other, and it was too loose — before this, a regulation pitch invented an
 * off-Laws marking in 2 runs out of 12.
 *
 * Telling a coach their centre circle is mismarked when it is not is worse
 * than telling them nothing, because unlike a residual it is an instruction to
 * go and repaint something.
 */
function claimReach(reachM) {
    return Math.max(MARK_TOL_M, MARK_SIGMA * reachM);
}

/** Coarse-then-fine 1-D minimum of `f` over [lo, hi], or null if never finite. */
function minimiseAxis(f, lo, hi) {
    let bestV = null;
    let bestE = Infinity;
    for (let v = lo; v <= hi + 1e-9; v += MARK_COARSE_STEP_M) {
        const e = f(v);
        if (e < bestE) { bestE = e; bestV = v; }
    }
    if (bestV === null || !Number.isFinite(bestE)) return null;

    const from = Math.max(lo, bestV - MARK_COARSE_STEP_M);
    const to = Math.min(hi, bestV + MARK_COARSE_STEP_M);
    for (let v = from; v <= to + 1e-9; v += MARK_FINE_STEP_M) {
        const e = f(v);
        if (e < bestE) { bestE = e; bestV = v; }
    }
    return bestV;
}

/** Which dimensions these clicks can say anything at all about. */
function liveSpecs(entries) {
    // A dimension no clicked landmark depends on leaves the error exactly
    // flat, and sweeping it would be reading a number off a horizontal line.
    // One landmark is no better — see MIN_MARK_POINTS — and a dimension that
    // does not appear here simply stays at its Laws value, which is the right
    // answer when there is nothing to say.
    return MARK_SPECS.filter(
        (spec) => entries.filter(
            ([name]) => spec.deps.test(name)).length >= MIN_MARK_POINTS);
}

/**
 * Coordinate-descend the live dimensions, then say which of them stuck.
 *
 * Returns `{ fitted, meanM, measured, adopted, offLaws }`, or null if the
 * clicks cannot be fitted at all. `fitted` holds every value the descent
 * landed on; `adopted` holds only the ones `profileInterval` was willing to
 * call measured, with the rest left at their Laws defaults — the bottom of a
 * flat valley is not a measurement and must not be handed out as one.
 */
function descendMarks(entries, lengthM, widthM) {
    const live = liveSpecs(entries);
    const fitted = { ...DEFAULT_MARKS };
    const measured = {};
    const adopted = { ...DEFAULT_MARKS };
    const offLaws = [];

    if (!live.length) {
        const flat = sizeError(entries, lengthM, widthM, DEFAULT_MARKS);
        if (!Number.isFinite(flat)) return null;
        return { fitted, meanM: flat, measured, adopted, offLaws };
    }

    const at = (key, v) => sizeError(entries, lengthM, widthM,
        { ...fitted, [key]: v });

    for (let pass = 0; pass < MARK_PASSES; pass++) {
        for (const spec of live) {
            const best = minimiseAxis((v) => at(spec.key, v), spec.lo, spec.hi);
            if (best !== null) fitted[spec.key] = best;
        }
    }

    const meanM = sizeError(entries, lengthM, widthM, fitted);
    if (!Number.isFinite(meanM)) return null;

    // Same tolerance rule as `measureField`, and for the same reason: a
    // dimension counts as measured only when moving away from it costs error.
    const limit = Math.max(meanM * INTERVAL_TOL, meanM + INTERVAL_FLOOR_M);

    for (const spec of live) {
        const value = fitted[spec.key];
        const defaultM = DEFAULT_MARKS[spec.key];
        const points = entries.filter(([name]) => spec.deps.test(name)).length;
        const range = profileInterval(
            (v) => at(spec.key, v), value, limit, spec.lo, spec.hi);
        // The interval edge on the side the Laws value sits, which is the
        // distance this dimension could have drifted without the error
        // objecting. Clearing it by a factor is what makes this a finding
        // rather than a reading of the click noise; it also subsumes the
        // plainer test of whether the Laws value fell outside the interval,
        // since anything twice the reach is outside it by construction.
        const reachM = defaultM < value
            ? value - range.low
            : range.high - value;
        const off = range.confident
            && Math.abs(value - defaultM) >= claimReach(reachM);
        measured[spec.key] = {
            label: spec.label,
            valueM: value,
            defaultM,
            low: range.low,
            high: range.high,
            reachM,
            confident: range.confident,
            offLaws: off,
            points,
        };
        if (range.confident) adopted[spec.key] = value;
        if (off) offLaws.push(spec.key);
    }

    return { fitted, meanM, measured, adopted, offLaws };
}

/**
 * Do the two ends of the pitch have the same paint on them?
 *
 * Measured by fitting each end on its own clicks and comparing the two
 * answers, which is not the same as comparing the two ends' residuals under a
 * shared model. That first attempt does not work and it is worth saying why:
 * least squares distributes a mismatch evenly, so on a synthetic camera with
 * one box marked 13.0m against the other's 16.5m the two ends' mean residuals
 * differed by 0.02m. The signal was there; the statistic was blind to it.
 *
 * This does re-fit each end separately, which the module otherwise refuses to
 * do — but it never adopts either answer. The split values exist only to be
 * compared, and both have to be independently confident before the comparison
 * is allowed to happen at all. Reporting that the ends disagree is honest;
 * reporting two separate box depths off ten clicks would not be.
 */
function endDisagreements(entries, lengthM, widthM) {
    const shared = entries.filter(([name]) => !MARKING_NAME.test(name));
    const forEnd = (end) => shared.concat(entries.filter(
        ([name]) => MARKING_NAME.test(name) && name.includes(end)));

    const left = forEnd('left');
    const right = forEnd('right');
    // Per end, the looser bar rather than MIN_MARK_TOTAL. Requiring eight at
    // each end would mean sixteen clicks before the page could see a lopsided
    // pitch, and this comparison does not need the same protection: it never
    // adopts a value, and it already demands that both ends be independently
    // confident and that the gap between them clear both intervals. Measured
    // over 12 noise seeds on a regulation pitch, the worst gap it reported was
    // 0.00m and it named nothing.
    if (left.length < MIN_MEASURABLE_POINTS) return null;
    if (right.length < MIN_MEASURABLE_POINTS) return null;

    const l = descendMarks(left, lengthM, widthM);
    const r = descendMarks(right, lengthM, widthM);
    if (!l || !r) return null;

    const out = [];
    for (const spec of MARK_SPECS) {
        const a = l.measured[spec.key];
        const b = r.measured[spec.key];
        if (!a || !b || !a.confident || !b.confident) continue;
        const gapM = Math.abs(a.valueM - b.valueM);
        // Each end brings its own uncertainty and the gap has to clear both.
        // This bar is high, and it is high for a reason: fitting one end on
        // half the clicks widens its interval, so two identically painted ends
        // routinely land half a metre apart on nothing but noise. Anything
        // that reports *that* as a lopsided pitch is worse than silence.
        // Half the interval, not `reachM`: which side the Laws value fell
        // on is not the question here, the two ends are.
        const half = (m) => (m.high - m.low) / 2;
        const floor = claimReach(half(a)) + claimReach(half(b));
        if (gapM < floor) continue;
        out.push({
            key: spec.key, label: spec.label,
            leftM: a.valueM, rightM: b.valueM, gapM,
        });
    }
    if (!out.length) return null;
    out.sort((x, y) => y.gapM - x.gapM);
    return out;
}

/**
 * Measure the paint on this field from the clicks, or refuse to.
 *
 * The point of this is leniency that is not permissiveness. When a fit misses
 * the bar there are two very different reasons, and the picker has always
 * blamed the coach for both: either the clicks were sloppy, or the model was
 * wrong about the pitch. Those are separable, and this is what separates them.
 * A mismarked box displaces every landmark of that family the same way, so the
 * residual it leaves is coherent, and shows up here as a dimension whose error
 * has a clear minimum somewhere other than the Laws value. Click error has no
 * such structure: sweeping a dimension through scattered clicks flattens the
 * valley rather than moving it, and `profileInterval` then refuses.
 *
 * Measured on a synthetic camera over a 100×64 pitch whose box was painted
 * 15.0m deep and 38.0m wide with the spot at 10.0m: the Laws model cost 0.79m
 * of mean error, the measured one cost 0.00m, and all three numbers came back
 * exact. That 0.79m is error no amount of re-clicking could ever have removed,
 * and until now the page reported it as the coach's aim.
 *
 * One set of markings serves both ends, as in cv/pitch.py. The two boxes are
 * painted by the same person with the same tape on the same morning, so they
 * are usually wrong together, and eight or ten clicks is not enough evidence
 * to fit them apart; splitting them would produce two confident-looking
 * numbers where the data supports one. `asymmetry` is the honest half of that,
 * and it is a list of disagreements rather than a second model.
 *
 * `lengthM` and `widthM` are an input, not something this re-derives. The
 * coach is told to put a tape on the pitch, and a typed length is better
 * evidence than a length inferred from the very markings being questioned.
 * Feeding it `measureField`'s answer instead is circular, and would read as
 * confident when it is not.
 */
export function measureMarkings(points, lengthM = 105.0, widthM = 68.0) {
    const known = landmarks();
    const entries = [...points].filter(([name]) => name in known);
    if (entries.length < MIN_MARK_TOTAL) return null;

    const baseMeanM = sizeError(entries, lengthM, widthM, DEFAULT_MARKS);
    if (!Number.isFinite(baseMeanM)) return null;

    const fit = descendMarks(entries, lengthM, widthM);
    if (!fit) return null;

    const fittedM = sizeError(entries, lengthM, widthM, fit.adopted);
    const meanM = Number.isFinite(fittedM) ? fittedM : baseMeanM;

    // How much of the error the remeasured pitch actually accounts for. Null
    // rather than 1.0 when there was no error to account for: a perfectly
    // clicked regulation pitch has nothing to explain, and calling that a
    // complete explanation would be arithmetic rather than evidence.
    const explained = baseMeanM > MARK_EXPLAIN_FLOOR_M
        ? (baseMeanM - meanM) / baseMeanM
        : null;

    // Not the pitch, then. Something is wrong with this calibration, but
    // remeasuring the paint did not account for it, so the values the descent
    // landed on are fitting whatever is wrong rather than describing a field
    // — and they are withheld rather than offered. `measured` still travels,
    // because a coach comparing numbers is entitled to see them; what it does
    // not travel with is permission to act on them.
    const trusted = explained === null || explained >= MARK_EXPLAINED_MIN;

    // Asymmetry is deliberately not gated on any of that, and the reason is
    // the whole argument for keeping it as a separate signal. One end painted
    // wrong is precisely the case where a single shared set of markings
    // explains almost nothing: the descent lands on the compromise between the
    // two ends, misses both, and `explained` comes out low. Measured on the
    // synthetic camera, a pitch with one mismarked end explained 15% — inside
    // the range where a shared answer is refused. Suppressing the end
    // comparison there would silence the one signal built for that exact
    // field. A low share is a symptom of asymmetry, not evidence against it.
    //
    // What keeps it honest instead is its own discipline: each end is re-fitted
    // on its own clicks, both have to be independently confident, the gap has
    // to clear both intervals, and neither value is ever adopted.
    const asymmetry = endDisagreements(entries, lengthM, widthM);

    return {
        lengthM,
        widthM,
        points: entries.length,
        // What the Laws model costs against what the measured one costs. The
        // difference between these two is the part of the error that re-doing
        // the clicking could never have removed.
        baseMeanM,
        meanM: trusted ? meanM : baseMeanM,
        explained,
        trusted,
        marks: trusted ? fit.adopted : { ...DEFAULT_MARKS },
        measured: fit.measured,
        offLaws: trusted ? fit.offLaws : [],
        asymmetry,
    };
}
