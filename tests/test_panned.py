"""cv/panned.py: a camera that follows the play, held still for the pipeline.

The first half of these are about the transform on its own: a box seen in a
panned frame must land where the same box would have appeared in the reference
view, its feet must still measure to the right metres, and a frame nobody
registered must be dropped rather than measured through somebody else's
homography.

The second half runs `analyse_match` end to end on the rectangle clip from
`tests/test_pipeline_end_to_end.py`, with the camera's pan undone by a
`PannedCamera` instead of a still calibration, and checks that the metre-based
stages run and agree with the still-camera run.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_panned.py -q
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from cv.calibration import CalibrationError
from cv.frames import FrameRecord, FrameTable
from cv.panned import MAP_FIT_ERROR, PannedCamera
from cv.pipeline import analyse_match
from cv.pitch import Pitch, STATSBOMB_LENGTH, STATSBOMB_WIDTH

PITCH = Pitch(length_m=112.7, width_m=70.8)
# Reference view: 10 px per StatsBomb unit, y down, no perspective. Simple on
# purpose, so the expected numbers can be worked out by hand.
REF_TO_SB = np.array([[0.1, 0.0, 0.0], [0.0, 0.1, 0.0], [0.0, 0.0, 1.0]])


def shifted(dx):
    """Frame -> StatsBomb for a camera panned `dx` reference pixels to the right."""
    return REF_TO_SB @ np.array([[1.0, 0.0, dx], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])


def record(frame, rows):
    players = np.array(rows, dtype=np.float32).reshape(-1, 6)
    return FrameRecord(frame_index=frame, timestamp_s=frame / 30.0, players=players)


def camera(frames, dxs, ball=None):
    kwargs = {}
    if ball is not None:
        xs, ys, seen = zip(*ball)
        kwargs = dict(ball_x=np.array(xs), ball_y=np.array(ys), ball_observed=np.array(seen))
    return PannedCamera.from_arrays(
        np.array(frames), np.array([shifted(d) for d in dxs]), REF_TO_SB, PITCH, **kwargs,
    )


class TestStabilise:
    def test_a_panned_box_lands_where_the_reference_view_saw_it(self):
        # The camera moved 300 px right, so the same player appears 300 px
        # further left in this frame; holding it still puts him back.
        table = FrameTable(fps=30.0, frame_width=1920, frame_height=1080, records=[
            record(10, [[7, 100, 200, 120, 260, 0.9]]),
        ])
        camera([10], [300.0]).stabilise(table)
        assert table.records[0].players[0, 1:5].tolist() == pytest.approx([400, 200, 420, 260])

    def test_the_feet_measure_to_the_right_metres(self):
        cam = camera([10], [300.0])
        table = FrameTable(fps=30.0, frame_width=1920, frame_height=1080, records=[
            record(10, [[7, 100, 200, 120, 260, 0.9]]),
        ])
        cam.stabilise(table)
        foot = table.records[0].player_boxes()[0].ground_point
        x_m, y_m = cam.calibration.to_pitch(*foot)
        # Feet at reference (410, 260) = StatsBomb (41, 26).
        assert x_m == pytest.approx(41 * PITCH.length_m / STATSBOMB_LENGTH)
        assert y_m == pytest.approx(26 * PITCH.width_m / STATSBOMB_WIDTH)

    def test_an_unregistered_frame_is_emptied_and_counted(self):
        table = FrameTable(fps=30.0, frame_width=1920, frame_height=1080, records=[
            record(10, [[7, 100, 200, 120, 260, 0.9]]),
            record(11, [[7, 101, 200, 121, 260, 0.9]]),
            record(12, []),
        ])
        dropped = camera([10], [0.0]).stabilise(table)
        assert dropped == 1
        assert len(table.records[1]) == 0

    def test_a_missing_homography_is_not_a_registration(self):
        H = np.array([shifted(0.0), np.full((3, 3), np.nan)])
        cam = PannedCamera.from_arrays(np.array([10, 11]), H, REF_TO_SB, PITCH)
        assert sorted(cam.to_reference) == [10]


class TestBall:
    def test_the_tracker_ball_comes_back_in_the_reference_view(self):
        cam = camera([10, 11, 12], [0.0, 0.0, 0.0],
                     ball=[(60.0, 40.0, True), (61.0, 40.0, False), (np.nan, np.nan, False)])
        table = FrameTable(fps=30.0, frame_width=1920, frame_height=1080, records=[
            record(10, []), record(11, []), record(12, []),
        ])
        ball = cam.trajectory(table)
        assert [p.frame_index for p in ball.points] == [10, 11]
        assert ball.points[0].xy == pytest.approx((600.0, 400.0))
        assert [p.observed for p in ball.points] == [True, False]

    def test_a_ball_outside_the_window_is_not_reported(self):
        cam = camera([10], [0.0], ball=[(60.0, 40.0, True)])
        table = FrameTable(fps=30.0, frame_width=1920, frame_height=1080, records=[record(99, [])])
        assert len(cam.trajectory(table)) == 0


class TestCalibration:
    def test_it_reports_the_venue_map_fit_not_nan(self):
        cam = camera([10], [0.0])
        assert cam.calibration.error() == MAP_FIT_ERROR
        assert cam.calibration.error().is_usable

    def test_a_given_error_is_carried(self):
        bad = CalibrationError(mean_m=2.0, max_m=5.0)
        cam = PannedCamera.from_arrays(np.array([10]), np.array([shifted(0)]), REF_TO_SB, PITCH, error=bad)
        assert not cam.calibration.error().is_usable

    def test_it_cannot_be_given_with_a_calibration_file(self, tmp_path):
        with pytest.raises(ValueError, match='one or the other'):
            analyse_match(tmp_path / 'x.mp4', calibration_path='x.json', panned=camera([0], [0.0]))


# ---------------------------------------------------------------- end to end

import test_pipeline_end_to_end as e2e  # noqa: E402  (a `tests` package in the venv shadows ours)

# The e2e clip, filmed twice: once still, and once by a camera that pans up to
# PAN_PX to the right and back, so every rectangle is drawn that much further
# left. A camera holding the still clip's view as its reference must turn the
# second into the first.
PAN_PX = 40.0


def _pan(n):
    return PAN_PX * np.sin(2 * np.pi * n / e2e.FRAMES)


def _write(path, pan):
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*'mp4v'), e2e.FPS, (e2e.WIDTH, e2e.HEIGHT)
    )
    if not writer.isOpened():
        pytest.skip('no mp4v encoder available in this OpenCV build')
    for n in range(e2e.FRAMES):
        frame = np.full((e2e.HEIGHT, e2e.WIDTH, 3), 60, dtype=np.uint8)
        d = pan(n)
        for _cls, _conf, x1, y1, x2, y2, colour in e2e.scene(n):
            cv2.rectangle(frame, (int(x1 - d), int(y1)), (int(x2 - d), int(y2)), colour, -1)
        writer.write(frame)
    writer.release()
    return path


class PanningDetector(e2e.ScriptedDetector):
    def __init__(self, pan):
        super().__init__()
        self.pan = pan

    def detect_batch_raw(self, images):
        first = self.frames_seen
        out = super().detect_batch_raw(images)
        for k, boxes in enumerate(out):
            boxes._rows[:, [2, 4]] -= self.pan(first + k)
        return out


def _run(tmp_path_factory, pan):
    path = _write(tmp_path_factory.mktemp('panned') / 'match.mp4', pan)
    P = e2e.PITCH
    # Reference (still clip) pixels -> metres, y flipped as in e2e.px, -> StatsBomb.
    clip_to_m = np.array([[1 / e2e.SCALE, 0, 0], [0, -1 / e2e.SCALE, P.width_m], [0, 0, 1.0]])
    ref_to_sb = np.diag([STATSBOMB_LENGTH / P.length_m, STATSBOMB_WIDTH / P.width_m, 1.0]) @ clip_to_m
    frames = np.arange(e2e.FRAMES)
    frame_to_sb = np.array([
        ref_to_sb @ np.array([[1, 0, pan(n)], [0, 1, 0], [0, 0, 1.0]]) for n in frames
    ])
    ball = np.array([e2e.ball_m(n / e2e.FPS) for n in frames])
    cam = PannedCamera.from_arrays(
        frames, frame_to_sb, ref_to_sb, P,
        ball_x=ball[:, 0] * STATSBOMB_LENGTH / P.length_m,
        ball_y=ball[:, 1] * STATSBOMB_WIDTH / P.width_m,
        ball_observed=np.ones(len(frames), bool),
    )
    return analyse_match(
        path, device='cpu', panned=cam, detector=PanningDetector(pan),
        tracker_factory=lambda name, device: e2e.SlotTracker(),
        orientation=e2e.ORIENTATION, period='first_half', side_of_team=e2e.SIDES,
    )


@pytest.fixture(scope='module')
def still(tmp_path_factory):
    return _run(tmp_path_factory, lambda n: 0.0)


@pytest.fixture(scope='module')
def panned(tmp_path_factory):
    return _run(tmp_path_factory, _pan)


class TestEndToEnd:
    def test_the_metre_stages_run(self, panned):
        assert [s.name for s in panned.timings.stages] == e2e.STAGES
        assert panned.movement_available

    def test_the_pan_is_undone_so_nobody_runs_further(self, still, panned):
        def distances(report):
            return sorted(p.movement.distance_m for p in report.players)
        assert distances(panned) == pytest.approx(distances(still), abs=0.5)

    def test_the_teams_and_the_shot_survive_the_pan(self, still, panned):
        assert sorted(p.team for p in panned.players) == sorted(p.team for p in still.players)
        assert len(list(panned.events.shots())) == len(list(still.events.shots())) == 1

    def test_nothing_was_left_unregistered(self, panned):
        assert not any('no pitch registration' in w for w in panned.warnings)
