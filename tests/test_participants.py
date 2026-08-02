"""Telling players apart from everyone else in the picture.

Every scene here is built by hand, because the question "was that person
playing?" has no ground truth on real footage without somebody watching it.
What the tests can prove is that the classifier does what its docstring claims:
that it rejects people who never moved and people who are demonstrably not on
the pitch, that it keeps everyone else including anyone it is unsure about, and
that none of its answers change when the camera zooms.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_participants.py -q
"""

from __future__ import annotations

import math

import numpy as np

from cv.calibration import Calibration
from cv.frames import FrameRecord, FrameTable
from cv.participants import (
    MIN_SCREEN_TIME_S,
    ROLE_OFFFIELD,
    ROLE_OFFICIAL,
    ROLE_PLAYER,
    ROLE_UNSURE,
    classify_participants,
)
from cv.pitch import Pitch
from cv.teams import TEAM_A, TEAM_B, UNKNOWN

FPS = 10.0
PLAYER_H = 60.0
WIDTH, HEIGHT = 1280, 720

# A run long enough to clear MIN_SCREEN_TIME_S with room to spare.
FRAMES = int(MIN_SCREEN_TIME_S * FPS) + 40

# The pitch, laid into the frame at ten pixels to the metre with a small margin
# so there is room to stand *off* it — which is the whole point of the
# calibrated tests below.
PITCH = Pitch()
PX_PER_M = 10.0
ORIGIN_PX = (100.0, 20.0)


