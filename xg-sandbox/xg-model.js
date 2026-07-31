// The xG model: turning a set of positions into a shot-quality number.
//
// Split out from the canvas code so the one part that has to stay in lockstep
// with the trained model lives on its own. FEATURE_ORDER below must match
// FEATURES in PitchIQHelper/main.py and FEATURE_ORDER in cv/xg_bridge.py
// exactly — the model takes a bare 12-wide float array with no names attached,
// so a reordering here fails silently rather than loudly.

const MODEL_URL = './xg_model6.onnx';

export const FEATURE_ORDER = [
    'distance_to_goal',
    'angle_to_goal',
    'is_foot',
    'is_header',
    'under_pressure',
    'is_open_play',
    'shot_height',
    'keeper_distance_to_goal',
    'keeper_angle_coverage',
    'keeper_off_line',
    'defenders_in_cone',
    'defender_pressure',
];

// StatsBomb pitch: 120 x 80, attacking towards x = 120.
const SB_LENGTH = 120.0;
const SB_WIDTH = 80.0;
const GOAL_CENTRE = { x: SB_LENGTH, y: SB_WIDTH / 2 };
const POST_LEFT = { x: SB_LENGTH, y: 36.0 };
const POST_RIGHT = { x: SB_LENGTH, y: 44.0 };

// A keeper this far off their line is treated as having come out.
const KEEPER_OFF_LINE_M = 3.0;

// What the training script substitutes when a freeze frame has no keeper in it
// (PitchIQHelper/main.py). Feeding the model anything else here is feeding it a
// value it never saw while learning.
const NO_KEEPER_DISTANCE = 5.0;
const NO_KEEPER_POSITION = { x: 115.0, y: 40.0 };

// Stand-in for the z-axis when the height of the shot is unknown, matching
// DEFAULT_SHOT_HEIGHT in cv/xg_bridge.py.
const DEFAULT_SHOT_HEIGHT = 0.6;

const dot = (a, b) => a.x * b.x + a.y * b.y;
const minus = (a, b) => ({ x: a.x - b.x, y: a.y - b.y });
const length = (v) => Math.hypot(v.x, v.y);

/**
 * Sandbox coordinates (0-1 across the width, 0-1 from the goal line) into
 * StatsBomb space. The sandbox draws the attack moving up the screen, so its
 * y axis runs the opposite way to StatsBomb's x.
 */
export function toStatsBomb(position) {
    return {
        x: (1.0 - position.y) * SB_LENGTH,
        y: position.x * SB_WIDTH,
    };
}

/** Angle subtended by the goalmouth from a point, in radians. */
export function shotAngle(location) {
    const toLeft = minus(POST_LEFT, location);
    const toRight = minus(POST_RIGHT, location);
    const denominator = length(toLeft) * length(toRight) + 1e-9;
    const cosine = dot(toLeft, toRight) / denominator;
    return Math.acos(Math.max(-1, Math.min(1, cosine)));
}

/** Is this point inside the triangle from the ball to both posts? */
export function inShotCone(point, ball) {
    const v0 = minus(POST_RIGHT, ball);
    const v1 = minus(POST_LEFT, ball);
    const v2 = minus(point, ball);

    const d00 = dot(v0, v0);
    const d01 = dot(v0, v1);
    const d11 = dot(v1, v1);
    const d02 = dot(v0, v2);
    const d12 = dot(v1, v2);

    const inverse = 1.0 / (d00 * d11 - d01 * d01 + 1e-9);
    const u = (d11 * d02 - d01 * d12) * inverse;
    const v = (d00 * d12 - d01 * d02) * inverse;

    return u >= 0 && v >= 0 && (u + v) < 1;
}

/**
 * The 12 numbers the model wants, from positions the sandbox already holds.
 *
 * `shot` carries the toggles and the height slider. Note there is no free-kick
 * feature: direct free kicks were dropped from the training set entirely
 * (PitchIQHelper/main.py), so the model has no way to represent one.
 */
export function buildFeatures({ shooter, keeper, defenders, shot }) {
    const ball = toStatsBomb(shooter);

    const features = {
        distance_to_goal: Math.hypot(GOAL_CENTRE.x - ball.x, GOAL_CENTRE.y - ball.y),
        angle_to_goal: shotAngle(ball),
        is_foot: shot.isFoot ? 1 : 0,
        is_header: shot.isHeader ? 1 : 0,
        under_pressure: shot.underPressure ? 1 : 0,
        is_open_play: shot.isOpenPlay ? 1 : 0,
        shot_height: shot.height ?? DEFAULT_SHOT_HEIGHT,
        // The no-keeper case, which the sandbox never reaches because it always
        // draws one — but the CV pipeline reaches it whenever the keeper is out
        // of frame or missed. This used to substitute the shooter's own angle,
        // which is not a value the model was ever trained on.
        keeper_distance_to_goal: NO_KEEPER_DISTANCE,
        keeper_angle_coverage: shotAngle(NO_KEEPER_POSITION),
        keeper_off_line: 0,
        defenders_in_cone: 0,
        defender_pressure: 0.0,
    };

    if (keeper) {
        const k = toStatsBomb(keeper);
        features.keeper_distance_to_goal =
            Math.hypot(GOAL_CENTRE.x - k.x, GOAL_CENTRE.y - k.y);
        features.keeper_angle_coverage = shotAngle(k);
        features.keeper_off_line =
            features.keeper_distance_to_goal > KEEPER_OFF_LINE_M ? 1 : 0;
    }

    // Only defenders actually between the ball and the goal count. Pressure is
    // the sum of inverse distances, so one defender at the shooter's feet
    // outweighs three standing on the line.
    for (const defender of defenders) {
        const d = toStatsBomb(defender);
        if (!inShotCone(d, ball)) continue;
        features.defenders_in_cone += 1;
        features.defender_pressure += 1.0 / (length(minus(d, ball)) + 1e-9);
    }

    return features;
}

export function featureVector(features) {
    return Float32Array.from(FEATURE_ORDER.map((name) => features[name]));
}

// One session for the page, created on first use.
let sessionPromise = null;

function loadSession() {
    if (!sessionPromise) {
        sessionPromise = ort.InferenceSession.create(MODEL_URL);
    }
    return sessionPromise;
}

/**
 * Probability that this shot is a goal, or null if the model could not be run.
 *
 * The classifier emits both a label and a probability pair; we want the second
 * element of the pair, so the output with two or more values is the one to read.
 */
export async function predictXg(shotSetup) {
    let session;
    try {
        session = await loadSession();
    } catch (err) {
        console.error('Could not load the xG model:', err);
        sessionPromise = null;   // let a later call retry
        return null;
    }

    const vector = featureVector(buildFeatures(shotSetup));
    const tensor = new ort.Tensor('float32', vector, [1, FEATURE_ORDER.length]);

    try {
        const results = await session.run({ float_input: tensor });
        for (const name of session.outputNames) {
            const output = results[name];
            if (output?.data?.length >= 2) return Number(output.data[1]);
        }
        console.error('No probability pair in the model output:', results);
        return null;
    } catch (err) {
        console.error('xG inference failed:', err);
        return null;
    }
}
