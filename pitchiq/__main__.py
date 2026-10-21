"""`python -m pitchiq <command> [args]`, and the `pitchiq` script that pip installs.

Only dispatch lives here: the command's own parser reads the arguments, so
`python -m pitchiq calibrate --help` prints the calibrate help.
"""

from __future__ import annotations

import importlib
import sys

from pitchiq.cli import COMMANDS


def usage() -> str:
    return 'usage: python -m pitchiq <command> [args]\n\ncommands:\n' + ''.join(
        f'  {name}\n' for name in COMMANDS
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if not args or args[0] in ('-h', '--help'):
        print(usage(), end='', file=sys.stdout if args else sys.stderr)
        return 0 if args else 2
    name, rest = args[0], args[1:]
    if name not in COMMANDS:
        print(f'unknown command {name!r}\n\n{usage()}', end='', file=sys.stderr)
        return 2
    return importlib.import_module(f'pitchiq.cli.{name}').main(rest)


if __name__ == '__main__':
    sys.exit(main())
