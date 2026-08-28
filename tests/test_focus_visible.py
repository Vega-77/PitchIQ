"""The keyboard can always see where it is.

WCAG 2.4.7. The site has one focus ring, declared once in `app.css`, and it
is the weakest selector that could possibly paint it -- a bare
`:focus-visible`, specificity (0, 1, 0). That is the right way to write a
default and it is also why a single careless line anywhere else wins.

The line that started this: `input[type="range"] { outline: none }` in the xG
sandbox, arriving as part of the appearance-reset recipe the whole block was
copied from. Specificity (0, 1, 1) -- one attribute *and* one element name --
so it outranked the ring on a sheet that declares no focus rule of its own.
Six sliders, tabbable, with nothing on screen saying which one had the
keyboard. They were the same six that had no accessible name
(`test_control_names.py`); unreachable by ear and unreadable by eye was one
oversight wearing two hats.

What this scan can get wrong, in the order the mistakes are easy to make:

1.  **`outline: none` is not the only spelling.** `outline: 0`,
    `outline-width: 0`, `outline-style: none` and `outline-color: transparent`
    all do the same damage, and a grep for the first one finds none of them.
    None appear in this repo today, which is exactly the condition under which
    a narrow scanner looks correct forever.

2.  **A suppression inside a `:focus` rule is a different animal from one on
    a base rule.** The first is a decision someone made about focus; the
    second removes the ring at all times and usually was not about focus at
    all. Both need to be found, only one of them is ever defensible, and
    lumping them together would have made the sandbox bug look like the two
    legitimate cases sitting next to it.

3.  **A ring that is drawn and cannot be seen is not a ring.** Suppression is
    the loud failure mode; a 1.9:1 outline against the card it sits on is the
    quiet one, and no amount of counting `outline: none` finds it.

The parser is the one `test_contrast_floor` already has. A second CSS parser
in this repo would be a second set of bugs, and this file needs exactly the
thing that one already does: flatten the at-rule wrappers so a rule inside a
`@media` block is scored like any other.
"""

import re

from test_contrast_floor import (GRAPHIC_FLOOR, PAGE, ROOT, SHEETS, blocks,
                                 over, parse, ratio, read, theme)

SUPPRESS = re.compile(
    r'outline\s*:\s*(?:none|0)\b'
    r'|outline-width\s*:\s*0(?![.\d])'
    r'|outline-style\s*:\s*none'
    r'|outline-color\s*:\s*transparent', re.I)

# Selectors allowed to put the ring out, and the reason each one is not the
# bug this file exists to catch. Keyed by selector rather than by line number:
# the gate next door pins `inset` to a line and a six-line comment broke it,
# which is a good lesson to only learn once. Both of these were commented in
# place by whoever wrote them, which is what makes them decisions rather than
# leftovers -- and the tests below re-derive that rather than trusting it.
EXEMPT = {
    # Focused by script the moment the dialog opens. Nobody put the caret
    # there with Tab, so a ring around the whole 680px panel would be
    # announcing something the user did not do.
    '.sheet:focus': ('live-tagging/tagging.css', 'programmatic'),
    # Trades the outline for a stroke on the mark itself. The default ring is
    # a rectangle around the bounding box of a circle whose radius encodes xG,
    # which on the smallest marks is a few pixels of ring in a crowded
    # goalmouth. Still an indicator, just not that one.
    '.shot-mark.is-pickable:focus-visible': ('assets/app.css', 'replaced'),
}

RING = 'assets/app.css'


def rules(rel):
    return [(rel, sel, body) for sel, body in blocks(read(rel))]


ALL = [r for rel in SHEETS for r in rules(rel)]


def suppressions(scope=None):
    """Every rule that puts a focus ring out, wherever it lives."""
    return [(rel, sel, body) for rel, sel, body in (scope or ALL)
            if SUPPRESS.search(body)]


def specificity(sel):
    """(ids, classes+attributes+pseudo-classes, elements+pseudo-elements).

    Enough of CSS 2.1 to reason about the rules in this repo and no more.
    `:not()`, `:is()` and `:where()` take their weight from their arguments
    and are not handled -- the assertion refuses a selector containing one
    rather than scoring it wrong, because a specificity function that is
    quietly approximate is worse than one that stops.
    """
    assert not re.search(r':(?:not|is|where|has)\(', sel), sel
    ids = len(re.findall(r'#[-\w]+', sel))
    s = re.sub(r'#[-\w]+', ' ', sel)
    els = len(re.findall(r'::[-\w]+', s))
    s = re.sub(r'::[-\w]+', ' ', s)
    mid = len(re.findall(r'\.[-\w]+', s))
    s = re.sub(r'\.[-\w]+', ' ', s)
    mid += len(re.findall(r'\[[^\]]*\]', s))
    s = re.sub(r'\[[^\]]*\]', ' ', s)
    mid += len(re.findall(r':[-\w]+', s))
    s = re.sub(r':[-\w]+', ' ', s)
    els += len(re.findall(r'(?<![-\w])[a-zA-Z][-\w]*', s))
    return (ids, mid, els)


def ring():
    """(selector, declarations) for the site-wide focus ring."""
    found = [(sel, body) for sel, body in blocks(read(RING))
             if sel == ':focus-visible']
    assert len(found) == 1, found
    return found[0]


RING_SPEC = specificity(':focus-visible')


# --- the finding ------------------------------------------------------------

