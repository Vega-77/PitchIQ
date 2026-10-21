"""Detection feasibility spike: can we see players and the ball in this footage?

Answers the question that gates the rest of the CV pipeline. Run it against any
new footage before assuming the camera setup is usable.

    python -m pitchiq.experiments.spike_detect "path/to/match.mp4"
    python -m pitchiq.experiments.spike_detect match.mp4 --crop 0,260,1280,430 --upscale 3

It reports hit rates *and* why they are what they are. A hit rate on its own
cannot tell a camera that was too far away from a frame we shrank before
looking at it, and those need opposite responses: one is next week's fixture,
the other is one flag on this command. The framing block at the end says which,
and `--tiles` is the fix when the answer is us.
"""

from __future__ import annotations

import argparse
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2

from pitchiq.detector import PersonBallDetector, TiledDetector, tile_windows
from pitchiq.video_io import sample_frames, video_info
from pitchiq.detection_diagnosis import assess_framing

OUTPUT_ROOT = Path('scratch_frames')


def parse_crop(value: str) -> tuple[int, int, int, int]:
    parts = value.split(',')
    if len(parts) != 4:
        raise argparse.ArgumentTypeError('--crop expects x1,y1,x2,y2')
    try:
        x1, y1, x2, y2 = (int(p) for p in parts)
    except ValueError:
        raise argparse.ArgumentTypeError('--crop values must be integers') from None
    if x2 <= x1 or y2 <= y1:
        raise argparse.ArgumentTypeError('--crop needs x2 > x1 and y2 > y1')
    return x1, y1, x2, y2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m pitchiq.experiments.spike_detect',
        description='Report how reliably players and the ball are detectable in a video.',
    )
    parser.add_argument('video', type=Path, help='path to the video file')
    parser.add_argument(
        '--interval', type=float, default=1.0, help='seconds between sampled frames'
    )
    parser.add_argument('--conf', type=float, default=0.25, help='detection confidence threshold')
    parser.add_argument('--imgsz', type=int, default=960, help='inference image size')
    parser.add_argument('--max-frames', type=int, default=None, help='stop after this many frames')
    parser.add_argument('--start', type=float, default=0.0, help='start time in seconds')
    parser.add_argument('--end', type=float, default=None, help='end time in seconds')
    parser.add_argument(
        '--crop', type=parse_crop, default=None, help='x1,y1,x2,y2 applied before detection'
    )
    parser.add_argument(
        '--upscale', type=float, default=None, help='scale factor applied after cropping'
    )
    parser.add_argument(
        '--device', default=None, help='torch device, e.g. 0 or cpu (default: auto)'
    )
    parser.add_argument('--batch', type=int, default=16, help='frames per inference batch')
    parser.add_argument(
        '--tiles',
        type=int,
        default=1,
        help='detect on this many crops along the long edge instead of one shrunken frame',
    )
    parser.add_argument(
        '--save-annotated', action='store_true', help='write annotated frames to scratch_frames/'
    )
    parser.add_argument(
        '--timeline', action='store_true', help='print a per-frame hit/miss timeline'
    )
    return parser


