"""Pitch geography, and the mirroring that makes it dangerous.

Almost every function in cv/zones.py takes an end to measure against, and the
failure mode when that end is wrong is silence: a clearance out of defence
reads as a final-third entry, a shot from your own box reads as a tap-in. The
numbers stay plausible. So the bulk of this file is the same assertion made
twice, once for each end, which is the only way that class of bug shows up.

Pure geometry, no video and no calibration — which is the point. The footage
that would let these run for real does not exist yet, so these tests are what
stands behind the module until it does.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_zones.py -q
"""

from __future__ import annotations

import pytest

from cv.pitch import GOAL_WIDTH_M, PENALTY_AREA_LENGTH_M, Pitch
from cv.zones import (
    ATTACKING_THIRD,
    DEFENSIVE_THIRD,
    MIDDLE_THIRD,
    PROGRESSIVE_CROSSING_HALFWAY_M,
    PROGRESSIVE_OPPONENT_HALF_M,
    PROGRESSIVE_OWN_HALF_M,
    angle_to_goal,
    channel,
    crosses_goal_line,
    defensive_line_m,
    distance_to_goal,
    enters_goal_mouth,
    in_final_third,
    in_goal_area,
    in_penalty_area,
    in_shot_cone,
    in_wide_channel,
    is_cross,
    is_progressive,
    is_switch,
    leaves_play,
    opposite,
    progressive_distance,
    third,
    third_boundaries,
    zone_grid,
)

PITCH = Pitch()          # 105 x 68
L, W = PITCH.length_m, PITCH.width_m
MID_Y = W / 2


class TestEndValidation:
    """A bad end must raise rather than quietly picking one.

    Defaulting would be worse than erroring: a caller who forgot the argument
    would get numbers measured from an arbitrary goal, and nothing downstream
    could tell.
    """

    @pytest.mark.parametrize('call', [
        lambda e: third(PITCH, 50.0, e),
        lambda e: in_penalty_area(PITCH, 5.0, MID_Y, e),
        lambda e: distance_to_goal(PITCH, 50.0, MID_Y, e),
        lambda e: angle_to_goal(PITCH, 50.0, MID_Y, e),
        lambda e: is_progressive(PITCH, (10.0, 34.0), (50.0, 34.0), e),
        lambda e: defensive_line_m([(10.0, 34.0)], e, PITCH),
    ])
    def test_rejects_a_nonsense_end(self, call):
        with pytest.raises(ValueError):
            call('north')

    def test_opposite_flips(self):
        assert opposite('left') == 'right'
        assert opposite('right') == 'left'


class TestThirds:
    def test_boundaries_are_exact_thirds(self):
        assert third_boundaries(PITCH) == pytest.approx((35.0, 70.0))

    def test_thirds_are_named_from_the_attacking_teams_view(self):
        assert third(PITCH, 10.0, 'right') == DEFENSIVE_THIRD
        assert third(PITCH, 52.5, 'right') == MIDDLE_THIRD
        assert third(PITCH, 95.0, 'right') == ATTACKING_THIRD

    def test_the_same_point_swaps_meaning_when_the_end_swaps(self):
        """The mirroring test, in its simplest form.

        x=10 is deep in your own half attacking right, and deep in *their* half
        attacking left. Any function that ignored the end would return the same
        answer twice.
        """
        assert third(PITCH, 10.0, 'right') == DEFENSIVE_THIRD
        assert third(PITCH, 10.0, 'left') == ATTACKING_THIRD

    def test_a_point_on_a_boundary_picks_the_forward_third(self):
        """So the three bands partition the pitch instead of overlapping."""
        assert third(PITCH, 35.0, 'right') == MIDDLE_THIRD
        assert third(PITCH, 70.0, 'right') == ATTACKING_THIRD

    def test_final_third_agrees_with_third(self):
        assert in_final_third(PITCH, 95.0, 'right')
        assert not in_final_third(PITCH, 95.0, 'left')


