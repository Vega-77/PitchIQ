"""How much of the pitch the camera saw — measured against cameras we construct.

Everything here is synthetic and that is not a compromise. Coverage is pure
geometry: a homography, a frame size, and a pitch. Building the camera means the
right answer is known in advance rather than eyeballed off footage, and the
cases that matter most — a goal out of shot, a pitch running past the horizon —
are ones no clip to hand contains.

Run:  python -m pytest tests/test_coverage.py -q
"""

import numpy as np
import pytest

from cv.calibration import Calibration
from cv.coverage import (
    GOALMOUTH_SEEN,
    _orientation,
    _project,
    PitchCoverage,
    coverage_warnings,
    pitch_coverage,
)
from cv.pitch import Pitch


PITCH = Pitch()


def overhead(
    x_min: float, x_max: float, y_min: float, y_max: float,
    width_px: int = 1920, height_px: int = 1080,
) -> Calibration:
    """A camera straight above the pitch, framing exactly that rectangle.

    No perspective at all, which makes the expected coverage a rectangle
    intersection anyone can do in their head. Perspective is exercised
    separately below — mixing the two would leave a failure ambiguous between
    the projection and the sampling.
    """
    sx = width_px / (x_max - x_min)
    sy = height_px / (y_max - y_min)
    # pixels -> metres, which is the direction Calibration.H runs in.
    H = np.array([
        [1 / sx, 0.0, x_min],
        [0.0, 1 / sy, y_min],
        [0.0, 0.0, 1.0],
    ])
    return Calibration(H, PITCH, image_size=(width_px, height_px))


def sideline(height_m: float = 12.0, back_m: float = 20.0,
             width_px: int = 1920, height_px: int = 1080) -> Calibration:
    """A real perspective camera on the halfway line, `back_m` behind a touchline.

    Built as an actual pinhole projection rather than a fitted homography, so
    the horizon is where physics puts it and the far end of the pitch genuinely
    recedes.
    """
    # World: x along the pitch, y across it, z up. Camera sits beside the pitch
    # at mid-length, raised, looking across and slightly down.
    cx, cy, cz = PITCH.length_m / 2, -back_m, height_m
    f = 900.0

    # Look at the pitch centre.
    target = np.array([PITCH.length_m / 2, PITCH.width_m / 2, 0.0])
    forward = target - np.array([cx, cy, cz])
    forward = forward / np.linalg.norm(forward)
    right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
    right = right / np.linalg.norm(right)
    down = np.cross(forward, right)

    R = np.vstack([right, down, forward])          # world -> camera
    t = -R @ np.array([cx, cy, cz])

    K = np.array([[f, 0, width_px / 2], [0, f, height_px / 2], [0, 0, 1.0]])
    # Ground plane z = 0, so the third column of R drops out.
    P = K @ np.column_stack([R[:, 0], R[:, 1], t])   # metres -> pixels
    return Calibration(np.linalg.inv(P), PITCH, image_size=(width_px, height_px))


class TestWholePitch:
    def test_a_camera_framing_everything_reports_complete(self):
        cov = pitch_coverage(overhead(0, PITCH.length_m, 0, PITCH.width_m))
        assert cov.complete
        assert cov.visible_share == pytest.approx(1.0, abs=0.02)
        assert cov.sees_goal('left') and cov.sees_goal('right')
        assert coverage_warnings(cov) == []

    def test_a_generous_frame_is_still_complete(self):
        # Pitch with room around it — the ordinary good case, and it must not
        # be reported as anything other than whole.
        cov = pitch_coverage(overhead(-10, PITCH.length_m + 10, -8, PITCH.width_m + 8))
        assert cov.visible_share == pytest.approx(1.0, abs=0.01)
        assert coverage_warnings(cov) == []


