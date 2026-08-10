"""Telling a camera that moved from twenty-two people who moved.

The detector is easy; not crying wolf is the whole job. A false positive here
condemns a perfectly good match report — `trustworthy` is `not warnings` — so
most of this file is football that must NOT register: a counter-attack, a whole
team pressing as a unit, a sprinter at full pace.

The signal it relies on is that the *median* per-track displacement over a
second is near zero, because players move in different directions, while a
camera moves all of them by one vector at once.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_camera.py -q
"""

from __future__ import annotations

import numpy as np
import pytest

from cv.camera import CameraMotion, Shift, camera_warnings, detect_camera_shift
from cv.frames import FrameRecord, FrameTable

FPS = 30.0
HEIGHT_PX = 60.0          # one player height, the unit everything is measured in


def frame(index: int, positions: dict[int, tuple[float, float]]) -> FrameRecord:
    """`positions` is {track_id: (x, y)} of the player's feet."""
    rows = [
        [track, x - 10, y - HEIGHT_PX, x + 10, y, 0.9]
        for track, (x, y) in sorted(positions.items())
    ]
    return FrameRecord(
        frame_index=index,
        timestamp_s=index / FPS,
        players=np.array(rows, dtype=np.float32).reshape(-1, 6),
    )


def table(frames: list[FrameRecord]) -> FrameTable:
    return FrameTable(fps=FPS, frame_width=1280, frame_height=720, records=frames)


def crowd(n=12):
    """A pitch full of people, spread out the way a formation is."""
    rng = np.random.default_rng(4)
    return {
        t: (float(200 + (t * 90) % 900), float(300 + rng.uniform(-80, 80)))
        for t in range(1, n + 1)
    }


def walk(start: dict, seconds: float, move, step_s=0.5) -> list[FrameRecord]:
    """Frames every `step_s`, with `move(track, t)` giving each player's offset."""
    out = []
    for i in range(int(seconds / step_s) + 1):
        t = i * step_s
        out.append(frame(
            int(round(t * FPS)),
            {k: (x + move(k, t)[0], y + move(k, t)[1]) for k, (x, y) in start.items()},
        ))
    return out


class TestFootballIsNotACameraBump:
    def test_players_milling_about_register_nothing(self):
        rng = np.random.default_rng(7)
        drift = {k: rng.uniform(-1, 1, 2) * 40 for k in crowd()}
        motion = detect_camera_shift(table(walk(
            crowd(), 6.0, lambda k, t: (drift[k][0] * t, drift[k][1] * t),
        )))
        assert motion.moved is False
        assert motion.checked is True

    def test_a_single_sprinter_does_not_move_the_median(self):
        """One player at full pace against eleven who are not.

        This is what the median buys over the mean, and why it is the median.
        """
        motion = detect_camera_shift(table(walk(
            crowd(), 6.0,
            lambda k, t: (250 * t, 0.0) if k == 3 else (0.0, 0.0),
        )))
        assert motion.moved is False

    def test_a_team_breaking_forward_together_does_not_register(self):
        """A counter-attack: half the pitch moving one way, hard.

        This is the case that changed the detector. Six of twelve at 90 px/s
        puts the median exactly midway between the movers and the still — 0.75
        player heights, right on the threshold — because a median describes the
        middle player and the middle player is not the frame. Agreement is what
        rejects it: nobody is actually near that midpoint.
        """
        motion = detect_camera_shift(table(walk(
            crowd(), 6.0,
            lambda k, t: (90.0 * t, 0.0) if k <= 6 else (0.0, 0.0),
        )))
        assert motion.moved is False

    def test_the_split_sprint_really_does_fool_the_median_alone(self):
        """Pinning the reason, so a future simplification cannot undo it.

        With agreement disabled this fires. If someone ever decides the extra
        check is redundant, this is the test that says what it was for.
        """
        frames = table(walk(
            crowd(), 6.0,
            lambda k, t: (90.0 * t, 0.0) if k <= 6 else (0.0, 0.0),
        ))
        assert detect_camera_shift(frames, min_agreement=0.0).moved is True

    def test_even_a_whole_pitch_pressing_is_slower_than_a_bump(self):
        """The hardest honest case: everybody shifting up together.

        A pressing trap moves all of them the same way, which is exactly the
        shape this looks for. The difference is speed — 20 px/s is a third of a
        player height a second; the threshold is three quarters, in one step.
        """
        motion = detect_camera_shift(table(walk(
            crowd(), 8.0, lambda k, t: (20.0 * t, 0.0),
        )))
        assert motion.moved is False


