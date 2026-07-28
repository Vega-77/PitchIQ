"""Save a single frame from a video, for calibrating against.

    python -m cv.experiments.grab_frame "match.mp4" --at 120
    python -m cv.experiments.grab_frame "match.mp4" --at 120 --out frame.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2

from cv.frame_sampler import sample_frames, video_info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grab_frame", description="Save one frame from a video."
    )
    parser.add_argument("video", type=Path)
    parser.add_argument("--at", type=float, default=60.0, help="timestamp in seconds")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.video.exists():
        print(f"error: video not found: {args.video}")
        return 1

    info = video_info(args.video)
    if args.at >= info.duration_s:
        print(f"error: --at {args.at}s is past the end ({info.duration_s:.0f}s)")
        return 1

    frame = next(
        iter(sample_frames(args.video, start_s=args.at, max_frames=1)), None
    )
    if frame is None:
        print("error: could not read a frame there")
        return 1

    out = args.out or Path(f"{args.video.stem}_{int(args.at)}s.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame.image)

    print(f"{info.width}x{info.height} @ {args.at:.0f}s -> {out}")
    print("\nNext: open calibrate/index.html and load this frame.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
