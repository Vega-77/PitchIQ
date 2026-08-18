"""Turn tracked positions into the numbers a coach cannot get by watching.

Everything here operates in pitch metres, so it depends on a calibration. The
headline outputs — distance covered, top speed, sprint count — are what the
roadmap promises the halftime report, and they are also the easiest numbers in
the whole project to get quietly, badly wrong.

The trap is jitter. A detection box wobbles a few pixels frame to frame even
when the player is standing still, and every wobble looks like movement. At
30fps a 0.2m wobble accumulates 6m/s of phantom speed and kilometres of
phantom distance over a match. Smoothing is therefore not a polish step, it is
load-bearing: without it these figures are not merely imprecise, they are
fiction. `smooth_positions` and the speed ceiling below exist for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Nobody outruns this. Anything above it is a tracking error — usually an
# identity switch teleporting a track across the pitch — not a fast player.
# Usain Bolt peaked around 12.4 m/s; a very quick footballer tops out near 10.
MAX_PLAUSIBLE_SPEED_MS = 12.0

# The conventional sprint threshold in football analytics, ~25.2 km/h.
SPRINT_THRESHOLD_MS = 7.0

# A sprint has to last a moment to count; a single frame over the line is noise.
MIN_SPRINT_DURATION_S = 0.5

# ---------------------------------------------------------------- bursts
#
# Acceleration is the second derivative of a position, and each derivative
# multiplies the noise. The jitter that fakes 6 m/s of speed between two frames
# fakes 180 m/s² of acceleration between three, which is fifteen times what a
# human can produce — so a frame-to-frame acceleration is not a noisy version of
# the real thing, it is entirely noise with the real thing somewhere inside it.
#
# So this never differentiates twice. A burst is measured as a **speed gain
# across a span**: how much faster the player was one second later. That is one
# derivative of a speed which was itself smoothed, and averaging over a whole
# second is what makes it survivable — the jitter is zero-mean, so it cancels
# across the span while a real acceleration does not.
#
# The cost is stated rather than hidden: a burst shorter than the span reads
# lower than it was, because it is averaged against the stillness on either
# side. A 0.4s explosion out of the blocks is real football and this will
# understate it. That is the direction to be wrong in — the other one invents
# bursts nobody ran.

# How long a burst is measured over.
ACCEL_WINDOW_S = 1.0

# Sustained across that span to count. Elite footballers reach 6-8 m/s² for a
# fraction of a second; held for a full second, 2 m/s² is already a hard
# acceleration — it is 2 m/s of extra pace, walk to jog to run.
MIN_ACCEL_MS2 = 2.0

# The speeds a burst is read off get their own, much longer smoothing window
# than distance does, because they are answering a second-order question.
#
# This was measured rather than picked. On synthetic 30fps tracks at the window
# the pipeline actually smooths over — a player jogging at a constant 3 m/s, so
# every burst reported is a false one, against a player running four genuine
# 3 m/s² bursts in the same minute — reading bursts straight off that smoothing
# gives, per 60 seconds:
#
#     noise σ    false bursts    real bursts (of 8)
#      0.05 m         0.0              8.0
#      0.10 m         2.4              9.6
#      0.20 m        40.1             37.4
#
# At 0.20 m there are more phantom accelerations in one minute than a real
# player makes in a match, and no threshold fixes it: the false and the real
# counts rise together, because they are the same noise. The window is what is
# wrong. Smoothing the burst pass over a full second instead:
#
#     noise σ    false bursts    real bursts (of 8)
#      0.05 m         0.0              8.0
#      0.10 m         0.0              8.0
#      0.20 m         0.0              8.0
#      0.30 m         0.0              7.5
#      0.40 m           — withheld, see below
#
# Exact to 0.2 m, fraying at 0.3 m, and past that the count is a count of the
# jitter — which is what MAX_ACCEL_NOISE_M is for.
ACCEL_SMOOTH_S = 1.0

# Above this much positional noise, the burst count is refused rather than
# reported. Measured per track, from the track itself — see `position_noise_m`.
MAX_ACCEL_NOISE_M = 0.30

# ------------------------------------------------- how long to smooth for
#
# The window distance is read off was 9 frames, written as a bare number in the
# pipeline, and it was never fitted to anything. Two errors decide it, and they
# pull in opposite directions:
#
#   phantom  — a still player accumulates distance from jitter alone. Measured
#              at 60·fps·σ·√π / W metres a minute, exactly: the smoothed noise
#              has neighbouring samples correlated (W-1)/W, so the step between
#              them has size σ√2/W per axis, and a 2D magnitude averages σ√π/W.
#              Verified to three figures at every window and noise level below.
#              **Falls as 1/W.**
#
#   corners  — a moving average cuts the inside off a turn. On a constant arc
#              the loss is exact: the average of an arc of half-angle θ = vT/2R
#              sits at radius R·sin(θ)/θ, so the path shortens by that factor.
#              Since a person cannot exceed about 4.5 m/s² sideways, R is at
#              least v²/a, and the metres lost to a turn of angle φ work out at
#              φ·a·T²/24 — **independent of speed**. A fast turn is necessarily
#              a wide one, which is the whole reason this is survivable: a 90°
#              turn costs 0.03m at 0.3s and 0.30m at 1.0s.
#
# The real cost is not the smooth arc but the hard cut — decelerate, plant,
# accelerate back — which is a cusp rather than a curve, and which a long
# average smears right through. Metres of real path lost to one 180° stop-turn,
# by how long the player is stopped:
#
#     window      0.2s stop   0.4s stop   0.8s stop
#      0.30s        0.36        0.19        0.09
#      0.50s        0.82        0.52        0.26
#      1.03s        2.12        1.71        1.10
#      1.50s        3.27        2.84        2.11
#
# Two metres a turn sounds ruinous until it is put beside the phantom it buys
# off: at σ=0.20 going from a 0.3s window to a 1.0s one saves 50 metres a minute
# of invented distance and costs about 2.1 metres for each hard cut. A player
# would have to stop and reverse 23 times a minute — one every two and a half
# seconds, for the whole match — before that trade starts to lose.
#
# So it was fitted rather than argued. Twenty minutes of synthetic ground truth
# — speeds drawn from the match-play gears, direction changes at a stated rate,
# every turn held to 4.5 m/s² — measured through each window, as metres per
# minute of error against a real 181 m/min:
#
#     window    σ=0.05   σ=0.10   σ=0.15   σ=0.20   σ=0.30
#      0.17s     +2.9    +11.3    +24.4    +41.3    +81.4
#      0.30s     +0.7     +3.5     +7.8    +13.6    +29.4      <- was here
#      0.50s     -0.2     +0.8     +2.5     +4.7    +10.8
#      0.70s     -0.7     -0.2     +0.7     +1.9     +5.2
#      1.03s     -1.7     -1.4     -1.0     -0.5     +1.1
#      1.50s     -3.2     -3.1     -2.9     -2.6     -1.8
#
# There is no single best window in that table, which is the finding: the right
# one moves with the noise, and since `position_noise_m` measures the noise per
# track, the window can follow it. The bands below are the argmin of each
# column. They hold the error inside ±2 m/min from σ=0.05 to σ=0.30, where the
# fixed 0.3s window drifts to +29 m/min — 1.8km over a 60-minute track, on a
# figure a coach reads as kilometres run.
#
# Fitted at 8 direction changes a minute, and then checked against that
# assumption, because a rule that only works at the rate it was fitted at is
# not a rule. Worst error under the rule, in m/min:
#
#     turns/min   σ=0.05   σ=0.10   σ=0.15   σ=0.20   σ=0.30
#          3       +0.1     +0.3     +1.1     +0.4     +1.9
#          8       -0.2     -0.2     +0.7     -0.5     +1.1
#         15       -0.6     -1.1     -0.2     -2.2     -0.6
#         25       -0.9     -1.6     -0.8     -3.3     -1.7
#
# Nothing here says a longer window is more *accurate*, only that the distance
# it totals is closer. The path itself is smoother than the player's, and the
# corner it cuts is real. That trade is right for a distance total and it is
# the reason heatmaps and shape are unaffected either way — a 1s window moves a
# position by the corner cut, which is under half a metre.
#
# Boundaries in metres of measured wobble, and the window in seconds for each.
# Open-ended at the top: past MAX_ACCEL_NOISE_M the bursts are already refused,
# and distance survives noise that bursts do not because it is one derivative
# rather than two.
SMOOTH_BANDS = ((0.075, 0.5), (0.15, 0.7), (float('inf'), 1.0))

# When nothing measured the noise — a track too short to take a second
# difference of — the middle band. Guessing low invents distance and guessing
# high erases it, and the middle is wrong by less than either.
DEFAULT_SMOOTH_S = 0.7

# The fewest samples a smoothing window may contain and still be an average.
#
# Two is a midpoint and one is the sample itself, so below three the window
# stops smoothing and the wobble it was hiding comes back. Measured rather than
# argued: at three samples every movement figure is where it was at thirty, and
# at two the burst count jumps by four a minute on a track with 0.15m of wobble
# — noise being counted as acceleration, which is the exact failure the window
# exists to prevent. See tests/test_sampling.py.
MIN_SAMPLES_IN_WINDOW = 3


def min_sample_hz(bands=SMOOTH_BANDS) -> float:
    """The slowest frame rate every smoothing band still survives.

    Set by the narrowest band, because that is the strictest requirement and
    which band a track lands in is not known until its wobble is measured. At
    the current fit that is 0.5s, so three samples means six a second.

    This is the floor on *sampling*, and it is a fact about the smoothing
    rather than about football — which is why it lives here beside the bands
    rather than in the sampler.
    """
    narrowest = min(seconds for _, seconds in bands)
    return MIN_SAMPLES_IN_WINDOW / narrowest if narrowest > 0 else 0.0


def sampling_warnings(sample_fps: float | None) -> list[str]:
    """Whether a run read too few frames a second for the window to work.

    Worded around the figure that actually breaks. Distance and speed are still
    about right below the floor — measured at −1.3% and −3.2% at one frame a
    second — and saying "the numbers are wrong" would send a reader to doubt
    the wrong ones. What goes is the burst count, in both directions: four a
    minute invented at two frames a second and seven a minute missed at one.
    """
    floor = min_sample_hz()
    if not sample_fps or sample_fps <= 0 or sample_fps >= floor:
        return []
    return [
        f'analysed at {sample_fps:.1f} frames a second, below the {floor:.0f} '
        f'the smoothing window needs — distance and speed are still about '
        f'right, but the burst count is reporting wobble rather than football'
    ]


@dataclass
class PositionSeries:
    """One track's path across the pitch, in metres."""

    track_id: int
    timestamps_s: np.ndarray
    positions_m: np.ndarray  # (N, 2)

    def __len__(self) -> int:
        return len(self.timestamps_s)


