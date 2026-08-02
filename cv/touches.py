"""When somebody touched the ball. Everything else is built on this.

A pass is two touches by the same team. An interception is two touches by
different teams with the ball in flight between them. A tackle is the same but
with the ball under control first. A shot is a touch followed by the ball
heading at a goal. Get touches right and the Opta taxonomy is mostly geometry;
get them wrong and every count downstream is wrong in the same direction.

    A touch is the conjunction of two signals, and neither works alone.

*Proximity alone* fires constantly. A player running alongside the ball sits
inside any sensible radius for a second at a time; a defender standing near a
rolling ball produces a clean minimum having done nothing. High recall,
useless precision.

*Motion change alone* fires on bounces, on the post, on the turf, and on the
seams where interpolated ball positions meet real ones — all with nobody near.

So a touch is: the ball changed what it was doing, **and** a specific player
was close enough to have been the cause.

    Scale. Nothing here is measured in pixels.

Radii are in player heights and speeds in player heights per second, extending
the convention `cv/possession.py` established, because the camera zooms and a
pixel is worth a different distance every second. This module goes one step
further and uses a *local* scale — the players nearest the ball — rather than
the frame median. On a wide shot a near-touchline player is three times the
pixel height of a far one, so a frame median is far too generous at the top of
the picture and far too mean at the bottom.

    The ceiling, stated plainly.

Every threshold below is a guess. Not an estimate — a guess, chosen to be
reasonable and never yet checked against a human watching the same video. The
synthetic tests prove the algorithm does what this docstring says; they say
nothing about whether what it says is what a football touch looks like.

On the footage available today it will find almost nothing, and that is the
correct outcome rather than a bug: the ball is detected in about 60% of frames,
and on the one clip where it is detected at all, the nearest player to a
"ball" sits a median of six player heights away — those detections are mostly
false positives. This becomes meaningful at higher ball recall from a camera
that holds still.

**No touch count should be shown to anyone before it has been checked against
scrubbed video.** `cv/experiments/touch_report.py` exists to make that cheap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .frames import FrameTable
from .possession import median_player_height
from .teams import UNKNOWN

# How close a player must be for a plain proximity minimum to count as a touch,
# in player heights. Roughly a stride.
TOUCH_RADIUS_PH = 1.2

# The wider radius, allowed only when the ball also visibly changed direction or
# speed. This is what catches a hard strike where the ball has already left the
# foot by the frame the detector managed to see it in.
LOOSE_RADIUS_PH = 2.0

# The motion test has a floor, and it is never zero. A ball rolling past a
# player who is standing still produces a textbook proximity minimum and is not
# a touch; without a floor here that fires once per player per pass, which is
# hundreds of phantom touches a match. Close range only buys a *lower* bar for
# how much the ball has to have changed, never no bar at all.
MIN_MOTION_CLOSE = 0.25
MIN_MOTION_LOOSE = 0.5

# A direction change of this many degrees scores full marks on the motion test.
TURN_THRESHOLD_DEG = 30.0

# A speed change of this many player heights per second likewise. A trap that
# kills the ball scores here as readily as a strike that launches it, which is
# why the magnitude is used unsigned.
ACCEL_THRESHOLD_PH_S = 3.0

# Frames either side used to estimate the ball's velocity before and after.
VELOCITY_WINDOW = 4

# Two touches by the same player closer together than this are one touch seen
# twice. Applied per track and never globally — see segment_touches.
MIN_SEPARATION_S = 0.25

# An unobserved run longer than this is a gap: too long to believe a straight
# line through it.
MAX_GAP_FRAMES = 12

# Players sampled around the ball to establish the local pixel scale.
LOCAL_SCALE_PLAYERS = 3


@dataclass(frozen=True)
class TouchConfidence:
    """Why we believe a touch happened, kept in parts rather than collapsed.

    The scalar is what downstream code filters on, but the components are what
    a human tunes thresholds from. Throwing them away would mean the only way
    to find out why a touch scored badly is to run the whole pipeline again
    with a debugger attached.
    """

    proximity: float        # how close the player was, against TOUCH_RADIUS_PH
    motion_change: float    # how much the ball's motion changed
    observation: float      # how much of the evidence was seen rather than filled in
    separation: float       # how much closer than the next-nearest player

    # Proximity and motion are the two halves of the definition, so they carry
    # the most. Separation carries as much as observation because in a crowded
    # box the nearest player is close to a coin flip, and that is the whole
    # difference between "shot by the striker" and "shot by the defender
    # marking him".
    WEIGHTS = (0.30, 0.30, 0.20, 0.20)

    @property
    def score(self) -> float:
        wp, wm, wo, ws = self.WEIGHTS
        return (
            wp * self.proximity
            + wm * self.motion_change
            + wo * self.observation
            + ws * self.separation
        )


@dataclass(frozen=True)
class Touch:
    frame_index: int
    timestamp_s: float
    track_id: int
    team: str
    ball_xy: tuple[float, float]                 # pixels, always present
    ball_m: tuple[float, float] | None           # metres, only when calibrated
    scale_px: float                              # one player height, in pixels
    distance_ph: float
    speed_before_ph_s: float
    speed_after_ph_s: float
    turn_deg: float
    observed: bool
    components: TouchConfidence

    @property
    def confidence(self) -> float:
        return self.components.score

    @property
    def speed_gain_ph_s(self) -> float:
        return self.speed_after_ph_s - self.speed_before_ph_s


@dataclass
class TouchSequence:
    """Touches in time order, plus the spans where we could not have seen one."""

    touches: list[Touch] = field(default_factory=list)
    gaps: list[tuple[float, float]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.touches)

    def __iter__(self):
        return iter(self.touches)

    def between(self, start_s: float, end_s: float) -> list[Touch]:
        return [t for t in self.touches if start_s <= t.timestamp_s <= end_s]

    def by_track(self, track_id: int) -> list[Touch]:
        return [t for t in self.touches if t.track_id == track_id]

    def by_team(self, team: str) -> list[Touch]:
        return [t for t in self.touches if t.team == team]

    def counts_by_track(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for touch in self.touches:
            counts[touch.track_id] = counts.get(touch.track_id, 0) + 1
        return counts

    def gap_between(self, a: Touch, b: Touch) -> bool:
        """Whether an unseen span sits between two touches.

        This is the guard that stops the event layer inventing a completed pass
        from A to C when the ball actually went A to B to C and B was never
        seen. Any consumer joining two touches into one event must ask.
        """
        lo, hi = sorted((a.timestamp_s, b.timestamp_s))
        return any(start < hi and end > lo for start, end in self.gaps)

    def confidence_percentile(self, percentile: float) -> float:
        if not self.touches:
            return 0.0
        return float(np.percentile([t.confidence for t in self.touches], percentile))


# ---------------------------------------------------------------- scale


def local_scale_px(
    ball_xy: tuple[float, float],
    boxes: list[tuple[int, tuple[float, float, float, float]]],
    k: int = LOCAL_SCALE_PLAYERS,
) -> float:
    """Median height of the k players nearest the ball, in pixels.

    The frame median is the wrong scale wherever the ball happens to be. On a
    wide shot a player at the near touchline is three times the pixel height of
    one at the far side, so a frame-median radius is far too generous at the top
    of the picture and far too mean at the bottom — and the ball spends most of
    a match somewhere other than the average depth.

    The players closest to the ball are the ones standing at roughly the ball's
    own depth in frame, so their height is the local pixels-per-metre. Falls
    back to the frame median when there are too few players to choose from.
    """
    if not boxes:
        return 0.0
    if len(boxes) <= k:
        return median_player_height(boxes)

    def ground_distance(entry) -> float:
        _, (x1, _, x2, y2) = entry
        return math.dist(ball_xy, ((x1 + x2) / 2, y2))

    nearest = sorted(boxes, key=ground_distance)[:k]
    return median_player_height(nearest)


def _two_nearest(
    ball_xy: tuple[float, float],
    boxes: list[tuple[int, tuple[float, float, float, float]]],
) -> tuple[int | None, float, float]:
    """(track_id, nearest_distance_px, second_nearest_distance_px).

    The second distance is what makes attribution honest: two players equally
    close to the ball means we do not know which of them touched it, however
    certain the nearest-player calculation looks.
    """
    best_id: int | None = None
    best = second = float('inf')

    for track_id, (x1, _, x2, y2) in boxes:
        # Ground point: the ball is on the floor, so compare against the feet
        # rather than the middle of the body.
        distance = math.dist(ball_xy, ((x1 + x2) / 2, y2))
        if distance < best:
            second = best
            best, best_id = distance, track_id
        elif distance < second:
            second = distance

    return best_id, best, second


# ---------------------------------------------------------------- the pass


def _ball_series(table: FrameTable):
    """Frames that have a ball, as parallel arrays."""
    records = [r for r in table.records if r.ball_xy is not None]
    return (
        records,
        np.array([r.timestamp_s for r in records], dtype=np.float64),
        np.array([r.ball_xy for r in records], dtype=np.float64),
        np.array([r.ball_observed for r in records], dtype=bool),
    )


def find_gaps(table: FrameTable, max_gap_frames: int = MAX_GAP_FRAMES):
    """Spans of time where the ball was not actually seen.

    Counts both a missing ball and an interpolated one, because they are the
    same fact: nobody looked at those pixels and found a ball. Interpolation
    fills a straight line, and a straight line has zero curvature by
    construction — so a touch inside an interpolated span is invisible to the
    motion test no matter how hard it was struck.
    """
    spans: list[tuple[float, float]] = []
    run_start: float | None = None
    run_length = 0
    last_time = 0.0

    for record in table.records:
        seen = record.ball_xy is not None and record.ball_observed
        if seen:
            if run_start is not None and run_length > max_gap_frames:
                spans.append((run_start, last_time))
            run_start, run_length = None, 0
        else:
            if run_start is None:
                run_start = record.timestamp_s
            run_length += 1
            last_time = record.timestamp_s

    if run_start is not None and run_length > max_gap_frames:
        spans.append((run_start, last_time))

    return spans


def _velocities(times, points, scales, window: int):
    """Ball velocity just before and just after each sample, in player heights/s.

    Two one-sided windows rather than one centred derivative. A centred
    derivative straddles the touch and averages the before and after together,
    smearing away exactly the discontinuity being looked for.
    """
    n = len(times)
    before = np.zeros((n, 2))
    after = np.zeros((n, 2))

    for i in range(n):
        lo = max(0, i - window)
        hi = min(n - 1, i + window)
        scale = scales[i] if scales[i] > 0 else 1.0

        dt_before = times[i] - times[lo]
        if dt_before > 0:
            before[i] = (points[i] - points[lo]) / dt_before / scale

        dt_after = times[hi] - times[i]
        if dt_after > 0:
            after[i] = (points[hi] - points[i]) / dt_after / scale

    return before, after


def _motion_change(v_before, v_after, turn_threshold_deg: float, accel_threshold: float):
    """How much the ball's motion changed, as (score in 0..1, turn in degrees)."""
    speed_before = float(np.hypot(*v_before))
    speed_after = float(np.hypot(*v_after))

    if speed_before < 1e-6 or speed_after < 1e-6:
        turn_deg = 0.0
    else:
        cosine = float(np.dot(v_before, v_after)) / (speed_before * speed_after)
        turn_deg = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))

    turn_score = min(1.0, turn_deg / turn_threshold_deg) if turn_threshold_deg else 0.0
    # Unsigned: a trap that kills the ball is as much a touch as a strike that
    # launches it.
    gain_score = (
        min(1.0, abs(speed_after - speed_before) / accel_threshold)
        if accel_threshold else 0.0
    )
    return max(turn_score, gain_score), turn_deg, speed_before, speed_after


