"""Turn a detected shot into the 11 features the existing xG model expects.

    STATUS — the model half is verified; the shot-detection half is not.

`build_features`, `feature_vector` and `_predict` now run against the real model
and agree with the browser to three decimal places on the same shot; see
tests/test_xg_parity.py. `xg_for_shots` connects that to the pipeline, which
until 2026-08-02 nothing did — `attach_xg` had no caller, so every xG figure
from the report JSON to a player's phone was null. What remains unverified is
upstream of here: whether a shot can be spotted in footage at all, and whether
the shooter, keeper and defenders handed to `ShotContext` are the right ones.

The model itself was replaced on 2026-08-06. Everything before that date ran
xg_model6, which was exported out of the middle of a CalibratedClassifierCV
without the isotonic step, and read about six times high near goal. It was
replaced twice the same day — xg_model7 restored the calibration, xg_model8
dropped `shot_height` (see below). Over the 53,337 training shots xg_model8
predicts 6,057 goals against the 6,014 actually scored, AUC 0.884, and tracks
the real conversion rate closely in every distance band. Any figure, note or
screenshot from before 2026-08-06 is on a different scale.

This is where the computer vision work meets the model already trained in
`PitchIQHelper/main.py` and already running in `xg-sandbox/xg-model.js`. The feature
order below must match `FEATURES` in main.py exactly; a mismatch does not raise,
it silently produces a plausible-looking wrong number.

Two things are known to be unresolved rather than merely untested:

* **shot_height was never a feature, and is gone.** *(Resolved 2026-08-06 —
  kept here because it is the most instructive thing this module has hit.)*

  It is a z-axis value, and a homography maps a plane, so there was no way to
  get it from one fixed camera and a constant was substituted on every shot.
  That much was known and documented. What was not: in the training script it is
  `end_location[2]` — **where the ball finished** — which is known only after the
  shot. An outcome dressed as a feature. 8,733 of the training shots end above
  3 m and convert at exactly 0.000, because a ball that ended four metres up
  went over the bar and the model could see that.

  Measured over all 53,337 training shots:

        model / input                     AUC     Brier   predicted   actual
        12 features, real end height     0.935   0.061      6,061      6,014
        12 features, height fixed at 0.6 0.857   0.080      9,213      6,014
        11 features, no height at all    0.884   0.073      6,057      6,014

  The middle row is what this module produced for as long as the feature
  existed: a total half again too high, in one direction, at every band. The
  first row is the one every printed metric described and no camera could ever
  reach. Dropping the feature beats feeding it a constant on **every** axis at
  once — better AUC, better Brier, and a total that lands on the goals actually
  scored.

  The general lesson, which is worth more than the fix: a feature the production
  path cannot supply is not a bonus that degrades gracefully. It is a hole the
  model has learned to lean on, and filling it with a constant puts the model
  somewhere it has never been.

* **The model has never seen CV-derived inputs**, and it is sensitive to that.
  It was trained on clean, human-verified StatsBomb data; positions from
  detection and homography are not. Re-measured 2026-08-06 by
  `validate_against_noise` against xg_model8, 400 trials over five spots
  averaging 0.188 xG:

        noise    mean shift   p95     max     share of the baseline
        0.25 m     0.019     0.065   0.099     10%  /  34%
        0.50 m     0.030     0.095   0.253     16%  /  50%
        1.00 m     0.043     0.168   0.267     23%  /  89%
        2.00 m     0.062     0.242   0.538     33%  / 129%
        4.00 m     0.107     0.380   0.624     57%  / 202%

  So half a metre of position error — the band `cv/calibrate.py` accepts as a
  good calibration — moves a single shot's xG by about 0.030 typically and 0.095
  at the 95th percentile, half the number. That is fine for "a decent chance"
  and not fine for ranking two shots 0.1 apart. At 2 m the p95 shift exceeds the
  xG itself, which is the point past which per-shot numbers are meaningless.

  **Removing shot_height made the model more position-sensitive, not less**, and
  in hindsight obviously: with eleven features instead of twelve, distance and
  angle carry more of the answer, so moving a player moves the answer further.
  The p95 at half a metre went from a third of the number to half of it, and the
  per-shot display band in assets/report.js tightened from 1 m to 0.5 m to
  match. A more honest model is a fussier one about where its inputs came from.

  Summing helps, and by a measured amount rather than a hoped-for one: simulated
  over a half's six shots, the *total* lands within 8% of the truth at 0.5 m,
  12% at 1 m, 18% at 2 m and 26% at 4 m, while a single shot at 1 m already
  carries an error bar nearly as wide as itself. That gap between the two is the
  whole reason `xgTrust` has a band where the total shows and no individual
  figure does. Pinned by tests/test_xg_noise.py.

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

# No stand-in for the z axis any more, because there is no z-axis feature.
# DEFAULT_SHOT_HEIGHT was substituted on every shot until 2026-08-06, when
# `shot_height` turned out to be where the ball *finished* rather than anything
# about the shot — so feeding a constant was feeding the model a claim about the
# outcome, and a wrong one. See the module docstring.

# The model the browser loads, and therefore the model behind every xG number
# anyone has actually looked at. Naming one file here means the pipeline and
# the sandbox cannot quietly end up on different models.
#
# Two predecessors were retired on 2026-08-06.
#
# xg_model6 was the raw XGBoost classifier out of the middle of a
# CalibratedClassifierCV, exported without the isotonic step that made its
# probabilities mean anything, and with scale_pos_weight ~= 9 still pushing them
# up — so it read about six times high near goal. Every xG this project showed
# before that date came from it.
#
# xg_model7 fixed the calibration and still took `shot_height`, which nothing
# here can measure, so a constant went in and the totals came out about half
# again too high. xg_model8 drops the feature; see the module docstring.
MODEL_PATH = Path(__file__).resolve().parents[1] / 'xg-sandbox' / 'xg_model8.onnx'


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
    """Metres -> the 11 features, expressed in StatsBomb space."""
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
    keeper_track: int | None = None,
    **kwargs,
) -> ShotContext:
    """Assemble a ShotContext from one frame of tracking.

    Pass `keeper_track` whenever it is known — from `cv/keeper.py` or from a
    human. Without it the keeper is guessed as the defending player nearest
    their own goal, which is usually right and occasionally catastrophically
    wrong: a covering centre-back on the line gets taken for the keeper, and
    every keeper feature then describes the wrong person.

    "Their own goal" is the goal the shooter is *attacking*. This searched the
    shooter's own end instead until 2026-08-02, which inverted the guess
    completely: the keeper standing on his line was counted as a defender
    blocking the shot, and whichever opponent had dropped deepest — often fifty
    metres the wrong side of the ball — was handed to the model as the keeper.
    The model was therefore told the goal was unguarded on every shot where
    nobody named the keeper, which inflates xG. Nothing called this function at
    the time, so no published number was ever affected; see
    tests/test_xg_bridge.py::TestKeeperGuess.
    """
    pitch = calibration.pitch
    attacking_end = orientation.attacking_end(side, period)

    shooter_m = None
    keeper_m = None
    opponents: list[tuple[float, float]] = []

    for track_id, xyxy in player_boxes:
        position = calibration.ground_point_to_pitch(xyxy)
        if track_id == shooter_track:
            shooter_m = position
        elif team_of(track_id) != team_of(shooter_track):
            if keeper_track is not None and track_id == keeper_track:
                keeper_m = position
            else:
                opponents.append(position)

    if shooter_m is None:
        shooter_m = calibration.to_pitch(*ball_px)

    if keeper_m is None and keeper_track is None and opponents:
        goal = pitch.goal_centre(attacking_end)
        keeper_m = min(opponents, key=lambda p: math.dist(p, goal))
        opponents = [p for p in opponents if p is not keeper_m]

    return ShotContext(
        shooter_m=shooter_m,
        attacking_end=attacking_end,
        keeper_m=keeper_m,
        defenders_m=opponents,
        **kwargs,
    )


def xg_for_shots(
    log,
    table,
    calibration: Calibration,
    orientation: MatchOrientation,
    period: str,
    side_of_team: dict[str, str] | None = None,
    keepers=None,
    session=None,
) -> tuple[dict[str, float], list[str]]:
    """{event_id: xG} for every shot in the log, plus warnings worth carrying.

    This is the seam the whole module existed for and did not have. `attach_xg`
    was written, `predict_xg` was written, the browser and the pipeline were
    checked against each other to six decimal places — and nothing ever called
    any of it, so every `xg` field from here to the player's phone has been null
    since the day it was added.

    Everything the model needs is already on the event or in the frame it came
    from. `start_m` is where the ball was struck; `under_pressure` was counted by
    the touch layer; `in_play` says whether a tagger saw a stoppage. The only
    thing fetched fresh is the other twenty-one people, which needs the frame.

    Two failures are handled rather than raised, because losing one column beats
    losing the match report:

    * onnxruntime missing or the model unreadable. Somebody running possession
      numbers on a laptop should not need a 200 MB inference runtime installed.
    * a shot with no `start_m`, or a team whose attacking end is unknown. Both
      mean the same thing — nothing here can say which goal it was aimed at, and
      guessing would produce a number for a shot at the wrong end.
    """
    from .events import Shot, attacking_end_for

    shots = [e for e in log.events if isinstance(e, Shot)]
    if not shots:
        return {}, []

    if session is None:
        try:
            session = load_session()
        except Exception as error:                       # noqa: BLE001
            return {}, [f'expected goals unavailable: {error}']

    pitch = calibration.pitch
    xg_by_event: dict[str, float] = {}
    skipped = 0

    for shot in shots:
        attacking_end = attacking_end_for(
            orientation, side_of_team, period, shot.team,
        )
        if attacking_end is None or shot.start_m is None:
            skipped += 1
            continue

        frame = table.at(shot.frame_index)
        boxes = frame.boxes() if frame is not None else []

        # Whoever is between this shot and the goal is the *other* team's
        # keeper. Passing it explicitly matters: `shot_context_from_tracking`
        # otherwise picks the defender nearest their own goal, which its own
        # docstring calls occasionally catastrophic — a covering centre-back on
        # the line gets taken for the keeper and every keeper feature then
        # describes the wrong person.
        keeper_track = None
        if keepers is not None:
            for team, tracks in (keepers.by_team or {}).items():
                if team != shot.team and tracks:
                    keeper_track = next(iter(tracks))
                    break

        context = shot_context_from_tracking(
            ball_px=shot.start_xy_px,
            player_boxes=boxes,
            shooter_track=shot.track_id,
            team_of=table.team_of,
            calibration=calibration,
            orientation=orientation,
            side=(side_of_team or {}).get(shot.team, 'us'),
            period=period,
            keeper_track=keeper_track,
            under_pressure=shot.under_pressure,
            # A shot inside a tagged stoppage is a set piece of some kind. The
            # log knows which — corner, free kick, penalty — but that does not
            # travel on the event, so this says only "not open play", which is
            # the single bit the model actually takes. With no tagged log
            # `in_play` is True throughout and this is the old assumption,
            # unchanged and now visible.
            is_open_play=shot.in_play,
            # Always False, and not a default we could improve on: one fixed
            # camera cannot see the ball's height. Headed chances are therefore
            # scored as foot shots, which biases their xG upward.
            is_header=False,
        )

        try:
            xg_by_event[shot.event_id] = _predict(
                session, feature_vector(context, pitch)
            )
        except Exception as error:                       # noqa: BLE001
            return xg_by_event, [f'expected goals stopped early: {error}']

    warnings = []
    if skipped:
        warnings.append(
            f'{skipped} of {len(shots)} shots got no expected-goals figure, '
            'because nothing said which goal that team was attacking'
        )
    return xg_by_event, warnings


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

    `session` is an onnxruntime InferenceSession for the model named by
    MODEL_PATH — `load_session()` with no argument gives the right one.
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


# One session per process. Loading a model per call is slow, and the project
# has already paid once for putting several models in one process — see the
# benchmark note in ROADMAP.md that produced figures wrong by 10x.
_SESSION = None


def predict_xg(context: ShotContext, pitch: Pitch, session=None) -> float:
    """One call from a ShotContext to P(goal).

    Everything a caller needed before was two steps with a session to manage in
    between, which is why nothing in the pipeline called this module at all.

    What the number does not know, all of it biasing upward or sideways rather
    than randomly: `is_header` is always False because one camera cannot see the
    ball's height, so headed chances are scored as foot shots; `shot_height`
    falls back to the training median and carries nothing from the actual shot;
    `is_open_play` defaults to True unless a tagger said otherwise.
    """
    global _SESSION
    if session is None:
        if _SESSION is None:
            _SESSION = load_session()
        session = _SESSION
    return _predict(session, feature_vector(context, pitch))


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
