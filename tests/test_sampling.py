"""How many frames a second the movement figures actually need.

The roadmap asked whether to process every frame or subsample to cut compute
cost, and the pipeline had half an answer: the `stride` docstring records what
skipping frames costs *tracking* — 100 tracks down to 70, the longest falling
from 449 frames to 225 — measured on real footage. Nothing measured what it
costs the numbers a coach reads.

These tests are that measurement, and they are synthetic for the same reason
tests/test_smoothing.py is: the ground truth is constructed, so the error is
known exactly rather than compared against a second guess.

    What came out.

Nothing. Distance, mean speed, sprints and bursts are flat from 60Hz down to
6Hz, and top speed moves less with the rate than it does with a tenth of a
metre of positional wobble. The reason is the previous item: the smoothing
window is stated in seconds, so the smoothed path is nearly the same curve
whatever the sample rate — the average simply has fewer samples in it.

Which is also where it ends. Below three samples the window stops being an
average, the wobble it was hiding comes back, and the burst count goes with it.
That is the floor, it is set by the smoothing rather than by football, and at
the current bands it is six a second.

    What this does not say.

That subsampling is free. It is free *for these figures*. Tracking, identity
and ball coverage are a different question with a worse answer, already
measured on real footage and recorded in `analyse_match`. The finding here is
narrower and more useful than "subsample everything": the movement figures were
never the reason to run at full rate.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_sampling.py -q
"""

from __future__ import annotations

import numpy as np
import pytest

from cv.frame_sampler import effective_fps, stride_for_fps
from cv.metrics import (
    MIN_SAMPLES_IN_WINDOW,
    SMOOTH_BANDS,
    PositionSeries,
    min_sample_hz,
    movement_stats,
    position_noise_m,
    sampling_warnings,
    smooth_for_noise,
    smoothing_window_s,
)

SOURCE_FPS = 30.0
MAX_LATERAL_MS2 = 4.5


def match_minutes(seed: int, minutes: float = 2.0, fps: float = SOURCE_FPS):
    """The same generator tests/test_smoothing.py fits the window against.

    Kept here rather than imported so a change made for one fit cannot silently
    move the other's numbers — these two files pin different constants against
    the same idea of a footballer, and they should fail separately.
    """
    rng = np.random.default_rng(seed)
    n = int(minutes * 60 * fps)
    dt = 1.0 / fps
    gears = np.array([0.5, 2.0, 4.0, 6.0, 8.0])
    weights = np.array([0.15, 0.40, 0.28, 0.13, 0.04])

    pos = np.zeros((n, 2))
    heading = rng.uniform(0, 2 * np.pi)
    speed, target = 2.0, 2.0
    turning, turn_rate = 0.0, 0.0
    p = np.array([50.0, 34.0])
    hazard = 8.0 / (60 * fps)

    for i in range(n):
        if rng.random() < 1.0 / (2.0 * fps):
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


def truth(points, fps: float = SOURCE_FPS):
    """Every figure at full rate, with no wobble and nothing smoothed away."""
    times = np.arange(len(points)) / fps
    return movement_stats(
        PositionSeries(1, times, np.asarray(points, float)), noise_m=0.0,
    )


def analysed(points, sigma: float, stride: int, seed: int = 0,
             fps: float = SOURCE_FPS):
    """The same truth put through wobble, subsampling and the real fit."""
    noisy = points + np.random.default_rng(seed).normal(0, sigma, np.shape(points))
    keep = np.arange(0, len(points), stride)
    raw = PositionSeries(1, keep / fps, noisy[keep])
    noise = position_noise_m(raw)
    smoothed, _ = smooth_for_noise(raw, noise)
    return movement_stats(smoothed, noise_m=noise)


def across_seeds(stride: int, sigma: float, seeds=range(8), fps=SOURCE_FPS):
    """Mean error in each figure against its own truth, over eight minutes."""
    distance, top, sprints, bursts = [], [], [], []
    for seed in seeds:
        points = match_minutes(seed, fps=fps)
        want = truth(points, fps)
        got = analysed(points, sigma, stride, seed=seed + 100, fps=fps)
        distance.append(100 * (got.distance_m / want.distance_m - 1))
        top.append(100 * (got.top_speed_ms / want.top_speed_ms - 1))
        sprints.append(got.sprint_count - want.sprint_count)
        bursts.append((got.accelerations or 0) - (want.accelerations or 0))
    return {
        'distance_pct': float(np.mean(distance)),
        'top_speed_pct': float(np.mean(top)),
        'sprints': float(np.mean(sprints)),
        'bursts': float(np.mean(bursts)),
    }


