"""One set of event types, written out three times, agreed on by nothing.

`cv/events.py` declares eight of them. `coach/review.js:57` offers a reviewer
eight words to retype an event into. They are the same eight words, typed out
twice in two languages, and nothing held them to each other -- so a ninth type
learned by the classifier would reach a coach's screen with no legal thing to
call it, and a word added to the reviewer's list that the pipeline never
produces would give that type a precision figure with an empty denominator
under it.

What the pipeline actually emits is seven. `clearance` is declared, offered to
a coach, and produced by nothing.

**That asymmetry is right, and it had never been written down.** A clearance
and an interception are the same contact seen from the same camera; what
separates them is what the defender did next and whether they meant to keep the
ball, and nothing here measures either. A coach watching the clip can tell in
one viewing. So the type belongs on the reviewer's list precisely because the
pipeline cannot produce it -- it is the correction the review tool exists to
collect. Until now the only record of that was its absence, which reads exactly
like an oversight, and `DefensiveAction`'s docstring actively said otherwise:
it listed clearances among the things the classifier produces.

`cv/events.py` now carries `EVENT_TYPES` and `HUMAN_ONLY_TYPES`, and this file
holds all three vocabularies to each other in both directions. Both directions,
because both go wrong quietly. A type the pipeline emits and nobody declared is
an event a reviewer cannot judge. A type declared and never emitted is either a
deliberate human-only word or a dead one, and the roster is what tells the next
reader which -- a name left on `HUMAN_ONLY_TYPES` after the classifier learns to
produce it turns a real detection into a permanent excuse.

Two more vocabularies for the same thing sit downstream, and the defensive four
are the ones that go stale silently: `DefensiveAction`'s docstring, and the team
counter loop in `cv/report_json.py:373`. A fifth defensive kind added to
`_defensive_action` reaches neither on its own, and a team that made twelve of
them would be published as having made none.
"""
import inspect
import re
from pathlib import Path

from cv import events, report_json
from cv.events import (
    EVENT_TYPES,
    HUMAN_ONLY_TYPES,
    DefensiveAction,
)

ROOT = Path(__file__).resolve().parents[1]

# `type=SHOT,` in a constructor and `kind = TACKLE` in the defensive classifier
# are the only two shapes that put a type on an event. Both sit alone on an
# indented line. `type=kind,` at `cv/events.py:642` is the classifier handing
# its answer on, not a type of its own, and is correctly missed by `[A-Z_]+`.
EMITTED = re.compile(r'^\s+(?:type|kind)\s*=\s*([A-Z_]+),?$', re.M)

# The one branch chain that decides which defensive action a touch was.
KIND = re.compile(r'^\s+kind = ([A-Z_]+)$', re.M)

# ('tackles', TACKLE) -- the name of the published counter beside the type that
# feeds it.
COUNTER = re.compile(r"\('\w+', ([A-Z_]+)\)")

REVIEW_LIST = re.compile(r'REVIEW_TYPES\s*=\s*\[(.*?)\]', re.S)
QUOTED = re.compile(r"'([a-z_]+)'")


def named(source, pattern):
    """The type *values* a pattern finds names for, in `cv/events.py`.

    A name that is not a constant on the module falls back to the name itself,
    which can never be a type value -- so a scan that finds a stranger fails the
    comparison instead of raising somewhere unhelpful.
    """
    return {getattr(events, name, name) for name in pattern.findall(source)}


def emitted():
    """Every type the pipeline can put on an event, read off the source."""
    return named(inspect.getsource(events), EMITTED)


def defensive_kinds():
    """The subset of those the defensive classifier decides between."""
    return named(inspect.getsource(events._defensive_action), KIND)


def offered():
    """The words `coach/review.js` offers a reviewer, read off the file."""
    text = (ROOT / 'coach' / 'review.js').read_text(encoding='utf-8')
    match = REVIEW_LIST.search(text)
    assert match, 'REVIEW_TYPES is not where this file looks for it'
    words = QUOTED.findall(match.group(1))
    assert words, 'REVIEW_TYPES was found and read as empty'
    return set(words)


