# -*- coding: utf-8 -*-
"""The five sheets on the tagging page, and the promise their markup makes.

`aria-modal="true"` tells a screen reader it may ignore everything outside the
dialog. That is a large promise, and it is only honest if the keyboard cannot
get out there either -- so the contract has two halves, and they are checked in
two places because they are two different kinds of claim.

  * Behaviour -- focus lands in the sheet, comes back on close, Escape closes,
    Tab wraps -- has to be run. `tests/sheets.test.js` boots the real page and
    the real `sheet.js` and presses real keys.
  * Markup -- `role="dialog"`, `aria-modal`, `aria-labelledby`, `tabindex="-1"`
    -- is a property of a file, and this is that file.

Neither half is worth much alone. Correct behaviour on a `<div>` nobody is told
is a dialog is invisible to the software that needed telling; correct markup
with Tab walking straight out the back is a lie in ARIA. The seam between them
is the third thing, and it is the one this file exists for: `sheet.js` is the
only code on the site allowed to touch `.open`, and thirteen call sites in
`tagging.js` go through `openSheet` / `closeSheet`. One surviving
`classList.add('open')` somewhere else opens a sheet with the keyboard left
behind it, and every test in the JS file next door would still pass.

What lives here rather than next door, and why. The sheets are hidden with
`display: none` until they carry `.open`, and `tabbable()` filters on
`offsetParent`. `tests/dom-shim.js` is not a renderer -- no layout, no cascade
-- so under it that filter passes everything, and the CSS rule the whole thing
rests on is invisible to the JS suite. It is pinned below instead. Faking
`offsetParent` in the shim was the alternative and was rejected: a shim that
answers layout questions it cannot answer is worse than one that says nothing.

What this does not repeat. `tests/test_focus_visible.py` owns every claim about
outlines, including the deliberate `.sheet:focus { outline: none }` -- a sheet
nobody reached with Tab gets no ring -- so nothing here mentions `outline`.
`tests/test_id_seam.py` already resolves `aria-labelledby` across every page.
The stronger claim this file adds is *where* the target lives: a name that
resolves to a heading in some other sheet resolves fine and reads wrong.
"""
import re

from test_contrast_floor import ROOT, read, strip_comments
from test_heading_outline import decomment, spans

PAGE = 'live-tagging/index.html'
CSS = 'live-tagging/tagging.css'
IMPL = 'live-tagging/sheet.js'

#: Every directory `stamp_version.py` treats as holding modules. The seam claim
#: below is about the whole site, so the whole site has to be read.
JS_DIRS = ['assets', 'coach', 'halftime', 'live-tagging', 'player']

#: The five, in document order. Read off the page everywhere else; written out
#: once here so that a scan which quietly stops finding overlays fails instead
#: of passing over an empty list.
FIVE = ['overlay-event', 'overlay-clock', 'overlay-sub', 'overlay-exit',
        'overlay-log']

OVERLAY_ID = re.compile(r'\bid="(overlay-[a-z-]+)"')
ANY_ID = re.compile(r'\bid="([^"]+)"')
ROLE = re.compile(r'\brole="([^"]*)"')
TOUCH = re.compile(r"classList\.\w+\(\s*'open'")
CALL = re.compile(r"\b(openSheet|closeSheet)\('([^']*)'\)")


def klass(attrs):
    m = re.search(r'\bclass="([^"]*)"', attrs)
    return m.group(1).split() if m else []


def unit(_tag, attrs):
    """Overlays and the sheets inside them, for one walk of the page.

    `class` is split into tokens rather than searched for a substring. The
    page carries a `.sheet-head` inside every one of the five, so a substring
    test would report ten dialogs, five of them missing every attribute -- a
    check that fails for a reason which has nothing to do with the thing it
    was asking about.
    """
    m = OVERLAY_ID.search(attrs)
    if m:
        return ('overlay', m.group(1), attrs)
    if 'sheet' in klass(attrs):
        return ('sheet', None, attrs)
    return None


