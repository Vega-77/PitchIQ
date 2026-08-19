"""Goalkeeper identification, and the refusals that keep it honest.

Getting the keeper wrong is unusually expensive: save percentage, distribution
and every keeper feature in the xG model all describe the wrong person at once,
and none of them look wrong. So the tests that matter most here are the ones
asserting that the module declines — no calibration, no answer; a centre-back
who drops deep, no answer; a referee working a diagonal, no answer.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_keeper.py -q
"""

from __future__ import annotations

import numpy as np
import pytest

from cv.calibration import Calibration
from cv.events import EventLog
from cv.keeper import (
    KeeperAssignment,
    KeeperDistribution,
    KeeperReport,
    _distribution_kind,
    goal_share,
    identify_keepers,
    keeper_reports,
)
from cv.frames import FrameRecord, FrameTable
from cv.pitch import Pitch
from cv.teams import TEAM_A, TEAM_B

FPS = 30.0
PITCH = Pitch()
L, W = PITCH.length_m, PITCH.width_m
MID_Y = W / 2
PX_PER_M = 20.0


def calibration():
    homography = np.array([
        [1 / PX_PER_M, 0.0, 0.0],
        [0.0, 1 / PX_PER_M, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)
    return Calibration(homography, PITCH, image_size=(4000, 2000))


def table_with(tracks_positions, frames=40, calibrated=True):
    """tracks_positions: {track_id: callable(frame) -> (x_m, y_m)}."""
    records = []
    for i in range(frames):
        rows = []
        for track_id, path in tracks_positions.items():
            x_m, y_m = path(i)
            px, py = x_m * PX_PER_M, y_m * PX_PER_M
            rows.append([track_id, px - 10, py - 36, px + 10, py, 0.9])
        records.append(FrameRecord(
            frame_index=i,
            timestamp_s=i / FPS,
            players=np.array(rows, dtype=np.float32).reshape(-1, 6),
        ))
    return FrameTable(
        fps=FPS, frame_width=4000, frame_height=2000,
        records=records,
        calibration=calibration() if calibrated else None,
    )


def fixed(x_m, y_m):
    return lambda i: (x_m, y_m)


ENDS = {TEAM_A: 'left', TEAM_B: 'right'}


class TestGoalShare:
    def test_a_keeper_on_their_line_scores_high(self):
        points = [(3.0, MID_Y)] * 20
        assert goal_share(points, PITCH, 'left') == pytest.approx(1.0)

    def test_a_winger_by_the_corner_flag_is_not_near_the_goal(self):
        """Close by straight-line distance, nowhere near being a goalkeeper.

        Distance alone would count a player hugging the touchline at the corner
        as guarding the goal; the lane test is what excludes them.
        """
        points = [(3.0, 1.0)] * 20
        assert goal_share(points, PITCH, 'left') == 0.0

    def test_a_midfielder_scores_zero(self):
        assert goal_share([(52.5, MID_Y)] * 20, PITCH, 'left') == 0.0

    def test_the_zone_is_measured_from_the_right_goal(self):
        points = [(3.0, MID_Y)] * 20
        assert goal_share(points, PITCH, 'right') == 0.0


class TestIdentification:
    def test_a_keeper_is_found_and_attributed(self):
        table = table_with({
            1: fixed(3.0, MID_Y),          # team A keeper, left goal
            2: fixed(L - 3.0, MID_Y),      # team B keeper, right goal
            3: fixed(52.5, MID_Y),         # a midfielder
        })
        keepers = identify_keepers(table, ENDS)
        assert keepers.method == 'colour+position'
        assert keepers.by_team[TEAM_A] == {1}
        assert keepers.by_team[TEAM_B] == {2}

    def test_a_midfielder_is_not_a_keeper(self):
        table = table_with({3: fixed(52.5, MID_Y)})
        assert identify_keepers(table, ENDS).all_tracks() == set()

    def test_a_centre_back_who_drops_in_is_not_a_keeper(self):
        """Occasionally deep is not the same as living there.

        This is the case the nearest-player-to-goal heuristic gets wrong, and
        the reason position is scored as a share over time rather than sampled
        at one moment.
        """
        def dropping(i):
            return (3.0, MID_Y) if i % 4 == 0 else (35.0, MID_Y)

        table = table_with({4: dropping})
        assert 4 not in identify_keepers(table, ENDS).all_tracks()

    def test_a_referee_working_a_diagonal_is_not_a_keeper(self):
        """Colour makes a referee a candidate; position rules them out."""
        def diagonal(i):
            return (5.0 + i * 2.5, 5.0 + i * 1.5)

        table = table_with({9: diagonal})
        assert 9 not in identify_keepers(table, ENDS).all_tracks()

    def test_a_briefly_seen_track_is_not_judged(self):
        table = table_with({1: fixed(3.0, MID_Y)}, frames=5)
        assert identify_keepers(table, ENDS).all_tracks() == set()

    def test_keepers_are_a_set_because_tracks_fragment(self):
        """One keeper is many tracks at the fragmentation measured here.

        Colour alone would only ever find pieces; the positional test recovers
        all of them, which is why by_team holds a set rather than one id.
        """
        table = table_with({
            1: fixed(3.0, MID_Y),
            2: fixed(4.0, MID_Y + 2),
            3: fixed(2.0, MID_Y - 3),
        })
        assert identify_keepers(table, ENDS).by_team[TEAM_A] == {1, 2, 3}


class TestRefusals:
    def test_no_calibration_means_no_answer(self):
        """Not a guess.

        The available fallback is the nearest-player-to-goal heuristic that
        xg_bridge already documents as occasionally catastrophic, and a wrong
        keeper poisons save percentage and xG at the same time.
        """
        table = table_with({1: fixed(3.0, MID_Y)}, calibrated=False)
        keepers = identify_keepers(table, ENDS)
        assert keepers.method == 'unavailable'
        assert keepers.all_tracks() == set()

    def test_no_ends_means_no_answer(self):
        """A keeper can be found without knowing the sides, but not attributed."""
        table = table_with({1: fixed(3.0, MID_Y)})
        assert identify_keepers(table, None).method == 'unavailable'

    def test_a_manual_override_wins(self):
        """A reviewer clicking the keeper once beats any of this."""
        table = table_with({1: fixed(52.5, MID_Y)})
        keepers = identify_keepers(table, ENDS, manual={TEAM_A: [1]})
        assert keepers.method == 'manual'
        assert keepers.is_keeper(1)
        assert keepers.team_of_keeper(1) == TEAM_A


class TestDistributionKind:
    def test_a_short_quick_ball_is_a_throw(self):
        assert _distribution_kind(15.0, hold_s=0.0) == 'throw'

    def test_a_long_ball_after_a_hold_is_a_punt(self):
        assert _distribution_kind(50.0, hold_s=2.0) == 'punt'

    def test_a_long_ball_with_no_hold_is_admitted_to_be_ambiguous(self):
        """Height would separate a punt from a drilled goal kick instantly.

        It is exactly what one camera cannot see, so this returns 'unknown'
        rather than picking. Never 'punt' — that is the claim we cannot make.
        """
        assert _distribution_kind(50.0, hold_s=0.0) == 'unknown'

    def test_a_mid_range_ball_is_a_kick(self):
        assert _distribution_kind(32.0, hold_s=0.0) == 'kick'


class TestReport:
    def test_save_pct_is_saves_over_shots_that_reached_the_line(self):
        report = KeeperReport(team=TEAM_A, saves=3, goals_conceded=1)
        assert report.save_pct == pytest.approx(0.75)

    def test_a_keeper_who_faced_nothing_has_no_percentage(self):
        """None, not 0.0 or 1.0 — neither is true of an untested keeper."""
        assert KeeperReport(team=TEAM_A).save_pct is None

    def test_accuracy_is_none_for_a_kind_never_attempted(self):
        report = KeeperReport(team=TEAM_A)
        report.distributions.append(
            KeeperDistribution(1.0, 'kick', 0.0, 35.0, True, 'middle')
        )
        assert report.accuracy('kick') == 1.0
        assert report.accuracy('punt') is None
        assert report.mean_distance_m('punt') is None

    def test_punches_are_absent_rather_than_zero(self):
        """Nothing distinguishes a punch from a catch without ball height.

        A zero would read as "the keeper never punched", which is a claim this
        pipeline cannot make.
        """
        assert 'punches' not in KeeperReport(team=TEAM_A).to_json()

    def test_json_is_all_primitives(self):
        import json
        report = KeeperReport(team=TEAM_A, track_ids=[1, 2], saves=2, goals_conceded=1)
        assert json.loads(json.dumps(report.to_json()))['save_pct'] is not None

    def test_the_furthest_sweep_is_absent_when_he_never_swept(self):
        """A maximum over an empty set is not 0.0 metres.

        The field starts at 0.0 because `max()` needs somewhere to start, and
        publishing that start would draw a keeper who held his line as one who
        came out and got exactly nowhere.
        """
        data = KeeperReport(team=TEAM_A, end_known=True).to_json()
        assert data['sweeper_actions'] == 0
        assert data['sweeper_max_distance_m'] is None

    def test_positional_figures_are_absent_when_no_end_was_known(self):
        """Nobody looked, which is not the same as nothing happened.

        Every one of these four is measured against the goal this keeper
        defends, so `keeper_reports` never computes them without one and they
        stay at the defaults. Sending the defaults would say a keeper claimed
        nothing and never left his line.
        """
        data = KeeperReport(team=TEAM_A, saves=2, goals_conceded=1).to_json()
        assert data['claims'] is None
        assert data['sweeper_actions'] is None
        assert data['sweeper_max_distance_m'] is None
        assert data['distributions'] is None
        # The shot figures do not need an end and must survive.
        assert data['saves'] == 2
        assert data['save_pct'] == pytest.approx(2 / 3)


class TestReportsForTheMatch:
    """`keeper_reports` itself — one report per team that had a keeper."""

    def assignment(self):
        return KeeperAssignment(by_team={TEAM_A: {7}}, method='colour+position')

    def test_an_end_it_knows_is_recorded_as_known(self):
        reports = keeper_reports(
            EventLog(), self.assignment(), PITCH, {TEAM_A: 'left'},
        )
        assert [r.end_known for r in reports] == [True]
        assert reports[0].to_json()['claims'] == 0

    def test_without_an_end_the_positional_figures_are_never_claimed(self):
        reports = keeper_reports(EventLog(), self.assignment(), PITCH, {})
        assert [r.end_known for r in reports] == [False]
        assert reports[0].to_json()['claims'] is None

    def test_a_team_with_no_keeper_gets_no_report(self):
        """One team can be identified and the other not, and often is."""
        assignment = KeeperAssignment(by_team={TEAM_A: {7}, TEAM_B: set()})
        reports = keeper_reports(EventLog(), assignment, PITCH, {TEAM_A: 'left'})
        assert [r.team for r in reports] == [TEAM_A]
