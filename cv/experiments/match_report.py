"""Run the whole pipeline over a clip and print the report.

    PitchIQHelper/.venv/Scripts/python.exe -m cv.experiments.match_report \
        --video "C:/Users/alexv/Videos/clip.mp4" --start 30 --end 50

This is the only entry point that exercises analyse_match end to end, which is
otherwise reachable only from Python. Without --calibration it still runs, and
reports possession and the team split; everything expressed in metres is
skipped and listed as a warning.

A word on what the numbers mean. On footage from a camera that pans or zooms,
they mean very little: tracks fragment as the view moves, the possession radius
is derived from apparent player height so it drifts with zoom, and nothing can
be expressed in metres without a calibration the moving camera cannot support.
Treat this as a check that the chain executes, not as a measurement.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cv.pipeline import analyse_match


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--video', required=True, help='path to the clip')
    parser.add_argument('--calibration', default=None,
                        help='calibration JSON; without it, metre-based stats are skipped')
    parser.add_argument('--start', type=float, default=0.0, help='seconds into the video')
    parser.add_argument('--end', type=float, default=None, help='seconds into the video')
    parser.add_argument('--conf', type=float, default=0.25, help='player confidence')
    parser.add_argument('--ball-conf', type=float, default=0.08, help='ball confidence')
    parser.add_argument('--imgsz', type=int, default=1280)
    parser.add_argument('--device', default='0', help="0 for the first GPU, or 'cpu'")

    # Tracking is the expensive half — about 26ms a frame against detection's
    # 12.5ms, because it runs one frame at a time. These make a run finish
    # sooner at the cost of holding player identity less well; see the
    # analyse_match docstring for what each one measured.
    parser.add_argument('--tracker', default='botsort.yaml',
                        choices=['botsort.yaml', 'bytetrack.yaml'],
                        help='bytetrack is ~1.9x faster and fragments more')
    parser.add_argument('--track-imgsz', type=int, default=None,
                        help='defaults to --imgsz; lowering it saves ~5%%')
    parser.add_argument('--track-stride', type=int, default=1,
                        help='process every Nth frame when tracking; 2 is ~1.8x faster')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    video = Path(args.video)
    if not video.exists():
        print(f'no such video: {video}', file=sys.stderr)
        return 2

    device = args.device if args.device == 'cpu' else int(args.device)

    # analyse_match holds every frame of the window in memory at once, so a
    # long window is a way to run the machine out of RAM rather than a way to
    # analyse more football. Warn rather than silently trying.
    window = (args.end - args.start) if args.end else None
    if window is None or window > 120:
        print('WARNING: analyse_match buffers the whole window in RAM '
              '(~2.8 MB per 720p frame, so ~5 GB per minute). Pass --end.',
              file=sys.stderr)

    report = analyse_match(
        video,
        calibration_path=args.calibration,
        start_s=args.start,
        end_s=args.end,
        conf=args.conf,
        ball_conf=args.ball_conf,
        imgsz=args.imgsz,
        device=device,
        tracker=args.tracker,
        track_imgsz=args.track_imgsz,
        track_stride=args.track_stride,
    )

    print()
    print(report.summary())
    print()
    print(f'  processing        {report.processing_s:.1f}s '
          f'for {report.duration_s:.0f}s of video '
          f'({report.processing_s / max(report.duration_s, 1e-9):.1f}x realtime)')
    print(f'  kit separation    {report.kit_separation:.1f}')
    print(f'  clear holder      {report.clear_holder_share:.0%} of frames')

    return 0 if report.is_trustworthy else 1


if __name__ == '__main__':
    raise SystemExit(main())
