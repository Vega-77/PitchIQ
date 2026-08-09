"""Serialising a match report, and the two ways that goes wrong quietly.

The first is numpy scalars. Every position in this pipeline starts life in a
numpy array, `isinstance(np.float32(1), float)` is False but looks fine in a
debugger, and `json.dumps` refuses them — so the failure surfaces as a 500 in
the browser rather than anywhere near the code that caused it. One test walks
the entire structure looking for survivors.

The second is zeros standing in for absences. A calibration-dependent field that
serialises as 0 tells a reader the thing was measured and came out zero. It was
not measured at all. `analyse_match` already learned this once, which is why
`movement_available` exists.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_report_json.py -q
"""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest

from cv.events import COMPLETED, EventLog, Pass, Shot
from cv.identity import PlayerCluster
from cv.metrics import MovementStats
from cv.participants import (
    ROLE_OFFFIELD,
    ROLE_OFFICIAL,
    ParticipantReport,
    ParticipantVerdict,
)
from cv.pipeline import MatchReport, PlayerReport
from cv.possession import PossessionSummary
from cv.report_json import SCHEMA_VERSION, shot_marks, team_stats
from cv.teams import TEAM_A, TEAM_B
from cv.touches import Touch, TouchConfidence, TouchSequence


def a_pass(track, team, outcome=COMPLETED, tags=(), bucket='short', direction=None):
    return Pass(
        event_id=f'{track}:p', type='pass', timestamp_s=1.0, frame_index=30,
        team=team, track_id=track, start_xy_px=(0.0, 0.0),
        confidence=0.8, tags=tags, outcome=outcome,
        length_bucket=bucket, direction=direction,
    )


def a_touch(track, team, t=1.0):
    return Touch(
        frame_index=int(t * 30), timestamp_s=t, track_id=track, team=team,
        ball_xy=(0.0, 0.0), ball_m=None, scale_px=36.0, distance_ph=0.4,
        speed_before_ph_s=0.0, speed_after_ph_s=0.0, turn_deg=40.0,
        observed=True, components=TouchConfidence(0.8, 0.7, 1.0, 0.9),
    )


def a_report(calibrated=False, **kwargs):
    report = MatchReport(source='clip.mp4', duration_s=15.0, processing_s=11.6)
    report.movement_available = calibrated
    report.possession = PossessionSummary(team_a_s=9.0, team_b_s=6.0)
    report.touches = TouchSequence(touches=[a_touch(1, TEAM_A), a_touch(2, TEAM_A, 2.0)])
    report.events = EventLog(
        events=[a_pass(1, TEAM_A)], touches=report.touches
    )
    report.clusters = [
        PlayerCluster(cluster_id=0, track_ids={1, 2}, team=TEAM_A,
                      first_seen_s=0.0, last_seen_s=15.0, sightings=400),
    ]
    for key, value in kwargs.items():
        setattr(report, key, value)
    return report


class TestJsonSafety:
    def test_the_whole_structure_round_trips(self):
        data = a_report().to_json()
        assert json.loads(json.dumps(data))['schema_version'] == SCHEMA_VERSION

    def test_no_numpy_scalars_survive_anywhere(self):
        """The failure that surfaces as a 500 nowhere near its cause."""
        report = a_report(calibrated=True)
        report.players = [PlayerReport(
            track_id=1, team=TEAM_A,
            movement=MovementStats(
                track_id=1,
                distance_m=np.float32(1234.5),
                top_speed_ms=np.float64(7.2),
                sprint_count=np.int64(3),
                sprint_distance_m=np.float32(88.0),
            ),
            minutes_tracked=np.float64(12.0),
        )]

        def walk(value, path='root'):
            if isinstance(value, dict):
                for key, item in value.items():
                    walk(item, f'{path}.{key}')
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    walk(item, f'{path}[{i}]')
            else:
                assert not isinstance(value, np.generic), f'numpy at {path}: {value!r}'
                assert isinstance(
                    value, (int, float, str, bool, type(None))
                ), f'{path}: {type(value)}'

        walk(report.to_json())

    def test_a_report_with_nothing_in_it_still_serialises(self):
        empty = MatchReport(source='x.mp4', duration_s=0.0, processing_s=0.0)
        assert json.dumps(empty.to_json())


