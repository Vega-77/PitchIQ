"""Write a report JSON from a half's top-down map: team shape and where each team stood.

    python -m pitchiq map_report <map.npz> --ours lighter --period 1 --out report.json
    python -m pitchiq map_report <map.npz> --ours darker --into pipeline.json --out report.json

The map table comes from `pitchiq/experiments/wholemap.py`. Without `--into`
the report carries the map's figures only and every event figure is null
(`report_export.map_report_json`); with it, the map's figures are put into an
existing pipeline report (`report_export.attach_map`). Either way the result
publishes with `python -m pitchiq publish_report`.

`--ours` says which side is the coach's: the lighter or darker shirts, or map
team 0 or 1. The map cannot know which side is "us", so there is no default.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pitchiq import map_stats
from pitchiq.report_export import attach_map, map_report_json

OURS = ('lighter', 'darker', '0', '1')


def which_team(path: Path, ours: str) -> int:
    """The map team (0 or 1) that `--ours` names."""
    if ours in ('0', '1'):
        return int(ours)
    light = map_stats.lighter_team(path)
    return light if ours == 'lighter' else 1 - light


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m pitchiq map_report',
        description="Write a report JSON from a half's top-down map.",
    )
    parser.add_argument('map', type=Path, help='map table (.npz) from experiments/wholemap.py')
    parser.add_argument(
        '--ours', required=True, choices=OURS, help="the coach's side: shirt shade or map team"
    )
    parser.add_argument('--period', type=int, choices=(1, 2), help='which half this is')
    parser.add_argument('--into', type=Path, help='pipeline report JSON to put the figures in')
    parser.add_argument('--out', type=Path, help='where to write; default prints it')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.map.is_file():
        print(f'no such map: {args.map}', file=sys.stderr)
        return 2
    t = map_stats.MapTable.load(args.map)
    block = map_stats.report_block(t, which_team(args.map, args.ours))
    if args.into is not None:
        if not args.into.is_file():
            print(f'no such report: {args.into}', file=sys.stderr)
            return 2
        report = attach_map(json.loads(args.into.read_text(encoding='utf-8')), block)
    else:
        frames = (int(t.f.min()), int(t.f.max()))
        report = map_report_json(
            block,
            source=args.map.stem,
            length_m=t.length_m,
            width_m=t.width_m,
            duration_s=(frames[1] - frames[0] + 1) / t.fps,
            period=args.period,
            window={'start_s': round(frames[0] / t.fps, 1), 'end_s': round(frames[1] / t.fps, 1)},
        )
    text = json.dumps(report, indent=1, allow_nan=False)
    if args.out is None:
        print(text)
    else:
        args.out.write_text(text + '\n', encoding='utf-8')
        print(f'wrote {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
