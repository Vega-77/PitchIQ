"""Diff two report JSONs, so a change to the pipeline has to explain itself.

    python -m cv.experiments.compare_reports baselines/first-half.json runs/today.json

The roadmap's Testing Strategy has asked for this from the beginning — "rerun
the pipeline against these fixed clips after every change and diff the output,
this is your regression suite" — and there has never been anything to rerun
against. Every threshold in `cv/touches.py`, `cv/participants.py` and
`cv/phases.py` is a guess, and the tests pin what those guesses *are*, not what
they *do* to a real match. Change one and the unit tests still pass; the only
thing that notices is the output.

    Why a tolerance rather than an exact match.

Floating point, mostly. A report is full of means and medians over hundreds of
frames, and the last decimal place moves when numpy does. Exact comparison would
fail every run and be switched off within a week, which is worse than no
comparison. So numbers compare within a relative tolerance and a small absolute
floor — the floor matters because a relative tolerance is meaningless near zero,
where 0.001 and 0.002 differ by 100%.

    What it deliberately will not do.

It will not tell you which run is right. A baseline is a run somebody looked at
and believed; it is not truth, and a diff against it is a prompt to look, not a
verdict. The exit code is there so this can gate a commit, not so a green run
can be taken as proof.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# How long this run took is not a property of the football. It changes with the
# machine, the GPU and whatever else was open, and including it would make every
# comparison fail for the least interesting reason available.
VOLATILE = ('processing_s',)

# Relative, and an absolute floor beneath which relative means nothing.
DEFAULT_TOLERANCE = 0.02
DEFAULT_ABS = 1e-6


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _close(a: float, b: float, tolerance: float, abs_tol: float) -> bool:
    return math.isclose(a, b, rel_tol=tolerance, abs_tol=abs_tol)


def compare(
    baseline,
    current,
    tolerance: float = DEFAULT_TOLERANCE,
    abs_tol: float = DEFAULT_ABS,
    ignore=VOLATILE,
    path: str = '',
) -> list[str]:
    """Everything that differs, as lines a person can read.

    Pure and recursive: dicts by key, lists by length and then element-wise,
    numbers by tolerance, everything else by equality. Returns an empty list
    when the two agree, which is the only quiet answer this gives.
    """
    ignore = frozenset(ignore or ())
    here = path or '(root)'

    if isinstance(baseline, dict) and isinstance(current, dict):
        out = []
        for key in sorted(set(baseline) | set(current)):
            if key in ignore:
                continue
            where = f'{path}.{key}' if path else key
            if key not in current:
                out.append(f'{where}: gone (was {_brief(baseline[key])})')
            elif key not in baseline:
                out.append(f'{where}: new ({_brief(current[key])})')
            else:
                out.extend(compare(
                    baseline[key], current[key], tolerance, abs_tol, ignore, where,
                ))
        return out

    if isinstance(baseline, list) and isinstance(current, list):
        if len(baseline) != len(current):
            # Report the count and stop. Two lists of different lengths differ
            # at almost every index once they slip by one, and printing four
            # hundred of those buries the one fact that matters.
            return [f'{here}: {len(baseline)} items became {len(current)}']
        out = []
        for index, (was, now) in enumerate(zip(baseline, current)):
            out.extend(compare(
                was, now, tolerance, abs_tol, ignore, f'{path}[{index}]',
            ))
        return out

    # None and a number are not a small difference. "We could not measure this"
    # becoming "we measured 0.0" is one of the changes most worth catching, and
    # a numeric comparison would either crash or quietly call it equal.
    if (baseline is None) != (current is None):
        return [f'{here}: {_brief(baseline)} -> {_brief(current)}']

    if _is_number(baseline) and _is_number(current):
        if _close(float(baseline), float(current), tolerance, abs_tol):
            return []
        return [f'{here}: {baseline} -> {current}{_delta(baseline, current)}']

    if baseline != current:
        return [f'{here}: {_brief(baseline)} -> {_brief(current)}']

    return []


def _delta(was, now) -> str:
    if not was:
        return ''
    return f'  ({(now - was) / abs(was):+.1%})'


def _brief(value, limit: int = 60) -> str:
    text = json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def load(path: Path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'{path} is not a report')
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Diff a pipeline run against a baseline run.',
    )
    parser.add_argument('baseline', type=Path,
                        help='a report JSON somebody looked at and believed')
    parser.add_argument('current', type=Path,
                        help='the report JSON from the run being checked')
    parser.add_argument('--tolerance', type=float, default=DEFAULT_TOLERANCE,
                        help='relative tolerance for numbers (default 0.02)')
    parser.add_argument('--abs', dest='abs_tol', type=float, default=DEFAULT_ABS,
                        help='absolute floor, for numbers near zero where a '
                             'relative tolerance means nothing')
    parser.add_argument('--any-window', action='store_true',
                        help='ignore the window and source, for comparing '
                             'different clips of the same match')
    parser.add_argument('--quiet', action='store_true',
                        help='exit code only, no listing')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    for path in (args.baseline, args.current):
        if not path.exists():
            print(f'no such report: {path}', file=sys.stderr)
            return 2

    ignore = set(VOLATILE)
    if args.any_window:
        ignore |= {'window', 'source', 'duration_s'}

    differences = compare(
        load(args.baseline), load(args.current),
        tolerance=args.tolerance, abs_tol=args.abs_tol, ignore=ignore,
    )

    if not differences:
        if not args.quiet:
            print(f'no change beyond {args.tolerance:.0%}')
        return 0

    if not args.quiet:
        print(f'{len(differences)} differences, {args.baseline.name} -> '
              f'{args.current.name}')
        print()
        for line in differences:
            print(f'  {line}')
        print()
        # Said out loud because an exit code of 1 reads as a failure, and this
        # is not one. A pipeline change is supposed to change the output.
        print('A difference is not a failure. The baseline is a run somebody '
              'believed, not the truth.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
