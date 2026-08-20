"""Reading a lens off the paint, and knowing when the frame cannot say.

The roadmap closed this item on 2026-08-12 for a stated reason: correcting for
a lens needs a per-camera calibration nobody could produce before there was a
camera, so the code would have had no caller. What reopened it is `cv/lines.py`
-- painted lines are straight by the Laws of the Game, so a painted line is
straight *in the image* only if the lens is rectilinear. That makes the single
calibration frame a coach already grabs into a lens target, and it makes
`calibrate --frame` a real caller.

Two things this file is really about.

**It has to be worth doing.** `TestWorthDoing` is the headline: perfect clicks
on a barrel-distorted frame, fitted with and without the recovered lens, scored
in metres on a grid of the real pitch. Nothing here is measured on the paint
that produced the coefficient.

**It has to know when to shut up.** Far more frames cannot pin a lens than can,
and a coefficient fitted to a frame that had nothing to say moves every
landmark for no reason. `TestKnowingWhenToShutUp` is the half that keeps this
honest, and `test_the_gate_lands_on_the_usability_bar` is the one that pins
where the line sits: measured across the sweep, every frame this refuses would
have cost under a quarter of a metre, and every frame it accepts would have
cost more than half -- which is exactly the bar `CalibrationError.is_usable`
draws.

The synthetic camera is imported from `tests/test_lines.py` rather than built
again here, for the reason that file already gives: two renderers drifting
apart would make one of them quietly stop testing anything.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_distortion.py -q
"""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from cv.calibration import Calibration, Correspondence
from cv.distortion import (
    Chain,
    DistortionModel,
    MAX_K1,
    estimate,
    lens_for_frame,
    trace_chains,
)
from cv.lines import pitch_line_segments
from cv.pitch import Pitch
from test_lines import FRAME_H, FRAME_W, GRASS_BGR, PAINT_BGR, camera, to_px

# Every drawn line is subdivided this many times before the lens is applied.
# A distorted straight line is a curve, and drawing it as one long segment
# would put perfectly straight paint in a frame that claims to be distorted --
# the tests would then measure the renderer's shortcut rather than the lens.
SUB = 40

# A grid of real pitch positions to score a calibration on. Deliberately not
# the eight landmarks that get clicked: a homography fitted through those eight
# is exact at those eight almost by construction, and the error a lens causes
# lives in between them.
GRID = [(x, y) for x in np.linspace(2, 103, 9) for y in np.linspace(2, 66, 7)]

CLICKED = [
    "corner_bottom_left", "corner_bottom_right",
    "corner_top_right", "corner_top_left",
    "halfway_bottom", "halfway_top",
    "pen_left_bottom_corner", "pen_right_top_corner",
]


@pytest.fixture(scope="module")
def scene():
    pitch = Pitch()
    return pitch, camera(pitch)


def lens(k1: float) -> DistortionModel:
    return DistortionModel.for_image((FRAME_W, FRAME_H), k1)


def render_through(pitch, matrix, k1: float) -> np.ndarray:
    """The frame this camera would produce behind a lens of this strength."""
    model = lens(k1)
    frame = np.full((FRAME_H, FRAME_W, 3), 30, dtype=np.uint8)

    corners = [(0, 0), (pitch.length_m, 0),
               (pitch.length_m, pitch.width_m), (0, pitch.width_m)]
    ring = []
    for i in range(4):
        ax, ay = corners[i]
        bx, by = corners[(i + 1) % 4]
        for t in np.linspace(0, 1, SUB, endpoint=False):
            ring.append(to_px(matrix, ax + (bx - ax) * t, ay + (by - ay) * t))
    ring = model.distort(np.array(ring))
    cv2.fillPoly(frame, [np.rint(ring).astype(np.int32)], GRASS_BGR)

    for seg in pitch_line_segments(pitch):
        pts = [to_px(matrix, x, y) for x, y in seg.sample(seg.length / SUB)]
        pts = model.distort(np.array(pts))
        cv2.polylines(frame, [np.rint(pts).astype(np.int32)], False,
                      PAINT_BGR, 3, cv2.LINE_AA)
    return frame


def perfect_clicks(pitch, matrix, k1):
    """The landmarks, clicked without a pixel of error -- on the frame the
    coach is actually looking at, which is the distorted one."""
    model = lens(k1)
    out = []
    for name in CLICKED:
        px = model.distort(np.array([to_px(matrix, *pitch.landmark(name))]))[0]
        out.append(Correspondence(name, (float(px[0]), float(px[1]))))
    return out