def page(rel=PAGE):
    return decomment(read(rel))


def dialogs(rel=PAGE):
    """[(overlay_id, sheet_attrs, lo, hi)] -- the sheet inside each overlay.

    `lo`/`hi` bound the sheet, not the overlay, because every question below
    is about what is *inside* the dialog: the heading its name points at, the
    text its description points at, the controls a trapped Tab can reach.
    """
    src = page(rel)
    found = spans(src, unit)
    inner = [(lo, hi, attrs) for (kind, _, attrs), lo, hi in found
             if kind == 'sheet']
    out = []
    for (kind, oid, _), lo, hi in found:
        if kind != 'overlay':
            continue
        mine = [s for s in inner if lo <= s[0] and s[1] <= hi]
        assert len(mine) == 1, '%s holds %d sheets' % (oid, len(mine))
        a, b, attrs = mine[0]
        out.append((oid, attrs, a, b))
    return sorted(out, key=lambda d: d[2])


def js():
    """{path: source} for every script the site ships."""
    out = {}
    for d in JS_DIRS:
        for p in sorted((ROOT / d).glob('*.js')):
            out['%s/%s' % (d, p.name)] = p.read_text(encoding='utf-8')
    return out


def code(src):
    """JavaScript with its comment *lines* dropped.

    Line-wise rather than a real strip, because `'https://...'` inside a
    string is indistinguishable from a comment to anything short of a parser,
    and three of these files import Firebase from a CDN URL. Every comment in
    this repo that quotes `classList.add('open')` -- and `sheet.js`'s header
    quotes it, which is the whole reason this function exists -- is a line of
    prose, and that is the only case that has to be handled.
    """
    keep = []
    for line in src.replace('\r\n', '\n').split('\n'):
        bare = line.strip()
        if bare.startswith('//') or bare.startswith('/*') \
                or bare.startswith('*'):
            continue
        keep.append(line)
    return '\n'.join(keep)


def rule(selector, rel=CSS):
    """The declarations of the one block with exactly this selector."""
    src = strip_comments(read(rel))
    hits = re.findall(r'(?m)^\s*%s\s*\{([^}]*)\}' % re.escape(selector), src)
    assert len(hits) == 1, '%s appears %d times' % (selector, len(hits))
    return ' '.join(hits[0].split())


class TestEverySheetIsADialog:
    """The attributes, on every one of them, with none assumed."""

    def test_the_page_carries_these_five(self):
        assert [d[0] for d in dialogs()] == FIVE

    def test_each_one_says_it_is_a_dialog(self):
        for oid, attrs, _, _ in dialogs():
            assert ROLE.search(attrs).group(1) == 'dialog', oid

    def test_each_one_says_it_is_modal(self):
        # The promise this whole file is about. Without it a screen reader
        # keeps reading the page behind, and the focus trap next door becomes
        # a cage around content the reader is still narrating.
        for oid, attrs, _, _ in dialogs():
            assert 'aria-modal="true"' in attrs, oid

    def test_each_one_can_be_given_the_keyboard(self):
        # `openSheet` focuses the container rather than the first control --
        # focusing an input on a tablet opens the on-screen keyboard over the
        # sheet. A container without `tabindex` cannot take focus at all, and
        # the caret would silently stay on the page behind.
        for oid, attrs, _, _ in dialogs():
            assert 'tabindex="-1"' in attrs, oid

    def test_no_other_element_on_the_site_claims_a_role(self):
        # `role="dialog"` x5 is the only explicit ARIA role anywhere on this
        # site. Everything else earns its role from its tag, which is the
        # cheaper and more reliable way to have one -- so a new `role=`
        # appearing is a thing to look at, not a thing to wave through.
        roles = {}
        from test_contrast_floor import HTML
        for rel in HTML:
            found = ROLE.findall(decomment(read(rel)))
            if found:
                roles[rel] = sorted(found)
        assert roles == {PAGE: ['dialog'] * 5}