class TestAbsentNotZero:
    def test_uncalibrated_positional_counts_are_null(self):
        """Nobody counted them. Zero would say they were counted and were none."""
        teams = a_report(calibrated=False).to_json()['teams']
        for field in (
            'progressive_passes', 'final_third_entries', 'box_entries',
            'switches', 'crosses', 'shots', 'shots_on_target', 'goals', 'xg',
        ):
            assert teams[TEAM_A][field] is None, field

    def test_calibrated_positional_counts_are_numbers(self):
        teams = a_report(calibrated=True).to_json()['teams']
        assert teams[TEAM_A]['shots'] == 0
        assert teams[TEAM_A]['progressive_passes'] == 0

    def test_calibration_error_is_null_without_a_calibration(self):
        assert a_report().to_json()['calibration_error_m'] is None

    def test_pass_accuracy_is_null_when_nothing_was_attempted(self):
        """Zero accuracy is a claim that every pass failed."""
        stats = team_stats(EventLog(), TEAM_B, calibrated=False)
        assert stats.pass_accuracy is None
        assert stats.to_json()['pass_accuracy'] is None

    def test_movement_is_null_for_a_cluster_with_no_calibration(self):
        track = a_report().to_json()['tracks'][0]
        assert track['distance_m'] is None
        assert track['top_speed_kmh'] is None


class TestShapeBelongsToATeam:
    """A single shape over every track on the pitch is the bounding box of the
    match — two banks of players plus the referee — and it was reported as Team
    A's formation until `MatchReport.shape` became a dict keyed by team.
    """

    SHAPE_A = {'width_m': 40.0, 'depth_m': 30.0, 'compactness_m': 12.0}
    SHAPE_B = {'width_m': 52.0, 'depth_m': 44.0, 'compactness_m': 18.0}

    def test_each_team_gets_its_own(self):
        report = a_report(calibrated=True,
                          shape={TEAM_A: self.SHAPE_A, TEAM_B: self.SHAPE_B})
        teams = report.to_json()['teams']

        assert teams[TEAM_A]['shape'] == self.SHAPE_A
        assert teams[TEAM_B]['shape'] == self.SHAPE_B

    def test_team_b_is_not_handed_an_empty_shape_by_default(self):
        report = a_report(calibrated=True,
                          shape={TEAM_A: self.SHAPE_A, TEAM_B: self.SHAPE_B})
        assert report.to_json()['teams'][TEAM_B]['shape'] != {}

    def test_a_missing_team_is_empty_rather_than_an_error(self):
        report = a_report(calibrated=True, shape={TEAM_A: self.SHAPE_A})
        assert report.to_json()['teams'][TEAM_B]['shape'] == {}


