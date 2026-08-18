"""Movement metrics, with synthetic motion so the right answer is known.

These are the numbers the halftime report leads with, and they are the easiest
in the project to get quietly wrong — a plausible-looking distance figure that
is mostly accumulated noise looks exactly like a correct one. Each test below
constructs motion whose true answer is calculable by hand.
"""

from __future__ import annotations

import itertools
import statistics

import numpy as np
import pytest

from cv.metrics import (
    MAX_PLAUSIBLE_SPEED_MS,
    MIN_LINE_OUTFIELDERS,
    PositionSeries,
    heatmap,
    line_players,
    movement_stats,
    smooth_positions,
    ShapeDrift,
    drift_notes,
    shape_drift,
    team_shape,
)
from cv.pitch import Pitch
from cv.zones import defensive_line_m


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


# A 4-4-2 defending the left goal, with its back line at 20m. Used by both
# classes below: one asks whether the line is measured right when everybody is
# tracked, the other asks what happens when they are not.
BACK_FOUR = [(20.0, 12.0), (20.0, 28.0), (20.0, 40.0), (20.0, 56.0)]
MIDFIELD = [(45.0, 10.0), (45.0, 26.0), (45.0, 42.0), (45.0, 58.0)]
FRONT_TWO = [(70.0, 25.0), (70.0, 43.0)]
FOUR_FOUR_TWO = BACK_FOUR + MIDFIELD + FRONT_TWO
TRUE_LINE_M = 20.0


def standing(positions, n=30):
    """Each position held still for `n` instants, one track apiece."""
    return {i: series([p] * n, track_id=i) for i, p in enumerate(positions)}


class TestDefensiveLineHeight:
    """The fourth figure in the shape family, and the one with real teeth.

    Width and depth are descriptive; a coach who is told the wrong line height
    changes how the team defends on the strength of it. So the tests below care
    less about "is it roughly right on clean input" than about the three ways it
    can be absent and the ways it can be confidently wrong.
    """

    def test_it_finds_a_known_back_line(self):
        shape = team_shape(standing(FOUR_FOUR_TWO), 'left', Pitch())
        assert shape['line_m'] == pytest.approx(TRUE_LINE_M, abs=0.5)

    def test_it_measures_from_the_goal_being_defended(self):
        """The mirrored eleven defending the mirrored goal is the same team, so
        it reads the same height. Getting this backwards would invert every
        line height after the sides change at half time, and read as a team
        that suddenly started defending sixty metres higher."""
        pitch = Pitch()
        mirrored = [(pitch.length_m - x, y) for x, y in FOUR_FOUR_TWO]
        shape = team_shape(standing(mirrored), 'right', pitch)
        assert shape['line_m'] == pytest.approx(TRUE_LINE_M, abs=0.5)

    def test_facing_the_wrong_way_is_not_a_small_error(self):
        """Which is what makes the test above worth having: the same positions
        read against the other goal are not slightly off, they are a different
        team. Nothing downstream could spot this from the number alone."""
        pitch = Pitch()
        defending_left = team_shape(standing(FOUR_FOUR_TWO), 'left', pitch)
        defending_right = team_shape(standing(FOUR_FOUR_TWO), 'right', pitch)
        assert defending_right['line_m'] > defending_left['line_m'] + 25.0

    def test_nobody_said_which_goal_so_there_is_no_answer(self):
        """Absent, not zero. Without `side_of_team` nothing can know which end a
        colour cluster defends, and a line height of 0.0m reads as a team
        defending on its own goal line."""
        shape = team_shape(standing(FOUR_FOUR_TWO))
        assert 'line_m' not in shape
        assert shape['width_m'] > 0

    def test_too_few_tracked_to_call_it_a_line(self):
        """Five visible players are not a back four plus cover, they are five
        players. The class below measures what reporting them anyway costs."""
        few = standing(FOUR_FOUR_TWO[:MIN_LINE_OUTFIELDERS - 1])
        shape = team_shape(few, 'left', Pitch())
        assert 'line_m' not in shape
        assert shape['width_m'] > 0

    def test_one_more_tracked_player_is_enough(self):
        enough = standing(FOUR_FOUR_TWO[:MIN_LINE_OUTFIELDERS])
        assert 'line_m' in team_shape(enough, 'left', Pitch())

    def test_the_keeper_is_left_out_of_it(self):
        """A keeper stands behind the defence at every instant, so he lands in
        the deepest few every time and drags the mean back. `assign_teams`
        usually drops him into UNKNOWN long before this runs — usually is why
        the exclusion is explicit rather than assumed."""
        with_keeper = standing(FOUR_FOUR_TWO + [(4.0, 34.0)])
        keeper_track = len(FOUR_FOUR_TWO)

        dropped = team_shape(with_keeper, 'left', Pitch(), {keeper_track})
        kept = team_shape(with_keeper, 'left', Pitch())

        assert dropped['line_m'] == pytest.approx(TRUE_LINE_M, abs=0.5)
        assert kept['line_m'] < dropped['line_m'] - 1.0

    def test_a_high_line_reads_higher_than_a_deep_one(self):
        pitch = Pitch()
        pushed_up = [(x + 25.0, y) for x, y in FOUR_FOUR_TWO]
        high = team_shape(standing(pushed_up), 'left', pitch)
        deep = team_shape(standing(FOUR_FOUR_TWO), 'left', pitch)
        assert high['line_m'] > deep['line_m'] + 20.0