class TestPenaltyAndGoalAreas:
    def test_the_landmark_corners_are_inside_their_own_box(self):
        """Cross-checked against pitch.landmarks(), not against fresh arithmetic.

        Re-deriving the corners here would let both copies drift together and
        still agree, which is the failure this avoids.
        """
        marks = PITCH.landmarks()
        for name, end in [
            ('pen_left_bottom_corner', 'left'),
            ('pen_left_top_corner', 'left'),
            ('pen_right_bottom_corner', 'right'),
            ('pen_right_top_corner', 'right'),
        ]:
            x, y = marks[name]
            assert in_penalty_area(PITCH, x, y, end), name

    def test_a_metre_beyond_the_box_edge_is_outside(self):
        assert in_penalty_area(PITCH, PENALTY_AREA_LENGTH_M, MID_Y, 'left')
        assert not in_penalty_area(PITCH, PENALTY_AREA_LENGTH_M + 1.0, MID_Y, 'left')

    def test_the_box_is_at_one_end_only(self):
        assert in_penalty_area(PITCH, 5.0, MID_Y, 'left')
        assert not in_penalty_area(PITCH, 5.0, MID_Y, 'right')

    def test_goal_area_sits_inside_the_penalty_area(self):
        marks = PITCH.landmarks()
        x, y = marks['goalarea_left_top_corner']
        assert in_goal_area(PITCH, x, y, 'left')
        assert in_penalty_area(PITCH, x, y, 'left')

    def test_the_penalty_spot_is_not_in_the_six_yard_box(self):
        x, y = PITCH.landmarks()['pen_spot_left']
        assert in_penalty_area(PITCH, x, y, 'left')
        assert not in_goal_area(PITCH, x, y, 'left')


class TestChannels:
    def test_the_middle_is_not_wide(self):
        assert not in_wide_channel(PITCH, MID_Y)

    def test_the_touchlines_are_wide(self):
        assert in_wide_channel(PITCH, 1.0)
        assert in_wide_channel(PITCH, W - 1.0)

    def test_channels_span_the_width(self):
        assert channel(PITCH, 0.0) == 0
        assert channel(PITCH, W - 0.01) == 4

    def test_an_off_pitch_ball_is_clamped_rather_than_out_of_range(self):
        assert channel(PITCH, -5.0) == 0
        assert channel(PITCH, W + 5.0) == 4

    def test_zone_grid_is_clamped_at_both_extremes(self):
        assert zone_grid(PITCH, -10.0, -10.0) == (0, 0)
        assert zone_grid(PITCH, L + 10.0, W + 10.0) == (5, 4)


class TestGoalGeometry:
    def test_distance_mirrors_between_ends(self):
        """20 from one goal is L-20 from the other. The canonical mirror test."""
        assert distance_to_goal(PITCH, L - 20.0, MID_Y, 'right') == pytest.approx(20.0)
        assert distance_to_goal(PITCH, L - 20.0, MID_Y, 'left') == pytest.approx(L - 20.0)

    def test_angle_is_widest_in_front_of_goal(self):
        close = angle_to_goal(PITCH, L - 6.0, MID_Y, 'right')
        far = angle_to_goal(PITCH, L - 30.0, MID_Y, 'right')
        assert close > far

    def test_angle_is_narrower_from_a_tight_position(self):
        central = angle_to_goal(PITCH, L - 12.0, MID_Y, 'right')
        tight = angle_to_goal(PITCH, L - 12.0, 2.0, 'right')
        assert tight < central

    def test_angle_collapses_on_the_goal_line_outside_the_post(self):
        assert angle_to_goal(PITCH, L, 2.0, 'right') == pytest.approx(0.0, abs=1e-9)

    def test_angle_mirrors(self):
        right = angle_to_goal(PITCH, L - 12.0, MID_Y, 'right')
        left = angle_to_goal(PITCH, 12.0, MID_Y, 'left')
        assert right == pytest.approx(left)


