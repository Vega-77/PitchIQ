"""The three words a coach's verdict can be, and the four places that use them.

A verdict is what a coach says about one thing the pipeline claimed to see:
`confirmed`, `rejected` or `edited`. It is written by exactly one thing — the
button `coach/review.js` renders, whose `data-act` attribute `decide` sends
straight to Firestore — and read by three others that cannot see it: the
scorecard arithmetic in `assets/report.js`, the coloured bar down the side of a
reviewed row in `assets/app.css`, and the progress line above the list.

`firestore.rules` is not one of the four. It says so itself, at the `cvReview`
match: the shapes inside `byEvent` are not validated there, deliberately,
because it is a map of hundreds of small decisions written from a phone at the
side of a pitch. Nothing rejects a verdict nobody recognises.

Which would be survivable if an unrecognised verdict were ignored. It is not.
`hasVerdict` asks only whether `status` is a non-empty string, so a verdict
under a name nothing knows still counts as *a coach checked this one*, and
`reviewScore` — finding it is neither `rejected` nor an `edited` retype — scores
it a **true positive**. Rename the reject button's attribute and a clip whose
candidates a coach threw half of away comes back reporting near-perfect
precision. Nothing raises. Nothing logs. The one number the review tool exists
to produce is confidently wrong.

So the words are declared once, in `assets/report.js`, and everything else
either imports them or is checked here against them. That leaves the CSS, which
cannot import anything, and this file pins it in both directions: a state the
tool can reach has to be styled, and a state that is styled has to be one the
tool can reach.

`coach/coach.js` also uses `data-act`, for row actions — invite, remove, erase
— and is deliberately not covered. Those values are read back by
`querySelector('[data-act="remove"]')` in the same file and dereferenced
without a guard, so a disagreement there is a TypeError on the spot. This file
is about the disagreement that stays quiet.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_review_verdicts.py -q
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'assets' / 'report.js'
REVIEW = ROOT / 'coach' / 'review.js'
SHEET = ROOT / 'assets' / 'app.css'

#: `export const CONFIRMED_STATUS = 'confirmed';`
DECLARED = re.compile(r"^export const ([A-Z]+_STATUS) = '([a-z]+)';$", re.M)

#: Whatever a `data-act` attribute carries, interpolation and all.
ACT = re.compile(r'data-act="([^"]*)"')

#: A class the review tool hard-codes rather than building from a verdict.
ADDED = re.compile(r"classList\.add\('(is-[a-z-]+)'\)")
ASSIGNED = re.compile(r"className = '([^']*\breview-row\b[^']*)'")

#: A row state the stylesheet has an opinion about.
STYLED = re.compile(r'\.review-row\.(is-[a-z-]+)')


@pytest.fixture(scope='module')
def report() -> str:
    return REPORT.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def review() -> str:
    return REVIEW.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def verdicts(report) -> dict[str, str]:
    """`{CONFIRMED_STATUS: 'confirmed', ...}`, read off the declarations."""
    return dict(DECLARED.findall(report))


def act_values(source: str) -> list[str]:
    """Every `data-act` attribute value in `source`, in order.

    Deliberately not blanked first. These live inside a template literal, and
    `blank` empties string literals down to their quote characters — the whole
    attribute would vanish. What is wanted here is the opposite of what a
    blanked scan gives: the text as written, so an interpolation can be told
    apart from a string somebody typed twice.
    """
    return ACT.findall(source)


def producible(source: str, verdicts: dict[str, str]) -> set[str]:
    """Every `is-` class the review tool can put on a row.

    Two kinds. `is-${decided.status}` builds one per verdict, which is why the
    verdicts are needed to answer this at all. The rest are typed out —
    `is-tagged` on a row that came from the tag log rather than the pipeline,
    `is-missed-goal` on one a coach recorded by hand — and are found by the
    shape of the call that sets them rather than by a list kept here, so adding
    a third needs no edit to this file.
    """
    classes = {f'is-{value}' for value in verdicts.values()}
    classes.update(ADDED.findall(source))
    for names in ASSIGNED.findall(source):
        classes.update(n for n in names.split() if n.startswith('is-'))
    return classes


class TestOneDeclaration:
    def test_the_three_verdicts_are_declared_and_exported_in_one_file(
        self, verdicts
    ):
        """Exported, because the only thing that writes one is elsewhere."""
        assert verdicts == {
            'CONFIRMED_STATUS': 'confirmed',
            'REJECTED_STATUS': 'rejected',
            'EDITED_STATUS': 'edited',
        }

    def test_the_review_tool_declares_none_of_its_own(self, review):
        """It had three of them, and used one.

        `CONFIRMED` sat at the top of that file declared and never referenced,
        while the button that writes `confirmed` spelled the word out by hand
        five hundred lines below it. That is not a naming quibble: a constant
        nothing reads is a constant nobody thinks to change, and the copy that
        matters was the one no reader would look for.
        """
        for value in ('confirmed', 'rejected', 'edited'):
            assert f"'{value}'" not in review

    def test_the_review_tool_imports_all_three(self, review, verdicts):
        block = review.split("} from '../assets/report.js")[0]
        for name in verdicts:
            assert name in block, f'{name} is not imported from report.js'


class TestTheButtons:
    def test_every_verdict_button_carries_an_interpolated_constant(
        self, review, verdicts
    ):
        """The attribute is the definition, so it may not be a second copy.

        `decide(event.id, { status: button.dataset.act })` sends the attribute
        to Firestore unexamined. A literal here would be a fourth place that
        has to agree with the other three and cannot be told when it stops.
        """
        values = act_values(review)
        assert values, 'no data-act attributes found — did the scan break?'
        for value in values:
            assert re.fullmatch(r'\$\{([A-Z]+_STATUS)\}', value), (
                f'data-act="{value}" is written out rather than interpolated'
            )
            assert value[2:-1] in verdicts

    def test_all_three_verdicts_have_a_button(self, review, verdicts):
        """Both directions of the same seam: three constants, three buttons.

        A verdict `report.js` scores and no button can produce is dead weight
        in the arithmetic; a button whose constant nothing scores writes a
        status the scorecard silently counts as a true positive.
        """
        assert sorted(v[2:-1] for v in act_values(review)) == sorted(verdicts)

    def test_the_scan_can_tell_an_interpolation_from_a_literal(self):
        """The failure this file was written about, in one line.

        Before the fix the attribute really was `data-act="confirmed"`, and a
        check that could not see the difference between that and
        `data-act="${CONFIRMED_STATUS}"` would have passed on the version with
        the bug in it.
        """
        assert act_values('<button data-act="${EDITED_STATUS}">') == [
            '${EDITED_STATUS}'
        ]
        assert act_values('<button data-act="edited">') == ['edited']
        assert act_values('<button class="btn">') == []


class TestTheStyling:
    def test_every_styled_review_row_state_is_one_the_tool_can_produce(
        self, review, verdicts
    ):
        """A rule for a class nothing sets is a rule nobody can find.

        It reads as coverage — the reviewed rows are coloured, so the colours
        must be right — while the state it was written for renders with no
        colour at all.
        """
        styled = set(STYLED.findall(SHEET.read_text(encoding='utf-8')))
        assert styled, 'no .review-row.is-* rules found — did the scan break?'
        assert not styled - producible(review, verdicts)

    def test_every_state_the_tool_can_produce_is_styled(self, review, verdicts):
        """The direction that loses the bar down the side of a rejected row.

        `is-${decided.status}` puts the class on regardless, so a verdict the
        stylesheet has never heard of is not an error — it is a row that looks
        exactly like an unreviewed one, in a list of four hundred where the
        only way to see what has been done is that stripe of colour.
        """
        reachable = producible(review, verdicts)
        assert len(reachable) >= 4, 'the class scan found almost nothing'
        styled = set(STYLED.findall(SHEET.read_text(encoding='utf-8')))
        assert not reachable - styled


class TestWhyThisMatters:
    def test_any_string_still_counts_as_a_verdict(self, report):
        """Pinned because it is the reason the checks above are load-bearing.

        `hasVerdict` is a truthiness test, so nothing downstream rejects a
        status it does not recognise — it scores one. If this ever becomes a
        membership test the danger drains out of this whole seam, and whoever
        makes that change should be sent here to say so rather than find the
        file still asserting a premise that stopped being true.
        """
        assert (
            'export const hasVerdict = (decision) => Boolean(decision?.status);'
            in report
        )
