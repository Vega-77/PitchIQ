"""Turn a detected shot into the 12 features the existing xG model expects.

    STATUS — the model half is verified; the shot-detection half is not.

`build_features`, `feature_vector` and `_predict` now run against the real model
and agree with the browser to three decimal places on the same shot; see
tests/test_xg_parity.py. What remains unverified is upstream of here: whether a
shot can be spotted in footage at all, and whether the shooter, keeper and
defenders handed to `ShotContext` are the right ones.

This is where the computer vision work meets the model already trained in
`PitchIQHelper/main.py` and already running in `xg-sandbox/xg-model.js`. The feature
order below must match `FEATURES` in main.py exactly; a mismatch does not raise,
it silently produces a plausible-looking wrong number.

Two things are known to be unresolved rather than merely untested:

* **shot_height is not recoverable here.** It is a z-axis value, and a
  homography maps a plane. There is no way to get it from one fixed camera
  without pose estimation or fitting the ball's flight arc. The median from the
  training data is substituted, which is honest but means the feature carries no
  information from this shot.

* **The model has never seen CV-derived inputs.** It was trained on clean,
  human-verified StatsBomb data. Positions from detection and homography are
  noisier, and a calibrated model can lose its calibration on inputs noisier
  than its training set. `validate_against_noise` exists to measure that before
  anyone trusts the output.

* **Pitch dimensions change the answer.** `Pitch.to_statsbomb` normalises by
  the configured pitch length, so a penalty spot — 11m out on any field — reads
  as ~13.9 StatsBomb units on a 95m pitch and ~12.0 on a 110m one, and the model
  sees two different shots. Whether that matches how StatsBomb normalises their
  own data has not been confirmed against their documentation; if they assume a
  standard pitch instead, this should use a fixed metres-per-unit factor. Until
  that is settled, the pitch dimensions in a calibration are not a cosmetic
  setting — measure the real field. Pinned by a test in tests/test_xg_bridge.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .calibration import Calibration
from .pitch import STATSBOMB_LENGTH, STATSBOMB_WIDTH, MatchOrientation, Pitch

# Order matters and must mirror FEATURES in PitchIQHelper/main.py.
FEATURE_ORDER = [
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
]

# StatsBomb goal geometry, matching main.py.
GOAL = np.array([STATSBOMB_LENGTH, STATSBOMB_WIDTH / 2])
POST_L = np.array([STATSBOMB_LENGTH, 36.0])
POST_R = np.array([STATSBOMB_LENGTH, 44.0])

# Stand-in for the unobtainable z-axis. Taken from the training distribution;
# see the module docstring.
DEFAULT_SHOT_HEIGHT = 0.6

# The model the browser loads, and therefore the model behind every xG number
# anyone has actually looked at.
#
# There is a second file of the same name in PitchIQHelper/, the output of a
# later re-run of the training script that was never adopted. The two are not
# interchangeable — they disagree by up to 0.29 xG on the same shot, which is
# the difference between a half chance and a good one. Naming the file here
# means the pipeline and the sandbox cannot quietly end up on different models.
MODEL_PATH = Path(__file__).resolve().parents[1] / 'xg-sandbox' / 'xg_model6.onnx'


def load_session(model_path: str | Path | None = None):
    """An onnxruntime session for the xG model.

    Kept here rather than left to each caller so there is one answer to "which
    model" in the Python half of the project.
    """
    import onnxruntime as ort

    return ort.InferenceSession(
        str(model_path or MODEL_PATH), providers=['CPUExecutionProvider']
    )


@dataclass
class ShotContext:
    """Everything known about one shot at the moment it was struck."""

    shooter_m: tuple[float, float]
    attacking_end: str = 'right'
    keeper_m: tuple[float, float] | None = None
    defenders_m: list[tuple[float, float]] = field(default_factory=list)

    is_header: bool = False
    under_pressure: bool = False
    is_open_play: bool = True
    shot_height: float | None = None


def shot_angle(location: np.ndarray) -> float:
    """Angle subtended by the goal mouth, in radians. Mirrors main.py."""
    a = POST_L - location
    b = POST_R - location
    cos = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)
    return float(np.arccos(np.clip(cos, -1, 1)))


def in_shot_cone(point: np.ndarray, ball: np.ndarray) -> bool:
    """Barycentric test — is `point` inside ball -> post_L -> post_R?

    Ported from main.py so the pipeline counts defenders the same way the
    training data did. Counting them differently is exactly the kind of
    mismatch that produces confident wrong numbers.
    """
    v0, v1, v2 = POST_R - ball, POST_L - ball, point - ball
    d00, d01, d02 = np.dot(v0, v0), np.dot(v0, v1), np.dot(v0, v2)
    d11, d12 = np.dot(v1, v1), np.dot(v1, v2)
    inv = 1.0 / (d00 * d11 - d01 * d01 + 1e-9)
    u = (d11 * d02 - d01 * d12) * inv
    v = (d00 * d12 - d01 * d02) * inv
    return bool(u >= 0 and v >= 0 and (u + v) < 1)


def build_features(context: ShotContext, pitch: Pitch) -> dict[str, float]:
    """Metres -> the 12 features, expressed in StatsBomb space."""
    ball = np.array(pitch.to_statsbomb(*context.shooter_m, context.attacking_end))

    distance = float(np.linalg.norm(ball - GOAL))
    angle = shot_angle(ball)

    if context.keeper_m is not None:
        keeper = np.array(pitch.to_statsbomb(*context.keeper_m, context.attacking_end))
        keeper_distance = float(np.linalg.norm(keeper - GOAL))
        keeper_angle = shot_angle(keeper)
        keeper_off_line = 1.0 if keeper_distance > 3.0 else 0.0
    else:
        # Same fallback main.py uses when a freeze frame has no keeper.
        keeper_distance = 5.0
        keeper_angle = shot_angle(np.array([115.0, 40.0]))
        keeper_off_line = 0.0

    defenders_in_cone = 0
    defender_pressure = 0.0
    for defender in context.defenders_m:
        point = np.array(pitch.to_statsbomb(*defender, context.attacking_end))
        if in_shot_cone(point, ball):
            defenders_in_cone += 1
            defender_pressure += 1.0 / (float(np.linalg.norm(point - ball)) + 1e-9)

    return {
        'distance_to_goal': distance,
        'angle_to_goal': angle,
        'is_foot': 0.0 if context.is_header else 1.0,
        'is_header': 1.0 if context.is_header else 0.0,
        'under_pressure': 1.0 if context.under_pressure else 0.0,
        'is_open_play': 1.0 if context.is_open_play else 0.0,
        'shot_height': (
            context.shot_height if context.shot_height is not None
            else DEFAULT_SHOT_HEIGHT
        ),
        'keeper_distance_to_goal': keeper_distance,
        'keeper_angle_coverage': keeper_angle,
        'keeper_off_line': keeper_off_line,
        'defenders_in_cone': float(defenders_in_cone),
        'defender_pressure': defender_pressure,
    }


def feature_vector(context: ShotContext, pitch: Pitch) -> np.ndarray:
    """The features as a (1, 12) float32 array, in the model's own order."""
    features = build_features(context, pitch)
    return np.array(
        [[features[name] for name in FEATURE_ORDER]], dtype=np.float32
    )