class TestTheRateBarelyMatters:
    """The finding. Measured on a clean track, so the rate is the only variable.

    A 30Hz source at each stride, against its own noiseless truth:

        rate     distance %   top speed %   sprints   bursts
        30.0        -0.3         -1.4        +0.0      -0.1
        15.0        -0.3         -1.3        -0.1      -0.4
        10.0        -0.4         -1.4        +0.0      -0.2
         7.5        -0.3         -1.1        -0.1      -0.9
         5.0        -0.2         -0.7        -0.2      -0.4
    """

    @pytest.mark.parametrize('stride', [1, 2, 3, 4, 6])
    def test_distance_holds_to_within_one_percent(self, stride):
        assert abs(across_seeds(stride, 0.0)['distance_pct']) < 1.0

    @pytest.mark.parametrize('stride', [1, 2, 3, 4, 6])
    def test_top_speed_holds_to_within_five_percent(self, stride):
        assert abs(across_seeds(stride, 0.0)['top_speed_pct']) < 5.0

    @pytest.mark.parametrize('stride', [1, 2, 3, 4, 6])
    def test_the_sprint_count_does_not_move(self, stride):
        assert abs(across_seeds(stride, 0.0)['sprints']) < 1.0

    def test_a_sixth_of_the_frames_is_the_same_distance_as_all_of_them(self):
        """Stated as the comparison rather than as two error bars.

        This is the whole item: five frames a second and thirty frames a second
        produce the same distance total, so the compute spent on the other
        twenty-five was never buying a distance figure.
        """
        full = across_seeds(1, 0.10)['distance_pct']
        sixth = across_seeds(6, 0.10)['distance_pct']
        assert abs(sixth - full) < 1.0


class TestWobbleMattersMoreThanRate:
    """Which variable actually moves these numbers, since only one is a choice.

    Top speed at 0.15m of wobble runs about +4% whatever the rate; across every
    rate from 60Hz to 6Hz it varies by less than that. So the frame rate is not
    where the error in a speed figure comes from, and lowering it is not what
    would fix one.
    """

    def test_the_spread_across_rates_is_smaller_than_the_cost_of_wobble(self):
        clean = [across_seeds(s, 0.0)['top_speed_pct'] for s in (1, 2, 4, 6)]
        spread = max(clean) - min(clean)
        wobble = abs(across_seeds(1, 0.15)['top_speed_pct']
                     - across_seeds(1, 0.0)['top_speed_pct'])
        assert spread < wobble

    def test_distance_survives_wobble_at_every_rate_because_the_window_fits_it(self):
        # The previous item's payoff: the window is chosen from each track's
        # own measured wobble, so subsampling does not need a different one.
        for stride in (1, 2, 4, 6):
            assert abs(across_seeds(stride, 0.20)['distance_pct']) < 1.5


class TestASixtyHertzSourceBuysNothing:
    """The case that actually costs money today.

    `FOOTAGE_DAY.md` asks for a native-resolution export, and phones shoot 60.
    At stride 1 that is twice the inference of a 30fps clip for figures that
    measure the same:

        rate     distance %   top speed %
        60.0        +0.1         +6.0
        30.0        -0.0         +5.1
        20.0        +0.1         +4.7
        15.0        -0.1         +3.8
        10.0        -0.1         +3.4
         6.0        +0.8         +6.5
    """

    def test_halving_a_sixty_hertz_source_changes_no_distance(self):
        full = across_seeds(1, 0.12, fps=60.0)
        halved = across_seeds(2, 0.12, fps=60.0)
        assert abs(halved['distance_pct'] - full['distance_pct']) < 0.5

    def test_nor_the_sprint_count(self):
        full = across_seeds(1, 0.12, fps=60.0)
        halved = across_seeds(2, 0.12, fps=60.0)
        assert abs(halved['sprints'] - full['sprints']) < 0.5

    def test_a_sixty_hertz_clip_asked_for_thirty_skips_every_other_frame(self):
        assert stride_for_fps(60.0, 30.0) == 2
        assert effective_fps(60.0, 2) == 30.0


