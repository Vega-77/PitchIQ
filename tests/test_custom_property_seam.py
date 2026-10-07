"""Every token is used, and every token used is declared.

Twenty-seven custom properties carry the entire look of this site -- every
colour, every radius, the two font stacks, the shell width -- and until this
file nothing checked them at all. `grep -rn 'custom.propert' tests/` returned
nothing.

The seam rots in two directions and, as everywhere else in this repo, they are
not the same kind of problem:

  * A token declared and never used is a value nobody can reach. Harmless in
    ones, and it accumulates the way dead CSS does: the component is rewritten,
    the token stays because deleting a colour without proof it is unread feels
    like tempting fate. `--mark-quiet` was born the week this file was written
    and would have been the first entry had it not been wired up the same day.

  * A token used and never declared is the quiet one, and it is the reason this
    file exists. `var(--nope)` with no fallback renders as nothing at all, which
    is usually visible. `var(--nope, 0)` renders as `0`, which is not:

        td.bar-cell .cell-bar { width: calc(var(--w, 0) * 100%); }

    `--w` is the one token in this repo declared outside CSS -- `coach/coach.js`
    writes it per row with `setProperty`. If that call is ever renamed away, the
    fallback catches it silently and every bar in the table renders at width
    zero. No error in the console, no unstyled flash, no exception. A cell of
    empty space where a number used to be, on a page whose whole job is showing
    numbers. This gate is the only thing that can make that loud.

Both directions hold at zero today, which is exactly when a check is worth
pinning. A gate written over a mess only records the mess.

The scanner
-----------

Five sites. Each is proved load-bearing by a test below rather than asserted to
be useful, and the proof is of one of two kinds, because a site can be inert in
two quite different ways:

  * it finds names some other site also finds -- the delta tests, which switch
    one site off and pin what goes wrong
  * it finds nothing whatever -- which no delta can distinguish from the first,
    since both show zero. Only the second kind is safe to delete.

Telling those apart needed a separate probe of what each site finds in absolute
terms, and it changed the roster in both directions:

    css-decl    --x: in the eight stylesheets                 27 hits
    css-use     var(--x) in the eight stylesheets            ~700 hits
    js-setprop  setProperty('--x', ...)                         1 hit  --w
    js-use      var(--x) written as a string in JS             1 hit  --line
    comments    stripped before either of the above runs       1 hit  --accent

The last two are invisible to the delta tests -- switch either off and the
counts do not move -- and both are load-bearing anyway:

`js-use` was not in the scanner at all until the probe found `coach/coach.js`
setting `kit.style.background = 'var(--line)'` for a kit swatch with no sampled
colour. `--line` has a hundred and eighteen other uses, so nothing was reported
wrong; a token used *only* that way would have been reported dead. That is the
loud direction, and this repo has paid for it once already -- the id seam's
scanner could not see `tests/smoke.test.js`, called three live calibrate buttons
dead, and a session deleted their ids on its word. A false "dead" verdict from
an audit tool costs more than no tool, because it gets acted on.

`comments` is the other direction, the silent one, and the single hit is almost
too good: the comment above `.shot-mark:focus-visible` in `assets/app.css` reads
"Not `var(--accent)`, which is what this rule said first". A scanner that does
not strip comments counts the sentence explaining a token's *absence* as
evidence of its presence. It cost nothing here because `--accent` has a hundred
and thirteen real uses, but the shape is exactly how a genuine ghost gets
hidden -- and the sister measurement to this one lost a whole declaration that
way, when the prose `--text-faint: a hairline pitch outline...` inside a comment
was picked up as a declaration and, being last, replaced the real one.

Three sites were probed, found to scan nothing whatever, and deleted. Their
measurements survive as tripwires in `TestNothingScansTheseSurfaces` -- the move
this repo already made for the `css` site in the data seam. A tripwire is not
the same claim as a site: it says this surface is empty *and must stay empty*,
so the day someone declares a token in a `style=` attribute the gate says to
come back and write the site rather than silently missing it.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CSS = ['assets/app.css', 'assets/landing.css', 'coach/coach.css',
       'player/player.css', 'live-tagging/tagging.css', 'halftime/halftime.css']
HTML = ['index.html', 'coach/index.html', 'player/index.html',
        'live-tagging/index.html', 'halftime/index.html']
JS_DIRS = ['assets', 'coach', 'player', 'live-tagging', 'halftime']

SITES = frozenset({'css-decl', 'css-use', 'js-setprop', 'js-use', 'comments'})

# `--x:` where the `--` is not the tail of something else. The left boundary
# matters: without it `calc(100%--x: )` and any hyphenated word ending in a
# custom property spelling would match.
DECL = re.compile(r'(?<![\w-])(--[a-z][a-z0-9-]*)\s*:')
# The second group is the fallback comma, and it is the whole point of
# `TestAGhostWithAFallbackIsTheSilentOne` below.
USE = re.compile(r'var\(\s*(--[a-z][a-z0-9-]*)\s*(,)?')
SETPROP = re.compile(r"setProperty\(\s*['\"](--[a-z][a-z0-9-]*)['\"]")

BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)
# Not a scheme's `//`, not one inside a quoted string, not an escaped one.
LINE_COMMENT = re.compile(r'(?<![:\'"\\])//[^\n]*')


def js_files():
    out = []
    for d in JS_DIRS:
        out += sorted(str(p.relative_to(ROOT)).replace('\\', '/')
                      for p in (ROOT / d).glob('*.js'))
    return out


def harness_js_files():
    return sorted(str(p.relative_to(ROOT)).replace('\\', '/')
                  for p in (ROOT / 'tests').glob('*.js'))


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def uncomment(src, *, line=False):
    src = BLOCK_COMMENT.sub('', src)
    return LINE_COMMENT.sub('', src) if line else src


# ------------------------------------------------------------------ the scan

def declared(*, off=frozenset()):
    """{token: [where]} for every token this repo declares."""
    out = {}
    if 'css-decl' not in off:
        for rel in CSS:
            src = read(rel) if 'comments' in off else uncomment(read(rel))
            for m in DECL.finditer(src):
                out.setdefault(m.group(1), []).append(rel)
    if 'js-setprop' not in off:
        for rel in js_files():
            src = read(rel) if 'comments' in off else uncomment(read(rel),
                                                                line=True)
            for m in SETPROP.finditer(src):
                out.setdefault(m.group(1), []).append(rel)
    return out


def used(*, off=frozenset()):
    """{token: [(where, has_fallback)]} for every token this repo reads."""
    out = {}
    if 'css-use' not in off:
        for rel in CSS:
            src = read(rel) if 'comments' in off else uncomment(read(rel))
            for m in USE.finditer(src):
                out.setdefault(m.group(1), []).append((rel, bool(m.group(2))))
    if 'js-use' not in off:
        for rel in js_files():
            src = read(rel) if 'comments' in off else uncomment(read(rel),
                                                                line=True)
            for m in USE.finditer(src):
                out.setdefault(m.group(1), []).append((rel, bool(m.group(2))))
    return out


def dead(**kw):
    return set(declared(**kw)) - set(used(**kw))


def ghost(**kw):
    return set(used(**kw)) - set(declared(**kw))


# ------------------------------------------------------------------ the seam

# Written from script with `style.setProperty`, never declared in a sheet.
JS_WRITTEN = {'--w', '--tab-x', '--tab-w', '--reveal-i', '--px', '--py',
              '--mx', '--my', '--sw-a', '--sw-b'}


class TestTheSeamHoldsInBothDirections:
    """The two halves, and neither is allowed to be the excuse for the other."""

    def test_every_declared_token_is_used(self):
        assert dead() == set()

    def test_every_used_token_is_declared(self):
        assert ghost() == set()

    def test_the_count_is_what_the_theme_says_it_is(self):
        # Not a magic number for its own sake: it is the assertion that the
        # scanner reached the theme block rather than some subset of it.
        # Fifty-one since the School Colours redesign (2026-10-07): the kit
        # and the tokens that came with it, and the ten written from script.
        assert len(declared()) == 51, sorted(declared())

    def test_the_tokens_declared_outside_css(self):
        # `--w` is a bar's width. The other nine are motion and the colour
        # picker, each measured or chosen at run time: where the active tab
        # sits (`--tab-x`, `--tab-w`), a block's place in the order it rises
        # in (`--reveal-i`), the pointer over a header (`--px`, `--py`) and
        # over a card (`--mx`, `--my`), and a preset swatch's two colours
        # (`--sw-a`, `--sw-b`).
        css = {t for t, w in declared().items() if all(x in CSS for x in w)}
        assert set(declared()) - css == JS_WRITTEN


class TestAGhostWithAFallbackIsTheSilentOne:
    """`var(--gone)` shows up as a missing paint. `var(--gone, 0)` shows up as
    a correct-looking zero, and this is the only place that can say so."""

    def test_no_ghost_hides_behind_a_fallback(self):
        u = used()
        hidden = {t: u[t] for t in ghost() if any(f for _, f in u[t])}
        assert hidden == {}

    def test_the_bar_width_token_still_has_both_halves(self):
        # The case the paragraph in the docstring is about. Both halves have to
        # be here for the silence to be possible, so both are pinned: if the
        # fallback goes the browser gets loud on its own and this gate is no
        # longer the only witness; if the writer goes the bars go to zero.
        u = used()
        assert any(f for _, f in u['--w']), 'var(--w) lost its fallback'
        assert declared()['--w'] == ['coach/coach.js']


# ------------------------------------------------------- each site earns itself

class TestEachScanSiteIsLoadBearing:
    """Five sites, and a guard that cannot fire is dead machinery.

    Three of them move the verdict when switched off, and those are pinned by
    the delta. Two do not, and are pinned by what they find in absolute terms
    instead -- a distinction the delta alone cannot make, since a site finding
    nothing and a site finding what another site also finds both read as zero.
    """

    def test_css_decl_is_where_the_theme_lives(self):
        assert ghost(off={'css-decl'}) - ghost() == set(declared()) - JS_WRITTEN
        assert len(ghost(off={'css-decl'})) == 41

    def test_css_use_is_where_the_reading_happens(self):
        # Everything but --line, which coach.js also reads.
        assert len(dead(off={'css-use'})) == 50
        assert '--line' not in dead(off={'css-use'})

    def test_js_setprop_is_the_only_writer_outside_css(self):
        assert ghost(off={'js-setprop'}) - ghost() == JS_WRITTEN

    def test_js_use_finds_a_use_no_other_site_can_see(self):
        # Inert by delta -- --line has a hundred and eighteen uses in CSS -- and
        # kept because the thing it finds is a real use in a served file. Drop
        # the site and a token read only from JS reads as dead, which is the
        # direction that gets acted on.
        hits = {t: w for t, w in used().items()
                if any(rel.endswith('.js') for rel, _ in w)}
        assert set(hits) == {'--line'}
        assert [rel for rel, _ in hits['--line'] if rel.endswith('.js')] \
            == ['coach/coach.js']
        assert dead(off={'js-use'}) == dead()

    def test_comments_are_stripped_before_anything_is_counted(self):
        # Inert by delta and load-bearing in absolute: one comment in this repo
        # contains a `var()`, and it is a comment whose sentence is that the
        # token is NOT used there.
        phantom = []
        for rel in CSS:
            for chunk in BLOCK_COMMENT.findall(read(rel)):
                phantom += [(rel, m.group(1)) for m in USE.finditer(chunk)]
                phantom += [(rel, m.group(1)) for m in DECL.finditer(chunk)]
        assert phantom == [('assets/app.css', '--accent')], phantom
        assert ghost(off={'comments'}) == ghost()

    def test_the_site_list_is_the_one_the_scanner_reads(self):
        # An entry here that no `off=` branch consults is a site that was
        # deleted from the scanner and left in the list.
        src = Path(__file__).read_text(encoding='utf-8')
        for site in SITES:
            # `comments` is consulted the other way round -- it strips rather
            # than adds, so its branch reads `in off`, not `not in off`.
            reads = (src.count("'%s' not in off" % site)
                     + src.count("'%s' in off" % site))
            assert reads >= 1, site


class TestNothingScansTheseSurfaces:
    """Three deleted sites, kept as the measurement that justified deleting them.

    Each was in the scanner, each was probed, and each was found to scan nothing
    whatever -- not "nothing another site did not also find", which would be a
    reason to keep it. A tripwire is the cheaper shape for that: it costs one
    regex pass instead of a branch in every scan, and it fails the day the
    surface stops being empty, which is the day the site is worth writing.
    """

    def test_no_html_file_declares_a_custom_property(self):
        found = [(rel, m.group(1)) for rel in HTML
                 for m in DECL.finditer(read(rel))]
        assert found == [], 'write the style-attr site: %r' % found

    def test_no_html_file_reads_a_custom_property(self):
        found = [(rel, m.group(1)) for rel in HTML
                 for m in USE.finditer(read(rel))]
        assert found == [], 'write the html-use site: %r' % found

    def test_no_js_file_declares_a_custom_property_outside_setproperty(self):
        found = [(rel, m.group(1)) for rel in js_files()
                 for m in DECL.finditer(uncomment(read(rel), line=True))]
        assert found == [], 'write the js-template site: %r' % found

    def test_no_test_harness_touches_a_custom_property(self):
        # The id seam keeps its harness site because `tests/smoke.test.js`
        # genuinely reaches into the markup by id. Nothing in `tests/` reaches
        # into the theme, so here the same surface is a tripwire instead.
        found = [(rel, m.group(1)) for rel in harness_js_files()
                 for rx in (DECL, USE) for m in rx.finditer(read(rel))]
        assert found == [], 'write the harness site: %r' % found

    def test_no_javascript_comment_mentions_a_custom_property(self):
        # The JS half of the comment site, which the CSS half already earns.
        found = []
        for rel in js_files():
            src = read(rel)
            for chunk in BLOCK_COMMENT.findall(src) + LINE_COMMENT.findall(src):
                found += [(rel, m.group(1)) for rx in (DECL, USE)
                          for m in rx.finditer(chunk)]
        assert found == [], found


# ------------------------------------------------------------- anti-vacuum

class TestTheScannerCanSeeTheRepo:
    """Every assertion above is either "this set is empty" or a count, and a
    scanner that reads nothing satisfies most of them at once. These are the
    ones that fail when it reads nothing."""

    def test_every_file_it_claims_to_read_exists(self):
        for rel in CSS + HTML:
            assert (ROOT / rel).exists(), rel
        assert len(js_files()) > 25, len(js_files())
        assert len(harness_js_files()) > 5, len(harness_js_files())

    def test_the_stylesheets_are_actually_full_of_var(self):
        u = used()
        assert sum(len(w) for w in u.values()) > 600
        assert len(u) == 51, sorted(u)

    def test_the_busiest_tokens_are_still_the_busiest(self):
        # Ordering, not exact counts: the counts move whenever a rule is added
        # and pinning them would make this file fail for the wrong reason.
        u = {t: len(w) for t, w in used().items()}
        assert u['--text-faint'] > u['--text-dim'] > u['--text']
        assert u['--line'] > u['--line-hi'] > u['--card-red']

    @pytest.mark.parametrize('token', ['--bg', '--accent', '--mark-quiet'])
    def test_a_token_removed_from_the_theme_would_be_seen(self, token):
        assert token in declared()
        assert token in used()
