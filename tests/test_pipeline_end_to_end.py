"""analyse_match, start to finish, on a clip made of rectangles.

This is the one function every published number comes out of, and until the
detector seam reached it nothing could call it: `analyse_match` built its own
`TrackedFramePass`, so running it meant installing ultralytics and torch, which
`requirements-test.txt` deliberately leaves out and `tests/test_requirements.py`
actively forbids. Every subsystem below had unit tests; the assembly of them had
none.

That gap is the shape of the two worst bugs this repo has shipped, both still
recorded in comments: `ppda` was null in every report ever produced because
nothing handed `team_stats` a pitch, and `attach_xg` sat with no caller for
months while the coach's screen quietly filtered out the empty row. Neither is
visible to a unit test. Both are visible here.

So what these tests pin is the wiring — that each stage runs, that its output
reaches the report, and that the calibrated and uncalibrated paths differ in
exactly the documented ways. Treat this as a check that the chain executes, not
as a measurement. The footage is nine seconds of coloured boxes moving along
arithmetic; the pass is a pass because the script says so, not because anything
in the pipeline recognised football.

Run:  .venv/Scripts/python.exe -m pytest tests/test_pipeline_end_to_end.py -q
"""

from __future__ import annotations

import json

import pytest

from pitchiq.demo import (
    FPS,
    FRAMES,
    PITCH,
    SHOOTER,
    STRIKE_S,
    run,
    write_calibration,
    write_clip,
)
from pitchiq.events import SHOT_SPEED_PH_S
from pitchiq.report_export import build_report_json
from pitchiq.teams import TEAM_A, TEAM_B

STAGES = [
    'load the detector',
    'detect, track and sample colour',
    'ball',
    'camera',
    'possession',
    'identity',
    'thumbnails',
    'touches and events',
    'expected goals',
    'movement',
]


@pytest.fixture(scope='module')
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp('e2e') / 'match.mp4'
    if not write_clip(path):
        pytest.skip('no mp4v encoder available in this OpenCV build')
    return path


@pytest.fixture(scope='module')
def calibration_path(tmp_path_factory):
    """The demo's calibration, fitted to seven landmarks (`write_calibration`)."""
    return write_calibration(tmp_path_factory.mktemp('e2e') / 'match.calib.json')


@pytest.fixture(scope='module')
def calibrated(clip, calibration_path):
    return run(clip, calibration_path)


@pytest.fixture(scope='module')
def uncalibrated(clip):
    return run(clip, None)


def _shots(report):
    return list(report.events.shots()) if report.events else []


def _strike(report):
    """The touch that struck the ball at goal — the last one in the clip."""
    return max(report.touches.touches, key=lambda t: t.timestamp_s)


class TestTheChainRuns:
    def test_the_clip_is_read_end_to_end(self, calibrated, uncalibrated):
        assert calibrated.duration_s == pytest.approx(FRAMES / FPS)
        assert uncalibrated.duration_s == pytest.approx(FRAMES / FPS)

    def test_every_stage_ran_and_was_timed(self, calibrated):
        assert [s.name for s in calibrated.timings.stages] == STAGES

    def test_the_stages_that_need_a_calibration_are_skipped_without_one(self, uncalibrated):
        # Not a subset check. 'expected goals' and 'movement' are the last two,
        # and this is the assertion that fails the day one of them stops being
        # called at all — the way attach_xg stopped being called for months.
        assert [s.name for s in uncalibrated.timings.stages] == STAGES[:-2]

    def test_the_report_serialises_without_a_nan(self, calibrated, uncalibrated):
        for report in (calibrated, uncalibrated):
            json.dumps(build_report_json(report), allow_nan=False)

    def test_both_reports_carry_the_same_top_level_keys(self, calibrated, uncalibrated):
        assert sorted(build_report_json(calibrated)) == sorted(build_report_json(uncalibrated))

    def test_the_ball_was_followed_through_every_frame(self, calibrated):
        assert len(calibrated.ball.points) == FRAMES


class TestTeams:
    def test_ten_tracks_split_five_and_five(self, calibrated):
        teams = [p.team for p in calibrated.players]
        assert len(teams) == 10
        assert teams.count(TEAM_A) == 5 and teams.count(TEAM_B) == 5

    def test_the_red_shirts_are_team_b(self, calibrated):
        # `_kmeans_two` seeds farthest-first from `samples[0]`, so the cluster
        # holding the first track emitted ends up as label 1 — TEAM_B. Pinning
        # the label rather than only the split means a change in seeding fails
        # here, loudly, instead of silently moving the shot to the other team.
        by_track = {p.track_id: p.team for p in calibrated.players}
        assert [by_track[t] for t in range(100, 105)] == [TEAM_B] * 5
        assert [by_track[t] for t in range(105, 110)] == [TEAM_A] * 5

    def test_the_two_kits_are_measurably_different(self, calibrated):
        assert calibrated.kit_separation > 0

    def test_nobody_was_ruled_out(self, calibrated):
        assert not calibrated.participants.excluded

    def test_every_player_was_tracked_for_the_whole_clip(self, calibrated):
        minutes = {round(p.minutes_tracked, 4) for p in calibrated.players}
        assert len(minutes) == 1
        assert minutes.pop() == pytest.approx(FRAMES / FPS / 60.0, abs=1e-3)


