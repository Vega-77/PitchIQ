"""Every figure on a published event reaches a page, off that event.

`tests/smoke.test.js` walks the keys of all five payload builders and checks
that some page reads each one. It also says, in its own docstring, exactly
where that check goes thin:

    What it cannot tell you is which object a `.key` was read off ... short
    generic names -- `id`, `type`, `team` on an event -- are effectively
    unchecked, since every one of them is a property of something else
    somewhere.

`events_payload` is made almost entirely of those names. In the served
JavaScript `id` is read off something 232 times, `type` 104, `team` 88, and
almost none of those somethings is a published event. So the smoke gate ticks
all twelve row fields off against strangers, and would keep ticking them off
after the last real reader was deleted.

This file binds the field to the object. It starts from the one handle the
document lands under -- `state.match.cvEvents`, written by `readCvEvents` --
and follows the *value* to wherever a field is read off it.

The other four seam gates (`quality`, `teams`, `keepers`, `reconciliation`)
could each stay inside one function, because a distinctive block name sat in
the same scope as the field read. This document has no such name: the list is
handed across function boundaries as a bare positional argument.

    shotLedger(state.match?.cvEvents?.events || [], state.match?.cvReview)

By the time a row field is read, the only thing in scope is `events`, then
`event`. So the scan is interprocedural, and it needs five shapes:

    const events = state.match?.cvEvents?.events || [];   # 1 alias
    for (const event of events) event.team;               # 2 loop binding
    (events || []).map((event) => event.id);              # 3 callback binding
    shotLedger(events, review);                           # 4 arg -> param
    const { events } = passingSource();                   # 5 bag -> destructure

Shape 5 exists because `coach/coach.js` builds three options bags -- from
`passingSource`, `shapeSource` and `subsSource` -- each returning an object
literal whose `events:` key holds the published list. Without it the passing
network, which is the only reader of `startM` and `receiverTrackId` in the
repo, is invisible to the file checking that they get read.

**Follow the value, not the container.** Three shapes look like an alias to a
regex and are not, and following any of them turns one seed into fifty:

    const rows = shotLedger(events, review);      // a new object, own shape
    render({ events, quality, review });          // a bag holding it
    const d = review?.byEvent[event.id];          // indexed BY it, not part

The first draft did follow all three, and reported fifty figures the pipeline
never produced -- among them `calibrationErrorM`, `byEvent` and `cardColor`,
each a real field of some real object that has nothing to do with an event.
A scan that loose cannot say anything about a name as common as `id`, which is
the entire reason this file exists.

The audit found no orphans: fifteen of the seventeen published figures are read
off a published event, and the two that are not were already excused in
`tests/smoke.test.js` for reasons that survive being asked again. That is the
answer, not a disappointment -- the value here is that the next deletion cannot
pass by accident.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Where the document lands in the browser. `readCvEvents` fetches
# `cvStats/events` and `coach.js` binds it into `state.match` under this name;
# every path below starts here.
HANDLE = 'cvEvents'

# The served JavaScript. `sample-report.js` is a fixture of hand-written
# numbers for the demo page, not a reader of anything published.
SERVED = ['assets', 'coach', 'player', 'halftime', 'live-tagging',
          'calibrate', 'xg-sandbox']
SKIP = {'sample-report.js'}

# Array methods, so `events.length` does not arrive as a figure the pipeline
# never made. Also the method names the callback rule keys off.
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

# A fixpoint: an alias can feed a call that seeds a parameter that becomes an
# alias. Six rounds is well past what this repo needs; the loop exits early
# when nothing grew.
MAX_ROUNDS = 8

# Both are already excused in `tests/smoke.test.js`, which reaches them by
# name. The reasons are repeated rather than referenced because this file
# checks a different claim -- that nothing reads them off an event -- and an
# excuse that has to be looked up somewhere else stops being argued with.
UNREAD_BY_DESIGN = {
    'schemaVersion':
        'provenance, and deliberately inert: nothing in the browser may '
        'branch on it, because a page that changes behaviour by schema '
        'version is a page that has to be rewritten every time the pipeline '
        'learns something new',
    'droppedBelowConfidence':
        'the confidence floor a truncated list was cut at -- a tuning figure '
        'for whoever is training the detector. Every note that mentions the '
        'trim says it in words instead, because `confidence` is a '
        'model-internal scale no page explains, so a coach reading '
        '"dropped below 0.45" learns nothing they can act on',
}


def _sources(strip: bool = True) -> dict[str, str]:
    """{path: code} for every served module, prose removed by default.

    A field named only in a comment must not count as read -- an orphan
    explained away by the sentence explaining it.
    """
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


def _chunks(text: str) -> list[tuple[str, str]]:
    """(function name, body) for every top-level `function` in a file.

    Without this, a name bound in one function claims every field read off the
    same name anywhere else in a four-thousand-line file.
    """
    marks = [(m.start(), m.group(1)) for m in FUNC.finditer(text)]
    out = []
    for i, (start, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        out.append((name, text[start:end]))
    return out


def _split_top(raw: str) -> list[str]:
    """Comma-split at bracket depth zero."""
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


def _params(body: str) -> list[str | None]:
    """Parameter names, positionally. A destructured slot yields None."""
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


def _args(body: str, at: int) -> list[str]:
    """Top-level argument expressions of the call whose `(` is at `at`."""
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


def _mentions(expr: str, names) -> bool:
    return any(re.search(r'\b%s\b' % re.escape(n), expr) for n in names)


def _computed(head: str) -> bool:
    """True for `byEvent[event.id]` -- indexed *by* a seed, not part of it."""
    depth = 0
    for ch in head:
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
        elif depth > 0 and (ch.isalpha() or ch in '_$'):
            return True
    return False


def _alias(expr: str, names, on: bool = True) -> bool:
    """True when `expr` *is* the seeded value, not something holding it."""
    if not on:
        return _mentions(expr, names)
    head = DEFAULTED.split(expr)[0]
    if '(' in head or '{' in head or _computed(head):
        return False
    return _mentions(head, names)


def _receiver(body: str, dot: int) -> str | None:
    """What the `.map(` at `dot` is called on, or None if it is a call result.

    `(events || []).map(...)` is the list; `reviewItems().filter(...)` is a
    different list entirely. Looking back a fixed number of characters instead
    conflates them, and seeds the callback of anything that happens to sit
    below a line mentioning the handle.
    """
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
            return None       # an identifier before `(` -- a call
        return body[j + 1:i]  # a grouping -- `(events || [])`
    start = i
    while start >= 0 and (body[start].isalnum() or body[start] in '_$.?[]'):
        start -= 1
    return body[start + 1:i + 1]


def _scan(*, cross=True, callbacks=True, hops=True, loops=True, aliases=True,
          chunked=True, strip_comments=True, builtins=True):
    """{field: {where it is read}} for everything reached from the handle."""
    files = _sources(strip=strip_comments)

    units = {}
    for rel, text in files.items():
        if chunked:
            for i, (name, body) in enumerate(_chunks(text)):
                units[(rel, i)] = (name, body)
        else:
            units[(rel, 0)] = ('<file>', text)

    by_name: dict[str, list] = {}
    for key, (name, _body) in units.items():
        by_name.setdefault(name, []).append(key)

    seeds = {key: {HANDLE} for key in units}

    for _round in range(MAX_ROUNDS):
        grew = False
        for key, (_name, body) in units.items():
            names = set(seeds[key])
            if hops:
                for n, expr in DECL.findall(body):
                    if _alias(expr, names, aliases):
                        names.add(n)
                for group, expr in DESTRUCT.findall(body):
                    if _alias(expr, names, aliases):
                        for piece in group.split(','):
                            piece = piece.split(':')[-1].strip()
                            if re.fullmatch(r'\w+', piece):
                                names.add(piece)
            if loops:
                for n, expr in FOR_OF.findall(body):
                    if _mentions(expr, names):
                        names.add(n)
            if callbacks:
                for m in CALLBACK.finditer(body):
                    head = (_receiver(body, m.start()) if aliases
                            else body[max(0, m.start() - 60):m.start() + 1])
                    if head and _mentions(head, names):
                        names.add(m.group(1))
            if names != seeds[key]:
                seeds[key] = names
                grew = True

        if cross:
            for key, (_name, body) in units.items():
                names = seeds[key]
                for m in CALL.finditer(body):
                    callee = m.group(1)
                    if callee not in by_name or callee in BUILTIN:
                        continue
                    for i, expr in enumerate(_args(body, m.end() - 1)):
                        if not _alias(expr, names, aliases):
                            continue
                        for target in by_name[callee]:
                            ps = _params(units[target][1])
                            if i < len(ps) and ps[i]:
                                if ps[i] not in seeds[target]:
                                    seeds[target].add(ps[i])
                                    grew = True
                # `const { events } = passingSource()` -- the bag rule, and
                # the only place a call result is followed, by key and never
                # wholesale.
                for group, expr in DESTRUCT.findall(body):
                    call = re.match(r'\s*(?:await\s+)?(\w+)\s*\(', expr)
                    if not call or call.group(1) not in by_name:
                        continue
                    wanted = {p.split(':')[-1].strip()
                              for p in group.split(',')}
                    for source in by_name[call.group(1)]:
                        bag = units[source][1]
                        for k, v in re.findall(r'(\w+)\s*:\s*([^,\n]+)', bag):
                            if k in wanted and _mentions(v, seeds[source]):
                                if k not in seeds[key]:
                                    seeds[key].add(k)
                                    grew = True
        if not grew:
            break

    found: dict[str, set] = {}
    for key, (name, body) in units.items():
        where = '%s::%s' % (key[0], name)
        for n in seeds[key]:
            # `?.` is the dot in an optional chain: requiring another one
            # after it matches nothing, which is how `cvEvents?.truncated`
            # first came back unread.
            pattern = r'\b%s\b\s*(?:\?\.|\.)\s*(\w+)\s*(\()?' % re.escape(n)
            for m in re.finditer(pattern, body):
                field, called = m.group(1), m.group(2)
                if called:
                    continue  # a method, not a figure
                if builtins and field in BUILTIN:
                    continue
                found.setdefault(field, set()).add(where)
            for group, expr in DESTRUCT.findall(body):
                if re.fullmatch(r'\s*%s\s*' % re.escape(n), expr):
                    for piece in group.split(','):
                        piece = piece.split(':')[0].strip()
                        if re.fullmatch(r'\w+', piece):
                            found.setdefault(piece, set()).add(where)
    return found


_CACHE: dict = {}


def read(**knobs):
    key = tuple(sorted(knobs.items()))
    if key not in _CACHE:
        _CACHE[key] = _scan(**knobs)
    return _CACHE[key]


def readers(field, found) -> set:
    return found.get(field, set())


def produced() -> dict[str, str]:
    """{field: which level of the document it sits on}.

    Two levels, told apart by indent: the document's own keys sit at eight
    spaces inside the returned dict, and a row's keys at sixteen, inside the
    list comprehension that builds each event.
    """
    text = (ROOT / 'cv' / 'publish.py').read_text(encoding='utf-8')
    body = text.split('def events_payload(', 1)[1]
    body = body.split('\ndef ', 1)[0]
    body = body.split('    return {', 1)[1]
    made = {}
    for key in re.findall(r"^ {8}'(\w+)':", body, re.M):
        made[key] = 'the document'
    for key in re.findall(r"^ {16}'(\w+)':", body, re.M):
        made[key] = 'each event'
    return made


@pytest.fixture(scope='module')
def made():
    return produced()


@pytest.fixture(scope='module')
def seen():
    return read()


class TestEverythingPublishedIsRead:
    def test_every_figure_is_read_off_an_event_or_excused(self, made, seen):
        orphans = {
            key: level for key, level in made.items()
            if not readers(key, seen) and key not in UNREAD_BY_DESIGN
        }
        assert orphans == {}, (
            'published on %s and read off nothing: %s. Either a page should '
            'be showing it, or it should stop being published, or it belongs '
            'in UNREAD_BY_DESIGN with a reason.'
            % (sorted(orphans.values()), sorted(orphans))
        )

    def test_nothing_reads_a_field_the_pipeline_never_produces(self, made,
                                                               seen):
        ghosts = sorted(k for k in seen if k not in made)
        assert ghosts == [], (
            'read off a published event, and never published: %s. Either the '
            'page is reading a field that is always undefined, or the scan '
            'followed something that is not an event.' % ghosts
        )

    def test_the_row_fields_are_all_reached(self, made, seen):
        # The point of the file. `tests/smoke.test.js` ticks these off against
        # any object anywhere; here each one has to be read off an event.
        rows = [k for k, level in made.items() if level == 'each event']
        assert len(rows) == 12
        assert [k for k in rows if not readers(k, seen)] == []


class TestTheExcusesDoNotRot:
    def test_no_stale_entries(self, seen):
        # A figure that started being rendered keeps its excuse forever, and
        # the list turns into a graveyard nobody dares touch.
        rendered = [k for k in UNREAD_BY_DESIGN if readers(k, seen)]
        assert rendered == [], (
            '%s now has a reader: %s. Drop the excuse.'
            % (rendered, {k: sorted(readers(k, seen)) for k in rendered})
        )

    def test_no_entries_for_fields_that_do_not_exist(self, made):
        gone = [k for k in UNREAD_BY_DESIGN if k not in made]
        assert gone == [], (
            '%s is excused and no longer published. Drop the excuse.' % gone
        )

    def test_every_entry_gives_a_reason(self):
        for key, why in UNREAD_BY_DESIGN.items():
            assert len(why.split()) >= 12, (
                '%r is excused with %d words. An excuse nobody can argue '
                'with is not an excuse.' % (key, len(why.split()))
            )


class TestThePremiseHolds:
    """If any of this stops being true the file above is checking nothing."""

    def test_publish_writes_the_events_document(self):
        text = (ROOT / 'cv' / 'publish.py').read_text(encoding='utf-8')
        assert "EVENTS_DOC = 'events'" in text
        assert 'events_payload(report_json)' in text

    def test_the_browser_binds_that_document_to_the_handle(self):
        db = (ROOT / 'assets' / 'db.js').read_text(encoding='utf-8')
        assert 'export async function readCvEvents(' in db
        assert "'cvStats', 'events'" in db
        coach = (ROOT / 'coach' / 'coach.js').read_text(encoding='utf-8')
        assert HANDLE in coach, (
            'the scan starts from `state.match.%s` and nothing binds it'
            % HANDLE
        )

    def test_the_producer_parse_found_both_levels(self, made):
        levels = sorted(set(made.values()))
        assert levels == ['each event', 'the document'], levels
        assert len(made) == 17, sorted(made)

    def test_the_scan_found_the_files_that_read_the_list(self, seen):
        where = {place.split('::')[0]
                 for places in seen.values() for place in places}
        # Four files, and each one matters: coach.js holds the handle,
        # report.js the shot log and the label export, passing.js the only
        # reader of `startM`, review.js the filter chips.
        for name in ('coach/coach.js', 'assets/report.js',
                     'assets/passing.js', 'coach/review.js'):
            assert name in where, '%s read nothing: %s' % (name, sorted(where))


class TestTheScannerCannotPassVacuously:
    """A scan that reached nothing would satisfy every assertion above.

    Seven of the eight knobs below were measured to change the answer on this
    repo, and each is pinned to the direction it changes it in. The eighth is
    not pinned, on purpose:

    * **`hops`**, the alias step, carries three readers today -- `reviewScore`
      for `id` and `type`, `deadBallCount` for `inPlay` -- and every one of
      those figures has another reader as well, so switching it off changes no
      verdict. It stays because it fails *loudly* when it is wrong: a scan
      that misses a reader reports an orphan somebody then goes looking for.
      It is deliberately not given a test, because a pin for a guard that
      cannot fire is the same mistake as a gate that cannot fail, one level
      down.
    """

    def test_following_containers_is_what_breaks_this(self, made):
        # The single most important guard here, and the one the first draft
        # got wrong. Follow call results, object literals and computed indexes
        # and the seed escapes into every derived object in the repo: fifty
        # figures the pipeline never made, including `calibrationErrorM`,
        # `byEvent` and `cardColor`. A scan that loose cannot say anything
        # about a name as common as `id`.
        loose = read(aliases=False)
        ghosts = [k for k in loose if k not in made]
        assert len(ghosts) > 20, len(ghosts)

    def test_the_hop_across_a_call_is_load_bearing(self, made):
        # `shotLedger(state.match?.cvEvents?.events || [], ...)`. Without the
        # arg-to-parameter step, eleven of the twelve row fields go orphaned
        # at once. The twelfth is `inPlay`, and it is the exception that
        # measures the rule: `coach/review.js::deadBallCount` is the only
        # function in the repo that names the handle and reads a row field in
        # the same four lines, so it is the only one a scan that cannot cross
        # a call boundary can still see.
        without = read(cross=False)
        rows = [k for k, level in made.items() if level == 'each event']
        assert [k for k in rows if readers(k, without)] == ['inPlay']

    def test_the_bag_is_load_bearing(self, seen):
        # `const { events } = passingSource()`. The passing network is the
        # only reader of these two in the repo, and it is reached only
        # through the bag.
        for key in ('startM', 'receiverTrackId'):
            assert readers(key, seen) == {'assets/passing.js::passingNetwork'}

    def test_the_callback_binding_is_load_bearing(self, seen):
        # `(events || []).map((event) => ...)` in the label export.
        without = read(callbacks=False)
        for key in ('confidence', 'inPlay'):
            assert not readers(key, without), key
            assert readers(key, seen), key

    def test_the_loop_binding_is_load_bearing(self, seen):
        # `for (const event of events)` in the passing network and the shot
        # log. Four figures, two of which nothing else reads at all.
        without = read(loops=False)
        for key in ('team', 'outcome', 'receiverTrackId', 'startM'):
            assert not readers(key, without), key
            assert readers(key, seen), key

    def test_the_per_function_split_is_load_bearing(self, made):
        # It is also what makes the cross-function step possible: without it
        # there are no callees to seed, so the whole interprocedural half of
        # the scan stops, and the row fields go with it -- down to the same
        # lone `inPlay` that survives `cross=False`, and for the same reason.
        whole = read(chunked=False)
        rows = [k for k, level in made.items() if level == 'each event']
        assert [k for k in rows if readers(k, whole)] == ['inPlay']

    def test_comment_stripping_is_load_bearing(self, made, seen):
        # In both directions, which is unusual -- on the other four seam gates
        # this guard is inert. Prose sitting between a receiver and its
        # `.map(` breaks the walk-back, so a real reader is lost; and a
        # comment containing example code hands back `const` as a figure.
        with_prose = read(strip_comments=False)
        assert not readers('confidence', with_prose)
        assert readers('confidence', seen)
        assert [k for k in with_prose if k not in made] == ['const']

    def test_the_builtin_exclusion_is_load_bearing(self, made):
        # `events.length` is a property of every array ever made.
        loose = read(builtins=False)
        assert [k for k in loose if k not in made] == ['length']
