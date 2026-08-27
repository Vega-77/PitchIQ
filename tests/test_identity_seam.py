# -*- coding: utf-8 -*-
"""Every figure on a published cluster or track row reaches a page.

`cvStats/identity` is the largest document this project publishes and, until
this file, the least checked. `tests/smoke.test.js` walks the keys of each
payload builder and asks whether some page reads each one; for this builder it
sees three names -- `clusters`, `tracks`, `playerByCluster` -- because those are
the only string literals in the function. The twenty-two figures on every track
row and the seven on every cluster row are inside lists it forwards verbatim out
of the report JSON, and the smoke gate has never had an opinion about any of
them.

It found six that reached nothing, which is what makes this file worth its
length. In order of how badly each one would have aged:

  * `PlayerCluster.heatmap` was declared for two years, published on every
    cluster of every run, and never once assigned. It was also armed:
    `identity_payload` flattens the *track* copy of a heatmap because Firestore
    refuses nested arrays outright, and never flattened this one. The first
    person to fill it in would have broken the entire publish, not one column.
    Deleted at the source.
  * `minutes_tracked` on the cluster row and `team` and `track_ids` on the track
    row are the same values twice in one document -- `report_json.track_stats`
    seeds every `TrackStats` straight off its `PlayerCluster` and neither is
    touched again. The copies pages actually read are on the other row.
  * `pass_accuracy` is a quotient of two fields published beside it, and
    recomputing it after merging two fragments is the only correct way to get
    it. Exactly the call already made for `cvPassAccuracy`.
  * `top_acceleration_ms2` was the one worth keeping: a real figure whose
    companion count was already on both screens. Wired through to the coach and
    player pages rather than deleted.

The first five are stripped at the seam by `DROPPED_TRACK_FIELDS` and
`DROPPED_CLUSTER_FIELDS`, not removed from the report JSON, and the difference
matters. The report JSON is a document a person reads end to end, where a
repeated figure is a convenience. This one is read by a browser that already
holds the other copy in the same payload and pays for every byte on every page
load.

Binding a field to its object needs eight shapes here. Five are inherited from
`tests/test_events_seam.py` -- alias hops, `for`-of bindings, callback
receivers, argument-to-parameter across a call, and bag destructuring. Three are
new, and without them seventeen of the twenty-two track figures look dead:

    const byId = new Map((tracks || []).map((t) => [String(t.cluster_id), t]));
    const track = byId.get(String(clusterId));       # 6 a Map of the rows

    for (const key of SUMMED) { acc[key] += track[key]; }   # 7 a name list

    for (const c of [...clusters].sort(byMinutes)) c.team;  # 8 a peel

Shape seven is the reason the smoke gate could never have caught this. Its
reader set is property accesses, and `assets/report.js` reads fifteen of these
figures through `track[key]` over a module-level array of names. No `.field`
appears anywhere.

**Follow the value, not the thing that holds it.** Three shapes look like an
alias to a regex and are not, and following any of them makes every field of
every derived object arrive as a published figure:

    const rows = shotLedger(events, review);     // a new object of its own
    render({ events, quality, review });         // a bag containing it
    const d = review?.byEvent[event.id];         // indexed BY it, not part of it

The peel in shape eight is the exception that proves the rule: `.sort`, `.slice`
and `.filter` put back the very elements they were handed, so following them
credits the same rows and not new ones.

Every guard below was measured by switching it off before it was pinned -- a pin
for a guard that cannot fire is the same mistake as a gate that cannot fail, one
level down. Ten of the eleven change a verdict and each has a test. The
eleventh, comment stripping, currently changes nothing, and is kept without one:
it exists to stop a commented-out read counting as a reader, and that is the
silent direction. A scanner that misses a reader makes a live field look dead
and this file fails loudly; a scanner that invents one makes a dead field look
live and this file says nothing. Guards against the second kind are worth
keeping inert, and are deliberately left untested so that nobody has to
manufacture a fake reader in a comment to keep a test alive.
"""
import re
from pathlib import Path

import pytest

# `from tests.conftest import ...` does not work here -- a `tests` package
# inside the virtualenv shadows this one.
ROOT = Path(__file__).resolve().parents[1]
Q3 = chr(34) * 3

SERVED = ['assets', 'coach', 'player', 'halftime', 'live-tagging',
          'calibrate', 'xg-sandbox']
SKIP = {'sample-report.js'}

