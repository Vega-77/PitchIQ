"""Every view a person can be looking at says what it is.

A heading list is the table of contents a screen reader navigates by, and on
this site it is not a property of a *file*. Every page here is a set of
mutually exclusive views swapped by `showOnly`, so `coach/index.html` holds
four h1s and is right to; only one of them is ever on screen. Count h1s per
document and all seven pages pass. Count them per view and two of the eighteen
had none at all:

  * `calibrate/index.html` `#workspace` -- the file's one h1 lived in
    `#intro`, which `calibrate.js:43` hides the instant the workspace opens.
    The whole working state of the page, which is where a person spends every
    second of the task, began at an h2 that reads "Click the highlighted
    spot": an instruction, not a name for where you are.

  * `halftime/index.html` `#view-report` -- only `#view-error` had one. The
    report is the entire purpose of the page and the thing a coach opens on a
    phone at half-time; its visible masthead is three divs carrying no heading
    semantics, so the outline started at "Before you talk to them".

Both were fixed the way this repo had already fixed it twice, in comments that
argue the rule this file now enforces: an `sr-only` h1 that costs nothing
visually. See `live-tagging/index.html` -- "a page with no h1 at all is a page
a screen reader cannot introduce."

Three ways a heading scan lies about this site, all of them measured:

1.  **Multiple h1s in one file are correct here**, on three pages. A scan that
    reports them reports work that must not be done.

2.  **The jump from h1 straight to h3 is the house style**, not a defect. h3
    is the section eyebrow -- `app.css` calls it "the heading on every section
    in the app" -- and there is no h2 tier in the information architecture at
    all. A no-skipped-levels scan would condemn thirteen of the eighteen
    views and every one of those findings would be noise. What is worth
    pinning is the two places an h2 *does* appear, so the convention stays a
    convention.

3.  **An `sr-only` h1 is still an h1.** Four of them are load-bearing. A scan
    scoring visible text finds four defects that are the fix.

And one that is structural rather than statistical: **view membership cannot
be read off the markup.** `calibrate`'s `#intro` carries no `hidden` class
until script adds one, so a heuristic over class names misses it -- which is
how the defect survived being looked at. The list below is named by hand and
cross-checked against the code that actually does the switching, in both
directions, the same way `SCRIPT_NAMED` is in `test_control_names.py`.
"""

import re

from test_contrast_floor import HTML, ROOT, read

# Every view, per page, in the order the code lists them. Cross-checked
# against the switching code by `TestTheListIsTheOneTheCodeUses` -- a name
# here that no longer switches, or a view the code swaps that is missing
# here, fails. An empty list means the page is a single view.
VIEWS = {
    'index.html': ['view-marketing', 'view-nowhere', 'view-routes'],
    'coach/index.html': ['view-noteam', 'view-main', 'view-match',
                         'view-player'],
    'player/index.html': ['view-empty', 'view-reports', 'view-match'],
    'live-tagging/index.html': ['view-setup', 'view-kickoff', 'view-live'],
    'halftime/index.html': ['view-error', 'view-report'],
    # The one pair no scan could have found: the class is added at runtime.
    'calibrate/index.html': ['intro', 'workspace'],
    'xg-sandbox/index.html': [],
}

# Pages whose view set is a literal array in one module, and where it lives.
ARRAYS = {
    'index.html': 'assets/landing.js',
    'coach/index.html': 'coach/shell.js',
    'player/index.html': 'player/player.js',
    'halftime/index.html': 'halftime/halftime.js',
}

# The only two h2s on the site outside a dialog, and why each is not the
# start of an h2 tier that does not exist. Both directions: grow a third and
# this fails, delete one and this fails.
LOOSE_H2 = {
    'Click the highlighted spot':
        'calibrate: an instruction above the stage, under the view h1',
    'Model a shot':
        'xg-sandbox: the panel head, the only section on a one-section page',
}

HEADING = re.compile(r'<h([1-6])\b([^>]*)>(.*?)</h\1>', re.S | re.I)
TAG = re.compile(r'<[^>]+>')


def decomment(src):
    """Comments out, newlines kept so line numbers survive.

    No commented-out heading exists in this repo today, which is exactly the
    condition under which forgetting this looks correct forever. The house
    style is long explanatory comments directly above the markup they
    describe, so the first one to quote a heading would be silently counted.
    """
    return re.sub(r'<!--.*?-->', lambda m: '\n' * m.group(0).count('\n'), src,
                  flags=re.S)


