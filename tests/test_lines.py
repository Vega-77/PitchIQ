"""Pitch-line detection and the calibration check built on it.

The same synthetic-camera trick `tests/test_calibration.py` uses, one step
further: instead of projecting landmarks to make fake clicks, project the whole
painted pitch to make a fake *frame*, then run the real detector over the real
pixels. Every number here has exact ground truth behind it, which no real
footage can offer -- nobody knows to the centimetre where the paint is on a
high school pitch.

One circularity has to be kept honest. The frame is drawn from
`pitch_line_segments`, and `fit_to_lines` scores against the same model, so a
mistake in the model would cancel out and look like a perfect fit. Two things
break that:

  * `test_model_matches_the_landmark_table` checks the model against
    `Pitch.landmarks()`, which is an independent source that calibration files
    already depend on.
  * the refinement tests are scored on landmark error in metres against
    `Pitch.landmark`, never on the line fit. Refinement has to move the
    calibration closer to where the pitch actually is, not merely closer to
    where this module thinks its lines are.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_lines.py -q
"""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from cv.calibration import Calibration, Correspondence
from cv.lines import (
    LineFit,
    _is_improvement,
    Segment,
    detect_segments,
    draw_pitch_lines,
    fit_to_lines,
    merge_collinear,
    pitch_line_segments,
    refine,
)
from cv.pitch import Pitch

FRAME_W, FRAME_H = 1280, 720
GRASS_BGR = (45, 125, 45)
PAINT_BGR = (235, 235, 235)


# ---------------------------------------------------------------------------
# The synthetic camera and the frame it sees
# ---------------------------------------------------------------------------

def camera(pitch: Pitch) -> np.ndarray:
    """Metres -> pixels for a plausible elevated, slightly off-centre view.

    Deliberately the same trapezoid `tests/test_calibration.py` uses, so a
    failure here and a failure there are talking about the same camera.
    """
    src = np.array(
        [[0.0, 0.0], [pitch.length_m, 0.0],
         [pitch.length_m, pitch.width_m], [0.0, pitch.width_m]],
        dtype=np.float32,
    )
    dst = np.array(
        [[140.0, 640.0], [1150.0, 640.0], [980.0, 300.0], [310.0, 300.0]],
        dtype=np.float32,
    )
    return cv2.getPerspectiveTransform(src, dst)


def to_px(matrix: np.ndarray, x_m: float, y_m: float) -> tuple[float, float]:
    out = cv2.perspectiveTransform(
        np.array([[[x_m, y_m]]], dtype=np.float64), matrix
    ).reshape(2)
    return (float(out[0]), float(out[1]))


def render(pitch: Pitch, matrix: np.ndarray, *, paint: bool = True) -> np.ndarray:
    """A frame: dark surround, green pitch, white lines."""
    frame = np.full((FRAME_H, FRAME_W, 3), 30, dtype=np.uint8)
    quad = np.array(
        [[to_px(matrix, 0, 0), to_px(matrix, pitch.length_m, 0),
          to_px(matrix, pitch.length_m, pitch.width_m),
          to_px(matrix, 0, pitch.width_m)]],
        dtype=np.int32,
    )
    cv2.fillPoly(frame, quad, GRASS_BGR)
    if paint:
        for seg in pitch_line_segments(pitch):
            a = to_px(matrix, seg.x1, seg.y1)
            b = to_px(matrix, seg.x2, seg.y2)
            cv2.line(
                frame,
                (int(round(a[0])), int(round(a[1]))),
                (int(round(b[0])), int(round(b[1]))),
                PAINT_BGR, 3, cv2.LINE_AA,
            )
    return frame


def truth_calibration(pitch: Pitch, matrix: np.ndarray) -> Calibration:
    """The exact answer: pixels -> metres, with no clicks involved."""
    return Calibration(np.linalg.inv(matrix.astype(np.float64)), pitch)


