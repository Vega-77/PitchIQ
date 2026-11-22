"""Put a game's match viewer data on the coach's match page.

    python -m pitchiq publish_viewer view/ --team T --match M [--dry-run]

`view/` is what `viewer_export` wrote: `index.json` and one JSON per chunk. They go to
`teams/{T}/matches/{M}/cvViewer/*` through `pitchiq/publish_to_firestore.py`'s
`publish_viewer`, which checks every document before writing any and removes chunks a
re-run no longer has. Apart from `publish_report` on purpose: the stats publish without
the viewer, so a viewer that fails costs the coach the viewer and nothing else.

The service-account key comes from `PITCHIQ_SA_KEY`, as for `publish_report`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pitchiq.cli.publish_report import DryRunClient, describe_writes
from pitchiq.publish_to_firestore import PublishError, publish_viewer


def load_view(folder: Path) -> tuple[dict, list[dict]]:
    """The index and its chunks, in the index's order, from a viewer_export folder."""
    index = json.loads((folder / 'index.json').read_text(encoding='utf-8'))
    chunks = []
    for half in index.get('halves') or []:
        for cid in half.get('chunks') or []:
            path = folder / f'{cid}.json'
            if not path.is_file():
                raise FileNotFoundError(
                    f'{folder.name}: the index names {cid} and there is no {path.name}'
                )
            chunks.append(json.loads(path.read_text(encoding='utf-8')))
    return index, chunks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m pitchiq publish_viewer',
        description="Write a game's match viewer data to Firestore for the coach's match page.",
    )
    parser.add_argument(
        'view', type=Path, help='the folder viewer_export wrote (index.json and chunks)'
    )
    parser.add_argument('--team', required=True, help='team document id')
    parser.add_argument('--match', required=True, help='match document id')
    parser.add_argument('--key', help='service account JSON; defaults to PITCHIQ_SA_KEY')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='record the writes and perform none; needs no credential',
    )
    parser.add_argument(
        '--verbose', action='store_true', help='with --dry-run, list every path and its size'
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (args.view / 'index.json').is_file():
        print(f'no index.json in {args.view}', file=sys.stderr)
        return 2
    try:
        index, chunks = load_view(args.view)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    client = DryRunClient() if args.dry_run else None
    try:
        written = publish_viewer(
            index, chunks, team_id=args.team, match_id=args.match, key_path=args.key, client=client
        )
    except PublishError as exc:
        print(f'refused: {exc}', file=sys.stderr)
        return 1

    where = f'teams/{args.team}/matches/{args.match}/cvViewer'
    print(('would publish to ' if args.dry_run else 'published to ') + where)
    print(
        f'{written["documents"]} documents: {written["chunks"]} chunks, {written["moments"]} '
        f'moments, {written["bytes"] / 1e6:.1f} MB'
    )
    if written['removed']:
        print(
            f'removed {len(written["removed"])} chunks the last publish had: {", ".join(written["removed"])}'
        )
    if args.dry_run and args.verbose:
        for line in describe_writes(client):
            print(line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
