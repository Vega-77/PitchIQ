"""The verdict has to separate a bad camera from a bad setting.

Every case below is one of the two, because getting them the wrong way round is
the expensive failure: told "the camera", somebody re-rigs a tripod for a week
to fix a flag; told "the setting", somebody re-runs the pipeline four times
against footage that never held the pixels.

The wide-clip case is the one real measurement the repo has, reconstructed from
`ROADMAP.md` — a player 4-8 px wide is roughly 18 px tall, and that clip
detected nothing at all.
"""

import unittest

from cv.detector import Detection
from cv.framing import (
    BALL_TO_PLAYER,
    GOOD,
    LIMIT_FRAMING,
    LIMIT_NONE,
    LIMIT_RESOLUTION,
    LIMIT_UNKNOWN,
    MARGINAL,
    PLAYER_GOOD_PX,
    UNKNOWN,
    UNUSABLE,
    assess_framing,
)


def person(height_px, x=0.0, y=0.0):
    return Detection(
        label='person',
        confidence=0.6,
        xyxy=(x, y, x + height_px / 3.0, y + height_px),
    )


def ball(x=0.0, y=0.0):
    return Detection(label='ball', confidence=0.3, xyxy=(x, y, x + 4, y + 4))


def frames(heights, count=10, with_ball=0):
    """`count` frames each holding players of the given heights."""
    out = []
    for index in range(count):
        detections = [person(h, x=i * 200.0) for i, h in enumerate(heights)]
        if index < with_ball:
            detections.append(ball(x=50.0))
        out.append(detections)
    return out


class TestTheCameraWasTheProblem(unittest.TestCase):
    def test_the_wide_clip_blames_the_camera(self):
        # 18 px tall in a 720p file: the pixels were never recorded, so the
        # verdict must not point at anything we could change here.
        verdict = assess_framing(frames([18.0] * 6), 1280, 720, imgsz=960)

        self.assertEqual(verdict.status, UNUSABLE)
        self.assertEqual(verdict.limit, LIMIT_FRAMING)
        self.assertAlmostEqual(verdict.player_px, 18.0)
        self.assertAlmostEqual(verdict.inference_px, 13.5)

    def test_no_tiling_rescues_a_camera_problem(self):
        verdict = assess_framing(frames([18.0] * 6), 1280, 720, imgsz=960)
        self.assertIsNone(verdict.tiles_needed())

    def test_the_advice_names_the_camera_and_not_a_flag(self):
        verdict = assess_framing(frames([18.0] * 6), 1280, 720, imgsz=960)
        text = ' '.join(verdict.lines())

        self.assertIn('camera', text)
        self.assertNotIn('--tiles', text)
        self.assertNotIn('--imgsz', text)


class TestWeThrewThePixelsAway(unittest.TestCase):
    """A wide native export: plenty of detail in the file, none of it shown."""

    def verdict(self):
        return assess_framing(frames([60.0] * 8), 3840, 1080, imgsz=1280)

    def test_the_file_is_fine_and_the_run_was_not(self):
        verdict = self.verdict()

        self.assertAlmostEqual(verdict.player_px, 60.0)
        self.assertAlmostEqual(verdict.inference_px, 20.0)
        self.assertEqual(verdict.status, MARGINAL)
        self.assertEqual(verdict.limit, LIMIT_RESOLUTION)

    def test_it_says_how_many_tiles(self):
        # 28 px wanted, 60 px held, a third of the frame shown: two tiles.
        self.assertEqual(self.verdict().tiles_needed(), 2)

    def test_the_advice_names_the_flag_and_not_the_camera(self):
        text = ' '.join(self.verdict().lines())

        self.assertIn('--tiles 2', text)
        self.assertIn('settings', text)

    def test_the_tiling_it_recommends_actually_clears_the_bar(self):
        verdict = self.verdict()
        tiles = verdict.tiles_needed()

        # Each tile's long edge is 3840/tiles, letterboxed up to imgsz.
        tile_long_edge = 3840 / tiles
        scale = min(1.0, verdict.imgsz / tile_long_edge)
        self.assertGreaterEqual(verdict.player_px * scale, PLAYER_GOOD_PX)


