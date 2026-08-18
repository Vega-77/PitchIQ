"""analyse_match, start to finish, on a clip made of rectangles.

This is the one function every published number comes out of, and until the
detector seam reached it nothing could call it: `analyse_match` built its own
`TrackedFramePass`, so running it meant installing ultralytics and torch, which
`requirements-test.txt` deliberately leaves out and `tests/test_requirements.py`
actively forbids. Every subsystem below had unit tests; the assembly of them had
none.

That gap is the shape of the two worst bugs this repo has shipped, both still
recorded in comments: `ppda` was null in every report ever produced because
nothing handed `team_stats` a pitch, and `attach_xg` sat with no caller for
months while the coach's screen quietly filtered out the empty row. Neither is
visible to a unit test. Both are visible here.

So what these tests pin is the wiring — that each stage runs, that its output
reaches the report, and that the calibrated and uncalibrated paths differ in
exactly the documented ways. Treat this as a check that the chain executes, not
as a measurement. The footage is nine seconds of coloured boxes moving along
arithmetic; the pass is a pass because the script says so, not because anything
in the pipeline recognised football.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_pipeline_end_to_end.py -q
"""

from __future__ import annotations

import json
import math

import cv2
import numpy as np
import pytest

from cv.calibration import Calibration, Correspondence
from cv.detector import CLASS_BALL, CLASS_PERSON
from cv.events import SHOT_SPEED_PH_S
from cv.pipeline import analyse_match
from cv.pitch import MatchOrientation, Pitch
from cv.report_json import build_report_json
from cv.teams import TEAM_A, TEAM_B

# 14 pixels to the metre puts the whole pitch in a 1470x952 frame and makes a
# 1.8m player 25 pixels tall — small, but above the 6-pixel floor `torso_patch`
# needs before it will read a shirt colour at all.
SCALE = 14.0
PITCH = Pitch()
WIDTH = int(PITCH.length_m * SCALE)
HEIGHT = int(PITCH.width_m * SCALE)
FPS = 30.0
FRAMES = 270                                    # nine seconds

RED = (40, 40, 220)                             # BGR, as OpenCV wants it
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
GOAL_M = (-2.0, PITCH.width_m / 2)              # past the line, not on it

ORIENTATION = MatchOrientation(home_attacks_first_half='right')
SIDES = {TEAM_A: 'us', TEAM_B: 'them'}

STAGES = [
    'load the detector', 'detect, track and sample colour', 'ball', 'camera',
    'possession', 'identity', 'thumbnails', 'touches and events',
    'expected goals', 'movement',
]


def px(x_m, y_m):
    """Pitch metres to image pixels. Y is flipped; the image grows downward."""
    return (x_m * SCALE, (PITCH.width_m - y_m) * SCALE)


