"""Put a finished report on the coach's screen.

    python -m cv.experiments.publish_report report.json --team T --match M
        [--mapping cvMapping.json] [--dry-run]

`cv/publish.py` is the last mile of this whole project: every number the
pipeline works out is worth nothing until it is in Firestore where the app can
read it. It has been written, guarded and tested since the day the pipeline
started producing reports — and until now it had no way to be run. No `main`,
no `__main__`, and no caller anywhere outside `tests/`. `FOOTAGE_DAY.md` walks
through the whole intake and stops one step short, at "save that JSON as the
first baseline and commit it", because there was nothing to tell anyone to type
next. The gap was invisible to the test suite by construction: every piece
works, and nothing calls them.

So this file is thin on purpose. It parses arguments, reads two JSON files,
calls `publish` and prints what came back. Every decision that matters — which
paths may be written, what happens without a confirmed mapping, refusing to
create a player report — stays in `cv/publish.py`, where it is tested.

    Dry runs, and the one thing a dry run cannot tell you.

`--dry-run` swaps in a client that records writes instead of performing them,
so the payloads and their sizes can be looked at before a credential is
involved. It is the right way to see what a publish would do.

It has one blind spot, and the honest thing is to name it rather than to let
the output imply otherwise. `publish` skips any player whose report a coach has
not published yet, and it finds that out by reading the document. A dry run
reads nothing, so it assumes every mapped player has one. The real run may
therefore write fewer player reports than the dry run predicted, never more,
and the printed output says so out loud.

    The mapping file.

Cluster to player id, as confirmed by a coach in the cluster picker. Either the
`cvMapping/current` document as it comes out of Firestore — a `byCluster` map
alongside `updatedAt` and `updatedBy` — or a bare object of cluster id to
player id. Both because the first is what you get by exporting the document and
the second is what you get by typing it, and neither is worth a conversion
step.

Without one, the team-level statistics publish and the per-player ones do not.
That is `publish`'s decision and this tool does not override it: an unconfirmed
cluster is a guess about which child a set of numbers belongs to.

The service-account key comes from `PITCHIQ_SA_KEY` and nowhere else. See
`cv/publish.py`'s module docstring for how to set it, and for why it must live
outside this repository.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cv.publish import PublishError, publish

# What a dry run assumes about every mapped player, and the reason the count it
# prints is an upper bound rather than a prediction.
DRY_RUN_ASSUMES_REPORTS_EXIST = True


class _RecordedDoc:
    def __init__(self, recorder, path):
        self._recorder = recorder
        self._path = path

    def set(self, data):
        self._recorder.writes.append(('set', self._path, data))

    def update(self, data):
        self._recorder.writes.append(('update', self._path, data))

    def get(self):
        return self

    @property
    def exists(self) -> bool:
        return DRY_RUN_ASSUMES_REPORTS_EXIST

    def collection(self, name):
        return _RecordedCollection(self._recorder, self._path + '/' + name)


class _RecordedCollection:
    def __init__(self, recorder, path):
        self._recorder = recorder
        self._path = path

    def document(self, name):
        return _RecordedDoc(self._recorder, self._path + '/' + name)


class DryRunClient:
    """Records what would be written and writes nothing.

    Deliberately not imported from `tests/`: a tool that only works when the
    test package is importable is a tool that stops working the first time
    somebody copies the repo without it.
    """

    def __init__(self):
        self.writes: list[tuple[str, str, dict]] = []

    def collection(self, name):
        return _RecordedCollection(self, name)


def load_mapping(path: Path) -> dict[str, str]:
    """Cluster id to player id, from either shape the file comes in."""
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(
            f'{path.name}: expected an object, got {type(data).__name__}'
        )

    by_cluster = data['byCluster'] if 'byCluster' in data else data
    if not isinstance(by_cluster, dict):
        raise ValueError(f'{path.name}: byCluster is not an object')

    # Keys stringified because `publish` looks clusters up by `str(cluster_id)`,
    # and a mapping typed by hand may carry numbers.
    return {str(k): str(v) for k, v in by_cluster.items()}


def describe(written: dict, dry_run: bool) -> list[str]:
    """The lines to print, in the order they are worth reading."""
    lines = [
        'summary        ' + ('written' if written['summary'] else '-'),
        'identity       ' + ('written' if written['identity'] else '-'),
        f'events         {written["events"]}',
        f'thumbnails     {written["thumbs"]}',
        f'playerReports  {written["playerReports"]}',
    ]
    for note in written['skipped']:
        lines.append(f'  skipped: {note}')
    if dry_run and written['playerReports']:
        lines.append(
            '  a dry run does not read, so it assumed every mapped player '
            'already has a published report. The real run checks, and may '
            'write fewer.'
        )
    return lines


def describe_writes(client: DryRunClient) -> list[str]:
    """Path and payload size for each write a dry run held back."""
    lines = []
    for verb, path, data in client.writes:
        size = len(json.dumps(data, default=str))
        lines.append(f'  {verb:<6} {path}  ({size} bytes)')
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m cv.experiments.publish_report',
        description='Write a match report JSON to Firestore for the app to read.',
    )
    parser.add_argument('report', type=Path,
                        help='report JSON, as written by event_report --json')
    parser.add_argument('--team', required=True, help='team document id')
    parser.add_argument('--match', required=True, help='match document id')
    parser.add_argument('--mapping', type=Path,
                        help='cluster-to-player mapping confirmed by a coach; '
                             'without it, no per-player stats are written')
    parser.add_argument('--key',
                        help='service account JSON; defaults to PITCHIQ_SA_KEY')
    parser.add_argument('--dry-run', action='store_true',
                        help='record the writes and perform none; needs no '
                             'credential')
    parser.add_argument('--verbose', action='store_true',
                        help='with --dry-run, list every path and its size')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.report.is_file():
        print(f'no such report: {args.report}', file=sys.stderr)
        return 2
    if args.mapping is not None and not args.mapping.is_file():
        print(f'no such mapping: {args.mapping}', file=sys.stderr)
        return 2

    try:
        report_json = json.loads(args.report.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        print(f'{args.report.name} is not valid JSON: {exc}', file=sys.stderr)
        return 2

    mapping = None
    if args.mapping is not None:
        try:
            mapping = load_mapping(args.mapping)
        except (json.JSONDecodeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if not mapping:
            print(f'{args.mapping.name} confirms no clusters, so no per-player '
                  'stats will be written')

    client = DryRunClient() if args.dry_run else None

    try:
        written = publish(
            report_json,
            team_id=args.team,
            match_id=args.match,
            mapping=mapping,
            key_path=args.key,
            client=client,
        )
    except PublishError as exc:
        # The refusals are the point of `cv/publish.py`, and every one of them
        # says why. A traceback would bury that under a stack nobody needs.
        print(f'refused: {exc}', file=sys.stderr)
        return 1

    where = f'teams/{args.team}/matches/{args.match}'
    print(('would publish to ' if args.dry_run else 'published to ') + where)
    print()
    for line in describe(written, args.dry_run):
        print(line)

    if args.dry_run and args.verbose:
        print()
        for line in describe_writes(client):
            print(line)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