def segment_touches(
    table: FrameTable,
    touch_radius_ph: float = TOUCH_RADIUS_PH,
    loose_radius_ph: float = LOOSE_RADIUS_PH,
    min_motion_close: float = MIN_MOTION_CLOSE,
    min_motion_loose: float = MIN_MOTION_LOOSE,
    turn_threshold_deg: float = TURN_THRESHOLD_DEG,
    accel_threshold_ph_s: float = ACCEL_THRESHOLD_PH_S,
    velocity_window: int = VELOCITY_WINDOW,
    min_separation_s: float = MIN_SEPARATION_S,
    max_gap_frames: int = MAX_GAP_FRAMES,
    is_player=None,
) -> TouchSequence:
    """Find the moments somebody touched the ball.

    Expects `table.records` to carry ball positions already — run
    `frames.attach_trajectory` first.

    `is_player` keeps anyone not in the match out of the attribution. It matters
    more here than almost anywhere: a touch is credited to the nearest figure,
    and a referee trailing play is regularly the nearest figure to the ball.
    """
    records, times, points, observed = _ball_series(table)
    sequence = TouchSequence(gaps=find_gaps(table, max_gap_frames))

    if len(records) < 3:
        return sequence

    # Scale, proximity and attribution, one frame at a time.
    scales = np.zeros(len(records))
    nearest_ph = np.full(len(records), np.inf)
    second_ph = np.full(len(records), np.inf)
    holders: list[int | None] = []

    for i, record in enumerate(records):
        boxes = record.boxes()
        if is_player is not None:
            boxes = [b for b in boxes if is_player(b[0])]
        scale = local_scale_px(record.ball_xy, boxes)
        scales[i] = scale

        holder, best_px, second_px = _two_nearest(record.ball_xy, boxes)
        holders.append(holder)
        if holder is not None and scale > 0:
            nearest_ph[i] = best_px / scale
            second_ph[i] = second_px / scale

    v_before, v_after = _velocities(times, points, scales, velocity_window)

    # A local minimum is the smallest distance within a window as wide as the
    # suppression interval. Defining it that way rather than as "smaller than
    # its two neighbours" means detector jitter cannot manufacture a minimum
    # every third frame.
    fps = table.fps or 30.0
    radius = max(1, int(round(min_separation_s * fps / 2)))

    def build_touch(i, motion, turn_deg, speed_before, speed_after) -> Touch:
        record = records[i]
        distance = float(nearest_ph[i])
        window = observed[max(0, i - velocity_window):i + velocity_window + 1]
        margin = second_ph[i] - distance if np.isfinite(second_ph[i]) else 1.0
        return Touch(
            frame_index=record.frame_index,
            timestamp_s=record.timestamp_s,
            track_id=holders[i],
            team=table.team_of(holders[i]),
            ball_xy=record.ball_xy,
            ball_m=table.to_pitch(record.ball_xy),
            # Carried so distances *between* touches can be normalised too —
            # pass length has the same zoom problem as touch radius.
            scale_px=float(scales[i]),
            distance_ph=distance,
            speed_before_ph_s=speed_before,
            speed_after_ph_s=speed_after,
            turn_deg=turn_deg,
            observed=bool(observed[i]),
            components=TouchConfidence(
                proximity=max(0.0, min(1.0, 1.0 - distance / touch_radius_ph)),
                motion_change=motion,
                observation=float(window.mean()) if len(window) else 0.0,
                separation=max(0.0, min(1.0, margin)),
            ),
        )

    candidates: list[Touch] = []

    # ---- touches we actually saw ----
    for i in range(len(records)):
        # Interpolated frames are handled separately below. Their motion is an
        # artefact of the straight line drawn through them, not a measurement.
        if not observed[i] or holders[i] is None or not np.isfinite(nearest_ph[i]):
            continue

        lo, hi = max(0, i - radius), min(len(records), i + radius + 1)
        if nearest_ph[i] > np.min(nearest_ph[lo:hi]):
            continue

        motion, turn_deg, speed_before, speed_after = _motion_change(
            v_before[i], v_after[i], turn_threshold_deg, accel_threshold_ph_s
        )

        # The conjunction. Being close is not enough on its own, and neither is
        # the ball having changed — a touch is both, with the tight radius
        # buying a lower motion bar rather than none.
        distance = float(nearest_ph[i])
        close_enough = distance <= touch_radius_ph and motion >= min_motion_close
        loose_but_moving = distance <= loose_radius_ph and motion >= min_motion_loose
        if not (close_enough or loose_but_moving):
            continue

        candidates.append(build_touch(i, motion, turn_deg, speed_before, speed_after))

    # ---- touches we can only infer ----
    candidates.extend(_gap_touches(
        records, observed, nearest_ph, holders, v_before, v_after,
        build_touch=build_touch,
        max_gap_frames=max_gap_frames,
        touch_radius_ph=touch_radius_ph,
        min_motion=min_motion_loose,
        turn_threshold_deg=turn_threshold_deg,
        accel_threshold_ph_s=accel_threshold_ph_s,
    ))

    sequence.touches = _suppress(candidates, min_separation_s)
    return sequence