def landmark_error(calibration: Calibration, pitch: Pitch, matrix: np.ndarray):
    """Mean and worst landmark error in metres, against the real pitch.

    This is the independent scorer. It never consults the line model: it takes
    each landmark's true metre position, finds the pixel the camera really puts
    it at, and asks where the calibration under test thinks that pixel is.
    """
    errors = []
    for name in pitch.landmarks():
        x_m, y_m = pitch.landmark(name)
        got = calibration.to_pitch(*to_px(matrix, x_m, y_m))
        errors.append(math.hypot(got[0] - x_m, got[1] - y_m))
    return float(np.mean(errors)), float(np.max(errors))


def rough_clicks(pitch, matrix, jitter_px, seed):
    """A human's landmark clicks, off by a few pixels each."""
    names = [
        "corner_bottom_left", "corner_bottom_right",
        "corner_top_right", "corner_top_left",
        "halfway_bottom", "halfway_top",
        "pen_left_bottom_corner", "pen_right_top_corner",
    ]
    rng = np.random.default_rng(seed)
    out = []
    for name in names:
        px, py = to_px(matrix, *pitch.landmark(name))
        out.append(
            Correspondence(
                name,
                (px + float(rng.normal(0, jitter_px)),
                 py + float(rng.normal(0, jitter_px))),
            )
        )
    return out


@pytest.fixture(scope="module")
def scene():
    pitch = Pitch()
    matrix = camera(pitch)
    frame = render(pitch, matrix)
    return pitch, matrix, frame, detect_segments(frame)


# ---------------------------------------------------------------------------
# The model of what is painted
# ---------------------------------------------------------------------------

class TestPitchModel:
    def test_model_matches_the_landmark_table(self):
        """The line model and the click vocabulary must describe one pitch.

        This is the test that stops the rest of the file marking its own
        homework: `Pitch.landmarks()` is what calibration files store and what
        the picker page offers, and every one of these points is where two
        painted lines meet. If the model disagrees with it, the frames rendered
        below are of some other pitch.
        """
        pitch = Pitch()
        endpoints = set()
        for seg in pitch_line_segments(pitch):
            endpoints.add((round(seg.x1, 6), round(seg.y1, 6)))
            endpoints.add((round(seg.x2, 6), round(seg.y2, 6)))

        for name in (
            "corner_bottom_left", "corner_top_left",
            "corner_bottom_right", "corner_top_right",
            "halfway_bottom", "halfway_top",
            "pen_left_bottom_corner", "pen_left_top_corner",
            "pen_right_bottom_corner", "pen_right_top_corner",
            "goalarea_left_bottom_corner", "goalarea_left_top_corner",
            "goalarea_right_bottom_corner", "goalarea_right_top_corner",
        ):
            x_m, y_m = pitch.landmark(name)
            assert (round(x_m, 6), round(y_m, 6)) in endpoints, (
                f"{name} is not an endpoint of any modelled line"
            )

    def test_every_landmark_lies_on_a_painted_line(self):
        """Including the ones that are not corners.

        The penalty spots and the centre spot are painted marks rather than
        line ends, so they are allowed to be off the lines -- everything else
        is a point on the paint, and being metres away from all of it would
        mean the model is missing a line the picker expects a human to click.
        """
        pitch = Pitch()
        model = pitch_line_segments(pitch)
        for name, (x_m, y_m) in pitch.landmarks().items():
            if "spot" in name:
                continue
            nearest = min(seg.distance_to(x_m, y_m) for seg in model)
            assert nearest < 0.01, f"{name} sits {nearest:.2f}m off every line"

    def test_a_measured_pitch_gets_its_own_model(self):
        """Dimensions are configurable because school pitches vary, and the
        check has to move with them or it grades a 90m pitch against a 105m
        one."""
        small = pitch_line_segments(Pitch(length_m=90.0, width_m=55.0))
        assert max(max(s.x1, s.x2) for s in small) == pytest.approx(90.0)
        assert max(max(s.y1, s.y2) for s in small) == pytest.approx(55.0)

        # The box is fixed by the Laws, so it does not shrink with the pitch.
        default = pitch_line_segments(Pitch())
        assert len(small) == len(default)


