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

/** Landmark coordinates in metres for a pitch of the given size. */
export function landmarks(lengthM = 105.0, widthM = 68.0) {
    const L = lengthM;
    const W = widthM;
    const cy = W / 2;
    const paHalf = PENALTY_AREA_WIDTH_M / 2;
    const gaHalf = GOAL_AREA_WIDTH_M / 2;

    return {
        corner_bottom_left: [0.0, 0.0],
        corner_top_left: [0.0, W],
        corner_bottom_right: [L, 0.0],
        corner_top_right: [L, W],

        halfway_bottom: [L / 2, 0.0],
        halfway_top: [L / 2, W],
        centre_spot: [L / 2, cy],
        centre_circle_bottom: [L / 2, cy - CENTRE_CIRCLE_RADIUS_M],
        centre_circle_top: [L / 2, cy + CENTRE_CIRCLE_RADIUS_M],

        pen_left_bottom_goalline: [0.0, cy - paHalf],
        pen_left_top_goalline: [0.0, cy + paHalf],
        pen_left_bottom_corner: [PENALTY_AREA_LENGTH_M, cy - paHalf],
        pen_left_top_corner: [PENALTY_AREA_LENGTH_M, cy + paHalf],
        pen_spot_left: [PENALTY_SPOT_M, cy],

        pen_right_bottom_goalline: [L, cy - paHalf],
        pen_right_top_goalline: [L, cy + paHalf],
        pen_right_bottom_corner: [L - PENALTY_AREA_LENGTH_M, cy - paHalf],
        pen_right_top_corner: [L - PENALTY_AREA_LENGTH_M, cy + paHalf],
        pen_spot_right: [L - PENALTY_SPOT_M, cy],

        goalarea_left_bottom_corner: [GOAL_AREA_LENGTH_M, cy - gaHalf],
        goalarea_left_top_corner: [GOAL_AREA_LENGTH_M, cy + gaHalf],
        goalarea_right_bottom_corner: [L - GOAL_AREA_LENGTH_M, cy - gaHalf],
        goalarea_right_top_corner: [L - GOAL_AREA_LENGTH_M, cy + gaHalf],

        goalpost_left_bottom: [0.0, cy - GOAL_WIDTH_M / 2],
        goalpost_left_top: [0.0, cy + GOAL_WIDTH_M / 2],
        goalpost_right_bottom: [L, cy - GOAL_WIDTH_M / 2],
        goalpost_right_top: [L, cy + GOAL_WIDTH_M / 2],
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
function sizeError(entries, lengthM, widthM) {
    const marks = landmarks(lengthM, widthM);
    let H;
    try {
        H = fitHomography(entries.map(([name, px]) => ({
            src: px, dst: marks[name],
        })));
    } catch {
        return Infinity;
    }

    let total = 0;
    for (const [name, px] of entries) {
        const [x, y] = applyHomography(H, px[0], px[1]);
        const [tx, ty] = marks[name];
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
export function measureField(points) {
    const known = landmarks();
    const entries = [...points].filter(([name]) => name in known);
    if (entries.length < MIN_MEASURABLE_POINTS) return null;

    let best = { lengthM: 105.0, widthM: 68.0, meanM: Infinity };
    const sweep = (loL, hiL, loW, hiW, step) => {
        for (let L = loL; L <= hiL + 1e-9; L += step) {
            for (let W = loW; W <= hiW + 1e-9; W += step) {
                const e = sizeError(entries, L, W);
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
        (L) => sizeError(entries, L, best.widthM),
        best.lengthM, limit, MIN_LENGTH_M, MAX_LENGTH_M,
    );
    const widthRange = profileInterval(
        (W) => sizeError(entries, best.lengthM, W),
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
