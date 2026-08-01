"""YOLO detection restricted to the two classes PitchIQ cares about."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from ultralytics import YOLO

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
