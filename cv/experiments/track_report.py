"""Characterise tracking quality on a piece of footage.

Detection accuracy alone says nothing about whether per-player statistics are
possible. What matters is whether one track stays attached to one player, and
the number that exposes it is tracks-per-player: roughly 22 people are on the
pitch, so 90 tracks means the average player is being cut into four different
people and their "distance covered" is really four partial figures.

    python -m cv.experiments.track_report "match.mp4" --start 30 --end 60
    python -m cv.experiments.track_report "match.mp4" --compare-strides
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from cv.tracking import PlayerTracker, drop_short_tracks, split_by_label

# Two teams, a keeper each, plus officials in frame.
EXPECTED_PEOPLE = 25


def summarise(tracks, window_s: float, elapsed_s: float, label: str) -> dict:
    people, balls = split_by_label(tracks)
    kept = drop_short_tracks(people, min_frames=5)

    durations = sorted((t.duration_s for t in kept.values()), reverse=True)
    coverage = [d / window_s for d in durations[:EXPECTED_PEOPLE]]

    return {
        "label": label,
        "raw": len(people),
        "kept": len(kept),
        "balls": len(balls),
        "fragmentation": len(kept) / EXPECTED_PEOPLE if kept else 0.0,
        "longest_s": durations[0] if durations else 0.0,
        "median_top25_s": (
            sorted(durations[:EXPECTED_PEOPLE])[len(durations[:EXPECTED_PEOPLE]) // 2]
            if durations else 0.0
        ),
        "mean_coverage": sum(coverage) / len(coverage) if coverage else 0.0,
        "realtime_ratio": window_s / elapsed_s if elapsed_s else 0.0,
    }


def print_row(s: dict) -> None:
    print(
        f"  {s['label']:<10} {s['raw']:>5} {s['kept']:>6} {s['fragmentation']:>7.1f}x"
        f" {s['longest_s']:>8.1f}s {s['mean_coverage']:>9.0%} {s['balls']:>6}"
        f" {s['realtime_ratio']:>9.1f}x"
    )


def print_header() -> None:
    print(
        f"\n  {'':<10} {'raw':>5} {'kept':>6} {'frag':>8} {'longest':>9}"
        f" {'coverage':>9} {'balls':>6} {'speed':>10}"
    )
    print("  " + "-" * 70)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cv.experiments.track_report",
        description="Measure whether tracks stay attached to players.",
    )
    parser.add_argument("video", type=Path)
    parser.add_argument("--start", type=float, default=30.0)
    parser.add_argument("--end", type=float, default=60.0)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--imgsz", type=int, default=1280)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--device", default=0)
    parser.add_argument(
        "--compare-strides", action="store_true",
        help="run 1/2/3 and show what frame-skipping costs",
    )
    parser.add_argument(
        "--compare-trackers", action="store_true",
        help="compare the tracking algorithms ultralytics ships",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.video.exists():
        print(f"error: video not found: {args.video}")
        return 1

    window_s = args.end - args.start
    strides = [1, 2, 3] if args.compare_strides else [args.stride]

    print(f"\n{args.video.name}  {args.start:.0f}s-{args.end:.0f}s "
          f"(conf {args.conf}, imgsz {args.imgsz})")
    print_header()

    runs = (
        [(t, args.stride, t.replace(".yaml", ""))
         for t in ("bytetrack.yaml", "botsort.yaml", "ocsort.yaml", "deepocsort.yaml")]
        if args.compare_trackers
        else [("bytetrack.yaml", s, f"stride {s}") for s in strides]
    )

    results = []
    for tracker_cfg, stride, label in runs:
        # A fresh model each run so tracker state cannot leak between configs.
        tracker = PlayerTracker(
            conf=args.conf, imgsz=args.imgsz, device=args.device, tracker=tracker_cfg
        )
        started = time.perf_counter()
        try:
            tracks = tracker.run(
                args.video, start_s=args.start, end_s=args.end, stride=stride
            )
        except Exception as err:
            print(f"  {label:<10} failed: {str(err)[:60]}")
            continue
        elapsed = time.perf_counter() - started

        summary = summarise(tracks, window_s, elapsed, label)
        results.append(summary)
        print_row(summary)

    if not results:
        return 1

    best = min(results, key=lambda s: s["fragmentation"])
    print(
        f"\n  fragmentation = tracks kept / {EXPECTED_PEOPLE} expected people."
        "\n  1.0x is ideal; 4x means the average player is split four ways and"
        "\n  every per-player total is really a fragment."
    )
    print(f"\n  coverage = how much of the window the {EXPECTED_PEOPLE} longest tracks span.")
    print(f"\n  best here: {best['label']} at {best['fragmentation']:.1f}x")

    if all(r["balls"] == 0 for r in results):
        print(
            f"\n  No ball tracked at conf {args.conf}. The ball needs a much lower"
            "\n  threshold than players — try --conf 0.08 — which is why it warrants"
            "\n  its own detector rather than sharing the players' settings."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
