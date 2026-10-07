# -*- coding: utf-8 -*-
"""The browser's export seam: every exported name has somebody who wants it.

`tests/test_call_graph.py` asks this question of `cv/` and has never asked it of
the frontend. So a browser helper can be written, exported, covered by its own
tests, and called by nothing, and no gate says a word -- which is exactly the
shape that audit went looking for on the Python side.

Six knobs, every one measured before it was pinned:

1. `static` -- `import { a, b as c } from './x.js'`, multi-line clauses too.
   Carries 87 names on its own, more than the other sites put together.
2. `dynamic` -- `const { shotMapSvg } = await import('...')`. One name, one
   caller: `tests/smoke.test.js`.
3. `namespace` -- `import * as report from '../assets/report.js'` followed by
   `report.someName(...)`. `tests/video.test.js` reaches 37 names this way.
4. `local` -- the name used again inside its own file. Not a use site but a
   classifier: it separates "nobody wants this" from "exported wider than it
   needs to be".
5. `comments` -- strip `//` and `/* ... */` first. Load-bearing by luck rather
   than by design: `assets/db.js` carries a four-line comment inside its
   `export { ... }` list, and without the stripper the word `and` is read as an
   exported name.
6. `harness` -- whether `tests/*.js` count as readers at all.

That last knob is why this file is written the way it is. The id seam's scan
walked only the seven frontend directories, could not see `tests/smoke.test.js`
reaching into the markup by name, reported three live buttons dead, and they
were deleted on its word. Here the same blindness is worth 40 verdicts: switch
`harness` off and one export goes outright dead while 39 more are demoted. A
scanner's universe is part of its correctness, and a false `dead` costs more
than no scanner at all, because a scanner gets believed.
"""
import inspect
import re

from test_css_seam import ROOT, js_files, read

NAME = r'[A-Za-z_$][\w$]*'

# Declarations. `export function`, `export const`, and the re-export list.
DECL = re.compile(
    r'^export\s+(?:async\s+)?(?:function\*?|const|class|let|var)\s+('
    + NAME + r')', re.M)
DECL_LIST = re.compile(r'^export\s*\{([^}]*)\}', re.M)

# Uses.
STATIC = re.compile(r'\bimport\s*\{([^}]*)\}\s*from', re.S)
DYNAMIC = re.compile(r'\{([^}]*)\}\s*=\s*await\s+import\s*\(', re.S)
NAMESPACE = re.compile(r'\bimport\s*\*\s*as\s+(' + NAME + r')\s+from')

# Crude on purpose: this only has to be right about the comment blocks that sit
# inside an `export { ... }` list, not about JavaScript in general.
LINE_COMMENT = re.compile(r'(?<![:\'"\\])//[^\n]*')
BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)

# Every knob `scan()` has, and the whole vocabulary `drop` accepts. Not all six
# are use sites: `local` is a classifier and `comments` is a filter, and both
# are dropped by name like the rest. Kept in step with the scan itself by
# `test_every_site_it_names_is_a_site_it_reads`.
SITES = ('static', 'dynamic', 'namespace', 'local', 'comments', 'harness')


def harness_files():
    """The test suite's own JavaScript, which reads the frontend like any page."""
    return sorted(p.relative_to(ROOT).as_posix()
                  for p in (ROOT / 'tests').glob('*.js'))


def strip_comments(src):
    return LINE_COMMENT.sub('', BLOCK_COMMENT.sub('', src))


def named(clause):
    """The names a `{ ... }` clause binds, honouring `a as b`."""
    out = []
    for part in clause.split(','):
        text = part.strip()
        if not text:
            continue
        bits = text.split()
        out.append(bits[-1] if len(bits) == 3 and bits[1] == 'as' else bits[0])
    return [n for n in out if re.fullmatch(NAME, n)]


class Seam:
    """One reading of the seam, with one or more sites switched off."""

    def __init__(self, declared, owners, used, local):
        self.declared = declared      # name -> files declaring it outright
        self.owners = owners          # name -> files declaring or re-exporting
        self.used = used              # name -> where it is read from elsewhere
        self.local = local            # names used again inside their own file
        self.dead = sorted(n for n in owners
                           if n not in used and n not in local)
        self.local_only = sorted(n for n in owners
                                 if n not in used and n in local)
        self.only_tests = sorted(
            n for n, where in used.items()
            if all(w.startswith('tests/') for w in where))

    @property
    def product_dead(self):
        """Reached only from the test suite, and not used at home either.

        The sharp category. A name here is finished, covered, and connected to
        nothing a person using the site can reach.
        """
        return sorted(n for n in self.only_tests if n not in self.local)


