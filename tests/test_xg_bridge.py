"""The bridge from CV output to the existing xG model.

Most of this file exists to catch one specific class of failure: a mismatch
between the features produced here and the features the model was trained on.
That kind of mistake does not raise. It returns a plausible number that is
quietly wrong, which is far worse than a crash.

The pipeline itself cannot be tested end to end until there is footage from a
camera that holds still — see ROADMAP.md Phase 4.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from cv.calibration import Calibration
from cv.pitch import STATSBOMB_LENGTH, MatchOrientation, Pitch
from cv.xg_bridge import (
    FEATURE_ORDER,
    ShotContext,
    build_features,
    feature_vector,
    in_shot_cone,
    shot_context_from_tracking,
)

REPO = Path(__file__).resolve().parents[1]
TRAINING_SCRIPT = REPO / 'PitchIQHelper' / 'main.py'


@pytest.fixture
def pitch() -> Pitch:
    return Pitch()


class TestFeatureParity:
    """The features here must match the ones the model was trained on."""

    def test_order_matches_the_training_script(self):
        """Read FEATURES straight out of main.py and compare.

        Hard-coding the expected list here would defeat the purpose: it would
        pass happily while main.py drifted away from it.
        """
        source = TRAINING_SCRIPT.read_text(encoding='utf-8')
        match = re.search(r'FEATURES\s*=\s*\[(.*?)\]', source, re.S)
        assert match, 'could not find FEATURES in main.py'

        trained = re.findall(r'"([a-z_]+)"', match.group(1))

        assert trained == FEATURE_ORDER, (
            'feature order drifted from the trained model.\n'
            f'  main.py:    {trained}\n'
            f'  xg_bridge:  {FEATURE_ORDER}'
        )

    def test_vector_has_twelve_columns(self, pitch):
        vector = feature_vector(ShotContext(shooter_m=(95.0, 34.0)), pitch)
        assert vector.shape == (1, 11)
        assert vector.dtype == np.float32

    def test_vector_follows_the_declared_order(self, pitch):
        context = ShotContext(shooter_m=(95.0, 34.0), is_header=True)
        features = build_features(context, pitch)
        vector = feature_vector(context, pitch)

        for i, name in enumerate(FEATURE_ORDER):
            assert vector[0][i] == pytest.approx(features[name], rel=1e-6)


class TestGeometry:
    def test_closer_shots_are_closer(self, pitch):
        near = build_features(ShotContext(shooter_m=(100.0, 34.0)), pitch)
        far = build_features(ShotContext(shooter_m=(60.0, 34.0)), pitch)

        assert near['distance_to_goal'] < far['distance_to_goal']

    def test_central_shots_have_a_wider_angle(self, pitch):
        central = build_features(ShotContext(shooter_m=(95.0, 34.0)), pitch)
        wide = build_features(ShotContext(shooter_m=(95.0, 4.0)), pitch)

        assert central['angle_to_goal'] > wide['angle_to_goal']

    def test_attacking_the_other_way_is_handled(self, pitch):
        """The same spot is close to one goal and far from the other.

        Getting this wrong produces confident xG measured from the goal the
        team is defending, which is the failure MatchOrientation exists to
        prevent.
        """
        spot = (95.0, 34.0)
        attacking_right = build_features(
            ShotContext(shooter_m=spot, attacking_end='right'), pitch)
        attacking_left = build_features(
            ShotContext(shooter_m=spot, attacking_end='left'), pitch)

        assert attacking_right['distance_to_goal'] < 15
        assert attacking_left['distance_to_goal'] > 80

    def test_angle_is_symmetric_about_the_centre(self, pitch):
        left = build_features(ShotContext(shooter_m=(95.0, 24.0)), pitch)
        right = build_features(ShotContext(shooter_m=(95.0, 44.0)), pitch)

        assert left['angle_to_goal'] == pytest.approx(right['angle_to_goal'], rel=1e-6)

    def test_shot_from_the_goal_line_is_almost_zero_distance(self, pitch):
        features = build_features(ShotContext(shooter_m=(105.0, 34.0)), pitch)
        assert features['distance_to_goal'] == pytest.approx(0.0, abs=0.5)


class TestDefenders:
    def test_a_defender_in_the_way_is_counted(self, pitch):
        # Directly between a central shooter and the goal.
        blocked = build_features(ShotContext(
            shooter_m=(90.0, 34.0), defenders_m=[(97.0, 34.0)],
        ), pitch)

        assert blocked['defenders_in_cone'] == 1
        assert blocked['defender_pressure'] > 0

    def test_a_defender_behind_the_shooter_is_not(self, pitch):
        behind = build_features(ShotContext(
            shooter_m=(90.0, 34.0), defenders_m=[(80.0, 34.0)],
        ), pitch)

        assert behind['defenders_in_cone'] == 0

    def test_a_defender_out_wide_is_not(self, pitch):
        wide = build_features(ShotContext(
            shooter_m=(90.0, 34.0), defenders_m=[(95.0, 5.0)],
        ), pitch)

        assert wide['defenders_in_cone'] == 0

    def test_closer_defenders_apply_more_pressure(self, pitch):
        close = build_features(ShotContext(
            shooter_m=(90.0, 34.0), defenders_m=[(92.0, 34.0)]), pitch)
        distant = build_features(ShotContext(
            shooter_m=(90.0, 34.0), defenders_m=[(102.0, 34.0)]), pitch)

        assert close['defender_pressure'] > distant['defender_pressure']

    def test_cone_test_matches_the_training_definition(self):
        ball = np.array([100.0, 40.0])
        assert in_shot_cone(np.array([110.0, 40.0]), ball)
        assert not in_shot_cone(np.array([90.0, 40.0]), ball)


class TestKeeper:
    def test_absent_keeper_uses_the_training_fallback(self, pitch):
        features = build_features(ShotContext(shooter_m=(95.0, 34.0)), pitch)

        # Same defaults main.py applies when a freeze frame has no keeper.
        assert features['keeper_distance_to_goal'] == 5.0
        assert features['keeper_off_line'] == 0.0

    def test_a_keeper_off_their_line_is_flagged(self, pitch):
        advanced = build_features(ShotContext(
            shooter_m=(95.0, 34.0), keeper_m=(95.0, 34.0)), pitch)
        on_line = build_features(ShotContext(
            shooter_m=(95.0, 34.0), keeper_m=(104.5, 34.0)), pitch)

        assert advanced['keeper_off_line'] == 1.0
        assert on_line['keeper_off_line'] == 0.0


class TestBodyPart:
    def test_foot_and_header_are_mutually_exclusive(self, pitch):
        foot = build_features(ShotContext(shooter_m=(95.0, 34.0)), pitch)
        head = build_features(ShotContext(shooter_m=(95.0, 34.0), is_header=True), pitch)

        assert (foot['is_foot'], foot['is_header']) == (1.0, 0.0)
        assert (head['is_foot'], head['is_header']) == (0.0, 1.0)


class TestShotHeight:
    def test_the_model_is_not_told_a_shot_height(self, pitch):
        """There is no z-axis feature any more, and there should not be one.

        `shot_height` was `end_location[2]` in the training script: the height
        the ball finished at, which is the outcome and not the shot. A single
        fixed camera cannot recover the z axis at all, so this module could only
        ever have substituted a constant — and a constant claim about the
        outcome is a wrong claim about the outcome. Dropped from the model on
        2026-08-06; see the module docstring for what it was costing.
        """
        features = build_features(ShotContext(shooter_m=(95.0, 34.0)), pitch)
        assert 'shot_height' not in features
        assert 'shot_height' not in FEATURE_ORDER


class TestPitchSizeSensitivity:
    def test_pitch_size_changes_what_the_model_sees(self):
        """OPEN QUESTION, pinned here so it cannot be forgotten.

        The conversion normalises by the configured pitch length, so the same
        real distance becomes a different StatsBomb distance on a different
        pitch: a penalty spot is 11m out on any field, but reads as ~13.9 units
        on a 95m pitch and ~12.0 on a 110m one. The model then sees two
        different shots.

        Whether that is correct depends on how StatsBomb itself normalises, and
        that has not been confirmed against their documentation. If they assume
        a standard pitch rather than scaling to the real one, this conversion
        should use a fixed metres-per-unit factor instead.

        Practically it means the pitch dimensions in the calibration are not a
        cosmetic setting — they move every xG number. Measure the real field.
        """
        small = Pitch(length_m=95.0, width_m=60.0)
        large = Pitch(length_m=110.0, width_m=70.0)

        small_shot = build_features(
            ShotContext(shooter_m=(95.0 - 11.0, 30.0)), small)
        large_shot = build_features(
            ShotContext(shooter_m=(110.0 - 11.0, 35.0)), large)

        assert small_shot['distance_to_goal'] == pytest.approx(13.9, abs=0.2)
        assert large_shot['distance_to_goal'] == pytest.approx(12.0, abs=0.2)
        assert small_shot['distance_to_goal'] > large_shot['distance_to_goal']

    def test_the_attacked_goal_is_always_at_x_120(self):
        """Whatever the pitch size, the goal being attacked must land at the
        coordinate the model was trained to expect."""
        for pitch in (Pitch(95.0, 60.0), Pitch(105.0, 68.0), Pitch(110.0, 70.0)):
            x, _ = pitch.to_statsbomb(*pitch.right_goal_centre, attacking_end='right')
            assert x == pytest.approx(STATSBOMB_LENGTH)


class TestOrientationIntegration:
    def test_second_half_flips_which_goal_is_attacked(self, pitch):
        orientation = MatchOrientation(home_attacks_first_half='right')
        spot = (95.0, 34.0)

        first = build_features(ShotContext(
            shooter_m=spot,
            attacking_end=orientation.attacking_end('us', 'first_half'),
        ), pitch)
        second = build_features(ShotContext(
            shooter_m=spot,
            attacking_end=orientation.attacking_end('us', 'kickoff_2nd'),
        ), pitch)

        assert first['distance_to_goal'] < second['distance_to_goal']


class TestKeeperGuess:
    """Who gets taken for the goalkeeper when nobody says.

    This is the one place in the bridge where a wrong answer looks entirely
    normal: every feature still computes, the vector is still twelve wide, and
    the number that comes out is simply about a different person. It went
    untested from the day it was written until the day something finally called
    it, and it was inverted the whole time.
    """

    SCALE = 20.0

    def calibration(self, pitch):
        homography = np.array([
            [1 / self.SCALE, 0.0, 0.0],
            [0.0, 1 / self.SCALE, 0.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float64)
        return Calibration(homography, pitch, image_size=(1920, 1080))

    def box(self, x_m, y_m):
        """A box whose bottom centre — the only part read — sits on the pitch."""
        x, y = x_m * self.SCALE, y_m * self.SCALE
        return (x - 10, y - 36, x + 10, y)

    def context(self, pitch, players, **kwargs):
        teams = {track: team for track, team, _, _ in players}
        return shot_context_from_tracking(
            ball_px=(95 * self.SCALE, 34 * self.SCALE),
            player_boxes=[(t, self.box(x, y)) for t, _, x, y in players],
            shooter_track=7,
            team_of=teams.get,
            calibration=self.calibration(pitch),
            orientation=MatchOrientation(home_attacks_first_half='right'),
            side='us',
            period='first_half',
            **kwargs,
        )

    def test_the_keeper_is_the_one_in_the_goal_being_shot_at(self, pitch):
        """Not the deepest opponent, which is the far end of the pitch.

        Shooting right at a keeper on his line, with a centre-back who has
        dropped fifty metres the other way. Guessing the centre-back tells the
        model the goal is empty.
        """
        context = self.context(pitch, [
            (7, 'a', 95.0, 34.0),          # shooter
            (30, 'b', 103.0, 34.0),        # keeper, on his line
            (20, 'b', 40.0, 34.0),         # deepest opponent, nowhere near it
        ])
        assert context.keeper_m == (103.0, 34.0)
        assert context.defenders_m == [(40.0, 34.0)]

    def test_it_flips_with_the_half(self, pitch):
        context = shot_context_from_tracking(
            ball_px=(10 * self.SCALE, 34 * self.SCALE),
            player_boxes=[
                (7, self.box(10.0, 34.0)),
                (30, self.box(2.0, 34.0)),
                (20, self.box(65.0, 34.0)),
            ],
            shooter_track=7,
            team_of={7: 'a', 30: 'b', 20: 'b'}.get,
            calibration=self.calibration(pitch),
            orientation=MatchOrientation(home_attacks_first_half='right'),
            side='us',
            period='kickoff_2nd',
        )
        assert context.attacking_end == 'left'
        assert context.keeper_m == (2.0, 34.0)

    def test_naming_the_keeper_beats_guessing(self, pitch):
        """A named keeper is used even when he is nowhere sensible.

        The point is that the caller's answer wins outright — `cv/keeper.py` and
        a human both know things this geometry does not.
        """
        context = self.context(pitch, [
            (7, 'a', 95.0, 34.0),
            (30, 'b', 103.0, 34.0),
            (20, 'b', 40.0, 34.0),
        ], keeper_track=20)
        assert context.keeper_m == (40.0, 34.0)
        assert context.defenders_m == [(103.0, 34.0)]

    def test_no_opponents_leaves_the_keeper_unknown(self, pitch):
        """Rather than promoting a team-mate or inventing one."""
        context = self.context(pitch, [(7, 'a', 95.0, 34.0), (9, 'a', 90.0, 30.0)])
        assert context.keeper_m is None
        assert context.defenders_m == []
