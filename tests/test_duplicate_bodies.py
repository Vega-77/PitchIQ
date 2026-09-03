"""No two top-level functions in the site JS share a body.

The finding this was written for: a four-line SVG element factory existed six
times over -- `svgEl` in `assets/ui.js` and a private `el` in form-chart.js,
heatmap.js, pass-map.js, pitch-backdrop.js and shot-map.js -- and a stat-group
filler existed twice, as `coach/coach.js::fillPlayerGroup` and
`player/player.js::fillGroup`. Seven copies, byte-identical bodies.

Identical bodies are not the harm on their own. The harm is what happens next.
Three of the six factories took `attrs` with no default, so `el('title')` threw
on them and three call sites had to pass `{}` to a helper whose entire job was
to save them that typing. `assets/form-chart.js` records the other half in its
own docstring: the same copy-paste spread a broken `node.append(el(...))` chain
through every chart in the repo, and exactly one of them kept the bug. Copies
drift, and they drift silently, because nothing is comparing them.

So this counts them, and the count is zero.

**There is no allowlist, on purpose.** `tests/test_data_seam.py` makes the
argument: a gate written over a mess only records the mess, and the moment to
pin a seam is when both directions are already at zero. They are -- the seven
copies were consolidated into `assets/svg.js` and `assets/ui.js::fillStatGroup`
before this file was written. An allowlist here would be a list of duplicates
somebody had decided to keep, which is the graveyard `tests/test_call_graph.py`
warns about. A duplicate is a finding; there is nowhere to write one down.

Scope, stated plainly so the next reader does not over-trust the number:

* Top-level `function` declarations only. Arrow-function consts, class methods
  and nested closures are out. `assets/form-chart.js::titled` would not be seen
  by a scan of any shape here, because it exists once.
* Byte-identical bodies after whitespace normalisation. Two functions that
  differ by one renamed local are not duplicates to this test, and a determined
  copy-paste that renames as it goes escapes. That is the honest limit: this
  catches the copy nobody edited, which is the copy that drifts.
* The body is taken from the **source**, not from `blank()`'s output, so two
  functions whose bodies differ only inside a string literal stay distinct.
"""
import re
from collections import defaultdict

from test_contrast_floor import JS_DIRS, ROOT
from test_free_names import blank

# `export`/`async` are optional prefixes; anything else in front of `function`
# (a `const x = function`, a method shorthand) is out of scope by design.
DECL = re.compile(r'^(?:export )?(?:async )?function (\w+)\s*\(', re.M)

# Measured on the tree this file was written against: 565 functions across 31
# files, every directory contributing at least fifteen. The floors sit under
# those so an ordinary deletion does not trip them, and far enough under a
# collapsed scan that one could not pass quietly.
MIN_FUNCTIONS = 500
MIN_FILES = 28
MIN_PER_DIR = 10

# 67 of the 565 declare a `{` inside the parameter list -- a destructured or
# defaulted-object parameter. That number is the whole reason the extractor
# below is written the way it is, and it is pinned so the guard against the
# naive version cannot go vacuous.
MIN_BRACED_PARAMS = 40


def close(text, start, opener, closer):
    """The index of the delimiter matching the one at `start`."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
            if depth == 0:
                return i
    raise AssertionError('unbalanced %s at offset %d' % (opener, start))


def bodies_in(src):
    """`[(name, body, params)]` for every top-level function in `src`.

    Offsets come from `blank(src)` -- comments, strings and regex literals
    blanked space-for-space -- so a brace inside a string cannot be mistaken
    for structure. The text handed back is sliced out of the real source at
    those offsets.

    The parameter list is skipped explicitly, and that is not fussiness. The
    obvious version takes the first `{` after the match and calls it the body,
    which for `svgEl(name, attrs = {})` or `passMapSvg(network, { nameOf } = {})`
    is the *parameter's* brace: the extracted body comes out as `{}`, sixty-odd
    functions collapse onto one key, and the scan reports a vast fake duplicate
    group while hiding every real one behind it. This was not hypothetical --
    it happened, it reported a 39-member group of unrelated functions, and it
    concealed the `fillPlayerGroup`/`fillGroup` pair the fixed version found.
    """
    blanked = blank(src)
    found = []
    for m in DECL.finditer(blanked):
        rparen = close(blanked, m.end() - 1, '(', ')')
        params = blanked[m.end() - 1:rparen + 1]
        lbrace = blanked.index('{', rparen)
        rbrace = close(blanked, lbrace, '{', '}')
        body = re.sub(r'\s+', ' ', src[lbrace:rbrace + 1]).strip()
        found.append((m.group(1), body, params))
    return found


def scan():
    """`(functions, per_dir)` where a function is `(where, name, body, params)`."""
    functions = []
    per_dir = defaultdict(int)
    for name in JS_DIRS:
        for path in sorted((ROOT / name).glob('*.js')):
            src = path.read_text(encoding='utf-8')
            where = '%s/%s' % (name, path.name)
            for fn, body, params in bodies_in(src):
                functions.append((where, fn, body, params))
                per_dir[name] += 1
    return functions, per_dir


FUNCTIONS, PER_DIR = scan()


def sources():
    """Every site JS file as `{rel: text}`."""
    out = {}
    for name in JS_DIRS:
        for path in sorted((ROOT / name).glob('*.js')):
            out['%s/%s' % (name, path.name)] = path.read_text(encoding='utf-8')
    return out


SOURCES = sources()


# ------------------------------------------------------------ the finding

def test_no_two_functions_share_a_body():
    groups = defaultdict(list)
    for where, fn, body, _ in FUNCTIONS:
        groups[body].append('%s::%s' % (where, fn))
    repeated = {body: names for body, names in groups.items()
                if len(names) > 1}
    lines = ['%s\n      %s' % (' | '.join(sorted(names)), body[:200])
             for body, names in sorted(repeated.items(),
                                       key=lambda kv: -len(kv[0]))]
    assert not repeated, (
        'these functions have identical bodies -- move the shared one into a '
        'module both can import:\n  ' + '\n  '.join(lines))


# ------------------------------------------- the extractor, held honest

def test_no_body_extracts_as_empty():
    """`{}` is the naive extractor's signature failure, not a real body."""
    empty = ['%s::%s' % (where, fn)
             for where, fn, body, _ in FUNCTIONS if body == '{}']
    assert not empty, (
        'extracted an empty body for %s -- the parameter list was almost '
        'certainly mistaken for it' % ', '.join(empty))


