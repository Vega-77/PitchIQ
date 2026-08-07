"""Hard accelerations, and the wobble that decides whether they mean anything.

Acceleration is the second derivative of a tracked position, and each derivative
multiplies the noise: the jitter that fakes 6 m/s of speed between two frames
fakes 180 m/s² between three. So the question these tests answer is not "is the
number close" but "is the number about the player at all".

They are all synthetic, and deliberately so. A player running at exactly 3 m/s
in a straight line makes every burst reported a **false** one, which is the only
way to measure a false-positive rate without ground truth — and ground truth is
the thing this project does not have and will not have before the demo.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_bursts.py -q
"""

from __future__ import annotations

import numpy as np
import pytest

from cv.metrics import (
    ACCEL_SMOOTH_S,
    MAX_ACCEL_NOISE_M,
    PositionSeries,
    movement_stats,
    position_noise_m,
    smooth_positions,
)

FPS = 30.0

# The window the pipeline actually smooths distance over (cv/pipeline.py).
PIPELINE_WINDOW = 9


def track(profile, seconds: float, jitter_m: float = 0.0, seed: int = 0):
    """A player moving along the x axis to a speed profile, plus jitter."""
    n = int(seconds * FPS)
    times = np.arange(n) / FPS
    speeds = np.array([profile(t) for t in times])
    x = np.cumsum(speeds) / FPS

    positions = np.column_stack([x, np.zeros(n)])
    if jitter_m:
        rng = np.random.default_rng(seed)
        positions = positions + rng.normal(0, jitter_m, positions.shape)
    return PositionSeries(1, times, positions)


def jogging(_t):
    """Constant 3 m/s. Every burst found here is a false one."""
    return 3.0


def four_bursts(t):
    """Four genuine 3 m/s² accelerations out of a 2 m/s jog, in 60 seconds."""
    for start in (5.0, 20.0, 35.0, 50.0):
        if start <= t < start + 1.5:
            return 2.0 + 3.0 * (t - start)
        if start + 1.5 <= t < start + 4.0:
            return 6.5
    return 2.0


def measure(series, **kwargs):
    noise = position_noise_m(series)
    return movement_stats(
        smooth_positions(series, PIPELINE_WINDOW), noise_m=noise, **kwargs
    )


class TestItDoesNotInventBursts:
    """The failure that would make this metric worthless rather than rough."""

    @pytest.mark.parametrize('jitter_m', [0.0, 0.05, 0.10, 0.20])
    def test_a_player_at_a_constant_speed_never_accelerates(self, jitter_m):
        # Every burst reported here is fabricated from the jitter. Before the
        # burst pass got its own one-second smoothing this returned 40 of them
        # at 0.20m, against 37 on a track with four real ones in it — more
        # phantom accelerations in a minute than a player makes in a match.
        for seed in range(5):
            stats = measure(track(jogging, 60, jitter_m, seed))
            assert stats.accelerations == 0, f'{jitter_m}m, seed {seed}'

    def test_a_player_standing_still_never_accelerates(self):
        for seed in range(5):
            stats = measure(track(lambda _t: 0.0, 60, 0.20, seed))
            assert stats.accelerations == 0


class TestItFindsRealOnes:
    @pytest.mark.parametrize('jitter_m', [0.0, 0.05, 0.10])
    def test_four_real_bursts_are_found_exactly(self, jitter_m):
        # Eight rather than four: each burst is a 1.5s acceleration and the
        # window is 1s, so a long burst counts once per window it fills. Two
        # seconds of hard running is two seconds of hard running.
        #
        # Exactly eight on all forty seeds tried, at each of these noise
        # levels — not "close to", exactly.
        for seed in range(8):
            stats = measure(track(four_bursts, 60, jitter_m, seed))
            assert stats.accelerations == 8, f'{jitter_m}m, seed {seed}'

    def test_heavier_noise_loses_bursts_rather_than_inventing_them(self):
        # At 0.20m the count drops to 6-8 (mean 7.8 over forty seeds): noise
        # can push a real burst below the bar, and this is the shortfall.
        #
        # It never goes the other way. That is not luck — it is what
        # TestItDoesNotInventBursts measures separately, on a track with no
        # bursts in it at all. An undercount is a number a coach can still use;
        # an overcount is a number about the camera.
        counts = [
            measure(track(four_bursts, 60, 0.20, seed)).accelerations
            for seed in range(20)
        ]
        assert min(counts) >= 6
        assert max(counts) <= 8

    def test_the_hardest_burst_is_reported_in_ms2(self):
        stats = measure(track(four_bursts, 60))
        assert stats.top_acceleration_ms2 == pytest.approx(2.8, abs=0.3)


class TestWhatItCosts:
    """The bias, stated as a number rather than as a warning."""

    def test_a_burst_shorter_than_the_window_reads_low(self):
        # A true 10 m/s² over 0.4s, which is a real thing a footballer does.
        # Averaged across a whole second against the stillness either side it
        # comes out at about a third of that. Understating is the direction to
        # be wrong in; the other one invents bursts nobody ran.
        def explosive(t):
            if t < 10.0:
                return 1.0
            if t < 10.4:
                return 1.0 + 10.0 * (t - 10.0)
            return 5.0

        stats = measure(track(explosive, 30))
        assert stats.top_acceleration_ms2 == pytest.approx(3.6, abs=0.3)
        # Still found, which is what matters most.
        assert stats.accelerations >= 1

    def test_the_window_is_a_second_and_the_shortfall_follows_from_it(self):
        # Guards the constant against being nudged without the note above being
        # re-measured: every figure in this file was taken at one second.
        assert ACCEL_SMOOTH_S == 1.0


