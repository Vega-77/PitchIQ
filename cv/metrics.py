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

from .calibration import Calibration
from .tracking import Track

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


def to_pitch_series(track: Track, calib: Calibration) -> PositionSeries:
    """Project a track's boxes onto the pitch.

    Uses the bottom-centre of each box — the only point of a standing person
    that lies on the ground plane the homography maps. The box centre is
    floating in mid-air and projects metres away from the player's actual
    position.
    """
    if not track.boxes:
        return PositionSeries(track.track_id, np.empty(0), np.empty((0, 2)))

    pixels = np.array([b.ground_point for b in track.boxes], dtype=np.float64)
    times = np.array([b.timestamp_s for b in track.boxes], dtype=np.float64)
    return PositionSeries(track.track_id, times, calib.to_pitch_many(pixels))


def smooth_positions(series: PositionSeries, window: int = 5) -> PositionSeries:
    """Moving-average smoothing over the position series.

    A plain centred moving average, deliberately: a Kalman filter would model
    velocity more gracefully, but it needs tuning that cannot be validated
    without ground-truth tracks, and an untuned filter can lag hard during
    direction changes — exactly the moments that matter. This is honest about
    being crude and has no hidden parameters to get wrong.
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
    padded = np.pad(series.positions_m, ((pad, pad), (0, 0)), mode="edge")
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