class TestParticipants:
    def scene(self):
        report = a_report()
        report.participants = ParticipantReport(by_track={
            1: ParticipantVerdict(
                track_id=1, role=ROLE_OFFFIELD, reason='never moved',
                sightings=300, screen_time_s=30.0, spread_ph=0.2,
                travel_ph_per_min=1.0, edge_share=0.9, off_pitch_share=None,
                kit_known=False,
            ),
            2: ParticipantVerdict(
                track_id=2, role=ROLE_OFFICIAL, reason='neither kit',
                sightings=300, screen_time_s=30.0, spread_ph=6.0,
                travel_ph_per_min=90.0, edge_share=0.1, off_pitch_share=0.0,
                kit_known=False,
            ),
        })
        return report

    def test_every_verdict_is_carried_with_its_reason(self):
        """An exclusion that cannot be audited is indistinguishable from a bug."""
        data = self.scene().to_json()
        rows = {row['track_id']: row for row in data['participants']}

        assert rows[1]['role'] == ROLE_OFFFIELD
        assert rows[1]['reason'] == 'never moved'
        assert rows[2]['role'] == ROLE_OFFICIAL

    def test_the_size_of_the_correction_is_in_quality(self):
        quality = self.scene().to_json()['quality']
        assert quality['excluded_tracks'] == 1
        assert quality['flagged_officials'] == 1

    def test_an_unavailable_off_pitch_test_is_null_not_zero(self):
        """Zero claims we looked and they were on the pitch every time."""
        rows = {r['track_id']: r for r in self.scene().to_json()['participants']}
        assert rows[1]['off_pitch_share'] is None
        assert rows[2]['off_pitch_share'] == 0.0

    def test_a_report_without_the_pass_is_not_an_error(self):
        data = a_report().to_json()
        assert data['participants'] == []
        assert data['quality']['excluded_tracks'] == 0


class TestBallHonesty:
    """Seen and filled-in must never be folded into one figure.

    `BallTrajectory.coverage` counts interpolated points, which are a straight
    line drawn between two sightings. Reporting that as "ball coverage 98%"
    once sat directly above a warning saying the ball was barely detected —
    the two came from different measurements and flatly contradicted each
    other.
    """

    def report_with(self, observed, interpolated, duration_s=10.0):
        from cv.ball import BallPoint, BallTrajectory

        report = a_report()
        report.duration_s = duration_s
        points = [
            BallPoint(i, i / 30, (1.0, 1.0), i < observed)
            for i in range(observed + interpolated)
        ]
        report.ball = BallTrajectory(points)
        return report

    def test_seen_excludes_what_was_filled_in(self):
        report = self.report_with(observed=150, interpolated=140)
        assert report.ball_seen_share == pytest.approx(0.5)
        assert report.ball_filled_share == pytest.approx(140 / 300)

    def test_the_two_shares_are_reported_separately(self):
        quality = self.report_with(150, 140).to_json()['quality']
        assert quality['ball_seen_share'] == pytest.approx(0.5)
        assert quality['ball_filled_share'] == pytest.approx(0.467, abs=0.002)

    def test_a_report_with_no_ball_reports_zero_rather_than_crashing(self):
        report = a_report()
        report.ball = None
        assert report.ball_seen_share == 0.0
        assert report.to_json()['quality']['ball_seen_share'] == 0.0

    def test_the_summary_never_claims_the_ball_was_watched_throughout(self):
        """The headline line and the warnings have to agree."""
        text = self.report_with(observed=60, interpolated=230).summary()
        assert '20% seen' in text
        assert '77% filled in' in text


class TestNoTouchesReason:
    """The warning must diagnose, not assume.

    Not seeing the ball and seeing something that is never near a player are
    different failures needing different fixes, and an earlier version asserted
    the first whatever the numbers said.
    """

    def reason(self, seen, holder):
        from cv.ball import BallPoint, BallTrajectory
        from cv.pipeline import _no_touches_reason

        report = a_report()
        report.duration_s = 10.0
        report.clear_holder_share = holder
        report.ball = BallTrajectory([
            BallPoint(i, i / 30, (1.0, 1.0), i < int(300 * seen)) for i in range(300)
        ])
        return _no_touches_reason(report)

    def test_a_barely_seen_ball_is_named_as_the_cause(self):
        assert 'only detected in 20%' in self.reason(seen=0.2, holder=0.0)

    def test_a_well_seen_ball_nowhere_near_a_player_is_named_differently(self):
        text = self.reason(seen=0.85, holder=0.01)
        assert 'almost never near a player' in text
        assert 'something else' in text

    def test_good_coverage_on_both_admits_the_thresholds_may_be_wrong(self):
        assert 'thresholds' in self.reason(seen=0.9, holder=0.6)


