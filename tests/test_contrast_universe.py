"""What the contrast gate is allowed to not look at.

`test_contrast_floor.py` answers "does every colour clear its floor". This
file answers the question underneath it: **is the gate looking at everything?**
They are separate files because the second one is where the failures have
actually come from, and because a scanner that reports zero defects over the
wrong universe is indistinguishable from one that reports zero defects over
the right one.

Seven ways a universe has lied in this repo so far, each of which cost a real
wrong answer before it was found:

1. **Too narrow.** The id scanner followed only ids and reported three live
   calibrate buttons as dead.
2. **Too wide.** A paper scan run over five pages that cannot print turned a
   correctly hidden `.brand` into a defect.
3. **Too shallow.** A `color:`-only scanner saw 2 of 17 instances of the thing
   it was written to find.
4. **Frozen.** A hand-maintained list of defects is a memory, not a
   measurement, and reported three that had already been fixed.
5. **Absent entirely.** `landing.css` was scored by no contrast test at all,
   while both sibling gates carried it. The front page is the one page a
   stranger is guaranteed to see.
6. **Double-counted.** `blocks()` flattens at-rules, so every print-only rule
   was also scored against the screen theme it never meets.
7. **Vacuous.** An exemption list that came back empty exempted *everything*,
   silently, while every assertion still reported success.

And an eighth, which is why one table in the gate is checked rather than
believed: **absence is not evidence of hiding.** See the last class here.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_contrast_floor import (  # noqa: E402
    BG, CLASS_ATTR, COLOR, GRAPHIC_FLOOR, GROUND_LITERALS, HAIRLINE, HTML,
    JS_DIRS, JS_HOSTS, PAGE, PRINTABLE, PRINTS, SHEETS, SIZED, TEXT_FLOOR,
    VAR, ancestor_ground, blocks, failures, hidden_names, js_hidden,
    hidden_selectors, parse, ratio, read, roots, scored, strip_comments,
    theme, values, without_print,
)

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'<link[^>]+rel="stylesheet"[^>]*>')
HREF = re.compile(r'href="([^"?]+)')
INSET = re.compile(r'(?<![-\w])inset\s*:\s*([^;}]+)')


def linked():
    """Every stylesheet the site's own pages ask a browser to load."""
    out = set()
    for rel in HTML:
        here = Path(rel).parent
        for m in LINK.finditer(read(rel)):
            h = HREF.search(m.group(0))
            if h is None or h.group(1).startswith('http'):
                continue          # the font stylesheet is not ours to score
            out.add((here / h.group(1)).resolve()
                    .relative_to(ROOT).as_posix())
    return out


def literals():
    """(kind, sheet, selector, colour) for every declaration written as a
    bare colour rather than as a theme token."""
    out = []
    for rel in SHEETS:
        for sel, body in blocks(read(rel)):
            head = sel.splitlines()[-1].strip()[:38]
            for kind, pat in (('ink', COLOR), ('ground', BG)):
                for m in pat.finditer(body):
                    v = values(m.group(1))
                    if v and not v[0].startswith('--'):
                        out.append((kind, rel, head, v[0]))
    return out


class TestEverySheetIsScored:
    """Direction 5. The check that would have caught `landing.css`."""

    def test_no_linked_sheet_goes_unscored(self):
        missing = linked() - set(SHEETS)
        assert missing == set(), missing

    def test_no_scored_sheet_is_unlinked(self):
        # The other direction, and it is not decoration: a sheet nobody loads
        # is dead code, and scoring it would be work invented for somebody.
        stale = set(SHEETS) - linked()
        assert stale == set(), stale

    def test_there_are_eight_of_them(self):
        assert len(SHEETS) == 8, SHEETS