def grid_error_m(calibration, pitch, matrix, k1):
    """Mean and worst metres over `GRID`, through the real lens.

    The chain is the honest one: put the point where the distorted camera would
    show it, then ask the calibration what it thinks that pixel means.
    """
    truth = lens(k1)
    errors = []
    for x_m, y_m in GRID:
        px = truth.distort(np.array([to_px(matrix, x_m, y_m)]))[0]
        got = calibration.to_pitch(float(px[0]), float(px[1]))
        errors.append(math.hypot(got[0] - x_m, got[1] - y_m))
    return float(np.mean(errors)), float(np.max(errors))


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------

class TestTheModel:
    def test_the_two_directions_are_inverses(self):
        """The reason the division model was chosen over Brown's polynomial.

        Brown maps ideal to observed and needs iteration to go back, and back
        is the direction this repo needs -- pixels inward to metres. Here both
        directions are closed form, so this should hold to machine precision
        rather than to a tolerance.
        """
        points = np.array([
            [0.0, 0.0], [FRAME_W, FRAME_H], [FRAME_W / 2, FRAME_H / 2],
            [10.0, 700.0], [1270.0, 12.0], [640.0, 20.0], [3.0, 360.0],
        ])
        for k1 in (-0.25, -0.1, -0.03, 0.05, 0.2):
            model = lens(k1)
            back = model.undistort(model.distort(points))
            assert np.allclose(back, points, atol=1e-9), f"k1={k1}"
            forth = model.distort(model.undistort(points))
            assert np.allclose(forth, points, atol=1e-9), f"k1={k1}"

    def test_a_factor_of_r_is_easy_to_drop_and_this_is_where_it_shows(self):
        """The bug that cost this module its first afternoon.

        The quadratic solves for the distorted *radius*, but what scales the
        offset vector is the ratio of the two radii, so the inverse divides by
        the ideal radius twice. Dropping one factor is coincidentally correct
        at exactly r = 1 -- one point out near a corner -- which is why the
        first version looked plausible and was wrong everywhere else.

        So this checks the shape of the answer at a *small* radius, where the
        two versions differ by the most: close to the centre a lens barely
        moves anything, whatever its strength.
        """
        model = lens(-0.25)
        near = np.array([[FRAME_W / 2 + 4.0, FRAME_H / 2]])
        moved = float(np.linalg.norm(model.distort(near) - near))
        assert moved < 0.05, (
            "a point four pixels from the centre should barely move; it moved "
            f"{moved:.3f}px"
        )

    def test_the_centre_does_not_move(self):
        for k1 in (-0.3, -0.05, 0.05, 0.3):
            model = lens(k1)
            centre = np.array([[FRAME_W / 2, FRAME_H / 2]])
            assert np.allclose(model.distort(centre), centre)
            assert np.allclose(model.undistort(centre), centre)

    def test_negative_is_barrel(self):
        """Sign convention, matching Brown's, and worth a test because getting
        it backwards produces a correction that doubles the error instead of
        removing it -- while still looking like a working pipeline."""
        corner = np.array([[0.0, 0.0]])
        centre = np.array([FRAME_W / 2, FRAME_H / 2])
        straight = float(np.linalg.norm(corner - centre))

        barrel = float(np.linalg.norm(lens(-0.1).distort(corner) - centre))
        pincushion = float(np.linalg.norm(lens(+0.1).distort(corner) - centre))
        assert barrel < straight, "barrel should pull the corners in"
        assert pincushion > straight, "pincushion should push them out"

    def test_zero_is_the_identity_and_costs_nothing(self):
        model = lens(0.0)
        assert model.is_identity
        points = np.array([[1.0, 2.0], [900.0, 30.0]])
        assert np.array_equal(model.distort(points), points)
        assert np.array_equal(model.undistort(points), points)

    def test_it_survives_a_round_trip_through_json(self):
        model = lens(-0.037)
        back = DistortionModel.from_json(model.to_json())
        assert back == model

    def test_no_lens_reads_back_as_no_lens(self):
        """A calibration saved before this existed has no `lens` key at all,
        and must load as one without a lens rather than one with k1 = 0 -- the
        difference matters to anything that asks whether the lens was ever
        measured."""
        assert DistortionModel.from_json(None) is None
        assert DistortionModel.from_json({}) is None


class TestChain:
    def test_a_straight_run_has_no_residual(self):
        points = np.array([[x, 3.0 * x + 10.0] for x in np.linspace(0, 500, 60)])
        assert Chain(points).rms() < 1e-9

    def test_a_vertical_run_is_not_a_special_case(self):
        """Fitted by total least squares through an SVD rather than by least
        squares on y, which has no answer for a vertical line -- and a goal
        line is vertical in plenty of frames."""
        points = np.array([[100.0, y] for y in np.linspace(0, 500, 60)])
        assert Chain(points).rms() < 1e-9

    def test_bow_is_the_sagitta_and_notices_a_curve(self):
        straight = np.array([[x, 0.0] for x in np.linspace(-100, 100, 50)])
        curved = np.array([[x, x * x / 500.0] for x in np.linspace(-100, 100, 50)])
        assert Chain(straight).bow_px() < 1e-9
        assert Chain(curved).bow_px() > 5.0