def spans(src, key):
    """(key(attrs), start, end) for every element `key` accepts.

    A depth walk rather than a regex, because the question -- which container
    is this heading inside -- is the one thing a regex cannot answer.

    It carries no list of void elements, which is worth saying because it
    started with one. Popping until a name matches, and discarding whatever
    was left open in between, handles an unclosed `<input>` on its own; the
    list changed no result on any page here and was removed rather than kept
    as a comment claiming it did something. A close tag for a name never
    opened is the case it *would* have hurt -- inline SVG writing
    `<circle></circle>` rather than self-closing it -- and the honest reason
    that is not a live risk is that these pages contain no inline SVG at all.
    """
    stack, out = [], []
    for m in re.finditer(r'<(/?)(\w+)\b([^>]*?)(/?)>', src):
        close, tag, attrs, selfshut = m.groups()
        if selfshut:
            continue
        if close:
            while stack:
                name, start, opened = stack.pop()
                if name == tag.lower():
                    k = key(opened)
                    if k is not None:
                        out.append((k, start, m.end()))
                    break
        else:
            stack.append((tag.lower(), m.start(), attrs))
    return out


def attr(name):
    def key(attrs):
        m = re.search(r'\b%s="([^"]*)"' % name, attrs)
        return m.group(1) if m else None
    return key


def modal(attrs):
    """Dialogs are a context of their own, not part of the page outline.

    A screen reader in a modal is told it is in a modal and reads its label;
    the h2 inside is that dialog's title, and folding it into the page's
    heading list would say the page has seven sections it does not have.
    """
    return 'dialog' if 'aria-modal="true"' in attrs else None


def text(html):
    return ' '.join(TAG.sub(' ', html).split())


def scan(src, view, view_ids):
    """The heading list a person has while looking at `view`.

    One function, used by the seven real pages and by the synthetic markup in
    `TestTheScanCouldBeLying` alike. Two copies of this walk would mean the
    lying-directions were proved against a scan that is not the one running.
    """
    src = decomment(src)
    boxes = {k: (lo, hi) for k, lo, hi in spans(src, attr('id'))
             if k in view_ids}
    dialogs = [(lo, hi) for _, lo, hi in spans(src, modal)]
    seen = []
    for m in HEADING.finditer(src):
        pos = m.start()
        if any(lo <= pos < hi for lo, hi in dialogs):
            continue
        mine = [i for i, (lo, hi) in boxes.items() if lo <= pos < hi]
        assert len(mine) <= 1, (view, mine)
        # No box at all means the heading is outside every view and so is on
        # screen in all of them.
        if mine and mine[0] != view:
            continue
        seen.append((int(m.group(1)), 'sr-only' in (m.group(2) or ''),
                     text(m.group(3))))
    return seen


def outline(rel, view):
    return scan(read(rel), view, VIEWS[rel])


def every_view():
    """(page, view, outline) for all eighteen. `None` where a page is one
    view, so the single-view pages are scanned rather than skipped."""
    for rel in HTML:
        for view in (VIEWS[rel] or [None]):
            yield rel, view, outline(rel, view)


# --- the finding ------------------------------------------------------------

class TestEveryViewIntroducesItself:

    def test_each_one_has_exactly_one_h1(self):
        bad = [(rel, view, [h[2] for h in o if h[0] == 1])
               for rel, view, o in every_view()
               if len([h for h in o if h[0] == 1]) != 1]
        assert bad == [], bad

    def test_the_h1_comes_first(self):
        """A title that arrives third is not a title.

        Nothing on this site currently gets this wrong, and it is one line to
        check, but it is the half of the rule that a well-meaning fix breaks:
        the obvious way to give a view an h1 is to put it where there is room
        for it.
        """
        bad = [(rel, view, o[0]) for rel, view, o in every_view()
               if o and o[0][0] != 1]
        assert bad == [], bad

    def test_the_two_that_had_none_are_pinned(self):
        """The actual defects, named, so a revert is a failing test.

        Both are `sr-only`: the halftime masthead is a deliberate design
        decision not to shout the score back at somebody who just watched the
        half, and calibrate's h1 type scale would not fit beside the hint and
        the button. Neither view has anywhere to put a visible title, which is
        how both of them ended up with no title at all.
        """
        found = {(rel, view): [h for h in o if h[0] == 1][0]
                 for rel, view, o in every_view()
                 if (rel, view) in (('halftime/index.html', 'view-report'),
                                    ('calibrate/index.html', 'workspace'))}
        assert found == {
            ('halftime/index.html', 'view-report'):
                (1, True, 'Half-time report'),
            ('calibrate/index.html', 'workspace'):
                (1, True, 'Marking the pitch'),
        }, found

    def test_a_heading_outside_every_view_belongs_to_all_of_them(self):
        """Two pages title themselves once instead of once per view.

        Both are fullscreen tools whose views are steps in one task rather
        than separate places, and both were commented in place by whoever
        wrote them. They are the reason `outline` has to carry headings that
        sit outside the view boxes rather than only those inside.
        """
        loose = {}
        for rel in HTML:
            src = decomment(read(rel))
            boxes = {k: (lo, hi) for k, lo, hi in spans(src, attr('id'))
                     if k in VIEWS[rel]}
            for m in HEADING.finditer(src):
                if m.group(1) != '1':
                    continue
                if not any(lo <= m.start() < hi for lo, hi in boxes.values()):
                    loose[rel] = text(m.group(3))
        assert loose == {
            'live-tagging/index.html': 'PitchIQ live tagging',
            'xg-sandbox/index.html':
                'xG sandbox %s model a shot and see its chance of scoring'
                % chr(8212),
        }, loose