class TestQuality:
    def test_the_quality_block_sits_beside_the_numbers(self):
        quality = a_report().to_json()['quality']
        for key in (
            'ball_seen_share', 'ball_filled_share', 'clear_holder_share', 'kit_separation',
            'touch_confidence_p50', 'touch_confidence_p10', 'unseen_spans',
            'tracks_per_cluster', 'keeper_method',
        ):
            assert key in quality, key

    def test_the_smoothing_window_is_published_beside_what_it_cost(self):
        # Both, because neither can be worked out from the other any more. The
        # window is chosen per track from that track's measured wobble, so the
        # phantom rate stopped being proportional to the wobble — and a browser
        # holding a constant would caption the figures with a rate for a window
        # it has no way to know about.
        report = a_report()
        report.players = [
            PlayerReport(
                track_id=i, team=TEAM_A,
                movement=MovementStats(track_id=i, position_noise_m=noise,
                                       minutes_tracked=10.0),
            )
            for i, noise in enumerate((0.12, 0.13, 0.26))
        ]
        report.smoothing_s = {0: 0.7, 1: 0.7, 2: 1.0}

        quality = report.to_json()['quality']
        assert quality['position_noise_m'] == 0.13
        # The mode, not the mean: 0.85 is a window nothing was smoothed at.
        assert quality['smoothing_s'] == 0.7
        assert quality['phantom_m_per_minute'] == pytest.approx(19.8, rel=0.05)

    def test_a_run_that_smoothed_nothing_names_no_window(self):
        # An uncalibrated run has no positions in metres to smooth. Reporting
        # the default would describe work that never happened, which is the same
        # mistake as reporting zero for a figure nobody measured.
        quality = a_report().to_json()['quality']
        assert quality['smoothing_s'] is None
        assert quality['phantom_m_per_minute'] is None

    def test_keeper_method_defaults_to_unavailable(self):
        assert a_report().to_json()['quality']['keeper_method'] == 'unavailable'

    def test_fragmentation_is_reported_as_tracks_per_cluster(self):
        assert a_report().to_json()['quality']['tracks_per_cluster'] == 2.0

    def test_heavy_fragmentation_adds_a_warning_to_the_file(self):
        """Carried in the JSON, not only in the console summary.

        The summary is not what a frontend reads, and a per-player table with
        no caveat attached is exactly how a fragment gets shown as a player.
        """
        report = a_report()
        report.players = [
            PlayerReport(track_id=i, team=TEAM_A) for i in range(60)
        ]
        data = report.to_json()
        assert any('fragments' in w for w in data['warnings'])
        assert data['trustworthy'] is False