class TestTheNameComesFromInsideTheDialog:
    """A dialog is announced by its own heading, not by one somewhere else."""

    def test_every_sheet_is_named(self):
        for oid, attrs, _, _ in dialogs():
            assert 'aria-labelledby="' in attrs, oid

    def test_the_name_resolves_inside_the_sheet_that_uses_it(self):
        # `tests/test_id_seam.py` already proves these ids exist on the page.
        # Existing on the page is not enough: `aria-labelledby` pointing at
        # the heading of a *different* sheet resolves cleanly and announces
        # the wrong dialog, and nothing outside this test would notice.
        for oid, attrs, lo, hi in dialogs():
            want = re.search(r'\baria-labelledby="([^"]*)"', attrs).group(1)
            here = set(ANY_ID.findall(page()[lo:hi]))
            assert set(want.split()) <= here, '%s names %s' % (oid, want)

    def test_the_name_is_a_heading_with_words_in_it(self):
        src = page()
        names = {}
        for oid, attrs, lo, hi in dialogs():
            want = re.search(r'\baria-labelledby="([^"]*)"', attrs).group(1)
            m = re.search(r'<h([1-6])\b[^>]*\bid="%s"[^>]*>(.*?)</h\1>'
                          % re.escape(want), src[lo:hi], re.S)
            assert m, '%s names %s, which is not a heading' % (oid, want)
            names[oid] = ' '.join(re.sub(r'<[^>]+>', ' ', m.group(2)).split())
        assert names == {
            'overlay-event': 'Event',
            'overlay-clock': 'Match clock',
            'overlay-sub': 'Substitution',
            'overlay-exit': 'Stop recording?',
            'overlay-log': 'Everything recorded',
        }

    def test_no_two_sheets_are_announced_the_same_way(self):
        src = page()
        said = []
        for _, attrs, lo, hi in dialogs():
            want = re.search(r'\baria-labelledby="([^"]*)"', attrs).group(1)
            m = re.search(r'>([^<]*)</h', src[lo:hi][src[lo:hi].find(
                'id="%s"' % want):])
            said.append(' '.join(m.group(1).split()))
        assert len(set(said)) == len(said), said

    def test_the_two_described_sheets_describe_themselves(self):
        # Only the clock and the exit sheet carry one, and both point at the
        # sentence of explanation directly under their own heading.
        described = {}
        for oid, attrs, lo, hi in dialogs():
            m = re.search(r'\baria-describedby="([^"]*)"', attrs)
            if not m:
                continue
            described[oid] = m.group(1)
            here = set(ANY_ID.findall(page()[lo:hi]))
            assert set(m.group(1).split()) <= here, oid
        assert described == {'overlay-clock': 'clock-explain',
                             'overlay-exit': 'exit-explain'}


class TestNothingOpensASheetBehindTheSeam:
    """One file owns `.open`, and every call site goes through it."""

    def test_only_the_implementation_touches_the_class(self):
        # This is the check the whole file is for. Every behavioural test next
        # door opens sheets through `openSheet`, so a stray
        # `classList.add('open')` in `tagging.js` -- a sheet up with no focus
        # moved, no Escape, no trap -- would leave all thirteen of them green.
        where = {rel: len(TOUCH.findall(code(src)))
                 for rel, src in js().items() if TOUCH.search(code(src))}
        assert where == {IMPL: 2}

    def test_every_id_passed_in_is_a_sheet_that_exists(self):
        known = set(FIVE)
        for rel, src in js().items():
            for _, oid in CALL.findall(code(src)):
                assert oid in known, '%s opens %s' % (rel, oid)

    def test_every_sheet_is_reachable(self):
        # A sheet nothing opens is dead markup, and dead markup is where an
        # attribute quietly rots: it keeps passing every test above while no
        # tagger has seen it in months.
        opened = set()
        for rel, src in js().items():
            if rel == IMPL:
                continue
            for how, oid in CALL.findall(code(src)):
                if how == 'openSheet':
                    opened.add(oid)
        assert opened == set(FIVE)

    def test_the_one_listener_is_wired_once(self):
        # Twice and Escape closes two sheets on one press; never, and Escape
        # and the Tab trap are both simply absent with nothing to show for it.
        at = [(rel, line)
              for rel, src in js().items() if rel != IMPL
              for line in code(src).split('\n') if 'mountSheets()' in line]
        assert len(at) == 1, at
        rel, line = at[0]
        assert rel == 'live-tagging/tagging.js'
        assert line == 'mountSheets();', 'not at the top level: %r' % line


