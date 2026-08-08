"""How long to smooth a track for, and what each answer costs.

The window distance is read off was nine frames, written as a bare number in
the pipeline, and fitted to nothing. It is the single parameter every figure in
metres rests on: too short and a still player is credited with kilometres of
jitter, too long and every turn has its corner cut off.

These tests are the fit. They are synthetic on purpose — the ground truth is
constructed, so the error is known exactly, which is not something any amount
of real footage would give without a second measuring system alongside it.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_smoothing.py -q
"""

from __future__ import annotations

import numpy as np
import pytest

from cv.metrics import (
    DEFAULT_SMOOTH_S,
    MAX_ACCEL_NOISE_M,
    PositionSeries,
    movement_stats,
    phantom_m_per_minute,
    position_noise_m,
    smooth_for_noise,
    smooth_positions,
    smoothing_window_s,
)

FPS = 30.0

# The hardest a person turns. A footballer in boots on grass produces about
# this much sideways, which is what forces a fast turn to be a wide one.
MAX_LATERAL_MS2 = 4.5


def series(points) -> PositionSeries:
    points = np.asarray(points, dtype=float)
    return PositionSeries(1, np.arange(len(points)) / FPS, points)


def length(points) -> float:
    """The true path length, which is what every figure here is measured against."""
    points = np.asarray(points, dtype=float)
    return float(np.sum(np.linalg.norm(np.diff(points, axis=0), axis=1)))


def straight(speed_ms: float, seconds: float):
    n = int(seconds * FPS)
    return np.column_stack([np.zeros(n), np.arange(n) / FPS * speed_ms])


def arc(speed_ms: float, turn_rad: float, lateral_ms2: float = MAX_LATERAL_MS2):
    """A turn at the tightest radius this speed allows."""
    radius = speed_ms ** 2 / lateral_ms2
    n = max(3, int(round((turn_rad * radius / speed_ms) * FPS)))
    a = np.linspace(0, turn_rad, n)
    return np.column_stack([radius * np.cos(a), radius * np.sin(a)])


def turn_with_runway(speed_ms: float, turn_rad: float, runway_s: float = 3.0):
    """The same turn with straight running either side of it.

    Needed to measure the turn on its own: without a runway the arc is shorter
    than the window at low speeds, and the interior — the part not touched by
    the ends — is empty. What is being measured here is the corner, so the ends
    have to be somewhere else.
    """
    turn = arc(speed_ms, turn_rad)
    step = speed_ms / FPS
    n = int(runway_s * FPS)

    into = turn[1] - turn[0]
    into = into / np.linalg.norm(into)
    lead = turn[0] - np.outer(np.arange(n, 0, -1) * step, into)

    out = turn[-1] - turn[-2]
    out = out / np.linalg.norm(out)
    tail = turn[-1] + np.outer(np.arange(1, n + 1) * step, out)

    return np.vstack([lead, turn, tail])


def match_minute(seed: int, minutes: float = 1.0, turns_per_min: float = 8.0):
    """A minute of football as ground truth: gears, turns, and legal physics.

    Speeds are drawn from the coarse time-share match-play spends standing,
    walking, jogging, running and sprinting. Direction changes arrive at a
    stated rate and are held to `MAX_LATERAL_MS2`, so the generator cannot
    produce a pivot no player could make — which would have made a long window
    look worse than it is.
    """
    rng = np.random.default_rng(seed)
    n = int(minutes * 60 * FPS)
    dt = 1.0 / FPS
    gears = np.array([0.5, 2.0, 4.0, 6.0, 8.0])
    weights = np.array([0.15, 0.40, 0.28, 0.13, 0.04])

    pos = np.zeros((n, 2))
    heading = rng.uniform(0, 2 * np.pi)
    speed, target = 2.0, 2.0
    turning, turn_rate = 0.0, 0.0
    p = np.array([50.0, 34.0])
    hazard = turns_per_min / (60 * FPS)

    for i in range(n):
        if rng.random() < 1.0 / (2.0 * FPS):
            target = rng.choice(gears, p=weights)
        speed = max(0.0, speed + np.clip(target - speed, -3.0 * dt, 3.0 * dt))

        if turning <= 0 and rng.random() < hazard:
            angle = rng.uniform(np.pi / 4, np.pi) * rng.choice([-1, 1])
            max_rate = MAX_LATERAL_MS2 / max(speed, 0.5)
            turning = abs(angle) / max_rate
            turn_rate = np.sign(angle) * max_rate
        if turning > 0:
            heading += turn_rate * dt
            turning -= dt

        p = p + speed * dt * np.array([np.cos(heading), np.sin(heading)])
        pos[i] = p

    return pos


