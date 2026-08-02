"""Event derivation, on hand-written touch sequences.

No video, no detector, no tracker — a touch sequence is just a list, so the
whole taxonomy can be exercised by writing one down. That is the point of
splitting touches from events: the half that guesses is isolated from the half
that reasons, and this file tests the half that reasons.

The tests that matter most are the ones asserting a *refusal*: no direction
without a calibration, no completed pass across a span where the ball was never
seen, no shot without knowing which goal is which. Those are the places where
the easy implementation quietly produces a plausible wrong number.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_events.py -q
"""

from __future__ import annotations

import numpy as np
import pytest

from cv.calibration import Calibration
from cv.events import (
    BLOCKED,
    CARRY,
    COMPLETED,
    DUEL,
    GOAL,
    INCOMPLETE,
    INTERCEPTION,
    OFF_TARGET,
    PASS,
    RECOVERY,
    SAVED,
    TACKLE,
    UNKNOWN_OUTCOME,
    derive_events,
    ppda,
)
from cv.frames import FrameRecord, FrameTable
from cv.phases import DeadSpan, PhaseTable
from cv.pitch import MatchOrientation, Pitch
from cv.teams import TEAM_A, TEAM_B
from cv.touches import Touch, TouchConfidence, TouchSequence

FPS = 30.0
PITCH = Pitch()
L, W = PITCH.length_m, PITCH.width_m
MID_Y = W / 2

# Fixture scale, kept physically coherent: 20 pixels to the metre, and a player
# 1.8m tall is therefore 36 pixels. Speeds below are in player heights per
# second, so an incoherent pair here would make every threshold in cv/events.py
# mean something different in the tests than it does in a match.
PX_PER_M = 20.0
SCALE = 36.0


def touch(t, track, team, xy_px=(0.0, 0.0), m=None, confidence=1.0, speed_after=0.0):
    return Touch(
        frame_index=int(t * FPS),
        timestamp_s=t,
        track_id=track,
        team=team,
        ball_xy=xy_px,
        ball_m=m,
        scale_px=SCALE,
        distance_ph=0.5,
        speed_before_ph_s=0.0,
        speed_after_ph_s=speed_after,
        turn_deg=45.0,
        observed=True,
        components=TouchConfidence(confidence, confidence, confidence, confidence),
    )


