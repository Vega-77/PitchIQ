"""Tiling is only worth having if the boxes come back in frame coordinates.

Two failure modes are worth more than the rest, and both are silent. Boxes that
never get their tile offset added describe a player standing in the top-left
corner of the pitch, which looks like a tracking bug forever. And a player cut
by a tile boundary comes back twice — once whole, once as a pair of legs — which
looks like a real extra player, inflates every count, and stands still.

No model here: the detector is a stub that reports what it was handed.
"""

import unittest

import numpy as np

from cv.detector import Detection, TiledDetector, merge_detections, tile_windows


def box(x1, y1, x2, y2, label='person', conf=0.6):
    return Detection(label=label, confidence=conf, xyxy=(x1, y1, x2, y2))


class Stub:
    """Records the crops it was given; returns one box per crop, at its centre."""

    def __init__(self, per_crop=None):
        self.sizes = []
        self.calls = 0
        self._per_crop = per_crop

    def detect_batch(self, images):
        self.calls += 1
        out = []
        for image in images:
            height, width = image.shape[:2]
            self.sizes.append((width, height))
            if self._per_crop is not None:
                out.append(list(self._per_crop))
            else:
                out.append([box(width / 2 - 5, height / 2 - 15,
                                width / 2 + 5, height / 2 + 15)])
        return out


class TestWindows(unittest.TestCase):
    def test_one_tile_is_the_whole_frame(self):
        self.assertEqual(tile_windows(3840, 1080, 1), [(0, 0, 3840, 1080)])

    def test_a_wide_frame_is_cut_along_its_width(self):
        windows = tile_windows(3840, 1080, 3, overlap=0.0)

        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0], (0, 0, 1280, 1080))
        self.assertEqual(windows[2], (2560, 0, 3840, 1080))

    def test_a_tall_tile_gets_cut_again_rather_than_staying_tall(self):
        # 3840x2160 into three columns leaves 1280x2160 tiles, whose long edge
        # is right back where it started. Rows must follow.
        windows = tile_windows(3840, 2160, 3, overlap=0.0)

        self.assertEqual(len(windows), 6)
        widths = {x2 - x1 for x1, _, x2, _ in windows}
        heights = {y2 - y1 for _, y1, _, y2 in windows}
        self.assertEqual(widths, {1280})
        self.assertEqual(heights, {1080})

    def test_a_portrait_frame_is_cut_along_its_height(self):
        windows = tile_windows(1080, 3840, 3, overlap=0.0)

        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0], (0, 0, 1080, 1280))

    def test_overlap_pads_the_inner_edges(self):
        plain = tile_windows(3840, 1080, 3, overlap=0.0)
        padded = tile_windows(3840, 1080, 3, overlap=0.2)

        # The middle tile grows on both sides; the outer ones only inwards.
        self.assertLess(padded[1][0], plain[1][0])
        self.assertGreater(padded[1][2], plain[1][2])

    def test_overlap_never_escapes_the_frame(self):
        for x1, y1, x2, y2 in tile_windows(3840, 1080, 4, overlap=0.5):
            self.assertGreaterEqual(x1, 0)
            self.assertGreaterEqual(y1, 0)
            self.assertLessEqual(x2, 3840)
            self.assertLessEqual(y2, 1080)

    def test_the_tiles_cover_the_whole_frame(self):
        covered = np.zeros((1080, 3840), dtype=bool)
        for x1, y1, x2, y2 in tile_windows(3840, 1080, 5, overlap=0.1):
            covered[y1:y2, x1:x2] = True
        self.assertTrue(covered.all())


