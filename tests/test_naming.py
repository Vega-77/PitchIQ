"""The naming rules in docs/conventions.md that a machine can check.

Each gate starts at zero and has no allowlist: a gate written over a mess only
records the mess (see tests/test_data_seam.py). A gate joins this file in the
commit that brings its count to zero.
"""

import ast
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A module docstring opens with one summary line; anything longer is a
# paragraph that lost its line break.
SUMMARY_MAX = 90


def tracked(pattern: str) -> list[Path]:
    """Every file git tracks that matches `pattern`."""
    out = subprocess.check_output(['git', 'ls-files', pattern], cwd=ROOT, text=True)
    return [ROOT / line for line in out.splitlines()]


def test_the_scan_sees_the_python():
    assert len(tracked('*.py')) > 200


def test_module_docstrings_open_with_one_short_line():
    long = []
    for path in tracked('*.py'):
        doc = ast.get_docstring(ast.parse(path.read_text(encoding='utf-8')))
        if doc and len(doc.splitlines()[0]) > SUMMARY_MAX:
            long.append('%s (%d)' % (path.relative_to(ROOT).as_posix(), len(doc.splitlines()[0])))
    assert not long, 'module docstring first line over %d characters:\n  %s' % (
        SUMMARY_MAX,
        '\n  '.join(long),
    )
