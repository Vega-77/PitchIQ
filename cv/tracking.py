"""Follow the same player across frames.

Detection alone gives a bag of boxes per frame with no memory. Tracking adds
the one thing every per-player statistic depends on: that box 3 in this frame
is the same person as box 7 in the last one.

Uses Ultralytics' built-in ByteTrack rather than a hand-rolled tracker — the
roadmap is explicit about not building what already exists, and a custom
tracker is a large amount of work to arrive somewhere worse.

Track IDs are internally consistent only. Nothing here knows a player's name;
that mapping is a human job in the review tool, narrowed down by the
substitution log.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .detector import CLASS_BALL, CLASS_PERSON, DEFAULT_WEIGHTS, LABELS


@dataclass
class TrackedBox:
    """One object in one frame, with an identity that persists across frames."""

    track_id: int
    label: str
    confidence: float
    xyxy: tuple[float, float, float, float]
    frame_index: int
    timestamp_s: float

    @property
    def ground_point(self) -> tuple[float, float]:
        x1, _, x2, y2 = self.xyxy
        return ((x1 + x2) / 2, y2)


@dataclass
class Track:
    """One object's whole life, in order."""

    track_id: int
    label: str
    boxes: list[TrackedBox] = field(default_factory=list)

    @property
    def first_seen_s(self) -> float:
        return self.boxes[0].timestamp_s if self.boxes else 0.0

    @property
    def last_seen_s(self) -> float:
        return self.boxes[-1].timestamp_s if self.boxes else 0.0

    @property
    def duration_s(self) -> float:
        return self.last_seen_s - self.first_seen_s

    def __len__(self) -> int:
        return len(self.boxes)


class PlayerTracker:
    """Detect and track people and the ball through a video."""

    def __init__(
        self,
        weights: str | Path = DEFAULT_WEIGHTS,
        conf: float = 0.25,
        imgsz: int = 960,
        device: str | int | None = None,
        # BoT-SORT adds an appearance model on top of motion, which measurably
        # holds identity longer than plain ByteTrack on this footage (40% vs
        # 32% window coverage) at roughly twice the compute. See
        # cv/experiments/track_report.py for the comparison.
        tracker: str = "botsort.yaml",
    ) -> None:
        from ultralytics import YOLO

        self.model = YOLO(str(weights))
        self.conf = conf
        self.imgsz = imgsz
        self.device = device
        self.tracker = tracker

    def run(
        self,
        video_path: str | Path,
        start_s: float = 0.0,
        end_s: float | None = None,
        stride: int = 1,
    ) -> dict[int, Track]:
        """Track through a video and return every track by id.

        `stride` skips frames to trade accuracy for speed. Be careful raising
        it: ByteTrack matches on motion between consecutive frames, so a large
        stride makes players appear to teleport and identity switches climb.
        """
        import cv2

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise FileNotFoundError(f"Could not open video: {video_path}")

        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            stop_frame = int(end_s * fps) if end_s else total
            start_frame = int(start_s * fps)

            if start_frame:
                cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

            tracks: dict[int, Track] = {}
            frame_index = start_frame

            while frame_index < stop_frame:
                ok, frame = cap.read()
                if not ok:
                    break

                if (frame_index - start_frame) % stride == 0:
                    self._ingest(
                        frame, frame_index, frame_index / fps, tracks
                    )

                frame_index += 1

            return tracks
        finally:
            cap.release()

    def _ingest(self, frame, frame_index: int, timestamp_s: float, tracks: dict) -> None:
        results = self.model.track(
            frame,
            classes=[CLASS_PERSON, CLASS_BALL],
            conf=self.conf,
            imgsz=self.imgsz,
            device=self.device,
            tracker=self.tracker,
            persist=True,
            verbose=False,
        )

        for result in results:
            boxes = result.boxes
            if boxes is None or boxes.id is None:
                continue  # nothing tracked in this frame

            for tid, cls, score, xyxy in zip(
                boxes.id, boxes.cls, boxes.conf, boxes.xyxy
            ):
                track_id = int(tid)
                label = LABELS[int(cls)]

                box = TrackedBox(
                    track_id=track_id,
                    label=label,
                    confidence=float(score),
                    xyxy=tuple(float(v) for v in xyxy),
                    frame_index=frame_index,
                    timestamp_s=timestamp_s,
                )

                if track_id not in tracks:
                    tracks[track_id] = Track(track_id, label)
                tracks[track_id].boxes.append(box)


def drop_short_tracks(tracks: dict[int, Track], min_frames: int = 5) -> dict[int, Track]:
    """Remove blips.

    A track that exists for two frames is almost always a false positive or a
    fragment, and it pollutes per-player stats with impossible speeds.
    """
    return {tid: t for tid, t in tracks.items() if len(t) >= min_frames}


def split_by_label(tracks: dict[int, Track]) -> tuple[dict[int, Track], dict[int, Track]]:
    """Separate people from the ball."""
    people = {tid: t for tid, t in tracks.items() if t.label == "person"}
    balls = {tid: t for tid, t in tracks.items() if t.label == "ball"}
    return people, balls
