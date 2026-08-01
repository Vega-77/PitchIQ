"""Touch segmentation, on hand-built frames with no video and no detector.

These tests can prove the algorithm does what its docstring says. They cannot
prove the thresholds are right — that needs a human watching the same footage,
and the module says so at length. What they defend against is the class of bug
that would survive that human check anyway: a detector that fires on a ball
merely rolling past someone, one that invents touches inside a span where the
ball was never seen, or one that reports a confident attribution when two
players were equally close.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_touches.py -q
"""

from __future__ import annotations

import numpy as np
import pytest

from cv.frames import FrameRecord, FrameTable
from cv.teams import TEAM_A, TEAM_B
from cv.touches import (
    MAX_GAP_FRAMES,
    TouchConfidence,
    find_gaps,
    local_scale_px,
    segment_touches,
)

FPS = 30.0
PLAYER_H = 60.0          # pixels; one "player height" in these fixtures


def player(track_id, x, y, height=PLAYER_H):
    """A box whose ground point is (x, y) and whose height is `height`."""
    width = height / 3
    return [track_id, x - width / 2, y - height, x + width / 2, y, 0.9]


def build(frames, *, fps=FPS, teams=None, calibration=None):
    """frames: [(ball_xy | None, observed, [player rows])]."""
    records = []
    for i, (ball, observed, rows) in enumerate(frames):
        records.append(FrameRecord(
            frame_index=i,
            timestamp_s=i / fps,
            players=np.array(rows, dtype=np.float32).reshape(-1, 6),
            ball_xy=ball,
            ball_observed=observed,
        ))
    return FrameTable(
        fps=fps, frame_width=1280, frame_height=720,
        records=records,
        team_by_track=teams or {},
        calibration=calibration,
    )


def straight_pass(n=40, y=400.0, x0=100.0, step=12.0, players=None, observed=True):
    """A ball moving at constant speed in a straight line."""
    rows = players if players is not None else [player(1, 600.0, y)]
    return [((x0 + i * step, y), observed, rows) for i in range(n)]


# ------------------------------------------------------------------ scale


class TestLocalScale:
    def test_uses_the_players_nearest_the_ball(self):
        """The frame median is the wrong scale wherever the ball is.

        Near players tower over far ones on a wide shot, so a frame median
        makes the touch radius far too generous at the top of the picture and
        far too mean at the bottom.
        """
        boxes = [
            (1, (0, 0, 20, 200)),      # near camera, 200px tall, far from ball
            (2, (0, 0, 20, 200)),
            (3, (0, 0, 20, 200)),
            (4, (990, 340, 1010, 400)),   # by the ball, 60px tall
            (5, (995, 340, 1015, 400)),
            (6, (1005, 340, 1025, 400)),
        ]
        assert local_scale_px((1000.0, 400.0), boxes, k=3) == pytest.approx(60.0)

    def test_falls_back_to_the_frame_median_when_there_are_few_players(self):
        boxes = [(1, (0, 0, 20, 100)), (2, (0, 0, 20, 200))]
        assert local_scale_px((0.0, 0.0), boxes, k=3) == pytest.approx(150.0)

    def test_an_empty_frame_has_no_scale(self):
        assert local_scale_px((0.0, 0.0), []) == 0.0


# ------------------------------------------------------------------ the core


class TestNoTouch:
    def test_a_ball_rolling_past_a_stationary_player_is_not_a_touch(self):
        """The most important negative case in this file.

        Proximity alone fires here — the ball passes within a stride of a
        player standing still. Nothing about the ball changes, so nothing
        happened, and a detector that reports a touch here would report several
        hundred a match.
        """
        frames = straight_pass(n=60, players=[player(1, 500.0, 400.0)])
        assert len(segment_touches(build(frames))) == 0

    def test_a_ball_that_turns_with_nobody_near_is_not_a_touch(self):
        """A bounce off the turf or a post. Motion alone is not enough either."""
        frames = []
        for i in range(40):
            x = 100.0 + i * 12.0 if i < 20 else 100.0 + (40 - i) * 12.0
            frames.append(((x, 400.0), True, [player(1, 900.0, 100.0)]))
        assert len(segment_touches(build(frames))) == 0

    def test_a_frame_with_no_players_yields_nothing(self):
        frames = [((100.0 + i * 10, 400.0), True, []) for i in range(30)]
        assert len(segment_touches(build(frames))) == 0