BUILTIN = {
    'length', 'map', 'filter', 'forEach', 'flatMap', 'find', 'findIndex',
    'some', 'every', 'sort', 'reduce', 'slice', 'push', 'join', 'includes',
    'concat', 'indexOf', 'reverse', 'keys', 'values', 'entries',
}
ITERATORS = ('map', 'filter', 'forEach', 'flatMap', 'find', 'findIndex',
             'some', 'every', 'sort', 'reduce')

FUNC = re.compile(r'^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(', re.M)
DECL = re.compile(r'\b(?:const|let|var)\s+(\w+)\s*=\s*([^;\n]*)')
DESTRUCT = re.compile(r'\b(?:const|let|var)\s*\{([^}]*)\}\s*=\s*([^;\n]*)')
FOR_OF = re.compile(r'\bfor\s*\(\s*(?:const|let|var)\s+(\w+)\s+of\s+([^)]*)\)')
CALLBACK = re.compile(
    r'\.\s*(?:%s)\s*\(\s*\(?\s*(\w+)' % '|'.join(ITERATORS))
CALL = re.compile(r'\b(\w+)\s*\(')
LINE_COMMENT = re.compile(r'//[^\n]*')
BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)
DEFAULTED = re.compile(r'\|\||\?\?')
NEWMAP = re.compile(r'^\s*new\s+Map\s*\(')
GETTER = re.compile(r'^\s*(\w+)\s*\.\s*get\s*\(')
INDEXED = re.compile(r'\b(\w+)\s*\[\s*(\w+)\s*\]')
NAMELIST = re.compile(
    r'\bconst\s+([A-Z][A-Z0-9_]*)\s*=\s*\[([^\]]*)\]', re.S)
MAX_ROUNDS = 8


def _sources(strip=True):
    out = {}
    for name in SERVED:
        folder = ROOT / name
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob('*.js')):
            if path.name in SKIP:
                continue
            text = path.read_text(encoding='utf-8')
            if strip:
                text = LINE_COMMENT.sub('', BLOCK_COMMENT.sub('', text))
            out['%s/%s' % (name, path.name)] = text
    return out