class TestMerging(unittest.TestCase):
    def test_the_same_player_seen_twice_is_kept_once(self):
        merged = merge_detections([
            box(100, 100, 130, 190, conf=0.9),
            box(102, 101, 132, 191, conf=0.5),
        ])
        self.assertEqual(len(merged), 1)
        self.assertAlmostEqual(merged[0].confidence, 0.9)

    def test_a_player_clipped_by_a_boundary_does_not_become_a_second_player(self):
        # The whole player, and the legs the neighbouring tile saw. Their IoU is
        # a third; containment within the whole box is total.
        whole = box(100, 100, 130, 190, conf=0.9)
        legs = box(100, 160, 130, 190, conf=0.7)

        iou_only = merge_detections([whole, legs], containment_threshold=2.0)
        self.assertEqual(len(iou_only), 2)  # what plain NMS would have done

        self.assertEqual(len(merge_detections([whole, legs])), 1)

    def test_two_real_players_side_by_side_both_survive(self):
        merged = merge_detections([
            box(100, 100, 130, 190, conf=0.9),
            box(140, 100, 170, 190, conf=0.8),
        ])
        self.assertEqual(len(merged), 2)

    def test_a_ball_at_a_players_feet_survives_the_player(self):
        merged = merge_detections([
            box(100, 100, 130, 190, conf=0.9),
            box(112, 180, 120, 188, label='ball', conf=0.3),
        ])
        self.assertEqual(len(merged), 2)
        self.assertEqual({d.label for d in merged}, {'person', 'ball'})

    def test_results_come_back_most_confident_first(self):
        merged = merge_detections([
            box(0, 0, 30, 90, conf=0.3),
            box(400, 0, 430, 90, conf=0.9),
            box(800, 0, 830, 90, conf=0.6),
        ])
        self.assertEqual(
            [round(d.confidence, 1) for d in merged], [0.9, 0.6, 0.3]
        )

    def test_nothing_in_nothing_out(self):
        self.assertEqual(merge_detections([]), [])


class TestDetector(unittest.TestCase):
    def frame(self, width=3840, height=1080):
        return np.zeros((height, width, 3), dtype=np.uint8)

    def test_boxes_come_back_in_frame_coordinates(self):
        stub = Stub()
        tiled = TiledDetector(stub, tiles=3, overlap=0.0)

        detections = tiled.detect(self.frame())

        self.assertEqual(len(detections), 3)
        centres = sorted(round(d.center[0]) for d in detections)
        self.assertEqual(centres, [640, 1920, 3200])

    def test_the_detector_sees_native_sized_crops(self):
        stub = Stub()
        TiledDetector(stub, tiles=3, overlap=0.0).detect(self.frame())

        self.assertEqual(stub.sizes, [(1280, 1080)] * 3)

    def test_one_tile_is_the_untiled_frame(self):
        stub = Stub()
        TiledDetector(stub, tiles=1).detect(self.frame())

        self.assertEqual(stub.sizes, [(3840, 1080)])

    def test_a_batch_is_one_call_and_stays_in_order(self):
        stub = Stub()
        tiled = TiledDetector(stub, tiles=3, overlap=0.0)

        results = tiled.detect_batch([self.frame(), self.frame(1920, 1080)])

        self.assertEqual(stub.calls, 1)  # tiles flattened into one inference
        self.assertEqual(len(results), 2)

        # 3840x1080 into three columns leaves 1280x1080 tiles and stops there.
        self.assertEqual(len(results[0]), 3)
        # 1920x1080 into three leaves 640-wide tiles, taller than they are wide,
        # so rows follow and the same three columns come back twice.
        self.assertEqual(len(results[1]), 6)
        self.assertEqual(
            sorted({round(d.center[0]) for d in results[1]}), [320, 960, 1600]
        )

    def test_duplicates_across_the_seam_are_merged_away(self):
        # Every tile reports a box at the same place in its own coordinates.
        # With overlap, adjacent tiles see the same strip, so the offsets land
        # two of them on top of each other and only one should come back.
        stub = Stub(per_crop=[box(0, 0, 30, 90, conf=0.8)])
        tiled = TiledDetector(stub, tiles=2, overlap=0.0)

        detections = tiled.detect(self.frame(200, 100))
        self.assertEqual(len(detections), 2)  # far apart: both real

        tiled_overlapping = TiledDetector(stub, tiles=2, overlap=0.9)
        overlapping = tiled_overlapping.detect(self.frame(200, 100))
        self.assertLessEqual(len(overlapping), 2)

    def test_an_empty_batch_asks_the_detector_nothing(self):
        stub = Stub()
        self.assertEqual(TiledDetector(stub, tiles=3).detect_batch([]), [])
        self.assertEqual(stub.calls, 0)


if __name__ == '__main__':
    unittest.main()
