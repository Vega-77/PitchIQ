"""No browser module is finished, imported by nobody, and shipped anyway.

`tests/test_call_graph.py` looks for code that is written, tested in isolation
and connected to nothing — the shape a test-per-function cannot see, because the
shape is the *absence* of an edge. It names its own blind spot in its docstring:
*"the browser modules themselves, which are not walked at all — an export there
is reached from `<script type=\"module\">` and from event handlers that no Python
AST is going to find."* This is that walk.

It is a coarser question than the Python one, and deliberately so. Asking which
*exports* are dead would be noise here: `assets/report.js` has no imports by
design so `tests/video.test.js` can cover it, and roughly ninety internals across
the frontend are exported purely so a namespace-importing test can reach them.
Those are a documented pattern, not a finding. Asking which *modules* are dead is
a tight question with no false positives — an ES import names its source file
explicitly, so a module nothing imports and no page loads is genuinely never
executed by anyone.

Three things are pinned:

  * every module under the frontend directories is reachable from a page,
  * every page has exactly one entry point and it is a module we walk, and
  * the import graph has no cycles.

The last one is a design decision the roadmap already records: `coach.js` imports
`review.js`, and the two calls back the other way are registered as callbacks
rather than imported, precisely because a direct call would close a cycle. ES
modules survive cycles, so nothing would have crashed — it would have produced a
binding that is undefined at import time and defined later, which is a bug that
reads like a typo.

The specifier regex here is quote-agnostic on purpose, unlike the one in
`stamp_version.py`. If a specifier were ever retyped with double quotes this walk
must still see it; the check that the *stamper* also sees it lives in
`tests/test_version_stamp.py`.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import stamp_version

REPO = Path(__file__).resolve().parent.parent

MODULE_DIRS = stamp_version.MODULE_DIRS
PAGES = stamp_version.PAGES

# A module that is deliberately not loaded by any page yet. Same contract as
# PENDING_BY_DESIGN in tests/test_call_graph.py: a name here needs a reason
# somebody can read, and a name that has since been wired up has to come out,
# or the list rots into a graveyard nobody dares touch.
#
# Empty, and that is the finding — every module the frontend ships is reached.
STRANDED_BY_DESIGN: dict[str, str] = {}

SPEC = re.compile(
    r"""(?:from|import)\s*\(?\s*(['"])(\.{1,2}/[^'"]+\.js)(?:\?v=\d+)?\1""")
ENTRY = re.compile(
    r"""<script[^>]*\btype=(['"])module\1[^>]*\bsrc=(['"])([^'"]+)\2""", re.I)


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def modules() -> dict[Path, str]:
    """Every frontend module, keyed by resolved path."""
    found = {}
    for directory in MODULE_DIRS:
        for path in sorted((REPO / directory).glob('*.js')):
            found[path.resolve()] = _read(path)
    return found


def imports_of(path: Path, source: str) -> list[Path]:
    return [(path.parent / spec).resolve()
            for _quote, spec in SPEC.findall(source)]


def entry_points() -> dict[Path, list[str]]:
    """Resolved module -> the pages whose <script type="module"> names it."""
    found: dict[Path, list[str]] = {}
    for page in PAGES:
        path = REPO / page
        for _q1, _q2, src in ENTRY.findall(_read(path)):
            src = src.split('?')[0]
            if src.startswith(('http:', 'https:', '//')):
                continue
            found.setdefault(
                (path.parent / src).resolve(), []).append(page)
    return found


def reachable() -> set[Path]:
    source = modules()
    seen = set(entry_points())
    stack = list(seen)
    while stack:
        current = stack.pop()
        if current not in source:
            continue
        for target in imports_of(current, source[current]):
            if target not in seen:
                seen.add(target)
                stack.append(target)
    return seen


class TestNothingIsStranded(unittest.TestCase):
    def test_every_module_is_reached_from_a_page_or_has_a_recorded_reason(self):
        found = reachable()
        stranded = sorted(_rel(Path(p)) for p in modules() if p not in found)
        unexplained = [m for m in stranded if m not in STRANDED_BY_DESIGN]
        self.assertEqual(
            unexplained, [],
            'a module no page loads and nothing imports — either wire it up, '
            'delete it, or record why it is waiting in STRANDED_BY_DESIGN')

    def test_the_record_has_not_gone_stale(self):
        found = reachable()
        stranded = {_rel(Path(p)) for p in modules() if p not in found}
        wired = sorted(name for name in STRANDED_BY_DESIGN
                       if name not in stranded)
        self.assertEqual(
            wired, [],
            'these are loaded now and should come out of the list')

    def test_every_reason_says_something(self):
        for name, reason in STRANDED_BY_DESIGN.items():
            self.assertGreater(len(reason), 40, f'{name}: say why')


class TestTheGraphItself(unittest.TestCase):
    def test_every_page_has_exactly_one_entry_point(self):
        by_page: dict[str, list[str]] = {}
        for module, pages in entry_points().items():
            for page in pages:
                by_page.setdefault(page, []).append(_rel(Path(module)))
        self.assertEqual(sorted(by_page), sorted(PAGES), 'a page loads nothing')
        for page, found in sorted(by_page.items()):
            self.assertEqual(len(found), 1, f'{page} loads {found}')

    def test_every_entry_point_is_a_module_we_walk(self):
        # An entry point outside MODULE_DIRS would be loaded by the browser,
        # never stamped, and invisible to this walk all at once.
        source = modules()
        outside = sorted(_rel(Path(p)) for p in entry_points()
                         if p not in source)
        self.assertEqual(outside, [])

    def test_the_walk_actually_follows_imports(self):
        # The anti-vacuum guard. A regex that matched nothing would leave the
        # entry points reachable and every other module stranded, which the
        # first test would catch — but a regex that matched only the *first*
        # import of a file would not. So pin a two-hop path: review.js is not
        # an entry point, it is reached through coach.js, which is the whole
        # reason a graph walk beats a grep.
        found = reachable()
        review = (REPO / 'coach' / 'review.js').resolve()
        self.assertNotIn(review, entry_points(), 'no page loads review.js')
        self.assertIn(review, found, 'review.js was not reached via coach.js')

        auth = (REPO / 'assets' / 'auth.js').resolve()
        coach = (REPO / 'coach' / 'coach.js').resolve()
        self.assertIn(auth, imports_of(coach, modules()[coach]))

    def test_no_specifier_points_at_a_file_that_is_not_there(self):
        source = modules()
        broken = []
        for path, text in source.items():
            for target in imports_of(path, text):
                if target not in source:
                    broken.append(f'{_rel(path)} -> {target}')
        self.assertEqual(broken, [])


class TestNoCycles(unittest.TestCase):
    """A cycle does not crash an ES module. It gives you a binding that is
    undefined at import time and defined a moment later, and the symptom reads
    like a typo in a name that is spelled correctly."""

    def _cycles(self) -> list[str]:
        source = modules()
        state: dict[Path, int] = {}
        found: list[str] = []

        def walk(node: Path, path: list[Path]) -> None:
            state[node] = 1
            for target in imports_of(node, source[node]):
                if target not in source:
                    continue
                if state.get(target) == 1:
                    loop = path[path.index(target):] + [target]
                    found.append(' -> '.join(_rel(p) for p in loop))
                elif target not in state:
                    walk(target, path + [target])
            state[node] = 2

        for node in sorted(source):
            if node not in state:
                walk(node, [node])
        return found

    def test_the_import_graph_is_acyclic(self):
        self.assertEqual(self._cycles(), [])

    def test_a_cycle_would_be_found(self):
        # Same reason the walk above is pinned: an empty result is only worth
        # something if a non-empty one is reachable. Two modules that import
        # each other, checked against the same traversal.
        state: dict[str, int] = {}
        graph = {'a': ['b'], 'b': ['c'], 'c': ['a'], 'd': []}
        found: list[str] = []

        def walk(node: str, path: list[str]) -> None:
            state[node] = 1
            for target in graph[node]:
                if state.get(target) == 1:
                    found.append(' -> '.join(path[path.index(target):]
                                             + [target]))
                elif target not in state:
                    walk(target, path + [target])
            state[node] = 2

        for node in sorted(graph):
            if node not in state:
                walk(node, [node])
        self.assertEqual(found, ['a -> b -> c -> a'])


if __name__ == '__main__':
    unittest.main()