# ---------------------------------------------------------------------------
# Segment geometry
# ---------------------------------------------------------------------------

class TestSegment:
    def test_distance_is_to_the_segment_not_its_infinite_line(self):
        """A point beyond the end of a line is not on that line.

        The goal line and the halfway line are parallel; if distances were
        measured to infinite lines, a pixel out past the corner flag would
        match the halfway line at zero and the check would report a perfect fit
        for a calibration that is 52 metres wrong.
        """
        seg = Segment(0.0, 0.0, 10.0, 0.0)
        assert seg.distance_to(5.0, 3.0) == pytest.approx(3.0)
        assert seg.distance_to(14.0, 0.0) == pytest.approx(4.0)
        assert seg.distance_to(-3.0, 4.0) == pytest.approx(5.0)

    def test_closest_point_is_clamped_to_the_ends(self):
        seg = Segment(0.0, 0.0, 10.0, 0.0)
        assert seg.closest_point(5.0, 9.0) == pytest.approx((5.0, 0.0))
        assert seg.closest_point(99.0, 9.0) == pytest.approx((10.0, 0.0))

    def test_sample_spans_the_whole_segment(self):
        seg = Segment(0.0, 0.0, 40.0, 0.0)
        points = seg.sample(8.0)
        assert points[0] == pytest.approx((0.0, 0.0))
        assert points[-1] == pytest.approx((40.0, 0.0))
        assert len(points) == 6

    def test_a_zero_length_segment_does_not_divide_by_zero(self):
        seg = Segment(3.0, 4.0, 3.0, 4.0)
        assert seg.distance_to(3.0, 0.0) == pytest.approx(4.0)
        assert seg.closest_point(0.0, 0.0) == (3.0, 4.0)
        assert seg.sample(8.0) == [(3.0, 4.0), (3.0, 4.0)]

    def test_angle_ignores_which_way_round(self):
        assert Segment(0, 0, 10, 0).angle_deg == pytest.approx(0.0)
        assert Segment(10, 0, 0, 0).angle_deg == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Merging fragments
# ---------------------------------------------------------------------------

