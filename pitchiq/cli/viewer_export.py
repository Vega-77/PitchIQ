"""Pack a game's map, 3D ball, possession and moments for the coach's match viewer.

    python -m pitchiq viewer_export wbr --ours lighter --report wbr_report.json --out view/

Reads each half's map (`PitchIQ-data/stats/map/<tag>.npz`), its v17 3D ball, and who had the
ball (`pipe_<tag>.holds.npz`, which `cached_report half` writes beside its report), plus the
merged report's moments. Writes `index.json` and one JSON per chunk (`pitchiq/viewer_data.py`
says what is in them). A half without a holds file still exports, with no possession bar.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pitchiq import viewer_data
from pitchiq.balltrack.pitch_registration.merge_air_and_ground import camera_height_m
from pitchiq.map_table import MAPS, TRACK, VER

STATS = MAPS.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m pitchiq viewer_export',
        description="Pack a game's map, ball, possession and moments for the match viewer.",
    )
    parser.add_argument('game', help='the game tag; its halves are <game>1 and <game>2')
    parser.add_argument(
        '--ours',
        required=True,
        choices=('lighter', 'darker', '0', '1'),
        help="the coach's side, as the stats were run",
    )
    parser.add_argument('--report', type=Path, required=True, help='the merged report JSON')
    parser.add_argument(
        '--run-dir', type=Path, help='where pipe_<tag>.holds.npz are (default stats/<game>/run)'
    )
    parser.add_argument('--out', type=Path, required=True, help='the folder to write')
    return parser


def inputs(game: str, run_dir: Path) -> list[viewer_data.HalfInputs]:
    out = []
    for tag in (f'{game}1', f'{game}2'):
        venue = TRACK / 'pitch_registration' / f'{tag}_venue.npz'
        out.append(
            viewer_data.HalfInputs(
                tag=tag,
                map_path=MAPS / f'{tag}.npz',
                ball_path=TRACK / f'h3d_{VER}' / f'{tag}_ball3dd.npz',
                holds_path=run_dir / f'pipe_{tag}.holds.npz',
                camera_height_m=camera_height_m(str(venue)),
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    run_dir = a.run_dir or STATS / a.game / 'run'
    halves = inputs(a.game, run_dir)
    for h in halves:
        for p in (h.map_path, h.ball_path):
            if not p.exists():
                print(f'{h.tag}: {p} is missing', file=sys.stderr)
                return 1
        if not h.holds_path.exists():
            print(f'{h.tag}: no {h.holds_path.name}; no possession for this half')
    report = json.loads(a.report.read_text(encoding='utf-8'))
    try:
        index, chunks = viewer_data.build(a.game, halves, report, a.ours)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    paths = viewer_data.write(a.out, index, chunks)
    sizes = [p.stat().st_size for p in paths]
    print(
        f'{a.game}: {len(index["moments"])} moments ({index["team_mismatch"]} with a team unlike '
        f'their track), {len(chunks)} chunks; index {sizes[0] / 1e3:.0f} KB, chunks '
        f'{sum(sizes[1:]) / 1e6:.1f} MB (largest {max(sizes[1:], default=0) / 1e3:.0f} KB) '
        f'in {a.out}'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
