"""YOLO detection restricted to the two classes PitchIQ cares about.

`ultralytics` is imported inside the constructor rather than at module scope,
which is the same arrangement `cv/xg_bridge.py` uses for onnxruntime and for the
same reason. It drags in torch — two gigabytes of it — and almost nothing in
this package needs a detector: the pipeline takes one by injection, every test
supplies a fake, and `import cv.pipeline` on a machine that only wanted to read
a report JSON should not require a GPU stack. The cost of getting this wrong is
paid at import time by everyone, and the cost of getting it right is one import
statement in the one place that actually builds a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

# COCO class indices in the pretrained YOLO weights.
CLASS_PERSON = 0
CLASS_BALL = 32

LABELS = {CLASS_PERSON: "person", CLASS_BALL: "ball"}

DEFAULT_WEIGHTS = Path(__file__).parent / "weights" / "yolov8n.pt"


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    xyxy: tuple[float, float, float, float]

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.xyxy
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    @property
    def ground_point(self) -> tuple[float, float]:
        """Bottom-centre of the box — where the object meets the pitch."""
        x1, _, x2, y2 = self.xyxy
        return ((x1 + x2) / 2, y2)


class PersonBallDetector:
    """Thin wrapper over ultralytics YOLO, filtered to people and the ball.

    Filtering at predict time is what keeps the spurious 'car'/'dog'/'umbrella'
    classes out of the results on wide stadium shots.
    """

    def __init__(
        self,
        weights: str | Path = DEFAULT_WEIGHTS,
        conf: float = 0.25,
        imgsz: int = 960,
        device: str | int | None = None,
    ) -> None:
        from ultralytics import YOLO

        self.model = YOLO(str(weights))
        self.conf = conf
        self.imgsz = imgsz
        self.device = device

    def detect(self, image: np.ndarray) -> list[Detection]:
        return self.detect_batch([image])[0]

    def detect_batch(self, images: list[np.ndarray]) -> list[list[Detection]]:
        batch: list[list[Detection]] = []
        for boxes in self.detect_batch_raw(images):
            batch.append([
                Detection(
                    label=LABELS[int(cls)],
                    confidence=float(score),
                    xyxy=tuple(float(v) for v in box),
                )
                for cls, score, box in zip(boxes.cls, boxes.conf, boxes.xyxy)
            ])
        return batch

    def detect_batch_raw(self, images: list[np.ndarray]) -> list:
        """Per-image ultralytics `Boxes`, moved to CPU as numpy.

        Exists because the trackers in ultralytics.trackers take exactly this
        object — it is what `on_predict_postprocess_end` hands them, and it
        carries the `.xywh` / `.conf` / `.cls` attributes plus boolean indexing
        that `parse_bboxes` and `_split_detections` rely on. Duck-typing a
        replacement would work right up until it quietly didn't.

        `.cpu().numpy()` here rather than later: holding a batch of GPU tensors
        alive across the frame loop is how a bounded-memory decode turns back
        into an unbounded one.
        """
        results = self.model.predict(
            images,
            classes=[CLASS_PERSON, CLASS_BALL],
            conf=self.conf,
            imgsz=self.imgsz,
            device=self.device,
            verbose=False,
        )
        return [result.boxes.cpu().numpy() for result in results]


def tile_windows(
    width: int,
    height: int,
    tiles: int,
    overlap: float = 0.2,
) -> list[tuple[int, int, int, int]]:
    """Split a frame into overlapping windows, `tiles` of them along its long edge.

    A football frame is much wider than it is tall, so the long edge is the one
    worth cutting and the short edge is only cut when a tile would otherwise
    still be taller than it is wide. Three tiles across a 3840x1080 export gives
    1280x1080 windows; three across a 3840x2160 gives six 1280x1080 windows,
    because leaving those 2160 px tall would put the long edge straight back
    where it started and buy nothing.

    The windows overlap because a tile boundary falls across a player about as
    often as it falls anywhere else, and half a player detects poorly and
    localises worse. `overlap` is a fraction of the tile, split between the two
    sides, so 0.2 pads each edge by a tenth of a tile — comfortably more than a
    player is wide at any framing worth running.

    Returned in reading order, clamped to the frame, and always at least one
    window, so a caller never has to special-case `tiles=1`.
    """
    tiles = max(1, int(tiles))
    if tiles == 1:
        return [(0, 0, int(width), int(height))]

    if width >= height:
        cols = tiles
        rows = max(1, int(-(-float(height) // (float(width) / tiles))))
    else:
        rows = tiles
        cols = max(1, int(-(-float(width) // (float(height) / tiles))))

    tile_w = float(width) / cols
    tile_h = float(height) / rows
    pad_x = tile_w * overlap / 2.0
    pad_y = tile_h * overlap / 2.0

    windows = []
    for row in range(rows):
        for col in range(cols):
            x1 = max(0, int(round(col * tile_w - pad_x)))
            y1 = max(0, int(round(row * tile_h - pad_y)))
            x2 = min(int(width), int(round((col + 1) * tile_w + pad_x)))
            y2 = min(int(height), int(round((row + 1) * tile_h + pad_y)))
            if x2 > x1 and y2 > y1:
                windows.append((x1, y1, x2, y2))
    return windows


def _overlap_scores(a: Detection, b: Detection) -> tuple[float, float]:
    """(IoU, containment) for two boxes, where containment divides by the smaller."""
    ax1, ay1, ax2, ay2 = a.xyxy
    bx1, by1, bx2, by2 = b.xyxy

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = ix2 - ix1, iy2 - iy1
    if iw <= 0 or ih <= 0:
        return 0.0, 0.0

    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    smaller = min(area_a, area_b)

    iou = inter / union if union > 0 else 0.0
    containment = inter / smaller if smaller > 0 else 0.0
    return iou, containment


def merge_detections(
    detections: list[Detection],
    iou_threshold: float = 0.45,
    containment_threshold: float = 0.75,
) -> list[Detection]:
    """Drop duplicates from overlapping tiles, keeping the most confident.

    Ordinary IoU suppression is not enough here, and the reason is specific to
    tiling. A player straddling a boundary is seen whole by one tile and clipped
    to their legs by the other. Those two boxes can share less than half their
    union while describing one person, so IoU keeps both and the frame ends up
    with a phantom player standing in their own shins.

    So a box is also suppressed when most of *it* lies inside a better one —
    intersection over the smaller area rather than over the union. That is the
    test that catches the clipped copy, because the clipped copy is almost
    entirely inside the whole one.

    Compared per label, never across: a ball resting at a player's feet is
    genuinely inside that player's box and must survive it.
    """
    by_label: dict[str, list[Detection]] = {}
    for det in detections:
        by_label.setdefault(det.label, []).append(det)

    kept: list[Detection] = []
    for group in by_label.values():
        group.sort(key=lambda d: d.confidence, reverse=True)
        chosen: list[Detection] = []
        for det in group:
            duplicate = False
            for winner in chosen:
                iou, containment = _overlap_scores(det, winner)
                if iou >= iou_threshold or containment >= containment_threshold:
                    duplicate = True
                    break
            if not duplicate:
                chosen.append(det)
        kept.extend(chosen)

    kept.sort(key=lambda d: d.confidence, reverse=True)
    return kept


class TiledDetector:
    """Detect on native-resolution crops instead of one shrunken whole frame.

    Ultralytics letterboxes each frame so its long edge becomes `imgsz`. On a
    3840-wide export at `imgsz=1280` that is a third of the width thrown away
    before the model looks at anything, and it is thrown away hardest from the
    smallest objects — which here are the entire subject matter. Cutting the
    frame into three and detecting on each at full size hands those pixels back.

    It is composition rather than a flag on `PersonBallDetector` deliberately.
    `detect_batch_raw` returns real ultralytics `Boxes` because the trackers
    take exactly that object, and there is no honest way to stitch four tiles'
    worth of `Boxes` back into one without fabricating the type. So this wraps
    the detector and offers the `Detection` path only; the tracking pass keeps
    the plain detector and its single-shot frame. When tiling turns out to be
    what the footage needs, the tracker is the next thing to work out, and it
    will be a real piece of work rather than a flag.

    The cost is honest and linear: `len(tile_windows(...))` inferences per
    frame instead of one. Tiles across every image in the batch are flattened
    into one call, so batching still happens; it is the same GPU doing more
    work, not the same work done worse.
    """

    def __init__(self, detector, tiles: int = 2, overlap: float = 0.2) -> None:
        self.detector = detector
        self.tiles = max(1, int(tiles))
        self.overlap = overlap

    def detect(self, image: np.ndarray) -> list[Detection]:
        return self.detect_batch([image])[0]

    def detect_batch(self, images: list[np.ndarray]) -> list[list[Detection]]:
        crops: list[np.ndarray] = []
        offsets: list[tuple[int, int]] = []
        counts: list[int] = []

        for image in images:
            height, width = image.shape[:2]
            windows = tile_windows(width, height, self.tiles, self.overlap)
            counts.append(len(windows))
            for x1, y1, x2, y2 in windows:
                crops.append(image[y1:y2, x1:x2])
                offsets.append((x1, y1))

        flat = self.detector.detect_batch(crops) if crops else []

        merged: list[list[Detection]] = []
        cursor = 0
        for count in counts:
            gathered: list[Detection] = []
            for index in range(cursor, cursor + count):
                dx, dy = offsets[index]
                for det in flat[index]:
                    x1, y1, x2, y2 = det.xyxy
                    gathered.append(
                        Detection(
                            label=det.label,
                            confidence=det.confidence,
                            xyxy=(x1 + dx, y1 + dy, x2 + dx, y2 + dy),
                        )
                    )
            merged.append(merge_detections(gathered))
            cursor += count

        return merged