class TestMerge:
    def test_the_two_edges_of_a_stripe_become_its_centre(self):
        """Canny finds edges, not centres, and the difference is a bias.

        A painted stripe arrives as two parallel lines a few pixels apart.
        Adopting either one shifts every measurement by half a line width in
        the same direction -- and, measured on the synthetic camera, that was
        enough to make a calibration shifted onto the near edge score *better*
        than the true one. The merged result has to land between them.
        """
        merged = merge_collinear([
            Segment(0.0, 100.0, 400.0, 100.0),
            Segment(0.0, 104.0, 400.0, 104.0),
        ])
        assert len(merged) == 1
        assert merged[0].midpoint[1] == pytest.approx(102.0, abs=0.01)

    def test_a_gap_where_a_player_stood_is_not_bridged(self):
        """Two pieces of one touchline stay two pieces.

        Joining them would read better and measure worse: the merged segment
        would be sampled across the stretch the player was hiding, and those
        samples would be counted as paint that was seen. Coverage is the
        number this module tells you to read first, and it has to mean what it
        says. Each fragment still scores against the model on its own, so
        nothing is lost but the tidy picture.
        """
        merged = merge_collinear([
            Segment(0.0, 50.0, 180.0, 50.0),
            Segment(240.0, 50.0, 600.0, 50.0),
        ])
        assert len(merged) == 2

    def test_fragments_that_overlap_become_one(self):
        """Hough returns overlapping pieces of the same stripe constantly, and
        those carry no hidden stretch between them -- they are two readings of
        the same paint, and averaging them is what `_average_line` is for."""
        merged = merge_collinear([
            Segment(0.0, 50.0, 400.0, 50.0),
            Segment(200.0, 52.0, 600.0, 52.0),
        ])
        assert len(merged) == 1
        assert merged[0].length == pytest.approx(600.0, abs=2.0)

    def test_lines_that_are_not_the_same_line_stay_apart(self):
        merged = merge_collinear([
            Segment(0.0, 50.0, 600.0, 50.0),     # far apart, same angle
            Segment(0.0, 400.0, 600.0, 400.0),
            Segment(300.0, 0.0, 300.0, 600.0),   # perpendicular
        ])
        assert len(merged) == 3

    def test_nothing_in_nothing_out(self):
        assert merge_collinear([]) == []


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class TestDetect:
    def test_it_finds_the_long_lines(self, scene):
        _, _, _, segments = scene
        assert len(segments) >= 8, "the pitch has more lines than that"
        # Longest first, and the longest of them spans most of the frame --
        # that is a touchline, and if it is missing nothing downstream works.
        assert segments[0].length > 900
        assert segments == sorted(segments, key=lambda s: -s.length)

    def test_a_pitch_with_no_paint_yields_nothing(self, scene):
        pitch, matrix, _, _ = scene
        assert detect_segments(render(pitch, matrix, paint=False)) == []

    def test_an_empty_frame_is_not_an_error(self):
        assert detect_segments(np.zeros((0, 0, 3), dtype=np.uint8)) == []
        assert detect_segments(None) == []

    def test_a_white_shirt_on_the_grass_is_not_a_line(self, scene):
        """The mask cannot tell paint from kit; the length floor can.

        Everything pale and on the field survives `line_mask`, players
        included. What stops a white shirt being scored as a touchline is that
        nothing on a person is forty pixels of straight edge.
        """
        pitch, matrix, _, baseline = scene
        frame = render(pitch, matrix)
        cv2.circle(frame, (700, 500), 9, PAINT_BGR, -1)
        cv2.circle(frame, (500, 560), 11, PAINT_BGR, -1)
        after = detect_segments(frame)
        assert len(after) <= len(baseline) + 1

    def test_the_crowd_behind_the_touchline_is_ignored(self, scene):
        """`field_mask` is what makes this work: a long white barrier outside
        the green is exactly the shape of a touchline and would be fitted as
        one if the detector looked anywhere but the pitch."""
        pitch, matrix, _, baseline = scene
        frame = render(pitch, matrix)
        cv2.line(frame, (0, 60), (1279, 60), PAINT_BGR, 5)
        after = detect_segments(frame)
        assert all(min(s.y1, s.y2) > 200 for s in after), (
            "a line above the pitch was detected"
        )
        assert len(after) == len(baseline)


# ---------------------------------------------------------------------------
# Scoring a calibration
# ---------------------------------------------------------------------------

