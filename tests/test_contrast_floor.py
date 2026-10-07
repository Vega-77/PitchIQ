# -*- coding: utf-8 -*-
"""Nothing on either medium falls under the floor WCAG puts on it.

Every colour pair in this site is derived from the stylesheets here and scored
on the spot. Nothing is listed. The previous measurement carried its pairs as a
literal and went on reporting a `--line-hi` defect for weeks after `--line-hi`
had stopped being ink anywhere -- a scanner that remembers what it found last
time is not measuring, it is reciting.

Three kinds of ink, because they do not share a floor:

  * text -- a `color:` that ends up as glyphs, floor 4.5 (1.4.3). Everything on
    this site is normal text at that threshold: `html { font-size: 17px }` is
    deliberate, and the smallest type on the site, `.cm-label` at 0.7rem, still
    comes to 11.9px, nowhere near the 18.66px bold / 24px that would earn the
    large-text 3.0.
  * graphical -- `fill:`, `stroke:`, and a `background:` on an element that
    sizes itself. Floor 3.0 (1.4.11, non-text contrast). A bar IS the datum, so
    its contrast is not decoration.
  * a `color:` consumed as `currentColor` by an SVG painter, which is graphical
    ink wearing a text declaration. Three rules do this and they matter: at 4.14
    they pass 3.0 and fail 4.5, so which floor applies decides the verdict.

`background:` being ink at all is the hole this file was written to close. The
pair measurement treated every background as a ground and nothing else, so it
could not see the one family it most needed to -- a 3px tick, a bar half, a
legend swatch. Scoring those as grounds scored them as the thing they sit on
rather than the thing they are. Turning that scan on immediately found the tab
hover underline at 1.49 against its own surface: a hover affordance nobody can
perceive, which is the same as not having one. It is `--mark-quiet` now.

Which way to be wrong. A floor set too high fails loudly on a colour that is
fine, and someone lightens a token that did not need lightening -- annoying,
visible, self-correcting. A floor waived too readily is silent: the exemption
swallows a real defect and no one hears about it again. So every exemption below
is proved twice over -- that its site still exists, and that removing it would
change a verdict. An exemption that changes nothing is not a kindness, it is a
hole waiting for something to fall through.

The four exemptions, each derived rather than declared:

  * halo -- a `stroke:` painted in one of the four page grounds is a separator
    ring, not a mark. `.pass-dot` and `.form-dot` both stroke `--surface` around
    an `--accent` fill so the dot detaches from whatever is behind it. They
    score 1.00, and the 1.00 is the entire point of them.
  * hairline -- a background mark one CSS pixel thick in either dimension is a
    rule, not a datum. `.record-sep` (1x18) and `.tally-sep` (10x1) divide; they
    encode nothing, and a divider loud enough to clear 3.0 is a divider that has
    started competing with the numbers it separates.
  * print-hidden -- scoring a rule against a medium it never renders in is the
    too-wide failure this repo has already paid for once, when `--card-yellow`
    was flagged at 1.54 on paper for a rule living in a stylesheet no printable
    page loads. The trap is set again by `.steps li::before`, whose counters
    really do score 4.23 on white, except `#getting-started` is in the print
    hide list so they are never on the paper. Derived, not listed: the print
    block names what it hides, an id it hides takes its subtree with it, and a
    class every one of whose occurrences sits inside such a subtree is hidden
    too. That last clause is what keeps `.card` -- on the hidden section, and on
    sixteen printed ones -- out of the exemption.
  * currentColor -- the three pitch maps, above.

Paper is scored over the three stylesheets a printable page loads, and no
others.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Every sheet the site loads. `landing.css` was missing from this list while
# both sibling gates carried it, so the front page -- the one page a stranger
# is guaranteed to see -- was scored by no contrast test at all.
SHEETS = ['assets/app.css', 'assets/landing.css', 'coach/coach.css',
          'player/player.css', 'live-tagging/tagging.css',
          'halftime/halftime.css']
# Only the coach and player views ever call window.print(). A page nobody
# prints cannot have a paper defect, and scoring one is how a scanner invents
# work for somebody.
PRINTABLE = ['assets/app.css', 'coach/coach.css', 'player/player.css']
HTML = ['index.html', 'coach/index.html', 'player/index.html',
        'live-tagging/index.html', 'halftime/index.html']
# The only two pages that call window.print(). A paper question asked of
# any other page is a question about a medium that page never reaches --
# and asking it wrongly is not harmless: `.brand` sits inside the hidden
# topbar on both of these and outside it on the tagging page, so widening
# the universe by five files turned a hidden class into a false defect.
PRINTS = ['coach/index.html', 'player/index.html']
JS_DIRS = ['assets', 'coach', 'player', 'live-tagging', 'halftime']

# The four colours a block can land on. Anything that sets no background of its
# own inherits one of these, and is scored against the worst.
PAGE = ['--bg', '--bg-raised', '--surface', '--surface-hi']
# Grounds a sized element can paint without thereby becoming a mark: the page
# colours plus `--line`, which is what a divider or a track is drawn in.
GROUND_TOKENS = set(PAGE) | {'--line'}
# Literal colours a sized element may paint as ground rather than as a mark.
# Empty, and kept as the place a new one would have to be argued for. Black
# was here for the calibration stage and its loupe, and white was here before
# that for the xG sandbox's toggle knob; both pages left the site on
# 2026-10-07, and no sized element paints a bare colour any more. A waiver
# covering something that never needed it is how one grows until something
# real falls through. The universe is pinned next door.
GROUND_LITERALS = frozenset()

# A class the printable markup never contains because a script makes it, inside
# an element the print theme hides. The ancestry derivation below reads markup,
# which in a JS-rendered app is the static shell and very little else: 276 of
# the classes scored against paper appear in no printable markup at all, and
# `.job-count` and `.staff-initial` are two of them — both real defects. So
# absence cannot be allowed to imply hidden. Only a named host can, and the
# claim is checked against the markup below rather than believed.
JS_HOSTS = {'chip': 'cv-review-filters'}

TEXT_FLOOR = 4.5
GRAPHIC_FLOOR = 3.0

# `color:` here is SVG paint, not glyphs -- the painters draw in currentColor.
CURRENT_COLOR = ['.heatmap', '.shot-map', '.pass-map']

COLOR = re.compile(r'(?<![-\w])color\s*:\s*([^;}]+)')
BG = re.compile(r'(?<![-\w])background(?:-color)?\s*:\s*([^;}]+)')
PAINT = re.compile(r'(?<![-\w])(fill|stroke)\s*:\s*([^;}]+)')
VAR = re.compile(r'var\(\s*(--[a-z][a-z0-9-]*)')
DECL = re.compile(r'(--[a-z][a-z0-9-]*)\s*:\s*([^;]+);')
# `inset` is deliberately absent. It says "as big as the thing I am in",
# which is the container's answer, not a mark's — a mark states its size
# because being that size is the datum. Counting it scored a fullscreen scrim
# as though its dimness were a measurement, at 1.06. None of the five uses in
# the tree declare a size; they all position against an ancestor.
SIZED = re.compile(r'(?<![-\w])(width|height|flex-basis|flex)\s*:')
HAIRLINE = re.compile(r'(?<![-\w])(width|height)\s*:\s*1px\s*[;}]')
CLASS_ATTR = re.compile(r'class="([^"]*)"')


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def strip_comments(s):
    return re.sub(r'/\*.*?\*/', '', s, flags=re.S)


def blocks(src):
    """(selector, declarations) per rule, flattening at-rule wrappers.

    Media and supports queries wrap real rules, and the rules inside them are
    exactly as capable of a contrast defect as the ones outside. Recursing is
    what lets the print theme and the hover block be scored at all.
    """
    src = strip_comments(src)
    out, i, start = [], 0, 0
    while True:
        o = src.find('{', i)
        if o < 0:
            return out
        depth, j = 1, o + 1
        while depth and j < len(src):
            depth += (src[j] == '{') - (src[j] == '}')
            j += 1
        sel, body = src[start:o].strip(), src[o + 1:j - 1]
        if '{' in body:
            out.extend(blocks(body))
        else:
            out.append((sel, body))
        i = start = j


def theme(printed=False):
    """The token table as the browser would resolve it, screen or paper."""
    app = read('assets/app.css')
    head = strip_comments(app[app.index(':root {'):app.index('*, *::before')])
    t = {m.group(1): m.group(2).strip() for m in DECL.finditer(head)}
    if printed:
        pr = app[app.index('@media print {'):]
        body = strip_comments(pr[pr.index(':root {'):pr.index('@page')])
        t.update({m.group(1): m.group(2).strip() for m in DECL.finditer(body)})
    return t


def parse(v):
    """A hex or rgb() literal as (r, g, b, a), or None if it is neither."""
    v = (v or '').strip()
    if v.startswith('#'):
        h = v[1:]
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        if len(h) != 6:
            return None
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    m = re.match(r'rgba?\(([^)]*)\)$', v)
    if not m:
        return None
    p = [float(x) for x in m.group(1).replace('/', ',').split(',')]
    return (int(p[0]), int(p[1]), int(p[2]), p[3] if len(p) > 3 else 1.0)


def over(fg, bg):
    """`fg` composited onto opaque `bg`."""
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3)) + (1.0,)


def luminance(c):
    def channel(x):
        x /= 255.0
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
    return (0.2126 * channel(c[0]) + 0.7152 * channel(c[1])
            + 0.0722 * channel(c[2]))


def ratio(a, b):
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# --------------------------------------------------------------- print-hidden

def extent(src, i):
    """The index just past the block whose header starts at `i`.

    The loop has to see a brace open before a depth of zero means the end,
    or it returns after one character. It did, once, and the print hide list
    came back as a single `@` — which is the bug the assertion in
    `hidden_pattern` exists to catch on the way out.
    """
    depth, j, opened = 0, i, False
    while j < len(src):
        depth += (src[j] == '{') - (src[j] == '}')
        opened = opened or depth > 0
        if opened and depth == 0:
            return j + 1
        j += 1
    raise AssertionError('the block never closes')


def print_block():
    app = strip_comments(read('assets/app.css'))
    i = app.index('@media print {')
    return app[i:extent(app, i)]


def without_print(src):
    """`src` with the print theme cut out of it.

    `blocks()` flattens at-rules, so a rule inside `@media print` comes back
    indistinguishable from one outside it and gets scored twice: correctly
    against paper, and again against a screen theme it never meets. That is
    the exact mirror of scoring a screen-only rule against paper, and it
    invented a 1.17 on a `background: #fff` that exists only on paper.
    """
    src = strip_comments(src)
    i = src.find('@media print {')
    return src if i < 0 else src[:i] + src[extent(src, i):]


def hidden_selectors():
    """Every selector the print theme sets to `display: none`."""
    out = set()
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', print_block()):
        if not re.search(r'display:\s*none', m.group(2)):
            continue
        for sel in m.group(1).split(','):
            sel = sel.strip().splitlines()[-1].strip()
            if sel:
                out.add(sel)
    return out


def roots(src, selector):
    """Every element the print theme hides by this selector, as markup.

    Both `#id` and `.class` roots, because the print theme hides plenty of
    each, and a hidden `.topbar` takes the brand inside it just as surely
    as a hidden `#getting-started` takes its steps. Reading only the ids
    was the first version of this, and it scored three rules living under
    the topbar against paper they never reach -- the too-wide failure, in
    the tool built to avoid it.
    """
    if selector.startswith('#'):
        pat = r'<(\w+)[^>]*\bid="%s"' % re.escape(selector[1:])
    elif re.fullmatch(r'\.[\w-]+', selector):
        pat = (r'<(\w+)[^>]*\bclass="[^"]*(?<![\w-])%s(?![\w-])'
               % re.escape(selector[1:]))
    else:
        return []                 # a compound selector; nothing to walk
    return [subtree(src, m) for m in re.finditer(pat, src)]


def subtree(src, m):
    """The markup of a matched opening tag, its close tag included."""
    tag = m.group(1)
    opener = re.compile(r'<%s[\s>]' % tag)
    closer = re.compile(r'</%s>' % tag)
    void = re.match(r"<\w+[^>]*?/?>", src[m.start():])
    if void and void.group(0).endswith("/>"):
        return src[m.start():m.start() + void.end()]
    depth, j = 0, m.start()
    while j < len(src):
        o, c = opener.search(src, j), closer.search(src, j)
        if not c:
            break
        if o and o.start() < c.start():
            depth, j = depth + 1, o.end()
        else:
            depth, j = depth - 1, c.end()
            if depth == 0:
                return src[m.start():j]
    return src[m.start():]


def hidden_names():
    """Selector fragments that never reach paper.

    The print block's own list, plus every class that appears in the markup
    only inside a hidden element. A class used both inside and outside such a
    subtree is not hidden -- that is what keeps `.card`, which sits on the
    hidden `#getting-started` section and on sixteen printed blocks, from
    quietly exempting a sixth of the site.
    """
    listed = hidden_selectors()
    total, within = {}, {}
    for rel in PRINTS:
        src = read(rel)
        for m in CLASS_ATTR.finditer(src):
            for c in m.group(1).split():
                total[c] = total.get(c, 0) + 1
        for sel in listed:
            for block in roots(src, sel):
                for m in CLASS_ATTR.finditer(block):
                    for c in m.group(1).split():
                        within[c] = within.get(c, 0) + 1
    out = set(listed)
    out.update('.' + c for c, n in within.items() if n == total.get(c))
    return out


def hidden_pattern():
    names = hidden_names() | {'.' + c for c in js_hidden()}
    parts = [re.escape(n) for n in sorted(names)]
    # An empty alternation matches the empty string, so a hide list that came
    # back empty would not exempt nothing -- it would exempt everything, and
    # the paper scan would go quiet while still reporting success. That is the
    # exact failure this file was written to make impossible, so it is an
    # assertion rather than a comment.
    assert parts, 'the print hide list came back empty'
    return re.compile(r'(?<![\w.#-])(?:%s)(?![\w-])' % '|'.join(parts))


# ------------------------------------------------------------------- colours

def values(text):
    """The colours a declaration names: theme tokens, or a bare literal.

    Thirty-seven ink and ground declarations in this tree are written as
    literal hex rather than as tokens, and a scanner reading only `var()`
    cannot see any of them — including seven inks, one of which is the
    near-black the accent chips are lettered in. A literal is carried
    verbatim rather than by name because it is a colour the print theme
    cannot reach: that is the whole difference between the two kinds.
    """
    out = list(VAR.findall(text))
    v = text.strip().rstrip(';').strip()
    if not out and parse(v) is not None:
        out.append(v)
    return out


def colour(t, v):
    """`v` resolved against theme `t`, whether it is a token or a literal."""
    return parse(t.get(v)) if v.startswith('--') else parse(v)


def ancestor_ground(rules, sel):
    """The background a descendant selector inherits from its own prefix.

    `.ev.flash .ev-label` sets an ink and no ground of its own; the ground is
    on `.ev.flash`, one level up in the same sheet. Falling back to the page
    colours there scored near-black lettering against the dark page instead of
    against the bright accent it is really printed on, and reported 1.01 for
    one of the highest-contrast pairs on the site.

    The prefix may be declared in `app.css` rather than the sheet at hand:
    `.on-kit` and `.topbar` are painted in the team's shirt colour there, and
    `coach/coach.css` letters things inside them. `rules` is the sheet's own
    rules laid over app.css's, so a sheet can still repaint a shared prefix.
    """
    parts = sel.split()
    for k in range(len(parts) - 1, 0, -1):
        body = rules.get(' '.join(parts[:k]))
        if body is None:
            continue
        found = [g for m in BG.finditer(body) for g in values(m.group(1))]
        if found:
            return found[-1]
    return None


def js_hidden():
    """The `JS_HOSTS` classes whose host really is hidden, as bare names.

    A claim that does not check out exempts nothing. The host must exist in
    printable markup and must itself carry a hidden name, so the exemption
    rests on the same print hide list as every other one rather than on this
    table being right.
    """
    hidden, out = hidden_names(), set()
    for cls, host in JS_HOSTS.items():
        for rel in PRINTS:
            m = re.search(r'<\w+[^>]*\bid="%s"[^>]*>' % re.escape(host),
                          read(rel))
            if not m:
                continue
            names = {'#' + host}
            for a in CLASS_ATTR.finditer(m.group(0)):
                names.update('.' + c for c in a.group(1).split())
            if names & hidden:
                out.add(cls)
    return out


# ---------------------------------------------------------------- the scanner

def scored(sheets, *, printed, exempt=True):
    """Every (kind, floor, token, ground_or_None, where) this repo can be read
    to assert, with the exemptions already applied."""
    t = theme(printed)
    skip = hidden_pattern() if printed and exempt else None
    out = []

    def own_rules(rel):
        # On screen the print theme's rules do not exist, so they are cut out
        # rather than scored against a theme they never meet.
        src = read(rel) if printed else without_print(read(rel))
        found = {}
        for sel, body in blocks(src):
            found.setdefault(sel.splitlines()[-1].strip(), body)
        return src, found

    _, shared = own_rules('assets/app.css')
    for rel in sheets:
        src, mine = own_rules(rel)
        rules = {**shared, **mine}
        for sel, body in blocks(src):
            head = sel.splitlines()[-1].strip()
            where = '%s %s' % (rel, head[:44])
            if skip is not None and skip.search(sel):
                continue
            grounds = [g for m in BG.finditer(body)
                       for g in values(m.group(1))]
            ground = grounds[-1] if grounds else ancestor_ground(rules, head)
            is_map = any(sel.strip().startswith(c) for c in CURRENT_COLOR)

            for m in COLOR.finditer(body):
                for v in values(m.group(1)):
                    floor = GRAPHIC_FLOOR if is_map else TEXT_FLOOR
                    out.append(('text', floor, v, ground, where))

            for m in PAINT.finditer(body):
                prop, value = m.group(1), m.group(2)
                for v in values(value):
                    if prop == 'stroke' and v in PAGE:
                        continue          # halo: a ring in the ground colour
                    out.append(('paint', GRAPHIC_FLOOR, v, ground, where))

            if not SIZED.search(body) or HAIRLINE.search(body):
                continue                  # a container, or a 1px rule
            # A mark is read against what it is drawn on: the page, unless an
            # ancestor in its own selector paints a ground (the crest on the
            # kit-coloured bar).
            under = ancestor_ground(rules, head)
            for m in BG.finditer(body):
                for v in values(m.group(1)):
                    if v in GROUND_TOKENS or v in GROUND_LITERALS:
                        continue          # a sized container is still a ground
                    out.append(('mark', GRAPHIC_FLOOR, v, under, where))
    return t, out


def failures(sheets, *, printed, exempt=True):
    """Anything scoring under its own floor."""
    t, rows = scored(sheets, printed=printed, exempt=exempt)
    bad = []
    for kind, floor, v, ground, where in rows:
        fg = colour(t, v)
        if fg is None:
            continue
        bg = colour(t, ground) if ground is not None else None
        if bg is None:
            r = min(ratio(fg, parse(t[p])) for p in PAGE)
        elif bg[3] < 1:
            r = min(ratio(fg, over(bg, parse(t[p]))) for p in PAGE)
        else:
            r = ratio(fg, bg)
        if r < floor:
            bad.append((round(r, 2), floor, kind, v, ground, where))
    return sorted(bad)


class TestNothingFallsUnderItsFloor:
    def test_the_screen_is_clean(self):
        assert failures(SHEETS, printed=False) == []

    def test_the_paper_is_clean(self):
        assert failures(PRINTABLE, printed=True) == []

    def test_both_media_actually_scored_something(self):
        # A floor nothing is measured against is not a floor. The exemptions
        # below could in principle empty the table and every assertion above
        # would still pass in triumphant silence.
        for sheets, printed in ((SHEETS, False), (PRINTABLE, True)):
            _, rows = scored(sheets, printed=printed)
            kinds = {k for k, _, _, _, _ in rows}
            assert kinds == {'text', 'paint', 'mark'}, kinds
            assert len(rows) > 200, len(rows)


class TestTheHaloIsASeparatorNotAMark:
    """A stroke in a page ground exists to have no contrast."""

    def test_both_haloed_dots_are_still_stroked_in_the_ground(self):
        app = strip_comments(read('assets/app.css'))
        found = {sel.strip() for sel, body in blocks(app)
                 for m in PAINT.finditer(body)
                 if m.group(1) == 'stroke'
                 for tok in VAR.findall(m.group(2)) if tok in PAGE}
        assert found == {'.pass-dot', '.form-dot'}, found

    def test_the_exemption_changes_a_verdict(self):
        # Without it, `--surface` on `--surface` is scored as a mark at 1.00 --
        # the worst ratio the site can produce, and entirely correct.
        t = theme(False)
        r = ratio(parse(t['--surface']), parse(t['--surface']))
        assert r == 1.0
        assert r < GRAPHIC_FLOOR

    def test_a_halo_is_paired_with_a_fill_that_does_carry_contrast(self):
        # The ring is exempt because the dot beside it is not. If the fill ever
        # stops being a real mark, the whole justification goes with it.
        for sel in ('.pass-dot', '.form-dot'):
            body = [b for s, b in blocks(strip_comments(read('assets/app.css')))
                    if s.strip() == sel][0]
            fills = [tok for m in PAINT.finditer(body) if m.group(1) == 'fill'
                     for tok in VAR.findall(m.group(2))]
            assert fills == ['--accent'], (sel, fills)


class TestAHairlineIsARuleNotADatum:
    def hairlines(self):
        out = {}
        for rel in SHEETS:
            for sel, body in blocks(read(rel)):
                if not (SIZED.search(body) and HAIRLINE.search(body)):
                    continue
                for m in BG.finditer(body):
                    for tok in VAR.findall(m.group(1)):
                        if tok not in GROUND_TOKENS:
                            out[sel.strip()] = tok
        return out

    def test_three_rules_are_one_pixel_thick(self):
        # There were four while the landing page had an eyebrow over its
        # headline with a 22px dash in front of it. The eyebrow went with the
        # School Colours redesign (2026-10-07).
        assert self.hairlines() == {'.record-sep': '--line-hi',
                                    '.tally-sep': '--line-hi',
                                    '.tick-half': '--mark-quiet'}

    def test_only_two_of_them_need_the_exemption(self):
        # `.tick-half` is the half-time divider on the timeline, and it clears
        # 3.0 on its own. An exemption is only worth having where it changes an
        # answer; naming it as load-bearing when it is not is how a waiver
        # grows quietly until something real falls through it.
        t = theme(False)
        needed = {sel for sel, tok in self.hairlines().items()
                  if min(ratio(parse(t[tok]), parse(t[p])) for p in PAGE)
                  < GRAPHIC_FLOOR}
        assert needed == {'.record-sep', '.tally-sep'}, needed

    def test_the_exemption_changes_a_verdict(self):
        # Both are `--line-hi`, which is 1.41 against the lightest surface. A
        # divider that cleared 3.0 would be competing with the numbers it
        # separates, which is the opposite of a divider's job.
        t = theme(False)
        r = min(ratio(parse(t['--line-hi']), parse(t[p])) for p in PAGE)
        assert r < GRAPHIC_FLOOR
        assert round(r, 2) == 1.41


class TestPaperOnlyScoresWhatReachesPaper:
    def test_only_two_pages_can_print(self):
        # The derivation reads the markup of these two and no others. If a
        # third page grows a print button its markup has to join them, and
        # a scope that silently stayed at two would start exempting classes
        # on a page it had never looked at.
        callers = sorted(
            str(f.relative_to(ROOT)).replace('\\', '/').rsplit('/', 1)[0]
            for d in JS_DIRS for f in (ROOT / d).glob('*.js')
            if 'window.print()' in f.read_text(encoding='utf-8'))
        assert callers == ['coach', 'player'], callers
        assert [h.split('/')[0] for h in PRINTS] == callers

    def test_the_hide_list_is_read_from_the_print_theme(self):
        listed = hidden_selectors()
        assert '#getting-started' in listed
        assert '.no-print' in listed
        assert len(listed) > 12, sorted(listed)

    def test_a_class_used_outside_a_hidden_subtree_is_not_hidden(self):
        names = hidden_names()
        # On `#getting-started` and on sixteen blocks that do print.
        assert '.card' not in names
        assert '.btn' in names          # listed outright by the print theme
        assert '.steps' in names        # only ever inside the hidden section
        assert '.step-note' in names
        assert '.brand' in names        # only ever inside the hidden topbar

    def test_the_exemption_selects_the_rules_it_should(self):
        # The derivation's job, checked head-on rather than through whatever
        # happens to be failing today. Anything under the hidden topbar or
        # the hidden getting-started section never reaches paper; the accent
        # chips that do print are not covered and must not be.
        pat = hidden_pattern()
        for sel in ['.brand span', '.steps li::before', '.topbar',
                    '.step-note']:
            assert pat.search(sel), sel
        for sel in ['.job-count', '.staff-initial', '.card', '.card-chip']:
            assert not pat.search(sel), sel

    def test_the_exemption_narrows_the_paper_scan(self):
        # Reach, measured. An exemption that removed nothing would be dead
        # code dressed as a safeguard, and the empty-alternation bug proved
        # that one removing everything can look identical from the outside.
        kept = scored(PRINTABLE, printed=True)[1]
        every = scored(PRINTABLE, printed=True, exempt=False)[1]
        assert 0 < len(kept) < len(every)
        assert len(every) - len(kept) > 20, len(every) - len(kept)

    def test_the_paper_accent_is_the_screen_accent(self):
        # `.steps li::before` was --accent on its own --accent-dim over the
        # lightest paper surface at 4.23, under the 4.5 its 0.8rem counters
        # are owed, and for a while paper carried its own darker accent to
        # fix it and the identical pair at .job-count and .staff-initial.
        # Since the light redesign the screen accent is already ink on a
        # light ground, so paper keeps it, and the pair is far clear.
        screen, paper = theme(False), theme(True)
        assert paper['--accent'] == screen['--accent']
        r = ratio(parse(paper['--accent']),
                  over(parse(paper['--accent-dim']), parse(paper['--surface-hi'])))
        assert round(r, 2) == 11.27, r

    def test_the_exemption_is_what_keeps_paper_green(self):
        """Exactly what the waiver is carrying, named one row at a time.

        For a while this asserted the difference was empty: the exemption was
        kept for its reasoning rather than its effect, with a note that the
        day it stopped being empty would be a thing to notice. This is that
        day. It was not a regression — nothing about the site changed. The
        scanner learned to read literal hex, and near-black lettering on the
        accent is written that way in all four of these places.

        The fourth arrived later and on purpose. A skip link is furniture
        for getting around a page you are looking at, so it is hidden on
        paper -- and being hidden on paper is exactly what buys it a place
        here, since the print theme's hide list is the same list this
        waiver reads.

        So the difference is asserted row by row rather than by count, and
        every exempting name is checked against the hide list here rather
        than taken on the pattern's word. A waiver that grows quietly is how
        something real eventually falls through one.

        Empty again since the School Colours redesign (2026-10-07). All five
        rows were near-black or pale-pink lettering on the bright accent of
        the dark theme. The accent is navy now, lettered in white, and it is
        legible on paper without help, so the waiver carries nothing today.
        It stays because the next button nobody prints might need it.
        """
        assert failures(PRINTABLE, printed=True) == []
        rows = failures(PRINTABLE, printed=True, exempt=False)
        assert [(r, ink, where.split()[-1]) for r, _, _, ink, _, where
                in rows] == [], rows
        # `.toast` and `.btn` are written into the print hide list. `.chip` is
        # not, and cannot be: no printable markup contains one, because a
        # script makes them inside a container the list does hide. That claim
        # is the one JS_HOSTS entry, and it is checked, not asserted.
        listed = hidden_names()
        assert {'.toast', '.btn', '.skip-link'} <= listed
        assert '.chip' not in listed
        assert js_hidden() == {'chip'}

    def test_nothing_is_hidden_on_screen(self):
        # The exemption is paper-only. A screen scan that honoured the print
        # hide list would stop looking at most of the site.
        _, screen = scored(SHEETS, printed=False)
        _, paper = scored(PRINTABLE, printed=True)
        assert len(screen) > len(paper)


class TestTheMapsPaintWithCurrentColor:
    def test_each_map_still_sets_a_colour_and_owns_an_svg(self):
        app = strip_comments(read('assets/app.css'))
        rules = {sel.strip(): body for sel, body in blocks(app)}
        for sel in CURRENT_COLOR:
            assert sel in rules, sel
            assert VAR.findall(COLOR.search(rules[sel]).group(1))
            assert sel + '-svg' in rules, sel

    def test_the_list_is_every_selector_shaped_like_one(self):
        # If a fourth map is added, it arrives with the same two marks -- a
        # `color:` and a companion `-svg` rule -- and this fails until somebody
        # decides on purpose whether it is text or paint.
        app = strip_comments(read('assets/app.css'))
        rules = {sel.strip(): body for sel, body in blocks(app)}
        shaped = sorted(s for s, b in rules.items()
                        if COLOR.search(b) and s + '-svg' in rules)
        assert shaped == sorted(CURRENT_COLOR), shaped

    def test_something_still_paints_in_currentcolor(self):
        # The whole exemption rests on this. If the painters stop using
        # currentColor the three `color:` declarations become dead weight and
        # the maps stop being drawn at all.
        users = [rel for d in JS_DIRS for p in (ROOT / d).glob('*.js')
                 for rel in [str(p.relative_to(ROOT)).replace('\\', '/')]
                 if 'currentColor' in p.read_text(encoding='utf-8')]
        assert sorted(users) == ['assets/heatmap.js',
                                 'assets/pitch-backdrop.js'], users

    def test_the_exemption_changes_a_verdict(self):
        # 4.10 passes 3.0 and fails 4.5. Which floor applies is the whole
        # question, and it is the only exemption here that turns on a judgement
        # about what a declaration means rather than what it says.
        t = theme(False)
        r = ratio(parse(t['--mark-quiet']), parse(t['--surface']))
        assert GRAPHIC_FLOOR <= r < TEXT_FLOOR
        assert round(r, 2) == 4.10


class TestTheScannerCanSeeTheRepo:
    def test_every_file_it_claims_to_read_exists(self):
        for rel in SHEETS + HTML:
            assert (ROOT / rel).exists(), rel

    def test_the_theme_resolves_on_both_media(self):
        screen, paper = theme(False), theme(True)
        # Thirty-eight: the dark theme's twenty-five, less --pitch which
        # nothing read any more, plus fourteen from the School Colours
        # redesign -- the kit (six tokens), --on-accent, a chip tint for each
        # state colour, two type faces, a lifted shadow and an easing curve. `--report` and `--w` are still left out: one is
        # declared inside a min-width query, the other written from
        # JavaScript, and neither is a colour. Paper differs from the screen in five: it turns
        # every tinted ground white and darkens the two hairline colours.
        assert len(screen) == 38, sorted(screen)
        assert screen['--surface'] == '#ffffff'
        assert paper['--surface'] == '#ffffff'
        assert sum(1 for k in screen if screen[k] != paper.get(k)) == 5

    def test_the_maths_agrees_with_the_published_examples(self):
        # WCAG's own worked pair, and the two ends of the scale.
        assert round(ratio((255, 255, 255, 1), (0, 0, 0, 1)), 2) == 21.0
        assert ratio((0, 0, 0, 1), (0, 0, 0, 1)) == 1.0
        assert round(ratio((255, 255, 255, 1), (119, 119, 119, 1)), 2) == 4.48

    def test_a_translucent_ground_is_composited_not_ignored(self):
        t = theme(False)
        dim = parse(t['--accent-dim'])
        assert dim[3] < 1, dim
        flat = over(dim, parse(t['--surface']))
        assert flat[3] == 1.0
        assert flat != dim[:3] + (1.0,)

    @pytest.mark.parametrize('rel', SHEETS)
    def test_every_stylesheet_yields_rules(self, rel):
        rules = blocks(read(rel))
        assert len(rules) > 10, (rel, len(rules))
        assert all(sel for sel, _ in rules), rel

    def test_a_broken_floor_would_be_noticed(self):
        # The negative control: score everything against an impossible floor
        # and the scan must light up. If this passes, the scan found nothing
        # to score and every assertion above is vacuous.
        t, rows = scored(SHEETS, printed=False)
        under = [r for kind, floor, tok, ground, where in rows
                 for fg in [parse(t.get(tok))] if fg
                 for r in [min(ratio(fg, parse(t[p])) for p in PAGE)]
                 if r < 21.0]
        assert len(under) > 200, len(under)
