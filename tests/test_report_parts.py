"""The report modules import nothing but each other.

`shared/report.js` is the match report's logic: what a player may see, the
match clock, the quality notes, the stat rows. It is split across the
`shared/report-*.js` files and re-exported from `report.js`, which every page
and the pure JS suites (`npm run test:unit`) import. Those suites load it
straight from disk under Node, with no DOM and no Firestore, and that only
works while no part reaches for either. Before the split the rule was
"report.js has no imports"; this is the same rule for a module made of
several files.
"""

import re
from pathlib import Path

SHARED = Path(__file__).resolve().parents[1] / 'shared'

# `import { ... } from '...'`, `export { ... } from '...'`, `import * as x from '...'`
# and a bare `import '...'`; a `{ ... }` clause may run over several lines.
FROM = re.compile(
    r"""^(?:(?:import|export)\s*(?:\{[^}]*\}|\*\s+as\s+\w+)\s*from\s*|import\s+)['"]([^'"]+)['"]""",
    re.M,
)
SIBLING = re.compile(r'\./report-[a-z-]+\.js(\?v=\d+)?')
DECLARATION = re.compile(r'^(?:export\s+)?(?:async\s+)?(?:const|let|var|function|class)\b', re.M)


def parts() -> list[Path]:
    """The `shared/report-*.js` files."""
    return sorted(SHARED.glob('report-*.js'))


def test_the_report_is_split_into_parts():
    assert len(parts()) >= 10


def test_the_report_modules_import_only_each_other():
    for path in [SHARED / 'report.js', *parts()]:
        text = path.read_text(encoding='utf-8')
        assert 'import(' not in text, path.name
        for spec in FROM.findall(text):
            assert SIBLING.fullmatch(spec), (path.name, spec)


def test_every_part_is_re_exported_or_imported_by_a_part():
    """A part nothing reaches is a part no page and no test can load."""
    sources = [(SHARED / 'report.js').read_text(encoding='utf-8')]
    sources += [p.read_text(encoding='utf-8') for p in parts()]
    reached = {re.sub(r'\?v=\d+$', '', spec)[2:] for text in sources for spec in FROM.findall(text)}
    assert reached == {p.name for p in parts()}


def test_the_barrel_declares_nothing_of_its_own():
    assert not DECLARATION.search((SHARED / 'report.js').read_text(encoding='utf-8'))