class TestFitToLines:
    def test_the_true_calibration_lands_on_the_paint(self, scene):
        pitch, matrix, _, segments = scene
        fit = fit_to_lines(truth_calibration(pitch, matrix), segments)

        assert fit.coverage == pytest.approx(1.0)
        # Measured 0.10m; the residual is rasterising 12cm of paint onto a
        # pixel grid seen from 60 metres away, not a fitting error.
        assert fit.median_m < 0.25
        assert fit.p90_m < 0.5
        assert fit.is_usable

    def test_a_badly_wrong_calibration_reports_a_small_error_over_nothing(self, scene):
        """The trap this metric exists to survive, pinned as a test.

        Shift the whole calibration 25 metres up-pitch and the median error
        barely moves: 0.13m, against the true fit's own 0.10m. The points that
        no longer sit near any line are dropped rather than counted, so all
        that is left is the handful that coincidentally still do.
        Coverage is the number that notices, and `is_usable` is why it has to
        be part of the verdict rather than a footnote beside it.
        """
        pitch, matrix, _, segments = scene
        shifted = Calibration(
            np.array([[1, 0, 25.0], [0, 1, 0], [0, 0, 1]])
            @ truth_calibration(pitch, matrix).H,
            pitch,
        )
        fit = fit_to_lines(shifted, segments)

        assert fit.median_m < 0.5, "the premise of this test is a small median"
        assert fit.coverage < 0.6, "coverage should have collapsed"
        assert not fit.is_usable

    def test_a_shift_along_the_touchlines_is_caught_by_the_tail(self, scene):
        """The documented blind spot, and the reason p90 is in the verdict.

        A line constrains a fit only perpendicular to itself, and most of a
        pitch is two long parallel touchlines. Slide everything three metres
        up-pitch and those points do not move relative to their own line at
        all, so the median stays inside the bar. Only the goal lines, the
        halfway line and the box edges object -- and they show up in the tail.
        """
        pitch, matrix, _, segments = scene
        shifted = Calibration(
            np.array([[1, 0, 3.0], [0, 1, 0], [0, 0, 1]])
            @ truth_calibration(pitch, matrix).H,
            pitch,
        )
        fit = fit_to_lines(shifted, segments)

        assert fit.coverage == pytest.approx(1.0)
        assert fit.median_m < 0.5, "the median is not what catches this"
        assert fit.p90_m > 1.5
        assert not fit.is_usable

    def test_nothing_to_check_against_is_reported_as_nothing(self, scene):
        """Not as a score of zero, and not as an exception.

        A frame with no findable paint says nothing about the calibration, and
        the JSON has to carry that distinction out to whatever reads it --
        absent is not the same as perfect and not the same as terrible.
        """
        pitch, matrix, _, _ = scene
        fit = fit_to_lines(truth_calibration(pitch, matrix), [])

        assert fit.sampled == 0
        assert fit.matched == 0
        assert not fit.is_usable
        assert fit.to_json()["median_m"] is None
        assert fit.to_json()["p90_m"] is None
        assert "no line pixels" in fit.summary()

    def test_a_fit_that_matched_nothing_still_says_how_much_it_looked_at(self):
        fit = LineFit(float("nan"), float("nan"), 400, 0)
        assert fit.coverage == 0.0
        assert "400 sampled" in fit.summary()
        assert not fit.is_usable


# ---------------------------------------------------------------------------
# Refinement
# ---------------------------------------------------------------------------