@dataclass
class MovementStats:
    track_id: int
    distance_m: float = 0.0
    top_speed_ms: float = 0.0
    mean_speed_ms: float = 0.0
    sprint_count: int = 0
    sprint_distance_m: float = 0.0
    # None, not zero, when the track is shorter than one burst window: a
    # player watched for half a second did not fail to accelerate.
    accelerations: int | None = None
    top_acceleration_ms2: float | None = None
    # How much the raw position wobbled. None when nobody measured it.
    position_noise_m: float | None = None
    minutes_tracked: float = 0.0
    discarded_frames: int = 0

    @property
    def distance_km(self) -> float:
        return self.distance_m / 1000.0

    @property
    def top_speed_kmh(self) -> float:
        return self.top_speed_ms * 3.6


def smoothing_window_s(noise_m: float | None) -> float:
    """How long to smooth a track whose wobble has been measured.

    See SMOOTH_BANDS above for the measurement this is read off. The window is
    in seconds rather than frames because it is a claim about the player — how
    long a real movement takes — and not about the camera. Written as a frame
    count, as it was, the same code smooths for half as long again the moment
    anyone subsamples the video to save compute, and every distance figure
    moves without a line changing.
    """
    if noise_m is None or not np.isfinite(noise_m) or noise_m < 0:
        return DEFAULT_SMOOTH_S
    for ceiling, window_s in SMOOTH_BANDS:
        if noise_m < ceiling:
            return window_s
    return SMOOTH_BANDS[-1][1]