# --- the list has to be the code's list -------------------------------------

class TestTheListIsTheOneTheCodeUses:

    def test_every_named_view_is_in_the_markup(self):
        for rel in HTML:
            ids = {k for k, _, _ in spans(decomment(read(rel)), attr('id'))}
            assert set(VIEWS[rel]) <= ids, (rel, set(VIEWS[rel]) - ids)

    def test_the_javascript_arrays_agree(self):
        """Four pages declare their views as a literal. Read it, do not
        trust the copy here."""
        for rel, mod in ARRAYS.items():
            m = re.search(r'VIEWS\s*=\s*\[([^\]]*)\]', read(mod))
            assert m, mod
            got = re.findall(r"'([^']+)'", m.group(1))
            assert sorted(got) == sorted(VIEWS[rel]), (mod, got)

    def test_the_class_switched_page_agrees(self):
        """live-tagging swaps `.view.active` instead of listing ids."""
        src = decomment(read('live-tagging/index.html'))
        got = re.findall(r'<section id="([^"]+)" class="view\b', src)
        assert got == VIEWS['live-tagging/index.html'], got

    def test_the_hand_toggled_pair_agrees(self):
        """calibrate has no array and no `view` class -- it adds and removes
        `hidden` by id. This is the pair a markup heuristic cannot see, and
        the reason the whole list is named rather than derived."""
        src = read('calibrate/calibrate.js')
        got = set(re.findall(
            r"byId\('([^']+)'\)\.classList\.(?:add|remove)\('hidden'\)", src))
        assert got == set(VIEWS['calibrate/index.html']), got

    def test_the_single_view_page_really_has_no_switcher(self):
        """The empty list is a claim, so it gets checked like one."""
        src = read('xg-sandbox/sandbox.js')
        assert 'showOnly' not in src
        assert 'VIEWS' not in src
        assert 'class="view' not in read('xg-sandbox/index.html')

    def test_the_pages_are_the_pages_the_site_ships(self):
        on_disk = sorted(p.relative_to(ROOT).as_posix()
                         for p in list(ROOT.glob('*.html'))
                         + list(ROOT.glob('*/index.html')))
        assert on_disk == sorted(HTML), on_disk
        assert sorted(VIEWS) == sorted(HTML)


# --- the convention, pinned rather than assumed -----------------------------

class TestTheHouseStyleIsDeliberate:

    def test_h3_is_the_section_heading_everywhere(self):
        """Why a no-skipped-levels scan would be wrong on every page.

        The h1 -> h3 jump is not thirteen oversights; there is no h2 tier.
        The stylesheet says so in as many words, and that comment is the
        thing this test is protecting -- change the eyebrow to h2 and the
        skip becomes real, so this must be revisited rather than silently
        still passing.
        """
        css = ' '.join(read('assets/app.css').split())
        assert 'This is the heading on every section in the app' in css
        # Both directions, by naming the exceptions rather than counting
        # the rule: thirteen views go h1 -> h3, and the five that do not are
        # the three with no sections at all and the two h2s below.
        kept = sorted((rel, view) for rel, view, o in every_view()
                      if [h[0] for h in o][:2] != [1, 3])
        assert kept == [
            ('calibrate/index.html', 'workspace'),
            ('coach/index.html', 'view-noteam'),
            ('halftime/index.html', 'view-error'),
            ('live-tagging/index.html', 'view-live'),
            ('xg-sandbox/index.html', None),
        ], kept

    def test_every_h2_outside_a_dialog_is_one_of_two(self):
        got = {}
        for rel, _, o in every_view():
            for level, _, words in o:
                if level == 2:
                    got[words] = rel
        assert set(got) == set(LOOSE_H2), set(got) ^ set(LOOSE_H2)

    def test_each_dialog_is_titled_by_a_heading_inside_it(self):
        """Held apart from the page outline, so checked on its own terms.

        WCAG 4.1.2 wants the dialog named; pointing `aria-labelledby` at the
        heading rather than duplicating the words into an `aria-label` is the
        same choice `test_control_names` records for the controls, and for
        the same reason (2.5.3, label in name).
        """
        src = decomment(read('live-tagging/index.html'))
        ids = {}
        for m in HEADING.finditer(src):
            i = re.search(r'\bid="([^"]+)"', m.group(2) or '')
            if i:
                ids[i.group(1)] = (int(m.group(1)), m.start())
        labelled = re.findall(r'aria-modal="true"[^>]*?'
                              r'aria-labelledby="([^"]+)"', src, re.S)
        assert len(labelled) == 5, labelled
        for target in labelled:
            assert target in ids, target
            assert ids[target][0] == 2, (target, ids[target])

    def test_an_sr_only_h1_is_still_an_h1(self):
        """Four of them, and all four are load-bearing.

        A scan reading visible text reports these as four missing titles,
        which is the fix reported as the defect.
        """
        hidden = sorted({(rel, words) for rel, _, o in every_view()
                         for level, sr, words in o if level == 1 and sr})
        assert [rel for rel, _ in hidden] == [
            'calibrate/index.html', 'halftime/index.html',
            'live-tagging/index.html', 'xg-sandbox/index.html'], hidden
        assert '.sr-only' in read('assets/app.css')


