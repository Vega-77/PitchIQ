"""Track the ball, which is not the same problem as tracking players.

Measured on 30s of real footage: the detector finds the ball in 60% of frames,
but pushing those detections through a multi-object tracker yields 1.6%. MOT
trackers confirm a track only when detections associate consistently across
consecutive frames, and a small, fast, low-confidence object never clears that
bar — so almost every real detection is discarded as unconfirmed.

The fix is to stop treating the ball as one object among many. There is exactly
one ball, which removes the data-association problem that MOT exists to solve
and leaves a much easier one: choose the most plausible path through a sparse,
noisy set of candidates. That is a shortest-path problem, solved here with
dynamic programming over candidates, then interpolation across the gaps.

Everything here works in pixels, because ball tracking has to function before
calibration exists (and on this footage, calibration may not be possible at
all). Speed gates are therefore expressed as a fraction of frame width per
second rather than in metres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# A long pass can cross most of the frame in about a second. Anything faster is
# two different objects being mistaken for one ball.
MAX_BALL_SPEED_FRAC_PER_S = 1.2

# Beyond this the ball has been unseen too long to trust a straight line through
# the gap — a real ball gets kicked, and interpolating over a bounce invents a
# path it never took.
MAX_INTERPOLATION_GAP_S = 1.0

# How close together, and for how long, detections have to sit before they are
# read as scenery rather than as the ball.
#
# This exists because of how the scorer below is shaped. A link costs
# `distance / (max_speed * dt)`, so a detection at the same pixel as the last
# one costs nothing at all, and a white object bolted to the ground is
# redetected in every single frame. Scenery therefore chains unbroken at very
# nearly the full reward, while the real ball moves — paying motion cost — and
# is seen intermittently. Left alone, the scorer prefers the marker disc to the
# match.
#
# It is not a hypothetical. Over one checked minute of real footage a 24px cell
# held candidates for 29 seconds, another for 18.7; the contact sheet showed
# five unbroken seconds ringed on a stationary white speck on empty grass while
# play was happening elsewhere in the frame. Those cells were 13% of the
# candidates and most of the chosen path.
#
# The span is set well past any real dwell. A ball can sit still — waiting on a
# goal kick, trapped under a foot — and the cost of dropping those frames is
# small, because a ball that is not moving is not in play and no event is being
# missed. The cost of keeping the scenery is the whole trajectory.
#
# Frame space, not world space, so a camera pan moves fixed objects and hides
# them from this filter. That is the safe direction to fail in: a pan makes the
# filter quieter, never more aggressive.
STATIC_RADIUS_PX = 20.0
STATIC_SPAN_S = 4.0
STATIC_MIN_HITS = 25


@dataclass(frozen=True)
class BallCandidate:
    frame_index: int
    timestamp_s: float
    xy: tuple[float, float]
    confidence: float


@dataclass(frozen=True)
class BallPoint:
    frame_index: int
    timestamp_s: float
    xy: tuple[float, float]
    observed: bool          # False when filled in across a gap
    confidence: float = 0.0


@dataclass
class BallTrajectory:
    points: list[BallPoint] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.points)

    @property
    def observed_count(self) -> int:
        return sum(1 for p in self.points if p.observed)

    @property
    def interpolated_count(self) -> int:
        return len(self.points) - self.observed_count

    def coverage(self, total_frames: int) -> float:
        return len(self.points) / total_frames if total_frames else 0.0

    def at_frame(self, frame_index: int) -> BallPoint | None:
        for p in self.points:
            if p.frame_index == frame_index:
                return p
        return None

    def as_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        times = np.array([p.timestamp_s for p in self.points])
        xy = np.array([p.xy for p in self.points])
        return times, xy


def candidates_from_detections(
    detections_by_frame: dict[int, list],
    timestamps: dict[int, float],
) -> dict[int, list[BallCandidate]]:
    """Pull ball detections out of per-frame detector output."""
    out: dict[int, list[BallCandidate]] = {}
    for frame_index, detections in detections_by_frame.items():
        balls = [d for d in detections if getattr(d, "label", None) == "ball"]
        if not balls:
            continue
        out[frame_index] = [
            BallCandidate(
                frame_index=frame_index,
                timestamp_s=timestamps.get(frame_index, 0.0),
                xy=d.center,
                confidence=d.confidence,
            )
            for d in balls
        ]
    return out


def drop_static_candidates(
    candidates: list[BallCandidate],
    radius_px: float = STATIC_RADIUS_PX,
    span_s: float = STATIC_SPAN_S,
    min_hits: int = STATIC_MIN_HITS,
) -> list[BallCandidate]:
    """Remove detections that are part of something standing still.

    A candidate is scenery if enough other candidates sit within `radius_px` of
    it and the earliest and latest of them are more than `span_s` apart. Both
    conditions matter: a handful of hits over a long window is a ball passing
    through the same place twice, and a dense burst over half a second is a
    ball being controlled.

    Judged per candidate against its own neighbourhood rather than by binning
    the frame, so an object sitting on a bin boundary is not split in two and
    thereby saved.
    """
    if len(candidates) < min_hits or radius_px <= 0:
        return list(candidates)

    xy = np.array([c.xy for c in candidates], dtype=np.float64)
    times = np.array([c.timestamp_s for c in candidates], dtype=np.float64)

    keep = np.ones(len(candidates), dtype=bool)
    radius_sq = radius_px * radius_px

    for i in range(len(candidates)):
        near = ((xy[:, 0] - xy[i, 0]) ** 2 + (xy[:, 1] - xy[i, 1]) ** 2) <= radius_sq
        hits = int(near.sum())
        if hits < min_hits:
            continue
        window = times[near]
        if float(window.max() - window.min()) >= span_s:
            keep[i] = False

    return [c for c, k in zip(candidates, keep) if k]


def build_trajectory(
    candidates_by_frame: dict[int, list[BallCandidate]],
    frame_width: int,
    max_gap_s: float = MAX_INTERPOLATION_GAP_S,
    max_speed_frac: float = MAX_BALL_SPEED_FRAC_PER_S,
    static_radius_px: float = STATIC_RADIUS_PX,
    static_span_s: float = STATIC_SPAN_S,
    static_min_hits: int = STATIC_MIN_HITS,
) -> BallTrajectory:
    """Choose the most plausible path through the candidates, then fill gaps.

    Dynamic programming rather than greedy chaining: a greedy pass commits to
    whichever detection comes first, and one early false positive drags the
    whole path off course with no way to recover. The DP scores complete paths,
    so a wrong-looking start loses to a better overall explanation.
    """
    flat: list[BallCandidate] = []
    for frame_index in sorted(candidates_by_frame):
        flat.extend(candidates_by_frame[frame_index])

    # Before scoring, not after: the scorer's preference for a motionless chain
    # is strong enough that a single static source drags the whole path onto it,
    # and there is nothing left downstream to undo that.
    flat = drop_static_candidates(
        flat, static_radius_px, static_span_s, static_min_hits,
    )

    if not flat:
        return BallTrajectory()
    if len(flat) == 1:
        c = flat[0]
        return BallTrajectory([
            BallPoint(c.frame_index, c.timestamp_s, c.xy, True, c.confidence)
        ])

    max_speed_px = max_speed_frac * frame_width

    # Scored as a maximisation, not a cost minimisation. Cost accumulates
    # monotonically along a path, so under a minimising objective starting a
    # fresh single-point path is always cheaper than extending an existing one
    # and no chain ever forms. Rewarding each link instead makes a long,
    # smooth, confident path the best-scoring explanation, which is what we
    # actually want.
    LINK_REWARD = 1.0

    score = [c.confidence for c in flat]
    prev: list[int | None] = [None] * len(flat)

    for i, cand in enumerate(flat):
        for j in range(i):
            other = flat[j]
            dt = cand.timestamp_s - other.timestamp_s
            if dt <= 0:
                continue                      # same frame: alternatives, not a step
            if dt > max_gap_s:
                continue                      # too far apart to link
            distance = math.dist(cand.xy, other.xy)
            if distance / dt > max_speed_px:
                continue                      # physically impossible

            # Smooth motion costs little; a near-teleport costs nearly the whole
            # reward. Longer gaps are penalised so a continuous chain beats a
            # sparse one covering the same span.
            motion_cost = distance / max(max_speed_px * dt, 1e-6)
            gap_cost = dt * 0.5
            candidate_score = (
                score[j] + LINK_REWARD + cand.confidence - motion_cost - gap_cost
            )

            if candidate_score > score[i]:
                score[i] = candidate_score
                prev[i] = j

    end = max(range(len(flat)), key=lambda i: score[i])

    chain: list[BallCandidate] = []
    cursor: int | None = end
    while cursor is not None:
        chain.append(flat[cursor])
        cursor = prev[cursor]
    chain.reverse()

    return _interpolate(chain, max_gap_s)


def _interpolate(chain: list[BallCandidate], max_gap_s: float) -> BallTrajectory:
    """Straight-line fill between consecutive picks, where the gap is short."""
    points: list[BallPoint] = []

    for index, cand in enumerate(chain):
        points.append(
            BallPoint(cand.frame_index, cand.timestamp_s, cand.xy, True, cand.confidence)
        )

        if index + 1 >= len(chain):
            break

        nxt = chain[index + 1]
        missing = nxt.frame_index - cand.frame_index - 1
        gap_s = nxt.timestamp_s - cand.timestamp_s

        if missing <= 0 or gap_s > max_gap_s:
            continue

        for step in range(1, missing + 1):
            t = step / (missing + 1)
            points.append(
                BallPoint(
                    frame_index=cand.frame_index + step,
                    timestamp_s=cand.timestamp_s + gap_s * t,
                    xy=(
                        cand.xy[0] + (nxt.xy[0] - cand.xy[0]) * t,
                        cand.xy[1] + (nxt.xy[1] - cand.xy[1]) * t,
                    ),
                    observed=False,
                )
            )

    return BallTrajectory(points)


def nearest_player(
    ball_xy: tuple[float, float],
    player_boxes: list[tuple[int, tuple[float, float, float, float]]],
) -> tuple[int | None, float]:
    """Which tracked player is closest to the ball, and how far in pixels.

    Compares against each player's ground point rather than box centre, since
    the ball is on the floor. This is the primitive possession is built from —
    but note that at the fragmentation levels measured in Phase 6, the track id
    it returns is not a stable player identity, so it supports team-level
    possession long before it supports per-player touch counts.
    """
    best_id: int | None = None
    best_distance = float("inf")

    for track_id, (x1, _, x2, y2) in player_boxes:
        ground = ((x1 + x2) / 2, y2)
        distance = math.dist(ball_xy, ground)
        if distance < best_distance:
            best_distance = distance
            best_id = track_id

    return best_id, best_distance