class TestPossession:
    def test_the_team_with_the_ball_holds_it(self, calibrated):
        possession = calibrated.possession
        assert possession.team_b_s > 5.0
        assert possession.team_a_s == 0.0

    def test_the_camera_is_not_reported_as_having_moved(self, calibrated):
        assert calibrated.camera.checked is True
        assert calibrated.camera.shifts == []


class TestEvents:
    def test_the_pass_and_the_shot_both_land(self, calibrated):
        types = [e.type for e in calibrated.events.events]
        assert 'pass' in types and 'shot' in types

    def test_the_strike_reads_as_a_strike(self, calibrated):
        strike = _strike(calibrated)
        assert strike.timestamp_s == pytest.approx(STRIKE_S, abs=0.05)
        assert strike.track_id == 100 + SHOOTER
        assert strike.speed_after_ph_s > SHOT_SPEED_PH_S

    def test_the_shot_is_a_goal_at_the_end_that_team_attacks(self, calibrated):
        shots = _shots(calibrated)
        assert len(shots) == 1
        shot = shots[0]
        assert shot.team == TEAM_B
        assert calibrated.attacking_ends[TEAM_B] == 'left'
        assert shot.outcome == 'goal'
        assert shot.on_target is True

    def test_the_shot_is_placed_on_the_pitch(self, calibrated):
        shot = _shots(calibrated)[0]
        x_m, y_m = shot.start_m
        assert x_m == pytest.approx(16.0, abs=2.0)
        assert y_m == pytest.approx(PITCH.width_m / 2, abs=3.0)
        assert shot.distance_to_goal_m == pytest.approx(16.0, abs=2.0)
        assert 'in_box' in shot.tags

    def test_without_a_calibration_no_event_has_a_position(self, uncalibrated):
        assert uncalibrated.events.events
        assert all(e.start_m is None for e in uncalibrated.events.events)

    def test_without_a_calibration_there_are_no_shots(self, uncalibrated):
        # Not a gap in coverage — a shot needs metres to know it went goalward,
        # and refusing to guess is the documented behaviour.
        assert _shots(uncalibrated) == []


class TestExpectedGoals:
    def test_the_shot_carries_a_probability_from_the_real_model(self, calibrated):
        pytest.importorskip('onnxruntime')
        shot = _shots(calibrated)[0]
        assert shot.xg is not None
        assert 0.0 < shot.xg < 1.0

    def test_the_header_reading_is_computed_alongside_it(self, calibrated):
        pytest.importorskip('onnxruntime')
        shot = _shots(calibrated)[0]
        assert shot.xg_header is not None
        assert 0.0 < shot.xg_header < 1.0

    def test_the_probability_reaches_the_team_total(self, calibrated):
        pytest.importorskip('onnxruntime')
        shot = _shots(calibrated)[0]
        totals = build_report_json(calibrated)['teams']
        # The JSON rounds to three places on the way out; the point is that the
        # team total is the shot's number and not a zero or a None.
        assert totals[TEAM_B]['xg'] == pytest.approx(shot.xg, abs=1e-3)

    def test_the_uncalibrated_run_claims_no_expected_goals(self, uncalibrated):
        totals = build_report_json(uncalibrated)['teams']
        assert totals.get(TEAM_B, {}).get('xg') is None


class TestWhatOnlyACalibrationBuys:
    def test_the_pitch_is_known_only_when_calibrated(self, calibrated, uncalibrated):
        assert calibrated.pitch == PITCH
        assert uncalibrated.pitch is None

    def test_movement_is_measured_only_when_calibrated(self, calibrated, uncalibrated):
        assert all(p.movement is not None for p in calibrated.players)
        assert all(p.movement is None for p in uncalibrated.players)

    def test_coverage_and_territory_need_metres(self, calibrated, uncalibrated):
        assert calibrated.coverage is not None
        assert calibrated.territory is not None
        assert uncalibrated.coverage is None
        assert uncalibrated.territory is None

    def test_the_keeper_search_says_so_when_it_could_not_run(self, calibrated, uncalibrated):
        assert calibrated.keepers.method == 'colour+position'
        assert uncalibrated.keepers.method == 'unavailable'

    def test_the_missing_calibration_is_warned_about(self, calibrated, uncalibrated):
        assert not any('no calibration supplied' in w for w in calibrated.warnings)
        assert any('no calibration supplied' in w for w in uncalibrated.warnings)

    def test_a_still_camera_raises_no_camera_warning(self, calibrated, uncalibrated):
        for report in (calibrated, uncalibrated):
            assert not any('camera moved' in w for w in report.warnings)