class TestTouchDetection:
    def reversal(self, at=20, n=40, track=7, x=340.0):
        """A ball that reverses direction beside a player standing at the turn."""
        frames = []
        for i in range(n):
            bx = 100.0 + i * 12.0 if i <= at else 100.0 + (2 * at - i) * 12.0
            frames.append(((bx, 400.0), True, [
                player(track, x, 400.0),
                player(99, 1100.0, 400.0),
            ]))
        return frames

    def test_a_reversal_beside_a_player_is_one_touch(self):
        touches = segment_touches(build(self.reversal()))
        assert len(touches) == 1
        assert touches.touches[0].track_id == 7

    def test_the_touch_lands_on_the_frame_the_ball_turned(self):
        touches = segment_touches(build(self.reversal(at=20)))
        assert touches.touches[0].frame_index == pytest.approx(20, abs=2)

    def test_the_team_comes_from_the_table(self):
        table = build(self.reversal(track=7), teams={7: TEAM_B, 99: TEAM_A})
        assert segment_touches(table).touches[0].team == TEAM_B

    def test_metres_are_absent_without_a_calibration(self):
        touches = segment_touches(build(self.reversal()))
        assert touches.touches[0].ball_m is None

    def test_a_trap_that_kills_the_ball_counts(self):
        """Speed change is used unsigned, so stopping the ball is a touch too."""
        frames = []
        for i in range(40):
            bx = 100.0 + i * 14.0 if i < 20 else 100.0 + 20 * 14.0
            frames.append(((bx, 400.0), True, [player(3, 380.0, 400.0)]))
        touches = segment_touches(build(frames))
        assert len(touches) == 1
        assert touches.touches[0].track_id == 3


class TestScaleInvariance:
    def test_doubling_every_pixel_gives_an_identical_touch_list(self):
        """The property the whole module rests on.

        A zooming camera changes every pixel distance without anything on the
        pitch changing. If the answer moved with the zoom, none of this would
        mean anything on the only footage that exists.
        """
        def scenario(k):
            frames = []
            for i in range(40):
                bx = (100.0 + i * 12.0) if i <= 20 else (100.0 + (40 - i) * 12.0)
                frames.append((
                    (bx * k, 400.0 * k), True,
                    [player(7, 340.0 * k, 400.0 * k, height=PLAYER_H * k),
                     player(8, 1100.0 * k, 400.0 * k, height=PLAYER_H * k)],
                ))
            return segment_touches(build(frames))

        small, large = scenario(1.0), scenario(2.0)
        assert [t.frame_index for t in small] == [t.frame_index for t in large]
        assert [t.track_id for t in small] == [t.track_id for t in large]
        assert small.touches[0].distance_ph == pytest.approx(
            large.touches[0].distance_ph, rel=1e-6
        )


class TestGaps:
    def test_a_long_unseen_run_is_recorded_as_a_gap(self):
        frames = (
            straight_pass(n=10)
            + [((220.0 + i * 12, 400.0), False, [player(1, 600.0, 400.0)])
               for i in range(MAX_GAP_FRAMES + 5)]
            + straight_pass(n=10, x0=500.0)
        )
        gaps = find_gaps(build(frames))
        assert len(gaps) == 1

    def test_a_short_unseen_run_is_not_a_gap(self):
        frames = (
            straight_pass(n=10)
            + [((220.0 + i * 12, 400.0), False, [player(1, 600.0, 400.0)])
               for i in range(3)]
            + straight_pass(n=10, x0=500.0)
        )
        assert find_gaps(build(frames)) == []

    def test_missing_ball_frames_count_as_unseen(self):
        """A ball nobody found and a ball drawn in by interpolation are one fact."""
        frames = (
            straight_pass(n=5)
            + [(None, False, [player(1, 600.0, 400.0)]) for _ in range(MAX_GAP_FRAMES + 3)]
            + straight_pass(n=5, x0=500.0)
        )
        assert len(find_gaps(build(frames))) == 1

    def unseen_reversal(self, unseen=(19, 20, 21), n=40, turn=20):
        """A ball that reverses while the detector is not finding it."""
        frames = []
        for i in range(n):
            bx = 100.0 + i * 12.0 if i <= turn else 100.0 + (2 * turn - i) * 12.0
            frames.append(((bx, 400.0), i not in unseen, [player(7, 340.0, 400.0)]))
        return frames

    def test_a_touch_inside_a_short_gap_is_still_found(self):
        """Roughly 40% of frames have no real ball detection.

        Refusing to look inside interpolated spans would throw away a large
        share of the match, so the comparison across the span is used instead:
        the ball went in travelling one way and came out travelling another, so
        something touched it, even though no frame shows it.
        """
        touches = segment_touches(build(self.unseen_reversal()))
        assert len(touches) == 1
        assert touches.touches[0].track_id == 7

    def test_an_inferred_touch_earns_no_motion_or_observation_credit(self):
        """A straight line has zero curvature by construction.

        The motion test is blind inside a gap, so scoring it would read
        confidence off the interpolation rather than off the ball. The touch is
        still reported — it is just reported as the weak evidence it is.
        """
        touch = segment_touches(build(self.unseen_reversal())).touches[0]
        assert touch.observed is False
        assert touch.components.motion_change == 0.0
        assert touch.components.observation < 1.0
        assert touch.confidence < 0.8

    def test_a_ball_that_carries_straight_on_through_a_gap_is_not_a_touch(self):
        """Nothing changed across the span, so nothing happened in it."""
        frames = [
            ((100.0 + i * 12.0, 400.0), i not in (19, 20, 21),
             [player(7, 340.0, 400.0)])
            for i in range(40)
        ]
        assert len(segment_touches(build(frames))) == 0

    def test_nothing_is_inferred_inside_a_long_gap(self):
        """Over a long unseen span several touches may have happened.

        Picking one of them would be fiction rather than inference, so the span
        is recorded as a gap and the event layer is told not to join across it.
        """
        unseen = tuple(range(15, 15 + MAX_GAP_FRAMES + 4))
        table = build(self.unseen_reversal(unseen=unseen, n=60, turn=25))
        sequence = segment_touches(table)
        assert sequence.gaps
        start, end = sequence.gaps[0]
        assert not sequence.between(start, end)