class TestABumpIsFound:
    def bumped(self, dx=0.0, dy=0.0, at=3.0, seconds=6.0):
        return table(walk(
            crowd(), seconds,
            lambda k, t: (dx, dy) if t >= at else (0.0, 0.0),
        ))

    def test_a_sideways_knock_is_caught(self):
        motion = detect_camera_shift(self.bumped(dx=HEIGHT_PX * 1.5))
        assert motion.moved is True
        assert motion.shifts[0].shift == pytest.approx(1.5, abs=0.05)

    def test_a_vertical_nudge_is_caught_too(self):
        # Tilting down is as fatal as panning across, and reads differently in
        # the log to whoever is working out what happened at the field.
        motion = detect_camera_shift(self.bumped(dy=HEIGHT_PX * 1.2))
        assert motion.moved is True
        assert motion.shifts[0].dy_heights == pytest.approx(1.2, abs=0.05)
        assert motion.shifts[0].dx_heights == pytest.approx(0.0, abs=0.05)

    def test_it_says_when_the_metres_stopped_being_trustworthy(self):
        motion = detect_camera_shift(self.bumped(dx=HEIGHT_PX * 2, at=4.0))
        # The knock lands in the sample that closes at 4s.
        assert motion.first_s == pytest.approx(4.0, abs=1.0)

    def test_one_knock_is_one_shift_not_a_permanent_alarm(self):
        """It moved once and stayed there.

        The frames after a bump are steady again from the new position, so a
        detector that kept firing would be reporting the consequence rather than
        the event — and the count is what tells a coach whether the camera was
        knocked once or repeatedly.
        """
        motion = detect_camera_shift(self.bumped(dx=HEIGHT_PX * 2, at=3.0, seconds=10.0))
        assert len(motion.shifts) == 1

    def test_knocked_and_straightened_again_is_two(self):
        # Two shifts with a ruined stretch between them, which reads very
        # differently from one knock nobody corrected.
        frames = walk(
            crowd(), 12.0,
            lambda k, t: (HEIGHT_PX * 2, 0.0) if 3.0 <= t < 7.0 else (0.0, 0.0),
        )
        motion = detect_camera_shift(table(frames))
        assert len(motion.shifts) == 2


class TestRefusingToGuess:
    def test_too_few_players_is_not_a_verdict(self):
        """Four people cannot vote on whether the pitch moved.

        `checked` is False rather than `moved` being False, because "we looked
        and it was fine" and "we could not look" must not read the same.
        """
        motion = detect_camera_shift(table(walk(
            crowd(n=4), 6.0, lambda k, t: (0.0, 0.0),
        )))
        assert motion.checked is False
        assert motion.moved is False

    def test_an_empty_window_is_not_an_error(self):
        assert detect_camera_shift(table([])).checked is False
        assert detect_camera_shift(table([frame(0, crowd())])).checked is False

    def test_frames_too_close_together_are_not_compared(self):
        # Everything inside one sampling interval, so there is no pair a second
        # apart to compare. Nothing to say, and it says nothing.
        frames = [frame(i, crowd()) for i in range(0, 10)]
        assert detect_camera_shift(table(frames)).checked is False

    def test_a_slow_creep_is_deliberately_not_chased(self):
        """A tripod settling over forty minutes, honestly out of reach.

        Two pixels a second is a real drift that would ruin the second half, and
        it is indistinguishable from a team shifting up. Turning the threshold
        down far enough to catch it turns every counter-attack into a camera
        bump, so this is a documented limit rather than a setting.
        """
        motion = detect_camera_shift(table(walk(
            crowd(), 20.0, lambda k, t: (2.0 * t, 0.0),
        )))
        assert motion.moved is False

    def test_players_swapped_wholesale_are_not_compared(self):
        """Nobody is in both frames, so there is no displacement to take.

        A substitution window or a tracker that lost everyone at once must not
        read as the pitch moving.
        """
        first = frame(0, {t: (100.0 + t * 50, 300.0) for t in range(1, 13)})
        second = frame(60, {t: (900.0 - t * 50, 300.0) for t in range(20, 32)})
        motion = detect_camera_shift(table([first, second]))
        assert motion.checked is False