class TestPartialPitch:
    def test_half_the_length_is_half_the_pitch(self):
        cov = pitch_coverage(overhead(0, PITCH.length_m / 2, 0, PITCH.width_m))
        assert cov.visible_share == pytest.approx(0.5, abs=0.02)
        assert not cov.complete

    def test_the_thirds_say_which_half_was_missing(self):
        cov = pitch_coverage(overhead(0, PITCH.length_m / 2, 0, PITCH.width_m))
        assert cov.left_third == pytest.approx(1.0, abs=0.02)
        assert cov.middle_third == pytest.approx(0.5, abs=0.05)
        assert cov.right_third == pytest.approx(0.0, abs=0.02)

    def test_a_third_left_out_is_named(self):
        cov = pitch_coverage(overhead(0, PITCH.length_m * 2 / 3, 0, PITCH.width_m))
        warnings = coverage_warnings(cov)
        assert any('right' in w for w in warnings), warnings

    def test_a_missing_goalmouth_is_the_loudest_thing_said(self):
        # The case that ruins a shot map: shots at that end are not fewer, they
        # are absent, and every xG behind them with it.
        cov = pitch_coverage(overhead(0, PITCH.length_m * 0.8, 0, PITCH.width_m))
        assert cov.sees_goal('left')
        assert not cov.sees_goal('right')
        assert coverage_warnings(cov)[0].startswith('the right goalmouth')

    def test_a_blind_end_is_not_also_reported_as_a_thin_third(self):
        # Same mistake seen twice. Two sentences about one end of the pitch
        # reads as two problems.
        cov = pitch_coverage(overhead(0, PITCH.length_m * 0.7, 0, PITCH.width_m))
        warnings = coverage_warnings(cov)
        assert len([w for w in warnings if 'right' in w]) == 1, warnings

    def test_a_squeezed_width_loses_the_touchlines(self):
        cov = pitch_coverage(overhead(0, PITCH.length_m, 14, PITCH.width_m - 14))
        expected = (PITCH.width_m - 28) / PITCH.width_m
        assert cov.visible_share == pytest.approx(expected, abs=0.03)
        # Evenly, across all three — nothing about the length changed.
        assert cov.left_third == pytest.approx(cov.right_third, abs=0.02)

    def test_something_short_of_whole_is_still_said_out_loud(self):
        # Every third above the floor and both goals in shot, but not complete.
        # Without this branch the run would report nothing at all.
        cov = pitch_coverage(overhead(0, PITCH.length_m, 4, PITCH.width_m - 4))
        assert not cov.complete
        assert all(s > 0.75 for s in
                   (cov.left_third, cov.middle_third, cov.right_third))
        assert len(coverage_warnings(cov)) == 1
        assert 'framed' in coverage_warnings(cov)[0]


class TestPerspective:
    def test_a_sideline_camera_high_enough_sees_the_pitch(self):
        cov = pitch_coverage(sideline(height_m=25.0, back_m=45.0))
        assert cov.visible_share > 0.9, cov.to_json()
        assert cov.sees_goal('left') and cov.sees_goal('right')

    def test_a_camera_low_and_close_loses_most_of_the_pitch(self):
        # Ground level, right on the touchline: the far half is a few pixels
        # tall and the ends run out of frame entirely.
        cov = pitch_coverage(sideline(height_m=1.2, back_m=1.0))
        assert not cov.complete, cov.to_json()
        assert cov.visible_share < 0.75, cov.to_json()

    def test_raising_the_camera_buys_coverage(self):
        # The one thing FOOTAGE_DAY can actually ask for on the day, so the
        # measure had better move in the direction the advice assumes.
        low = pitch_coverage(sideline(height_m=2.0, back_m=8.0)).visible_share
        high = pitch_coverage(sideline(height_m=20.0, back_m=8.0)).visible_share
        assert high > low, (low, high)