def phantom_m_per_minute(
    noise_m: float | None, window_s: float, fps: float = 30.0
) -> float | None:
    """Metres a minute a motionless player is credited with, at this smoothing.

    Exact rather than fitted: smoothing white noise of size σ over W frames
    leaves neighbouring samples correlated (W-1)/W, so the step between two of
    them has size σ√2/W per axis and its 2D magnitude averages σ√π/W. Sixty
    seconds of those steps is the figure below, and it matches the measured
    tables to three figures.

    This exists because the browser used to hold the same number as a constant,
    which was true only while the window was one. The window now follows the
    noise, so the rate is no longer proportional to the noise, and the only
    place that knows both is here.
    """
    if noise_m is None or noise_m <= 0 or window_s <= 0 or fps <= 0:
        return None
    frames = max(1.0, round(window_s * fps))
    return float(60.0 * fps * noise_m * np.sqrt(np.pi) / frames)


def smooth_positions(series: PositionSeries, window: int = 5) -> PositionSeries:
    """Moving-average smoothing over the position series.

    A plain centred moving average, deliberately: a Kalman filter would model
    velocity more gracefully, but it needs tuning that cannot be validated
    without ground-truth tracks, and an untuned filter can lag hard during
    direction changes — exactly the moments that matter. This is honest about
    being crude and has no hidden parameters to get wrong.

    The ends are extended by reflecting the path through its own endpoint, not
    by repeating that endpoint. Repeating it pins the last position and drags
    the average toward it, which shortens every track by a fixed amount at each
    end — 1.29m at a one-second window, paid once per fragment, and the tracker
    hands over 3.4 fragments per player. Reflecting oddly extends the straight
    line the player was on instead, which costs exactly nothing on a straight
    run at any window, even one as long as a third of the track. The price is a
    little extra noise at the very ends, measured at 0.14m per fragment against
    the 1.29m of real path it stops throwing away.
    """
    n = len(series)
    if n < 3 or window < 2:
        return series

    window = min(window, n if n % 2 else n - 1)
    if window % 2 == 0:
        window -= 1
    if window < 3:
        return series

    pad = window // 2
    padded = np.pad(
        series.positions_m, ((pad, pad), (0, 0)),
        mode="reflect", reflect_type="odd",
    )
    kernel = np.ones(window) / window

    smoothed = np.column_stack([
        np.convolve(padded[:, 0], kernel, mode="valid"),
        np.convolve(padded[:, 1], kernel, mode="valid"),
    ])

    return PositionSeries(series.track_id, series.timestamps_s, smoothed)