def calibration():
    homography = np.array([
        [1 / PX_PER_M, 0.0, -ORIGIN_PX[0] / PX_PER_M],
        [0.0, 1 / PX_PER_M, -ORIGIN_PX[1] / PX_PER_M],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)
    return Calibration(homography, PITCH, image_size=(WIDTH, HEIGHT))


def player(track_id, x, y, height=PLAYER_H):
    """A box whose ground point is (x, y) and whose height is `height`."""
    width = height / 3
    return [track_id, x - width / 2, y - height, x + width / 2, y, 0.9]


def build(frames, *, fps=FPS, teams=None, width=WIDTH, height=HEIGHT,
          calibrated=False):
    """frames: [[player rows]] — one list of rows per frame."""
    records = [
        FrameRecord(
            frame_index=i,
            timestamp_s=i / fps,
            players=np.array(rows, dtype=np.float32).reshape(-1, 6),
        )
        for i, rows in enumerate(frames)
    ]
    return FrameTable(
        fps=fps, frame_width=width, frame_height=height,
        records=records, team_by_track=teams or {},
        calibration=calibration() if calibrated else None,
    )


def crowd_xy(n, i):
    """Where crowd player `n` stands on frame `i`.

    Each one orbits its own patch of the pitch. They have to genuinely move:
    a fixture of statues would be rejected by the classifier, correctly, and
    then prove nothing about anybody else.
    """
    angle = i / 25.0 + n * 0.8
    return (
        300.0 + (n % 4) * 220.0 + math.cos(angle) * 120.0,
        250.0 + (n // 4) * 220.0 + math.sin(angle) * 120.0,
    )


def crowd(i, exclude=(), factor=1.0):
    """Eight kit-classified players spread across the frame, as on a real pitch."""
    rows = []
    for n in range(8):
        if n + 10 in exclude:
            continue
        x, y = crowd_xy(n, i)
        rows.append(player(10 + n, x * factor, y * factor, PLAYER_H * factor))
    return rows


def crowd_teams():
    return {10 + n: (TEAM_A if n < 4 else TEAM_B) for n in range(8)}


class TestStillness:
    def test_a_motionless_person_is_rejected(self):
        frames = [crowd(i) + [player(1, 640.0, 400.0)] for i in range(FRAMES)]
        teams = {**crowd_teams(), 1: TEAM_A}

        verdict = classify_participants(build(frames, teams=teams)).by_track[1]
        assert verdict.role == ROLE_OFFFIELD
        assert 'body length' in verdict.reason

    def test_rejection_is_reported_and_auditable(self):
        frames = [crowd(i) + [player(1, 640.0, 400.0)] for i in range(FRAMES)]
        report = classify_participants(build(frames, teams=crowd_teams()))

        assert report.excluded == frozenset({1})
        assert not report.is_player(1)
        assert all(report.is_player(t) for t in range(10, 18))

        row = next(r for r in report.to_json() if r['track_id'] == 1)
        assert row['reason']
        assert row['role'] == ROLE_OFFFIELD

    def test_the_crowd_of_real_players_survives_its_own_fixture(self):
        """If the eight orbiting players were rejected, nothing else here means
        anything — every other test compares a suspect against them."""
        report = classify_participants(
            build([crowd(i) for i in range(FRAMES)], teams=crowd_teams())
        )
        assert report.excluded == frozenset()
        assert all(v.role == ROLE_PLAYER for v in report.by_track.values())


class TestOffPitch:
    """The calibrated test, and the one that catches a warming-up substitute."""

    def sub_behind_the_goal(self):
        # Jogging up and down six metres beyond the byline. Moves exactly like a
        # player, because they are one — just not one in the match.
        frames = []
        for i in range(FRAMES):
            x = ORIGIN_PX[0] - 60.0
            y = 150.0 + math.sin(i / 20.0) * 130.0
            frames.append(crowd(i) + [player(1, x, y)])
        return frames

    def test_a_substitute_behind_the_goal_is_rejected(self):
        table = build(self.sub_behind_the_goal(),
                      teams={**crowd_teams(), 1: TEAM_A}, calibrated=True)
        verdict = classify_participants(table).by_track[1]

        assert verdict.spread_ph > 1.5          # stillness would never catch them
        assert verdict.off_pitch_share == 1.0
        assert verdict.role == ROLE_OFFFIELD
        assert 'off the pitch' in verdict.reason

    def test_without_a_calibration_the_same_person_is_kept(self):
        table = build(self.sub_behind_the_goal(),
                      teams={**crowd_teams(), 1: TEAM_A}, calibrated=False)
        verdict = classify_participants(table).by_track[1]

        # None, not zero. Zero would claim we looked and they were on the pitch.
        assert verdict.off_pitch_share is None
        assert verdict.role == ROLE_PLAYER

    def test_players_on_the_pitch_are_not_rejected_by_it(self):
        table = build([crowd(i) for i in range(FRAMES)],
                      teams=crowd_teams(), calibrated=True)
        report = classify_participants(table)

        assert report.excluded == frozenset()
        assert all(v.off_pitch_share == 0.0 for v in report.by_track.values())


class TestPlayers:
    def test_someone_running_is_never_rejected(self):
        frames = [
            crowd(i, exclude=(10,)) + [player(10, 200.0 + i * 3.0, 300.0 + i * 1.5)]
            for i in range(FRAMES)
        ]
        report = classify_participants(build(frames, teams=crowd_teams()))

        assert report.by_track[10].role == ROLE_PLAYER
        assert report.excluded == frozenset()

    def test_being_at_the_frame_edge_is_never_enough_to_reject(self):
        """A wide player on a camera framing the whole pitch is at the edge of
        the picture for most of the match. That is a fact about the camera."""
        frames = [
            crowd(i) + [player(1, 30.0, 200.0 + (i * 9.0) % 400)]
            for i in range(FRAMES)
        ]
        teams = {**crowd_teams(), 1: TEAM_B}

        verdict = classify_participants(build(frames, teams=teams)).by_track[1]
        assert verdict.edge_share > 0.9
        assert verdict.role == ROLE_PLAYER


class TestOfficials:
    def test_an_unknown_kit_that_moves_is_flagged_not_dropped(self):
        frames = [
            crowd(i) + [player(1, 500.0 + math.cos(i / 18.0) * 200.0,
                               400.0 + math.sin(i / 18.0) * 150.0)]
            for i in range(FRAMES)
        ]
        teams = {**crowd_teams(), 1: UNKNOWN}

        report = classify_participants(build(frames, teams=teams))
        assert report.by_track[1].role == ROLE_OFFICIAL
        assert report.officials == frozenset({1})

        # The whole point: flagged, but still counted as playing.
        assert report.is_player(1)
        assert 1 not in report.excluded


class TestUnsure:
    def test_too_brief_to_judge_is_kept(self):
        short = int(MIN_SCREEN_TIME_S * FPS) - 20
        frames = [crowd(i) + [player(1, 640.0, 400.0)] for i in range(short)]
        teams = {**crowd_teams(), 1: TEAM_A}

        report = classify_participants(build(frames, teams=teams))
        # Motionless, and would be rejected outright given more evidence.
        assert report.by_track[1].role == ROLE_UNSURE
        assert report.is_player(1)

    def test_an_unseen_track_counts_as_playing(self):
        report = classify_participants(
            build([crowd(i) for i in range(FRAMES)], teams=crowd_teams())
        )
        assert report.is_player(9999)

    def test_an_empty_table_is_not_an_error(self):
        report = classify_participants(
            FrameTable(fps=FPS, frame_width=WIDTH, frame_height=HEIGHT)
        )
        assert report.by_track == {}
        assert report.excluded == frozenset()


class TestScaleInvariance:
    """The camera zooms, so nothing here may be measured in pixels."""

    def scenes(self, factor):
        """The same match, filmed from twice as close.

        Track 1 stands still; track 2 runs. Both answers must survive the zoom.
        """
        frames = []
        for i in range(FRAMES):
            rows = crowd(i, factor=factor)
            rows.append(player(1, 640.0 * factor, 400.0 * factor, PLAYER_H * factor))
            rows.append(player(2, (200.0 + i * 3.0) * factor,
                               (300.0 + i * 1.5) * factor, PLAYER_H * factor))
            frames.append(rows)

        teams = {**crowd_teams(), 1: TEAM_A, 2: TEAM_A}
        return build(frames, teams=teams,
                     width=int(WIDTH * factor), height=int(HEIGHT * factor))

    def test_doubling_every_coordinate_changes_no_verdict(self):
        plain = classify_participants(self.scenes(1.0))
        zoomed = classify_participants(self.scenes(2.0))

        assert {t: v.role for t, v in plain.by_track.items()} == \
               {t: v.role for t, v in zoomed.by_track.items()}

        # And the scale-free measurements themselves agree, not just the labels.
        for track_id, verdict in plain.by_track.items():
            other = zoomed.by_track[track_id]
            assert abs(verdict.spread_ph - other.spread_ph) < 1e-4
            assert abs(verdict.travel_ph_per_min - other.travel_ph_per_min) < 1e-3

    def test_the_motionless_track_is_still_rejected_when_zoomed(self):
        zoomed = classify_participants(self.scenes(2.0))
        assert zoomed.by_track[1].role == ROLE_OFFFIELD
        assert zoomed.by_track[2].role == ROLE_PLAYER
