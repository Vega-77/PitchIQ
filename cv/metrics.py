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


def movement_stats(
    series: PositionSeries,
    max_speed_ms: float = MAX_PLAUSIBLE_SPEED_MS,
    sprint_threshold_ms: float = SPRINT_THRESHOLD_MS,
) -> MovementStats:
    """Distance, speed and sprints from a smoothed series."""
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

    return stats


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