class TestSparseTrackingBias:
    """What a line height is worth when the tracker only saw some of them.

    This is the measurement that chose the estimator, so it is pinned here
    rather than only written down. `defensive_line_m` defaults to the deepest
    four, which is exactly right on ten tracked players and increasingly wrong
    below that: every missing defender is replaced among the deepest four by a
    midfielder twenty-five metres up the pitch, so the error only points one
    way.

    Exhaustive over every subset rather than sampled, so these are facts about
    the estimator and not about a seed.
    """

    def biases(self):
        """Mean error of both estimators at each number of tracked players."""
        pitch = Pitch()
        out = {}
        for k in range(4, len(FOUR_FOUR_TWO) + 1):
            fixed, share = [], []
            for combo in itertools.combinations(FOUR_FOUR_TWO, k):
                points = list(combo)
                fixed.append(defensive_line_m(points, 'left', pitch))
                share.append(defensive_line_m(
                    points, 'left', pitch, players=line_players(k)))
            out[k] = (statistics.mean(fixed) - TRUE_LINE_M,
                      statistics.mean(share) - TRUE_LINE_M)
        return out

    def test_the_naive_estimator_is_badly_biased_upward(self):
        """The finding, pinned as literals so any change to it is visible. A
        team with six of its ten outfielders tracked would be reported as
        defending ten metres higher than it did — a whole pitch-third of
        artefact, on a figure a coach would act on."""
        bias = self.biases()
        assert bias[4][0] == pytest.approx(20.0, abs=0.1)
        assert bias[6][0] == pytest.approx(10.0, abs=0.1)
        assert bias[9][0] == pytest.approx(2.5, abs=0.1)
        assert bias[10][0] == pytest.approx(0.0, abs=0.1)

    def test_the_bias_only_ever_points_one_way(self):
        """Which is what makes it worth correcting at all. Random error averages
        out across a half; a one-directional error accumulates into the mean,
        and tracking density varies over a match rather than staying put."""
        assert all(fixed >= -0.05 for fixed, _ in self.biases().values())

    def test_taking_a_share_instead_of_a_count_removes_most_of_it(self):
        bias = self.biases()
        for k in range(MIN_LINE_OUTFIELDERS, len(FOUR_FOUR_TWO) + 1):
            assert abs(bias[k][1]) < 2.0, (k, bias[k])
            assert abs(bias[k][1]) <= abs(bias[k][0]) + 0.05, (k, bias[k])

    def test_below_the_guard_no_share_saves_it(self):
        """Which is why there is a minimum as well as a share."""
        assert abs(self.biases()[4][1]) > 2.0

    def test_a_full_team_still_gets_the_conventional_back_four(self):
        """The share is chosen so that good tracking is untouched by any of
        this: at ten outfielders it comes to four, which is what a coach means
        by "the back line". Only sparse tracking degrades away from it."""
        assert line_players(10) == 4
        assert line_players(11) == 4

    def test_it_never_asks_for_more_players_than_it_has(self):
        assert all(1 <= line_players(k) <= k for k in range(1, 12))


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

    def dropped_off(self, n=200, start_m=30.0, end_m=15.0):
        """A ten that starts on a high line and is pushed back onto its own box.

        Only the back four move; everyone ahead of them stands still, so the
        line is the one figure with anywhere to go. A version where the whole
        team retreats would move all four figures at once and prove nothing
        about which one is being measured.
        """
        by_track = {}
        for track, (x, y) in enumerate(FOUR_FOUR_TWO):
            if (x, y) in BACK_FOUR:
                points = [
                    (start_m + (end_m - start_m) * i / (n - 1), y)
                    for i in range(n)
                ]
            else:
                points = [(x, y)] * n
            by_track[track] = series(points, track_id=track)
        return by_track

    def test_a_back_line_that_was_pushed_back_reads_as_deeper(self):
        drift = shape_drift(self.dropped_off(), defending_end='left',
                            pitch=Pitch())
        assert drift is not None
        assert drift.change('line_m') < -5.0
        assert drift.late['line_m'] < drift.early['line_m']

    def test_and_says_so_in_words(self):
        drift = shape_drift(self.dropped_off(), defending_end='left',
                            pitch=Pitch())
        assert any('deeper' in note for note in drift_notes(drift))

    def test_a_line_that_held_produces_no_note_about_it(self):
        """The other three figures barely move here either. The point is that
        a figure which did not move stays out of the sentence list rather than
        appearing as a change of nought."""
        drift = shape_drift(standing(FOUR_FOUR_TWO, n=200),
                            defending_end='left', pitch=Pitch())
        assert drift.change('line_m') == pytest.approx(0.0, abs=0.5)
        assert drift_notes(drift) == []

    def test_without_a_defended_goal_the_other_three_still_drift(self):
        """Losing the line height must not cost the drift its other figures.
        Absent, not zero, and absent for one key rather than for the report."""
        drift = shape_drift(self.dropped_off())
        assert drift is not None
        assert drift.change('line_m') is None
        assert 'line_m' not in drift.to_json()['change']
        assert drift.change('depth_m') is not None

    def half_a_side_walks_off(self, n=200):
        """Seven tracked early, four tracked late.

        Not a football event — it is what a tracker losing three players in the
        second half looks like from in here, and the tracker does lose players.
        """
        by_track = {}
        for track, (x, y) in enumerate(FOUR_FOUR_TWO[:7]):
            length = n if track < 4 else n // 2
            by_track[track] = series([(x, y)] * length, track_id=track)
        return by_track

    def test_the_line_is_dropped_on_its_own_when_the_tracking_thins_out(self):
        """The bar is applied to `line_m` separately for exactly this case: a
        window with plenty of width samples late and too few line samples. The
        width drift is still worth reporting; the line drift is not, and taking
        the whole drift away because of it would be the wrong trade."""
        drift = shape_drift(self.half_a_side_walks_off(),
                            defending_end='left', pitch=Pitch())
        assert drift is not None
        assert 'width_m' in drift.late
        assert 'line_m' not in drift.late
        assert 'line_m' not in drift.early


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
