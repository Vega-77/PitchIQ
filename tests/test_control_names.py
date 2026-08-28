"""Every control a person can operate is announced as something.

A button with no text, no `aria-label` and no label around it is read out as
"button" and nothing else. Sighted users are fine -- the words are usually
right there on screen, in a sibling element -- which is exactly why this
goes wrong silently and stays wrong.

The scanner's universe is the interesting half, the same as it was for
contrast. Three ways it can lie, all of them found by measurement rather
than by reasoning:

1.  **Too narrow.** The first version looked for `<label for=>` and reported
    thirty nameless controls. Not one control on this site is named that
    way; they are wrapped -- `<label class="field"><span>Team name</span>
    <input></label>` -- which names the control with no `for` anywhere. The
    tell was the count: a naming mechanism used by exactly zero controls in
    a form-heavy site is a mechanism the scanner is looking for in the wrong
    place. Twenty-three controls depend on it.

2.  **Absence is not namelessness.** One button is empty in the markup and
    stays hidden until a script writes its text. Scoring markup alone calls
    it a defect; it is not one. The exemption is a named list of one, and
    every entry has to prove itself -- the element exists, it really is
    empty, and a script really does set its text.

3.  **A pointer is only a name if it lands.** `aria-labelledby` naming an id
    that no longer exists is worse than nothing: it reads as a name to a
    scanner and as silence to a screen reader.

What it found, the day it first ran: the six xG sandbox sliders had no
accessible name at all. Their labels sit in a sibling div, so the whole
"Distance / Angle / Distance to shooter" column was visible and unspoken.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PAGES = ('index.html', 'coach/index.html', 'player/index.html',
         'live-tagging/index.html', 'halftime/index.html',
         'calibrate/index.html', 'xg-sandbox/index.html')

CONTROL = re.compile(r'<(button|a|input|select|textarea)\b([^>]*)>', re.I)
LABEL = re.compile(r'<label\b([^>]*)>', re.I)
ATTR = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')
TAG = re.compile(r'<[^>]+>')

# The one control whose name arrives at runtime. `renderTimeline` sets its
# text and unhides it in the same breath, so it is never both visible and
# unnamed. Named here rather than derived: a rule like "empty buttons are
# probably filled by a script" would exempt every future mistake too.
SCRIPT_NAMED = {'btn-more-timeline': 'halftime/halftime.js'}


def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def attrs(raw):
    return {k.lower(): v for k, v in ATTR.findall(raw)}


def text_of(fragment):
    return ' '.join(TAG.sub(' ', fragment).replace('&nbsp;', ' ').split())


def label_spans(src):
    """(start, end, text) for every `<label>` on the page.

    Labels do not nest anywhere in this repo and the assertion below keeps it
    that way -- a nested one would make "which label names this control" a
    question with two answers, and this function would quietly pick the outer.
    """
    out = []
    for m in LABEL.finditer(src):
        close = src.find('</label>', m.end())
        assert close > 0, (m.start(), 'unclosed label')
        body = src[m.end():close]
        assert '<label' not in body, (m.start(), 'nested label')
        out.append((m.end(), close, text_of(body)))
    return out


def controls(rel):
    """Every operable control on one page, with how it gets its name."""
    return controls_in(rel, read(rel))


def controls_in(rel, src):
    """The scan, over markup rather than over a path.

    Split out so the universe tests below can hand it markup that does not
    exist on disk. A branch that only ever sees the real pages is a branch
    tested by whatever the real pages happen to contain today.
    """
    ids = set(re.findall(r'\bid="([^"]+)"', src))
    spans = label_spans(src)
    for_ids = {a for a in (attrs(m.group(1)).get('for')
                           for m in LABEL.finditer(src)) if a}
    out = []
    for m in CONTROL.finditer(src):
        tag, a = m.group(1).lower(), attrs(m.group(2))
        if tag == 'input' and a.get('type', '').lower() == 'hidden':
            continue
        why = []
        if tag not in ('input', 'select', 'textarea'):
            close = src.find('</' + tag, m.end())
            if close > 0 and text_of(src[m.end():close]):
                why.append('text')
        if a.get('aria-label'):
            why.append('aria-label')
        if a.get('aria-labelledby'):
            # A pointer that misses is not a name, and must not read as one.
            if all(t in ids for t in a['aria-labelledby'].split()):
                why.append('labelledby')
        if a.get('title'):
            why.append('title')
        if a.get('id') and a['id'] in for_ids:
            why.append('label-for')
        if any(lo <= m.start() < hi and txt for lo, hi, txt in spans):
            why.append('label-wrap')
        if tag == 'input' and a.get('value') and a.get(
                'type', '').lower() in ('submit', 'button', 'reset'):
            why.append('value')
        out.append((rel, src[:m.start()].count('\n') + 1, tag,
                    a.get('id', ''), tuple(why)))
    return out


def scan():
    return [c for rel in PAGES for c in controls(rel)]


ALL = scan()


def nameless():
    return [c for c in ALL if not c[4]]


# --- the two findings ------------------------------------------------------

class TestEveryControlIsAnnouncedAsSomething:

    def test_nothing_operable_is_nameless(self):
        bare = [(rel, line, tag, i) for rel, line, tag, i, _ in nameless()
                if i not in SCRIPT_NAMED]
        assert bare == [], bare

    def test_every_exemption_is_still_carrying_its_weight(self):
        """The other direction, and the one that rots if nobody checks it.

        A name written into the markup later would leave the entry here
        exempting a control that no longer needs exempting -- harmless today,
        and a hole the moment somebody empties that element again.
        """
        for control_id in SCRIPT_NAMED:
            assert control_id in {c[3] for c in nameless()}, control_id

    def test_every_exemption_names_an_element_a_script_really_fills(self):
        for control_id, js in SCRIPT_NAMED.items():
            src = read(js)
            var = re.search(r'const (\w+) = byId\(%r\)' % control_id, src)
            assert var, control_id
            assert re.search(r'\b%s\.textContent = ' % var.group(1), src), js


# --- universe: the ways this scan could be lying ---------------------------

class TestAWrappingLabelIsAName:

    def test_a_for_only_scanner_would_invent_two_dozen_defects(self):
        # What the first version of this file did. Nothing on this site uses
        # `for=`, so dropping the wrapping case does not lose a mechanism --
        # it loses the only one that form controls here actually have.
        assert not [c for c in ALL if 'label-for' in c[4]]
        wrapped = [c for c in ALL if 'label-wrap' in c[4]]
        assert len(wrapped) == 23, len(wrapped)
        assert not [c for c in wrapped if set(c[4]) - {'label-wrap'}]

    def test_no_label_on_the_site_is_empty(self):
        # The text is what names; a label with none of it is decoration. If
        # this ever stops holding it means a label somewhere lost its span,
        # and every control inside it went silent while still looking wrapped.
        for rel in PAGES:
            for _, _, text in label_spans(read(rel)):
                assert text, rel

    def test_and_an_empty_one_would_name_nothing(self):
        # The other half, and the one the site cannot demonstrate on its own:
        # empty a label here and the control inside it has to go quiet. Without
        # this the scan could be treating "is inside a label" as the test and
        # ignoring the words, which is the same mistake as counting an
        # `aria-labelledby` without following it.
        src = read('coach/index.html')
        span = '<span>Team name</span>'
        assert src.count(span) == 1
        before = controls_in('x', src)
        after = controls_in('x', src.replace(span, ''))
        assert len(before) == len(after)
        gone = [b for b, a in zip(before, after) if b[4] and not a[4]]
        assert [g[3] for g in gone] == ['input-team-name'], gone


class TestAPointerIsOnlyANameIfItLands:

    def test_every_labelledby_on_a_control_resolves(self):
        for rel in PAGES:
            ids = set(re.findall(r'\bid="([^"]+)"', read(rel)))
            for m in CONTROL.finditer(read(rel)):
                target = attrs(m.group(2)).get('aria-labelledby')
                for token in (target or '').split():
                    assert token in ids, (rel, token)

    def test_a_dangling_pointer_does_not_count_as_a_name(self):
        """Break the real page in memory and watch the name disappear.

        Asserting that today's pointers resolve says nothing about what the
        scan would do if one stopped resolving -- it could be counting the
        attribute rather than reading it, and every test above would still
        pass. So take the page that has six of these, delete the id one of
        them points at, and require that the control goes quiet.
        """
        src = read('xg-sandbox/index.html')
        named = [c for c in controls_in('x', src) if 'labelledby' in c[4]]
        assert len(named) == 6, len(named)

        broken = src.replace('<span class="slider-name" id="name-distance">',
                             '<span class="slider-name">', 1)
        assert broken != src
        after = [c for c in controls_in('x', broken) if 'labelledby' in c[4]]
        assert len(after) == 5, len(after)
        assert len([c for c in controls_in('x', broken) if not c[4]]) == 1


class TestTheSlidersSayWhatIsOnTheScreen:

    def sliders(self):
        src = read('xg-sandbox/index.html')
        return re.findall(r'<input type="range" id="(\w+)"'
                          r' aria-labelledby="([\w-]+)"', src)

    def test_all_six_are_named(self):
        assert len(self.sliders()) == 6, self.sliders()

    def test_each_points_at_the_words_already_on_screen(self):
        """WCAG 2.5.3: what is heard has to contain what is seen.

        Pointing at the visible span rather than repeating it into an
        `aria-label` is what makes that true by construction -- there is one
        string, so the two cannot drift. The two sliders whose label carries
        a `measured` badge announce it too, which is right: it is part of
        what the label says.
        """
        src = read('xg-sandbox/index.html')
        for _, span_id in self.sliders():
            m = re.search(r'<span class="slider-name" id="%s">(.*?)</span>\s*'
                          r'<span class="slider-value"' % span_id, src,
                          re.S)
            assert m, span_id
            assert text_of(m.group(1)), span_id


# --- the scan is looking at something --------------------------------------

class TestTheScanReportsSomething:

    def test_it_reads_every_page_the_site_has(self):
        pages = sorted(p.relative_to(ROOT).as_posix()
                       for p in ROOT.glob('*/index.html'))
        pages = [p for p in pages if not p.startswith(('node_modules', 'cv'))]
        assert set(pages) | {'index.html'} == set(PAGES), pages
        for rel in PAGES:
            assert (ROOT / rel).is_file(), rel
            assert controls(rel), rel

    def test_it_finds_controls_at_all(self):
        assert len(ALL) >= 140, len(ALL)
        assert len(nameless()) <= len(SCRIPT_NAMED)

    def test_every_mechanism_it_knows_about_is_in_use(self):
        # A branch nothing exercises is a branch nobody would notice breaking.
        used = {w for c in ALL for w in c[4]}
        assert used == {'text', 'title', 'label-wrap', 'labelledby'}, used
        counted = {w: len([c for c in ALL if w in c[4]]) for w in used}
        # Named by their own text: a hundred and eighteen for a long while,
        # then a hundred and twenty-four, when six of the seven pages each
        # gained a skip link. `live-tagging` is the seventh and wants none:
        # its <main> is the first thing in its <body>.
        assert counted == {'text': 124, 'title': 3, 'label-wrap': 23,
                           'labelledby': 6}, counted