class TestWhereItBreaks:
    """The floor, and that it is set by the smoothing rather than by football.

    At two samples in the window on a track with 0.15m of wobble the burst
    count runs +4.0 a minute against truth — noise counted as acceleration,
    which is the precise failure the window exists to prevent.
    """

    def test_the_floor_is_three_samples_in_the_narrowest_band(self):
        assert min_sample_hz() == pytest.approx(6.0)
        narrowest = min(seconds for _, seconds in SMOOTH_BANDS)
        assert round(min_sample_hz() * narrowest) == MIN_SAMPLES_IN_WINDOW

    def test_at_the_floor_every_figure_is_still_where_it_was(self):
        stride = int(round(SOURCE_FPS / min_sample_hz()))
        at_floor = across_seeds(stride, 0.15)
        assert abs(at_floor['distance_pct']) < 1.5
        assert abs(at_floor['bursts']) < 2.0

    def test_just_below_it_wobble_starts_being_counted_as_acceleration(self):
        # Two samples in the window, which is a midpoint rather than an
        # average. On a track with 0.15m of wobble the burst count runs four a
        # minute over truth — the precise failure the window exists to prevent,
        # arriving the moment the window stops being one.
        assert across_seeds(15, 0.15)['bursts'] > 3.0

    def test_further_down_the_bursts_stop_being_findable_at_all(self):
        # A different failure at one sample a second, and worth separating: the
        # burst window now holds too few points to see an acceleration through,
        # so instead of counting noise the run counts nothing. Seven a minute
        # missing, against four a minute invented one rate above.
        assert across_seeds(30, 0.15)['bursts'] < -4.0

    def test_at_the_floor_itself_the_burst_count_is_exactly_right(self):
        # Not "close". Six a second is where the last band still holds three
        # samples, and the figure most sensitive to that lands on truth.
        assert across_seeds(5, 0.15)['bursts'] == pytest.approx(0.0, abs=0.5)

    def test_the_floor_is_a_fact_about_the_window_not_about_the_sport(self):
        """Widen every band and the floor moves with it, unprompted."""
        wider = tuple((edge, seconds * 2) for edge, seconds in SMOOTH_BANDS)
        assert min_sample_hz(wider) == pytest.approx(min_sample_hz() / 2)


class TestTheWarningBelowTheFloor:
    def test_nothing_is_said_about_a_run_at_or_above_it(self):
        assert sampling_warnings(30.0) == []
        assert sampling_warnings(min_sample_hz()) == []

    def test_below_it_the_run_says_which_figure_broke(self):
        # Not "the numbers are wrong". Distance is still within about a percent
        # down here; the burst count is not, and sending a reader to doubt the
        # wrong figure is its own kind of error.
        warnings = sampling_warnings(2.0)
        assert len(warnings) == 1
        assert 'burst count' in warnings[0]
        assert 'distance and speed are still about right' in warnings[0]

    def test_a_run_that_never_recorded_a_rate_is_not_accused_of_one(self):
        assert sampling_warnings(None) == []
        assert sampling_warnings(0.0) == []


class TestAskingForARate:
    def test_a_target_the_source_cannot_reach_leaves_it_alone(self):
        # Not an error: a clip already slower than the ceiling should be run
        # whole, not padded to a rate it never had.
        assert stride_for_fps(24.0, 30.0) == 1
        assert stride_for_fps(10.0, 60.0) == 1

    def test_no_target_is_every_frame(self):
        assert stride_for_fps(30.0, None) == 1
        assert stride_for_fps(30.0, 0) == 1

    def test_a_nonsense_source_does_not_divide_by_it(self):
        assert stride_for_fps(0.0, 15.0) == 1
        assert stride_for_fps(-5.0, 15.0) == 1

    def test_the_published_rate_is_the_one_that_ran_not_the_one_requested(self):
        """A 30fps clip asked for 12 gets 15, and the report must say 15.

        Quoting the request would describe a run that did not happen, and every
        figure in the report belongs to the run that did.
        """
        stride = stride_for_fps(30.0, 12.0)
        assert stride == 2
        assert effective_fps(30.0, stride) == 15.0

    def test_the_rate_a_stride_implies_is_recoverable_from_it(self):
        for source in (24.0, 25.0, 30.0, 50.0, 60.0):
            for stride in (1, 2, 3):
                assert effective_fps(source, stride) == source / stride

    def test_a_smoothing_window_still_lands_in_frames_correctly_at_any_rate(self):
        # The join between the two ideas: a window in seconds has to become a
        # count of frames, and that count is what changes with the rate.
        for rate in (30.0, 15.0, 10.0, 6.0):
            times = np.arange(int(rate * 4)) / rate
            series = PositionSeries(1, times, np.zeros((len(times), 2)))
            noise = 0.10
            smoothed, window_s = smooth_for_noise(series, noise)
            assert window_s == smoothing_window_s(noise)
            assert len(smoothed) == len(series)