def identity_calibration(scale=10.0):
    """A calibration where one metre is `scale` pixels and the axes line up.

    Built from a homography directly rather than by fitting points, because
    these tests are about events, not about calibration — that is what
    tests/test_calibration.py is for.
    """
    homography = np.array([
        [1 / scale, 0.0, 0.0],
        [0.0, 1 / scale, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)
    return Calibration(homography, PITCH, image_size=(1920, 1080))


def table_for(touches, calibration=None, teams=None, extra_players=None):
    """A FrameTable covering the frames a touch sequence refers to."""
    records = []
    frames = {t.frame_index for t in touches}
    for index in sorted(frames or {0}):
        rows = []
        for t in touches:
            if t.frame_index == index:
                x, y = t.ball_xy
                rows.append([t.track_id, x - 10, y - SCALE, x + 10, y, 0.9])
        rows.extend(extra_players or [])
        records.append(FrameRecord(
            frame_index=index,
            timestamp_s=index / FPS,
            players=np.array(rows, dtype=np.float32).reshape(-1, 6),
        ))
    return FrameTable(
        fps=FPS, frame_width=1920, frame_height=1080,
        records=records,
        team_by_track=teams or {},
        calibration=calibration,
    )


def derive(touches, gaps=(), **kwargs):
    sequence = TouchSequence(touches=list(touches), gaps=list(gaps))
    table = kwargs.pop('table', None) or table_for(touches, kwargs.pop('calibration', None))
    return derive_events(sequence, table, pitch=PITCH, **kwargs)


class TestPasses:
    def test_same_team_different_players_is_a_completed_pass(self):
        log = derive([touch(0.0, 1, TEAM_A), touch(1.0, 2, TEAM_A)])
        passes = log.passes()
        assert len(passes) == 1
        assert passes[0].outcome == COMPLETED
        assert passes[0].receiver_track_id == 2

    def test_a_ball_reaching_an_opponent_is_incomplete(self):
        log = derive([touch(0.0, 1, TEAM_A), touch(0.5, 9, TEAM_B)])
        assert log.passes()[0].outcome == INCOMPLETE

    def test_an_incomplete_pass_has_no_receiver(self):
        """A receiver is a teammate. Naming the opponent would read as an assist."""
        log = derive([touch(0.0, 1, TEAM_A), touch(0.5, 9, TEAM_B)])
        assert log.passes()[0].receiver_track_id is None

    def test_the_same_player_twice_is_a_carry_not_a_pass(self):
        log = derive([touch(0.0, 1, TEAM_A), touch(0.3, 1, TEAM_A)])
        assert log.passes() == []
        assert len(log.by_type(CARRY)) == 1
        assert log.by_type(CARRY)[0].touches == 2

    def test_length_is_none_without_a_calibration(self):
        """Not zero. A zero-length pass is a measurement; this is an absence."""
        log = derive([touch(0.0, 1, TEAM_A), touch(1.0, 2, TEAM_A)])
        assert log.passes()[0].length_m is None
        assert log.passes()[0].length_bucket is None


class TestGaps:
    """The guard against inventing football that was never seen."""

    def test_a_pass_across_an_unseen_span_is_not_a_completion(self):
        """A to C looks like a completed pass. It may have been A to B to C.

        Reporting it as completed would inflate pass accuracy by exactly the
        share of the match the detector missed, which on this footage is large.
        """
        log = derive(
            [touch(0.0, 1, TEAM_A), touch(2.0, 2, TEAM_A)],
            gaps=[(0.5, 1.5)],
        )
        assert log.passes()[0].outcome == UNKNOWN_OUTCOME
        assert log.passes()[0].crossed_gap is True

    def test_a_gapped_pass_is_marked_less_confident(self):
        clean = derive([touch(0.0, 1, TEAM_A), touch(2.0, 2, TEAM_A)])
        gapped = derive(
            [touch(0.0, 1, TEAM_A), touch(2.0, 2, TEAM_A)], gaps=[(0.5, 1.5)]
        )
        assert gapped.passes()[0].confidence < clean.passes()[0].confidence

    def test_a_gap_elsewhere_does_not_taint_a_clean_pass(self):
        log = derive(
            [touch(0.0, 1, TEAM_A), touch(0.4, 2, TEAM_A)],
            gaps=[(5.0, 6.0)],
        )
        assert log.passes()[0].outcome == COMPLETED


class TestDefensiveActions:
    def test_taking_the_ball_off_a_player_in_control_is_a_tackle(self):
        """Controlled before, controlled after — somebody took it off them."""
        log = derive([
            touch(0.0, 1, TEAM_A, xy_px=(100.0, 400.0)),
            touch(0.4, 1, TEAM_A, xy_px=(110.0, 400.0)),      # carrying, slow
            touch(0.7, 9, TEAM_B, xy_px=(118.0, 400.0)),      # dispossessed
        ])
        assert [e.type for e in log.by_type(TACKLE)] == [TACKLE]

    def test_reading_a_ball_in_flight_is_an_interception(self):
        """Fast between two different players — a pass was cut out."""
        log = derive([
            touch(0.0, 1, TEAM_A, xy_px=(100.0, 400.0)),
            touch(0.2, 2, TEAM_A, xy_px=(400.0, 400.0)),
            touch(0.4, 9, TEAM_B, xy_px=(900.0, 400.0)),
        ])
        assert log.by_type(INTERCEPTION)

    def test_a_ball_left_alone_is_recovered_not_won(self):
        log = derive([
            touch(0.0, 1, TEAM_A, xy_px=(100.0, 400.0)),
            touch(3.0, 9, TEAM_B, xy_px=(300.0, 400.0)),
        ])
        assert log.by_type(RECOVERY)

    def test_two_opponents_on_the_same_ball_is_a_duel(self):
        """Same place, a fraction of a second apart — contested, not exchanged.

        Ground duels only: telling an aerial duel apart needs the ball's
        height, so calling these plain "duels" would silently exclude every
        header from a number people expect to include them.
        """
        log = derive([
            touch(0.0, 1, TEAM_A, xy_px=(500.0, 400.0)),
            touch(0.3, 9, TEAM_B, xy_px=(510.0, 400.0)),
        ])
        assert log.by_type(DUEL)

    def test_the_defensive_action_belongs_to_the_team_that_won_it(self):
        log = derive([touch(0.0, 1, TEAM_A), touch(0.5, 9, TEAM_B)])
        action = log.by_type(TACKLE, INTERCEPTION, RECOVERY, DUEL)[0]
        assert action.team == TEAM_B
        assert action.opponent_track_id == 1


class TestUncalibrated:
    """Without metres, positional questions are unanswerable, not approximate."""

    def test_direction_is_none_rather_than_guessed(self):
        """There is no honest pixel fallback.

        On a panning camera the image x-axis is not the pitch x-axis, and the
        relationship changes every frame.
        """
        log = derive([touch(0.0, 1, TEAM_A), touch(1.0, 2, TEAM_A)])
        assert log.passes()[0].direction is None

    def test_no_positional_tags_are_produced(self):
        log = derive([touch(0.0, 1, TEAM_A), touch(1.0, 2, TEAM_A)])
        assert log.passes()[0].tags == ()

    def test_no_shots_are_detected(self):
        """Without knowing where the goal is, a shot and a long pass are one thing."""
        log = derive([touch(0.0, 1, TEAM_A), touch(0.3, 2, TEAM_A)])
        assert log.shots() == []

    def test_passes_still_work(self):
        """The pixels-only column is the point: this ships with no footage."""
        log = derive([touch(0.0, 1, TEAM_A), touch(1.0, 2, TEAM_A)])
        assert log.passes()[0].outcome == COMPLETED


class TestCalibrated:
    """Metres unlock the geography. Positions below are chosen in metres and
    converted to the pixels the fixture calibration expects."""

    SCALE_PX_PER_M = PX_PER_M

    def px(self, x_m, y_m):
        return (x_m * self.SCALE_PX_PER_M, y_m * self.SCALE_PX_PER_M)

    def at(self, t, track, team, x_m, y_m, **kwargs):
        return touch(t, track, team, xy_px=self.px(x_m, y_m), m=(x_m, y_m), **kwargs)

    def derive(self, touches, **kwargs):
        calibration = identity_calibration(self.SCALE_PX_PER_M)
        table = table_for(touches, calibration=calibration, teams=kwargs.pop('teams', None))
        return derive_events(
            TouchSequence(touches=list(touches)), table,
            pitch=PITCH, orientation=MatchOrientation(home_attacks_first_half='right'),
            period='first_half', side_of_team={TEAM_A: 'us', TEAM_B: 'them'},
            **kwargs,
        )

    def test_a_forward_pass_is_forward(self):
        log = self.derive([
            self.at(0.0, 1, TEAM_A, 20.0, MID_Y),
            self.at(1.0, 2, TEAM_A, 60.0, MID_Y),
        ])
        assert log.passes()[0].direction == 'forward'

    def test_a_backward_pass_is_backward(self):
        log = self.derive([
            self.at(0.0, 1, TEAM_A, 60.0, MID_Y),
            self.at(1.0, 2, TEAM_A, 20.0, MID_Y),
        ])
        assert log.passes()[0].direction == 'backward'

    def test_a_square_ball_is_sideways(self):
        log = self.derive([
            self.at(0.0, 1, TEAM_A, 50.0, 10.0),
            self.at(1.0, 2, TEAM_A, 52.0, 55.0),
        ])
        assert log.passes()[0].direction == 'sideways'

    def test_length_buckets(self):
        short = self.derive([
            self.at(0.0, 1, TEAM_A, 50.0, MID_Y),
            self.at(1.0, 2, TEAM_A, 58.0, MID_Y),
        ])
        long_ball = self.derive([
            self.at(0.0, 1, TEAM_A, 20.0, MID_Y),
            self.at(1.0, 2, TEAM_A, 75.0, MID_Y),
        ])
        assert short.passes()[0].length_bucket == 'short'
        assert long_ball.passes()[0].length_bucket == 'long'

    def test_a_long_forward_ball_is_tagged_progressive(self):
        log = self.derive([
            self.at(0.0, 1, TEAM_A, 10.0, MID_Y),
            self.at(1.0, 2, TEAM_A, 45.0, MID_Y),
        ])
        assert 'progressive' in log.passes()[0].tags

    def test_entering_the_final_third_is_tagged_once(self):
        log = self.derive([
            self.at(0.0, 1, TEAM_A, 60.0, MID_Y),
            self.at(1.0, 2, TEAM_A, 80.0, MID_Y),
        ])
        assert 'final_third_entry' in log.passes()[0].tags

    def test_a_ball_already_in_the_final_third_is_not_an_entry(self):
        log = self.derive([
            self.at(0.0, 1, TEAM_A, 75.0, MID_Y),
            self.at(1.0, 2, TEAM_A, 85.0, MID_Y),
        ])
        assert 'final_third_entry' not in log.passes()[0].tags

    def test_a_wide_ball_into_the_box_is_tagged_a_cross(self):
        log = self.derive([
            self.at(0.0, 1, TEAM_A, 95.0, 3.0),
            self.at(1.0, 2, TEAM_A, 100.0, MID_Y),
        ])
        assert 'cross' in log.passes()[0].tags

    def test_a_wide_ball_is_tagged_a_switch(self):
        log = self.derive([
            self.at(0.0, 1, TEAM_A, 50.0, 5.0),
            self.at(1.0, 2, TEAM_A, 55.0, 60.0),
        ])
        assert 'switch' in log.passes()[0].tags


class TestShots(TestCalibrated):
    def strike(self, x_m=95.0, y_m=MID_Y, then=None, speed_after=20.0):
        """A hard touch near goal, followed by the ball crossing the line.

        `speed_after` is what makes it a shot rather than a roll — the ball's
        speed leaving the foot, in player heights per second. Twenty is a
        firmly struck ball: 1.8m per player height, so about 36 m/s.
        """
        touches = [self.at(0.0, 7, TEAM_A, x_m, y_m, speed_after=speed_after)]
        touches.extend(then or [])
        calibration = identity_calibration(self.SCALE_PX_PER_M)
        table = table_for(touches, calibration=calibration)

        # The ball travels on past the touch and over the goal line.
        for step in range(1, 20):
            t = step / FPS
            x = x_m + (L + 1.0 - x_m) * step / 12.0
            table.records.append(FrameRecord(
                frame_index=int(t * FPS) + 1,
                timestamp_s=t,
                players=np.empty((0, 6), dtype=np.float32),
                ball_xy=self.px(min(x, L + 1.0), y_m),
                ball_observed=True,
            ))
        table.records.sort(key=lambda r: r.frame_index)
        table._index = None
        return touches, table

    def run(self, touches, table, **kwargs):
        return derive_events(
            TouchSequence(touches=list(touches)), table,
            pitch=PITCH, orientation=MatchOrientation(home_attacks_first_half='right'),
            period='first_half', side_of_team={TEAM_A: 'us', TEAM_B: 'them'},
            **kwargs,
        )

    def test_a_ball_struck_through_the_mouth_is_a_shot(self):
        touches, table = self.strike()
        shots = self.run(touches, table).shots()
        assert len(shots) == 1
        assert shots[0].track_id == 7

    def test_an_untouched_shot_that_crosses_the_line_is_a_goal(self):
        touches, table = self.strike()
        assert self.run(touches, table).shots()[0].outcome == GOAL

    def test_the_keeper_getting_to_it_is_a_save(self):
        touches, table = self.strike(
            then=[self.at(0.3, 30, TEAM_B, L - 1.0, MID_Y)]
        )
        log = self.run(touches, table, keeper_tracks={30})
        assert log.shots()[0].outcome == SAVED

    def test_a_defender_close_to_the_shooter_blocks_it(self):
        touches, table = self.strike(
            then=[self.at(0.2, 20, TEAM_B, 97.0, MID_Y)]
        )
        assert self.run(touches, table).shots()[0].outcome == BLOCKED

    def test_a_defender_far_away_did_not_block_it(self):
        touches, table = self.strike(
            then=[self.at(0.5, 20, TEAM_B, 60.0, MID_Y)]
        )
        assert self.run(touches, table).shots()[0].outcome == OFF_TARGET

    def test_distance_and_angle_are_measured_from_the_attacked_goal(self):
        touches, table = self.strike(x_m=L - 20.0)
        shot = self.run(touches, table).shots()[0]
        assert shot.distance_to_goal_m == pytest.approx(20.0)
        assert shot.angle_to_goal_rad > 0

    def test_a_header_is_always_recorded_as_a_foot_shot(self):
        """One camera cannot see the ball's height.

        Kept as an explicit field rather than left out, so the bias it puts on
        xG is visible in the output instead of only in a docstring.
        """
        touches, table = self.strike()
        assert self.run(touches, table).shots()[0].is_header is False

    def test_a_shot_in_the_box_is_tagged(self):
        touches, table = self.strike(x_m=98.0)
        assert 'in_box' in self.run(touches, table).shots()[0].tags


class TestPpda:
    def test_it_is_none_without_a_direction_of_play(self):
        log = derive([touch(0.0, 1, TEAM_A), touch(1.0, 2, TEAM_A)])
        assert ppda(log, PITCH, TEAM_A, None) is None

    def test_no_defensive_actions_gives_none_rather_than_infinity(self):
        """Dividing by zero actions is not "pressed infinitely badly"."""
        log = derive([touch(0.0, 1, TEAM_B), touch(1.0, 2, TEAM_B)])
        assert ppda(log, PITCH, TEAM_A, 'right') is None


class TestInPlay:
    """A restart is still a real event; it is just not open play."""

    def phases(self, start_s, end_s):
        return PhaseTable(spans=[
            DeadSpan(start_s, end_s, 'out_of_bounds', 'throw_in'),
        ])

    def test_events_during_a_stoppage_are_flagged_not_dropped(self):
        touches = [
            touch(0.0, 1, TEAM_A), touch(0.5, 2, TEAM_A),
            touch(4.0, 3, TEAM_A), touch(4.5, 4, TEAM_A),
        ]
        log = derive(touches, phases=self.phases(3.5, 5.0))

        # Four touches by four players is three links: 1->2, 2->3, 3->4.
        assert len(log.passes()) == 3, 'the throw-in is still a pass'
        by_time = {round(p.timestamp_s, 1): p.in_play for p in log.passes()}
        assert by_time[0.0] is True
        assert by_time[0.5] is True
        assert by_time[4.0] is False

    def test_everything_is_in_play_without_a_log(self):
        """True means nobody told us, not that we checked."""
        log = derive([touch(0.0, 1, TEAM_A), touch(0.5, 2, TEAM_A)])
        assert all(e.in_play for e in log)

    def test_the_flag_reaches_the_json(self):
        log = derive([touch(0.0, 1, TEAM_A), touch(0.5, 2, TEAM_A)],
                     phases=self.phases(0.0, 5.0))
        assert log.to_json()['events'][0]['in_play'] is False


class TestSerialisation:
    def test_events_round_trip_through_json(self):
        import json

        log = derive([
            touch(0.0, 1, TEAM_A), touch(0.5, 2, TEAM_A), touch(1.2, 9, TEAM_B),
        ])
        text = json.dumps(log.to_json())
        assert json.loads(text)['events']

    def test_every_leaf_is_a_json_primitive(self):
        """numpy scalars are not JSON-serialisable and surface as a 500.

        They arrive easily here: every position starts life in a numpy array.
        """
        log = derive([touch(0.0, 1, TEAM_A), touch(0.5, 2, TEAM_A)])

        def walk(value):
            if isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
            else:
                assert isinstance(value, (int, float, str, bool, type(None))), value
                assert not isinstance(value, np.generic), value

        walk(log.to_json())

    def test_pass_type_is_named_in_the_output(self):
        log = derive([touch(0.0, 1, TEAM_A), touch(0.5, 2, TEAM_A)])
        assert log.to_json()['events'][0]['type'] == PASS