def wander(home, i, t, sign):
    """A slow orbit, at a different phase and heading for every player.

    Not decoration. `cv/camera.py` calls the camera moved when the *median*
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


@pytest.fixture(scope='module')
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp('e2e') / 'match.mp4'
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*'mp4v'), FPS, (WIDTH, HEIGHT)
    )
    if not writer.isOpened():
        pytest.skip('no mp4v encoder available in this OpenCV build')
    for n in range(FRAMES):
        frame = np.full((HEIGHT, WIDTH, 3), 60, dtype=np.uint8)
        for _cls, _conf, x1, y1, x2, y2, colour in scene(n):
            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), colour, -1)
        writer.write(frame)
    writer.release()
    return path


@pytest.fixture(scope='module')
def calibration_path(tmp_path_factory):
    """A homography fitted to seven landmarks, not hand-built.

    `Calibration.error()` returns NaN with no correspondences behind it, which
    would poison `calibration_error_m` all the way into the JSON — so the
    fixture goes through `fit` the way a real calibration file does.
    """
    names = [
        'corner_bottom_left', 'corner_top_left', 'corner_bottom_right',
        'corner_top_right', 'centre_spot', 'halfway_bottom', 'halfway_top',
    ]
    points = [Correspondence(n, px(*PITCH.landmark(n))) for n in names]
    fitted = Calibration.fit(points, PITCH, image_size=(WIDTH, HEIGHT))
    path = tmp_path_factory.mktemp('e2e') / 'match.calib.json'
    fitted.save(path)
    return path


def _run(clip, calibration_path):
    return analyse_match(
        clip,
        calibration_path=calibration_path,
        device='cpu',
        detector=ScriptedDetector(),
        tracker_factory=lambda name, device: SlotTracker(),
        orientation=ORIENTATION,
        period='first_half',
        side_of_team=SIDES,
    )


@pytest.fixture(scope='module')
def calibrated(clip, calibration_path):
    return _run(clip, calibration_path)


@pytest.fixture(scope='module')
def uncalibrated(clip):
    return _run(clip, None)


def _shots(report):
    return list(report.events.shots()) if report.events else []


def _strike(report):
    """The touch that struck the ball at goal — the last one in the clip."""
    return max(report.touches.touches, key=lambda t: t.timestamp_s)


class TestTheChainRuns:
    def test_the_clip_is_read_end_to_end(self, calibrated, uncalibrated):
        assert calibrated.duration_s == pytest.approx(FRAMES / FPS)
        assert uncalibrated.duration_s == pytest.approx(FRAMES / FPS)

    def test_every_stage_ran_and_was_timed(self, calibrated):
        assert [s.name for s in calibrated.timings.stages] == STAGES

    def test_the_stages_that_need_a_calibration_are_skipped_without_one(self, uncalibrated):
        # Not a subset check. 'expected goals' and 'movement' are the last two,
        # and this is the assertion that fails the day one of them stops being
        # called at all — the way attach_xg stopped being called for months.
        assert [s.name for s in uncalibrated.timings.stages] == STAGES[:-2]

    def test_the_report_serialises_without_a_nan(self, calibrated, uncalibrated):
        for report in (calibrated, uncalibrated):
            json.dumps(build_report_json(report), allow_nan=False)

    def test_both_reports_carry_the_same_top_level_keys(self, calibrated, uncalibrated):
        assert sorted(build_report_json(calibrated)) == sorted(build_report_json(uncalibrated))

    def test_the_ball_was_followed_through_every_frame(self, calibrated):
        assert len(calibrated.ball.points) == FRAMES


class TestTeams:
    def test_ten_tracks_split_five_and_five(self, calibrated):
        teams = [p.team for p in calibrated.players]
        assert len(teams) == 10
        assert teams.count(TEAM_A) == 5 and teams.count(TEAM_B) == 5

    def test_the_red_shirts_are_team_b(self, calibrated):
        # `_kmeans_two` seeds farthest-first from `samples[0]`, so the cluster
        # holding the first track emitted ends up as label 1 — TEAM_B. Pinning
        # the label rather than only the split means a change in seeding fails
        # here, loudly, instead of silently moving the shot to the other team.
        by_track = {p.track_id: p.team for p in calibrated.players}
        assert [by_track[t] for t in range(100, 105)] == [TEAM_B] * 5
        assert [by_track[t] for t in range(105, 110)] == [TEAM_A] * 5

    def test_the_two_kits_are_measurably_different(self, calibrated):
        assert calibrated.kit_separation > 0

    def test_nobody_was_ruled_out(self, calibrated):
        assert not calibrated.participants.excluded

    def test_every_player_was_tracked_for_the_whole_clip(self, calibrated):
        minutes = {round(p.minutes_tracked, 4) for p in calibrated.players}
        assert len(minutes) == 1
        assert minutes.pop() == pytest.approx(FRAMES / FPS / 60.0, abs=1e-3)


class TestPossession:
    def test_the_team_with_the_ball_holds_it(self, calibrated):
        possession = calibrated.possession
        assert possession.team_b_s > 5.0
        assert possession.team_a_s == 0.0

    def test_the_camera_is_not_reported_as_having_moved(self, calibrated):
        assert calibrated.camera.checked is True
        assert calibrated.camera.shifts == []


class TestEvents:
    def test_the_pass_and_the_shot_both_land(self, calibrated):
        types = [e.type for e in calibrated.events.events]
        assert 'pass' in types and 'shot' in types

    def test_the_strike_reads_as_a_strike(self, calibrated):
        strike = _strike(calibrated)
        assert strike.timestamp_s == pytest.approx(STRIKE_S, abs=0.05)
        assert strike.track_id == 100 + SHOOTER
        assert strike.speed_after_ph_s > SHOT_SPEED_PH_S

    def test_the_shot_is_a_goal_at_the_end_that_team_attacks(self, calibrated):
        shots = _shots(calibrated)
        assert len(shots) == 1
        shot = shots[0]
        assert shot.team == TEAM_B
        assert calibrated.attacking_ends[TEAM_B] == 'left'
        assert shot.outcome == 'goal'
        assert shot.on_target is True

    def test_the_shot_is_placed_on_the_pitch(self, calibrated):
        shot = _shots(calibrated)[0]
        x_m, y_m = shot.start_m
        assert x_m == pytest.approx(16.0, abs=2.0)
        assert y_m == pytest.approx(PITCH.width_m / 2, abs=3.0)
        assert shot.distance_to_goal_m == pytest.approx(16.0, abs=2.0)
        assert 'in_box' in shot.tags

    def test_without_a_calibration_no_event_has_a_position(self, uncalibrated):
        assert uncalibrated.events.events
        assert all(e.start_m is None for e in uncalibrated.events.events)

    def test_without_a_calibration_there_are_no_shots(self, uncalibrated):
        # Not a gap in coverage — a shot needs metres to know it went goalward,
        # and refusing to guess is the documented behaviour.
        assert _shots(uncalibrated) == []


class TestExpectedGoals:
    def test_the_shot_carries_a_probability_from_the_real_model(self, calibrated):
        pytest.importorskip('onnxruntime')
        shot = _shots(calibrated)[0]
        assert shot.xg is not None
        assert 0.0 < shot.xg < 1.0

    def test_the_header_reading_is_computed_alongside_it(self, calibrated):
        pytest.importorskip('onnxruntime')
        shot = _shots(calibrated)[0]
        assert shot.xg_header is not None
        assert 0.0 < shot.xg_header < 1.0

    def test_the_probability_reaches_the_team_total(self, calibrated):
        pytest.importorskip('onnxruntime')
        shot = _shots(calibrated)[0]
        totals = build_report_json(calibrated)['teams']
        # The JSON rounds to three places on the way out; the point is that the
        # team total is the shot's number and not a zero or a None.
        assert totals[TEAM_B]['xg'] == pytest.approx(shot.xg, abs=1e-3)

    def test_the_uncalibrated_run_claims_no_expected_goals(self, uncalibrated):
        totals = build_report_json(uncalibrated)['teams']
        assert totals.get(TEAM_B, {}).get('xg') is None


class TestWhatOnlyACalibrationBuys:
    def test_the_pitch_is_known_only_when_calibrated(self, calibrated, uncalibrated):
        assert calibrated.pitch == PITCH
        assert uncalibrated.pitch is None

    def test_movement_is_measured_only_when_calibrated(self, calibrated, uncalibrated):
        assert all(p.movement is not None for p in calibrated.players)
        assert all(p.movement is None for p in uncalibrated.players)

    def test_coverage_and_territory_need_metres(self, calibrated, uncalibrated):
        assert calibrated.coverage is not None
        assert calibrated.territory is not None
        assert uncalibrated.coverage is None
        assert uncalibrated.territory is None

    def test_the_keeper_search_says_so_when_it_could_not_run(self, calibrated, uncalibrated):
        assert calibrated.keepers.method == 'colour+position'
        assert uncalibrated.keepers.method == 'unavailable'

    def test_the_missing_calibration_is_warned_about(self, calibrated, uncalibrated):
        assert not any('no calibration supplied' in w for w in calibrated.warnings)
        assert any('no calibration supplied' in w for w in uncalibrated.warnings)

    def test_a_still_camera_raises_no_camera_warning(self, calibrated, uncalibrated):
        for report in (calibrated, uncalibrated):
            assert not any('camera moved' in w for w in report.warnings)