def jitter(points, sigma: float, seed: int = 0):
    return points + np.random.default_rng(seed).normal(0, sigma, np.shape(points))


class TestWhatALongWindowCosts:
    """The fear the roadmap recorded: that smoothing eats real turns."""

    @pytest.mark.parametrize('speed_ms', [3.0, 5.0, 7.0])
    def test_a_turn_loses_the_same_metres_whatever_the_speed(self, speed_ms):
        # The result that makes a long window affordable. A moving average over
        # an arc pulls the path onto radius R·sin(θ)/θ where θ = vT/2R, so the
        # metres lost to a turn of angle φ are φ·R·(1 - sinc θ) ≈ φ·v²T²/24R.
        # Substituting the tightest legal radius, R = v²/a, cancels the speed
        # entirely: the loss is φ·a·T²/24 and nothing else.
        #
        # A fast player cannot turn tightly, and that is precisely why a fast
        # turn is not the disaster it sounds like.
        #
        # The law over-predicts at 3 m/s (0.23m against 0.29m) and it errs in
        # the safe direction: that turn takes 1.05s, barely longer than the
        # window, so the straight running either side is averaged in and pulls
        # the corner back out. A turn shorter than the window loses less than
        # an endless arc would, not more.
        window = int(1.0 * FPS)
        expected = (np.pi / 2) * MAX_LATERAL_MS2 * 1.0 ** 2 / 24

        path = turn_with_runway(speed_ms, np.pi / 2)
        smoothed = smooth_positions(series(path), window).positions_m
        pad = window // 2
        lost = length(path[pad:-pad]) - length(smoothed[pad:-pad])

        assert lost == pytest.approx(expected, rel=0.30), f'{speed_ms} m/s'
        assert lost <= expected * 1.1, f'{speed_ms} m/s: worse than the law'

    def test_and_it_is_a_third_of_a_metre_at_a_full_second(self):
        # Stated as the number rather than as the formula, because "0.3m per
        # turn" is what has to be weighed against tens of metres a minute of
        # phantom, and the formula hides how small it is.
        expected = (np.pi / 2) * MAX_LATERAL_MS2 * 1.0 ** 2 / 24
        assert expected == pytest.approx(0.29, abs=0.02)

    @pytest.mark.parametrize('window_s,expected_m', [
        (0.30, 0.36), (0.50, 0.82), (1.03, 2.12),
    ])
    def test_a_hard_cut_costs_far_more_than_a_curve(self, window_s, expected_m):
        # The honest adversarial case: not a curve at all but a cusp — sprint
        # in, plant, come back the way you came. A long average smears straight
        # through it. Six times the cost of a smooth 90° turn, and this is the
        # figure the window has to survive rather than the arc.
        run, stop_s, v = int(2.5 * FPS), 0.2, 5.0
        half = int(stop_s * FPS / 2)
        ys = [-v * t / FPS for t in range(run, 0, -1)]
        for i in range(1, half + 1):
            ys.append(ys[-1] + v * (1 - i / half) / FPS)
        for i in range(1, half + 1):
            ys.append(ys[-1] - v * (i / half) / FPS)
        ys += [ys[-1] - v * i / FPS for i in range(1, run + 1)]

        pts = np.column_stack([np.zeros(len(ys)), np.array(ys)])
        window = int(round(window_s * FPS))
        pad = window // 2
        smoothed = smooth_positions(series(pts), window).positions_m
        lost = length(pts[pad:-pad]) - length(smoothed[pad:-pad])

        assert lost == pytest.approx(expected_m, rel=0.15)

    def test_it_would_take_twenty_hard_cuts_a_minute_to_lose_the_trade(self):
        # The trade, in one assertion. Going from the old 0.3s window to 1.0s
        # at sigma=0.20 saves 50m of invented distance every minute and costs
        # about 2.1m for each stop-turn, so it takes 23 full stop-and-reverses
        # a minute — one every two and a half seconds, for the whole match —
        # before the longer window is the worse of the two.
        saved = phantom_m_per_minute(0.20, 0.3) - phantom_m_per_minute(0.20, 1.0)
        assert saved / 2.12 > 20


