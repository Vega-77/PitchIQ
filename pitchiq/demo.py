"""A nine-second scripted match, for trying PitchIQ without footage or a GPU.

`python -m pitchiq demo` draws a clip of coloured boxes (ten players and a ball:
a long pass, a touch, a strike at goal), fits a fixed-camera calibration from
seven landmarks, and runs `analyse_match` on it with `ScriptedDetector` and
`SlotTracker` standing in for YOLO and BoT-SORT. It needs only `pip install -e
.[test]`. tests/test_pipeline_end_to_end.py runs the same scene and pins what
each stage makes of it.

The scene is football by script, not by recognition: the numbers show the
chain running, not how well it sees a real match.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from .calibration import Calibration, Correspondence
from .detector import CLASS_BALL, CLASS_PERSON
from .pipeline import AnalysisOptions, MatchContext, MatchReport, analyse_match
from .pitch import MatchOrientation, Pitch
from .teams import TEAM_A, TEAM_B

# 14 pixels to the metre puts the whole pitch in a 1470x952 frame and makes a
# 1.8m player 25 pixels tall — small, but above the 6-pixel floor `torso_patch`
# needs before it will read a shirt colour at all.
SCALE = 14.0
PITCH = Pitch()
WIDTH = int(PITCH.length_m * SCALE)
HEIGHT = int(PITCH.width_m * SCALE)
FPS = 30.0
FRAMES = 270  # nine seconds

RED = (40, 40, 220)  # BGR, as OpenCV wants it
BLUE = (220, 60, 40)
WHITE = (245, 245, 245)

# Home positions, all in the left half so the shot is a shot rather than a
# hopeful punt. The red shirts are emitted first every frame, which is what
# decides their label — see `test_the_red_shirts_are_team_b`.
RED_HOME = [(24.0, 14.0), (26.0, 26.0), (22.0, 46.0), (17.0, 34.0), (28.0, 56.0)]
BLUE_HOME = [(11.0, 12.0), (9.0, 23.0), (10.0, 47.0), (13.0, 57.0), (34.0, 30.0)]
SHOOTER = 3

# The only football in the clip: a long pass, a settling touch, a strike at the
# left goal. The beats are spaced wider than SHOT_LOOKAHEAD_S so the 2.5s window
# `_as_shot` searches for the ball never reaches from one beat to the next — the
# first version of this fixture had a three-second clip, and the pass was
# classified as a shot because the lookahead ran off the end and found the ball
# sitting in the goal.
PASS_S = 3.0
RECEIVE_S = 4.4
NUDGE_S = 4.9
STRIKE_S = 7.0
FLIGHT_S = 0.8
NUDGE_LEAD_M = 1.0
GOAL_M = (-2.0, PITCH.width_m / 2)  # past the line, not on it

ORIENTATION = MatchOrientation(home_attacks_first_half='right')
SIDES = {TEAM_A: 'us', TEAM_B: 'them'}


def px(x_m, y_m):
    """Pitch metres to image pixels. Y is flipped; the image grows downward."""
    return (x_m * SCALE, (PITCH.width_m - y_m) * SCALE)


def wander(home, i, t, sign):
    """A slow orbit, at a different phase and heading for every player.

    Not decoration. `pitchiq/camera.py` calls the camera moved when the *median*
    per-track displacement passes 0.75 player heights with 70% of tracks
    agreeing on the direction, so two teams drifting steadily produce a
    confident false positive — which is exactly what the first version of this
    fixture did. Ten players orbiting out of phase keep the median near zero,
    the way twenty-two people moving independently keep it there.
    """
    ang = 2.0 * math.pi * (0.25 * t + i / 5.0)
    return (home[0] + sign * 1.6 * math.cos(ang), home[1] + 1.6 * math.sin(ang))


def player_positions(t):
    out = [(wander(home, i, t, 1.0), RED) for i, home in enumerate(RED_HOME)]
    out += [(wander(home, i, t, -1.0), BLUE) for i, home in enumerate(BLUE_HOME)]
    return out


def ball_m(t):
    """Where the ball is, in metres, at video second `t`."""
    a0 = wander(RED_HOME[0], 0, t, 1.0)
    a3 = wander(RED_HOME[SHOOTER], SHOOTER, t, 1.0)
    if t < PASS_S:
        return a0
    if t < RECEIVE_S:
        f = (t - PASS_S) / (RECEIVE_S - PASS_S)
        return (a0[0] + (a3[0] - a0[0]) * f, a0[1] + (a3[1] - a0[1]) * f)
    if t < STRIKE_S:
        # Nudged ahead, then run onto again. The gap has to close monotonically
        # into the strike. `segment_touches` places a touch at the smallest
        # ball-to-player distance within a window, so a ball glued to a player's
        # feet ties every frame in that window and the touch lands up to four
        # frames early — where the one-sided velocity window averages the strike
        # together with the still frames before it and reads about a third of
        # the real speed. That is below SHOT_SPEED_PH_S, and the strike was
        # silently not a shot.
        if t < NUDGE_S:
            gap = NUDGE_LEAD_M * (t - RECEIVE_S) / (NUDGE_S - RECEIVE_S)
        else:
            gap = NUDGE_LEAD_M * (STRIKE_S - t) / (STRIKE_S - NUDGE_S)
        return (a3[0] - gap, a3[1])
    f = min(1.0, (t - STRIKE_S) / FLIGHT_S)
    return (a3[0] + (GOAL_M[0] - a3[0]) * f, a3[1] + (GOAL_M[1] - a3[1]) * f)


def scene(n):
    """Every box in frame `n`: (cls, conf, x1, y1, x2, y2, colour), in pixels."""
    t = n / FPS
    rows = []
    for (x_m, y_m), colour in player_positions(t):
        cx, cy = px(x_m, y_m)
        half_w, height = 0.35 * SCALE, 1.8 * SCALE
        rows.append((CLASS_PERSON, 0.9, cx - half_w, cy - height, cx + half_w, cy, colour))
    cx, cy = px(*ball_m(t))
    r = 0.25 * SCALE
    rows.append((CLASS_BALL, 0.5, cx - r, cy - r, cx + r, cy + r, WHITE))
    return rows


class FakeBoxes:
    """The three attributes `TrackedFramePass` reads off an ultralytics result."""

    def __init__(self, rows):
        self._rows = np.array(rows, dtype=np.float32).reshape(-1, 6)

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, mask):
        return FakeBoxes(self._rows[mask])

    @property
    def cls(self):
        return self._rows[:, 0]

    @property
    def conf(self):
        return self._rows[:, 1]

    @property
    def xyxy(self):
        return self._rows[:, 2:6]


class ScriptedDetector:
    """Returns the scripted scene for each frame, counting frames as it goes.

    Counting rather than matching on pixels is safe because `_stream_batches`
    delivers every frame in the window exactly once and in order — the property
    `tests/test_pipeline_stream.py` exists to pin.
    """

    def __init__(self):
        self.frames_seen = 0

    def detect_batch_raw(self, images):
        out = []
        for _ in images:
            out.append(FakeBoxes([row[:6] for row in scene(self.frames_seen)]))
            self.frames_seen += 1
        return out


class SlotTracker:
    """One stable id per detection slot, in the STrack.result column order.

    The scene emits its players in the same order every frame, so slot *is*
    identity here. That sidesteps ByteTrack entirely, which is the point: what
    is under test is what the pipeline does with tracks, not the tracker.
    """

    def update(self, boxes, img=None, **kwargs):
        rows = [
            [*xyxy, 100 + i, conf, CLASS_PERSON, i]
            for i, (conf, xyxy) in enumerate(zip(boxes.conf, boxes.xyxy))
        ]
        return np.array(rows, dtype=np.float32).reshape(-1, 8)


def write_clip(path: str | Path) -> bool:
    """Draw the scene into an mp4 at `path`; False when OpenCV has no mp4v encoder."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), FPS, (WIDTH, HEIGHT))
    if not writer.isOpened():
        return False
    for n in range(FRAMES):
        frame = np.full((HEIGHT, WIDTH, 3), 60, dtype=np.uint8)
        for _cls, _conf, x1, y1, x2, y2, colour in scene(n):
            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), colour, -1)
        writer.write(frame)
    writer.release()
    return True


def write_calibration(path: str | Path) -> Path:
    """A homography fitted to seven landmarks, not hand-built, saved at `path`.

    `Calibration.error()` returns NaN with no correspondences behind it, which
    would poison `calibration_error_m` all the way into the JSON, so this goes
    through `fit` the way a real calibration file does.
    """
    names = [
        'corner_bottom_left',
        'corner_top_left',
        'corner_bottom_right',
        'corner_top_right',
        'centre_spot',
        'halfway_bottom',
        'halfway_top',
    ]
    points = [Correspondence(n, px(*PITCH.landmark(n))) for n in names]
    fitted = Calibration.fit(points, PITCH, image_size=(WIDTH, HEIGHT))
    fitted.save(path)
    return Path(path)


def run(clip: str | Path, calibration_path: str | Path | None) -> MatchReport:
    """`analyse_match` on the clip, with the scripted detector and tracker."""
    return analyse_match(
        clip,
        calibration_path=calibration_path,
        options=AnalysisOptions(device='cpu'),
        context=MatchContext(orientation=ORIENTATION, period='first_half', side_of_team=SIDES),
        detector=ScriptedDetector(),
        tracker_factory=lambda name, device: SlotTracker(),
    )
