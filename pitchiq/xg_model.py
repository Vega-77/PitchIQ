"""The xG model's contract: its file, its version, its feature order and its geometry.

Training (`xg_training/`) and inference (`pitchiq/xg_bridge.py`) both import
from here, so the model that is trained and the model that is run agree on
which file it is, which order the features come in, and how distance, angle
and the defender cone are measured. Everything is in StatsBomb space: x along
the pitch 0..120 with the attacked goal at x=120, y across it 0..80.

A retrain that changes the model bumps `MODEL_VERSION`, which renames the file
both sides use. The model takes a bare float array with no names attached, so
`FEATURES` is the only record of what each column means.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .pitch import STATSBOMB_LENGTH, STATSBOMB_WIDTH

# The model behind every xG figure. Its two predecessors (xg_model6,
# uncalibrated; xg_model7, fed a constant shot height) and why they were
# retired are in docs/design/xg_bridge.md.
MODEL_VERSION = 'xg_model8'

# The one copy, beside the code that loads it. xg_training/ writes it here.
MODEL_PATH = Path(__file__).resolve().parent / f'{MODEL_VERSION}.onnx'

# The model's input columns, in order. Changing this changes the model: retrain
# and bump MODEL_VERSION.
FEATURES = [
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

# The attacked goal in StatsBomb space: its centre and its two posts.
GOAL_CENTRE_SB = np.array([STATSBOMB_LENGTH, STATSBOMB_WIDTH / 2])
POST_L = np.array([STATSBOMB_LENGTH, 36.0])
POST_R = np.array([STATSBOMB_LENGTH, 44.0])


def distance_sb(a, b=GOAL_CENTRE_SB) -> float:
    """Straight-line distance from `a` to `b` (the goal centre by default), StatsBomb units."""
    return float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float)))


def shot_angle(location) -> float:
    """Angle the goal mouth subtends from `location`, in radians."""
    location = np.asarray(location, float)
    a = POST_L - location
    b = POST_R - location
    cos = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)
    return float(np.arccos(np.clip(cos, -1, 1)))


def in_shot_cone_sb(point, ball) -> bool:
    """True if `point` is inside the triangle ball -> left post -> right post.

    A barycentric test. Training counted defenders with it, so the pipeline must
    count them with the same one.
    """
    point, ball = np.asarray(point, float), np.asarray(ball, float)
    v0, v1, v2 = POST_R - ball, POST_L - ball, point - ball
    d00, d01, d02 = np.dot(v0, v0), np.dot(v0, v1), np.dot(v0, v2)
    d11, d12 = np.dot(v1, v1), np.dot(v1, v2)
    inv = 1.0 / (d00 * d11 - d01 * d01 + 1e-9)
    u = (d11 * d02 - d01 * d12) * inv
    v = (d00 * d12 - d01 * d02) * inv
    return bool(u >= 0 and v >= 0 and (u + v) < 1)
