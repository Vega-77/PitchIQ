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