class TestTheNoiseGate:
    def test_bursts_are_withheld_rather_than_reported_from_jitter(self):
        # Past the ceiling the count is a count of the wobble, and it would sit
        # on a player's card looking exactly like a count of their runs.
        stats = measure(track(four_bursts, 60, jitter_m=0.6, seed=1))
        assert stats.accelerations is None
        assert stats.top_acceleration_ms2 is None
        assert stats.position_noise_m > MAX_ACCEL_NOISE_M

    def test_distance_survives_the_gate_even_when_bursts_do_not(self):
        # The gate is on the burst count alone. A noisy track still covered
        # ground, and refusing every figure because one is unreadable would
        # throw away the ones that are.
        stats = measure(track(four_bursts, 60, jitter_m=0.6, seed=1))
        assert stats.distance_m > 0
        assert stats.top_speed_ms > 0

    def test_a_run_that_never_measured_the_noise_still_reports_bursts(self):
        # No estimate given means the caller has said it does not know. Refusing
        # on an unknown would delete the figure from every run that predates the
        # measurement.
        series = smooth_positions(track(four_bursts, 60), PIPELINE_WINDOW)
        assert movement_stats(series).accelerations == 8


class TestTooShortToAnswer:
    def test_a_track_shorter_than_a_window_answers_none_not_zero(self):
        # A player watched for half a second did not fail to accelerate.
        stats = measure(track(four_bursts, 0.5))
        assert stats.accelerations is None
        assert stats.top_acceleration_ms2 is None

    def test_and_a_long_enough_one_can_still_answer_zero(self):
        stats = measure(track(jogging, 60))
        assert stats.accelerations == 0


class TestMeasuringTheWobble:
    """The first thing this pipeline has measured about its own tracking."""

    @pytest.mark.parametrize('jitter_m', [0.02, 0.05, 0.10, 0.20])
    def test_it_recovers_the_noise_it_was_given(self, jitter_m):
        # Within 15% at every level, on a track with four real accelerations
        # mixed in — which is the point of taking a median rather than a mean.
        estimates = [
            position_noise_m(track(four_bursts, 60, jitter_m, seed))
            for seed in range(5)
        ]
        assert np.mean(estimates) == pytest.approx(jitter_m, rel=0.15)

    def test_a_clean_track_reports_almost_none(self):
        assert position_noise_m(track(four_bursts, 60)) < 0.005

    def test_it_must_be_given_the_raw_series(self):
        # A moving average correlates neighbouring samples, which is exactly
        # what this measures the absence of. Run on a smoothed series it reports
        # a fraction of the truth, and the fraction would look like good news.
        raw = track(four_bursts, 60, jitter_m=0.20, seed=3)
        smoothed = smooth_positions(raw, PIPELINE_WINDOW)

        assert position_noise_m(raw) == pytest.approx(0.20, rel=0.15)
        assert position_noise_m(smoothed) < position_noise_m(raw) / 2

    def test_too_few_samples_is_no_answer(self):
        assert position_noise_m(track(jogging, 0.05)) is None


class TestWhatTheWobbleCosts:
    """Phantom distance, measured. Nothing here changes; it is a record."""

    def test_a_motionless_player_is_credited_with_running(self):
        # At the pipeline's smoothing window, jitter of size sigma gives a
        # player who never moved roughly 353*sigma metres per minute:
        #
        #     0.05m ->  18m      0.10m ->  35m      0.20m ->  71m
        #
        # That is the size of the correction nobody has been able to make, and
        # the reason `position_noise_m` is now in the quality block. It is also
        # what PHANTOM_M_PER_MINUTE in assets/report.js quotes to a coach.
        for jitter_m, expected in ((0.05, 17.6), (0.10, 35.3), (0.20, 70.6)):
            phantom = np.mean([
                measure(track(lambda _t: 0.0, 60, jitter_m, seed)).distance_m
                for seed in range(5)
            ])
            assert phantom == pytest.approx(expected, rel=0.2), f'{jitter_m}m'

    def test_a_real_distance_is_inflated_by_the_same_wobble(self):
        # A true 180m jog. The error is one-sided — noise only ever adds
        # distance, never removes it — so this is a bias and not a spread. Far
        # smaller than the standing-still case above, because a real step
        # dominates the wobble added to it: +4.8% at 0.20m against a phantom
        # 71m from nothing. Distance totals are safer than they look; a
        # substitute who spent the half warming up is not.
        for jitter_m, expected in ((0.0, 179.7), (0.10, 181.9), (0.20, 188.7)):
            measured = np.mean([
                measure(track(jogging, 60, jitter_m, seed)).distance_m
                for seed in range(5)
            ])
            assert measured == pytest.approx(expected, rel=0.08), f'{jitter_m}m'