class TestWhatAShortWindowCosts:
    def test_phantom_distance_falls_as_one_over_the_window(self):
        # Exact, not fitted. Smoothing white noise leaves neighbours correlated
        # (W-1)/W, so the step between them is sigma·√2/W per axis and its 2D
        # magnitude averages sigma·√π/W.
        still = np.zeros((int(60 * FPS), 2)) + np.array([50.0, 34.0])
        for window in (9, 15, 21, 31):
            noisy = jitter(still, 0.10, seed=7)
            measured = length(smooth_positions(series(noisy), window).positions_m)
            claimed = phantom_m_per_minute(0.10, window / FPS)
            assert measured == pytest.approx(claimed, rel=0.05), f'W={window}'

    def test_the_old_fixed_window_over_reported_a_noisy_track(self):
        # What this change is worth, on the case it was worst on. A nine-frame
        # window at sigma=0.30 credits a still player with 56m a minute; the
        # window that noise now earns brings it to 32m.
        old = phantom_m_per_minute(0.30, 9 / FPS)
        new = phantom_m_per_minute(0.30, smoothing_window_s(0.30))
        assert old > 55 and new < 35


class TestTheFittedRule:
    """The bands, measured against ground truth rather than argued for."""

    @pytest.mark.parametrize('sigma', [0.05, 0.10, 0.15, 0.20, 0.30])
    def test_it_stays_inside_two_metres_a_minute(self, sigma):
        # Twenty minutes of ground truth at 181 m/min of real running. The
        # fixed nine-frame window drifts to +29 m/min at sigma=0.30, which is
        # 1.8km over a match on a figure a coach reads as kilometres run.
        paths = [match_minute(seed) for seed in range(20)]
        truth = sum(length(p) for p in paths)

        total = 0.0
        for i, path in enumerate(paths):
            noisy = jitter(path, sigma, seed=100 + i)
            smoothed, _ = smooth_for_noise(noisy_series := series(noisy),
                                           position_noise_m(noisy_series))
            total += movement_stats(smoothed).distance_m

        assert abs(total - truth) / len(paths) < 2.0

    @pytest.mark.parametrize('turns_per_min', [3.0, 8.0, 25.0])
    def test_it_survives_a_turn_rate_it_was_not_fitted_at(self, turns_per_min):
        # Fitted at 8 direction changes a minute. A rule that only holds at the
        # rate it was fitted at is not a rule, so this is the check that the
        # answer does not hinge on the one number here that had to be assumed.
        # Worst observed across 3 to 25 turns a minute is -3.3 m/min.
        paths = [match_minute(seed, turns_per_min=turns_per_min) for seed in range(20)]
        truth = sum(length(p) for p in paths)

        total = 0.0
        for i, path in enumerate(paths):
            noisy = series(jitter(path, 0.20, seed=100 + i))
            smoothed, _ = smooth_for_noise(noisy, position_noise_m(noisy))
            total += movement_stats(smoothed).distance_m

        assert abs(total - truth) / len(paths) < 4.0

    def test_the_window_grows_with_the_noise_and_stops(self):
        assert smoothing_window_s(0.02) == 0.5
        assert smoothing_window_s(0.10) == 0.7
        assert smoothing_window_s(0.20) == 1.0
        # Open-ended at the top rather than growing without limit: past
        # MAX_ACCEL_NOISE_M the bursts are already refused, and a window long
        # enough to rescue a track that noisy would have flattened the football
        # out of it.
        assert smoothing_window_s(MAX_ACCEL_NOISE_M * 3) == 1.0

    def test_an_unmeasured_track_gets_the_middle_band(self):
        # Guessing low invents distance and guessing high erases it. Neither is
        # safe, so the answer is the one that is wrong by less either way.
        assert smoothing_window_s(None) == DEFAULT_SMOOTH_S
        assert 0.5 <= DEFAULT_SMOOTH_S <= 1.0

    def test_a_nonsense_noise_estimate_does_not_pick_a_nonsense_window(self):
        assert smoothing_window_s(float('nan')) == DEFAULT_SMOOTH_S
        assert smoothing_window_s(-1.0) == DEFAULT_SMOOTH_S


