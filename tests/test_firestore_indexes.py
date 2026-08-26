"""Every compound query has an index, and every index has a query.

This is the one class of Firestore bug that no test in this repo could catch,
because the emulator does not enforce it. A query that needs a composite index
runs perfectly against `firebase emulators:exec`, passes `tests/rules.test.js`,
passes `tests/flow.test.js`, and then fails in production with
`FAILED_PRECONDITION: The query requires an index` the first time a real person
opens the page. Green locally, broken only where it matters — the same shape as
the two orphan checks next to this file, and a demo-day footgun.

The rule it applies is Firestore's own, narrowed to what this codebase can
write. Single-field indexes are created automatically, for collection and
collection-group scope alike, so a query that constrains **one** field needs
nothing declared. A query that constrains **two or more** distinct fields —
counting equality filters, range filters and `orderBy` together — needs a
composite index that names exactly those fields, with the equality fields ahead
of the ordered one and the direction matching.

It reads in both directions, for the reason `PENDING_BY_DESIGN` and
`STRANDED_BY_DESIGN` already spell out: an uncovered query is a finding, and a
declared index nothing queries is a stale entry that has to come out. The second
direction is not hypothetical. It is what found the `matches` index, declared in
the first Firebase commit and never used by anything, because `listMatches`
reads the whole subcollection and every `finalized` filter in the app is a
client-side `.filter()`.

The `fieldOverrides` are checked the other way round. An override with
`"indexes": []` switches indexing **off** for a field, which makes any query
that touches it fail — in production only, again. So each disabled field is
asserted to be one nothing queries.

Scope: this walks the same module directories `stamp_version.py` stamps, which
is the whole browser surface as long as two neighbouring gates hold —
`test_version_stamp.py` pins `MODULE_DIRS` against what is on disk and pins that
no page carries an inline `<script type="module">` body. The only other code
that talks to Firestore is `cv/publish.py`, on the Admin SDK, and it is asserted
here to build no queries at all rather than left to be remembered.
"""

from __future__ import annotations

import json
import re
import unittest
from dataclasses import dataclass
from pathlib import Path

import stamp_version

REPO = Path(__file__).resolve().parent.parent
MODULE_DIRS = stamp_version.MODULE_DIRS
INDEXES = REPO / 'firestore.indexes.json'
ADMIN = REPO / 'cv' / 'publish.py'

# `in` and `array-contains-any` are disjunctions and `!=` is a range; all of
# them constrain a field the same way for the purpose of counting dimensions.
# Only `==` lets Firestore pin a field to a single value.
EQUALITY_OPS = {'=='}

QUERY = re.compile(r'\bquery\s*\(')
GROUP = re.compile(r"""\bcollectionGroup\s*\(\s*\w+\s*,\s*['"]([^'"]+)['"]""")
PLAIN = re.compile(r"""\bcollection\s*\(([^()]*)\)""")
LITERAL = re.compile(r"""['"]([^'"]+)['"]""")
WHERE = re.compile(
    r"""\bwhere\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]""")
ORDER_BY = re.compile(
    r"""\borderBy\s*\(\s*['"]([^'"]+)['"]\s*(?:,\s*['"](asc|desc)['"])?""")


@dataclass(frozen=True)
class Query:
    """One `query(...)` call, reduced to what decides whether it needs an index."""

    site: str
    group: str
    scope: str
    equality: tuple[str, ...]
    ranges: tuple[str, ...]
    order: tuple[tuple[str, str], ...]

    @property
    def fields(self) -> tuple[str, ...]:
        seen = []
        for field in self.equality + self.ranges + tuple(
                f for f, _d in self.order):
            if field not in seen:
                seen.append(field)
        return tuple(seen)

    @property
    def needs_index(self) -> bool:
        return len(self.fields) > 1


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def module_paths() -> list[Path]:
    found = []
    for directory in MODULE_DIRS:
        found.extend(sorted((REPO / directory).glob('*.js')))
    return found


def call_bodies(source: str) -> list[tuple[int, str]]:
    """The argument text of every `query(...)`, with its 1-based line number."""
    found = []
    for match in QUERY.finditer(source):
        depth, i = 0, match.end() - 1
        while i < len(source):
            if source[i] == '(':
                depth += 1
            elif source[i] == ')':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        found.append((source.count('\n', 0, match.start()) + 1,
                      source[match.end():i]))
    return found