class TestPositionalFieldsReachTheJson:
    """The report builder has to hand `team_stats` a pitch and a direction.

    It did neither until 2026-08-02, so `ppda` was None in every report this
    project has produced — not because the footage could not support it, but
    because the guard `if pitch is not None` was never satisfied. These pin the
    plumbing rather than the arithmetic, which is tested in test_events.py.
    """

    def a_positional_report(self):
        from cv.pitch import Pitch

        report = a_report(calibrated=True)
        report.pitch = Pitch()
        report.attacking_ends = {TEAM_A: 'right', TEAM_B: 'left'}
        return report

    def test_without_a_pitch_the_positional_fields_are_null(self):
        team = a_report(calibrated=True).to_json()['teams'][TEAM_A]
        assert team['ppda'] is None
        assert team['turnovers_by_third'] is None

    def test_with_one_they_are_computed(self):
        team = self.a_positional_report().to_json()['teams'][TEAM_A]
        assert team['turnovers_by_third'] == {
            'defensive': 0, 'middle': 0, 'attacking': 0,
        }

    def test_each_team_is_given_the_other_team_direction_for_pressing(self):
        """PPDA is measured in the zone the *opponent* is building out of, so
        handing both teams the same end would measure the wrong half."""
        from cv.report_json import team_stats

        ends = {TEAM_A: 'right', TEAM_B: 'left'}
        for team in (TEAM_A, TEAM_B):
            other = TEAM_B if team == TEAM_A else TEAM_A
            assert ends[team] != ends[other]

        report = self.a_positional_report()
        data = report.to_json()['teams']
        assert data[TEAM_A]['turnovers_by_third'] is not None
        assert data[TEAM_B]['turnovers_by_third'] is not None
        assert team_stats(EventLog(), TEAM_A, calibrated=True).ppda is None

    def test_a_fifteen_second_clip_has_no_pressing_trend(self):
        """The default fixture is a clip, and a clip holds no blocks."""
        team = self.a_positional_report().to_json()['teams'][TEAM_A]
        assert team['pressing_segments'] is None

    def test_a_full_match_gets_one_block_per_quarter_hour(self):
        report = self.a_positional_report()
        report.duration_s = 90 * 60.0
        segments = report.to_json()['teams'][TEAM_A]['pressing_segments']
        assert len(segments) == 6
        assert segments[-1]['end_s'] == 5400.0

    def test_the_blocks_cover_the_processed_window_not_the_whole_video(self):
        """`--end` is optional, so the fallback has to be what was processed.

        Tiling from zero to the length of the file would put five empty blocks
        in front of a clip taken from the second half.
        """
        report = self.a_positional_report()
        report.duration_s = 30 * 60.0
        segments = report.to_json(
            window={'start_s': 2700.0},
        )['teams'][TEAM_A]['pressing_segments']
        assert segments[0]['start_s'] == 2700.0
        assert segments[-1]['end_s'] == 4500.0

    def test_territory_and_drift_are_null_when_the_run_had_neither(self):
        team = a_report(calibrated=True).to_json()['teams'][TEAM_A]
        assert team['territory'] is None
        assert team['shape_drift'] is None


class TestWhatWasNeverSeen:
    """`no_ball_s` split into its parts, and what happens when nothing split it.

    The figure has been in the quality block since possession was first
    measured, covering both a throw-in the camera could not follow the ball
    through and a stretch of live football nobody saw. Only the second is a
    hole in the report, and the two added together hid it — worse, hid it more
    the better the tagging was.
    """

    def a_blindness(self, **kwargs):
        from cv.blind import ACCOUNTED, DEAD, UNEXPLAINED, Blindness, BlindSpell

        return Blindness(spells=[
            BlindSpell(10.0, 40.0, DEAD),
            BlindSpell(60.0, 70.0, ACCOUNTED, ('corner',)),
            BlindSpell(100.0, 120.0, UNEXPLAINED),
        ], **kwargs)

    def test_no_run_of_it_leaves_the_key_null_rather_than_absent(self):
        assert a_report().to_json()['quality']['blind'] is None

    def test_the_split_reaches_the_quality_block(self):
        blind = a_report(blindness=self.a_blindness(checked=True)).to_json()
        published = blind['quality']['blind']
        assert published['dead_s'] == 30.0
        assert published['accounted_s'] == 10.0
        assert published['unexplained_s'] == 20.0
        assert published['total_s'] == 60.0

    def test_an_unchecked_run_withholds_the_parts_and_keeps_the_total(self):
        """Three zeroes would read as a clean bill of health for a run
        where no check was possible at all."""
        from cv.blind import UNCHECKED, Blindness, BlindSpell

        unchecked = Blindness(spells=[BlindSpell(0.0, 40.0, UNCHECKED)])
        published = a_report(blindness=unchecked).to_json()['quality']['blind']
        assert published['total_s'] == 40.0
        assert published['checked'] is False
        assert published['dead_s'] is None
        assert published['unexplained_s'] is None

    def test_only_the_unaccounted_stretches_travel_as_places_to_look(self):
        published = a_report(
            blindness=self.a_blindness(checked=True),
        ).to_json()['quality']['blind']
        assert [s['kind'] for s in published['worst']] == ['unexplained']

    def test_the_whole_block_round_trips_as_json(self):
        data = a_report(blindness=self.a_blindness(checked=True)).to_json()
        assert json.loads(json.dumps(data))['quality']['blind'] is not None