class TestTheWindowIsInSeconds:
    def test_the_same_footage_at_half_the_frame_rate_smooths_the_same_time(self):
        # The latent bug this fixes. Written as nine frames, the pipeline
        # smoothed over 0.3s at 30fps and 0.6s at 15fps, so subsampling the
        # video to save compute would have moved every distance figure without
        # a line of code changing. The roadmap has frame subsampling on it.
        path = match_minute(1)
        full = series(path)
        half = PositionSeries(1, np.arange(0, len(path), 2) / FPS, path[::2])

        _, a = smooth_for_noise(full, 0.10)
        _, b = smooth_for_noise(half, 0.10)
        assert a == b == 0.7

    def test_the_phantom_stops_depending_on_the_frame_rate_at_all(self):
        # The consequence, and the reason this is worth fixing rather than
        # noting. Phantom distance is 60·f·σ·√π/W metres a minute; a window
        # fixed in *frames* leaves the f in place, so halving the frame rate
        # halves the invented distance and every player's total moves. Written
        # in seconds, W = T·f and the frame rate cancels out exactly: the same
        # jitter costs the same metres a minute at any sampling.
        #
        # Twenty seconds of a motionless player, sampled two ways.
        still_30 = np.zeros((int(20 * FPS), 2))
        still_15 = np.zeros((int(20 * FPS / 2), 2))
        s30 = PositionSeries(1, np.arange(len(still_30)) / FPS,
                             jitter(still_30, 0.10, 5))
        s15 = PositionSeries(1, np.arange(len(still_15)) / (FPS / 2),
                             jitter(still_15, 0.10, 5))

        d30 = length(smooth_for_noise(s30, 0.10)[0].positions_m)
        d15 = length(smooth_for_noise(s15, 0.10)[0].positions_m)

        assert d15 == pytest.approx(d30, rel=0.20)


class TestTheEnds:
    """Repeating the last position shortens every track, once per fragment."""

    @pytest.mark.parametrize('window', [9, 15, 21, 31, 45])
    def test_a_straight_run_survives_any_window_exactly(self, window):
        # The case where "correct" is unambiguous: a player running in a
        # straight line covers exactly what they covered, and no amount of
        # smoothing should change it. Repeating the endpoint lost 0.37m at nine
        # frames and 1.29m at a full second; reflecting through it loses none.
        pts = straight(5.0, 10.0)
        smoothed = smooth_positions(series(pts), window).positions_m
        assert length(smoothed) == pytest.approx(length(pts), abs=1e-6)

    def test_even_when_the_window_is_a_third_of_the_track(self):
        # Which is the case that matters: the tracker hands over 3.4 fragments
        # per player, so the ends are a large share of a real track and the
        # loss was paid once for each of them.
        pts = straight(5.0, 3.0)
        smoothed = smooth_positions(series(pts), 31).positions_m
        assert length(smoothed) == pytest.approx(length(pts), abs=1e-6)

    def test_the_price_is_a_little_more_noise_at_the_ends(self):
        # Stated rather than hidden. Reflecting mirrors the wobble as well as
        # the path, so a still player gains a fraction of a metre more per
        # fragment — against 1.29m of real path no longer thrown away.
        still = np.zeros((int(10 * FPS), 2))
        noisy = series(jitter(still, 0.10, seed=11))
        assert length(smooth_positions(noisy, 31).positions_m) < 2.0


class TestWhatDidNotGetBetter:
    """The limits, so the roadmap entry is not the only place they appear."""

    @pytest.mark.parametrize('sigma,ceiling', [(0.05, 1.10), (0.20, 1.25)])
    def test_top_speed_is_still_biased_upward(self, sigma, ceiling):
        # A maximum of a noisy series is biased up by construction — noise can
        # only ever push the peak higher, never lower — and no window removes
        # that. The fitted window roughly halves it (from +30% to +9% at
        # sigma=0.20) and cannot do more.
        jog = np.column_stack([
            np.zeros(int(30 * FPS)),
            np.cumsum(np.full(int(30 * FPS), 8.0 / FPS)),
        ])
        noisy = series(jitter(jog, sigma, seed=4))
        smoothed, _ = smooth_for_noise(noisy, position_noise_m(noisy))
        assert movement_stats(smoothed).top_speed_ms < 8.0 * ceiling

    def test_the_smoothed_path_is_still_shorter_than_the_real_one(self):
        # Nothing here claims the path became accurate. The corner it cuts is
        # real; the totals agree because the two errors are of a size, not
        # because either went away. A clean track with a genuine turn in it
        # still under-reports, and that is the honest reading of this fit.
        turn = arc(5.0, np.pi)
        smoothed = smooth_positions(series(turn), 31).positions_m
        assert length(smoothed) < length(turn)