# --- the ways this scan could be lying --------------------------------------

class TestTheScanCouldBeLying:

    def test_it_does_not_count_a_commented_out_heading(self):
        src = ('<section id="v"><!-- <h1>ghost</h1> --><h1>real</h1>'
               '</section>')
        assert marks(src, 'v', ['v']) == [(1, False, 'real')]

    def test_it_sees_an_h1_however_it_is_dressed(self):
        for tag in ('<h1>', '<H1>', '<h1 class="sr-only">',
                    '<h1\n    style="margin-top:44px">', '<h1 id="x">'):
            src = '<section id="v">%stitle</h1></section>' % tag
            got = marks(src, 'v', ['v'])
            assert [g[0] for g in got] == [1], (tag, got)
            assert got[0][2] == 'title', (tag, got)

    def test_it_does_not_leak_between_sibling_views(self):
        src = ('<section id="a"><h1>A</h1></section>'
               '<section id="b"><h1>B</h1></section>')
        assert marks(src, 'a', ['a', 'b']) == [(1, False, 'A')]
        assert marks(src, 'b', ['a', 'b']) == [(1, False, 'B')]

    def test_a_heading_outside_the_views_shows_up_in_both(self):
        src = ('<h1 class="sr-only">page</h1>'
               '<section id="a"><h3>A</h3></section>'
               '<section id="b"><h3>B</h3></section>')
        assert marks(src, 'a', ['a', 'b']) == [(1, True, 'page'),
                                               (3, False, 'A')]
        assert marks(src, 'b', ['a', 'b']) == [(1, True, 'page'),
                                               (3, False, 'B')]

    def test_it_survives_a_view_nested_in_a_wrapper(self):
        """Every real page wraps its views in `<main class="shell">`, and
        `view-report` sits two divs deep before its first heading."""
        src = ('<main><div class="shell"><section id="v">'
               '<header><div><h1>deep</h1></div></header>'
               '</section></div></main>')
        assert marks(src, 'v', ['v']) == [(1, False, 'deep')]

    def test_an_unclosed_element_does_not_cost_a_view_its_box(self):
        """`<input>` never closes, and twenty-six here carry an id.

        Lose a view’s box and its headings stop belonging to any view,
        which this scan reads as *belonging to every* view -- so the failure
        shows up as a heading leaking sideways into a sibling, not as one
        going missing. That is why the sibling is in this test and why a
        single view could not have caught it: an earlier version of this
        case used one, and passed with the walk broken.
        """
        src = ('<section id="a"><input id="x"><h1>A</h1></section>'
               '<section id="b"><h1>B</h1></section>')
        assert marks(src, 'a', ['a', 'b']) == [(1, False, 'A')]
        assert marks(src, 'b', ['a', 'b']) == [(1, False, 'B')]

    def test_it_finds_headings_at_all(self):
        """The anti-vacuum guard. Every assertion above is over a list, and
        a scan that returns nothing satisfies all of them."""
        total = sum(len(o) for _, _, o in every_view())
        assert total >= 80, total
        assert len(list(every_view())) == 18
        for rel in HTML:
            assert any(o for r, _, o in every_view() if r == rel), rel


def marks(src, view, ids):
    """Run the real scan over synthetic markup.

    The scan has to be exercised on pages that are not on disk. Four of the
    six cases above — a commented-out heading among them — have no
    instance in this repo, which is the whole reason they are worth a test.
    """
    return scan(src, view, ids)