# ---------------------------------------------------------------------------
# Following the paint
# ---------------------------------------------------------------------------

class TestTracing:
    def test_it_follows_a_touchline_the_whole_way(self, scene):
        """The one measurement the whole method rests on.

        Hough reports *chords*, and the more a line is bent the shorter its
        chords get -- which is exactly backwards from what is wanted, since the
        most bent line is the most informative one. The tracer exists to turn a
        short seed back into the run of paint it came from.
        """
        pitch, matrix = scene
        chains = trace_chains(render_through(pitch, matrix, 0.0))
        assert max(c.span_px for c in chains) > 900, (
            "the near touchline is about a thousand pixels of paint and "
            "should be traced as one run"
        )

    def test_a_bent_line_traces_out_far_longer_than_its_seed(self, scene):
        """At k1 = -0.2 a 235px Hough seed traced out to 894px of real paint.
        That is why the seed floor has to sit well below the chain floor."""
        pitch, matrix = scene
        chains = trace_chains(render_through(pitch, matrix, -0.2))
        assert max(c.span_px for c in chains) > 700

    def test_a_junction_does_not_derail_a_trace(self, scene):
        """The longest Hough segment on this pitch is the near touchline, and
        its midpoint sits almost exactly where the halfway line meets it. A
        perpendicular slice there runs the full height of the halfway line, so
        centring on it lands seven pixels off the touchline and both walks die
        on the first step -- losing the two longest lines in the frame.

        Pinned because it is invisible: the fit still returns a number, it is
        just a number measured on the short lines.
        """
        pitch, matrix = scene
        chains = trace_chains(render_through(pitch, matrix, 0.0))
        long_runs = [c for c in chains if c.span_px > 600]
        assert len(long_runs) >= 2, (
            "both touchlines cross the halfway line and both should survive it"
        )

    def test_grass_with_no_paint_traces_nothing(self, scene):
        pitch, matrix = scene
        frame = np.full((FRAME_H, FRAME_W, 3), 30, dtype=np.uint8)
        cv2.rectangle(frame, (100, 100), (1100, 600), GRASS_BGR, -1)
        assert trace_chains(frame) == []


# ---------------------------------------------------------------------------
# What it is worth
# ---------------------------------------------------------------------------

class TestWorthDoing:
    """The headline. Everything else here is in service of this number."""

    @pytest.mark.parametrize("k1", [-0.03, -0.05, -0.1, -0.2, 0.05])
    def test_a_perfect_click_set_goes_from_unusable_to_usable(self, scene, k1):
        """Measured with every landmark clicked *exactly* right.

        That is the point worth holding on to: this is not a click-accuracy
        problem and no amount of care with the picker touches it. A wide lens
        does not degrade a calibration, it disqualifies one -- and the coach
        gets no hint of it beyond a reprojection error they cannot explain.
        """
        pitch, matrix = scene
        frame = render_through(pitch, matrix, k1)
        clicks = perfect_clicks(pitch, matrix, k1)

        naive = Calibration.fit(clicks, pitch, (FRAME_W, FRAME_H))
        _, worst_before = grid_error_m(naive, pitch, matrix, k1)
        assert worst_before > 0.5, (
            f"k1={k1} should already be past the usability bar without a lens; "
            f"worst was {worst_before:.2f}m"
        )

        model, fit = lens_for_frame(frame)
        assert model is not None, f"k1={k1} should be measurable: {fit.summary()}"

        undistorted = [
            Correspondence(c.landmark,
                           tuple(model.undistort(np.array([c.pixel]))[0]))
            for c in clicks
        ]
        fixed = Calibration.fit(undistorted, pitch, (FRAME_W, FRAME_H))

        errors = []
        for x_m, y_m in GRID:
            px = lens(k1).distort(np.array([to_px(matrix, x_m, y_m)]))[0]
            ideal = model.undistort(np.array([px]))[0]
            got = fixed.to_pitch(float(ideal[0]), float(ideal[1]))
            errors.append(math.hypot(got[0] - x_m, got[1] - y_m))
        worst_after = float(np.max(errors))

        assert worst_after < 0.5, (
            f"k1={k1}: {worst_before:.2f}m -> {worst_after:.2f}m, still past "
            "the bar"
        )
        assert worst_after < worst_before / 3.0

    @pytest.mark.parametrize("k1", [-0.2, -0.1, -0.05, -0.03, 0.05, 0.1])
    def test_it_recovers_the_coefficient_it_was_given(self, scene, k1):
        """Tighter than the metres actually need, because a drift here is the
        first sign of the tracer going wrong and it is much easier to read than
        a change in the fifth landmark of a grid."""
        pitch, matrix = scene
        fit = estimate(render_through(pitch, matrix, k1))
        assert abs(fit.k1 - k1) < max(0.005, 0.1 * abs(k1)), fit.summary()