class TestRefine:
    """Refinement, and the exact size of what it cannot do.

    Every test here scores the result with `landmark_error` -- true metre
    positions through the true camera -- and never with the line fit. The line
    fit is the thing refinement optimises; using it to grade refinement would
    only prove the optimiser works.
    """

    def sweep(self, pitch, matrix, segments, jitter_px, trials=20):
        """Refine `trials` independently jittered click sets.

        One seed is an anecdote. *Which* landmarks a random draw happens to
        displace matters more than how far it displaces them -- a bad pull on
        two corners is worth several pixels everywhere else -- so a single seed
        can be made to show very nearly anything.
        """
        rows = []
        for seed in range(trials):
            rough = Calibration.fit(
                rough_clicks(pitch, matrix, jitter_px, seed),
                pitch, (FRAME_W, FRAME_H),
            )
            refined, _, after = refine(rough, segments)
            rows.append({
                "before": landmark_error(rough, pitch, matrix),
                "after": landmark_error(refined, pitch, matrix),
                "usable": after.is_usable,
            })
        return rows

    def test_it_pulls_a_carefully_clicked_calibration_onto_the_paint(self, scene):
        """Three pixels of jitter is a coach concentrating on a phone screen.

        Across twenty click sets refinement improved every one and made none
        worse: the typical worst landmark went from 1.50m -- sitting exactly on
        `CalibrationError`'s limit, so half of these would have been rejected
        -- to 0.20m. That is the feature. The clicks locate the pitch; the
        paint, which nobody had to click, sharpens it.
        """
        pitch, matrix, _, segments = scene
        rows = self.sweep(pitch, matrix, segments, 3.0)

        assert all(r["after"][0] <= r["before"][0] for r in rows), (
            "refinement made a landmark fit worse"
        )
        before_max = float(np.median([r["before"][1] for r in rows]))
        after_max = float(np.median([r["after"][1] for r in rows]))
        assert before_max > 1.0, "this test needs calibrations worth refining"
        assert after_max < before_max / 3
        assert after_max < 0.6

    def test_it_cannot_rescue_a_badly_wrong_calibration(self, scene):
        """Eight pixels of jitter across eight points is often beyond saving,
        and the honest requirement is not that refinement fixes it but that
        nothing downstream claims it did.

        Measured over twenty click sets: refinement roughly halves the typical
        error and still leaves most of them outside the usable bar. Not one
        calibration left more than three metres wrong was reported usable.
        """
        pitch, matrix, _, segments = scene
        rows = self.sweep(pitch, matrix, segments, 8.0)

        still_wrong = [r for r in rows if r["after"][1] > 3.0]
        assert still_wrong, "this test needs some hopeless cases in it"
        assert not any(r["usable"] for r in still_wrong), (
            "a calibration metres out was reported as usable"
        )

    def test_a_usable_verdict_bounds_the_error_but_loosely(self, scene):
        """What a tick from `LineFit.is_usable` is actually worth.

        `CalibrationError.is_usable` promises a worst landmark inside 1.5m.
        This check does not deliver that, and must not be read as though it
        does. Across sixty refinements -- twenty click sets at each of three
        jitter levels -- every calibration the line fit passed was within
        2.93m at its worst landmark: far better than the five metres and more
        those clicks started at, and still around twice the bar the
        click-based check sets.

        The gap is the blind spot the tail test measures, seen from the other
        side. Paint pins a calibration across the lines and lets it slide
        along them, so a fit can be excellent everywhere it is measured and
        metres out where it is not. Read a line-fit tick as *this calibration
        is on the pitch*, never as *this calibration is accurate* -- the
        reprojection and leave-one-out errors are still the numbers a coach
        should be shown.
        """
        pitch, matrix, _, segments = scene
        rows = [row for jitter in (3.0, 5.0, 8.0)
                for row in self.sweep(pitch, matrix, segments, jitter)]

        passed = [r for r in rows if r["usable"]]
        assert len(passed) > 20, "this test needs a real sample of verdicts"
        assert max(r["after"][1] for r in passed) < 3.5

    def test_the_tail_is_what_notices_a_slide_along_the_lines(self, scene):
        """The most important result in this file.

        This click set refines to a median of 0.08m against the paint -- a
        better score than the *true* calibration manages, because rasterised
        paint has width and the true answer has none -- while its worst
        landmark sits seventeen metres from where it belongs. The middle of the
        distribution is exactly what a line cannot see: everything slid along
        the touchlines together, and every sample stayed on its own line.

        The 90th percentile is the only line-based number that objects, and it
        objects at 3.13m against a bar of 1.5m. Had `LineFit.is_usable` stayed
        median-and-coverage, as it was when this module was first written, this
        calibration would have been published with a tick beside it.
        """
        pitch, matrix, _, segments = scene
        truth_fit = fit_to_lines(truth_calibration(pitch, matrix), segments)

        rough = Calibration.fit(
            rough_clicks(pitch, matrix, 8.0, 7), pitch, (FRAME_W, FRAME_H)
        )
        refined, _, after = refine(rough, segments)
        _, after_max = landmark_error(refined, pitch, matrix)

        assert after.median_m <= truth_fit.median_m
        assert after.coverage > 0.9
        assert after_max > 10.0, "the calibration really is metres wrong"
        assert after.p90_m > 1.5
        assert not after.is_usable

    def test_it_reports_the_fit_it_started_from(self, scene):
        """`before` is why refinement returns three things: a coach needs to
        see that something changed, not only that the result is good."""
        pitch, matrix, _, segments = scene
        rough = Calibration.fit(
            rough_clicks(pitch, matrix, 3.0, 0), pitch, (FRAME_W, FRAME_H)
        )
        refined, before, after = refine(rough, segments)

        assert refined is not rough
        assert before.median_m == pytest.approx(
            fit_to_lines(rough, segments).median_m
        )
        assert after.median_m < before.median_m

    def test_it_keeps_the_clicks_that_made_it(self, scene):
        """The refined calibration still has to report the human's own
        reprojection error, or the picker loses the numbers it shows."""
        pitch, matrix, _, segments = scene
        clicks = rough_clicks(pitch, matrix, 3.0, 0)
        refined, _, _ = refine(
            Calibration.fit(clicks, pitch, (FRAME_W, FRAME_H)), segments
        )
        assert [c.landmark for c in refined.correspondences] == [
            c.landmark for c in clicks
        ]
        assert refined.image_size == (FRAME_W, FRAME_H)

    def test_a_frame_with_no_lines_leaves_the_calibration_alone(self, scene):
        """Returned unchanged, by identity -- not refitted to nothing.

        This is the guard that makes refinement safe to run automatically. A
        frame grabbed during a throw-in, or from a camera pointed at the wrong
        half, has to cost the coach nothing.
        """
        pitch, matrix, _, _ = scene
        original = truth_calibration(pitch, matrix)
        refined, before, after = refine(original, [])

        assert refined is original
        assert before.sampled == 0
        assert after is before