def _chunks(text):
    marks = [(m.start(), m.group(1)) for m in FUNC.finditer(text)]
    out = []
    for i, (start, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        out.append((name, text[start:end]))
    return out


def _split_top(raw):
    out, depth, cur = [], 0, ''
    for ch in raw:
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == ',' and depth == 0:
            out.append(cur)
            cur = ''
        else:
            cur += ch
    out.append(cur)
    return out


def _params(body):
    open_at = body.index('(')
    depth, i = 0, open_at
    while i < len(body):
        if body[i] in '([{':
            depth += 1
        elif body[i] in ')]}':
            depth -= 1
            if depth == 0:
                break
        i += 1
    names = []
    for piece in _split_top(body[open_at + 1:i]):
        piece = piece.split('=')[0].strip()
        names.append(piece if re.fullmatch(r'\w+', piece) else None)
    return names


def _args(body, at):
    depth, i, cur = 0, at, ''
    while i < len(body):
        ch = body[i]
        if ch in '([{':
            depth += 1
            if depth == 1:
                i += 1
                continue
        elif ch in ')]}':
            depth -= 1
            if depth == 0:
                return _split_top(cur)
        cur += ch
        i += 1
    return []


def _mentions(expr, names, root):
    if root is not None and root.search(expr):
        return True
    return any(re.search(r'\b%s\b' % re.escape(n), expr) for n in names)


def _computed(head):
    depth = 0
    for ch in head:
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
        elif depth > 0 and (ch.isalpha() or ch in '_$'):
            return True
    return False


PRESERVING = ('slice', 'filter', 'sort', 'reverse', 'concat', 'flat')
SPREAD = re.compile(r'^\s*\[\s*\.\.\.\s*(.+?)\s*\]\s*$')


def _head(expr):
    """Everything left of a top-level `||` or `??` default."""
    depth = 0
    for i, ch in enumerate(expr):
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif depth == 0 and expr[i:i + 2] in ('||', '??'):
            return expr[:i]
    return expr


def _peel(expr, on=True):
    """Strip the list operations that hand back the very same rows.

    `[...clusters].sort(...)` is not a new object the way `shotLedger(...)` is:
    spread, sort, filter, slice, reverse, concat and flat all put back the
    elements they were given. Everything else that ends in a call is refused,
    because following a call result is how a scan starts inventing figures.
    """
    if not on:
        return expr
    while True:
        e = expr.strip()
        m = SPREAD.match(e)
        if m:
            expr = m.group(1)
            continue
        if e.endswith(')'):
            depth, at = 0, -1
            for i in range(len(e) - 1, -1, -1):
                if e[i] == ')':
                    depth += 1
                elif e[i] == '(':
                    depth -= 1
                    if depth == 0:
                        at = i
                        break
            if at < 0:
                return expr
            got = re.search(r'[.]\s*(\w+)\s*$', e[:at])
            if got and got.group(1) in PRESERVING:
                expr = e[:at][:got.start()]
                continue
        return expr


def _alias(expr, names, root, on=True, rowmaps=(), peel=True):
    if not on:
        return _mentions(expr, names, root)
    head = _peel(_head(expr), peel)
    got = GETTER.match(head)
    if got and got.group(1) in rowmaps:
        return True
    if '(' in head or '{' in head or _computed(head):
        return False
    return _mentions(head, names, root)


def _receiver(body, dot):
    i = dot - 1
    while i >= 0 and body[i] in ' \t\n?.':
        i -= 1
    if i < 0:
        return None
    if body[i] == ')':
        depth, j = 0, i
        while j >= 0:
            if body[j] == ')':
                depth += 1
            elif body[j] == '(':
                depth -= 1
                if depth == 0:
                    break
            j -= 1
        if j <= 0:
            return None
        k = j - 1
        while k >= 0 and body[k] in ' \t\n':
            k -= 1
        if k >= 0 and (body[k].isalnum() or body[k] in '_$'):
            return None
        return body[j + 1:i]
    start = i
    while start >= 0 and (body[start].isalnum() or body[start] in '_$.?[]'):
        start -= 1
    return body[start + 1:i + 1]


def _namelists(files):
    """{CONST: [names]} for every module-level array of bare string literals."""
    out = {}
    for text in files.values():
        for name, raw in NAMELIST.findall(text):
            body = LINE_COMMENT.sub('', raw)
            if not body.strip():
                continue
            pieces = [p.strip() for p in body.split(',') if p.strip()]
            if all(re.fullmatch(r"'(\w+)'", p) for p in pieces):
                out.setdefault(name, []).extend(p[1:-1] for p in pieces)
    return out


def scan(root_src, *, cross=True, callbacks=True, hops=True, loops=True,
         aliases=True, chunked=True, strip_comments=True, builtins=True,
         maps=True, keylists=True, peel=True):
    root = re.compile(root_src)
    files = _sources(strip=strip_comments)
    lists = _namelists(files) if keylists else {}

    units = {}
    for rel, text in files.items():
        if chunked:
            for i, (name, body) in enumerate(_chunks(text)):
                units[(rel, i)] = (name, body)
        else:
            units[(rel, 0)] = ('<file>', text)

    by_name = {}
    for key, (name, _body) in units.items():
        by_name.setdefault(name, []).append(key)

    seeds = {key: set() for key in units}
    rowmaps = {key: set() for key in units}

    for _round in range(MAX_ROUNDS):
        grew = False
        for key, (_name, body) in units.items():
            names = set(seeds[key])
            held = set(rowmaps[key])
            if maps:
                for n, expr in DECL.findall(body):
                    if NEWMAP.match(expr) and _mentions(expr, names, root):
                        held.add(n)
            if hops:
                for n, expr in DECL.findall(body):
                    if _alias(expr, names, root, aliases, held, peel):
                        names.add(n)
                for group, expr in DESTRUCT.findall(body):
                    if _alias(expr, names, root, aliases, held, peel):
                        for piece in group.split(','):
                            piece = piece.split(':')[-1].strip()
                            if re.fullmatch(r'\w+', piece):
                                names.add(piece)
            if loops:
                for n, expr in FOR_OF.findall(body):
                    if _mentions(_peel(_head(expr), peel), names, root):
                        names.add(n)
            if callbacks:
                for m in CALLBACK.finditer(body):
                    head = (_receiver(body, m.start()) if aliases
                            else body[max(0, m.start() - 60):m.start() + 1])
                    if head and _mentions(head, names, root):
                        names.add(m.group(1))
            if names != seeds[key] or held != rowmaps[key]:
                seeds[key] = names
                rowmaps[key] = held
                grew = True

        if cross:
            for key, (_name, body) in units.items():
                names = seeds[key]
                for m in CALL.finditer(body):
                    callee = m.group(1)
                    if callee not in by_name or callee in BUILTIN:
                        continue
                    for i, expr in enumerate(_args(body, m.end() - 1)):
                        if not _alias(expr, names, root, aliases,
                                      rowmaps[key], peel):
                            continue
                        for target in by_name[callee]:
                            ps = _params(units[target][1])
                            if i < len(ps) and ps[i]:
                                if ps[i] not in seeds[target]:
                                    seeds[target].add(ps[i])
                                    grew = True
                for group, expr in DESTRUCT.findall(body):
                    call = re.match(r'\s*(?:await\s+)?(\w+)\s*\(', expr)
                    if not call or call.group(1) not in by_name:
                        continue
                    wanted = {p.split(':')[-1].strip()
                              for p in group.split(',')}
                    for source in by_name[call.group(1)]:
                        bag = units[source][1]
                        for k, v in re.findall(r'(\w+)\s*:\s*([^,\n]+)', bag):
                            if k in wanted and _mentions(v, seeds[source],
                                                         None):
                                if k not in seeds[key]:
                                    seeds[key].add(k)
                                    grew = True
        if not grew:
            break

    found = {}
    for key, (name, body) in units.items():
        where = '%s::%s' % (key[0], name)
        patterns = [root_src + r'\s*(?:\?\.|\.)\s*(\w+)\s*(\()?']
        for n in seeds[key]:
            patterns.append(
                r'\b%s\b\s*(?:\?\.|\.)\s*(\w+)\s*(\()?' % re.escape(n))
        for pattern in patterns:
            for m in re.finditer(pattern, body):
                field, called = m.group(1), m.group(2)
                if called:
                    continue
                if builtins and field in BUILTIN:
                    continue
                found.setdefault(field, set()).add(where)
        for n in seeds[key]:
            for group, expr in DESTRUCT.findall(body):
                if re.fullmatch(r'\s*%s\s*' % re.escape(n), expr):
                    for piece in group.split(','):
                        piece = piece.split(':')[0].strip()
                        if re.fullmatch(r'\w+', piece):
                            found.setdefault(piece, set()).add(where)
        if keylists:
            bound = {}
            for n, expr in FOR_OF.findall(body):
                bound.setdefault(n, []).append(expr)
            for holder, idx in INDEXED.findall(body):
                if holder not in seeds[key]:
                    continue
                for const in re.findall(r'\b([A-Z][A-Z0-9_]*)\b', ' '.join(bound.get(idx, ()))):
                    for field in lists.get(const, ()):
                        found.setdefault(field, set()).add(
                            '%s[%s]' % (where, const))
    return found


# ------------------------------------------------------------- producers

def payload_keys():
    text = (ROOT / 'cv' / 'publish.py').read_text(encoding='utf-8')
    body = text.split('def identity_payload(', 1)[1].split('\ndef ', 1)[0]
    return re.findall(r"^ {8}'(\w+)':", body, re.M)


def _to_json_keys(path, cls):
    text = (ROOT / path).read_text(encoding='utf-8')
    body = text.split('class %s:' % cls, 1)[1]
    body = re.split(r'\n(?:class|def|@) ?', body, maxsplit=1)[0]
    body = body.split('    def to_json(', 1)[1]
    return re.findall(r"^ {12}'(\w+)':", body, re.M)


def _stripped(*tuples):
    """The field names `identity_payload` takes back off a row on its way out.

    Read out of publish.py rather than restated here, so that a name added to
    one of those tuples cannot leave this probe measuring a document the seam
    stopped publishing.
    """
    pub = (ROOT / 'cv' / 'publish.py').read_text(encoding='utf-8')
    out = set()
    for name in tuples:
        body = re.search(r"%s = \(([^)]*)\)" % name, pub).group(1)
        out |= set(re.findall(r"'(\w+)'", body))
    return out


def track_keys():
    keys = _to_json_keys('cv/report_json.py', 'TrackStats')
    gone = _stripped('DROPPED_TRACK_FIELDS')
    return [k for k in keys if k not in gone]


def cluster_keys():
    keys = _to_json_keys('cv/identity.py', 'PlayerCluster')
    gone = _stripped('THUMB_FIELDS', 'DROPPED_CLUSTER_FIELDS')
    return [k for k in keys if k not in gone]


ROOTS = {
    'doc': r'\bidentity\b',
    'tracks': r'\bidentity\b\s*\??\.\s*tracks\b',
    'clusters': r'\bidentity\b\s*\??\.\s*clusters\b',
}


# --------------------------------------------------------------- the excuses

# Published, read by nothing, and correct to keep. One entry, and it has to
# earn its place every time this file runs: `TestTheExcusesDoNotRot` deletes it
# the moment a page starts reading it, and deletes it again if the field stops
# being published.
UNREAD_BY_DESIGN = {
    'playerByCluster': (
        'Provenance, not authority. It records which cluster a human had put '
        'which name to at the moment this run was published, so that a report '
        'read a season later can be understood. The live mapping lives on the '
        'match document and is the one every page reads, which is the whole '
        'reason this copy must never be read: two answers to "who is cluster '
        '7" and the wrong one is the older one.'
    ),
}

# The other direction. These two are read off a cluster and are not on the
# published cluster row, and both facts are deliberate: `identity_payload`
# strips them into a separate `thumbs` document so that deleting the pictures
# cannot take a statistic with it, and `db.js::withThumbs` puts them back on
# the way in. A page reading `cluster.thumb` is reading a real field of a real
# cluster -- just one that arrived by the other door.
READ_ELSEWHERE = {
    'thumb': 'rejoined from the thumbs document by assets/db.js::withThumbs',
    'thumb_height_px': 'ditto -- it travels with the picture it describes',
}


def readers(key, seen):
    return sorted(seen.get(key, ()))


def verdict(root, keys, **knobs):
    """(unread, ghosts) for one root expression under one set of guards."""
    found = scan(root, **knobs)
    unread = [k for k in keys if not found.get(k)]
    return unread, sorted(set(found) - set(keys))


@pytest.fixture(scope='module')
def doc_keys():
    return payload_keys()


@pytest.fixture(scope='module')
def tracks():
    return track_keys()


@pytest.fixture(scope='module')
def clusters():
    return cluster_keys()


@pytest.fixture(scope='module')
def seen_doc():
    return scan(ROOTS['doc'])


@pytest.fixture(scope='module')
def seen_tracks():
    return scan(ROOTS['tracks'])


@pytest.fixture(scope='module')
def seen_clusters():
    return scan(ROOTS['clusters'])


@pytest.fixture(scope='module')
def publish_source():
    return (ROOT / 'cv' / 'publish.py').read_text(encoding='utf-8')


class TestEveryPublishedFigureReachesAPage:
    """The finding direction. A failure here is a figure nobody can see."""

    def test_every_track_figure_is_read_off_a_track_row(self, tracks,
                                                        seen_tracks):
        orphans = [k for k in tracks if not readers(k, seen_tracks)]
        assert not orphans, (
            'These are published on every track of every run and no page reads '
            'them off a track: %s. Either wire one up or stop publishing it -- '
            'a figure that reaches nobody still costs a coach bandwidth on '
            'every page load. If it is genuinely wanted unread, add it to '
            'UNREAD_BY_DESIGN with a reason a stranger would accept.'
            % ', '.join(orphans)
        )

    def test_every_cluster_figure_is_read_off_a_cluster_row(self, clusters,
                                                            seen_clusters):
        orphans = [k for k in clusters if not readers(k, seen_clusters)]
        assert not orphans, (
            'Published on every cluster and read off none: %s' % ', '.join(orphans)
        )

    def test_every_document_key_is_read_or_excused(self, doc_keys, seen_doc):
        orphans = [k for k in doc_keys
                   if not readers(k, seen_doc) and k not in UNREAD_BY_DESIGN]
        assert not orphans, (
            'Top-level keys of the identity document that nothing reads: %s'
            % ', '.join(orphans)
        )


class TestNothingIsReadThatIsNotPublished:
    """The other direction, and the quieter one.

    A page reading a field the pipeline stopped publishing does not throw -- it
    gets `undefined`, renders a blank, and looks like a match where nothing
    happened. These tests are why the scan is narrow enough to be believed.
    """

    def test_no_page_reads_a_track_field_the_pipeline_does_not_publish(
            self, tracks, seen_tracks):
        ghosts = sorted(set(seen_tracks) - set(tracks))
        assert not ghosts, (
            'Read off a published track row and not published on one: %s. '
            'Either the field was dropped from `TrackStats.to_json` and a '
            'reader was left behind, or the scan has started following '
            'something that is not a track.' % ', '.join(ghosts)
        )

    def test_the_only_cluster_fields_read_and_not_published_are_the_pictures(
            self, clusters, seen_clusters):
        ghosts = set(seen_clusters) - set(clusters)
        assert ghosts == set(READ_ELSEWHERE), (
            'Expected exactly the two thumbnail fields, which arrive from the '
            'thumbs document rather than this one. Got: %s'
            % ', '.join(sorted(ghosts))
        )

    def test_nothing_else_is_read_off_the_identity_document(
            self, doc_keys, tracks, clusters, seen_doc):
        # The document-level root matches the bare name, so the row fields
        # reached through it show up here too. Anything outside that union is
        # a field of something that is not this document.
        allowed = (set(doc_keys) | set(tracks) | set(clusters)
                   | set(READ_ELSEWHERE))
        stray = sorted(set(seen_doc) - allowed)
        assert not stray, (
            'Read off `identity` and not part of it at any level: %s'
            % ', '.join(stray)
        )


class TestTheExcusesDoNotRot:
    """An allowlist nobody may delete from is a graveyard, not a gate."""

    def test_no_stale_entries(self, seen_doc):
        rendered = [k for k in UNREAD_BY_DESIGN if readers(k, seen_doc)]
        assert not rendered, (
            'These are excused as unread and something now reads them: %s. '
            'Delete the excuse -- the field is covered by the ordinary check '
            'from here on.' % ', '.join(rendered)
        )

    def test_no_entries_for_fields_that_do_not_exist(self, doc_keys):
        gone = [k for k in UNREAD_BY_DESIGN if k not in doc_keys]
        assert not gone, (
            'Excused, and no longer published at all: %s' % ', '.join(gone)
        )

    def test_every_entry_gives_a_reason(self):
        for key, why in list(UNREAD_BY_DESIGN.items()) + list(
                READ_ELSEWHERE.items()):
            assert len(why) > 40, (
                '%s is excused with %r, which is a label and not a reason. The '
                'next person has to be able to disagree with it.' % (key, why)
            )

    def test_the_picture_fields_are_still_stripped_at_the_seam(
            self, publish_source, clusters):
        named = re.search(r"THUMB_FIELDS = \(([^)]*)\)", publish_source)
        assert named, 'THUMB_FIELDS is where the two sides of this agree.'
        assert set(re.findall(r"'(\w+)'", named.group(1))) == set(READ_ELSEWHERE)
        assert not set(READ_ELSEWHERE) & set(clusters), (
            'A field cannot be both stripped out of the cluster row and on it.'
        )


class TestThePremiseHolds:
    """If any of this stops being true the file above measures nothing."""

    def test_publish_writes_the_identity_document(self, publish_source):
        assert "IDENTITY_DOC = 'identity'" in publish_source
        assert 'document(IDENTITY_DOC).set(identity_payload(' in publish_source

    def test_the_browser_binds_that_document_to_the_handle(self):
        db = (ROOT / 'assets' / 'db.js').read_text(encoding='utf-8')
        assert 'export async function readCvStats' in db
        assert "getDoc(doc(db, ...base, 'identity'))" in db
        assert 'identity: data ?' in db

    def test_the_pages_reach_it_the_way_this_file_assumes(self):
        coach = (ROOT / 'coach' / 'coach.js').read_text(encoding='utf-8')
        assert 'cv?.identity?.tracks' in coach
        assert 'cv?.identity?.clusters' in coach

    def test_the_producer_parse_found_all_three_levels(self, doc_keys, tracks,
                                                       clusters):
        assert set(doc_keys) == {'clusters', 'tracks', 'playerByCluster'}
        assert len(tracks) > 15, (
            'Only %d track figures parsed out of `TrackStats.to_json`, which '
            'is too few to be the real list -- the parse has drifted off the '
            'class.' % len(tracks)
        )
        assert len(clusters) > 5, len(clusters)

    def test_the_dropped_figures_are_really_dropped(self, publish_source,
                                                    tracks, clusters):
        body = publish_source.split('def identity_payload(', 1)[1]
        body = body.split('\ndef ', 1)[0]
        # Look for the filter, not the name. The docstring names all three
        # and so does a comment further down, and prose strips nothing:
        # measured, with the track comprehension replaced by a plain copy
        # of the row, an `in body` check still passed on the mention alone.
        head, _, body = body.partition(Q3)
        _, _, body = body.partition(Q3)
        assert 'report_json' in head and body.strip(), (
            'identity_payload no longer opens with a docstring, so the '
            'split above is cutting the function somewhere else.'
        )
        body = re.sub(r'#[^\n]*', '', body)
        for name in ('THUMB_FIELDS', 'DROPPED_TRACK_FIELDS',
                     'DROPPED_CLUSTER_FIELDS'):
            assert 'not in %s' % name in body, (
                'Nothing in identity_payload takes %s off a row. The tuple '
                'can still be there, and the probe above reads the tuple, so '
                'this file would go on measuring a document narrower than '
                'the one actually being published.' % name
            )
        assert 'pass_accuracy' not in tracks
        assert 'minutes_tracked' not in clusters

    def test_the_scan_found_the_readers_it_should_have(self, seen_tracks,
                                                       seen_clusters):
        where = {w for v in seen_tracks.values() for w in v}
        assert any(w.startswith('assets/report.js') for w in where), (
            'cvStatsByPlayer is the only reader of the track rows in the repo. '
            'If the scan cannot see it, it can see nothing.'
        )
        where = {w for v in seen_clusters.values() for w in v}
        assert len({w.split('::')[0] for w in where}) >= 2, sorted(where)


class TestTheScannerCannotPassVacuously:
    """Every guard, switched off, must change a verdict.

    Measured before it was written: ten of the eleven do, and each has a test
    here saying which figures go dark or which strangers walk in. The eleventh
    is comment stripping, which changes nothing today and has no test on
    purpose -- see this module's docstring for why an inert guard against the
    silent direction is still worth keeping.
    """

    def test_a_scan_that_followed_nothing_would_find_nothing(self, tracks):
        unread, _ = verdict(r'\bnoSuchHandleAnywhere\b', tracks)
        assert unread == tracks, (
            'Seeded with a name that appears nowhere, the scan still reported '
            'readers. It is matching on something other than the seed.'
        )

    def test_the_hop_across_a_call_is_load_bearing(self, tracks, clusters):
        unread, _ = verdict(ROOTS['tracks'], tracks, cross=False)
        assert unread == tracks, unread
        unread, _ = verdict(ROOTS['clusters'], clusters, cross=False)
        assert 'team' in unread

    def test_the_callback_binding_is_load_bearing(self, tracks):
        unread, _ = verdict(ROOTS['tracks'], tracks, callbacks=False)
        assert 'cluster_id' in unread

    def test_the_alias_hop_is_load_bearing(self, tracks):
        unread, _ = verdict(ROOTS['tracks'], tracks, hops=False)
        assert 'distance_m' in unread

    def test_the_loop_binding_is_load_bearing(self, clusters):
        unread, _ = verdict(ROOTS['clusters'], clusters, loops=False)
        assert 'team' in unread

    def test_refusing_to_follow_containers_is_load_bearing(self, tracks):
        _, ghosts = verdict(ROOTS['tracks'], tracks, aliases=False)
        assert 'byEvent' in ghosts, (
            'With the alias check switched off the scan should follow calls '
            'and index expressions into objects that are not tracks, and '
            'report their fields as published figures. It did not, which means '
            'the check is not what is holding the scan in.'
        )

    def test_the_per_function_split_is_load_bearing(self, tracks):
        unread, _ = verdict(ROOTS['tracks'], tracks, chunked=False)
        assert 'distance_m' in unread

    def test_the_builtin_exclusion_is_load_bearing(self, clusters):
        _, ghosts = verdict(ROOTS['clusters'], clusters, builtins=False)
        assert 'length' in ghosts

    def test_the_map_of_rows_is_load_bearing(self, tracks):
        unread, _ = verdict(ROOTS['tracks'], tracks, maps=False)
        assert 'distance_m' in unread, (
            'Every track figure but the id is reached through a Map keyed by '
            'cluster_id. Without that shape this whole document looks dead.'
        )

    def test_the_constant_name_list_is_load_bearing(self, tracks):
        unread, _ = verdict(ROOTS['tracks'], tracks, keylists=False)
        assert 'touches' in unread
        assert 'top_speed_kmh' in unread
        assert 'shot_map' not in unread, (
            'shot_map is read as a plain property, so it must survive this '
            'guard being switched off -- otherwise the guard is not doing the '
            'one specific thing it claims to do.'
        )

    def test_the_element_preserving_peel_is_load_bearing(self, clusters):
        unread, _ = verdict(ROOTS['clusters'], clusters, peel=False)
        assert 'colour' in unread
