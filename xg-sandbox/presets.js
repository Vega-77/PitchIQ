// Shots worth looking at, as starting positions for the sandbox.
//
// Dragging ten dots around until they resemble a chance you had in mind is slow
// and the result is never quite the shot you meant. These are the handful of
// situations anyone actually wants to ask the model about, placed once and
// correctly, so the page opens on a question instead of on a shrug.
//
//     Written in metres, not in the sandbox's own coordinates.
//
// The canvas works in 0-1 across a half pitch, which is the right thing for a
// canvas and unreadable as a list of positions: nobody can see that 0.2095 is
// the penalty spot. Everything below is `[across, from the goal line]` in
// metres, converted in one place. That also makes the last two entries possible
// — they are lifted straight out of the sample match in
// `assets/sample-report.js`, whose shots are published in metres, so the number
// the preview shows for a chance can be reproduced here by clicking its name.
// tests/test_sample_xg.py checks those two still line up.
//
// The penalty is a real entry rather than a toggle because there is no penalty
// feature: the model sees `is_open_play = 0` and a shot from twelve yards, and
// that is the whole of what makes it a penalty. Direct free kicks are missing
// for the opposite reason — they were dropped from the training set, so there
// is nothing to place.

import { Vector } from './geometry.js?v=107';

// The half the sandbox draws. Matching WIDTH_M and HALF_LENGTH_M in sandbox.js,
// and the 105x68 default in cv/pitch.py that the sample match is measured on.
export const WIDTH_M = 68;
export const HALF_LENGTH_M = 52.5;

const CENTRE_M = WIDTH_M / 2;

/** `[across, out from the goal line]` in metres to the canvas's 0-1 space. */
export function fromMetres([acrossM, fromGoalM]) {
    return new Vector(acrossM / WIDTH_M, fromGoalM / HALF_LENGTH_M);
}

// Defaults, so each preset only states what it is actually about. A preset that
// had to list four toggles to say "an ordinary shot" would bury the one that
// matters.
const OPEN_PLAY = {
    isFoot: true, isHeader: false, underPressure: false, isOpenPlay: true,
};

export const PRESETS = [
    {
        id: 'penalty',
        name: 'Penalty',
        detail: 'Twelve yards, keeper on his line, nobody else close. Reads far '
            + 'lower than the ~0.76 a penalty is worth, and that is the model '
            + 'being caught out: almost every penalty it trained on had no '
            + 'keeper in the freeze frame at all, so it has barely seen one '
            + 'standing on the line from twelve yards.',
        shooter: [CENTRE_M, 11],
        keeper: [CENTRE_M, 0.4],
        defenders: [[26, 17.5], [42, 17.5], [30, 20], [38, 20]],
        attackers: [[28, 15], [40, 15], [CENTRE_M, 23], [24, 21]],
        shot: { ...OPEN_PLAY, isOpenPlay: false },
    },
    {
        id: 'tap-in',
        name: 'Tap-in',
        detail: 'Four metres out with the keeper beaten to the near post. About '
            + 'as good as a chance gets.',
        shooter: [33, 4],
        keeper: [29, 2.5],
        defenders: [[36, 3], [40, 7], [27, 9], [44, 12]],
        attackers: [[38, 6], [30, 10], [CENTRE_M, 16], [46, 14]],
        shot: { ...OPEN_PLAY },
    },
    {
        id: 'edge-of-the-box',
        name: 'Edge of the box',
        detail: 'Eighteen metres, central, a defender stepping across and a man '
            + 'closing him down. The shot most people overrate.',
        shooter: [CENTRE_M, 18],
        keeper: [CENTRE_M, 1.5],
        defenders: [[34.6, 15], [31, 12], [38, 13], [30, 21]],
        attackers: [[27, 16], [41, 17], [CENTRE_M, 26], [22, 24]],
        shot: { ...OPEN_PLAY, underPressure: true },
    },
    {
        id: 'tight-angle',
        name: 'Tight angle',
        detail: 'Seven metres out and almost on the byline. Close to the goal '
            + 'and hardly any of it to aim at — the case distance alone gets wrong.',
        shooter: [52, 7],
        keeper: [37, 1.2],
        defenders: [[49, 5.5], [45, 9], [40, 13], [CENTRE_M, 17]],
        attackers: [[40, 5], [30, 9], [CENTRE_M, 15], [24, 19]],
        shot: { ...OPEN_PLAY, underPressure: true },
    },
    {
        id: 'one-on-one',
        name: 'One on one',
        detail: 'Fourteen metres with the keeper seven off his line and the '
            + 'defence behind the ball. Nothing in the shooting lane at all.',
        shooter: [CENTRE_M, 14],
        keeper: [CENTRE_M, 7],
        defenders: [[29, 21], [40, 22], [24, 27], [45, 28]],
        attackers: [[26, 18], [43, 19], [CENTRE_M, 27], [20, 25]],
        shot: { ...OPEN_PLAY },
    },
    {
        id: 'long-range',
        name: 'Long range',
        detail: 'Twenty-eight metres with bodies in front of it. Worth almost '
            + 'nothing, and worth seeing be worth almost nothing.',
        shooter: [CENTRE_M, 28],
        keeper: [CENTRE_M, 1.5],
        defenders: [[34.4, 25], [31, 23], [38, 22], [28, 19]],
        attackers: [[26, 24], [42, 25], [CENTRE_M, 34], [20, 30]],
        shot: { ...OPEN_PLAY, underPressure: true },
    },
    // The two below are the sample match's shots, in the same metres the
    // fixture publishes: a shot at x_m on a 105 m pitch attacking right is
    // 105 - x_m out from the goal line, and y_m across.
    {
        id: 'sample-opener',
        name: 'Sample match — the opener',
        detail: 'The goal at 6:52 in the preview data. Thirteen metres, two '
            + 'defenders in the lane. Worth 0.098 — and it went in, which is '
            + 'most goals: the chance was never the likely outcome.',
        shooter: [33.8, 12.9],
        keeper: [34.4, 1.8],
        defenders: [[34.3, 9.4], [32.7, 6.9], [28, 18], [40, 19]],
        attackers: [[28, 11], [41, 12], [CENTRE_M, 22], [23, 20]],
        shot: { ...OPEN_PLAY, underPressure: true },
    },
    {
        id: 'sample-miss',
        name: 'Sample match — the miss',
        detail: 'The best chance in the preview data at 0.479, six metres out '
            + 'with the keeper committed. It went off target, which is the '
            + 'whole reason a coach wants this number.',
        shooter: [38.4, 5.8],
        keeper: [36.4, 3.4],
        defenders: [[38.7, 1.3], [30, 8], [44, 10], [CENTRE_M, 16]],
        attackers: [[31, 6], [44, 7], [CENTRE_M, 14], [25, 17]],
        shot: { ...OPEN_PLAY },
    },
];

/** A preset by id, or undefined. */
export const presetById = (id) => PRESETS.find((preset) => preset.id === id);
