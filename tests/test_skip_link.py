"""The first thing in the tab order, and the one nobody sighted ever sees.

Six of the seven pages repeat the same topbar ahead of their content -- brand,
a link or two, sign out. A mouse skips it by looking past it. A keyboard walks
through every item of it again on every navigation, which is what WCAG 2.4.1
(Bypass Blocks) is about, and the fix is one link that jumps to `<main>`.

A skip link is unusually easy to ship broken, and unusually hard to notice
broken, because the only person who can tell is the one person who cannot see
it. Three failures are common enough to have names, and all three are silent:

1. **Hidden the wrong way.** `display: none` or `visibility: hidden` at rest
   takes the link out of the tab order entirely, so the link exists, validates,
   reads correctly in the markup, and can never be reached. This is the most
   common broken skip link on the web.
2. **Hidden the wrong way round.** A resting offset on one property and a
   reveal on a different one -- `top: -100px` undone by
   `transform: translateY(0)` -- leaves it reachable and still off screen. The
   caret goes somewhere the person cannot see, which is worse than no link,
   because now they are tabbing blind through a control they cannot find.
3. **Pointed at nothing.** `href="#main"` with no `#main` below it moves the
   caret nowhere and announces nothing. `tests/test_id_seam.py` covers the
   dangling half of this; what is checked here is that the id lands on the
   landmark rather than on some wrapper that happens to be nearby.

None of the three changes a single pixel for a sighted reader, so none of them
would ever be reported. They have to be read out of the file.

**The six-not-seven split is measured here, not declared.** `live-tagging` has
no skip link and should not have one: its `<main>` is the first thing in its
`<body>`, so a link in front of it would skip nothing and cost a keypress. The
test does not take that on trust -- it counts what is focusable ahead of each
page's `<main>`, and requires the pages with nothing there to be exactly the
pages with no link. Add a topbar to the tagging page tomorrow and this fails,
which is the point; a hard-coded list of six would not.
"""

import re

from test_contrast_floor import HTML, SHEETS, read, strip_comments

# The sheet the link is styled in, and the sheets that can paint over it. The
# tagging page's own sheet is not among them: it is the one page with no link,
# so what it stacks is not this gate's business.
RING = 'assets/app.css'
OVER = [s for s in SHEETS if s != 'live-tagging/tagging.css']

# The link, exactly as it is written on all six pages.
LINK = re.compile(r'<a class="skip-link" href="#([A-Za-z][\w-]*)"[^>]*>'
                  r'([^<]*)</a>')
COMMENT = re.compile(r'<!--.*?-->', re.S)

# Anything a Tab can land on. Deliberately generous -- this is the scan the
# six-not-seven split rests on, and an under-reading one would quietly agree
# that a page has nothing before its <main> when it has three things.
FOCUSABLE = re.compile(r'<(a|button|input|select|textarea|summary)\b([^>]*)>',
                       re.I)
TABINDEX = re.compile(r'\btabindex\s*=\s*"(-?\d+)"')
Z_INDEX = re.compile(r'\bz-index\s*:\s*(-?\d+)')


def markup(rel):
    """A page with its comments gone. A commented-out control is not one."""
    return COMMENT.sub('', read(rel))


def prefix(rel):
    """Everything between `<body>` and the page's `<main>`.

    What a keyboard has to get through before it reaches the content, which
    is the whole quantity a skip link exists to reduce.
    """
    src = markup(rel)
    start = src.index('<body>') + len('<body>')
    end = src.index('<main', start)
    return src[start:end]


def focusables(src):
    """Every focusable start tag in `src`, as (offset, tag, attrs)."""
    found = []
    for m in FOCUSABLE.finditer(src):
        tag, attrs = m.group(1).lower(), m.group(2)
        if 'disabled' in attrs:
            continue
        if tag == 'a' and 'href' not in attrs:
            continue
        if tag == 'input' and 'type="hidden"' in attrs:
            continue
        if 'tabindex="-1"' in attrs:
            continue
        found.append((m.start(), tag, attrs))
    # Anything at all can be given a tab stop, and two things on this site are.
    for m in TABINDEX.finditer(src):
        if int(m.group(1)) >= 0:
            found.append((m.start(), 'tabindex', m.group(0)))
    return sorted(found)


def screen(rel):
    """A sheet with its comments and its print theme removed.

    `blocks()` in the sibling gate flattens at-rule wrappers, so the print
    theme's `.skip-link { display: none }` would come back looking like a
    screen rule and every check below would read backwards.
    """
    src = strip_comments(read(rel))
    return src.split('@media print', 1)[0]


def paper(rel=RING):
    src = strip_comments(read(rel))
    assert '@media print' in src, rel
    return src.split('@media print', 1)[1]


def rule(selector, src):
    """The declaration body of `selector`, or None."""
    m = re.search(re.escape(selector) + r'\s*\{([^{}]*)\}', src)
    return m.group(1) if m else None


def props(body):
    return {d.split(':', 1)[0].strip()
            for d in body.split(';') if ':' in d}


LINKED = {rel: LINK.search(markup(rel)) for rel in HTML}
CARRIES = {rel for rel, m in LINKED.items() if m}
RESTING = rule('.skip-link', screen(RING))
REVEALED = rule('.skip-link:focus', screen(RING))