class TestImprovementRule:
    """`_is_improvement` decides whether a coach's clicks get overwritten.

    Tested directly, underscore and all, because reaching each clause through
    `refine` means constructing a frame that fails exactly that clause -- which
    is how the tail-versus-middle rule came to be missing in the first place.
    """

    def test_a_better_fit_is_taken(self):
        assert _is_improvement(LineFit(0.80, 2.00, 500, 500),
                               LineFit(0.20, 0.60, 500, 500))

    def test_the_tail_may_not_be_traded_for_the_middle(self):
        """Halving the median while doubling the 90th percentile describes a
        calibration that has slid, not one that has settled."""
        assert not _is_improvement(LineFit(0.80, 2.00, 500, 500),
                                   LineFit(0.20, 4.00, 500, 500))

    def test_coverage_may_wobble_but_not_collapse(self):
        before = LineFit(0.80, 2.00, 500, 500)
        assert _is_improvement(before, LineFit(0.20, 0.60, 500, 498))
        assert not _is_improvement(before, LineFit(0.20, 0.60, 500, 400))

    def test_no_change_is_not_an_improvement(self):
        same = LineFit(0.40, 1.00, 500, 500)
        assert not _is_improvement(same, same)

    def test_a_fit_that_matched_nothing_is_never_an_improvement(self):
        nothing = LineFit(float("nan"), float("nan"), 500, 0)
        assert not _is_improvement(LineFit(0.40, 1.00, 500, 500), nothing)
        assert not _is_improvement(nothing, nothing)

    def test_a_first_measurement_against_nothing_can_be_accepted(self):
        """Starting from no matches at all there is no median to beat, so the
        comparison is skipped rather than counted as a failure."""
        assert _is_improvement(LineFit(float("nan"), float("nan"), 500, 0),
                               LineFit(0.30, 0.90, 500, 480))


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

class TestDraw:
    def test_the_outline_lands_on_the_paint(self, scene):
        """Drawn through the true calibration, the model has to cover the
        lines already in the frame -- that is what makes the overlay a check a
        human can do by eye."""
        pitch, matrix, frame, _ = scene
        painted = np.count_nonzero(np.all(frame == PAINT_BGR, axis=2))

        overlay = frame.copy()
        draw_pitch_lines(overlay, truth_calibration(pitch, matrix),
                         colour=(0, 0, 255), thickness=5)
        left = np.count_nonzero(np.all(overlay == PAINT_BGR, axis=2))

        assert painted > 1000
        assert left < painted * 0.1, "the drawn model missed the painted lines"

    def test_a_hopeless_homography_does_not_crash_the_drawing(self, scene):
        """A calibration can fold the pitch behind the camera and send
        landmarks to infinity. Drawing it must fail visibly, not raise."""
        pitch, _, frame, _ = scene
        broken = Calibration(
            np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 1e-12]]), pitch
        )
        draw_pitch_lines(frame.copy(), broken)
