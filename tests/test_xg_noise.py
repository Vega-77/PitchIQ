"""How much the xG model moves when the positions it is fed are noisy.

Testing Strategy #6 and the Reality Check both asked for this and neither had
been done: `validate_against_noise` was written, and until 2026-08-02 nothing
had ever run it. The model was trained on hand-verified StatsBomb positions.
Ours come out of a detector and a homography, and are wrong by tens of
centimetres at best.

    Re-measured 2026-08-06 on xg-sandbox/xg_model7.onnx, 400 trials, seed 0,
    across five spots with a mean clean xG of 0.254:

        noise    mean shift   p95     max      as a share of the 0.254 baseline
        0.25 m     0.024     0.060   0.106       9%   /  24%
        0.50 m     0.035     0.084   0.168      14%   /  33%
        1.00 m     0.043     0.101   0.173      17%   /  40%
        2.00 m     0.064     0.201   0.495      25%   /  79%
        4.00 m     0.106     0.344   0.506      42%   / 135%

The honest reading: **half a metre of position error moves a shot's xG by about
0.035 on average, and by 0.084 at the 95th percentile.** Against a baseline of
0.254 that is a 14% swing typically and a 33% swing in the tail. A calibration
inside the 0.5 m band `cv/calibrate.py` treats as good therefore still leaves
per-shot xG meaningfully uncertain — good enough for "that was a decent chance",
not for comparing two shots that differ by 0.1.

The previous table, measured on xg_model6, read roughly twice as large in
absolute terms (0.066 mean and 0.175 p95 at half a metre) — but on a baseline
of 0.472, because that model was exported without its calibration and read
about six times high near goal. **As a share of the quantity being measured the
two tables are almost identical**, 14%/33% against 14%/37%. Which is the point
worth keeping: this measures how far the model moves when its inputs move, and
recalibrating the outputs did not make the model less sensitive to a metre of
position error. The trust bands in assets/report.js are set off the ratios, so
they did not move either.

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
        meaningless — a probability pinned at an end cannot move much.

        The band was 0.2-0.8 and the baseline was 0.472, which should have been
        read as a warning rather than a pass: five ordinary spots, one of them
        30 metres out, averaging a coin flip is not what an xG model does. It
        now averages 0.254.
        """
        assert 0.1 < measure(session, 0.5)['mean_baseline_xg'] < 0.6


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
        moves a typical shot's xG by ~0.035 on a 0.254 baseline. The bounds are
        loose on purpose: this pins the order of magnitude, not the digits, so
        it fails on a real regression rather than on a numpy version.

        A share of the answer, not an absolute figure, is what this test is
        about — and it is why the name still reads the same after the model was
        recalibrated and every number in it halved.
        """
        result = measure(session, 0.5)
        assert 0.02 < result['mean_shift'] < 0.07
        assert result['p95_shift'] < 0.15
        assert result['mean_shift'] / result['mean_baseline_xg'] < 0.25

    def test_the_result_barely_moves_across_seeds(self, session):
        """400 trials is enough that the figures above are the model's, not the
        random number generator's."""
        a = measure(session, 0.5, seed=0)['mean_shift']
        b = measure(session, 0.5, seed=7)['mean_shift']
        assert abs(a - b) < 0.01

    def test_a_bad_calibration_is_worse_than_useless_per_shot(self, session):
        """At 4 m the p95 shift exceeds the baseline xG itself.

        This is the number that says a poorly calibrated run should not publish
        per-shot xG at all — the error bar is wider than the quantity.
        """
        result = measure(session, 4.0)
        assert result['p95_shift'] > result['mean_baseline_xg'] * 0.9