class TestConfidence:
    def test_two_equidistant_players_destroy_the_separation_score(self):
        """In a crowd the nearest player is close to a coin flip.

        The scalar confidence has to fall when attribution is ambiguous,
        because "shot by the striker" and "shot by the defender marking him"
        are the same measurement otherwise.
        """
        def scenario(second_player_at):
            frames = []
            for i in range(40):
                bx = 100.0 + i * 12.0 if i <= 20 else 100.0 + (40 - i) * 12.0
                frames.append(((bx, 400.0), True, [
                    player(7, 340.0, 400.0),
                    player(8, second_player_at, 400.0),
                ]))
            return segment_touches(build(frames))

        alone = scenario(1100.0).touches[0]
        crowded = scenario(348.0).touches[0]

        assert crowded.components.separation < 0.2
        assert crowded.confidence < alone.confidence

    def test_the_weights_sum_to_one(self):
        assert sum(TouchConfidence.WEIGHTS) == pytest.approx(1.0)

    def test_a_perfect_touch_scores_one(self):
        perfect = TouchConfidence(1.0, 1.0, 1.0, 1.0)
        assert perfect.score == pytest.approx(1.0)

    def test_components_survive_onto_the_touch(self):
        """Kept in parts, not collapsed — this is what threshold tuning reads."""
        frames = []
        for i in range(40):
            bx = 100.0 + i * 12.0 if i <= 20 else 100.0 + (40 - i) * 12.0
            frames.append(((bx, 400.0), True, [player(7, 340.0, 400.0)]))
        touch = segment_touches(build(frames)).touches[0]
        assert 0.0 <= touch.components.proximity <= 1.0
        assert touch.components.observation == pytest.approx(1.0)
        assert touch.turn_deg > 0


class TestSuppression:
    def test_a_dribble_collapses_to_spaced_touches(self):
        """One player carrying the ball must not produce a touch every frame."""
        frames = []
        for i in range(60):
            bx = 300.0 + i * 3.0
            wobble = 6.0 if i % 4 else -6.0
            frames.append(((bx, 400.0 + wobble), True, [player(5, bx, 400.0)]))

        touches = segment_touches(build(frames))
        stamps = [t.timestamp_s for t in touches]
        assert all(b - a >= 0.25 - 1e-9 for a, b in zip(stamps, stamps[1:]))

    def test_two_players_touching_in_quick_succession_both_survive(self):
        """Suppression is per track, never global.

        A tackle is two players touching the ball a fifth of a second apart. A
        global window would delete one of them — and that is exactly the event
        the layer above most needs.
        """
        frames = []
        for i in range(48):
            if i <= 16:
                bx = 100.0 + i * 14.0
            elif i <= 22:
                bx = 100.0 + 16 * 14.0 - (i - 16) * 4.0
            else:
                bx = 100.0 + 16 * 14.0 - 6 * 4.0 + (i - 22) * 16.0
            frames.append(((bx, 400.0), True, [
                player(1, 330.0, 400.0),
                player(2, 306.0, 400.0),
            ]))

        touches = segment_touches(build(frames))
        assert len({t.track_id for t in touches}) == 2


class TestSequence:
    def test_gap_between_spots_an_unseen_span(self):
        """The guard that stops the event layer inventing a completed pass."""
        frames = (
            straight_pass(n=6)
            + [(None, False, [player(1, 600.0, 400.0)]) for _ in range(MAX_GAP_FRAMES + 3)]
            + straight_pass(n=6, x0=500.0)
        )
        table = build(frames)
        sequence = segment_touches(table)
        sequence.gaps = find_gaps(table)

        from cv.touches import Touch

        def fake(t):
            return Touch(0, t, 1, TEAM_A, (0.0, 0.0), None, 60.0, 0.0,
                         0.0, 0.0, 0.0, True, TouchConfidence(1, 1, 1, 1))

        assert sequence.gaps
        start, end = sequence.gaps[0]
        assert sequence.gap_between(fake(start - 0.1), fake(end + 0.1))
        assert not sequence.gap_between(fake(end + 0.1), fake(end + 0.2))

    def test_counts_by_track(self):
        frames = []
        for i in range(40):
            bx = 100.0 + i * 12.0 if i <= 20 else 100.0 + (40 - i) * 12.0
            frames.append(((bx, 400.0), True, [player(7, 340.0, 400.0)]))
        assert segment_touches(build(frames)).counts_by_track() == {7: 1}

    def test_an_empty_table_is_not_an_error(self):
        table = FrameTable(fps=FPS, frame_width=1280, frame_height=720)
        assert len(segment_touches(table)) == 0
