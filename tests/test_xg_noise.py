"""How much the xG model moves when the positions it is fed are noisy.

Testing Strategy #6 and the Reality Check both asked for this and neither had
been done: `validate_against_noise` was written, and until 2026-08-02 nothing
had ever run it. The model was trained on hand-verified StatsBomb positions.
Ours come out of a detector and a homography, and are wrong by tens of
centimetres at best.

    What it measured, on xg-sandbox/xg_model6.onnx, 400 trials, seed 0,
    across five spots with a mean clean xG of 0.472:

        noise    mean shift   p95     max
        0.25 m     0.059     0.147   0.236
        0.50 m     0.066     0.175   0.236
        1.00 m     0.076     0.204   0.459
        2.00 m     0.096     0.240   0.657
        4.00 m     0.159     0.513   0.676

The honest reading: **half a metre of position error moves a shot's xG by about
0.066 on average, and by 0.17 at the 95th percentile.** Against a baseline of
0.47 that is a 14% swing typically and a 37% swing in the tail. A calibration
inside the 0.5 m band `cv/calibrate.py` treats as good therefore still leaves
per-shot xG meaningfully uncertain — good enough for "that was a decent chance",
not for comparing two shots that differ by 0.1.

Two things it does not say. It measures *sensitivity*, not *calibration*: a
model can shift a lot under noise and still be right on average, which is what
matters for a match total. And the shifts are one shot at a time; summing many
shots averages much of this out, so team xG for a half is far steadier than any
single number in the table above.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_xg_noise.py -q
"""

import pytest

from cv.pitch import Pitch
from cv.xg_bridge import load_session, validate_against_noise

TRIALS = 400


@pytest.fixture(scope='module')
def session():
    pytest.importorskip('onnxruntime')
    return load_session()


def measure(session, noise_m, seed=0):
    return validate_against_noise(
        session, Pitch(), noise_m=noise_m, trials=TRIALS, seed=seed
    )


class TestTheHarnessItself:
    """If these fail the numbers below are measuring the wrong thing."""

    def test_no_noise_moves_nothing(self, session):
        """The obvious check, and the one that catches a jitter applied twice
        or a baseline recomputed per trial."""
        result = measure(session, 0.0)
        assert result['mean_shift'] == 0.0
        assert result['max_shift'] == 0.0

    def test_it_is_deterministic_for_a_seed(self, session):
        assert measure(session, 0.5)['mean_shift'] == measure(session, 0.5)['mean_shift']

    def test_the_spots_it_uses_are_ordinary_chances(self, session):
        """A mean baseline near a tap-in or near zero would make the shifts
        meaningless — a probability pinned at an end cannot move much."""
        assert 0.2 < measure(session, 0.5)['mean_baseline_xg'] < 0.8


class TestSensitivity:
    def test_more_noise_moves_the_answer_more(self, session):
        """Monotonic, which is the one shape the result has to have.

        If a worse calibration did not produce a wider spread of xG, this
        measurement would not be measuring position error at all.
        """
        shifts = [measure(session, n)['mean_shift'] for n in (0.25, 0.5, 1.0, 2.0, 4.0)]
        assert shifts == sorted(shifts)

    def test_a_good_calibration_still_costs_about_a_seventh_of_the_answer(
        self, session
    ):
        """The headline figure, pinned so a model swap cannot quietly change it.

        0.5 m is the error `cv/calibrate.py` accepts as good. Half a metre of it
        moves a typical shot's xG by ~0.066 on a 0.47 baseline. The bounds are
        loose on purpose: this pins the order of magnitude, not the digits, so
        it fails on a real regression rather than on a numpy version.
        """
        result = measure(session, 0.5)
        assert 0.03 < result['mean_shift'] < 0.12
        assert result['p95_shift'] < 0.30

    def test_the_result_barely_moves_across_seeds(self, session):
        """400 trials is enough that the figures above are the model's, not the
        random number generator's."""
        a = measure(session, 0.5, seed=0)['mean_shift']
        b = measure(session, 0.5, seed=7)['mean_shift']
        assert abs(a - b) < 0.02

    def test_a_bad_calibration_is_worse_than_useless_per_shot(self, session):
        """At 4 m the p95 shift exceeds the baseline xG itself.

        This is the number that says a poorly calibrated run should not publish
        per-shot xG at all — the error bar is wider than the quantity.
        """
        result = measure(session, 4.0)
        assert result['p95_shift'] > result['mean_baseline_xg'] * 0.9
