"""`python -m pitchiq <command> [args]`, and the `pitchiq` script that pip installs.

Only dispatch lives here: the command's own parser reads the arguments, so
`python -m pitchiq calibrate --help` prints the calibrate help.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys

from pitchiq import __version__
from pitchiq.cli import COMMANDS
from pitchiq.extras import MissingExtra


def summary(name: str) -> str:
    """The first line of a command's module docstring, read without importing it."""
    origin = importlib.util.find_spec(f'pitchiq.cli.{name}').origin
    with open(origin, encoding='utf-8') as f:
        doc = ast.get_docstring(ast.parse(f.read())) or ''
    return doc.split('\n', 1)[0]


def usage() -> str:
    """The usage text: how to call it and the commands it knows."""
    width = max(map(len, COMMANDS)) + 2
    return (
        'usage: python -m pitchiq <command> [args]\n'
        '       python -m pitchiq --version\n\ncommands:\n'
        + ''.join(f'  {name:<{width}}{summary(name)}\n' for name in COMMANDS)
        + '\npython -m pitchiq <command> --help prints its options.\n'
    )


def main(argv: list[str] | None = None) -> int:
    """Run the named command with the remaining arguments; returns its exit code."""
    args = sys.argv[1:] if argv is None else list(argv)
    if not args or args[0] in ('-h', '--help'):
        print(usage(), end='', file=sys.stdout if args else sys.stderr)
        return 0 if args else 2
    if args[0] == '--version':
        print(f'pitchiq {__version__}')
        return 0
    name, rest = args[0], args[1:]
    if name not in COMMANDS:
        print(f'unknown command {name!r}\n\n{usage()}', end='', file=sys.stderr)
        return 2
    try:
        return importlib.import_module(f'pitchiq.cli.{name}').main(rest)
    except MissingExtra as e:
        # An install question, not a bug: the message is the whole answer.
        print(e, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