# Properties that decide whether a focused element is somewhere a person can
# look at. The reveal has to undo the rest using one of these, and using the
# same one.
PLACEMENT = {'transform', 'translate', 'top', 'bottom', 'left', 'right',
             'inset', 'opacity', 'clip', 'clip-path', 'position'}


class TestTheLinkIsWhereTheTabOrderStarts:
    def test_six_of_the_seven_pages_carry_one(self):
        assert CARRIES == set(HTML) - {'live-tagging/index.html'}

    def test_the_seventh_is_the_one_with_nothing_to_skip(self):
        """The split, derived rather than listed.

        A page earns a skip link by having something in front of its content
        worth skipping. Counting that is the only way this stays true: the day
        the tagging page grows a topbar, it wants a link, and a hard-coded six
        would go on agreeing that it does not.
        """
        ahead = {}
        for rel in HTML:
            src = prefix(rel)
            # The link itself sits in that prefix on six pages and is the
            # thing being justified, so it cannot be its own justification.
            ahead[rel] = [f for f in focusables(src)
                          if 'skip-link' not in f[2]]
        bare = {rel for rel, f in ahead.items() if not f}
        assert bare == set(HTML) - CARRIES, ahead

    def test_it_is_the_first_thing_a_tab_reaches(self):
        """Second is no good. A skip link reached after the topbar has already
        cost the person the walk it exists to save them."""
        for rel in sorted(CARRIES):
            src = markup(rel)
            first = focusables(src)[0]
            assert 'skip-link' in first[2], (rel, first)

    def test_the_six_are_the_same_link(self):
        # Six copies of one line, maintained by hand. Drift here is a typo on
        # one page that nobody would ever see.
        assert len({m.group(0) for m in LINKED.values() if m}) == 1


class TestItGoesSomewhere:
    def test_every_link_names_the_page_it_is_on(self):
        for rel in sorted(CARRIES):
            assert LINKED[rel].group(1) == 'main', rel

    def test_the_target_is_the_landmark_itself(self):
        """Not a wrapper next to it. Moving the caret to a `<div>` above
        `<main>` looks identical on screen and leaves a screen reader
        announcing the wrong thing -- or nothing."""
        for rel in sorted(CARRIES):
            src = markup(rel)
            assert src.count('id="main"') == 1, rel
            assert re.search(r'<main id="main"', src), rel

    def test_it_says_out_loud_where_it_goes(self):
        # The text is read aloud with no surrounding context, so "Skip" alone
        # would leave the question "skip to where?" unanswered.
        for rel in sorted(CARRIES):
            text = LINKED[rel].group(2).strip().lower()
            assert 'skip' in text and 'main' in text, (rel, text)


class TestItComesBackWhenItIsReached:
    def test_the_rest_state_does_not_take_it_out_of_the_tab_order(self):
        """Failure 1. `display: none` and `visibility: hidden` are how most
        broken skip links are broken: unreachable, and unreachable silently."""
        assert RESTING is not None
        assert 'display' not in props(RESTING), RESTING
        assert 'visibility' not in props(RESTING), RESTING

    def test_the_reveal_undoes_the_property_the_rest_offsets(self):
        """Failure 2. A reveal on a property the resting rule never set moves
        nothing, and the caret ends up on a control that is still off screen
        -- reachable, invisible, and impossible to report."""
        assert REVEALED is not None
        moved = props(REVEALED)
        assert moved, REVEALED
        assert moved <= props(RESTING), (moved, props(RESTING))
        assert moved & PLACEMENT, moved

    def test_arriving_does_not_shove_the_page_down(self):
        # In flow, a link that appears pushes the whole layout down the moment
        # it is focused. Everything the reader had their eye on moves.
        assert re.search(r'position\s*:\s*(fixed|absolute)', RESTING), RESTING

    def test_nothing_the_six_pages_load_paints_over_it(self):
        """It is fixed and near the top corner, which is where headers,
        toasts and overlays also live. Being reachable and covered is
        failure 2 wearing a different hat."""
        mine = int(Z_INDEX.search(RESTING).group(1))
        for rel in OVER:
            src = screen(rel)
            for m in Z_INDEX.finditer(src):
                other = int(m.group(1))
                assert other <= mine, (rel, other, mine)

    def test_paper_never_shows_it(self):
        """A way of reaching content you are already holding. It is also the
        one rule on the site whose colours fail on paper, and the print hide
        list is what the contrast gate reads to know what it may stop scoring
        -- so this line is load-bearing twice over."""
        assert re.search(r'^\s*\.skip-link,\s*$', paper(), re.M), \
            'the print theme stopped hiding it'


class TestTheScanCouldBeLying:
    def test_the_focusable_scan_finds_plenty(self):
        # If this went blind, `test_the_seventh_is_the_one_with_nothing_to
        # _skip` would decide every page has nothing ahead of its content and
        # pass by agreeing that no page needs a link at all.
        for rel in HTML:
            assert len(focusables(markup(rel))) >= 5, rel

    def test_both_halves_of_the_rule_were_actually_read(self):
        assert props(RESTING) >= {'position', 'z-index', 'background'}
        assert Z_INDEX.search(RESTING), RESTING
        assert props(REVEALED)

    def test_the_prefix_is_a_prefix(self):
        # `prefix()` finding nothing on every page would make the split test
        # vacuous in the other direction.
        for rel in HTML:
            assert 0 < len(prefix(rel)) < len(markup(rel)), rel

    def test_the_print_theme_was_found(self):
        assert len(paper()) > 500
