"""Where each team had the ball, not just how much.

The distinction this exists for: a side pinned in its own half can hold 60% of
the ball and be losing badly, and every possession figure this pipeline produced
before now would have called that a good half.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_territory.py -q
"""

import pytest

from cv.pitch import Pitch
from cv.possession import DEAD, FrameState, prepare_states
from cv.teams import TEAM_A, TEAM_B, UNKNOWN
from cv.territory import TerritorySplit, pinned_back, territory
from cv.zones import ATTACKING_THIRD, DEFENSIVE_THIRD, MIDDLE_THIRD

PITCH = Pitch()
L, W = PITCH.length_m, PITCH.width_m
MID_Y = W / 2

# Team A attacks right, Team B attacks left — so the same x means opposite
# thirds for the two of them, which is the whole point of naming a third from
# the holder's own direction.
ENDS = {TEAM_A: 'right', TEAM_B: 'left'}


def at(t, team, x_m, y_m=MID_Y):
    return FrameState(
        timestamp_s=t, holder_track=1, team=team,
        distance_px=10.0, ball_m=(x_m, y_m),
    )


def run(states, ends=None):
    return territory(states, PITCH, ends or ENDS)


class TestSplittingByThird:
    def test_time_in_the_attacking_third_is_counted_there(self):
        split = run([at(0.0, TEAM_A, 95.0), at(1.0, TEAM_A, 96.0)])
        assert split.seconds[TEAM_A][ATTACKING_THIRD] == pytest.approx(1.0)
        assert split.share(TEAM_A, ATTACKING_THIRD) == 1.0

    def test_a_third_is_named_from_the_holder_own_direction(self):
        """The same spot on the pitch is Team A's attacking third and Team B's
        defensive third, and both sentences are the one a coach wants."""
        split = run([
            at(0.0, TEAM_A, 95.0), at(1.0, TEAM_A, 95.0),
            at(2.0, TEAM_B, 95.0), at(3.0, TEAM_B, 95.0),
        ])
        assert split.share(TEAM_A, ATTACKING_THIRD) == 1.0
        assert split.share(TEAM_B, DEFENSIVE_THIRD) == 1.0

    def test_the_middle_third_is_the_middle(self):
        split = run([at(0.0, TEAM_A, L / 2), at(1.0, TEAM_A, L / 2)])
        assert split.share(TEAM_A, MIDDLE_THIRD) == 1.0

    def test_shares_across_the_thirds_add_to_one(self):
        split = run([
            at(0.0, TEAM_A, 10.0), at(1.0, TEAM_A, 10.0),
            at(2.0, TEAM_A, 52.0), at(3.0, TEAM_A, 95.0),
        ])
        total = sum(split.share(TEAM_A, t) for t in
                    (DEFENSIVE_THIRD, MIDDLE_THIRD, ATTACKING_THIRD))
        assert total == pytest.approx(1.0)


class TestTimeNotFrames:
    def test_elapsed_seconds_are_counted_not_frames(self):
        """`stride` is a documented speed lever, so a frame count would make the
        answer depend on how fast the run was told to go."""
        dense = run([at(i * 0.1, TEAM_A, 95.0) for i in range(11)])
        sparse = run([at(i * 0.5, TEAM_A, 95.0) for i in range(3)])

        assert dense.total_s(TEAM_A) == pytest.approx(1.0)
        assert sparse.total_s(TEAM_A) == pytest.approx(1.0)

    def test_a_frame_out_of_order_contributes_nothing(self):
        """Rather than a negative interval quietly cancelling real time."""
        split = run([at(5.0, TEAM_A, 95.0), at(1.0, TEAM_A, 95.0)])
        assert split.total_s(TEAM_A) == 0.0


