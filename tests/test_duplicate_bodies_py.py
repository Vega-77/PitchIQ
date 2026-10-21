"""No two functions in the pipeline core (`pitchiq/*.py`) share a body.

The Python twin of `tests/test_duplicate_bodies.py`, which makes the case for
the JS side. The finding that motivated this one: `_clock_of`, five lines that
read a log entry's match clock, existed byte-identically in `pitchiq/blind.py`,
`pitchiq/phases.py` and `pitchiq/reconcile.py`. A fix to one (say, accepting a string
clock) would have reached a third of the callers. It is now `phases.clock_of`,
imported by the other two.

**There is no allowlist**, for the reason the JS test gives: a duplicate is a
finding, and a list of kept duplicates is a graveyard.

Scope, so the number is not over-trusted:

* Every function and method in `pitchiq/*.py`, nested ones included. The
  subpackages (`pitchiq/balltrack/`, `pitchiq/experiments/`, ...) are out: they are
  scripts with their own cleanup, and the core is what the report depends on.
* Bodies of at least five lines and three statements, so one-line accessors
  and `return None if x is None else round(x)` helpers do not swamp it.
* Two bodies match when their ASTs match with every variable name replaced
  by `_` and the docstring dropped. That catches a copy with its locals
  renamed, and also any two functions that call different helpers in the same
  shape; if that ever produces a false match, rename nothing -- make the shared
  shape a helper, which is what the match is telling you.
"""

import ast
import hashlib
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = sorted((ROOT / 'pitchiq').glob('*.py'))

MIN_LINES = 5
MIN_STATEMENTS = 3

# Measured when this was written: 41 files, 725 functions, 458 of them big
# enough to compare. The floors sit well under that so an ordinary deletion
# does not trip them, and far above a scan that silently found nothing.
MIN_FILES = 30
MIN_COMPARED = 300


class _Anonymise(ast.NodeTransformer):
    def visit_Name(self, node):
        return ast.copy_location(ast.Name(id='_', ctx=node.ctx), node)

    def visit_arg(self, node):
        return ast.copy_location(ast.arg(arg='_', annotation=None), node)


def shape(func):
    """A hash of `func`'s body with names erased and the docstring dropped."""
    body = list(func.body)
    if ast.get_docstring(func) is not None:
        body = body[1:]
    module = _Anonymise().visit(ast.Module(body=body, type_ignores=[]))
    return hashlib.md5(ast.dump(module).encode()).hexdigest()


def compared():
    """`[(where, shape)]` for every core function big enough to compare."""
    out = []
    for path in CORE:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.end_lineno - node.lineno + 1 < MIN_LINES or len(node.body) < MIN_STATEMENTS:
                continue
            out.append(('pitchiq/%s:%d:%s' % (path.name, node.lineno, node.name), shape(node)))
    return out


def test_the_scan_sees_the_core():
    assert len(CORE) >= MIN_FILES, [p.name for p in CORE]
    assert len(compared()) >= MIN_COMPARED


def test_no_two_core_functions_share_a_body():
    groups = defaultdict(list)
    for where, key in compared():
        groups[key].append(where)
    duplicates = [sorted(g) for g in groups.values() if len(g) > 1]
    assert not duplicates, 'functions with the same body; keep one and import it:\n' + '\n'.join(
        '  ' + ', '.join(g) for g in duplicates
    )


def test_the_scan_would_see_a_copy():
    """Guards the gate itself: a renamed copy hashes the same as its original."""
    original = ast.parse(
        'def a(entry):\n'
        "    value = entry.get('k')\n"
        '    if value is None:\n'
        '        return 0\n'
        '    return float(value)\n'
    ).body[0]
    renamed = ast.parse(
        'def b(row):\n'
        '    """Docstrings do not count."""\n'
        "    got = row.get('k')\n"
        '    if got is None:\n'
        '        return 0\n'
        '    return float(got)\n'
    ).body[0]
    assert shape(original) == shape(renamed)