def position_noise_m(series: PositionSeries) -> float | None:
    """How much the tracked position wobbles frame to frame, in metres.

    Nothing in this project has ever measured this, and almost every figure the
    pipeline produces depends on it — see the tables above, and the phantom
    distance a still player accumulates.

    Estimated from the **second** difference of position, which is what
    separates a wobble from a movement: a real trajectory is smooth, so its
    second difference is the acceleration times dt² — about 0.01 m for a hard
    acceleration at 30fps — while white noise of size σ produces second
    differences of σ√6. Taking a median rather than a mean is what makes it
    survive the real accelerations mixed in: they are the minority of frames,
    and a median ignores a minority.

    For noise of size σ, median|Δ²x| = 0.6745·σ·√6 = 1.652σ. Recovered within
    1.5% at every level from 0.02 m to 0.20 m on synthetic tracks.

    **Must be given the raw series, before smoothing.** A moving average
    correlates neighbouring samples, which is precisely what this measures the
    absence of; run on a smoothed series it reports a fraction of the truth.
    """
    if len(series) < 3:
        return None

    second = np.diff(series.positions_m, n=2, axis=0)
    if not len(second):
        return None

    # Per axis and combined, because the homography stretches the two
    # differently — a metre across the pitch is not a metre up it, at the far
    # touchline especially.
    per_axis = [
        float(np.median(np.abs(second[:, axis])) / 1.652) for axis in (0, 1)
    ]
    return float(np.hypot(*per_axis) / np.sqrt(2))


def smooth_for_noise(
    series: PositionSeries, noise_m: float | None
) -> tuple[PositionSeries, float]:
    """Smooth a track over the window its own measured wobble calls for.

    Returns the smoothed series and the window actually used, in seconds. The
    caller wants both — the window is a fact about how the figures were made
    and belongs in the report beside them, not only in this module's head.

    One function rather than a band lookup plus a frame conversion plus a call,
    because those three have to agree and there is no reason for anywhere else
    to hold two thirds of that.
    """
    window_s = smoothing_window_s(noise_m)
    return smooth_positions(series, _window_for(series, window_s)), window_s


def _window_for(series: PositionSeries, seconds: float) -> int:
    """A smoothing window in frames, from one in seconds."""
    if len(series) < 2:
        return 0
    dt = float(np.median(np.diff(series.timestamps_s)))
    if dt <= 0:
        return 0
    return int(round(seconds / dt))


