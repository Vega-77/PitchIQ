"""Possession sequences and the phase-of-play funnel.

The catalog asked for phase-of-play and left its shape open. These tests are
where the shape is decided, so they are mostly about the definition rather than
the arithmetic: what counts as one possession, what "reached the final third"
means, and which of the several plausible denominators each figure uses.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_sequences.py -q
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from cv import zones
from cv.events import COMPLETED, INCOMPLETE, EventLog, Pass, Shot, DefensiveAction
from cv.pitch import Pitch
from cv.sequences import (
    ENDED_LOST,
    ENDED_SHOT,
    ENDED_STOPPED,
    MAX_GAP_S,
    phase_of_play,
    sequences,
)
from cv.teams import TEAM_A, TEAM_B

PITCH = Pitch()
ENDS = {TEAM_A: 'right', TEAM_B: 'left'}

# Pitch is 105m long, so the thirds break at 35 and 70. Team A attacks right, so
# for them "own third" is x < 35 and the final third is x > 70.
OWN = 20.0
MID = 52.5
FINAL = 90.0


def a_pass(t, x, team=TEAM_A, outcome=COMPLETED, in_play=True):
    return Pass(
        event_id=f'p{t}', type='pass', timestamp_s=float(t), frame_index=int(t * 30),
        team=team, track_id=1, start_xy_px=(0.0, 0.0), start_m=(x, 34.0),
        outcome=outcome, in_play=in_play,
    )


def a_shot(t, x, team=TEAM_A):
    return Shot(
        event_id=f's{t}', type='shot', timestamp_s=float(t), frame_index=int(t * 30),
        team=team, track_id=1, start_xy_px=(0.0, 0.0), start_m=(x, 34.0),
    )


def a_tackle(t, x, team=TEAM_B):
    return DefensiveAction(
        event_id=f'd{t}', type='tackle', timestamp_s=float(t), frame_index=int(t * 30),
        team=team, track_id=9, start_xy_px=(0.0, 0.0), start_m=(x, 34.0),
    )


def log_of(*events):
    return EventLog(events=list(events))


def split(*events, **kwargs):
    return sequences(log_of(*events), PITCH, ENDS, **kwargs)


class TestWhatCountsAsOnePossession:
    def test_consecutive_touches_by_one_team_are_one_spell(self):
        spells = split(a_pass(0, OWN), a_pass(1, MID), a_pass(2, FINAL))
        assert len(spells) == 1
        assert spells[0].events == 3

    def test_the_other_team_doing_something_ends_it(self):
        spells = split(a_pass(0, OWN), a_tackle(1, MID), a_pass(2, MID))
        assert [s.team for s in spells] == [TEAM_A, TEAM_B, TEAM_A]

    def test_a_long_gap_ends_it_even_for_the_same_team(self):
        # Two touches half a minute apart are two spells with something
        # unrecorded in between. Welding them together would invent a move.
        spells = split(a_pass(0, OWN), a_pass(0 + MAX_GAP_S + 1, MID))
        assert len(spells) == 2

    def test_a_gap_inside_the_window_does_not(self):
        spells = split(a_pass(0, OWN), a_pass(MAX_GAP_S - 0.5, MID))
        assert len(spells) == 1

    def test_the_ball_going_dead_ends_it(self):
        # Only a tagged log knows this. A throw-in starts a new possession
        # however cleanly it is taken.
        spells = split(
            a_pass(0, OWN), a_pass(1, MID),
            a_pass(2, MID, in_play=False), a_pass(3, FINAL),
        )
        assert len(spells) == 2
        assert spells[1].events == 2

    def test_a_restart_and_what_follows_it_stay_together(self):
        # The other half of that rule. The throw-in and the move it begins are
        # one possession; splitting on dead-to-live as well would make every
        # restart a spell of exactly one event.
        spells = split(a_pass(0, MID, in_play=False), a_pass(1, FINAL))
        assert len(spells) == 1

    def test_an_event_with_no_position_is_skipped_not_a_boundary(self):
        # The homography failing on one frame is not the defence winning the
        # ball, and treating it as one would shred every spell it touched.
        blind = replace(a_pass(1, MID), start_m=None)
        spells = split(a_pass(0, OWN), blind, a_pass(2, FINAL))
        assert len(spells) == 1
        assert spells[0].events == 2


class TestHowFarItGot:
    def test_reached_is_the_furthest_point_not_the_last_one(self):
        # A move worked to the byline and pulled back to the penalty spot
        # reached the final third. Scoring it by its last touch says otherwise.
        spells = split(a_pass(0, OWN), a_pass(1, FINAL), a_pass(2, MID))
        assert spells[0].reached_third == zones.ATTACKING_THIRD

    def test_where_it_started_is_where_it_started(self):
        spells = split(a_pass(0, OWN), a_pass(1, FINAL))
        assert spells[0].start_third == zones.DEFENSIVE_THIRD

    def test_thirds_are_read_from_each_team_s_own_direction(self):
        # The same square metre is one team's own third and the other's final
        # third. A funnel that got this wrong would be exactly inverted.
        theirs = split(a_pass(0, OWN, team=TEAM_B))
        assert theirs[0].start_third == zones.ATTACKING_THIRD
        ours = split(a_pass(0, OWN))
        assert ours[0].start_third == zones.DEFENSIVE_THIRD


class TestHowItEnded:
    def test_a_move_with_a_shot_in_it_ended_in_a_shot(self):
        spells = split(a_pass(0, MID), a_shot(1, FINAL))
        assert spells[0].ended == ENDED_SHOT

    def test_a_shot_beats_a_giveaway_after_it(self):
        # A rebound played away is not a failed move. It got the shot off, which
        # is what the move was for.
        spells = split(a_shot(0, FINAL), a_pass(1, FINAL, outcome=INCOMPLETE))
        assert spells[0].ended == ENDED_SHOT

    def test_an_incomplete_last_pass_is_a_loss(self):
        spells = split(a_pass(0, OWN), a_pass(1, MID, outcome=INCOMPLETE))
        assert spells[0].ended == ENDED_LOST

    def test_anything_else_is_stopped_and_that_is_not_lost(self):
        # The ball going out, the clip ending, the tracker losing it. Calling
        # these turnovers would blame the defence for the detector's blind
        # spots, which is why turnovers_by_third counts only incomplete passes.
        spells = split(a_pass(0, OWN), a_pass(1, MID))
        assert spells[0].ended == ENDED_STOPPED


class TestTheFunnel:
    def build(self, *events, team=TEAM_A):
        log = log_of(*events)
        return phase_of_play(
            sequences(log, PITCH, ENDS), log, PITCH, team, ENDS[team],
        )

    def test_reaching_is_cumulative(self):
        # Everything that reached the final third also reached midfield, so the
        # funnel is monotonic and can be drawn as one.
        report = self.build(a_pass(0, OWN), a_pass(1, FINAL))
        assert report.reached[zones.DEFENSIVE_THIRD] == 1
        assert report.reached[zones.MIDDLE_THIRD] == 1
        assert report.reached[zones.ATTACKING_THIRD] == 1

    def test_a_move_that_never_left_the_back_reaches_only_the_back(self):
        report = self.build(a_pass(0, OWN), a_pass(1, OWN + 5))
        assert report.reached[zones.DEFENSIVE_THIRD] == 1
        assert report.reached[zones.MIDDLE_THIRD] == 0

    def test_the_share_reaching_is_out_of_everything_started(self):
        report = self.build(
            a_pass(0, OWN), a_pass(1, FINAL),        # reaches
            a_tackle(2, MID),                        # theirs, ends ours
            a_pass(20, OWN), a_pass(21, OWN + 2),    # does not
        )
        assert report.total == 2
        assert report.share_reaching(zones.ATTACKING_THIRD) == pytest.approx(0.5)

    def test_playing_out_from_the_back_has_its_own_denominator(self):
        # The whole-funnel share is flattered by every possession that started
        # in midfield already. "Can we play out from the back" is a question
        # about the ones that did not.
        report = self.build(
            a_pass(0, OWN), a_pass(1, MID),           # out from the back
            a_tackle(2, MID),
            a_pass(20, OWN), a_pass(21, OWN + 2),     # stuck in it
            a_tackle(22, OWN),
            a_pass(40, MID), a_pass(41, FINAL),       # started upfield
        )
        assert report.total == 3
        # Two started at the back, one escaped — not two of three.
        assert report.out_of_defence() == (2, 1)

    def test_a_team_that_never_had_it_at_the_back_says_nothing(self):
        # Not zero. A side that never won the ball in its own third did not
        # fail to play out from it.
        report = self.build(a_pass(0, MID), a_pass(1, FINAL))
        assert report.out_of_defence() is None

    def test_shots_are_counted_as_an_ending(self):
        report = self.build(
            a_pass(0, MID), a_shot(1, FINAL),
            a_tackle(2, FINAL),
            a_pass(20, MID), a_pass(21, MID, outcome=INCOMPLETE),
        )
        assert report.ended[ENDED_SHOT] == 1
        assert report.ended[ENDED_LOST] == 1


class TestPassingByPhase:
    def build(self, *events, team=TEAM_A):
        log = log_of(*events)
        return phase_of_play(
            sequences(log, PITCH, ENDS), log, PITCH, team, ENDS[team],
        )

    def test_accuracy_is_split_by_where_the_pass_was_played_from(self):
        # The number this whole feature exists for. 92% at the back and 55% in
        # the final third is a normal, healthy side; one overall figure of 78%
        # hides both halves of that and 60% at the back is a problem nobody
        # would see.
        report = self.build(
            a_pass(0, OWN), a_pass(1, OWN + 1), a_pass(2, OWN + 2),
            a_pass(3, OWN + 3, outcome=INCOMPLETE),
            a_tackle(4, OWN),
            a_pass(20, FINAL), a_pass(21, FINAL, outcome=INCOMPLETE),
        )
        assert report.accuracy_in(zones.DEFENSIVE_THIRD) == pytest.approx(0.75)
        assert report.accuracy_in(zones.ATTACKING_THIRD) == pytest.approx(0.5)

    def test_a_third_nobody_passed_in_has_no_accuracy(self):
        report = self.build(a_pass(0, OWN))
        assert report.accuracy_in(zones.ATTACKING_THIRD) is None

    def test_one_possession_can_feed_three_thirds(self):
        # Passes and possessions are different denominators, which is why they
        # are counted separately rather than rolled up off the sequences.
        report = self.build(a_pass(0, OWN), a_pass(1, MID), a_pass(2, FINAL))
        assert report.total == 1
        assert sum(report.passes.values()) == 3


class TestWhatCannotBeMeasured:
    def test_no_calibration_means_no_phases_rather_than_empty_ones(self):
        # A run with no homography did not watch a team that never attacked.
        assert sequences(log_of(a_pass(0, OWN)), None, ENDS) is None

    def test_nor_does_an_unknown_attacking_end(self):
        assert sequences(log_of(a_pass(0, OWN)), PITCH, {TEAM_A: None}) is None

    def test_a_team_with_no_possessions_at_all_answers_none(self):
        log = log_of(a_pass(0, OWN, team=TEAM_A))
        assert phase_of_play(
            sequences(log, PITCH, ENDS), log, PITCH, TEAM_B, 'left',
        ) is None

    def test_one_team_being_unreadable_does_not_take_the_other_down(self):
        # Half a homography is still half an answer. The team whose direction is
        # known keeps its funnel.
        ends = {TEAM_A: 'right', TEAM_B: None}
        log = log_of(a_pass(0, OWN), a_tackle(1, MID), a_pass(2, MID))
        spells = sequences(log, PITCH, ends)
        assert [s.team for s in spells] == [TEAM_A, TEAM_A]
        assert phase_of_play(spells, log, PITCH, TEAM_B, None) is None