class TestReconciliation:
    """Absent is not zero, one more time.

    A run with no tagged log was never compared against anything. Reporting
    that as 0% agreement would say the two records contradicted each other on
    every goal, which is a claim about the match rather than about what we were
    given to check.
    """

    def a_reconciliation(self, **kwargs):
        from cv.reconcile import Disagreement, Reconciliation

        return Reconciliation(entries=[
            Disagreement('goal', 'agreed', cv_s=600.0, tag_s=602.0),
            Disagreement('goal', 'tag_only', tag_s=1200.0),
        ], **kwargs)

    def test_no_tagged_log_leaves_it_null_rather_than_empty(self):
        data = a_report().to_json()
        assert data['reconciliation'] is None
        assert data['quality']['goal_agreement'] is None
        assert data['quality']['exit_agreement'] is None

    def test_the_agreement_rate_reaches_the_quality_block(self):
        data = a_report(reconciliation=self.a_reconciliation()).to_json()
        assert data['quality']['goal_agreement'] == 0.5

    def test_the_disagreements_travel_whole(self):
        """A rate says how bad it is; the list says where to look."""
        data = a_report(reconciliation=self.a_reconciliation()).to_json()
        entries = data['reconciliation']['disagreements']
        assert [e['status'] for e in entries] == ['tag_only']
        assert entries[0]['tag_s'] == 1200.0

    def test_exits_unchecked_is_distinct_from_exits_that_agreed(self):
        data = a_report(reconciliation=self.a_reconciliation()).to_json()
        assert data['reconciliation']['exits_checked'] is False
        assert data['reconciliation']['exits'] is None

    def test_the_whole_block_round_trips_as_json(self):
        data = a_report(reconciliation=self.a_reconciliation()).to_json()
        assert json.loads(json.dumps(data))['reconciliation'] is not None


class TestTeamRollup:
    def test_passes_are_counted_per_team(self):
        log = EventLog(events=[
            a_pass(1, TEAM_A), a_pass(2, TEAM_A, outcome='incomplete'),
            a_pass(9, TEAM_B),
        ])
        stats = team_stats(log, TEAM_A, calibrated=False)
        assert stats.passes_attempted == 2
        assert stats.passes_completed == 1
        assert stats.pass_accuracy == pytest.approx(0.5)

    def test_length_and_direction_are_bucketed(self):
        log = EventLog(events=[
            a_pass(1, TEAM_A, bucket='short', direction='forward'),
            a_pass(2, TEAM_A, bucket='long', direction='forward'),
        ])
        stats = team_stats(log, TEAM_A, calibrated=True)
        assert stats.passes_by_length == {'short': 1, 'long': 1}
        assert stats.passes_by_direction == {'forward': 2}

    def test_direction_buckets_stay_empty_without_a_calibration(self):
        log = EventLog(events=[a_pass(1, TEAM_A, direction=None)])
        assert team_stats(log, TEAM_A, calibrated=False).passes_by_direction == {}

    def test_possession_share_carries_through(self):
        teams = a_report().to_json()['teams']
        assert teams[TEAM_A]['possession_pct'] == pytest.approx(0.6)


class TestTrackRollup:
    def test_a_cluster_sums_its_fragments_touches(self):
        track = a_report().to_json()['tracks'][0]
        assert track['touches'] == 2
        assert track['track_ids'] == [1, 2]

    def test_movement_sums_across_fragments_but_speed_takes_the_max(self):
        """Distance adds up; top speed does not."""
        report = a_report(calibrated=True)
        report.players = [
            PlayerReport(track_id=1, team=TEAM_A, movement=MovementStats(
                track_id=1, distance_m=100.0, top_speed_ms=6.0, sprint_count=1)),
            PlayerReport(track_id=2, team=TEAM_A, movement=MovementStats(
                track_id=2, distance_m=50.0, top_speed_ms=8.0, sprint_count=2)),
        ]
        track = report.to_json()['tracks'][0]
        assert track['distance_m'] == pytest.approx(150.0)
        assert track['top_speed_kmh'] == pytest.approx(8.0 * 3.6)
        assert track['sprint_count'] == 3


