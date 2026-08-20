"""Calibration and pitch-geometry tests.

The valuable trick here is the synthetic camera: build a known homography,
project the pitch through it to make fake "clicks", then check that fitting
those clicks recovers the original transform. That gives exact ground truth,
which real footage can never provide — on a real frame nobody knows where the
corner flag truly is to the centimetre.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_calibration.py -q
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from cv.calibration import Calibration, Correspondence
from cv.pitch import (
    STATSBOMB_LENGTH,
    STATSBOMB_WIDTH,
    MatchOrientation,
    Pitch,
)


# ---------------------------------------------------------------------------
# A synthetic camera with known ground truth
# ---------------------------------------------------------------------------

def synthetic_camera(pitch: Pitch, width_px: int = 1280, height_px: int = 720):
    """A plausible elevated, slightly off-centre view of the pitch.

    Returns the pitch->pixel matrix, so tests can generate exact pixel
    coordinates for any landmark.
    """
    src = np.array(
        [
            [0.0, 0.0],
            [pitch.length_m, 0.0],
            [pitch.length_m, pitch.width_m],
            [0.0, pitch.width_m],
        ],
        dtype=np.float32,
    )
    # Trapezoid: far touchline compressed toward the horizon, near one wide.
    dst = np.array(
        [
            [140.0, 640.0],
            [1150.0, 640.0],
            [980.0, 300.0],
            [310.0, 300.0],
        ],
        dtype=np.float32,
    )
    import cv2

    return cv2.getPerspectiveTransform(src, dst)


def project(matrix: np.ndarray, x: float, y: float) -> tuple[float, float]:
    import cv2

    pts = np.array([[[x, y]]], dtype=np.float64)
    out = cv2.perspectiveTransform(pts, matrix).reshape(2)
    return (float(out[0]), float(out[1]))


def clicks_for(pitch: Pitch, matrix: np.ndarray, names: list[str], jitter_px: float = 0.0,
               seed: int = 0) -> list[Correspondence]:
    rng = np.random.default_rng(seed)
    out = []
    for name in names:
        x_m, y_m = pitch.landmark(name)
        px, py = project(matrix, x_m, y_m)
        if jitter_px:
            px += float(rng.normal(0, jitter_px))
            py += float(rng.normal(0, jitter_px))
        out.append(Correspondence(name, (px, py)))
    return out


def picker_export(pitch: Pitch, names: list[str] | None = None) -> dict:
    """The JSON the browser picker writes for a set of perfect clicks.

    The `pitch` block carries the markings as well as the size, because that is
    what the picker now writes once a coach has accepted what it measured.
    """
    from dataclasses import asdict

    matrix = synthetic_camera(pitch)
    return {
        "image_size": [1280, 720],
        "pitch": asdict(pitch),
        "points": [
            {"landmark": c.landmark, "x": c.pixel[0], "y": c.pixel[1]}
            for c in clicks_for(pitch, matrix, names or EIGHT_POINTS)
        ],
    }


FOUR_CORNERS = [
    "corner_bottom_left",
    "corner_bottom_right",
    "corner_top_right",
    "corner_top_left",
]

EIGHT_POINTS = FOUR_CORNERS + [
    "halfway_bottom",
    "halfway_top",
    "pen_left_bottom_corner",
    "pen_right_top_corner",
]


@pytest.fixture
def pitch() -> Pitch:
    return Pitch(length_m=105.0, width_m=68.0)


@pytest.fixture
def camera(pitch: Pitch) -> np.ndarray:
    return synthetic_camera(pitch)


# ---------------------------------------------------------------------------
# Pitch geometry
# ---------------------------------------------------------------------------

class TestPitch:
    def test_goal_posts_are_regulation_width(self, pitch: Pitch):
        bottom, top = pitch.goal_posts("left")
        assert top[1] - bottom[1] == pytest.approx(7.32)

    def test_landmarks_lie_within_the_pitch(self, pitch: Pitch):
        for name, (x, y) in pitch.landmarks().items():
            assert pitch.contains(x, y, margin_m=0.01), f"{name} is off the pitch"

    def test_penalty_area_matches_the_laws(self, pitch: Pitch):
        bottom = pitch.landmark("pen_left_bottom_goalline")
        top = pitch.landmark("pen_left_top_goalline")
        corner = pitch.landmark("pen_left_bottom_corner")

        assert top[1] - bottom[1] == pytest.approx(40.32)
        assert corner[0] == pytest.approx(16.5)

    def test_smaller_pitch_scales_the_lines_but_not_the_goal(self):
        small = Pitch(length_m=95.0, width_m=60.0)
        bottom, top = small.goal_posts("right")

        # Goal size is fixed by the Laws even when the field is short.
        assert top[1] - bottom[1] == pytest.approx(7.32)
        assert small.landmark("corner_top_right") == (95.0, 60.0)


class TestStatsBombConversion:
    def test_attacked_goal_lands_at_x_120(self, pitch: Pitch):
        x, y = pitch.to_statsbomb(*pitch.right_goal_centre, attacking_end="right")
        assert x == pytest.approx(STATSBOMB_LENGTH)
        assert y == pytest.approx(STATSBOMB_WIDTH / 2)

    def test_attacking_left_mirrors_so_the_goal_is_still_at_x_120(self, pitch: Pitch):
        # The xG model always assumes the target goal is at x=120, so a team
        # attacking the left end must be mirrored into that frame.
        x, y = pitch.to_statsbomb(*pitch.left_goal_centre, attacking_end="left")
        assert x == pytest.approx(STATSBOMB_LENGTH)
        assert y == pytest.approx(STATSBOMB_WIDTH / 2)

    def test_round_trips(self, pitch: Pitch):
        for end in ("left", "right"):
            for point in [(10.0, 20.0), (52.5, 34.0), (95.0, 60.0)]:
                sb = pitch.to_statsbomb(*point, attacking_end=end)
                back = pitch.from_statsbomb(*sb, attacking_end=end)
                assert back == pytest.approx(point, abs=1e-9)

    def test_a_shot_from_the_same_spot_differs_by_which_way_you_attack(self, pitch: Pitch):
        # 20m from the right goal.
        spot = (85.0, 34.0)
        attacking_right = pitch.to_statsbomb(*spot, attacking_end="right")
        attacking_left = pitch.to_statsbomb(*spot, attacking_end="left")

        dist_right = STATSBOMB_LENGTH - attacking_right[0]
        dist_left = STATSBOMB_LENGTH - attacking_left[0]
        assert dist_right < dist_left, "attacking the far goal must read as farther"


class TestMatchOrientation:
    def test_sides_attack_opposite_ends(self):
        o = MatchOrientation(home_attacks_first_half="right")
        assert o.attacking_end("us", "first_half") == "right"
        assert o.attacking_end("them", "first_half") == "left"

    def test_ends_swap_after_halftime(self):
        o = MatchOrientation(home_attacks_first_half="right")
        assert o.attacking_end("us", "kickoff_2nd") == "left"
        assert o.attacking_end("them", "kickoff_2nd") == "right"

    def test_defending_end_is_always_the_other_one(self):
        o = MatchOrientation(home_attacks_first_half="left")
        for period in ("first_half", "second_half"):
            for side in ("us", "them"):
                assert o.attacking_end(side, period) != o.defending_end(side, period)

    def test_rejects_a_bad_side(self):
        with pytest.raises(ValueError):
            MatchOrientation().attacking_end("nobody", "first_half")


# ---------------------------------------------------------------------------
# Homography
# ---------------------------------------------------------------------------

class TestCalibrationFit:
    def test_recovers_a_known_camera_from_four_corners(self, pitch, camera):
        calib = Calibration.fit(clicks_for(pitch, camera, FOUR_CORNERS), pitch)

        for name, truth in pitch.landmarks().items():
            px = project(camera, *truth)
            recovered = calib.to_pitch(*px)
            assert recovered == pytest.approx(truth, abs=0.01), f"{name} drifted"

    def test_error_is_essentially_zero_on_clean_clicks(self, pitch, camera):
        calib = Calibration.fit(clicks_for(pitch, camera, EIGHT_POINTS), pitch)
        err = calib.error()

        assert err.mean_m < 0.01
        assert err.is_usable

    def test_survives_realistic_click_jitter(self, pitch, camera):
        # A careful human clicking a 1280x720 still is good to a few pixels.
        calib = Calibration.fit(
            clicks_for(pitch, camera, EIGHT_POINTS, jitter_px=3.0, seed=7), pitch
        )
        err = calib.error()

        assert err.is_usable, f"jittered clicks gave {err.summary()}"

    def test_rejects_too_few_points(self, pitch, camera):
        with pytest.raises(ValueError, match="at least 4"):
            Calibration.fit(clicks_for(pitch, camera, FOUR_CORNERS[:3]), pitch)

    def test_rejects_a_duplicated_landmark(self, pitch, camera):
        points = clicks_for(pitch, camera, FOUR_CORNERS)
        points.append(points[0])
        with pytest.raises(ValueError, match="more than once"):
            Calibration.fit(points, pitch)

    def test_rejects_an_unknown_landmark(self, pitch, camera):
        points = clicks_for(pitch, camera, FOUR_CORNERS)
        points[0] = Correspondence("corner_of_the_moon", points[0].pixel)
        with pytest.raises(KeyError):
            Calibration.fit(points, pitch)


class TestHoldoutError:
    def test_needs_five_points(self, pitch, camera):
        calib = Calibration.fit(clicks_for(pitch, camera, FOUR_CORNERS), pitch)
        assert calib.holdout_error() is None

    def test_clean_clicks_hold_out_well(self, pitch, camera):
        calib = Calibration.fit(clicks_for(pitch, camera, EIGHT_POINTS), pitch)
        holdout = calib.holdout_error()

        assert holdout is not None
        assert holdout.mean_m < 0.05

    def test_catches_a_mislabelled_landmark(self, pitch, camera):
        # The realistic human error: clicking the right spot but naming it
        # wrong. Reprojection error alone can absorb this; hold-out cannot.
        points = clicks_for(pitch, camera, EIGHT_POINTS)
        bad_pixel = project(camera, 30.0, 55.0)
        points[5] = Correspondence(points[5].landmark, bad_pixel)

        calib = Calibration.fit(points, pitch)
        holdout = calib.holdout_error()

        assert holdout is not None
        assert not holdout.is_usable, "a mislabelled point should fail the bar"

    def test_names_the_actual_culprit(self, pitch, camera):
        """Detection and localisation want different metrics.

        Hold-out is the better alarm but a poor pointer — dropping a good point
        lets the bad one skew the refit, so an innocent point can rank worst.
        The suspect list uses each point's own residual for that reason.
        """
        points = clicks_for(pitch, camera, EIGHT_POINTS)
        culprit = points[5].landmark
        px, py = points[5].pixel
        points[5] = Correspondence(culprit, (px + 40, py + 25))

        calib = Calibration.fit(points, pitch)
        assert calib.worst_landmarks(1)[0][0] == culprit


class TestProjection:
    def test_ground_point_uses_the_bottom_of_the_box(self, pitch, camera):
        calib = Calibration.fit(clicks_for(pitch, camera, EIGHT_POINTS), pitch)

        feet_px = project(camera, 52.5, 34.0)
        # A detection box roughly 20px wide, 60px tall standing on that point.
        box = (feet_px[0] - 10, feet_px[1] - 60, feet_px[0] + 10, feet_px[1])

        assert calib.ground_point_to_pitch(box) == pytest.approx((52.5, 34.0), abs=0.05)

    def test_box_centre_would_be_wrong(self, pitch, camera):
        """Why ground_point_to_pitch exists rather than using the centre."""
        calib = Calibration.fit(clicks_for(pitch, camera, EIGHT_POINTS), pitch)

        feet_px = project(camera, 52.5, 34.0)
        box = (feet_px[0] - 10, feet_px[1] - 60, feet_px[0] + 10, feet_px[1])

        centre = calib.to_pitch((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        error = math.dist(centre, (52.5, 34.0))
        assert error > 3.0, "using the box centre should be visibly wrong"

    def test_pixels_and_metres_round_trip(self, pitch, camera):
        calib = Calibration.fit(clicks_for(pitch, camera, EIGHT_POINTS), pitch)

        for point in [(0.0, 0.0), (52.5, 34.0), (105.0, 68.0), (16.5, 13.84)]:
            px = calib.to_pixels(*point)
            assert calib.to_pitch(*px) == pytest.approx(point, abs=0.01)

    def test_many_points_matches_one_at_a_time(self, pitch, camera):
        calib = Calibration.fit(clicks_for(pitch, camera, EIGHT_POINTS), pitch)
        pixels = [project(camera, x, 34.0) for x in (10, 30, 50, 70, 90)]

        batch = calib.to_pitch_many(pixels)
        for got, px in zip(batch, pixels):
            assert tuple(got) == pytest.approx(calib.to_pitch(*px), abs=1e-9)


class TestSanityChecks:
    def test_a_good_calibration_reports_nothing(self, pitch, camera):
        calib = Calibration.fit(
            clicks_for(pitch, camera, EIGHT_POINTS), pitch, image_size=(1280, 720)
        )
        assert calib.sanity_check() == []

    def test_flags_a_nonsense_fit(self, pitch):
        # Corners clicked in a scrambled order — still fits, still garbage.
        scrambled = [
            Correspondence("corner_bottom_left", (140.0, 640.0)),
            Correspondence("corner_bottom_right", (310.0, 300.0)),
            Correspondence("corner_top_right", (1150.0, 640.0)),
            Correspondence("corner_top_left", (980.0, 300.0)),
        ]
        calib = Calibration.fit(scrambled, pitch, image_size=(1280, 720))
        assert calib.sanity_check(), "a scrambled fit should raise a complaint"


class TestPersistence:
    def test_round_trips_through_a_file(self, pitch, camera, tmp_path):
        calib = Calibration.fit(
            clicks_for(pitch, camera, EIGHT_POINTS), pitch, image_size=(1280, 720)
        )
        path = tmp_path / "calib.json"
        calib.save(path)

        loaded = Calibration.load(path)
        assert loaded.pitch == calib.pitch
        assert loaded.image_size == (1280, 720)
        assert len(loaded.correspondences) == len(calib.correspondences)

        for point in [(0.0, 0.0), (52.5, 34.0), (105.0, 68.0)]:
            px = calib.to_pixels(*point)
            assert loaded.to_pitch(*px) == pytest.approx(calib.to_pitch(*px), abs=1e-9)

    def test_reads_the_picker_export_format(self, pitch, camera, tmp_path):
        points = clicks_for(pitch, camera, EIGHT_POINTS)
        export = {
            "image_size": [1280, 720],
            "pitch": {"length_m": 105.0, "width_m": 68.0},
            "points": [
                {"landmark": c.landmark, "x": c.pixel[0], "y": c.pixel[1]}
                for c in points
            ],
        }
        path = tmp_path / "picker.json"
        path.write_text(json.dumps(export), encoding="utf-8")

        calib = Calibration.from_picker_export(path)
        assert calib.error().is_usable
        assert calib.image_size == (1280, 720)

    def test_saving_keeps_the_markings_it_was_fitted_against(self, camera, tmp_path):
        """The saved file is what the pipeline loads, so it has to carry them.

        Writing only the length and width here would undo every step upstream:
        the picker measures the paint, the export carries it, the CLI reports
        it — and then tomorrow's run quietly refits against the Laws.
        """
        school = Pitch(length_m=105.0, width_m=68.0, penalty_spot_m=10.0,
                       goal_width_m=7.0)
        calib = Calibration.fit(
            clicks_for(school, camera, EIGHT_POINTS), school, image_size=(1280, 720)
        )
        path = tmp_path / "school.calib.json"
        calib.save(path)

        assert Calibration.load(path).pitch == school

    def test_reads_the_markings_the_picker_measured(self, tmp_path):
        """A school pitch clicked in the picker must load back as itself.

        The picker can now measure the paint from the coach's own clicks and
        write what it found. If this side quietly put the Laws back, the same
        clicks would be refitted against a field that does not exist — and the
        file would look fine while every metre out of it was wrong.
        """
        school = Pitch(
            length_m=105.0, width_m=68.0,
            penalty_area_length_m=15.0, penalty_area_width_m=38.0,
            penalty_spot_m=10.0,
        )
        path = tmp_path / "school.json"
        path.write_text(json.dumps(picker_export(school)), encoding="utf-8")

        calib = Calibration.from_picker_export(path)
        assert calib.pitch.penalty_area_length_m == 15.0
        assert calib.pitch.penalty_area_width_m == 38.0
        assert calib.pitch.penalty_spot_m == 10.0
        assert not calib.pitch.markings_are_standard
        # The clicks were perfect, so with the right field they fit
        # perfectly. The tenth of a millimetre of slack is float32 inside
        # `getPerspectiveTransform`, not anything about the pitch.
        assert calib.error().max_m < 1e-4

    def test_ignoring_those_markings_would_break_the_fit(self, tmp_path):
        """What the test above is worth, measured.

        The same eight perfect clicks read under Laws markings: 0.47m average,
        which passes the half-metre bar, and 1.90m at the worst point, which
        does not. That shape is the danger — an average that looks respectable
        hiding a homography that is wrong everywhere, including at the many
        positions nobody ever clicked.
        """
        school = Pitch(
            length_m=105.0, width_m=68.0,
            penalty_area_length_m=15.0, penalty_area_width_m=38.0,
            penalty_spot_m=10.0,
        )
        export = picker_export(school)
        export["pitch"] = {"length_m": 105.0, "width_m": 68.0}
        path = tmp_path / "laws.json"
        path.write_text(json.dumps(export), encoding="utf-8")

        err = Calibration.from_picker_export(path).error()
        assert err.mean_m == pytest.approx(0.474, abs=0.01)
        assert err.max_m == pytest.approx(1.896, abs=0.01)
        assert not err.is_usable

    def test_a_file_without_markings_still_loads_at_the_laws(self, pitch, camera,
                                                             tmp_path):
        """Every export written before the picker could measure paint."""
        export = {
            "image_size": [1280, 720],
            "pitch": {"length_m": 100.0, "width_m": 64.0},
            "points": [
                {"landmark": c.landmark, "x": c.pixel[0], "y": c.pixel[1]}
                for c in clicks_for(pitch, camera, EIGHT_POINTS)
            ],
        }
        path = tmp_path / "old.json"
        path.write_text(json.dumps(export), encoding="utf-8")

        calib = Calibration.from_picker_export(path)
        assert calib.pitch.length_m == 100.0
        assert calib.pitch.width_m == 64.0
        assert calib.pitch.markings_are_standard