def movement_stats(
    series: PositionSeries,
    max_speed_ms: float = MAX_PLAUSIBLE_SPEED_MS,
    sprint_threshold_ms: float = SPRINT_THRESHOLD_MS,
    accel_window_s: float = ACCEL_WINDOW_S,
    min_accel_ms2: float = MIN_ACCEL_MS2,
    noise_m: float | None = None,
) -> MovementStats:
    """Distance, speed and sprints from a smoothed series.

    `noise_m` is `position_noise_m` measured on the **raw** series, before this
    one was smoothed. Given it, the burst count is refused above
    MAX_ACCEL_NOISE_M rather than reported as a number nobody could act on.
    Left out, the bursts are reported and the caller has said, in effect, that
    it does not know how noisy its input was.
    """
    stats = MovementStats(track_id=series.track_id)
    if len(series) < 2:
        return stats

    deltas = np.diff(series.positions_m, axis=0)
    steps_m = np.linalg.norm(deltas, axis=1)
    dt = np.diff(series.timestamps_s)

    valid = dt > 1e-6
    speeds = np.zeros_like(steps_m)
    speeds[valid] = steps_m[valid] / dt[valid]

    # Drop implausible jumps rather than letting one identity switch dominate
    # the total. Counted and reported, because a high count means the tracking
    # is struggling and the whole figure deserves suspicion.
    plausible = speeds <= max_speed_ms
    stats.discarded_frames = int(np.sum(~plausible))

    if not np.any(plausible):
        return stats

    good_steps = steps_m[plausible]
    good_speeds = speeds[plausible]
    good_dt = dt[plausible]

    stats.distance_m = float(np.sum(good_steps))
    stats.top_speed_ms = float(np.max(good_speeds))
    stats.mean_speed_ms = float(np.average(good_speeds, weights=good_dt))
    stats.minutes_tracked = float(
        (series.timestamps_s[-1] - series.timestamps_s[0]) / 60.0
    )

    sprints, sprint_distance = _find_sprints(
        good_speeds, good_dt, good_steps, sprint_threshold_ms
    )
    stats.sprint_count = sprints
    stats.sprint_distance_m = sprint_distance

    stats.position_noise_m = noise_m
    if noise_m is not None and noise_m > MAX_ACCEL_NOISE_M:
        # Left as None. A burst count from a track this noisy is a count of the
        # noise, and it would sit on a player's card looking exactly like a
        # count of their runs.
        return stats

    # Bursts get their own, much longer smoothing than distance did, for the
    # reasons measured at the top of this module. Applied on top of whatever
    # the caller already did, which only ever helps.
    eased = smooth_positions(series, _window_for(series, ACCEL_SMOOTH_S))
    burst_speeds, burst_times = _speed_series(eased, max_speed_ms)
    bursts, hardest = _find_bursts(
        burst_times, burst_speeds, accel_window_s, min_accel_ms2,
    )
    stats.accelerations = bursts
    stats.top_acceleration_ms2 = hardest

    return stats