class TestNothingSilentlyPutsTheRingOut:

    def test_no_suppression_sits_on_a_base_rule(self):
        """The sandbox bug, and the one shape with no legitimate instance.

        A rule that is not about focus has no business deciding what focus
        looks like. There is no exemption list here on purpose: every case
        this has ever had was somebody resetting an element's appearance and
        taking the ring with it as a side effect.
        """
        base = [(rel, sel) for rel, sel, _ in suppressions()
                if ':focus' not in sel]
        assert base == [], base

    def test_every_focus_suppression_is_named_and_explained(self):
        found = {sel: rel for rel, sel, _ in suppressions()}
        assert found == {s: r for s, (r, _) in EXEMPT.items()}, found

    def test_every_exemption_is_still_putting_a_ring_out(self):
        """The direction that rots if nobody checks it.

        Take the `outline: none` out of one of these and the entry here
        survives as permission for the next person to put one back without
        arguing for it. An allowlist that only ever fails in one direction
        becomes a graveyard.
        """
        live = {sel for _, sel, _ in suppressions()}
        assert set(EXEMPT) == live, set(EXEMPT) ^ live

    def test_the_one_that_claims_a_replacement_declares_one(self):
        """'Replaced' has to mean something a person can see.

        Scoring the stroke's own contrast belongs to the contrast gate, which
        already carries `--text` on the pitch backdrop. What is checked here
        is narrower and is the part that would rot: that the block still
        paints *something* where the ring used to be.
        """
        claimed = [s for s, (_, kind) in EXEMPT.items() if kind == 'replaced']
        assert claimed == ['.shot-mark.is-pickable:focus-visible'], claimed
        for sel in claimed:
            body = [b for _, s, b in suppressions() if s == sel][0]
            names = {d.split(':')[0].strip() for d in body.split(';')
                     if ':' in d}
            assert {'stroke', 'stroke-width'} <= names, names


# --- specificity is the mechanism, not carelessness -------------------------

class TestWhyOneLineWasEnough:

    def test_the_ring_is_the_weakest_selector_that_could_paint_it(self):
        sel, body = ring()
        assert specificity(sel) == RING_SPEC == (0, 1, 0)
        assert 'outline' in body

    def test_a_bare_element_selector_would_not_have_managed_it(self):
        """The trap is narrower than "never write outline: none".

        `input { outline: none }` loses to the ring and would have been
        harmless. It was the `[type="range"]` that carried the rule over the
        line -- an attribute *and* an element name against a lone
        pseudo-class. Nobody adding an appearance reset is thinking about
        that, which is why this needs a test rather than a convention.
        """
        assert specificity('input') < RING_SPEC
        assert specificity('input[type="range"]') > RING_SPEC

    def test_every_suppression_on_the_site_outranks_the_ring(self):
        """Otherwise the entry is describing a rule that never fires.

        A suppression the ring already beats is not an exemption, it is dead
        code with a comment on it.
        """
        for _, sel, _ in suppressions():
            assert specificity(sel) > RING_SPEC, sel


# --- a ring nobody can see is not a ring ------------------------------------

class TestTheRingCanBeSeen:

    def colour(self):
        sel, body = ring()
        m = re.search(r'outline\s*:\s*[\d.]+px\s+solid\s+var\(\s*(--[-\w]+)',
                      body)
        assert m, body
        return m.group(1)

    def test_it_is_a_token_and_not_a_literal(self):
        assert self.colour() in theme(), self.colour()

    def test_it_clears_the_non_text_floor_on_every_ground(self):
        """WCAG 1.4.11: 3:1, the same floor the graphical marks are held to.

        Screen only. The print theme recolours `--accent`, and paper has no
        keyboard focus to indicate.
        """
        t = theme()
        fg = parse(t[self.colour()])
        got = []
        for token in PAGE:
            bg = parse(t[token])
            got.append((ratio(over(fg, bg), bg), token))
            assert got[-1][0] >= GRAPHIC_FLOOR, got[-1]
        worst = min(got)
        assert (worst[1], round(worst[0], 2)) == ('--surface-hi', 8.53), worst


# --- the ways this scan could be lying --------------------------------------

class TestTheScanCouldBeLying:

    def test_it_would_see_the_other_four_spellings(self):
        """None of these are in the repo, which is the whole problem.

        A scanner whose universe is one string looks perfect right up until
        somebody writes the synonym, and then it keeps looking perfect.
        """
        for decl in ('outline: 0;', 'outline-width: 0;',
                     'outline-style: none;', 'outline-color: transparent;',
                     'outline:none', 'OUTLINE: NONE;'):
            css = '.x:focus { %s }' % decl
            assert suppressions(rules_of(css)), decl

    def test_it_does_not_cry_wolf_over_a_ring_being_drawn(self):
        for decl in ('outline: 2px solid var(--accent);',
                     'outline-width: 0.5px;', 'outline-offset: 0;'):
            css = '.x:focus { %s }' % decl
            assert not suppressions(rules_of(css)), decl

    def test_it_looks_inside_media_queries(self):
        """Where a suppression is most likely to hide and least likely to be
        read -- `@media (hover: hover)` blocks are full of interaction CSS."""
        css = '@media (hover: hover) { .x:focus { outline: none; } }'
        assert [s for _, s, _ in suppressions(rules_of(css))] == ['.x:focus']

    def test_it_reads_every_stylesheet_the_site_ships(self):
        on_disk = sorted(p.relative_to(ROOT).as_posix()
                         for p in ROOT.glob('*/*.css'))
        assert on_disk == sorted(SHEETS), on_disk

    def test_it_finds_rules_at_all(self):
        assert len(ALL) >= 1000, len(ALL)
        for rel in SHEETS:
            assert rules(rel), rel


def rules_of(css):
    """Synthetic markup for the universe tests above.

    The scan has to be exercised on CSS that is not on disk. A branch that
    only ever sees the eight real sheets is a branch tested by whatever those
    sheets happen to contain today, which for four of the five spellings
    above is nothing at all.
    """
    return [('synthetic.css', sel, body) for sel, body in blocks(css)]