def annotate(image, detections):
    canvas = image.copy()
    for det in detections:
        x1, y1, x2, y2 = (int(v) for v in det.xyxy)
        color = (0, 200, 255) if det.label == 'ball' else (80, 200, 80)
        thickness = 2 if det.label == 'ball' else 1
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness)
        cv2.putText(
            canvas,
            f'{det.label} {det.confidence:.2f}',
            (x1, max(0, y1 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            color,
            1,
            cv2.LINE_AA,
        )
    return canvas


def summarize(label: str, frames_with_hit: int, total_frames: int, confidences: list[float]) -> str:
    rate = frames_with_hit / total_frames * 100 if total_frames else 0.0
    if confidences:
        detail = (
            f'avg conf {statistics.mean(confidences):.2f}   '
            f'max {max(confidences):.2f}   {len(confidences)} detections'
        )
    else:
        detail = 'no detections'
    return f'  {label:<8} {frames_with_hit:>4}/{total_frames} frames ({rate:5.1f}%)   {detail}'


def longest_gap(hits: list[tuple[float, bool]]) -> tuple[float, float] | None:
    """Longest run of consecutive misses, returned as (start_s, end_s)."""
    best: tuple[float, float] | None = None
    best_len = 0.0
    run_start: float | None = None
    prev_t = None

    for timestamp, hit in hits:
        if not hit:
            if run_start is None:
                run_start = timestamp
        else:
            if run_start is not None and prev_t is not None:
                length = prev_t - run_start
                if length > best_len:
                    best_len, best = length, (run_start, prev_t)
            run_start = None
        prev_t = timestamp

    if run_start is not None and prev_t is not None:
        length = prev_t - run_start
        if length > best_len:
            best = (run_start, prev_t)
    return best


@dataclass
class SpikeRun:
    """What the detector found on each sampled frame, tallied."""

    total: int = 0
    person_hits: int = 0
    ball_hits: int = 0
    person_confs: list[float] = field(default_factory=list)
    ball_confs: list[float] = field(default_factory=list)
    ball_timeline: list[tuple[float, bool]] = field(default_factory=list)
    # Kept for the framing verdict. Boxes, not images: a couple of hundred
    # dataclasses per frame, so the whole run costs less than one frame does.
    per_frame: list[list] = field(default_factory=list)
    # What the detector was handed, which is the cropped and upscaled frame
    # rather than whatever the file says it is.
    seen_size: tuple[int, int] | None = None


def detect_sampled(frames, detector, batch_size: int, annotate_to: Path | None = None) -> SpikeRun:
    """Run `detector` over sampled `frames` in batches and tally what it found.

    With `annotate_to`, every frame is also written there with its boxes drawn.
    """
    run = SpikeRun()
    pending: list = []

    def flush(batch: list) -> None:
        if not batch:
            return
        for frame, detections in zip(batch, detector.detect_batch([f.image for f in batch])):
            run.total += 1
            run.per_frame.append(detections)
            if run.seen_size is None:
                height, width = frame.image.shape[:2]
                run.seen_size = (width, height)
            people = [d for d in detections if d.label == 'person']
            balls = [d for d in detections if d.label == 'ball']

            if people:
                run.person_hits += 1
                run.person_confs.extend(d.confidence for d in people)
            if balls:
                run.ball_hits += 1
                run.ball_confs.extend(d.confidence for d in balls)
            run.ball_timeline.append((frame.timestamp_s, bool(balls)))

            if annotate_to is not None:
                path = annotate_to / f'{frame.timestamp_s:07.1f}s.jpg'
                cv2.imwrite(str(path), annotate(frame.image, detections))
        batch.clear()

    for frame in frames:
        pending.append(frame)
        if len(pending) >= batch_size:
            flush(pending)
    flush(pending)
    return run


def print_spike(run: SpikeRun, args, elapsed: float, out_dir: Path) -> None:
    """Hit rates, the framing verdict, the longest ball gap and the speed."""
    print(
        f'\n  sampled {run.total} frames every {args.interval}s at conf>={args.conf}, imgsz={args.imgsz}'
    )
    print(summarize('person', run.person_hits, run.total, run.person_confs))
    print(summarize('ball', run.ball_hits, run.total, run.ball_confs))

    if run.seen_size is not None:
        width, height = run.seen_size
        if args.tiles > 1:
            # The model sees a tile, not the frame, so the letterbox that
            # matters is the tile's. Boxes come back in frame coordinates
            # either way, so only the denominator changes.
            x1, y1, x2, y2 = tile_windows(width, height, args.tiles)[0]
            width, height = x2 - x1, y2 - y1
        print()
        for line in assess_framing(run.per_frame, width, height, imgsz=args.imgsz).lines():
            print(f'  {line}')

    gap = longest_gap(run.ball_timeline)
    if gap and gap[1] - gap[0] > args.interval:
        print(f'\n  longest ball gap: {gap[0]:.0f}s -> {gap[1]:.0f}s ({gap[1] - gap[0]:.0f}s)')

    print(f'\n  {run.total / elapsed:.1f} frames/sec ({elapsed:.1f}s total)')
    if args.save_annotated:
        print(f'  annotated frames -> {out_dir}')

    if args.timeline:
        print()
        for timestamp, hit in run.ball_timeline:
            print(f'  {timestamp:6.0f}s  {"ball" if hit else "."}')

    print()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.video.exists():
        print(f'error: video not found: {args.video}')
        return 1

    info = video_info(args.video)
    print(f'\n{info.path.name}')
    print(f'  {info.width}x{info.height} @ {info.fps:.2f}fps   {info.duration_s / 60:.1f} min')
    if args.crop:
        print(f'  crop {args.crop}' + (f'   upscale {args.upscale}x' if args.upscale else ''))

    detector = PersonBallDetector(conf=args.conf, imgsz=args.imgsz, device=args.device)
    if args.tiles > 1:
        detector = TiledDetector(detector, tiles=args.tiles)
        print(f'  tiling into {args.tiles} along the long edge')

    out_dir = OUTPUT_ROOT / args.video.stem
    if args.save_annotated:
        out_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    frames = sample_frames(
        args.video,
        interval_s=args.interval,
        max_frames=args.max_frames,
        start_s=args.start,
        end_s=args.end,
        crop=args.crop,
        upscale=args.upscale,
    )
    run = detect_sampled(frames, detector, args.batch, out_dir if args.save_annotated else None)
    elapsed = time.perf_counter() - started

    if run.total == 0:
        print('\n  no frames sampled — check --start/--end/--interval\n')
        return 1

    print_spike(run, args, elapsed, out_dir)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