class TestGoalLine:
    def test_a_ball_through_the_middle_enters_the_mouth(self):
        assert enters_goal_mouth(PITCH, (L - 10.0, MID_Y), (L + 1.0, MID_Y), 'right')

    def test_a_metre_outside_the_post_does_not(self):
        outside = MID_Y + GOAL_WIDTH_M / 2 + 1.0
        assert not enters_goal_mouth(PITCH, (L - 10.0, outside), (L + 1.0, outside), 'right')

    def test_a_ball_that_stops_short_does_not_cross(self):
        crossed, y = crosses_goal_line(PITCH, (L - 10.0, MID_Y), (L - 2.0, MID_Y), 'right')
        assert crossed is False
        assert y is None

    def test_a_ball_parallel_to_the_line_never_crosses_it(self):
        crossed, _ = crosses_goal_line(PITCH, (L, 10.0), (L, 50.0), 'right')
        assert crossed is False

    def test_crossing_the_wrong_goal_line_is_not_a_goal(self):
        """Attacking right, reaching the left line is a ball behind your own goal."""
        assert not enters_goal_mouth(PITCH, (L - 10.0, MID_Y), (L + 1.0, MID_Y), 'left')

    def test_the_y_of_the_crossing_is_interpolated(self):
        crossed, y = crosses_goal_line(PITCH, (L - 10.0, 30.0), (L + 10.0, 40.0), 'right')
        assert crossed
        assert y == pytest.approx(35.0)

    def test_leaves_play_names_the_boundary(self):
        assert leaves_play(PITCH, (50.0, 30.0), (50.0, -1.0)) == 'touchline'
        assert leaves_play(PITCH, (100.0, 30.0), (L + 1.0, 30.0)) == 'byline'
        assert leaves_play(PITCH, (50.0, 30.0), (60.0, 30.0)) is None


class TestProgression:
    def test_gain_is_signed_by_direction_of_attack(self):
        assert progressive_distance(PITCH, (10.0, 34.0), (40.0, 34.0), 'right') == 30.0
        assert progressive_distance(PITCH, (10.0, 34.0), (40.0, 34.0), 'left') == -30.0

    def test_a_backward_pass_is_never_progressive(self):
        assert not is_progressive(PITCH, (60.0, 34.0), (40.0, 34.0), 'right')

    def test_own_half_needs_the_long_threshold(self):
        """Both ends inside our own half: 30m, and 29 is not enough."""
        start = 5.0
        short = start + PROGRESSIVE_OWN_HALF_M - 1.0
        exact = start + PROGRESSIVE_OWN_HALF_M
        assert not is_progressive(PITCH, (start, 34.0), (short, 34.0), 'right')
        assert is_progressive(PITCH, (start, 34.0), (exact, 34.0), 'right')

    def test_crossing_halfway_needs_the_middle_threshold(self):
        start = L / 2 - 5.0
        exact = start + PROGRESSIVE_CROSSING_HALFWAY_M
        assert is_progressive(PITCH, (start, 34.0), (exact, 34.0), 'right')
        assert not is_progressive(
            PITCH, (start, 34.0), (start + PROGRESSIVE_CROSSING_HALFWAY_M - 1, 34.0), 'right'
        )

    def test_opponent_half_needs_only_the_short_threshold(self):
        start = L / 2 + 5.0
        exact = start + PROGRESSIVE_OPPONENT_HALF_M
        assert is_progressive(PITCH, (start, 34.0), (exact, 34.0), 'right')
        assert not is_progressive(
            PITCH, (start, 34.0), (start + PROGRESSIVE_OPPONENT_HALF_M - 1, 34.0), 'right'
        )

    def test_progression_mirrors(self):
        """The same pass, mirrored, is progressive for the other end.

        Attacking left, moving from x=100 to x=70 gains 30m — identical to
        attacking right from x=5 to x=35.
        """
        assert is_progressive(PITCH, (L - 5.0, 34.0), (L - 35.0, 34.0), 'left')
        assert not is_progressive(PITCH, (L - 5.0, 34.0), (L - 35.0, 34.0), 'right')