def scan(drop=()):
    def on(site):
        return site not in drop

    clean = strip_comments if on('comments') else (lambda src: src)

    declared = {}
    reexported = {}
    for rel in js_files():
        src = clean(read(rel))
        for m in DECL.finditer(src):
            declared.setdefault(m.group(1), []).append(rel)
        for m in DECL_LIST.finditer(src):
            for name in named(m.group(1)):
                reexported.setdefault(name, []).append(rel)

    owners = dict(declared)
    for name, where in reexported.items():
        owners.setdefault(name, where)

    used = {}

    def note(name, where):
        if name in owners:
            used.setdefault(name, []).append(where)

    readers = js_files() + (harness_files() if on('harness') else [])
    for rel in readers:
        src = clean(read(rel))
        if on('static'):
            for m in STATIC.finditer(src):
                for name in named(m.group(1)):
                    note(name, rel + ' (static)')
        if on('dynamic'):
            for m in DYNAMIC.finditer(src):
                for name in named(m.group(1)):
                    note(name, rel + ' (dynamic)')
        if on('namespace'):
            for m in NAMESPACE.finditer(src):
                alias = re.escape(m.group(1))
                for hit in re.finditer(alias + r'\.(' + NAME + r')', src):
                    note(hit.group(1), rel + ' (namespace)')

    local = set()
    if on('local'):
        for name, homes in owners.items():
            for rel in homes:
                body = clean(read(rel))
                if len(re.findall(r'\b' + re.escape(name) + r'\b', body)) > 1:
                    local.add(name)
                    break

    return Seam(declared, owners, used, local)


SEAM = scan()


def dead_without(site):
    return set(scan(drop=(site,)).dead) - set(SEAM.dead)


def local_only_without(site):
    return set(scan(drop=(site,)).local_only) - set(SEAM.local_only)


# ---------------------------------------------------------------------------
# What the scan can see
# ---------------------------------------------------------------------------

def test_every_file_it_claims_to_read_exists():
    for rel in js_files() + harness_files():
        assert (ROOT / rel).is_file(), rel
    assert len(js_files()) >= 25
    assert len(harness_files()) >= 8


def test_it_finds_exports_at_all():
    # The vacuum guard. A scan that has quietly stopped matching reports a clean
    # seam, and a clean seam and a broken scan look identical from here.
    assert len(SEAM.owners) >= 280
    assert len(SEAM.used) >= 240
    # 150 until the calibrate page and the xG sandbox left (2026-10-07).
    assert len(SEAM.local) >= 130


def test_no_two_files_export_the_same_name():
    # The precondition for scanning by name rather than by module. The moment
    # two files export a `format`, an import of one of them vouches for both and
    # every verdict below is worth less than it looks.
    dupes = {n: w for n, w in SEAM.declared.items() if len(w) > 1}
    assert dupes == {}


def test_every_import_in_the_repo_is_a_shape_this_scan_can_read():
    """No default exports, no star re-exports, no CommonJS.

    Each of those would be a name this scan cannot follow, and an unfollowable
    name is not reported as unfollowable -- it is reported as dead.
    """
    plain = []
    for rel in js_files() + harness_files():
        src = read(rel)
        assert not re.search(r'^export\s+default', src, re.M), rel
        assert not re.search(r'^export\s*\*', src, re.M), rel
        assert 'require(' not in src, rel
        for line in src.splitlines():
            if (line.startswith('import ')
                    and not line.startswith('import {')
                    and not line.startswith('import *')):
                plain.append(line.strip())
    # The two shapes that bind no project name: a node builtin, and one bare
    # side-effect import that installs the module hooks.
    assert set(plain) == {
        "import assert from 'node:assert/strict';",
        "import './module-hooks.js';",
    }


# ---------------------------------------------------------------------------
# The headline
# ---------------------------------------------------------------------------

def test_nothing_is_exported_that_nobody_reads():
    assert SEAM.dead == []


