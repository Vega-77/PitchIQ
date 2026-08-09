"""Sample frames from a video at fixed time intervals."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    width: int
    height: int
    fps: float
    frame_count: int

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.fps if self.fps else 0.0


@dataclass(frozen=True)
class SampledFrame:
    index: int
    timestamp_s: float
    image: np.ndarray


def stride_for_fps(source_fps: float, target_fps: float | None) -> int:
    """How many frames to skip to analyse a clip at roughly `target_fps`.

    The lever used to be the stride itself, which is not a quantity — it is a
    ratio to a number nobody stated. Every measurement about it, and every rule
    that depends on it, is in hertz: *stride 2* is fifteen a second on a
    camcorder and thirty on a phone, and those are different analyses run by the
    same flag. This is the same mistake the smoothing window made when it was
    nine frames instead of 0.7 seconds, and it is worth more here, because a
    60fps export at stride 1 is doing twice the work of a 30fps one for figures
    that measure the same to within a percent.

    Never below 1: a target above the source cannot invent frames, and asking
    for one is not an error — it is a clip that already runs slower than the
    ceiling and should be left alone.
    """
    if not target_fps or target_fps <= 0 or source_fps <= 0:
        return 1
    return max(1, int(round(source_fps / target_fps)))


def effective_fps(source_fps: float, stride: int) -> float:
    """The rate a run actually analysed, which is what belongs in the report.

    Not the target that was asked for. A 30fps source at a 12fps target strides
    2 and analyses at 15, and a report that quoted 12 would be describing a run
    that did not happen.
    """
    return source_fps / max(1, stride) if source_fps > 0 else 0.0


def video_info(video_path: str | Path) -> VideoInfo:
    path = Path(video_path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise FileNotFoundError(f"Could not open video: {path}")
    try:
        return VideoInfo(
            path=path,
            width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=cap.get(cv2.CAP_PROP_FPS),
            frame_count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        )
    finally:
        cap.release()


def sample_frames(
    video_path: str | Path,
    interval_s: float = 1.0,
    max_frames: int | None = None,
    start_s: float = 0.0,
    end_s: float | None = None,
    crop: tuple[int, int, int, int] | None = None,
    upscale: float | None = None,
) -> Iterator[SampledFrame]:
    """Yield frames every `interval_s` seconds, optionally cropped and upscaled.

    `crop` is (x1, y1, x2, y2) in source pixels, applied before `upscale`.
    """
    path = Path(video_path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise FileNotFoundError(f"Could not open video: {path}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0:
            raise ValueError(f"Video reports invalid fps ({fps}): {path}")
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_s = total / fps

        stop_s = duration_s if end_s is None else min(end_s, duration_s)
        timestamp = start_s
        emitted = 0

        while timestamp < stop_s:
            if max_frames is not None and emitted >= max_frames:
                break

            frame_idx = int(round(timestamp * fps))
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok:
                break

            if crop is not None:
                x1, y1, x2, y2 = crop
                frame = frame[y1:y2, x1:x2]
                if frame.size == 0:
                    raise ValueError(f"Crop {crop} produced an empty region")

            if upscale is not None and upscale != 1.0:
                frame = cv2.resize(
                    frame, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC
                )

            yield SampledFrame(index=frame_idx, timestamp_s=timestamp, image=frame)
            emitted += 1
            timestamp += interval_s
    finally:
        cap.release()
