"""Movement metrics, with synthetic motion so the right answer is known.

These are the numbers the halftime report leads with, and they are the easiest
in the project to get quietly wrong — a plausible-looking distance figure that
is mostly accumulated noise looks exactly like a correct one. Each test below
constructs motion whose true answer is calculable by hand.
"""

from __future__ import annotations

import numpy as np
import pytest

from cv.metrics import (
    MAX_PLAUSIBLE_SPEED_MS,
    PositionSeries,
    heatmap,
    movement_stats,
    smooth_positions,
    team_shape,
)


def series(points, fps: float = 25.0, track_id: int = 1) -> PositionSeries:
    arr = np.asarray(points, dtype=np.float64)
    times = np.arange(len(arr)) / fps
    return PositionSeries(track_id, times, arr)


def straight_line(distance_m: float, speed_ms: float, fps: float = 25.0):
    """A player jogging in a straight line at a constant speed."""
    steps = int(distance_m / speed_ms * fps)
    step_m = distance_m / steps
    return [(i * step_m, 34.0) for i in range(steps + 1)]


class TestDistance:
    def test_measures_a_known_straight_run(self):
        stats = movement_stats(series(straight_line(100.0, 5.0)))
        assert stats.distance_m == pytest.approx(100.0, rel=0.01)

    def test_a_stationary_player_covers_no_ground(self):
        stats = movement_stats(series([(50.0, 34.0)] * 100))
        assert stats.distance_m == pytest.approx(0.0, abs=1e-9)
        assert stats.top_speed_ms == pytest.approx(0.0, abs=1e-9)

    def test_returning_to_the_start_still_counts_the_journey(self):
        # Out 20m and back: 40m covered, zero net displacement.
        # Steps are 0.2m at 25fps (5 m/s) — a jog, not a teleport, so the
        # plausibility ceiling leaves it alone.
        out = [(i * 0.2, 34.0) for i in range(101)]
        back = [(20.0 - i * 0.2, 34.0) for i in range(1, 101)]
        stats = movement_stats(series(out + back))
        assert stats.distance_m == pytest.approx(40.0, rel=0.01)


class TestJitter:
    """The failure mode that makes these numbers fiction if unhandled."""

    def test_raw_jitter_fabricates_distance(self):
        rng = np.random.default_rng(0)
        still = np.array([[50.0, 34.0]] * 250)
        jittered = still + rng.normal(0, 0.15, still.shape)

        stats = movement_stats(series(jittered))

        # A player who never moved appears to have run a long way.
        assert stats.distance_m > 30.0, (
            "this test exists to prove the problem is real; if it fails, "
            "the jitter model is too gentle to be representative"
        )

    def test_smoothing_removes_most_of_it(self):
        rng = np.random.default_rng(0)
        still = np.array([[50.0, 34.0]] * 250)
        jittered = series(still + rng.normal(0, 0.15, still.shape))

        raw = movement_stats(jittered).distance_m
        smoothed = movement_stats(smooth_positions(jittered, window=9)).distance_m

        assert smoothed < raw * 0.35, (
            f"smoothing barely helped: {raw:.1f}m -> {smoothed:.1f}m"
        )

    def test_smoothing_keeps_real_movement(self):
        """Noise suppression must not eat the signal."""
        rng = np.random.default_rng(1)
        path = np.array(straight_line(100.0, 5.0))
        noisy = series(path + rng.normal(0, 0.1, path.shape))

        smoothed = movement_stats(smooth_positions(noisy, window=9))
        assert smoothed.distance_m == pytest.approx(100.0, rel=0.10)

    def test_smoothing_is_a_no_op_on_short_tracks(self):
        short = series([(0.0, 0.0), (1.0, 0.0)])
        assert len(smooth_positions(short)) == 2