class TestPrintRulesAreNotScoredTwice:
    """Direction 6. `blocks()` flattens at-rules, so a rule inside
    `@media print` comes back looking exactly like one outside it."""

    def test_the_print_theme_is_cut_out_of_the_screen_source(self):
        app = read('assets/app.css')
        assert '@media print {' in app
        assert '@media print {' not in without_print(app)

    def test_a_paper_only_background_is_invisible_on_screen(self):
        # `background: #fff` appears twice, both times inside the print
        # block. Scored against the dark screen theme it read 1.17 -- a
        # defect in a medium where the rule does not exist.
        assert 'background: #fff' in read('assets/app.css')
        assert 'background: #fff' not in without_print(read('assets/app.css'))

    def test_a_sheet_with_no_print_block_is_untouched(self):
        # Seven of the eight have no print theme, and the cut must be a
        # no-op on them rather than merely harmless.
        n = 0
        for rel in SHEETS:
            src = read(rel)
            if '@media print {' not in src:
                assert without_print(src) == strip_comments(src), rel
                n += 1
        assert n == 7, n

    def test_a_print_only_selector_produces_no_screen_row(self):
        # `.print-stamp` is the line naming the match at the top of a printed
        # sheet; it exists nowhere else. `body` is the subtler one -- it has
        # a rule in both themes, so the tell is not the selector but the
        # ground: on paper it letters `var(--text)` onto white, which read
        # 1.17 against the dark screen theme. A defect invented entirely out
        # of scoring a rule against a medium it never renders in. Leaving the
        # print theme in adds exactly three rows, one of them that failure.
        _, rows = scored(SHEETS, printed=False)
        here = [r for r in rows if r[4].startswith('assets/app.css')]
        assert not [r for r in here if '.print-stamp' in r[4]], here
        assert not [r for r in here if r[3] in ('#fff', '#ffffff')], here
        assert failures(SHEETS, printed=False) == []


class TestInsetIsNotASize:
    """A mark states its own size, because being that size is the datum. A
    container lets its contents decide. `inset` says "as big as the thing I
    am in", which is the container's answer -- and counting it scored a
    fullscreen scrim as though its dimness were a measurement, at 1.06."""

    def uses(self):
        out = []
        for rel in SHEETS:
            for i, line in enumerate(read(rel).splitlines(), 1):
                m = INSET.search(line)
                if m:
                    out.append(('%s:%d' % (rel, i), m.group(1).strip()))
        return out

    def test_all_five_uses_position_rather_than_size(self):
        assert self.uses() == [
            ('assets/app.css:294', '0'),
            ('assets/app.css:311', '-40% -30%'),
            ('assets/app.css:1529', 'auto 0 0 0'),
            ('live-tagging/tagging.css:563', '0'),
            ('xg-sandbox/sandbox.css:320', '0'),
        ], self.uses()

    def test_the_scanner_does_not_count_it(self):
        assert not SIZED.search('inset: 0;')
        assert SIZED.search('width: 12px;')


class TestLiteralColoursAreVisible:
    """Direction 3, in the form it took here. Thirty-six ink and ground
    declarations in this tree are written as bare hex, and a scanner that
    reads only `var()` cannot see a single one of them."""

    def test_a_token_only_scanner_would_see_none_of_them(self):
        for kind, rel, head, v in literals():
            assert VAR.findall(v) == [], (rel, head, v)

    def test_the_seven_literal_inks(self):
        # Pinned by name because these are the ones a contrast floor is for.
        # Four of them are the same near-black lettering on the accent, which
        # is what the exemption in the floor gate is now carrying.
        inks = [(rel, head, v) for kind, rel, head, v in literals()
                if kind == 'ink']
        assert inks == [
            ('assets/app.css', '.btn.primary', '#04120f'),
            ('assets/app.css', '.toast.error', '#ffd4d4'),
            ('assets/app.css', '.chip.on', '#04120f'),
            ('assets/app.css', '.btn.tiny.on', '#04120f'),
            ('live-tagging/tagging.css', '.btn-kickoff', '#fff'),
            ('live-tagging/tagging.css', '.ev.flash .ev-label', '#04120f'),
            ('live-tagging/tagging.css', '.choice.second_yellow', '#fb923c'),
        ], inks

    def test_there_are_plenty_of_literal_grounds_too(self):
        grounds = [x for x in literals() if x[0] == 'ground']
        assert len(grounds) >= 25, len(grounds)

    def test_the_orange_second_yellow_clears_its_floor(self):
        # The one literal ink that is not near-black or near-white, and the
        # one worth re-checking: it is a third card colour sitting between
        # --card-yellow (10.04) and --danger (6.06), and it was written as a
        # literal rather than promoted to a token.
        t = theme(False)
        r = ratio(parse('#fb923c'), parse(t['--surface']))
        assert r >= TEXT_FLOOR
        assert round(r, 2) == 7.40, r


