"""How much the xG model moves when the positions it is fed are noisy.

Testing Strategy #6 and the Reality Check both asked for this and neither had
been done: `validate_against_noise` was written, and until 2026-08-02 nothing
had ever run it. The model was trained on hand-verified StatsBomb positions.
Ours come out of a detector and a homography, and are wrong by tens of
centimetres at best.

    Re-measured 2026-08-06 on xg-sandbox/xg_model8.onnx, 400 trials, seed 0,
    across five spots with a mean clean xG of 0.188:

        noise    mean shift   p95     max      as a share of the 0.188 baseline
        0.25 m     0.019     0.065   0.099      10%   /  34%
        0.50 m     0.030     0.095   0.253      16%   /  50%
        1.00 m     0.043     0.168   0.267      23%   /  89%
        2.00 m     0.062     0.242   0.538      33%   / 129%
        4.00 m     0.107     0.380   0.624      57%   / 202%

The honest reading: **half a metre of position error moves a shot's xG by about
0.030 on average, and by 0.095 at the 95th percentile** — half the quantity.
A calibration inside the 0.5 m band `cv/calibrate.py` treats as good therefore
still leaves per-shot xG meaningfully uncertain: good enough for "that was a
decent chance", not for comparing two shots that differ by 0.1. **At one metre
the tail is 89% of the number, and at two it exceeds it.**

This table has now been measured against three models and the trend is worth
recording, because it runs the opposite way to intuition:

    model      what changed                      p95 at 0.5 m, as a share
    xg_model6  (uncalibrated)                             37%
    xg_model7  calibration restored                       33%
    xg_model8  shot_height dropped                        50%

Recalibrating the outputs barely moved the ratios. Removing a feature moved them
a lot, and for a plain reason: with eleven features instead of twelve, distance
and angle carry more of the answer, so moving a player moves the answer further.
**A more honest model is a more position-sensitive one**, and the per-shot
display band in assets/report.js tightened from 1.0 m to 0.5 m to match.

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
        now averages 0.188.
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

    def test_a_good_calibration_still_costs_about_a_sixth_of_the_answer(
        self, session
    ):
        """The headline figure, pinned so a model swap cannot quietly change it.

        0.5 m is the error `cv/calibrate.py` accepts as good. Half a metre of it
        moves a typical shot's xG by ~0.030 on a 0.188 baseline — about a sixth,
        with a tail at half. The bounds are loose on purpose: this pins the
        order of magnitude, not the digits, so it fails on a real regression
        rather than on a numpy version.

        A share of the answer, not an absolute figure, is what this test is
        about, and it is the share that decides where the display bands go.
        """
        result = measure(session, 0.5)
        assert 0.015 < result['mean_shift'] < 0.06
        assert result['p95_shift'] < 0.15
        assert result['mean_shift'] / result['mean_baseline_xg'] < 0.30

    def test_the_result_barely_moves_across_seeds(self, session):
        """400 trials is enough that the figures above are the model's, not the
        random number generator's."""
        a = measure(session, 0.5, seed=0)['mean_shift']
        b = measure(session, 0.5, seed=7)['mean_shift']
        assert abs(a - b) < 0.01

    def test_a_bad_calibration_is_worse_than_useless_per_shot(self, session):
        """By 2 m the p95 shift exceeds the baseline xG itself.

        This is the number that says a loosely calibrated run should not publish
        per-shot xG — the error bar is wider than the quantity. It used to take
        4 m to reach this point; on the 11-feature model it takes 2, which is
        why `XG_PER_SHOT_LIMIT_M` came down to 0.5.
        """
        assert measure(session, 2.0)['p95_shift'] > measure(session, 2.0)['mean_baseline_xg']

    def test_a_good_calibration_is_still_only_good_enough_for_the_total(self, session):
        """Where the two display bands part company.

        At the fit `calibrate/` calls good, a single shot's tail is already half
        the quantity. That is the whole argument for showing a team total on
        runs where no individual figure is worth printing — pinned here so a
        model swap that changes it cannot pass quietly.
        """
        result = measure(session, 0.5)
        assert result['p95_shift'] / result['mean_baseline_xg'] > 0.3
        assert result['p95_shift'] / result['mean_baseline_xg'] < 0.8