class TestCrossesAndSwitches:
    def test_a_wide_ball_into_the_box_is_a_cross(self):
        assert is_cross(PITCH, (95.0, 3.0), (100.0, MID_Y), 'right')

    def test_a_central_ball_into_the_box_is_not(self):
        """A square pass from the spot to the six-yard line is not a cross."""
        assert not is_cross(PITCH, (94.0, MID_Y), (100.0, MID_Y), 'right')

    def test_a_wide_ball_from_our_own_half_is_not(self):
        assert not is_cross(PITCH, (30.0, 3.0), (100.0, MID_Y), 'right')

    def test_a_cross_mirrors(self):
        assert is_cross(PITCH, (L - 95.0, 3.0), (L - 100.0, MID_Y), 'left')
        assert not is_cross(PITCH, (L - 95.0, 3.0), (L - 100.0, MID_Y), 'right')

    def test_a_switch_is_lateral_and_end_agnostic(self):
        assert is_switch((50.0, 5.0), (55.0, 60.0))
        assert not is_switch((50.0, 30.0), (90.0, 38.0))


class TestDefensiveLine:
    def test_the_line_is_the_mean_of_the_deepest_four(self):
        """A high striker must not drag the reported line up the pitch."""
        back_four = [(12.0, 20.0), (14.0, 30.0), (14.0, 40.0), (16.0, 50.0)]
        striker = [(80.0, 34.0)]
        assert defensive_line_m(back_four + striker, 'left', PITCH) == pytest.approx(14.0)

    def test_the_line_mirrors(self):
        back_four = [(L - 12.0, 20.0), (L - 14.0, 30.0), (L - 14.0, 40.0), (L - 16.0, 50.0)]
        assert defensive_line_m(back_four, 'right', PITCH) == pytest.approx(14.0)

    def test_no_players_gives_none_rather_than_zero(self):
        """Zero would read as a back line standing on its own goal line."""
        assert defensive_line_m([], 'left', PITCH) is None

    def test_fewer_players_than_asked_for_still_answers(self):
        assert defensive_line_m([(10.0, 34.0), (20.0, 34.0)], 'left', PITCH) == 15.0


class TestShotCone:
    def test_a_defender_on_the_line_of_the_shot_is_in_the_cone(self):
        assert in_shot_cone(PITCH, (L - 5.0, MID_Y), (L - 20.0, MID_Y), 'right')

    def test_a_defender_behind_the_shooter_is_not(self):
        assert not in_shot_cone(PITCH, (L - 30.0, MID_Y), (L - 20.0, MID_Y), 'right')

    def test_a_defender_wide_of_the_cone_is_not(self):
        assert not in_shot_cone(PITCH, (L - 5.0, 5.0), (L - 20.0, MID_Y), 'right')

    def test_the_cone_mirrors(self):
        assert in_shot_cone(PITCH, (5.0, MID_Y), (20.0, MID_Y), 'left')
        assert not in_shot_cone(PITCH, (5.0, MID_Y), (20.0, MID_Y), 'right')


class TestPitchSizeSensitivity:
    """Nothing may hardcode 105x68 — high-school pitches are smaller.

    The Pitch docstring already warns that distance figures inherit any error
    in these dimensions; these pin that the geometry actually reads them.
    """

    SMALL = Pitch(length_m=90.0, width_m=55.0)

    def test_thirds_follow_the_pitch(self):
        assert third_boundaries(self.SMALL) == pytest.approx((30.0, 60.0))

    def test_the_goal_moves_with_the_pitch(self):
        assert distance_to_goal(self.SMALL, 70.0, 27.5, 'right') == pytest.approx(20.0)

    def test_the_box_stays_a_fixed_real_size(self):
        """The penalty area is 16.5m by law whatever the pitch measures."""
        assert in_penalty_area(self.SMALL, 16.0, 27.5, 'left')
        assert not in_penalty_area(self.SMALL, 17.0, 27.5, 'left')