class TestSpeed:
    def test_top_speed_matches_a_known_pace(self):
        stats = movement_stats(series(straight_line(100.0, 8.0)))
        assert stats.top_speed_ms == pytest.approx(8.0, rel=0.02)
        assert stats.top_speed_kmh == pytest.approx(28.8, rel=0.02)

    def test_teleports_are_discarded_not_counted(self):
        """An identity switch would otherwise dominate every total."""
        path = [(i, 34.0) for i in range(20)]
        path.append((95.0, 34.0))          # track jumps to the far end
        path += [(95.0 + i, 34.0) for i in range(1, 10)]

        stats = movement_stats(series(path))

        assert stats.discarded_frames >= 1
        assert stats.top_speed_ms <= MAX_PLAUSIBLE_SPEED_MS
        # ~19m before the jump plus ~9m after, and crucially not the 75m jump.
        assert stats.distance_m < 40.0

    def test_a_clean_track_discards_nothing(self):
        stats = movement_stats(series(straight_line(100.0, 6.0)))
        assert stats.discarded_frames == 0


class TestSprints:
    def test_counts_a_sustained_burst(self):
        jog = [(i * 0.12, 34.0) for i in range(50)]           # ~3 m/s
        start = jog[-1][0]
        sprint = [(start + i * 0.36, 34.0) for i in range(1, 76)]  # ~9 m/s, 3s

        stats = movement_stats(series(jog + sprint))
        assert stats.sprint_count == 1
        assert stats.sprint_distance_m > 20.0

    def test_ignores_a_momentary_spike(self):
        # One fast frame is noise, not a sprint.
        path = [(i * 0.12, 34.0) for i in range(40)]
        path.append((path[-1][0] + 0.4, 34.0))
        path += [(path[-1][0] + i * 0.12, 34.0) for i in range(1, 20)]

        assert movement_stats(series(path)).sprint_count == 0

    def test_counts_two_separate_bursts(self):
        def burst(x0, n=60):
            return [(x0 + i * 0.36, 34.0) for i in range(n)]

        def jog(x0, n=40):
            return [(x0 + i * 0.08, 34.0) for i in range(n)]

        path = burst(0.0) + jog(21.6) + burst(24.8)
        assert movement_stats(series(path)).sprint_count == 2


class TestHeatmap:
    def test_sums_to_one(self):
        grid = heatmap(series(straight_line(100.0, 5.0)), 105.0, 68.0)
        assert grid.sum() == pytest.approx(1.0)

    def test_concentrates_where_the_player_was(self):
        grid = heatmap(series([(10.0, 10.0)] * 50), 105.0, 68.0, bins=(10, 8))
        assert grid[0][1] == pytest.approx(1.0)

    def test_empty_series_is_all_zero(self):
        empty = PositionSeries(1, np.empty(0), np.empty((0, 2)))
        assert heatmap(empty, 105.0, 68.0).sum() == 0.0


class TestTeamShape:
    def test_measures_a_known_formation(self):
        # A flat back four across the pitch, 30m apart end to end.
        players = {
            i: series([(20.0, 20.0 + i * 10.0)] * 10, track_id=i)
            for i in range(4)
        }
        shape = team_shape(players)

        assert shape["width_m"] == pytest.approx(30.0, abs=0.5)
        assert shape["depth_m"] == pytest.approx(0.0, abs=0.5)

    def test_a_compact_side_scores_lower_than_a_stretched_one(self):
        compact = {
            i: series([(50.0 + i, 34.0 + i)] * 10, track_id=i) for i in range(5)
        }
        stretched = {
            i: series([(20.0 + i * 15, 10.0 + i * 12)] * 10, track_id=i)
            for i in range(5)
        }

        assert (
            team_shape(compact)["compactness_m"]
            < team_shape(stretched)["compactness_m"]
        )

    def test_needs_enough_players_to_mean_anything(self):
        two = {i: series([(50.0, 34.0)] * 5, track_id=i) for i in range(2)}
        assert team_shape(two)["width_m"] == 0.0

    def test_handles_no_players(self):
        assert team_shape({})["width_m"] == 0.0