def parse(body: str, site: str) -> Query | None:
    """One query call as a Query, or None when it names no collection."""
    group_match = GROUP.search(body)
    if group_match:
        group, scope = group_match.group(1), 'COLLECTION_GROUP'
    else:
        plain = PLAIN.search(body)
        if not plain:
            return None
        # collection(db, 'teams', teamId, 'matches') — path segments alternate
        # between literals and variables, and the collection being queried is
        # the last literal, whatever came before it.
        literals = LITERAL.findall(plain.group(1))
        if not literals:
            return None
        group, scope = literals[-1], 'COLLECTION'

    equality, ranges = [], []
    for field, op in WHERE.findall(body):
        (equality if op in EQUALITY_OPS else ranges).append(field)
    order = tuple((field, direction or 'asc')
                  for field, direction in ORDER_BY.findall(body))
    return Query(site, group, scope, tuple(equality), tuple(ranges), order)


def queries() -> list[Query]:
    found = []
    for path in module_paths():
        source = _read(path)
        for line, body in call_bodies(source):
            parsed = parse(body, f'{_rel(path)}:{line}')
            if parsed is not None:
                found.append(parsed)
    return found


def declared() -> list[dict]:
    return json.loads(_read(INDEXES)).get('indexes', [])


def overrides() -> list[dict]:
    return json.loads(_read(INDEXES)).get('fieldOverrides', [])


def covers(index: dict, query: Query) -> bool:
    """Does this declared index serve this query?

    Field *set* must match exactly — a composite index with a field the query
    does not constrain will not be used, and one missing a field cannot be. The
    ordered field has to sit last and point the same way; the equality fields
    ahead of it may be in any order.
    """
    if index.get('collectionGroup') != query.group:
        return False
    if index.get('queryScope') != query.scope:
        return False
    fields = [f['fieldPath'] for f in index.get('fields', [])]
    if sorted(fields) != sorted(query.fields):
        return False
    if not query.order:
        return True
    field, direction = query.order[-1]
    want = 'DESCENDING' if direction == 'desc' else 'ASCENDING'
    last = index['fields'][-1]
    return last['fieldPath'] == field and last.get('order') == want


class TestEveryCompoundQueryHasAnIndex(unittest.TestCase):
    def test_no_query_would_fail_only_in_production(self):
        uncovered = [
            f'{q.site} {q.scope} {q.group} {list(q.fields)}'
            for q in queries()
            if q.needs_index and not any(covers(i, q) for i in declared())]
        self.assertEqual(
            uncovered, [],
            'this query constrains more than one field and no composite index '
            'in firestore.indexes.json matches it — the emulator will run it '
            'and production will refuse it')

    def test_no_index_is_declared_for_a_query_nobody_writes(self):
        found = queries()
        stale = [f"{i.get('queryScope')} {i.get('collectionGroup')} "
                 f"{[f['fieldPath'] for f in i.get('fields', [])]}"
                 for i in declared()
                 if not any(covers(i, q) for q in found)]
        self.assertEqual(
            stale, [],
            'no query uses this index — delete it, or the file stops being a '
            'description of what the app does')

    def test_a_disabled_field_is_one_nothing_queries(self):
        # An override with "indexes": [] turns indexing off for that field.
        # Querying it then fails, and only in production.
        constrained = {(q.group, field) for q in queries()
                       for field in q.fields}
        for override in overrides():
            if override.get('indexes') != []:
                continue
            key = (override['collectionGroup'], override['fieldPath'])
            self.assertNotIn(
                key, constrained,
                f'{key[0]}.{key[1]} has indexing disabled but is queried')

    def test_this_check_is_looking_at_something(self):
        # The anti-vacuum guard. A paren matcher that truncated, or a regex
        # that matched nothing, would leave every assertion above trivially
        # true. So pin the one compound query the app actually makes, by shape
        # and not just by count — the player portal's report list, which is the
        # reason the surviving index exists.
        reports = [q for q in queries()
                   if q.group == 'playerReports']
        self.assertEqual(len(reports), 1, 'myReports was not found')
        found = reports[0]
        self.assertEqual(found.scope, 'COLLECTION_GROUP')
        self.assertEqual(sorted(found.equality), ['linkedUid', 'published'])
        self.assertEqual(found.order, (('matchDate', 'desc'),))
        self.assertTrue(found.needs_index)
        self.assertTrue(any(covers(i, found) for i in declared()))