class TestProjection:
    """The behind-the-camera check, pinned where it is visible.

    Kept at this level deliberately. Measured across 750 camera setups, removing
    the sign check moves the reported coverage by at most 0.002 — so no
    assertion about a share would fail if it broke, and a test that cannot fail
    is worse than no test because it reads as protection.
    """

    def _camera_on_the_halfway_line(self):
        """Standing on the pitch at the halfway line, filming the right goal.

        Half the pitch is then literally behind the lens — the case that
        produces a sign flip at all, and one no sideline camera ever hits.
        """
        c = np.array([PITCH.length_m / 2, PITCH.width_m / 2, 1.7])
        target = np.array([PITCH.length_m, PITCH.width_m / 2, 0.0])
        forward = target - c
        forward = forward / np.linalg.norm(forward)
        right = np.cross(forward, np.array([0.0, 0.0, 1.0]))
        right = right / np.linalg.norm(right)
        down = np.cross(forward, right)
        R = np.vstack([right, down, forward])
        K = np.array([[900.0, 0, 960], [0, 900.0, 540], [0, 0, 1.0]])
        P = K @ np.column_stack([R[:, 0], R[:, 1], -R @ c])
        return Calibration(np.linalg.inv(P), PITCH, image_size=(1920, 1080))

    def test_the_half_behind_the_camera_is_marked_as_behind(self):
        cam = self._camera_on_the_halfway_line()
        h_inv = np.linalg.inv(cam.H)
        sign = _orientation(cam, 1920, 1080)

        # A point well down the pitch it is filming, and one behind its back.
        _, _, front = _project(h_inv, np.array([95.0]), np.array([34.0]), sign)
        _, _, back = _project(h_inv, np.array([10.0]), np.array([34.0]), sign)
        assert front[0]
        assert not back[0]

    def test_one_point_answers_the_same_as_seven_thousand(self):
        # The reason the sign is resolved from a reference rather than voted on
        # by the batch: a majority over a pitch half behind the camera is a coin
        # toss, and a single point would always vote itself in front.
        cam = self._camera_on_the_halfway_line()
        h_inv = np.linalg.inv(cam.H)
        sign = _orientation(cam, 1920, 1080)

        xs = np.repeat(np.arange(0.5, PITCH.length_m, 1.0), 68)
        ys = np.tile(np.arange(0.5, PITCH.width_m, 1.0), 105)
        _, _, many = _project(h_inv, xs, ys, sign)
        _, _, one = _project(h_inv, np.array([10.0]), np.array([34.0]), sign)

        assert 0.4 < 1 - many.mean() < 0.6, many.mean()
        assert not one[0]

    def test_negating_the_matrix_changes_nothing(self):
        # A homography is defined up to scale. The orientation reference is what
        # absorbs that; without it every verdict here would invert.
        cam = self._camera_on_the_halfway_line()
        flipped = Calibration(-cam.H, PITCH, image_size=cam.image_size)
        xs, ys = np.array([95.0, 10.0]), np.array([34.0, 34.0])

        straight = _project(np.linalg.inv(cam.H), xs, ys,
                            _orientation(cam, 1920, 1080))[2]
        negated = _project(np.linalg.inv(flipped.H), xs, ys,
                           _orientation(flipped, 1920, 1080))[2]
        assert list(straight) == list(negated)


class TestSilence:
    def test_no_image_size_means_no_answer(self):
        # The frame's own dimensions are the boundary being tested. A guess
        # would report coverage for a camera nobody described.
        blind = Calibration(overhead(0, 105, 0, 68).H, PITCH, image_size=None)
        assert pitch_coverage(blind) is None
        assert coverage_warnings(None) == []

    def test_a_nonsense_frame_size_is_refused_rather_than_divided_by(self):
        cam = Calibration(overhead(0, 105, 0, 68).H, PITCH, image_size=(0, 1080))
        assert pitch_coverage(cam) is None

    def test_an_explicit_size_beats_the_stored_one(self):
        # A run whose frames were resized after calibration.
        cam = overhead(0, PITCH.length_m, 0, PITCH.width_m)
        half = pitch_coverage(cam, image_size=(960, 1080))
        assert half.visible_share == pytest.approx(0.5, abs=0.02)


class TestShape:
    def test_the_thirds_average_to_the_whole(self):
        cov = pitch_coverage(overhead(10, 80, 5, 60))
        weighted = np.average(
            [cov.left_third, cov.middle_third, cov.right_third], weights=[1, 1, 1]
        )
        assert cov.visible_share == pytest.approx(weighted, abs=0.01)

    def test_json_rounds_and_carries_the_verdict(self):
        cov = pitch_coverage(overhead(0, PITCH.length_m, 0, PITCH.width_m))
        data = cov.to_json()
        assert set(data) == {
            'visible_share', 'thirds', 'goalmouths', 'complete', 'cell_m'
        }
        assert set(data['thirds']) == {'left', 'middle', 'right'}
        assert data['complete'] is True

    def test_asking_for_a_third_by_end_matches_the_field(self):
        cov = pitch_coverage(overhead(0, PITCH.length_m * 0.6, 0, PITCH.width_m))
        assert cov.third_share('left') == cov.left_third
        assert cov.third_share('right') == cov.right_third
        with pytest.raises(ValueError):
            cov.third_share('middle')

    def test_the_goalmouth_threshold_is_the_one_documented(self):
        cov = PitchCoverage(
            visible_share=0.9, left_third=1.0, middle_third=1.0, right_third=0.7,
            left_goalmouth=GOALMOUTH_SEEN, right_goalmouth=GOALMOUTH_SEEN - 0.01,
            cell_m=1.0,
        )
        assert cov.sees_goal('left')
        assert not cov.sees_goal('right')