def shot_context_from_tracking(
    ball_px: tuple[float, float],
    player_boxes,
    shooter_track: int,
    team_of,
    calibration: Calibration,
    orientation: MatchOrientation,
    side: str,
    period: str,
    **kwargs,
) -> ShotContext:
    """Assemble a ShotContext from one frame of tracking.

    UNVERIFIED. In particular the goalkeeper is guessed as the defending
    player nearest their own goal, which is usually right and occasionally
    catastrophically wrong — a covering centre-back on the line will be taken
    for the keeper and the keeper features will describe the wrong person.
    """
    pitch = calibration.pitch
    attacking_end = orientation.attacking_end(side, period)
    defending_end = orientation.defending_end(side, period)

    shooter_m = None
    opponents: list[tuple[float, float]] = []

    for track_id, xyxy in player_boxes:
        position = calibration.ground_point_to_pitch(xyxy)
        if track_id == shooter_track:
            shooter_m = position
        elif team_of(track_id) != team_of(shooter_track):
            opponents.append(position)

    if shooter_m is None:
        shooter_m = calibration.to_pitch(*ball_px)

    keeper_m = None
    if opponents:
        goal = pitch.goal_centre(defending_end)
        keeper_m = min(opponents, key=lambda p: math.dist(p, goal))
        opponents = [p for p in opponents if p is not keeper_m]

    return ShotContext(
        shooter_m=shooter_m,
        attacking_end=attacking_end,
        keeper_m=keeper_m,
        defenders_m=opponents,
        **kwargs,
    )