class TestWhatIsSaid:
    def moved_at(self, seconds, count=1):
        return CameraMotion(shifts=[
            Shift(frame_index=int(seconds * FPS) + i, timestamp_s=seconds + i,
                  shift=1.4, dx_heights=1.4, dy_heights=0.0, tracks=11,
                  agreement=0.95)
            for i in range(count)
        ])

    def test_it_names_the_minute_and_what_it_costs(self):
        [note] = camera_warnings(self.moved_at(154.0), calibrated=True)
        assert '2:34' in note
        assert 'metres' in note

    def test_more_than_one_knock_is_counted(self):
        [note] = camera_warnings(self.moved_at(60.0, count=3), calibrated=True)
        assert '2 more times' in note

    def test_nothing_is_said_without_a_calibration(self):
        # No homography, no metres to invalidate. Warning on every uncalibrated
        # clip would be noise on the majority of runs.
        assert camera_warnings(self.moved_at(60.0), calibrated=False) == []

    def test_a_still_camera_says_nothing(self):
        assert camera_warnings(CameraMotion(shifts=[]), calibrated=True) == []

    def test_a_run_that_could_not_check_is_not_condemned(self):
        # `trustworthy` is `not warnings`. A short clip with four people in it
        # has not earned a verdict either way; `checked` carries that instead.
        assert camera_warnings(CameraMotion(shifts=[], checked=False), True) == []

    def test_no_motion_at_all_is_not_an_error(self):
        assert camera_warnings(None, calibrated=True) == []


class TestJson:
    def test_the_whole_thing_survives_json(self):
        import json
        motion = detect_camera_shift(table(walk(
            crowd(), 6.0, lambda k, t: (HEIGHT_PX * 2, 0.0) if t >= 3 else (0.0, 0.0),
        )))
        data = json.loads(json.dumps(motion.to_json()))
        assert data['moved'] is True
        assert data['shifts'][0]['tracks'] == 12

    def test_a_still_camera_reports_null_rather_than_zero(self):
        # It did not move at second zero; it did not move.
        data = CameraMotion(shifts=[]).to_json()
        assert data['first_s'] is None
        assert data['moved'] is False


class TestItReachesTheReport:
    """The wiring, not the detector.

    Verified by hand against the real pipeline first, which is how the gap
    below came to light: on the only clip available, YOLO detects zero people
    per frame, so `analyse_match` produces `checked=False` and the positive
    path never runs. That is correct behaviour and useless as a check, so the
    table is built directly here.
    """

    def report_for(self, frames):
        from cv.pipeline import MatchReport
        from cv.report_json import build_report_json

        motion = detect_camera_shift(table(frames))
        report = MatchReport(source='clip.mp4', duration_s=8.0, processing_s=1.0)
        report.camera = motion
        report.warnings.extend(camera_warnings(motion, calibrated=True))
        return motion, build_report_json(report)

    def bumped(self):
        return walk(crowd(), 8.0,
                    lambda k, t: (HEIGHT_PX * 1.5, 0.0) if t >= 4.0 else (0.0, 0.0))

    def test_a_moved_camera_makes_the_run_untrustworthy(self):
        # Unlike the real-time factor, this genuinely does make the numbers
        # wrong, so it is a warning and `trustworthy` is `not warnings`.
        _, data = self.report_for(self.bumped())
        assert data['trustworthy'] is False
        assert any('camera moved' in w for w in data['warnings'])

    def test_the_detail_travels_in_the_quality_block(self):
        _, data = self.report_for(self.bumped())
        camera = data['quality']['camera']
        assert camera['moved'] is True
        assert camera['first_s'] == pytest.approx(4.0, abs=1.0)
        assert camera['shifts'][0]['agreement'] == pytest.approx(1.0)

    def test_a_still_camera_leaves_the_run_trustworthy(self):
        motion, data = self.report_for(walk(crowd(), 8.0, lambda k, t: (0.0, 0.0)))
        assert motion.moved is False
        assert data['quality']['camera']['moved'] is False
        assert not any('camera' in w for w in data['warnings'])

    def test_the_whole_report_is_still_json_safe(self):
        import json
        _, data = self.report_for(self.bumped())
        json.dumps(data)