class TestBlackIsGroundAndWhiteIsNot:
    """`GROUND_LITERALS` is a waiver, so its universe is pinned and so is the
    subset it actually changes an answer for."""

    def sized_literal_grounds(self):
        out = {}
        for rel in SHEETS:
            for sel, body in blocks(read(rel)):
                if not SIZED.search(body) or HAIRLINE.search(body):
                    continue
                for m in BG.finditer(body):
                    for v in values(m.group(1)):
                        if not v.startswith('--') and parse(v) is not None:
                            out[sel.splitlines()[-1].strip()] = v
        return out

    def test_only_two_sized_elements_paint_a_bare_colour(self):
        assert self.sized_literal_grounds() == {
            '.loupe': '#000',
            '.toggle-thumb': '#fff',
        }, self.sized_literal_grounds()

    def test_the_waiver_covers_the_one_that_needs_it(self):
        # The loupe is the magnifier window over the calibration frame. Its
        # black is what sits behind the video, not a datum drawn on top of
        # one, and at 1.11 it would be reported as a defect every run.
        t = theme(False)
        r = min(ratio(parse('#000'), parse(t[p])) for p in PAGE)
        assert round(r, 2) == 1.11, r
        assert '#000' in GROUND_LITERALS

    def test_the_waiver_does_not_cover_the_one_that_does_not(self):
        # The sandbox toggle knob is a mark: it is the thing being read. It
        # was exempted for a while because white was in the set alongside
        # black, and it never needed to be -- 15.31 clears every floor here.
        # An exemption carrying nothing is how one grows until something real
        # falls through it.
        t = theme(False)
        r = min(ratio(parse('#fff'), parse(t[p])) for p in PAGE)
        assert round(r, 2) == 15.31, r
        assert r >= GRAPHIC_FLOOR
        assert '#fff' not in GROUND_LITERALS


class TestAGroundCanComeFromAnAncestor:
    """A descendant selector often sets an ink and no ground, because the
    ground is on its parent one rule up. Falling back to the page colours
    there is not a near miss -- it scores the mark against something it is
    never drawn on."""

    def test_the_flashed_event_label_is_read_on_the_accent(self):
        rules = {}
        for sel, body in blocks(without_print(read('live-tagging/tagging.css'))):
            rules.setdefault(sel.splitlines()[-1].strip(), body)
        assert ancestor_ground(rules, '.ev.flash .ev-label') == '--accent'

    def test_and_that_is_the_difference_between_1_01_and_10_65(self):
        # `.ev.flash .ev-label` is `#04120f` on `.ev.flash`'s `--accent`. It
        # is one of the highest-contrast pairs on the site, and the page-
        # colour fallback reported it as the worst.
        t = theme(False)
        assert round(ratio(parse('#04120f'), parse(t['--accent'])), 2) == 10.65
        assert round(min(ratio(parse('#04120f'), parse(t[p]))
                         for p in PAGE), 2) == 1.01

    def test_this_is_not_a_one_off(self):
        # Every rule that sets an ink, sets no ground, and inherits one from
        # a prefix in the same sheet. Scoring these against the page instead
        # of against their own container is the general shape of the bug.
        n = 0
        for rel in SHEETS:
            rules = {}
            for sel, body in blocks(without_print(read(rel))):
                rules.setdefault(sel.splitlines()[-1].strip(), body)
            for head, body in rules.items():
                if not COLOR.search(body):
                    continue
                if [g for m in BG.finditer(body) for g in values(m.group(1))]:
                    continue
                n += ancestor_ground(rules, head) is not None
        assert n >= 40, n


