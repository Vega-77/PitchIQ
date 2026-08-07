"""Comparing the tagged log against what the pipeline derived.

The thing worth pinning here is restraint. It would be easy to write something
that reports 95% agreement on any input by comparing generously, and that number
would be worse than no number: it would look like validation.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_reconcile.py -q
"""

import numpy as np
import pytest

from cv.ball import BallPoint, BallTrajectory
from cv.calibration import Calibration
from cv.events import Shot
from cv.phases import VideoClock
from cv.pitch import Pitch
from cv.reconcile import (
    AGREED,
    CV_ONLY,
    GOAL,
    TAG_ONLY,
    Disagreement,
    Reconciliation,
    ball_exits,
    pair_up,
    reconcile,
    tagged_times,
    warnings_for,
)
from cv.teams import TEAM_A

PITCH = Pitch()
SCALE = 20.0


def tag(kind, clock_s):
    return {'type': kind, 'matchClockS': clock_s, 'kind': 'event'}


def shot(t, outcome=GOAL):
    return Shot(
        event_id=f'shot-{t}', type='shot', timestamp_s=t, frame_index=int(t * 30),
        team=TEAM_A, track_id=9, start_xy_px=(0.0, 0.0), outcome=outcome,
    )


class _Log:
    def __init__(self, events):
        self.events = events


