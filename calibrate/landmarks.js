// The landmarks a person can pin on a picture of the pitch, and where each one is.
//
// Two calibrators read the pins, and they name the same points differently:
//
//   pitchiq/pitch.py `Pitch.landmarks()`    corner_bottom_left, pen_left_top_corner, ...
//                                      read by `python -m pitchiq.experiments.calibrate`
//                                      (a fixed camera: one frame, one homography)
//   the pin solve (`pin_cal.py`)       'left corner near', 'left box far', ...
//                                      (a panning camera: pins on its panorama)
//
// Each row carries both names, so one set of clicks feeds either. "Left" is the
// goal on the left of the picture. "Near", or "bottom" in pitchiq/pitch.py, is the
// touchline on the camera's side. The goalposts have no pin-solve name; it
// ignores them.
//
// tests/test_calibrate_landmarks.py holds the names to pitchiq/pitch.py and the
// positions to `Pitch.landmarks()`.

import { MARKINGS_M } from '../assets/pitch-backdrop.js?v=137';

// [pitchiq/pitch.py name, pin-solve name or null, label, suggested]
// Suggested: the spread the solve does best with when only a few can be placed.
export const GROUPS = [
    ['Halfway', [
        ['centre_spot', 'centre spot', 'Centre spot', true],
        ['halfway_top', 'halfway far', 'Halfway line meets far touchline', true],
        ['halfway_bottom', 'halfway near', 'Halfway line meets near touchline', true],
        ['centre_circle_top', 'circle far', 'Centre circle, far side', false],
        ['centre_circle_bottom', 'circle near', 'Centre circle, near side', false],
    ]],
    ['Left end', [
        ['corner_top_left', 'left corner far', 'Far corner', true],
        ['corner_bottom_left', 'left corner near', 'Near corner', false],
        ['pen_left_top_corner', 'left box far', 'Penalty box corner, far', true],
        ['pen_left_bottom_corner', 'left box near', 'Penalty box corner, near', true],
        ['pen_left_top_goalline', 'left box line far', 'Penalty box meets goal line, far', false],
        ['pen_left_bottom_goalline', 'left box line near', 'Penalty box meets goal line, near', false],
        ['goalarea_left_top_corner', 'left goal area far', 'Six-yard box corner, far', false],
        ['goalarea_left_bottom_corner', 'left goal area near', 'Six-yard box corner, near', false],
        ['pen_spot_left', 'left spot', 'Penalty spot', false],
        ['goalpost_left_top', null, 'Goalpost foot, far', false],
        ['goalpost_left_bottom', null, 'Goalpost foot, near', false],
    ]],
    ['Right end', [
        ['corner_top_right', 'right corner far', 'Far corner', true],
        ['corner_bottom_right', 'right corner near', 'Near corner', false],
        ['pen_right_top_corner', 'right box far', 'Penalty box corner, far', true],
        ['pen_right_bottom_corner', 'right box near', 'Penalty box corner, near', true],
        ['pen_right_top_goalline', 'right box line far', 'Penalty box meets goal line, far', false],
        ['pen_right_bottom_goalline', 'right box line near', 'Penalty box meets goal line, near', false],
        ['goalarea_right_top_corner', 'right goal area far', 'Six-yard box corner, far', false],
        ['goalarea_right_bottom_corner', 'right goal area near', 'Six-yard box corner, near', false],
        ['pen_spot_right', 'right spot', 'Penalty spot', false],
        ['goalpost_right_top', null, 'Goalpost foot, far', false],
        ['goalpost_right_bottom', null, 'Goalpost foot, near', false],
    ]],
];

export const LANDMARKS = GROUPS.flatMap(([group, rows]) => rows.map(
    ([name, pin, label, suggested]) => ({ name, pin, label, suggested, group })));

/**
 * Every landmark's position in metres on a `length` x `width` pitch with Laws
 * markings: x along the pitch from the left goal line, y across it from the
 * near touchline. The same geometry as `Pitch.landmarks()`.
 */
export function positions(length, width) {
    const M = MARKINGS_M;
    const L = length, W = width, cy = W / 2;
    const pa = M.penaltyAreaWidth / 2, ga = M.goalAreaWidth / 2, gp = M.goalWidth / 2;
    const out = {
        centre_spot: [L / 2, cy],
        halfway_top: [L / 2, W],
        halfway_bottom: [L / 2, 0],
        centre_circle_top: [L / 2, cy + M.centreCircleRadius],
        centre_circle_bottom: [L / 2, cy - M.centreCircleRadius],
    };
    for (const [side, g, s] of [['left', 0, 1], ['right', L, -1]]) {
        out[`corner_top_${side}`] = [g, W];
        out[`corner_bottom_${side}`] = [g, 0];
        out[`pen_${side}_top_corner`] = [g + s * M.penaltyAreaLength, cy + pa];
        out[`pen_${side}_bottom_corner`] = [g + s * M.penaltyAreaLength, cy - pa];
        out[`pen_${side}_top_goalline`] = [g, cy + pa];
        out[`pen_${side}_bottom_goalline`] = [g, cy - pa];
        out[`goalarea_${side}_top_corner`] = [g + s * M.goalAreaLength, cy + ga];
        out[`goalarea_${side}_bottom_corner`] = [g + s * M.goalAreaLength, cy - ga];
        out[`pen_spot_${side}`] = [g + s * M.penaltySpot, cy];
        out[`goalpost_${side}_top`] = [g, cy + gp];
        out[`goalpost_${side}_bottom`] = [g, cy - gp];
    }
    return out;
}

/**
 * The painted lines as polylines in metres, for drawing the fitted pitch back
 * over the picture. Arcs are sampled finely enough to look round when
 * projected.
 */
export function lines(length, width) {
    const M = MARKINGS_M;
    const L = length, W = width, cy = W / 2;
    const rect = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]];
    const arc = (cx, cyy, r, a0, a1, n = 48) => Array.from({ length: n + 1 }, (_, i) => {
        const a = a0 + (a1 - a0) * i / n;
        return [cx + r * Math.cos(a), cyy + r * Math.sin(a)];
    });
    const out = [
        rect(0, 0, L, W),
        [[L / 2, 0], [L / 2, W]],
        arc(L / 2, cy, M.centreCircleRadius, 0, 2 * Math.PI, 96),
    ];
    const pa = M.penaltyAreaWidth / 2, ga = M.goalAreaWidth / 2;
    // The arc at the top of each box: the part of the spot's 9.15 m circle outside it.
    const a = Math.acos((M.penaltyAreaLength - M.penaltySpot) / M.centreCircleRadius);
    out.push(rect(0, cy - pa, M.penaltyAreaLength, cy + pa));
    out.push(rect(L - M.penaltyAreaLength, cy - pa, L, cy + pa));
    out.push(rect(0, cy - ga, M.goalAreaLength, cy + ga));
    out.push(rect(L - M.goalAreaLength, cy - ga, L, cy + ga));
    out.push(arc(M.penaltySpot, cy, M.centreCircleRadius, -a, a));
    out.push(arc(L - M.penaltySpot, cy, M.centreCircleRadius, Math.PI - a, Math.PI + a));
    return out;
}