class TestTheScannerReachesEverything(unittest.TestCase):
    def test_the_admin_sdk_still_builds_no_queries(self):
        # cv/publish.py is the only Firestore code outside the browser modules,
        # and it writes rather than reads. If that ever changes, its queries
        # need the same check and this scanner does not see Python.
        source = _read(ADMIN)
        for construct in ('.where(', '.order_by(', 'collection_group('):
            self.assertNotIn(
                construct, source,
                f'{_rel(ADMIN)} builds queries now — teach this test to read it')

    def test_every_query_names_a_collection(self):
        # parse() returns None when it cannot find one, which would silently
        # drop that query out of every check above.
        unparsed = []
        for path in module_paths():
            source = _read(path)
            for line, body in call_bodies(source):
                if parse(body, '') is None:
                    unparsed.append(f'{_rel(path)}:{line}')
        self.assertEqual(unparsed, [])

    def test_the_index_file_is_shaped_the_way_this_test_reads_it(self):
        for index in declared():
            self.assertIn(index.get('queryScope'),
                          ('COLLECTION', 'COLLECTION_GROUP'))
            self.assertTrue(index.get('collectionGroup'))
            self.assertGreater(len(index.get('fields', [])), 1,
                               'a single-field index is created automatically')
            for field in index['fields']:
                self.assertIn(field.get('order'),
                              ('ASCENDING', 'DESCENDING'), field)


class TestTheRuleItself(unittest.TestCase):
    """The classifier, against queries this app does not currently write. An
    empty finding is only worth something if a non-empty one is reachable."""

    def _parse(self, body: str) -> Query:
        found = parse(body, 'synthetic')
        assert found is not None
        return found

    def test_one_equality_filter_needs_nothing(self):
        found = self._parse(
            "collection(db, 'teams'), where('coachUid', '==', uid)")
        self.assertEqual(found.fields, ('coachUid',))
        self.assertFalse(found.needs_index)

    def test_a_range_on_the_field_it_orders_by_needs_nothing(self):
        found = self._parse(
            "collection(db, 'teams'), where('date', '>=', from), "
            "orderBy('date', 'desc')")
        self.assertEqual(found.fields, ('date',))
        self.assertFalse(found.needs_index)

    def test_two_equality_filters_need_an_index(self):
        found = self._parse(
            "collection(db, 'teams'), where('a', '==', 1), where('b', '==', 2)")
        self.assertTrue(found.needs_index)
        self.assertEqual(declared_covering(found), [])

    def test_an_order_on_another_field_needs_an_index(self):
        found = self._parse(
            "collection(db, 'teams'), where('a', '==', 1), orderBy('b')")
        self.assertTrue(found.needs_index)
        self.assertEqual(found.order, (('b', 'asc'),))

    def test_a_membership_filter_is_not_an_equality(self):
        # `in` matches several values, so Firestore cannot use it to pin the
        # field the way `==` does.
        found = self._parse(
            "collection(db, 'teams'), where('status', 'in', ['a', 'b'])")
        self.assertEqual(found.equality, ())
        self.assertEqual(found.ranges, ('status',))

    def test_the_last_path_segment_is_the_collection(self):
        found = self._parse(
            "collection(db, 'teams', teamId, 'matches', matchId, 'log'), "
            "orderBy('seq')")
        self.assertEqual(found.group, 'log')
        self.assertEqual(found.scope, 'COLLECTION')

    def test_direction_has_to_match(self):
        index = {'collectionGroup': 'r', 'queryScope': 'COLLECTION',
                 'fields': [{'fieldPath': 'a', 'order': 'ASCENDING'},
                            {'fieldPath': 'b', 'order': 'ASCENDING'}]}
        ascending = Query('x', 'r', 'COLLECTION', ('a',), (), (('b', 'asc'),))
        descending = Query('x', 'r', 'COLLECTION', ('a',), (), (('b', 'desc'),))
        self.assertTrue(covers(index, ascending))
        self.assertFalse(covers(index, descending))

    def test_an_extra_field_in_the_index_does_not_cover_the_query(self):
        index = {'collectionGroup': 'r', 'queryScope': 'COLLECTION',
                 'fields': [{'fieldPath': 'a', 'order': 'ASCENDING'},
                            {'fieldPath': 'c', 'order': 'ASCENDING'},
                            {'fieldPath': 'b', 'order': 'ASCENDING'}]}
        query = Query('x', 'r', 'COLLECTION', ('a',), (), (('b', 'asc'),))
        self.assertFalse(covers(index, query))

    def test_scope_has_to_match(self):
        index = {'collectionGroup': 'r', 'queryScope': 'COLLECTION',
                 'fields': [{'fieldPath': 'a', 'order': 'ASCENDING'},
                            {'fieldPath': 'b', 'order': 'ASCENDING'}]}
        query = Query('x', 'r', 'COLLECTION_GROUP', ('a',), (),
                      (('b', 'asc'),))
        self.assertFalse(covers(index, query))


def declared_covering(query: Query) -> list[dict]:
    return [i for i in declared() if covers(i, query)]


if __name__ == '__main__':
    unittest.main()