# ---------------------------------------------------------------------------
# What it is worth *not* doing
# ---------------------------------------------------------------------------

class TestKnowingWhenToShutUp:
    def test_a_rectilinear_frame_is_refused(self, scene):
        """The most important refusal, and the most common frame.

        A phone on a tripod is very close to rectilinear, and the fit on such a
        frame still returns a coefficient -- it is a least-squares problem and
        one always exists. What makes it safe is that the correction buys
        nothing measurable, so `gain` turns it down.
        """
        pitch, matrix = scene
        model, fit = lens_for_frame(render_through(pitch, matrix, 0.0))
        assert model is None, fit.summary()
        assert abs(fit.k1) < 0.01, "and the number it declined was tiny anyway"

    def test_the_gate_lands_on_the_usability_bar(self, scene):
        """Where the line sits, and why it sits there.

        Swept across the coefficient, the two answers this module can give line
        up with the only threshold that matters. Every frame it refuses would
        have cost less than a quarter of a metre at its worst landmark, which
        is comfortably inside what `CalibrationError.is_usable` allows; every
        frame it accepts would have cost more than half a metre, which is not.

        That was measured, not chosen -- the constants were set from the
        tracer's own noise floor -- so it is pinned here. A change that starts
        applying a lens to frames that did not need one fails on the first
        half; a change that starts refusing frames that did fails on the
        second.
        """
        pitch, matrix = scene
        for k1 in (0.0, -0.015, -0.02, -0.025, -0.03, -0.05):
            frame = render_through(pitch, matrix, k1)
            model, fit = lens_for_frame(frame)
            clicks = perfect_clicks(pitch, matrix, k1)
            naive = Calibration.fit(clicks, pitch, (FRAME_W, FRAME_H))
            _, worst = grid_error_m(naive, pitch, matrix, k1)

            if model is None:
                assert worst < 0.5, (
                    f"k1={k1} was refused but cost {worst:.2f}m -- the gate is "
                    f"too strict: {fit.summary()}"
                )
            else:
                assert worst > 0.5, (
                    f"k1={k1} was corrected but only cost {worst:.2f}m -- the "
                    f"gate is too loose: {fit.summary()}"
                )

    def test_a_frame_with_nothing_in_it_answers_rather_than_raises(self):
        """`--frame` is optional and its failure must never cost a coach the
        calibration. A dark frame, a stoppage, a camera pointed at the car
        park: all of them come through here."""
        blank = np.full((FRAME_H, FRAME_W, 3), 30, dtype=np.uint8)
        model, fit = lens_for_frame(blank)
        assert model is None
        assert fit.chains == 0
        assert "no runs of paint" in fit.summary()

    def test_too_few_runs_is_not_a_measurement(self, scene):
        """One line cannot distinguish a bent lens from a bent line. Three is
        already thin, and it is the floor rather than the target."""
        pitch, matrix = scene
        chains = trace_chains(render_through(pitch, matrix, -0.1))
        fit = estimate(chains=chains[:2], image_size=(FRAME_W, FRAME_H))
        assert not fit.confident

    def test_an_arc_traced_as_a_line_does_not_carry_the_fit(self, scene):
        """The centre circle is real paint that is really curved, and nothing
        about it is a lens. It wants a coefficient no straight line wants, so
        dropping the straight runs and leaving it must not produce a confident
        answer.
        """
        pitch, matrix = scene
        frame = render_through(pitch, matrix, 0.0)
        chains = trace_chains(frame)
        bent = sorted(chains, key=lambda c: c.bow_px())[-1]
        fit = estimate(chains=[bent] * 3, image_size=(FRAME_W, FRAME_H))
        assert not fit.confident, (
            f"three copies of one curve is one opinion: {fit.summary()}"
        )

    def test_it_never_returns_a_coefficient_past_its_own_bound(self, scene):
        """Past about 0.5 the division model stops being invertible across the
        frame, so the search bound is load-bearing rather than cosmetic."""
        pitch, matrix = scene
        for k1 in (0.0, -0.1, 0.1):
            fit = estimate(render_through(pitch, matrix, k1))
            assert abs(fit.k1) <= MAX_K1 + 1e-9

    def test_the_summary_says_which_way_it_went(self, scene):
        """A coach reads this line and nothing else, so the verdict has to be
        in it rather than implied by a number."""
        pitch, matrix = scene
        clean = estimate(render_through(pitch, matrix, 0.0)).summary()
        bent = estimate(render_through(pitch, matrix, -0.1)).summary()
        assert "not confident" in clean
        assert "not confident" not in bent and "confident" in bent
