"""Turn a detected shot into the 11 features the existing xG model expects.

`build_features` and `feature_vector` build the inputs from a `ShotContext`
(shooter, keeper and defenders in pitch metres), `predict_xg` runs
`xg_model8.onnx`, and `xg_for_shots` connects it to the pipeline's shots.
This is the only place xG is computed; the site shows the stored figures. The
feature order, model file and goal geometry come from `pitchiq/xg_model.py`,
which training imports too.

Limits: the model half is verified against the real model; spotting a shot in
footage and choosing the right players for `ShotContext` is not. The model was
trained on clean StatsBomb positions, and half a metre of position error moves
one shot's xG by about 0.03 (0.095 at p95), so per-shot figures need a good
calibration and totals are far steadier. `Pitch.to_statsbomb` normalises by the
configured pitch length, so measure the real field.

Design notes, measurements and history: `docs/design/xg_bridge.md`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .calibration import Calibration
from .pitch import MatchOrientation, Pitch
from .xg_model import (
    FEATURES,
    MODEL_PATH,
    distance_sb,
    in_shot_cone_sb,
    shot_angle,
)

if TYPE_CHECKING:
    import onnxruntime as ort

# No stand-in for the z axis any more, because there is no z-axis feature.
# DEFAULT_SHOT_HEIGHT used to be substituted on every shot, until
# `shot_height` turned out to be where the ball *finished* rather than anything
# about the shot — so feeding a constant was feeding the model a claim about the
# outcome, and a wrong one. See docs/design/xg_bridge.md.


def load_session(model_path: str | Path | None = None) -> ort.InferenceSession:
    """An onnxruntime session for the xG model.

    Kept here rather than left to each caller so there is one answer to "which
    model" in the Python half of the project.
    """
    import onnxruntime as ort

    return ort.InferenceSession(str(model_path or MODEL_PATH), providers=['CPUExecutionProvider'])


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


def build_features(context: ShotContext, pitch: Pitch) -> dict[str, float]:
    """Metres -> the 11 features, expressed in StatsBomb space."""
    ball = np.array(pitch.to_statsbomb(*context.shooter_m, context.attacking_end))

    distance = distance_sb(ball)
    angle = shot_angle(ball)

    if context.keeper_m is not None:
        keeper = np.array(pitch.to_statsbomb(*context.keeper_m, context.attacking_end))
        keeper_distance = distance_sb(keeper)
        keeper_angle = shot_angle(keeper)
        keeper_off_line = 1.0 if keeper_distance > 3.0 else 0.0
    else:
        # Same fallback training uses when a freeze frame has no keeper.
        keeper_distance = 5.0
        keeper_angle = shot_angle(np.array([115.0, 40.0]))
        keeper_off_line = 0.0

    defenders_in_cone = 0
    defender_pressure = 0.0
    for defender in context.defenders_m:
        point = np.array(pitch.to_statsbomb(*defender, context.attacking_end))
        if in_shot_cone_sb(point, ball):
            defenders_in_cone += 1
            defender_pressure += 1.0 / (distance_sb(point, ball) + 1e-9)

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
    return np.array([[features[name] for name in FEATURES]], dtype=np.float32)


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

    Pass `keeper_track` whenever it is known — from `pitchiq/keeper.py` or from a
    human. Without it the keeper is guessed as the defending player nearest
    their own goal, which is usually right and occasionally catastrophically
    wrong: a covering centre-back on the line gets taken for the keeper, and
    every keeper feature then describes the wrong person.

    "Their own goal" is the goal the shooter is *attacking*. An earlier version
    searched the shooter's own end, which inverted the guess before anything
    called it; see tests/test_xg_bridge.py::TestKeeperGuess.
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
) -> tuple[dict[str, tuple[float, float]], list[str]]:
    """{event_id: (foot xG, header xG)} per shot, plus warnings worth carrying.

    Both readings, because one fixed camera cannot see the ball's height and the
    difference is not small: measured against this model, scoring a header as a
    foot shot overstates it by 1.3x at a tight angle and 3.7x from the edge of
    the box. That is larger than the position noise the whole `xgTrust` ladder
    was built to manage, and it runs one way.

    So the body part is left to whoever watches the video, and both answers are
    precomputed here rather than shipping a model to the browser to recompute
    one. A binary question with a cheap model on both branches wants two numbers,
    not an inference runtime on the coach's phone.

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
        # Broad on purpose. Besides ImportError, onnxruntime raises its own
        # NoSuchFile, InvalidProtobuf and Fail, which derive straight from
        # Exception, and any of them should cost the report its xG, not the run.
        except Exception as error:  # noqa: BLE001
            return {}, [f'expected goals unavailable: {error}']

    pitch = calibration.pitch
    xg_by_event: dict[str, tuple[float, float]] = {}
    skipped = 0

    for shot in shots:
        attacking_end = attacking_end_for(
            orientation,
            side_of_team,
            period,
            shot.team,
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
            # Set per branch below. The context is otherwise identical: body
            # part is the only feature that changes, which is what makes
            # computing both cheap enough to do unconditionally.
            is_header=False,
        )

        try:
            foot = _predict(session, feature_vector(context, pitch))
            header = _predict(
                session,
                feature_vector(replace(context, is_header=True), pitch),
            )
            xg_by_event[shot.event_id] = (foot, header)
        # Broad for the same reason as `load_session` above: a model that fails
        # mid-match (onnxruntime's Fail, InvalidArgument) keeps what it scored.
        except Exception as error:  # noqa: BLE001
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
        (95.0, 34.0),  # central, close
        (88.0, 20.0),  # wide right
        (88.0, 48.0),  # wide left
        (75.0, 34.0),  # edge of the box
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
                defenders_m=[
                    (
                        spot[0] + 4 + rng.normal(0, noise_m),
                        spot[1] + 2 + rng.normal(0, noise_m),
                    )
                ],
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
# benchmark note in the roadmap archive (docs/history/) that produced figures
# wrong by 10x.
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
