# -*- coding: utf-8 -*-
"""The id seam: every element the frontend reaches for by name, and every name.

`tests/test_css_seam.py` asks whether a rule reaches an element. This asks the
sharper version of the same question, and the two directions are not equally
dangerous:

* An id that is **looked up and declared nowhere** is the silent one.
  `byId('cv-shotlog')` on a page where somebody renamed that element returns
  null, the caller guards it with `?.` or an `if`, and a whole block of the
  match view quietly never renders. Nothing throws. Nothing is logged. The page
  looks finished and is missing a section.

* An id that is **declared and referenced nowhere** is dead weight in markup.
  Untidy, cheap to find, and harmless until somebody wires a stylesheet to it
  and wonders why the element they meant is not the element they got.

So this scan is deliberately lopsided: greedy about uses, conservative about
declarations. Over-counting a use costs one entry off the dead list; missing a
use invents a ghost that is not there and sends the next person hunting a bug
that does not exist.

    Follow the value, not the thing that holds it.

The first draft found sixteen references for three hundred and ninety-five ids,
which is the lesson the stylesheet scan taught one level up.
`document.getElementById` appears exactly **once** in the whole frontend, at
`assets/ui.js:10`; every other lookup goes through the `byId` it exports, or
through `setText`, `showOnly`, `openSheet` and `closeSheet`, which take an id as
their first argument and call `byId` themselves. Scanning for the raw DOM call
would have found one use and condemned three hundred and eighty-nine ids.

There turned out to be five ways an id gets consumed here, and only the first is
a plain literal in a call:

1. a literal handed to one of those helpers, or written into a stylesheet, a
   `querySelector('#...')`, or a `for=` / `aria-labelledby=` attribute;
2. a **hole** -- ``byId(`view-${name}`)`` in `live-tagging/tagging.js` and
   ``byId(`tab-${name}`)`` in `coach/coach.js` -- where the id never exists as a
   string anywhere. Only the prefix before the first `${` survives a regex, so
   every declared id starting with it counts as reachable. Five ids are
   reachable *only* this way;
3. an id read off the **element**, never written in a call at all:
   `assets/rail.js` takes `block.dataset.railGroup || block.id` for anything
   carrying `data-rail`. Provable from markup alone, with no JavaScript parsed.
   Nine ids are reachable only this way;
4. a bare string threaded through a wrapper -- `fillPlayerGroup('pv-ball-block',
   'pv-ball-stats', ...)`, `{ host: 'pv-ball-form' }`, the
   `['us', 'team_a', 'cv-shots-us', ...]` tuple table, the keys of the
   `renderGettingStarted` checklist. Real string-valued dataflow across a
   function boundary, which a regex cannot follow. Those land in `WEAK` below
   with a written reason -- the same bargain `test_css_seam.py` strikes -- rather
   than being pretended away in either direction;
5. a lookup from outside the frontend altogether. `tests/smoke.test.js` builds
   each real page in a DOM shim and reaches into it by name -- `el(id)` and
   `text(id)` -- to check what rendered. Three ids exist for nothing else: the
   calibrate page builds `btn-apply-marks`, `btn-apply-size` and
   `btn-reset-marks` in JavaScript once it has a measurement worth offering,
   and the harness has to be able to press them.

   That site was missing at first, and the omission cost something. The scan
   walked the seven frontend directories and stopped there, so it saw three
   declarations no frontend file read, called them dead, and they were deleted
   on its word. `npm run test:pages` caught it; this gate did not. A scanner's
   universe is part of its correctness, and a false `dead` is worse than no
   scanner at all, because a scanner gets believed.

    Why this fails in both directions.

An allowlist of bare names rots into a graveyard nobody dares touch, so `WEAK`
is checked for staleness too: an entry that has since become provable, or whose
element has gone, fails exactly as loudly as a new orphan.

Every scan site was measured before anything here was pinned, by switching it off
on its own and reading what appeared. Three candidate sites -- `href="#..."`
fragments, `url(#...)` references, and a literal id array passed to `showOnly` --
turned out to have **no input at all** in this repo, and were deleted rather than
carried as machinery that cannot be shown to earn anything. A guard that cannot
fire is the same mistake as a gate that cannot fail, one level down.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_id_seam.py -q
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from test_css_seam import CSS, HTML, JS_DIRS, literals, selectors

ROOT = Path(__file__).resolve().parents[1]

# Which page each module directory belongs to. `assets/` is shared by all of
# them and so has no entry -- a lookup there cannot be checked against one
# page's markup.
PAGE_OF_DIR = {
    'coach': 'coach/index.html',
    'player': 'player/index.html',
    'live-tagging': 'live-tagging/index.html',
    'halftime': 'halftime/index.html',
    'calibrate': 'calibrate/index.html',
    'xg-sandbox': 'xg-sandbox/index.html',
}

# No dot and no colon: `#canvas.aiming` is an id *and* a class, and reading it as
# one name invents a ghost.
ID = r'[A-Za-z_][A-Za-z0-9_-]*'

ID_ATTR = re.compile(r'\bid\s*=\s*["\'](' + ID + r')["\']')
SET_ID = re.compile(r'\.id\s*=\s*["\'](' + ID + r')["\']')

# Attributes that name another element. Any of them may hold a space-separated
# list, which is why the value is split rather than matched as one id.
REF_ATTR = re.compile(
    r'\b(?:for|form|list|headers|aria-controls|aria-labelledby|'
    r'aria-describedby|aria-owns|aria-activedescendant)\s*=\s*'
    r'["\']([^"\']+)["\']')

# Every helper whose first argument is an element id, plus the raw call they all
# wrap. Adding one of these and forgetting to list it here is not a harmless
# omission: when `openSheet` was split out of `tagging.js`, five ids went from
# proven to merely mentioned in the same commit, and only this scan noticed.
HELPERS = (r'(?:document\.getElementById|byId|setText|showOnly'
           r'|openSheet|closeSheet)')
BY_ID = re.compile(HELPERS + r'\(\s*["\'`](' + ID + r')["\'`]')
HOLE_ARG = re.compile(HELPERS + r'\(\s*`([^`$]*)\$\{')

# `assets/rail.js`: `const RAILED = 'section.block[data-rail], ...'` picks these
# out of the page, and `groupOf` reads `block.dataset.railGroup || block.id`.
RAILED = re.compile(r'<[^>]*\bdata-rail(?:-group)?[=\s>][^>]*>')

IN_SELECTOR = re.compile(r'#(' + ID + r')')
QUERY = re.compile(r'\.(?:querySelector|querySelectorAll|closest|matches)\($')
ARRAY = re.compile(r'=\s*\[([^\]]*)\]', re.S)
BARE = re.compile(r'["\'`](' + ID + r')["\'`]')

# The two shapes a raw DOM lookup is allowed to take -- see the test that
# reads them. `[^;}]*?` keeps the second one inside a single statement, so a
# wrapper cannot claim a call several lines below it.
RAW_LOOKUP = re.compile(r'document\.getElementById\(')
RAW_WRAPPER = re.compile(
    r'(?:function\s+(\w+)\s*\(|const\s+(\w+)\s*=\s*\()'
    r'[^;}]*?document\.getElementById\(\s*[A-Za-z_]', re.S)

# The served markup has one reader that is not part of the frontend at all.
HARNESS = 'tests/smoke.test.js'
HARNESS_REF = re.compile(
    r'\b(?:el|text|getElementById)\(\s*["\'`](' + ID + r')["\'`]')

SITES = ('attribute', 'rail', 'stylesheet', 'byId', 'query', 'array',
         'hole', 'harness')


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def js_files():
    out = []
    for d in JS_DIRS:
        out += sorted(str(p.relative_to(ROOT)).replace('\\', '/')
                      for p in (ROOT / d).glob('*.js'))
    return out


class Seam:
    """What the scan found. Plain attributes, so the measurement script can read
    any of them without going through a test."""

    def __init__(self):
        self.declared = defaultdict(list)
        self.per_page = defaultdict(list)
        self.used = defaultdict(list)
        self.bare = defaultdict(list)
        self.prefixes = []
        self.locality = []

    @property
    def ghosts(self):
        """Looked up, declared nowhere. The silent direction."""
        return sorted(n for n in self.used if n not in self.declared)

    @property
    def orphans(self):
        """Declared, and not so much as mentioned anywhere else."""
        return sorted(n for n in self.declared
                      if n not in self.used and n not in self.bare)

    @property
    def weak(self):
        """Declared and mentioned, but only as a bare string."""
        return sorted(n for n in self.declared
                      if n not in self.used and n in self.bare)

    @property
    def dupes(self):
        out = []
        for page, names in sorted(self.per_page.items()):
            seen = set()
            for n in names:
                if n in seen:
                    out.append((page, n))
                seen.add(n)
        return out


def scan(*, drop=()):
    """Walk the seam. `drop` switches off one scan site or guard, which is how
    each of them was shown to be load-bearing before its figure was pinned."""
    seam = Seam()
    on = lambda site: site not in drop            # noqa: E731

    def note(name, where):
        seam.used[name].append(where)

    for rel in HTML:
        src = read(rel)
        for m in ID_ATTR.finditer(src):
            seam.declared[m.group(1)].append(rel)
            seam.per_page[rel].append(m.group(1))

    # A few ids are built in JavaScript rather than written in markup. They are
    # declarations like any other -- and the string that declares one must not
    # be allowed to vouch for it, which is what the `assigned` guard below is
    # for.
    if 'assigned-declares' not in drop:
        for rel in js_files():
            page = PAGE_OF_DIR.get(rel.split('/')[0])
            for m in SET_ID.finditer(read(rel)):
                seam.declared[m.group(1)].append(rel)
                if page:
                    seam.per_page[page].append(m.group(1))

    for rel in HTML:
        src = read(rel)
        if on('attribute'):
            for m in REF_ATTR.finditer(src):
                for part in m.group(1).split():
                    note(part, rel + ' (attribute)')
        if on('rail'):
            for m in RAILED.finditer(src):
                tag = ID_ATTR.search(m.group(0))
                if tag:
                    note(tag.group(1), rel + ' (rail)')

    if on('stylesheet'):
        for rel in CSS:
            for sel in selectors(read(rel)):
                for m in IN_SELECTOR.finditer(sel):
                    note(m.group(1), rel + ' (stylesheet)')

    # `tests/smoke.test.js` builds each real page in a DOM shim and reaches into
    # it by name: `el('btn-apply-marks')`, `text('marks-measured')`. Those are
    # uses -- delete one of those ids and the build stops. Leaving this site out
    # is not a theoretical gap. It reported three live buttons dead once, and
    # they were duly deleted, and the pages suite caught it rather than this
    # gate.
    if on('harness'):
        src = read(HARNESS)
        for m in HARNESS_REF.finditer(src):
            note(m.group(1), HARNESS + ' (harness)')

    # A commented-out `byId('foo')` must not vouch for `foo`. Currently inert --
    # no comment in the frontend holds an id -- and kept anyway, because it
    # guards the direction where being wrong is quiet: it can only ever remove a
    # false witness, never add one.
    keep_comments = 'comments' in drop

    for rel in js_files():
        src = read(rel)
        if on('byId'):
            for m in BY_ID.finditer(src):
                note(m.group(1), rel + ' (byId)')
        lits = list(literals(src, comments=not keep_comments))
        if on('query'):
            for _q, text, _h, start, _end in lits:
                if QUERY.search(src[max(0, start - 40):start]):
                    for m in IN_SELECTOR.finditer(text):
                        note(m.group(1), rel + ' (query)')
        if on('array'):
            # `const VIEWS = ['view-setup', 'view-kickoff', ...]` -- a list of
            # ids handed somewhere else later. Counted only when *every* member
            # is a declared id, so the `['us', 'team_a', 'cv-shots-us', ...]`
            # tuple table does not vouch for its own third column on the
            # strength of two team keys sitting beside it.
            for m in ARRAY.finditer(src):
                names = BARE.findall(m.group(1))
                if names and all(n in seam.declared for n in names):
                    for n in names:
                        note(n, rel + ' (array)')
        # A declaration must not vouch for itself: the literal in
        # `btn.id = 'btn-apply-marks'` is the id being made, not evidence that
        # anything reads it.
        spans = ([] if 'assigned' in drop
                 else [m.span() for m in SET_ID.finditer(src)])
        for _q, text, _h, _s, _e in lits:
            if any(a <= _s <= b for a, b in spans):
                continue
            if text in seam.declared:
                seam.bare[text].append(rel)

    if on('hole'):
        for rel in js_files():
            for m in HOLE_ARG.finditer(read(rel)):
                prefix = m.group(1)
                if not prefix:
                    continue
                seam.prefixes.append((prefix, rel))
                for n in list(seam.declared):
                    if n.startswith(prefix):
                        note(n, rel + ' (hole ' + prefix + '...)')

    # A page-specific module may only look up ids that exist on its own page.
    # `assets/` is excluded: those modules are shared and legitimately reach for
    # elements that only some pages carry.
    for rel in js_files():
        page = PAGE_OF_DIR.get(rel.split('/')[0])
        if not page:
            continue
        here = set(seam.per_page[page])
        holes = [p for p, at in seam.prefixes if at == rel]
        for m in BY_ID.finditer(read(rel)):
            n = m.group(1)
            if n in here or any(n.startswith(p) for p in holes):
                continue
            seam.locality.append((n, rel, page))

    return seam


SEAM = scan()


# --------------------------------------------------------------------------
# Names mentioned in JavaScript, but only as bare strings.
#
# Each of these is a real string-valued dataflow across a function boundary. The
# reason is the point: an entry with no explanation is an entry nobody can ever
# safely remove, which is how an allowlist becomes a graveyard.
# --------------------------------------------------------------------------

_WRAPPER = ('positional argument to fillPlayerGroup/fillGroup, which calls '
            'byId on it (coach/coach.js:806, player/player.js)')
_HOST = ("the `host:` key of a stat-group descriptor, read back as "
         '`byId(group.host)` (coach/coach.js:910, player/player.js:177)')
_TUPLE = ('a column of the sides tuple table destructured in the shot-map loop '
          '(coach/coach.js:1247)')
_CHECKLIST = ('a *key* of the renderGettingStarted checklist, iterated as '
              '`byId(id)` over Object.entries (coach/coach.js:346)')

WEAK = {
    'ball-form': _HOST,
    'ball-note': _WRAPPER,
    'ball-stats': _WRAPPER,
    'cv-shots-them': _TUPLE,
    'cv-shots-them-cap': _TUPLE,
    'cv-shots-us': _TUPLE,
    'cv-shots-us-cap': _TUPLE,
    'defending-note': _WRAPPER,
    'defending-stats': _WRAPPER,
    'pv-ball-form': _HOST,
    'pv-ball-note': _WRAPPER,
    'pv-ball-stats': _WRAPPER,
    'pv-defending-note': _WRAPPER,
    'pv-defending-stats': _WRAPPER,
    'pv-running-form': _HOST,
    'pv-running-note': _WRAPPER,
    'pv-running-stats': _WRAPPER,
    'pv-stats': _WRAPPER,
    'pv-tagged-note': _WRAPPER,
    'running-form': _HOST,
    'running-note': _WRAPPER,
    'running-stats': _WRAPPER,
    'season-stats': _WRAPPER,
    'step-invite': _CHECKLIST,
    'step-match': _CHECKLIST,
    'step-players': _CHECKLIST,
    'step-tag': _CHECKLIST,
}

# Measured, one site switched off at a time. Every figure below came off the
# repo rather than out of a guess -- an earlier gate in this project pinned eight
# guessed numbers and got all eight wrong, one of them backwards.
ONLY_BY_HOLE = {'tab-matches', 'tab-roster', 'tab-staff',
                'view-kickoff', 'view-setup'}
ONLY_BY_ATTRIBUTE = {'clock-explain', 'clock-title', 'cv-missed-help',
                     'exit-explain', 'exit-title', 'log-title',
                     'name-angle', 'name-def-dist', 'name-def-goal',
                     'name-distance', 'name-keeper-angle', 'name-keeper-dist',
                     'sub-title'}
ONLY_BY_RAIL = {'md-stats-block', 'md-team-block', 'pipeline-block',
                'players-block', 'publish-block', 'pv-matches-block',
                'season-matches-block', 'timeline-block', 'video-link-block'}
PREFIXES = {'input-', 'tab-', 'view-'}


def dead_without(site):
    """Ids that go unreferenced when one scan site is switched off."""
    return set(scan(drop=(site,)).orphans) - set(SEAM.orphans)


def weak_without(site):
    """Ids that drop from proven to merely mentioned without one site."""
    return set(scan(drop=(site,)).weak) - set(SEAM.weak)


# --- the scan is looking at something ------------------------------------

def test_every_file_it_claims_to_read_exists():
    for rel in list(HTML) + list(CSS) + js_files():
        assert (ROOT / rel).is_file(), rel
    assert len(HTML) >= 7 and len(CSS) >= 6 and len(js_files()) >= 25


def test_it_finds_ids_at_all():
    # The vacuum guard. A scan that quietly stops matching reports a clean seam,
    # which is indistinguishable from a clean seam until somebody looks.
    assert len(SEAM.declared) >= 350
    assert len(SEAM.used) >= 330
    assert len(SEAM.bare) >= 250


def test_every_raw_dom_lookup_is_one_this_scan_can_see_past():
    """A raw `document.getElementById` is only safe here in two shapes.

    Either its argument is a literal, and `BY_ID` reads it directly, or it is a
    parameter -- and then the function holding it is a wrapper, whose own name
    has to be in `HELPERS` or every call site going through it is invisible.

    A third shape -- a raw call on a variable, inside a function this scan has
    never heard of -- would take a whole page's worth of ids off the used list
    at once and report every one of them as dead. That is the noisy direction
    rather than the silent one, but it is a great deal of noise, and the fix is
    always to name the wrapper here rather than to widen the regex.
    """
    calls, wrapped, names = 0, 0, set()
    for rel in js_files():
        src = read(rel)
        calls += len(RAW_LOOKUP.findall(src))
        for m in RAW_WRAPPER.finditer(src):
            wrapped += 1
            names.add(m.group(1) or m.group(2))
    assert names == {'byId'}
    for name in names:
        assert re.fullmatch(HELPERS, name), name
    # One wrapper, written twice: once in `assets/ui.js` for everybody, and once
    # in `xg-sandbox/sandbox.js`, which says in a comment why it keeps its own
    # copy rather than dragging the report renderer onto a canvas page.
    assert wrapped == 2
    # The one remaining raw call takes a literal: `getElementById('display')`.
    assert calls - wrapped == 1


# --- the two findings -----------------------------------------------------

def test_no_id_is_looked_up_that_nothing_declares():
    # The silent direction: `byId` returns null, the caller guards it, and a
    # block of the page never renders without a word about it.
    assert SEAM.ghosts == []


def test_no_id_is_declared_that_nothing_references():
    assert SEAM.orphans == []


def test_no_page_declares_the_same_id_twice():
    # `getElementById` returns the first one, so the second element is
    # unreachable and the CSS rule lands on whichever the author did not mean.
    assert SEAM.dupes == []


def test_every_lookup_resolves_on_its_own_page():
    # A page-specific module reaching for an element its own page has not got.
    assert SEAM.locality == []


MADE_IN_JS = {
    'btn-apply-marks': 'calibrate/calibrate.js',
    'btn-apply-size': 'calibrate/calibrate.js',
    'btn-reset-marks': 'calibrate/calibrate.js',
}


def test_the_ids_built_in_javascript_are_the_three_we_know_about():
    """Almost everything this frontend builds carries its behaviour by closure,
    and so needs no name. These three are the exception, and they earn it: the
    calibrate page only creates them once it has a measurement worth offering,
    and `tests/smoke.test.js` has to be able to press them to check that the
    offer arrived. The id is the handle, and it is a real one.

    A fourth entry is not automatically wrong, but it is worth a look. An
    element that has to be findable by name after it is made is usually one
    that would be simpler written as markup that starts out hidden.
    """
    made = {m.group(1): rel
            for rel in js_files() for m in SET_ID.finditer(read(rel))}
    assert made == MADE_IN_JS


def test_every_id_built_in_javascript_is_pressed_by_the_harness():
    # The reason they are allowed to exist at all. If one stops being reached
    # from the harness it is either dead, or the test that used to press it has
    # quietly stopped checking. Both deserve a failure.
    for name in MADE_IN_JS:
        where = SEAM.used.get(name) or []
        assert any('(harness)' in w for w in where), name


def test_harness_site_is_load_bearing():
    # These three are declared in JavaScript and read only from the test suite.
    # Without this site the scan sees a declaration nothing wants and says so --
    # which is exactly what happened, and the three buttons were deleted on its
    # word before `npm run test:pages` disagreed.
    assert dead_without('harness') == set(MADE_IN_JS)


def test_javascript_declarations_are_load_bearing():
    # And the other half of the same pair: stop reading `.id =` as a
    # declaration and the harness is left reaching for three names that,
    # as far as the markup knows, do not exist.
    assert set(scan(drop=('assigned-declares',)).ghosts) == set(MADE_IN_JS)


# The guard that stops `btn.id = 'btn-apply-marks'` counting as evidence that
# something *reads* `btn-apply-marks` is currently inert -- with it switched
# off, no name changes category, because all three have a real reader in the
# harness. It stays anyway, untested, and deliberately so: it covers the quiet
# direction. Being wrong here does not add a false alarm, it removes a true
# one, and an id that vouches for itself is an id that can never be reported
# dead no matter how dead it gets.


# --- the weak list, checked for staleness in both directions --------------

def test_weak_list_is_exactly_what_the_scan_finds():
    assert sorted(WEAK) == SEAM.weak


def test_every_weak_name_is_still_declared_somewhere():
    for name in WEAK:
        assert name in SEAM.declared, name


def test_every_weak_name_carries_a_reason():
    for name, why in WEAK.items():
        assert len(why) > 40, name
        assert '.js' in why, name


def test_the_weak_list_stays_small():
    # It is a bargain, not a bucket. Twenty-seven of three hundred and ninety is
    # the price of four wrapper shapes; a jump means a new indirection went in
    # that should have been read and named rather than tolerated.
    assert len(WEAK) <= 32
    assert len(WEAK) < len(SEAM.declared) // 10


# --- every site earns its place -------------------------------------------

def test_attribute_site_is_load_bearing():
    # `for=`, `aria-labelledby=`, `aria-describedby=`. Thirteen ids exist only
    # to be named by another element: six dialog headings and explanations on
    # the tagging sheets, which no script ever looks up, and the six `name-*`
    # spans the sandbox sliders point at. Those six were added the day a scan
    # found the sliders had no accessible name at all -- their visible label
    # sits in a sibling div, which names them for anyone who can see it and
    # nobody else. Pointing at the span rather than repeating the words into
    # an `aria-label` is what keeps the two from drifting apart.
    assert dead_without('attribute') == ONLY_BY_ATTRIBUTE


def test_rail_site_is_load_bearing():
    # The largest markup site: nine section ids read off the DOM by
    # `assets/rail.js` and never written in a call.
    assert dead_without('rail') == ONLY_BY_RAIL


def test_hole_site_is_load_bearing():
    # ``byId(`view-${name}`)``. These five ids appear nowhere in any source file
    # as a whole string.
    assert dead_without('hole') == ONLY_BY_HOLE


def test_the_lookup_prefixes_are_the_measured_three():
    # A fourth prefix means a new runtime-built lookup; a missing one means a
    # view switcher stopped working. Both are worth a failing test.
    assert {p for p, _ in SEAM.prefixes} == PREFIXES


def test_helper_site_carries_almost_all_the_evidence():
    # Without it, two hundred and sixty-one ids fall back to being merely
    # mentioned in the same file they belong to, and the weak list -- the part a
    # human has to read and vouch for -- becomes ten times the size of the
    # thing it is meant to annotate.
    assert len(weak_without('byId')) >= 200


def test_array_site_carries_evidence():
    assert len(weak_without('array')) >= 10


def test_stylesheet_site_has_something_to_read():
    # It finds no *dead* id -- everything styled by name is also looked up by
    # name -- but it is the only site that would catch a rename that leaves a
    # rule behind, so it is kept and checked for input instead of for output.
    seen = {m.group(1)
            for rel in CSS for sel in selectors(read(rel))
            for m in IN_SELECTOR.finditer(sel)}
    # Measured at fourteen. Pinned under that, so a rule or two moving out of a
    # stylesheet is not a failure but the site going blind is.
    assert len(seen) >= 12
    assert seen <= set(SEAM.declared)


def test_query_site_has_something_to_read():
    hits = [(rel, m.group(1))
            for rel in js_files()
            for _q, text, _h, s, _e in literals(read(rel))
            if QUERY.search(read(rel)[max(0, s - 40):s])
            for m in IN_SELECTOR.finditer(text)]
    assert hits == [('live-tagging/tagging.js', 'sheet-question')]
