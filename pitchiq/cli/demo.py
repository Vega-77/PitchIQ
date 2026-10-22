"""Run the pipeline on a scripted nine-second match: no footage, GPU or detector.

python -m pitchiq demo
python -m pitchiq demo --out demo/      # keep the clip, calibration and report JSON

The clip is drawn on the spot (see pitchiq/demo.py), so this shows the chain
running end to end, not how well it sees a real match.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from pitchiq import demo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m pitchiq demo',
        description='Run the pipeline on a scripted nine-second match (no footage needed).',
    )
    parser.add_argument(
        '--out',
        type=Path,
        default=None,
        help='folder to keep demo.mp4, demo.calib.json and report.json in '
        '(default: a temporary folder, removed afterwards)',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Draw the clip, calibrate it, run analyse_match and print the summary."""
    args = build_parser().parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        folder = args.out or Path(tmp)
        folder.mkdir(parents=True, exist_ok=True)
        clip = folder / 'demo.mp4'
        print(f'Drawing a {demo.FRAMES / demo.FPS:.0f}-second scripted match ...')
        if not demo.write_clip(clip):
            print('This OpenCV build cannot write mp4 (no mp4v encoder).', file=sys.stderr)
            return 1
        calibration = demo.write_calibration(folder / 'demo.calib.json')
        print('Running analyse_match with the scripted detector ...\n')
        started = time.perf_counter()
        report = demo.run(clip, calibration)
        print(report.summary())
        print(f'\nDone in {time.perf_counter() - started:.0f} s.')
        if args.out:
            (folder / 'report.json').write_text(
                json.dumps(report.to_json(), indent=1, allow_nan=False), encoding='utf-8'
            )
            print(f'Clip, calibration and report.json are in {folder}')
    return 0
