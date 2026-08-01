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

import numpy as np
import pytest

from cv.events import COMPLETED, EventLog, Pass
from cv.identity import PlayerCluster
from cv.metrics import MovementStats
from cv.pipeline import MatchReport, PlayerReport
from cv.possession import PossessionSummary
from cv.report_json import SCHEMA_VERSION, team_stats
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
