"""Optional dependencies: a missing one says which extra installs it.

PitchIQ's heavy dependencies come in pip extras (`pyproject.toml`): `[detect]`
for the detector and trackers (Ultralytics, torch), `[balltrack]` for the ball
tracker. Without them a run used to end in a bare `ModuleNotFoundError` from
deep inside the code; `require` turns that into `MissingExtra`, whose message
names the install command, and `python -m pitchiq` prints it without a
traceback.

    YOLO = require('ultralytics', 'detect', 'The person and ball detector').YOLO
    check('balltrack')            # every module and file the ball tracker needs
    python -m pitchiq.extras balltrack     # the same check from a shell; exit 1 if missing
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

TAPNEXT = Path(__file__).resolve().parent / 'balltrack' / 'tapnext_bridge'

# What each extra must make importable, for `check`.
MODULES = {
    'detect': ['ultralytics', 'torch', 'torchvision'],
    'balltrack': ['ultralytics', 'torch', 'torchvision', 'xgboost', 'einops'],
}


class MissingExtra(ImportError):
    """An optional dependency is missing; the message says how to install it."""


def _install_hint(extra: str) -> str:
    """The command that installs `extra`, plus the CUDA note when it brings torch."""
    hint = f'Install it from the repo root with:  pip install -e .[{extra}]'
    if extra in ('detect', 'balltrack'):
        hint += (
            '\nFor a GPU, install the CUDA build of torch first (README.md, Setup;'
            ' the command is at the top of requirements.txt).'
        )
    return hint


def require(module: str, extra: str, what: str) -> ModuleType:
    """Import `module`, or raise MissingExtra naming `extra` if it (or its own
    dependency) is not installed. `what` names the feature that needs it."""
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as e:
        missing = e.name or module
        raise MissingExtra(
            f'{what} needs the Python package `{missing}`, which comes with the'
            f' [{extra}] extra and is not installed.\n{_install_hint(extra)}'
        ) from None


def tapnext_missing() -> list[str]:
    """What the TAPNext++ bridge needs on disk and does not have, as sentences."""
    out = []
    if not (TAPNEXT / 'tapnet' / 'tapnet').is_dir():
        out.append(f'the tapnet clone at {TAPNEXT / "tapnet"}')
    if not (TAPNEXT / 'tapnextpp_512.ckpt').is_file():
        out.append(f'the TAPNext++ checkpoint at {TAPNEXT / "tapnextpp_512.ckpt"}')
    return out


def check(extra: str) -> None:
    """Raise MissingExtra listing everything `extra` needs that is missing."""
    for module in MODULES[extra]:
        require(module, extra, f'The [{extra}] code')
    if extra == 'balltrack':
        files = tapnext_missing()
        if files:
            raise MissingExtra(
                'The ball tracker needs '
                + ' and '.join(files)
                + ', which pip does not install.\nrequirements-balltrack.txt has the'
                ' git clone and download commands.'
            )


def main(argv: list[str] | None = None) -> int:
    """Command line: <extra> - exit 0 when it is installed, else print why and exit 1."""
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 1 or args[0] not in MODULES:
        print(f'usage: python -m pitchiq.extras {{{",".join(MODULES)}}}', file=sys.stderr)
        return 2
    try:
        check(args[0])
    except MissingExtra as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