def test_every_body_is_a_brace_pair():
    bad = ['%s::%s -> %r' % (where, fn, body[:60])
           for where, fn, body, _ in FUNCTIONS
           if not (body.startswith('{') and body.endswith('}'))]
    assert not bad, 'not a brace-delimited body: ' + '; '.join(bad)


def test_braced_parameter_lists_are_common():
    """Without these the guard above would pass on a broken extractor too."""
    braced = [(where, fn) for where, fn, _, params in FUNCTIONS
              if '{' in params]
    assert len(braced) >= MIN_BRACED_PARAMS, (
        'only %d functions declare a brace in the parameter list, under the '
        'floor of %d -- either the tree changed a great deal or the parameter '
        'list is no longer being read' % (len(braced), MIN_BRACED_PARAMS))


def test_extractor_steps_over_a_defaulted_parameter():
    src = 'export function f(name, attrs = {}) {\n    return attrs;\n}\n'
    assert bodies_in(src) == [('f', '{ return attrs; }', '(name, attrs = {})')]


def test_extractor_steps_over_a_destructured_parameter():
    src = 'function g(a, { b } = {}, c) {\n    return b;\n}\n'
    assert bodies_in(src)[0][1] == '{ return b; }'


def test_extractor_ignores_a_brace_inside_a_string():
    src = 'function h() {\n    return "}";\n}\nfunction i() {\n    return 1;\n}\n'
    assert [name for name, _, _ in bodies_in(src)] == ['h', 'i']
    assert bodies_in(src)[0][1] == '{ return "}"; }'


def test_bodies_come_from_the_source_not_the_blanked_text():
    """Two functions differing only inside a string are not duplicates."""
    src = ('function a() {\n    return "one";\n}\n'
           'function b() {\n    return "two";\n}\n')
    found = bodies_in(src)
    assert found[0][1] != found[1][1], (
        'string contents were blanked out of the body, which would make every '
        'pair of otherwise-identical functions look like a duplicate')


def test_the_scan_can_still_see_a_duplicate():
    """The gate above passes today; prove it is not passing vacuously."""
    src = ('function a() {\n    return 1;\n}\n'
           'function b() {\n    return 1;\n}\n')
    found = bodies_in(src)
    assert found[0][1] == found[1][1] == '{ return 1; }'


# --------------------------------------- where the shared copies now live

def test_one_place_makes_an_svg_element():
    """`document.createElementNS` exists once, in assets/svg.js."""
    holders = sorted(rel for rel, src in SOURCES.items()
                     if 'createElementNS' in blank(src))
    assert holders == ['assets/svg.js'], (
        'the SVG element factory is back in more than one file: %s'
        % ', '.join(holders))


def test_everyone_who_uses_the_factory_imports_it():
    users = sorted(rel for rel, src in SOURCES.items()
                   if 'svgEl' in blank(src) and rel != 'assets/svg.js')
    missing = [rel for rel in users
               if not re.search(r"import \{[^}]*svgEl[^}]*\} from "
                                r"'\.\./?assets?/?svg\.js", SOURCES[rel])
               and not re.search(r"import \{[^}]*svgEl[^}]*\} from "
                                 r"'\./svg\.js", SOURCES[rel])]
    assert users, 'nothing imports the SVG factory, which cannot be right'
    assert not missing, (
        '%s names svgEl without importing it from assets/svg.js'
        % ', '.join(missing))


def test_one_place_reads_todays_date():
    """`localDate` had been written twice; `getFullYear` says whether it is."""
    holders = sorted(rel for rel, src in SOURCES.items()
                     if 'getFullYear' in blank(src))
    assert holders == ['assets/ui.js'], (
        "today's date is being formatted in more than one file: %s"
        % ', '.join(holders))


# ----------------------------------------------------------- anti-vacuum

def test_enough_functions_were_scanned():
    assert len(FUNCTIONS) >= MIN_FUNCTIONS, (
        'only %d top-level functions found, under the floor of %d -- the '
        'declaration pattern has probably stopped matching'
        % (len(FUNCTIONS), MIN_FUNCTIONS))


def test_enough_files_were_scanned():
    files = {where for where, _, _, _ in FUNCTIONS}
    assert len(files) >= MIN_FILES, (
        'only %d files contributed a function, under the floor of %d'
        % (len(files), MIN_FILES))


def test_every_directory_was_reached():
    thin = {name: PER_DIR.get(name, 0) for name in JS_DIRS
            if PER_DIR.get(name, 0) < MIN_PER_DIR}
    assert not thin, (
        'these directories contributed almost nothing, so the scan is not '
        'reaching them: %s' % thin)
