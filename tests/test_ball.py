"""Ball trajectory tests.

The ball is tracked separately from players because pushing it through a
multi-object tracker discarded 97% of real detections. These tests construct
synthetic candidate sets — sparse, noisy, with false positives — so the right
answer is known, and check the path-finding recovers it.
"""

from __future__ import annotations

import math

import pytest

from cv.ball import (
    BallCandidate,
    build_trajectory,
    nearest_player,
)

FPS = 30.0
FRAME_W = 1280


def cand(frame: int, x: float, y: float, conf: float = 0.5) -> BallCandidate:
    return BallCandidate(frame, frame / FPS, (x, y), conf)


def straight_path(n: int, x0=100.0, y0=400.0, dx=8.0, dy=0.0, conf=0.5, keep=None):
    """A ball moving steadily. `keep` drops frames to simulate missed detections."""
    out = []
    for i in range(n):
        if keep is not None and i not in keep:
            continue
        out.append(cand(i, x0 + dx * i, y0 + dy * i, conf))
    return out


def by_frame(candidates):
    grouped = {}
    for c in candidates:
        grouped.setdefault(c.frame_index, []).append(c)
    return grouped


class TestBasicTracking:
    def test_follows_a_clean_path(self):
        traj = build_trajectory(by_frame(straight_path(30)), FRAME_W)

        assert len(traj) == 30
        assert traj.observed_count == 30
        assert traj.interpolated_count == 0

    def test_empty_input_gives_empty_trajectory(self):
        assert len(build_trajectory({}, FRAME_W)) == 0

    def test_a_single_detection_survives(self):
        traj = build_trajectory(by_frame([cand(5, 100, 400)]), FRAME_W)
        assert len(traj) == 1
        assert traj.points[0].observed


class TestGapFilling:
    def test_fills_short_gaps(self):
        # Seen on frames 0-4 and 10-14; frames 5-9 missing.
        keep = set(range(5)) | set(range(10, 15))
        traj = build_trajectory(by_frame(straight_path(15, keep=keep)), FRAME_W)

        assert len(traj) == 15, "the gap should be bridged"
        assert traj.observed_count == 10
        assert traj.interpolated_count == 5

    def test_interpolated_points_are_on_the_line(self):
        keep = {0, 1, 2, 8, 9, 10}
        traj = build_trajectory(by_frame(straight_path(11, keep=keep)), FRAME_W)

        mid = traj.at_frame(5)
        assert mid is not None and not mid.observed
        # Straight-line motion at dx=8 from x0=100 puts frame 5 at x=140.
        assert mid.xy[0] == pytest.approx(140.0, abs=1.0)

    def test_refuses_to_bridge_a_long_gap(self):
        # Two seconds apart — far too long to assume a straight line.
        candidates = [cand(0, 100, 400), cand(1, 108, 400), cand(90, 900, 400)]
        traj = build_trajectory(by_frame(candidates), FRAME_W, max_gap_s=1.0)

        assert traj.interpolated_count == 0
        assert len(traj) <= 2, "the far detection must not be chained across 3s"

    def test_recovers_most_frames_from_sparse_detections(self):
        """The real-world case: detector sees the ball ~60% of the time."""
        keep = {i for i in range(60) if i % 5 != 0 and i % 7 != 0}
        traj = build_trajectory(by_frame(straight_path(60, keep=keep)), FRAME_W)

        assert traj.coverage(60) > 0.95, (
            f"only recovered {traj.coverage(60):.0%} of frames from "
            f"{len(keep)/60:.0%} detection"
        )


class TestRobustness:
    def test_rejects_an_impossible_jump(self):
        # A detection on the far side of the frame in the next frame is a
        # different object, not a ball that teleported.
        candidates = straight_path(10)
        candidates.append(cand(5, 1270.0, 50.0, conf=0.9))

        traj = build_trajectory(by_frame(candidates), FRAME_W)

        point = traj.at_frame(5)
        assert point is not None
        assert point.xy[0] < 400, "the outlier should not have been chosen"

    def test_prefers_the_longer_explanation(self):
        """A greedy tracker fails this; the DP should not.

        One very confident false positive sits alone, while the true ball has a
        long chain of less confident detections.
        """
        candidates = straight_path(25, conf=0.3)
        candidates.append(cand(12, 1200.0, 80.0, conf=0.99))

        traj = build_trajectory(by_frame(candidates), FRAME_W)

        assert len(traj) >= 20, "should follow the long chain, not the lone spike"
        point = traj.at_frame(12)
        assert point.xy[0] < 400

    def test_picks_between_same_frame_alternatives(self):
        # Two candidates in one frame: one continues the path, one does not.
        candidates = straight_path(20)
        candidates.append(cand(10, 900.0, 120.0, conf=0.8))

        traj = build_trajectory(by_frame(candidates), FRAME_W)

        point = traj.at_frame(10)
        assert point.xy == pytest.approx((180.0, 400.0), abs=1.0)

    def test_survives_positional_noise(self):
        import random

        rng = random.Random(3)
        candidates = [
            cand(i, 100 + 8 * i + rng.uniform(-4, 4), 400 + rng.uniform(-4, 4))
            for i in range(40)
        ]
        traj = build_trajectory(by_frame(candidates), FRAME_W)

        assert len(traj) == 40


class TestPossessionPrimitive:
    def test_finds_the_closest_player(self):
        players = [
            (1, (100.0, 300.0, 140.0, 400.0)),   # ground point (120, 400)
            (2, (500.0, 300.0, 540.0, 400.0)),   # ground point (520, 400)
        ]
        track_id, distance = nearest_player((130.0, 400.0), players)

        assert track_id == 1
        assert distance == pytest.approx(10.0, abs=0.1)

    def test_measures_from_the_feet_not_the_middle(self):
        # A player box 200px tall: using the centre would misjudge by ~100px.
        players = [(1, (100.0, 200.0, 140.0, 400.0))]
        _, distance = nearest_player((120.0, 400.0), players)

        assert distance == pytest.approx(0.0, abs=0.1)

    def test_handles_no_players(self):
        track_id, distance = nearest_player((100.0, 100.0), [])
        assert track_id is None
        assert math.isinf(distance)
