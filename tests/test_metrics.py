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
    ShapeDrift,
    drift_notes,
    shape_drift,
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


class TestShapeDrift:
    """Whether the shape held, which is a different question from what it was.

    The catalog asks for "team compactness dropped in the last 15 minutes" as a
    plain sentence. That needs two averages either side of a split rather than a
    single mean over the window, and it needs to refuse to answer when there is
    not enough on one side to average.
    """

    def spread_apart(self, n=200, start_gap=4.0, end_gap=20.0):
        """Four players who begin tight and end strung out across the pitch."""
        by_track = {}
        for track, offset in enumerate([-1.5, -0.5, 0.5, 1.5]):
            points = []
            for i in range(n):
                gap = start_gap + (end_gap - start_gap) * i / (n - 1)
                points.append((52.0, 34.0 + offset * gap))
            by_track[track] = series(points, track_id=track)
        return by_track

    def steady(self, n=200):
        return {
            track: series([(52.0, 34.0 + offset * 8.0)] * n, track_id=track)
            for track, offset in enumerate([-1.5, -0.5, 0.5, 1.5])
        }

    def test_a_side_that_spread_out_is_measured_as_wider_late(self):
        drift = shape_drift(self.spread_apart())
        assert drift is not None
        assert drift.late['width_m'] > drift.early['width_m']
        assert drift.change('width_m') > 0

    def test_a_side_that_held_its_shape_shows_no_meaningful_change(self):
        drift = shape_drift(self.steady())
        assert abs(drift.change('width_m')) < 0.5

    def test_it_refuses_to_answer_on_too_short_a_clip(self):
        """None, not a zero drift. Two averages over three snapshots each
        describe a moment, and reporting that as "no change" is a claim nobody
        measured."""
        assert shape_drift(self.steady(n=6)) is None

    def test_an_explicit_split_is_honoured(self):
        """So a caller can ask about the last fifteen minutes specifically,
        rather than only about the halfway point of whatever it ran on.

        Asserted on where the two windows sit, not on the size of the gap
        between them: on a side spreading at a steady rate the gap is the same
        wherever you cut, which is a property of the ramp rather than of this
        function.
        """
        by_track = self.spread_apart(n=400)
        cut_late = shape_drift(by_track, split_s=12.0)
        cut_early = shape_drift(by_track, split_s=4.0)

        assert cut_late.split_s == 12.0
        # A later cut means both windows sit later, so both are wider.
        assert cut_late.late['width_m'] > cut_early.late['width_m']
        assert cut_late.early['width_m'] > cut_early.early['width_m']

    def test_no_players_gives_nothing_rather_than_zeroes(self):
        assert shape_drift({}) is None

    def test_it_serialises_with_the_change_alongside_the_two_halves(self):
        data = shape_drift(self.spread_apart()).to_json()
        assert set(data) == {'early', 'late', 'split_s', 'change'}
        assert data['change']['width_m'] == pytest.approx(
            data['late']['width_m'] - data['early']['width_m'], abs=0.11,
        )


class TestDriftNotes:
    def test_it_says_which_way_a_figure_moved(self):
        drift = ShapeDrift(
            early={'width_m': 30.0, 'depth_m': 20.0, 'compactness_m': 10.0},
            late={'width_m': 40.0, 'depth_m': 20.0, 'compactness_m': 10.0},
            split_s=100.0,
        )
        notes = drift_notes(drift)
        assert notes == ['spread 10m wider']

    def test_shrinking_reads_differently_from_growing(self):
        drift = ShapeDrift(
            early={'width_m': 40.0, 'depth_m': 20.0, 'compactness_m': 10.0},
            late={'width_m': 30.0, 'depth_m': 20.0, 'compactness_m': 10.0},
            split_s=100.0,
        )
        assert drift_notes(drift) == ['squeezed 10m narrower']

    def test_a_small_move_is_not_worth_a_sentence(self):
        """A flag that fires every match is not a flag."""
        drift = ShapeDrift(
            early={'width_m': 30.0, 'depth_m': 20.0, 'compactness_m': 10.0},
            late={'width_m': 31.0, 'depth_m': 20.5, 'compactness_m': 10.2},
            split_s=100.0,
        )
        assert drift_notes(drift) == []

    def test_every_figure_that_moved_gets_its_own_sentence(self):
        drift = ShapeDrift(
            early={'width_m': 30.0, 'depth_m': 20.0, 'compactness_m': 8.0},
            late={'width_m': 40.0, 'depth_m': 32.0, 'compactness_m': 14.0},
            split_s=100.0,
        )
        assert len(drift_notes(drift)) == 3

    def test_nothing_to_describe_is_an_empty_list_not_a_crash(self):
        assert drift_notes(None) == []