class TestShape:
    def test_the_schema_is_versioned(self):
        """There is no build step, so a frontend cannot be updated in lockstep."""
        assert a_report().to_json()['schema_version'] == SCHEMA_VERSION

    def test_touches_are_opt_in(self):
        """At thirty a second they dwarf everything else in the file."""
        assert 'touches' not in a_report().to_json()
        assert 'touches' in a_report().to_json(include_touches=True)

    def test_the_window_is_recorded(self):
        data = a_report().to_json(window={'start_s': 90, 'end_s': 105})
        assert data['window']['start_s'] == 90

    def test_both_teams_always_appear(self):
        """A team with no events is still a team, not a missing key."""
        teams = a_report().to_json()['teams']
        assert set(teams) == {TEAM_A, TEAM_B}


class TestShotMarks:
    """Shots as points, always attacking right.

    The flip lives here rather than in three renderers, so the thing worth
    testing is that it is a mirror and not a translation — a shot from the right
    wing must not migrate to the left one.
    """

    def shot(self, x, y, xg=0.2, outcome='saved', on_target=True):
        return Shot(
            event_id='s1', type='shot', timestamp_s=612.0, frame_index=1,
            team=TEAM_A, track_id=9, start_xy_px=(0.0, 0.0),
            start_m=(x, y), xg=xg, outcome=outcome, on_target=on_target,
        )

    def test_attacking_right_is_left_alone(self):
        mark = shot_marks([self.shot(95.0, 20.0)], 'right')[0]
        assert (mark['x_m'], mark['y_m']) == (95.0, 20.0)

    def test_attacking_left_is_mirrored_through_the_centre(self):
        """Both axes, not just the length.

        Mirroring x alone would keep a shot from the right wing on the right of
        the picture, when from the attacking team's point of view it came from
        their left. That is a wrong answer that looks entirely normal.
        """
        mark = shot_marks([self.shot(10.0, 20.0)], 'left')[0]
        assert mark['x_m'] == 95.0
        assert mark['y_m'] == 48.0

    def test_distance_to_the_attacked_goal_survives_the_flip(self):
        """The invariant the whole mirror exists to preserve."""
        right = shot_marks([self.shot(95.0, 34.0)], 'right')[0]
        left = shot_marks([self.shot(10.0, 34.0)], 'left')[0]
        assert right['x_m'] == left['x_m']

    def test_no_direction_means_no_map_rather_than_an_unflipped_one(self):
        assert shot_marks([self.shot(95.0, 20.0)], None) is None

    def test_a_calibrated_run_with_no_shots_is_an_empty_list(self):
        """Distinct from None. One says nobody shot, the other says we could
        not have placed a shot if they had."""
        assert shot_marks([], 'right') == []

    def test_a_shot_with_no_position_is_skipped_not_placed_at_zero(self):
        shot = self.shot(95.0, 20.0)
        shot = replace(shot, start_m=None)
        assert shot_marks([shot], 'right') == []

    def test_the_outcome_and_xg_travel_with_the_point(self):
        mark = shot_marks(
            [self.shot(99.0, 34.0, xg=0.62, outcome='goal', on_target=True)], 'right',
        )[0]
        assert mark['outcome'] == 'goal'
        assert mark['on_target'] is True
        assert mark['xg'] == 0.62
        assert mark['video_s'] == 612.0

    def test_it_is_json_safe(self):
        marks = shot_marks([self.shot(95.0, 20.0)], 'right')
        assert json.loads(json.dumps(marks))[0]['x_m'] == 95.0
