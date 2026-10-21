"""A stylesheet as the browser assembles it.

`shared/app.css` is an entry that `@import`s the design system in parts from
`shared/styles/`, in cascade order. A scan that read the entry's own text would
see eleven import lines and nothing else -- and pass, with nothing checked. So
every CSS scan in this folder reads through `assembled`, which puts each part's
text where its import was, and a rule keeps the name of the sheet a page links.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

IMPORT = re.compile(r"@import\s+url\(\s*'([^'?]+)(?:\?v=\d+)?'\s*\)\s*;")


def assembled(rel):
    """The sheet at `rel` (repo-relative) with every local import inlined."""
    path = ROOT / rel
    text = path.read_text(encoding='utf-8')
    return IMPORT.sub(
        lambda m: assembled((path.parent / m.group(1)).relative_to(ROOT).as_posix()), text
    )


def files_of(rel):
    """`rel` and every part it imports, depth first, as repo-relative paths.

    For the scans that report a line number: a line of the assembled text is
    a line of no file anyone can open.
    """
    path = ROOT / rel
    out = [rel]
    for m in IMPORT.finditer(path.read_text(encoding='utf-8')):
        out.extend(files_of((path.parent / m.group(1)).relative_to(ROOT).as_posix()))
    return out


def read_text(rel):
    """Any frontend file; a stylesheet comes back assembled."""
    if rel.endswith('.css'):
        return assembled(rel)
    return (ROOT / rel).read_text(encoding='utf-8')