class TestAbsenceIsNotHiding:
    """The eighth way a universe lies, and the reason `JS_HOSTS` is a checked
    table of three words rather than a rule.

    The tempting rule is: a class that appears in no printable markup cannot
    print, so do not score it against paper. It is wrong, and expensively so.
    This is a JS-rendered app -- the markup is a static shell and very little
    else -- so most classes are absent from it, including the two that were
    the real defects this gate has found.
    """

    def absent(self):
        present = set()
        for rel in PRINTS:
            for m in CLASS_ATTR.finditer(read(rel)):
                present.update(m.group(1).split())
        out = set()
        for rel in PRINTABLE:
            for sel, _ in blocks(read(rel)):
                for c in re.findall(r'\.([a-zA-Z][\w-]*)',
                                    sel.splitlines()[-1]):
                    if c not in present:
                        out.add(c)
        return out

    def test_most_classes_are_absent_from_printable_markup(self):
        assert len(self.absent()) >= 200, len(self.absent())

    def test_an_absence_rule_would_have_deleted_the_gate_s_best_finding(self):
        # `.job-count` and `.staff-initial` were accent text on its own tint
        # over white at 4.23, under the 4.5 that 13.6px and 15.3px text is
        # owed. They are the reason the paper accent was darkened. Both are
        # absent from printable markup, so the absence rule would have
        # silenced them and reported success.
        assert {'job-count', 'staff-initial'} <= self.absent()

    def test_so_the_table_is_small_and_every_claim_in_it_is_checked(self):
        assert len(JS_HOSTS) == 1, JS_HOSTS
        assert js_hidden() == set(JS_HOSTS), js_hidden()

    def test_the_named_host_exists_and_is_itself_hidden(self):
        hidden = hidden_names()
        for cls, host in JS_HOSTS.items():
            seen = [rel for rel in PRINTS if 'id="%s"' % host in read(rel)]
            assert seen, host
            assert '#' + host in hidden or any(
                '.' + c in hidden
                for rel in seen
                for m in [re.search(r'<\w+[^>]*\bid="%s"[^>]*>' % host,
                                    read(rel))] if m
                for a in CLASS_ATTR.finditer(m.group(0))
                for c in a.group(1).split()), host

    def test_the_host_is_hidden_two_independent_ways(self):
        """Belt and braces, and worth knowing which is which.

        `.chips` is written into the print hide list by name, and the one
        element carrying it also sits inside `#cv-review-block`, which is
        written into the list too. Either alone would hide it. That is why
        no mutation of a single line can unhide this host, and why the case
        for it in the driver is a comment rather than a test: the direction
        that can go wrong is the table naming a host that is not there, and
        that one is covered above.
        """
        listed = hidden_selectors()
        assert '.chips' in listed
        assert '#cv-review-block' in listed
        src = read('coach/index.html')
        inside = [b for b in roots(src, '#cv-review-block')
                  if 'id="cv-review-filters"' in b]
        assert len(inside) == 1, inside

    def test_the_class_really_is_made_by_a_script(self):
        # If markup contained one, the ancestry derivation would already see
        # it and the table would be unnecessary.
        src = '\n'.join(read(p.relative_to(ROOT).as_posix())
                        for d in JS_DIRS
                        for p in sorted((ROOT / d).glob('*.js')))
        for cls in JS_HOSTS:
            assert re.search(r"""className\s*=\s*['"]%s['"]""" % cls, src) \
                or re.search(r"""classList\.\w+\(\s*['"]%s['"]""" % cls, src), \
                cls


class TestBothScansStillReportSomething:
    """The anti-vacuum guard, in the file that widened the universe. Every
    change here either found more to look at or was a mistake."""

    def test_the_screen_scan_covers_most_of_the_site(self):
        _, rows = scored(SHEETS, printed=False)
        assert len(rows) >= 350, len(rows)
        assert {r[0] for r in rows} == {'text', 'paint', 'mark'}

    def test_the_paper_scan_is_smaller_but_not_empty(self):
        _, paper = scored(PRINTABLE, printed=True)
        _, screen = scored(SHEETS, printed=False)
        assert 150 <= len(paper) < len(screen)

    def test_and_neither_of_them_finds_a_defect(self):
        assert failures(SHEETS, printed=False) == []
        assert failures(PRINTABLE, printed=True) == []