def calibration(scale=SCALE):
    homography = np.array([
        [1 / scale, 0.0, 0.0],
        [0.0, 1 / scale, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)
    return Calibration(homography, PITCH, image_size=(1920, 1080))


def path(points, fps=30.0):
    """[(t, x_m, y_m, observed)] -> a BallTrajectory in pixels."""
    return BallTrajectory(points=[
        BallPoint(
            frame_index=int(t * fps), timestamp_s=t,
            xy=(x * SCALE, y * SCALE), observed=observed,
        )
        for t, x, y, observed in points
    ])


class TestPairing:
    def test_the_nearest_pair_wins(self):
        pairs, cv_only, tag_only = pair_up([10.0], [12.0], 15.0)
        assert pairs == [(10.0, 12.0)]
        assert (cv_only, tag_only) == ([], [])

    def test_nothing_pairs_twice(self):
        """Two CV goals and one tag is one agreement and one extra, not two."""
        pairs, cv_only, tag_only = pair_up([10.0, 11.0], [12.0], 15.0)
        assert pairs == [(11.0, 12.0)]
        assert cv_only == [10.0]
        assert tag_only == []

    def test_outside_the_window_is_two_separate_moments(self):
        pairs, cv_only, tag_only = pair_up([10.0], [100.0], 15.0)
        assert pairs == []
        assert cv_only == [10.0]
        assert tag_only == [100.0]

    def test_empty_on_either_side_is_not_a_match(self):
        assert pair_up([], [5.0], 15.0) == ([], [], [5.0])
        assert pair_up([5.0], [], 15.0) == ([], [5.0], [])


class TestReadingTheLog:
    def test_only_the_types_asked_for(self):
        entries = [tag('goal', 100.0), tag('corner', 200.0), tag('foul', 300.0)]
        assert tagged_times(entries, {'goal'}) == [100.0]

    def test_the_offset_is_applied_once_at_the_edge(self):
        clock = VideoClock(12.0)
        assert tagged_times([tag('goal', 100.0)], {'goal'}, clock) == [112.0]

    def test_junk_is_skipped_rather_than_placed_at_zero(self):
        entries = [
            {'type': 'goal'},                       # no clock
            {'matchClockS': 10.0},                  # no type
            {'type': 'goal', 'matchClockS': True},  # a bool is not a clock
            tag('goal', 50.0),
        ]
        assert tagged_times(entries, {'goal'}) == [50.0]


class TestGoals:
    def test_a_goal_both_records_have_is_agreement(self):
        result = reconcile(_Log([shot(600.0)]), [tag('goal', 604.0)])
        assert result.counts(GOAL) == {AGREED: 1, CV_ONLY: 0, TAG_ONLY: 0}
        assert result.rate(GOAL) == 1.0

    def test_a_tagged_goal_the_pipeline_missed(self):
        result = reconcile(_Log([]), [tag('goal', 600.0)])
        assert result.counts(GOAL)[TAG_ONLY] == 1
        assert result.rate(GOAL) == 0.0

    def test_a_derived_goal_nobody_tagged(self):
        result = reconcile(_Log([shot(600.0)]), [])
        assert result.counts(GOAL)[CV_ONLY] == 1

    def test_a_saved_shot_is_not_a_goal(self):
        """Only the outcome makes a shot comparable. Counting every shot would
        produce a disagreement for each one and drown the real ones."""
        result = reconcile(_Log([shot(600.0, outcome='saved')]), [])
        assert result.counts(GOAL) == {AGREED: 0, CV_ONLY: 0, TAG_ONLY: 0}

    def test_a_late_tap_still_counts_as_the_same_goal(self):
        """Fourteen seconds is a tagger who watched the celebration first."""
        result = reconcile(_Log([shot(600.0)]), [tag('goal', 614.0)])
        assert result.rate(GOAL) == 1.0

    def test_a_minute_apart_is_two_different_moments(self):
        result = reconcile(_Log([shot(600.0)]), [tag('goal', 660.0)])
        assert result.counts(GOAL) == {AGREED: 0, CV_ONLY: 1, TAG_ONLY: 1}

    def test_the_video_offset_is_honoured(self):
        """The log is on the match clock, the pipeline on video time. Without
        this every goal in a match with an offset reads as two disagreements."""
        result = reconcile(
            _Log([shot(700.0)]), [tag('goal', 600.0)], clock=VideoClock(100.0),
        )
        assert result.rate(GOAL) == 1.0

    def test_an_agreed_goal_keeps_both_clocks(self):
        """How far apart they were is the video offset, showing itself."""
        entry = reconcile(_Log([shot(600.0)]), [tag('goal', 605.0)]).of_kind(GOAL)[0]
        assert (entry.cv_s, entry.tag_s) == (600.0, 605.0)


class TestNothingToCompare:
    def test_no_goals_anywhere_gives_no_rate_rather_than_zero(self):
        """Zero would say the records disagreed about every goal in a match
        that had none."""
        assert reconcile(_Log([]), []).rate(GOAL) is None

    def test_no_tagged_log_at_all_is_empty_not_wrong(self):
        assert reconcile(_Log([shot(1.0)]), None).counts(GOAL)[CV_ONLY] == 1

    def test_no_calibration_means_exits_were_never_checked(self):
        result = reconcile(_Log([]), [tag('corner', 10.0)])
        assert result.exits_checked is False
        assert result.rate('exit') is None
        assert result.to_json()['exits'] is None

    def test_an_empty_reconciliation_serialises(self):
        assert Reconciliation().to_json()['goal_agreement'] is None


class TestBallExits:
    def test_a_ball_crossing_the_touchline_is_one_exit(self):
        exits = ball_exits(path([
            (0.0, 50.0, 10.0, True),
            (1.0, 50.0, 2.0, True),
            (2.0, 50.0, -3.0, True),
        ]), calibration())
        assert len(exits) == 1
        assert exits[0].boundary == 'touchline'
        assert exits[0].timestamp_s == 2.0

    def test_crossing_the_byline_is_named_differently(self):
        exits = ball_exits(path([
            (0.0, 100.0, 34.0, True),
            (1.0, 107.0, 34.0, True),
        ]), calibration())
        assert [e.boundary for e in exits] == ['byline']

    def test_a_ball_that_stays_out_is_still_one_exit(self):
        """Three sightings beyond the line is one ball out of play, not three."""
        exits = ball_exits(path([
            (0.0, 50.0, 10.0, True),
            (1.0, 50.0, -2.0, True),
            (2.0, 50.0, -4.0, True),
            (3.0, 50.0, -6.0, True),
        ]), calibration())
        assert len(exits) == 1

    def test_coming_back_in_and_going_out_again_is_two(self):
        exits = ball_exits(path([
            (0.0, 50.0, 10.0, True),
            (1.0, 50.0, -2.0, True),     # out
            (10.0, 50.0, 20.0, True),    # back in
            (20.0, 50.0, -2.0, True),    # out again
        ]), calibration())
        assert len(exits) == 2

    def test_interpolated_points_never_prove_a_ball_went_out(self):
        """A filled-in point is a straight line between two sightings.

        Letting one cross a boundary would invent stoppages precisely in the
        stretches where the pipeline saw least — the worst place to be sure.
        """
        exits = ball_exits(path([
            (0.0, 50.0, 10.0, True),
            (1.0, 50.0, -5.0, False),    # drawn in, not seen
            (2.0, 50.0, 10.0, True),
        ]), calibration())
        assert exits == []

    def test_a_ball_that_never_leaves_produces_nothing(self):
        exits = ball_exits(path([
            (0.0, 20.0, 30.0, True),
            (1.0, 60.0, 40.0, True),
            (2.0, 90.0, 20.0, True),
        ]), calibration())
        assert exits == []

    def test_no_trajectory_or_no_calibration_is_empty_not_an_error(self):
        assert ball_exits(None, calibration()) == []
        assert ball_exits(path([(0.0, 1.0, 1.0, True)]), None) == []


class TestExitsAgainstTheLog:
    def out_and_tagged(self, tag_clock):
        return reconcile(
            _Log([]),
            [tag('throw_in', tag_clock)],
            trajectory=path([
                (10.0, 50.0, 10.0, True),
                (12.0, 50.0, -3.0, True),
            ]),
            calibration=calibration(),
        )

    def test_a_ball_out_and_a_throw_in_tagged_agree(self):
        result = self.out_and_tagged(16.0)
        assert result.exits_checked is True
        assert result.counts('exit')[AGREED] == 1

    def test_a_stoppage_nobody_tagged_is_visible_for_the_first_time(self):
        """The whole point of the cross-check: before this the tagged log was
        the pipeline's only source, so an untagged stoppage left no trace."""
        result = self.out_and_tagged(600.0)
        assert result.counts('exit')[CV_ONLY] == 1
        assert result.counts('exit')[TAG_ONLY] == 1

    def test_exit_disagreements_do_not_travel_in_the_json(self):
        """A half has a hundred throw-ins and nobody scrubs to each one. The
        rate travels; the list does not."""
        data = self.out_and_tagged(600.0).to_json()
        assert data['exit_agreement'] == 0.0
        assert data['disagreements'] == []


class TestWarnings:
    def test_a_tagged_goal_the_pipeline_missed_is_named_with_its_time(self):
        result = reconcile(_Log([]), [tag('goal', 754.0)])
        assert warnings_for(result) == [
            'a goal was tagged at 12:34 that the video analysis did not find'
        ]

    def test_a_goal_only_the_pipeline_saw_is_named_too(self):
        result = reconcile(_Log([shot(120.0)]), [])
        assert 'nobody tagged' in warnings_for(result)[0]

    def test_agreement_produces_no_warning(self):
        result = reconcile(_Log([shot(600.0)]), [tag('goal', 602.0)])
        assert warnings_for(result) == []

    def test_only_goals_are_loud(self):
        """A disputed throw-in is a rounding error in a possession figure."""
        result = reconcile(
            _Log([]), [tag('corner', 600.0)],
            trajectory=path([(10.0, 50.0, 10.0, True), (12.0, 50.0, -3.0, True)]),
            calibration=calibration(),
        )
        assert result.disagreements('exit')
        assert warnings_for(result) == []


class TestSerialisation:
    def test_the_shape_is_json_safe(self):
        import json

        result = reconcile(
            _Log([shot(600.0)]), [tag('goal', 602.0), tag('goal', 900.0)],
        )
        assert json.loads(json.dumps(result.to_json()))['goals'][AGREED] == 1

    def test_the_rate_is_rounded_not_raw(self):
        result = reconcile(
            _Log([shot(10.0), shot(20.0), shot(30.0)]), [tag('goal', 10.0)],
        )
        assert result.to_json()['goal_agreement'] == pytest.approx(0.333, abs=1e-3)

    def test_disagreements_are_ordered_by_when_to_look(self):
        result = reconcile(_Log([shot(900.0)]), [tag('goal', 100.0)])
        assert [e['status'] for e in result.to_json()['disagreements']] == [
            TAG_ONLY, CV_ONLY,
        ]

    def test_an_entry_points_at_whichever_record_has_an_opinion(self):
        assert Disagreement(GOAL, TAG_ONLY, tag_s=42.0).at_s == 42.0
        assert Disagreement(GOAL, CV_ONLY, cv_s=7.0).at_s == 7.0