# `onPitchAt` (assets/report.js) answers "who was on the pitch at this second"
# from a roster's stints. It has about ten cases in tests/video.test.js and no
# caller in the site: every screen that wants the eleven at a moment goes
# through `substitutionChanges`, which walks the same stints and returns the
# moments rather than a set.
#
# It is pinned rather than deleted deliberately. A scanner's word is not enough
# to remove tested code -- the id seam is exactly why -- and this is the one
# primitive a live-clock view would want first. If it is still here and still
# unwanted when that view is built or abandoned, delete it then.
PRODUCT_DEAD = {'onPitchAt'}


def test_the_exports_only_the_test_suite_wants_are_the_ones_we_know_about():
    assert set(SEAM.product_dead) == PRODUCT_DEAD


def test_plenty_of_exports_are_reached_only_by_tests_and_that_is_fine():
    # The other side of the same measurement, and the reason `product_dead` is
    # narrowed by `local` at all. Around forty helpers are exported so their own
    # tests can reach them and used by a sibling in the same file -- a rendered
    # SVG's row builder, a scale, a radius. Exported-for-testing is a real
    # pattern here, and calling all of it dead would be forty false alarms.
    assert len(SEAM.only_tests) >= 30


# ---------------------------------------------------------------------------
# Exported wider than they are used
# ---------------------------------------------------------------------------

# Names carrying `export` that nothing outside their own file ever reads. None
# of these is a bug; together they are a map of where the module boundaries are
# drawn looser than they need to be. Pinned in both directions: a new entry is
# a name that grew a keyword it does not need, and a departing one is a name
# that found a reader and should stop being listed here.
LOCAL_ONLY = {
    # Tuning constants that sit beside the code they tune. `assets/report.js`
    # exports nearly everything it declares so `tests/video.test.js` can reach
    # in; these are the ones no test asked for either.
    'report.js tuning constants': {
        'BRIDGE_S', 'CHANGE_GROUP_S', 'CHANGE_WINDOW_S', 'COVERAGE_MARGIN_M',
        'COVERAGE_SPREAD_MAX', 'LEVEL', 'MAX_TIMELINE', 'MIN_CHANGE_WINDOW_S',
        'NEARBY_TAG_S', 'PERIOD_WORDS', 'PHANTOM_M_PER_MINUTE',
        'POSITION_FLOOR_M', 'POSITION_SIGMAS', 'WANDER_TAU_S',
    },
    # Two helpers called once each, by a neighbour a few hundred lines away.
    'report.js private helpers': {'resultOf', 'reviewedCorrections'},
    # The season page's own measure table and its floor.
    'season.js measure table': {
        'FORM_MEASURES', 'MEASURE_BY_KEY', 'MIN_POINT_ATTEMPTS',
    },
    # The wording behind the confidence chips, used by the chip builder itself.
    'ui.js wording': {'CONFIDENCE_LEVELS'},
    # Read and write halves of the same two internal conveniences.
    'auth.js internals': {'emailOf', 'saveHint'},
    'db.js internals': {'cancelInvite'},
    # The initialised app and the config it was built from. Everything else
    # imports the services, not these.
    'firebase-init.js handles': {'app', 'firebaseConfig'},
    # Each of these draws a chart and is called only by the mount function
    # directly below it, which finds the element and puts the markup in.
    'renderers with a sibling mount': {
        'formCard', 'formSparkline', 'heatmapSvg', 'passMapSvg', 'pitchSvg',
    },
    'shell.js view list': {'VIEWS'},
}

LOCAL_ONLY_NAMES = {n for group in LOCAL_ONLY.values() for n in group}


def test_the_local_only_exports_are_the_ones_written_down():
    assert set(SEAM.local_only) == LOCAL_ONLY_NAMES


def test_the_local_only_groups_do_not_overlap():
    # Otherwise the union above hides a name written down twice and the count
    # stops meaning anything.
    total = sum(len(group) for group in LOCAL_ONLY.values())
    assert total == len(LOCAL_ONLY_NAMES) == 31


def test_each_local_only_name_really_does_live_where_its_group_says():
    where = {
        'report.js tuning constants': 'assets/report.js',
        'report.js private helpers': 'assets/report.js',
        'season.js measure table': 'assets/season.js',
        'ui.js wording': 'assets/ui.js',
        'auth.js internals': 'assets/auth.js',
        'db.js internals': 'assets/db.js',
        'firebase-init.js handles': 'assets/firebase-init.js',
        'shell.js view list': 'coach/shell.js',
    }
    for label, names in LOCAL_ONLY.items():
        if label not in where:
            continue
        for name in names:
            assert SEAM.owners[name] == [where[label]], (label, name)