class TestGoodFraming(unittest.TestCase):
    def test_a_tight_clip_at_full_size_passes(self):
        verdict = assess_framing(frames([90.0] * 9), 1280, 720, imgsz=1280)

        self.assertEqual(verdict.status, GOOD)
        self.assertEqual(verdict.limit, LIMIT_NONE)
        self.assertIn('usable', ' '.join(verdict.lines()))

    def test_players_can_be_fine_while_the_ball_is_not(self):
        # The 43%-ball clip in one assertion: people comfortably above their
        # floor at 60 px, the ball they imply 7.6 px and under its own, and the
        # verdict has to say so rather than read good and stop there.
        verdict = assess_framing(frames([60.0] * 9), 1280, 720, imgsz=1280)

        self.assertEqual(verdict.status, GOOD)
        self.assertFalse(verdict.ball_reachable)
        self.assertIn('under its floor', ' '.join(verdict.lines()))

    def test_a_close_camera_reaches_the_ball(self):
        verdict = assess_framing(frames([700.0] * 4), 1280, 720, imgsz=1280)
        self.assertTrue(verdict.ball_reachable)


class TestMeasurement(unittest.TestCase):
    def test_the_median_ignores_the_coach_stood_next_to_the_camera(self):
        # One person three metres away and five on the far touchline. The mean
        # describes nobody; the median describes the players.
        verdict = assess_framing(
            frames([20.0, 20.0, 20.0, 20.0, 20.0, 400.0]), 1280, 720, imgsz=1280
        )
        self.assertAlmostEqual(verdict.player_px, 20.0)

    def test_zero_height_boxes_are_not_players(self):
        rubbish = [[Detection(label='person', confidence=0.9, xyxy=(5, 5, 9, 5))]]
        verdict = assess_framing(rubbish, 1280, 720, imgsz=1280)

        self.assertIsNone(verdict.player_px)
        self.assertEqual(verdict.status, UNKNOWN)

    def test_nothing_detected_is_unknown_not_unusable(self):
        verdict = assess_framing([[], [], []], 1280, 720, imgsz=1280)

        self.assertEqual(verdict.status, UNKNOWN)
        self.assertEqual(verdict.limit, LIMIT_UNKNOWN)
        self.assertIsNone(verdict.inference_px)
        self.assertIsNone(verdict.predicted_ball_px)
        self.assertIn('nothing to measure', ' '.join(verdict.lines()))

    def test_an_empty_run_does_not_divide_by_zero(self):
        verdict = assess_framing([], 1280, 720, imgsz=1280)

        self.assertEqual(verdict.frames, 0)
        self.assertEqual(verdict.people_per_frame, 0.0)
        self.assertEqual(verdict.ball_share, 0.0)

    def test_ball_share_counts_frames_and_not_detections(self):
        verdict = assess_framing(
            frames([90.0] * 4, count=10, with_ball=4), 1280, 720, imgsz=1280
        )
        self.assertAlmostEqual(verdict.ball_share, 0.4)
        self.assertAlmostEqual(verdict.people_per_frame, 4.0)

    def test_the_ball_prediction_follows_the_player(self):
        verdict = assess_framing(frames([90.0] * 4), 1280, 720, imgsz=1280)
        self.assertAlmostEqual(
            verdict.predicted_ball_px, 90.0 * BALL_TO_PLAYER, places=5
        )


class TestJson(unittest.TestCase):
    def test_every_field_survives_serialisation(self):
        verdict = assess_framing(frames([60.0] * 8), 3840, 1080, imgsz=1280)
        payload = verdict.to_json()

        self.assertEqual(payload['status'], MARGINAL)
        self.assertEqual(payload['limit'], LIMIT_RESOLUTION)
        self.assertEqual(payload['tiles_needed'], 2)
        self.assertAlmostEqual(payload['inference_px'], 20.0)
        self.assertEqual(payload['width'], 3840)

    def test_absent_stays_absent_rather_than_becoming_zero(self):
        payload = assess_framing([[], []], 1280, 720, imgsz=1280).to_json()

        self.assertIsNone(payload['player_px'])
        self.assertIsNone(payload['inference_px'])
        self.assertIsNone(payload['predicted_ball_px'])
        self.assertIsNone(payload['tiles_needed'])


if __name__ == '__main__':
    unittest.main()