class TestTheClosedSheetIsOutOfTheWay:
    """The CSS the JS suite structurally cannot see."""

    def test_a_sheet_is_not_there_until_it_is_open(self):
        # `display: none` is what keeps a closed sheet's buttons out of the
        # tab order. `tabbable()` leans on it through `offsetParent`, and the
        # shim has no layout engine, so this rule is unfalsifiable next door.
        assert 'display: none;' in rule('.overlay')

    def test_opening_it_puts_it_back(self):
        assert rule('.overlay.open') == 'display: flex;'

    def test_it_covers_what_it_claims_to_cover(self):
        # A modal that does not cover the page leaves the page tappable, and
        # a thumb reaches what the keyboard has been trapped away from.
        decl = rule('.overlay')
        assert 'position: fixed;' in decl and 'inset: 0;' in decl


class TestTheScanCouldBeLying:
    """Each thing above, asked once of markup built to break it."""

    def test_it_finds_the_sheets_that_are_there(self):
        assert len(dialogs()) == 5

    def test_a_sheet_head_is_not_a_sheet(self):
        # The near miss this scan was written around: five `.sheet-head`
        # divs, one inside each dialog. Substring matching finds ten sheets,
        # five of them bare, and every attribute test above fails for a
        # reason that has nothing to do with accessibility.
        assert unit('div', 'class="sheet-head"') is None
        assert unit('div', 'class="sheet"')[0] == 'sheet'
        assert unit('div', 'class="sheet wide"')[0] == 'sheet'

    def test_it_would_notice_a_missing_attribute(self):
        found = spans(
            '<div id="overlay-x"><div class="sheet" role="dialog">'
            '</div></div>', unit)
        attrs = [a for (k, _, a), _, _ in found if k == 'sheet'][0]
        assert 'aria-modal="true"' not in attrs
        assert 'tabindex="-1"' not in attrs

    def test_the_page_reaches_the_scan_with_the_comments_gone(self):
        # The page really does carry commented-out markup, so this is a live
        # risk rather than a hypothetical one: the house style is long
        # explanations directly above what they describe, and the first one
        # to quote an overlay id would be counted as a sixth dialog.
        assert '<!--' in read(PAGE)
        assert '<!--' not in page()

    def test_it_would_see_a_sheet_opened_by_hand(self):
        assert TOUCH.search("byId('overlay-log').classList.add('open');")
        assert TOUCH.search('el.classList.remove( \'open\' )')
        assert not TOUCH.search("classList.add('opened')")

    def test_it_reads_every_script_the_site_ships(self):
        # 27 today. The number is here so that a glob which quietly matches
        # nothing -- a renamed directory, a move to subfolders -- fails
        # loudly instead of proving the seam intact across no files at all.
        # It moves when a file is genuinely added: assets/svg.js was the
        # thirty-third, split out of six copies of one SVG element factory.
        # It fell to 27 when the calibrate page and the xG sandbox left
        # (2026-10-07), and rose to 30 the same day with the team colours
        # (assets/kit.js, and assets/kit-boot.js which paints them before the
        # first frame) and assets/motion.js.
        seen = js()
        assert len(seen) == 30
        assert IMPL in seen and 'live-tagging/tagging.js' in seen