class TestTheDeclaredVocabulary:
    """`EVENT_TYPES` against what the pipeline can actually produce."""

    def test_the_declared_types_are_the_emitted_ones_plus_the_human_only_ones(self):
        """Nothing declared is unaccounted for, and nothing emitted undeclared.

        A type emitted and never declared is an event that arrives at the review
        tool as a word its dropdown has never heard of. A type declared and
        never emitted is dead vocabulary unless someone said why, and saying why
        is what `HUMAN_ONLY_TYPES` is for. Held as one equality, so a type that
        moves between the two sides has to move on both rosters at once.
        """
        assert emitted() == set(EVENT_TYPES) - set(HUMAN_ONLY_TYPES)

    def test_the_pipeline_still_produces_something(self):
        """The anti-vacuum guard.

        Every check here compares two sets read out of source text. Rename the
        constructor argument, reshape the classifier, and the scan finds nothing
        -- at which point the equality above is between two empty sets and
        passes with the whole vocabulary unchecked. Seven is not a target; it is
        a floor that says the reader still works.
        """
        assert len(emitted()) >= 5
        assert set(EVENT_TYPES) >= emitted()

    def test_every_human_only_type_is_one_a_reviewer_could_pick(self):
        """A word excused from the pipeline still has to exist.

        `HUMAN_ONLY_TYPES` is a subset claim, not a list of its own. A name on it
        that is not on `EVENT_TYPES` -- a typo, a renamed constant -- excuses
        nothing, silently stops excusing whatever it was meant to, and cannot be
        seen by the equality above, which subtracts it and finds nothing gone.
        """
        assert set(HUMAN_ONLY_TYPES) <= set(EVENT_TYPES)
        assert not set(HUMAN_ONLY_TYPES) & emitted()


class TestWhatTheReviewerIsOffered:
    """`coach/review.js` against `cv/events.py`, across the language boundary.

    These two lists are the same list. They are written twice because one is
    read by Python and the other by a browser, and there is no build step to
    generate either from the other -- so the only thing that can keep them equal
    is a check that reads both.
    """

    def test_the_reviewer_is_offered_exactly_the_declared_types(self):
        """Both directions, and both are a coach's problem.

        A declared type missing from the list is an event the review tool shows
        with no way to say what it really was. A word on the list that is not a
        declared type is a retype target that produces events the pipeline's own
        vocabulary has no name for, which then reach `EVENT_COUNTERS` and the
        team stats as a type nothing counts.

        Order is not asserted. The two lists happen to agree on it and there is
        no reader that depends on them doing so.
        """
        assert offered() == set(EVENT_TYPES)


class TestTheDefensiveFour:
    """The classifier's own vocabulary, and the two places that restate it."""

    def test_the_docstring_names_exactly_what_the_classifier_can_decide(self):
        """Prose that lists a vocabulary is a vocabulary that can go stale.

        `DefensiveAction`'s summary line said "a tackle, interception, recovery,
        clearance or duel" while `_defensive_action` had no clearance branch and
        never would. It is the first thing a reader of `cv/events.py` sees about
        the type, and it was wrong in the one direction that matters: it made a
        deliberate human-only word look like something the pipeline produces.

        Only the summary line is read. The paragraph under it exists to say what
        the type is *not*, and naming a word there has to stay legal.
        """
        summary = inspect.getdoc(DefensiveAction).strip().split('\n')[0]
        assert set(re.findall(r'[a-z]+', summary)) & set(EVENT_TYPES) == \
            defensive_kinds()

    def test_every_defensive_kind_reaches_a_team_counter(self):
        """A kind with no counter is a column of football that goes unpublished.

        `team_stats` walks four (name, type) pairs and counts each. Add a fifth
        branch to the classifier and the events are derived, carried through the
        log, published on the event list -- and the team totals say the team did
        it zero times, which is a number, and wrong, rather than an absence.
        """
        source = inspect.getsource(report_json.team_stats)
        assert named(source, COUNTER) == defensive_kinds()

    def test_the_defensive_kinds_are_a_real_part_of_the_whole(self):
        """The anti-vacuum guard for the two checks above.

        Both read the classifier through one regex. If it stops matching, both
        comparisons become "the empty set equals the empty set" -- the docstring
        check passes with the docstring naming nothing, and the counter check
        passes with `team_stats` counting nothing.
        """
        kinds = defensive_kinds()
        assert len(kinds) >= 3
        assert kinds < emitted()