def test_the_renderers_and_their_mounts_share_a_file():
    # The one group whose members live in five different files. What makes them
    # a group is the shape, not the address: a renderer that returns markup and
    # a mount beside it that places the markup.
    for name in LOCAL_ONLY['renderers with a sibling mount']:
        homes = SEAM.owners[name]
        assert len(homes) == 1, name
        assert homes[0].endswith('.js'), name


# ---------------------------------------------------------------------------
# Every site earns its place
# ---------------------------------------------------------------------------

def reading(seam):
    """Everything one scan concluded, in a shape two scans can be compared in.

    `Seam` has no `__eq__`, so comparing the objects would compare identity and
    every check below would pass without looking at anything.
    """
    return (sorted(seam.owners), seam.dead, seam.local_only, sorted(seam.local),
            sorted((n, sorted(w)) for n, w in seam.used.items()))


def test_every_site_it_names_is_a_site_it_reads():
    """`SITES` and `scan()`, held to each other.

    Every test in this section switches one site off by name. A misspelt name
    is the quiet kind of wrong: `drop=('statc',)` switches nothing off, the
    scan comes back identical, and a test asserting a difference of nothing
    passes for the wrong reason. So the two lists have to match both ways.

    And each name has to change something. A site that has gone blind -- a
    regex that stopped matching, a directory that moved -- contributes no
    evidence, which from here is indistinguishable from not being there. What
    it changes is deliberately not asserted here; that is each site's own test
    below, and the direction is not even the same for all six. Dropping a
    reader removes evidence, while dropping `comments` adds it, because an
    export list read as code exports the English inside it.
    """
    consulted = set(re.findall(r"on\('(\w+)'\)", inspect.getsource(scan)))
    assert consulted == set(SITES)

    base = reading(SEAM)
    for site in SITES:
        assert reading(scan(drop=(site,))) != base, site


def test_static_imports_are_load_bearing():
    # By a distance the biggest site. Nothing subtle to assert here beyond the
    # scale of what would break without it.
    assert len(dead_without('static')) >= 80


def test_dynamic_imports_are_load_bearing():
    # `tests/smoke.test.js` pulls the shot map in mid-test rather than at the
    # top, because it only wants it once the DOM shim is standing up.
    assert local_only_without('dynamic') == {'shotMapSvg'}


def test_namespace_imports_are_load_bearing():
    # `tests/video.test.js` imports nine modules wholesale and reaches names off
    # the namespace object. Miss this and a third of the seam looks unwanted.
    assert dead_without('namespace') == PRODUCT_DEAD
    assert len(local_only_without('namespace')) >= 30


def test_stripping_comments_is_load_bearing():
    # `assets/db.js` explains inside its own `export { ... }` list why two names
    # moved out of the file. Read that comment as code and the word `and`
    # becomes an export -- a name declared nowhere, reachable by nobody, and
    # reported for the rest of time.
    assert local_only_without('comments') == {'and'}


def test_the_test_harness_is_load_bearing():
    # The knob this whole file is built around. Forty verdicts turn on whether
    # `tests/*.js` are readers, and they are: an export the suite imports is an
    # export something depends on.
    assert dead_without('harness') == PRODUCT_DEAD
    assert len(local_only_without('harness')) >= 30


def test_local_use_is_what_separates_dead_from_merely_broad():
    # Not a use site -- a classifier. Switch it off and every name in the
    # local-only list is reported dead, which is the honest reading of "nothing
    # outside this file wants it" and a useless one to act on.
    assert set(scan(drop=('local',)).dead) == set(SEAM.dead) | LOCAL_ONLY_NAMES


def test_switching_everything_off_leaves_nothing_standing():
    # The far end of the anti-vacuum guard: if the sites are genuinely what the
    # verdicts rest on, removing all of them must condemn the whole seam. A scan
    # that still finds readers with every reader switched off is finding them
    # somewhere this file does not describe.
    blind = scan(drop=('static', 'dynamic', 'namespace', 'local', 'harness'))
    assert blind.used == {}
    assert len(blind.dead) == len(blind.owners) >= 280