def _unobserved_runs(observed):
    """Maximal runs of consecutive frames where the ball was not really seen."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, seen in enumerate(observed):
        if seen:
            if start is not None:
                runs.append((start, i - 1))
                start = None
        elif start is None:
            start = i
    if start is not None:
        runs.append((start, len(observed) - 1))
    return runs


def _gap_touches(
    records, observed, nearest_ph, holders, v_before, v_after, *,
    build_touch, max_gap_frames, touch_radius_ph, min_motion,
    turn_threshold_deg, accel_threshold_ph_s,
):
    """At most one touch per interpolated span, and only if the ball changed.

    Roughly 40% of frames have no real ball detection, so refusing to look
    inside interpolated spans would throw away a large share of the match. But
    the straight line cv/ball.py draws through a gap has zero curvature by
    construction, so the per-frame motion test is blind in there and running it
    anyway would just read confidence off the interpolation.

    What survives is the comparison *across* the span: if the ball entered
    travelling one way and left travelling another, something touched it in
    between, even though no frame shows it. That justifies exactly one touch —
    attributed to whoever was closest, flagged unobserved, and scored with no
    motion credit and no observation credit at all.

    Long spans are excluded entirely. Over a second of unseen play several
    touches may have happened, and picking one of them would be fiction rather
    than inference; those become TouchSequence.gaps instead, which is how the
    event layer knows not to join across them.
    """
    out: list[Touch] = []

    for start, end in _unobserved_runs(observed):
        if end - start + 1 > max_gap_frames:
            continue
        if start == 0 or end == len(records) - 1:
            continue                     # no before or no after to compare

        motion, turn_deg, speed_before, speed_after = _motion_change(
            v_before[start - 1], v_after[end + 1],
            turn_threshold_deg, accel_threshold_ph_s,
        )
        if motion < min_motion:
            continue                     # the ball carried on doing what it was doing

        inside = [
            i for i in range(start, end + 1)
            if holders[i] is not None and nearest_ph[i] <= touch_radius_ph
        ]
        if not inside:
            continue

        i = min(inside, key=lambda j: nearest_ph[j])
        touch = build_touch(i, 0.0, turn_deg, speed_before, speed_after)
        out.append(touch)

    return out


def _suppress(candidates: list[Touch], min_separation_s: float) -> list[Touch]:
    """Thin repeated detections of one touch, per track rather than globally.

    Per track matters. A tackle is genuinely two players touching the ball a
    fifth of a second apart, and global suppression would delete one of them —
    which is precisely the event the layer above most needs to see. A dribbler
    taking the ball forward, by contrast, produces a candidate every few frames
    and should collapse to one.
    """
    kept: list[Touch] = []
    last_by_track: dict[int, Touch] = {}

    for touch in sorted(candidates, key=lambda t: t.timestamp_s):
        previous = last_by_track.get(touch.track_id)
        if previous is not None and touch.timestamp_s - previous.timestamp_s < min_separation_s:
            if touch.confidence > previous.confidence:
                kept[kept.index(previous)] = touch
                last_by_track[touch.track_id] = touch
            continue
        kept.append(touch)
        last_by_track[touch.track_id] = touch

    return kept


def summarise(sequence: TouchSequence) -> str:
    """A line for the console. Leads with the caveat rather than burying it."""
    if not sequence.touches:
        return 'no touches found'

    known = sum(1 for t in sequence.touches if t.team != UNKNOWN)
    return (
        f'{len(sequence.touches)} touches, {known} with a known team, '
        f'confidence p50 {sequence.confidence_percentile(50):.2f} '
        f'p10 {sequence.confidence_percentile(10):.2f}, '
        f'{len(sequence.gaps)} unseen spans'
    )