def _speed_series(
    series: PositionSeries, max_speed_ms: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Speeds and the moments they belong to.

    At the midpoint of each step, not at either end: a speed measured between
    two frames happened between them, and pinning it to one of them shifts
    every burst half a frame in that direction.
    """
    steps = np.linalg.norm(np.diff(series.positions_m, axis=0), axis=1)
    dt = np.diff(series.timestamps_s)
    mids = (series.timestamps_s[:-1] + series.timestamps_s[1:]) / 2.0

    valid = dt > 1e-6
    speeds = np.zeros_like(steps)
    speeds[valid] = steps[valid] / dt[valid]

    keep = valid & (speeds <= max_speed_ms)
    return speeds[keep], mids[keep]


def _find_bursts(
    times_s: np.ndarray,
    speeds: np.ndarray,
    window_s: float,
    min_accel_ms2: float,
) -> tuple[int | None, float | None]:
    """Count the hard accelerations, and find the hardest.

    A burst is a speed gain sustained across `window_s`, not an instantaneous
    second derivative — see the note at the top of this module for why that
    distinction is the whole design.

    Counted **without overlap**: one acceleration is one acceleration, and a
    window sliding through it frame by frame would report thirty. Once a burst
    is taken the search resumes at the end of it, so a long continuous
    acceleration counts once per window it fills, which is the honest reading —
    two seconds of hard running is two seconds of hard running.

    Returns (None, None) when the track never spans a whole window. That is
    different from a player who was watched and did not accelerate.
    """
    n = len(times_s)
    if n < 2 or times_s[-1] - times_s[0] < window_s:
        return None, None

    # Every window at once. searchsorted rather than a scan because a 45-minute
    # track at 30fps is eighty thousand samples and this runs per player.
    ends = np.searchsorted(times_s, times_s + window_s, side='left')
    usable = ends < n
    if not np.any(usable):
        return None, None

    starts = np.flatnonzero(usable)
    ends = ends[usable]
    accel = (speeds[ends] - speeds[starts]) / (times_s[ends] - times_s[starts])

    # The hardest is taken over every window, including the ones the count
    # below steps over. A maximum that depended on where the counting happened
    # to land would not be a maximum.
    hardest = float(np.max(accel))

    # Counted **without overlap**: one acceleration is one acceleration, and a
    # window sliding through it frame by frame would report thirty. A long
    # continuous burst counts once per window it fills, which is the honest
    # reading — two seconds of hard running is two seconds of hard running.
    count = 0
    i = 0
    at = {int(start): k for k, start in enumerate(starts)}
    while i in at:
        k = at[i]
        if accel[k] >= min_accel_ms2:
            count += 1
            i = int(ends[k])
        else:
            i += 1

    return count, hardest


def _find_sprints(
    speeds: np.ndarray, dt: np.ndarray, steps_m: np.ndarray, threshold_ms: float
) -> tuple[int, float]:
    """Count runs above the threshold that last long enough to be real."""
    above = speeds >= threshold_ms
    count = 0
    distance = 0.0

    run_duration = 0.0
    run_distance = 0.0

    for is_fast, step_dt, step_m in zip(above, dt, steps_m):
        if is_fast:
            run_duration += float(step_dt)
            run_distance += float(step_m)
            continue

        if run_duration >= MIN_SPRINT_DURATION_S:
            count += 1
            distance += run_distance
        run_duration = 0.0
        run_distance = 0.0

    if run_duration >= MIN_SPRINT_DURATION_S:
        count += 1
        distance += run_distance

    return count, distance


def heatmap(
    series: PositionSeries,
    pitch_length_m: float,
    pitch_width_m: float,
    bins: tuple[int, int] = (12, 8),
) -> np.ndarray:
    """Occupancy grid over the pitch, normalised to sum to 1."""
    if len(series) == 0:
        return np.zeros(bins)

    grid, _, _ = np.histogram2d(
        series.positions_m[:, 0],
        series.positions_m[:, 1],
        bins=bins,
        range=[[0, pitch_length_m], [0, pitch_width_m]],
    )
    total = grid.sum()
    return grid / total if total else grid


EMPTY_SHAPE = {"width_m": 0.0, "depth_m": 0.0, "compactness_m": 0.0}

# Fewest instants either side of the split before a drift figure is offered. A
# comparison drawn from three snapshots is describing a moment, not a trend.
MIN_DRIFT_SAMPLES = 20

# How much a shape figure has to move before it is worth a sentence. Metres,
# and a guess like every other threshold in this package — set here because
# three metres is about a player's worth of spacing, and anything smaller is
# inside the noise a jittering detection box produces anyway.
MIN_DRIFT_M = 3.0


def shape_samples(series_by_track: dict[int, PositionSeries]):
    """Per-instant width, depth and spread, with the times they belong to.

    Split out of `team_shape` because averaging is only one of the questions
    worth asking of these. The other is whether they moved, which needs the
    samples rather than the mean, and recomputing them separately would let the
    two answers drift apart over time.

    Returns `(times, widths, depths, spreads)`, all the same length, covering
    only the instants where at least three players were on screen — two players
    have a width but not a shape.
    """
    if not series_by_track:
        return [], [], [], []

    # Snap to a common time base so players are compared at the same instants.
    times = sorted({round(t, 1) for s in series_by_track.values() for t in s.timestamps_s})

    kept, widths, depths, spreads = [], [], [], []

    for t in times:
        points = []
        for series in series_by_track.values():
            idx = np.searchsorted(series.timestamps_s, t)
            if 0 <= idx < len(series):
                points.append(series.positions_m[idx])

        if len(points) < 3:
            continue

        arr = np.array(points)
        kept.append(t)
        depths.append(float(np.ptp(arr[:, 0])))  # ndarray.ptp() went away in NumPy 2
        widths.append(float(np.ptp(arr[:, 1])))
        centroid = arr.mean(axis=0)
        spreads.append(float(np.mean(np.linalg.norm(arr - centroid, axis=1))))

    return kept, widths, depths, spreads


def team_shape(series_by_track: dict[int, PositionSeries]) -> dict[str, float]:
    """Width, depth and compactness averaged over time.

    Compactness is the mean distance from each player to the team's centroid.
    For whether it *changed* — the roadmap's "did the shape stretch in the last
    fifteen minutes" question, which is the sort of thing a coach genuinely
    cannot eyeball — see `shape_drift`.
    """
    _, widths, depths, spreads = shape_samples(series_by_track)
    if not widths:
        return dict(EMPTY_SHAPE)

    return {
        "width_m": float(np.mean(widths)),
        "depth_m": float(np.mean(depths)),
        "compactness_m": float(np.mean(spreads)),
    }


@dataclass
class ShapeDrift:
    """The same three figures, early in the window and late in it."""

    early: dict[str, float]
    late: dict[str, float]
    split_s: float

    def change(self, key: str) -> float | None:
        """Late minus early, in metres. Positive means it grew."""
        if key not in self.early or key not in self.late:
            return None
        return self.late[key] - self.early[key]

    def to_json(self) -> dict:
        return {
            'early': {k: round(v, 1) for k, v in self.early.items()},
            'late': {k: round(v, 1) for k, v in self.late.items()},
            'split_s': round(float(self.split_s), 1),
            'change': {
                k: round(self.change(k), 1) for k in self.early
                if self.change(k) is not None
            },
        }


def shape_drift(
    series_by_track: dict[int, PositionSeries],
    split_s: float | None = None,
    min_samples: int = MIN_DRIFT_SAMPLES,
) -> ShapeDrift | None:
    """How the shape differed late in the window compared with early.

    Two averages either side of a split rather than a fitted trend, because two
    averages are a thing a coach can be told — "you were four metres wider in
    the last twenty minutes" — and a gradient in metres per second is not.

    `split_s` defaults to the midpoint of the observed window. Returns None,
    never a zero drift, when either side has too few instants to average: a
    comparison drawn from three snapshots describes a moment, not a trend, and
    reporting it as "no change" would be a claim nobody measured.
    """
    times, widths, depths, spreads = shape_samples(series_by_track)
    if not times:
        return None

    if split_s is None:
        split_s = (times[0] + times[-1]) / 2

    early_idx = [i for i, t in enumerate(times) if t < split_s]
    late_idx = [i for i, t in enumerate(times) if t >= split_s]

    if len(early_idx) < min_samples or len(late_idx) < min_samples:
        return None

    def mean_of(values, indices):
        return float(np.mean([values[i] for i in indices]))

    return ShapeDrift(
        early={
            'width_m': mean_of(widths, early_idx),
            'depth_m': mean_of(depths, early_idx),
            'compactness_m': mean_of(spreads, early_idx),
        },
        late={
            'width_m': mean_of(widths, late_idx),
            'depth_m': mean_of(depths, late_idx),
            'compactness_m': mean_of(spreads, late_idx),
        },
        split_s=float(split_s),
    )


# What each figure growing actually means on a pitch, in a coach's words.
DRIFT_TEXT = {
    'width_m': ('spread {n:.0f}m wider', 'squeezed {n:.0f}m narrower'),
    'depth_m': ('stretched {n:.0f}m longer front to back',
                'compressed {n:.0f}m front to back'),
    'compactness_m': ('drifted {n:.0f}m further apart',
                      'tightened up by {n:.0f}m'),
}


def drift_notes(drift: ShapeDrift | None, min_change_m: float = MIN_DRIFT_M):
    """Plain sentences for whichever figures actually moved.

    Empty when nothing moved much, which should be the common case. A flag that
    fires every match is not a flag, and the catalog asks for plain-language
    flags precisely so that the ones that appear are worth reading.

    Deliberately not coloured good or bad. A side that tightened up was
    well-drilled or was pinned in its own half, and this number cannot tell the
    difference — the same reason `coach.js` refuses to tone the compactness row.
    """
    if drift is None:
        return []

    notes = []
    for key, (grew, shrank) in DRIFT_TEXT.items():
        change = drift.change(key)
        if change is None or abs(change) < min_change_m:
            continue
        template = grew if change > 0 else shrank
        notes.append(template.format(n=abs(change)))
    return notes