class TestWhatIsExcluded:
    def test_contested_time_belongs_to_nobody(self):
        """The conservative reading. Attributing a scramble to whoever was
        marginally nearer is the thing possession smoothing exists to avoid."""
        split = run([at(0.0, UNKNOWN, 95.0), at(1.0, UNKNOWN, 95.0)])
        assert split.total_s(TEAM_A) == 0.0
        assert split.total_s(TEAM_B) == 0.0

    def test_dead_ball_time_is_not_territory(self):
        """A throw-in taken in your attacking third is not time spent there."""
        split = run([at(0.0, DEAD, 95.0), at(1.0, DEAD, 95.0)])
        assert split.total_s(TEAM_A) == 0.0

    def test_frames_with_no_position_are_skipped_not_guessed(self):
        states = [
            FrameState(0.0, 1, TEAM_A, 10.0, ball_m=None),
            FrameState(1.0, 1, TEAM_A, 10.0, ball_m=None),
        ]
        assert run(states).total_s(TEAM_A) == 0.0

    def test_a_team_with_no_known_direction_is_skipped(self):
        """Without a side mapping there is no attacking end, so a third cannot
        be named — and naming it anyway would be a coin flip."""
        split = run(
            [at(0.0, TEAM_A, 95.0), at(1.0, TEAM_A, 95.0)],
            ends={TEAM_A: None, TEAM_B: None},
        )
        assert split.total_s(TEAM_A) == 0.0


class TestAbsence:
    def test_a_team_that_never_held_it_has_no_split_rather_than_zeroes(self):
        """Zero in each third would say the ball was spread evenly across a
        pitch it never touched."""
        split = run([at(0.0, TEAM_A, 95.0), at(1.0, TEAM_A, 95.0)])
        assert split.share(TEAM_B, ATTACKING_THIRD) is None
        assert split.to_json()[TEAM_B] is None

    def test_an_empty_run_produces_an_empty_split(self):
        assert territory([], PITCH, ENDS).seconds == {}
        assert TerritorySplit().to_json()[TEAM_A] is None

    def test_one_state_is_not_an_interval(self):
        assert run([at(0.0, TEAM_A, 95.0)]).total_s(TEAM_A) == 0.0


class TestAgreeingWithPossession:
    def test_it_reads_the_same_labels_possession_does(self):
        """Territory built from raw per-frame answers while possession used
        smoothed ones would produce two figures about one half that disagree.

        One stray frame inside a run of Team A possession: smoothing votes it
        away, and territory must not still be counting it for Team B.
        """
        raw = [at(i * 0.1, TEAM_A, 95.0) for i in range(20)]
        raw[10] = at(1.0, TEAM_B, 95.0)

        prepared = prepare_states(raw, smooth_window=5)
        split = territory(prepared, PITCH, ENDS)

        assert split.total_s(TEAM_B) == 0.0
        assert split.total_s(TEAM_A) > 1.5

    def test_smoothing_keeps_the_ball_position(self):
        """A field added to FrameState and dropped by smooth_states would leave
        territory silently empty on every real run."""
        prepared = prepare_states(
            [at(i * 0.1, TEAM_A, 95.0) for i in range(20)], smooth_window=5,
        )
        assert all(s.ball_m is not None for s in prepared)


class TestSerialisation:
    def test_the_json_carries_shares_and_the_seconds_behind_them(self):
        data = run([
            at(0.0, TEAM_A, 10.0), at(1.0, TEAM_A, 10.0), at(2.0, TEAM_A, 95.0),
        ]).to_json()

        assert data[TEAM_A][DEFENSIVE_THIRD] == pytest.approx(0.5)
        assert data[TEAM_A][ATTACKING_THIRD] == pytest.approx(0.5)
        assert data[TEAM_A]['seconds'] == pytest.approx(2.0)

    def test_it_round_trips(self):
        import json

        data = run([at(0.0, TEAM_A, 95.0), at(1.0, TEAM_A, 95.0)]).to_json()
        assert json.loads(json.dumps(data))[TEAM_A][ATTACKING_THIRD] == 1.0


class TestPinnedBack:
    def test_it_says_so_when_a_team_is_pinned_in_its_own_third(self):
        split = run([
            at(0.0, TEAM_A, 10.0), at(1.0, TEAM_A, 10.0), at(2.0, TEAM_A, 95.0),
        ])
        assert '50%' in pinned_back(split, TEAM_A)

    def test_it_stays_quiet_when_there_is_nothing_to_say(self):
        """The common case. A flag that fires every match is not a flag."""
        split = run([at(0.0, TEAM_A, 95.0), at(1.0, TEAM_A, 95.0)])
        assert pinned_back(split, TEAM_A) is None

    def test_a_team_with_no_possession_produces_no_claim(self):
        assert pinned_back(TerritorySplit(), TEAM_A) is None