def validate_against_noise(
    session,
    pitch: Pitch,
    noise_m: float = 0.5,
    trials: int = 200,
    seed: int = 0,
) -> dict[str, float]:
    """Measure how much the model's output moves under CV-scale position noise.

    The roadmap flags this as an open question: the model was trained on clean
    StatsBomb positions, and nobody has checked whether it stays calibrated when
    fed noisier ones. This perturbs a spread of shots and reports how far the
    predictions shift.

    A large mean shift does not mean the model is wrong — it means xG from CV
    positions carries meaningfully more uncertainty than xG from hand-tagged
    ones, and should be presented with that in mind.

    `session` is an onnxruntime InferenceSession for xg_model6.onnx.
    """
    rng = np.random.default_rng(seed)

    spots = [
        (95.0, 34.0),   # central, close
        (88.0, 20.0),   # wide right
        (88.0, 48.0),   # wide left
        (75.0, 34.0),   # edge of the box
        (100.0, 30.0),  # inside, tight angle
    ]

    shifts: list[float] = []
    baselines: list[float] = []

    for spot in spots:
        clean = ShotContext(
            shooter_m=spot,
            keeper_m=(pitch.length_m - 4.0, pitch.width_m / 2),
            defenders_m=[(spot[0] + 4, spot[1] + 2)],
        )
        base = _predict(session, feature_vector(clean, pitch))
        baselines.append(base)

        for _ in range(trials // len(spots)):
            jittered = ShotContext(
                shooter_m=(
                    spot[0] + rng.normal(0, noise_m),
                    spot[1] + rng.normal(0, noise_m),
                ),
                keeper_m=(
                    pitch.length_m - 4.0 + rng.normal(0, noise_m),
                    pitch.width_m / 2 + rng.normal(0, noise_m),
                ),
                defenders_m=[(
                    spot[0] + 4 + rng.normal(0, noise_m),
                    spot[1] + 2 + rng.normal(0, noise_m),
                )],
            )
            shifts.append(abs(_predict(session, feature_vector(jittered, pitch)) - base))

    return {
        'noise_m': noise_m,
        'mean_shift': float(np.mean(shifts)),
        'p95_shift': float(np.percentile(shifts, 95)),
        'max_shift': float(np.max(shifts)),
        'mean_baseline_xg': float(np.mean(baselines)),
    }


def _predict(session, features: np.ndarray) -> float:
    """Run one feature vector through the ONNX model and pull out P(goal)."""
    outputs = session.run(None, {'float_input': features})
    for output in outputs:
        array = np.asarray(output)
        # The probability output is the one with two columns: [no goal, goal].
        if array.ndim == 2 and array.shape[1] >= 2:
            return float(array[0][1])
        if array.dtype.kind == 'f' and array.size >= 2:
            return float(array.flatten()[1])
    raise ValueError('could not find a probability in the model output')
