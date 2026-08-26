"""Every figure inside the reconciliation block reaches a page, or says why not.

`tests/smoke.test.js` walks the top-level keys of the payload builders and checks
that some page reads each one. For four of those keys the check is empty, because
the key is a whole block forwarded whole:

    'reconciliation': report_json.get('reconciliation'),   # cv/publish.py

One name, one reader, gate satisfied. `tests/test_quality_seam.py`,
`tests/test_teams_seam.py` and `tests/test_keepers_seam.py` are this file for the
other three. This one closes the last of them, and the audit that produced it
found three published figures on no screen at all: `exit_agreement`, `exits` and
`exits_checked` — half of a shipped comparison, computed on every calibrated run
and rendered nowhere.

So this reads both ends as text. `Reconciliation.to_json` and
`Disagreement.to_json` in `cv/reconcile.py` say what is produced; the JavaScript
under the module directories says what is read. A field that is deliberately not
rendered has to be listed here with a reason somebody can argue with.

**Three producers, one namespace.** The block is nested where the other three are
flat: a rate and two count dicts at the top, `counts()` keys inside those, and a
list of rows with their own six fields. All of them are checked as one set of
names, because a reader reads `rec.goals` and `entry.status` the same way and
neither `goals` nor `status` collides with anything.

**Three shapes of reader, and every one of them is load-bearing.** The keepers
gate needed one step to reach a figure and the teams gate two; this block is
reached three ways, because it is nested a level deeper than either:

    const rec = options.reconciliation;          // hop one: names the block
    const goals = rec?.goals || {};              // hop two: an alias of an alias
    for (const entry of entries) entry.status;   // and a loop over the rows

Twelve of the fifteen figures reach a page. Drop hop two and the three count
keys go with it; drop the loop binding and the three row fields go. Both were
measured before they were written down, and both are pinned below, along with
the per-function split — `coach/review.js` binds `entry` in the conflict loop
and reads `item.entry.playerId` in a function four hundred lines away — and
one exclusion by name: `entries.length` is a property of every array and would
otherwise arrive as a figure the pipeline never made.

Reading text rather than executing it is crude and is the right crudeness here:
there is no build step in this project and no JS runtime is a Python test
dependency. The cost is that the scanner can be fooled, so the last class pins it
against known reads by name and by file — and pins only the guards that were
measured to fire. See `TestTheScannerActuallyScans` for the ones that are inert
today and why they are kept anyway.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_reconcile_seam.py -q
"""

import re
from pathlib import Path

import pytest

import stamp_version

ROOT = Path(__file__).resolve().parents[1]

# A chunk is one top-level function body, so a name bound in one function is not
# still in scope for the next.
CHUNK = re.compile(r'^(?:export\s+)?(?:async\s+)?function\s', re.M)
DECL = re.compile(r'\b(?:const|let|var)\s+(\w+)\s*=\s*([^;\n]*)')

# `for (const entry of entries)`. The rows are the only part of this block that
# arrives as a list, and a list is read in a loop rather than through an alias.
FOR_OF = re.compile(r'\bfor\s*\(\s*(?:const|let|var)\s+(\w+)\s+of\s+(\w+)')

BLOCK = re.compile(r'\breconciliation\b')
HANDLE = 'reconciliation'

LINE_COMMENT = re.compile(r'//[^\n]*')
BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)

# Properties of the JavaScript, not of the payload. `entries.length` is how
# `renderConflicts` decides whether to draw anything at all, and no version of
# `Reconciliation.to_json` will ever produce a field called `length`, so without
# this it is a permanent false ghost. Kept to exactly the names that are true of
# every array — anything longer than this is a scanner being talked out of its
# own findings.
BUILTIN = {'length'}

# How deep an alias chain the scanner will follow. Three hops is one more than
# anything in the repo uses; the bound exists so a self-referential declaration
# cannot spin.
MAX_HOPS = 3


# Produced and deliberately not rendered. The reason is the point: it has to say
# where the figure went instead, or why a coach is better off without it.
#
# All three here are the same argument, and the argument is in `to_json`:
# disagreements travel filtered to `self.disagreements(GOAL)`. Every row that
# reaches a page is a goal by construction, so all three of these are constants
# in what actually ships.
UNREAD_BY_DESIGN = {
    'kind':
        "'goal' in every row that travels — to_json sends disagreements(GOAL) "
        'and nothing else, so rendering it would put the same word on every '
        'line of a list that is already headed as goals',
    'tag_type':
        "'goal' or null on the rows that travel, which is status said twice: a "
        'row with no tagged type is exactly a cv_only row, and status already '
        'says that in the words the reviewer needs',
    'detail':
        "the boundary a ball crossed, 'touchline' or 'byline' — only ever set "
        'on exit rows, and exit rows never travel, so this is null in every '
        'payload ever published',
}


def _dict_keys(text: str, class_name: str) -> list[str]:
    """The keys of one class's `to_json` return dict."""
    body = text.split('class %s:' % class_name, 1)[1]
    body = body.split('def to_json(self) -> dict:', 1)[1]
    # Bounded at the closing brace: without it the split runs on into the rest
    # of the module and reports names the class does not publish.
    body = body.split('return {', 1)[1].split('\n        }', 1)[0]
    return re.findall(r"^ {12}'([a-z_0-9]+)':", body, re.M)


def produced() -> dict[str, str]:
    """{field: which producer writes it} for everything inside the block.

    Three sources, because the block is three levels deep. The count keys are
    read off the constant line rather than off `counts()`, which builds them in
    a comprehension — the names only exist as those three literals.
    """
    text = (ROOT / 'cv' / 'reconcile.py').read_text(encoding='utf-8')
    made = {}
    for key in _dict_keys(text, 'Reconciliation'):
        made[key] = 'Reconciliation.to_json'
    counts = re.search(r'^AGREED, CV_ONLY, TAG_ONLY = (.+)$', text, re.M)
    for key in re.findall(r"'(\w+)'", counts.group(1)):
        made[key] = 'the counts() keys'
    for key in _dict_keys(text, 'Disagreement'):
        made.setdefault(key, 'Disagreement.to_json')
    return made


def _code(text: str, strip: bool = True) -> str:
    """The file with its prose removed.

    A field named only in a comment must not count as rendered — an orphan
    explained away by the sentence explaining it.
    """
    if not strip:
        return text
    return LINE_COMMENT.sub('', BLOCK_COMMENT.sub('', text))


def _mentions(expr: str, names: set[str]) -> bool:
    return any(re.search(r'\b%s\b' % re.escape(n), expr) for n in names)


def _seeded(chunk: str, block, hops: bool, loops: bool) -> set[str]:
    """Names holding some part of the reconciliation block.

    Grown to a fixed point rather than in one pass, because the chain here is
    genuinely three long: `rec` names the block, `goals` is a field of `rec`,
    and `entry` is an item of a list that came out of `rec`.
    """
    names = {name for name, expr in DECL.findall(chunk) if block.search(expr)}
    # The block's own name is a handle too. `coach/review.js` never aliases
    # it: it reads `state.match?.cv?.reconciliation?.disagreements` straight
    # off the chain, and without this that read is invisible.
    names |= {HANDLE}
    for _ in range(MAX_HOPS):
        grown = set(names)
        if hops:
            for name, expr in DECL.findall(chunk):
                if _mentions(expr, names):
                    grown.add(name)
        if loops:
            for name, source in FOR_OF.findall(chunk):
                if source in names:
                    grown.add(name)
        if grown == names:
            break
        names = grown
    return names


def _fields(chunk: str, names: set[str], skip_methods: bool) -> set[str]:
    keys = set()
    for name in names:
        pattern = re.compile(
            r'\b%s\s*\??\s*(?:\.\s*(\w+)|\[\s*\'(\w+)\'\s*\])' % re.escape(name))
        for match in pattern.finditer(chunk):
            # `(v) => v.toFixed(2)` is a formatter calling a method, not a page
            # reading a figure.
            if skip_methods and chunk[match.end():match.end() + 1] == '(':
                continue
            key = match.group(1) or match.group(2)
            if key not in BUILTIN:
                keys.add(key)
    return keys


def read(*, hops: bool = True, loops: bool = True, skip_methods: bool = True,
         strip: bool = True, chunked: bool = True,
         block=BLOCK) -> dict[str, set[str]]:
    """{field: {files that read it}} across every page's JavaScript.

    Every keyword is a knob only so the last class can loosen it and prove that
    the checks above are capable of failing.
    """
    found: dict[str, set[str]] = {}

    for directory in stamp_version.MODULE_DIRS:
        for path in sorted((ROOT / directory).glob('*.js')):
            rel = path.relative_to(ROOT).as_posix()
            text = _code(path.read_text(encoding='utf-8'), strip)
            if not block.search(text):
                continue
            for chunk in (CHUNK.split(text) if chunked else [text]):
                if not block.search(chunk):
                    continue
                names = _seeded(chunk, block, hops, loops)
                if not names:
                    continue
                for key in _fields(chunk, names, skip_methods):
                    found.setdefault(key, set()).add(rel)
    return found


def camel(key: str) -> str:
    """snake_case as the emulator fixtures spell it.

    Both spellings are live and both are read: Python writes the block
    snake_cased, the fixtures write it camelCased. Either counts as a reader.
    """
    head, *rest = key.split('_')
    return head + ''.join(p[:1].upper() + p[1:] for p in rest)


@pytest.fixture(scope='module')
def made() -> dict[str, str]:
    return produced()


@pytest.fixture(scope='module')
def seen() -> dict[str, set[str]]:
    return read()


def readers(key: str, found: dict[str, set[str]]) -> set[str]:
    return found.get(key, set()) | found.get(camel(key), set())


class TestForwarded:
    """The premise: the JS gate cannot see inside this block."""

    def test_publish_forwards_the_block_whole(self):
        # If this stops being true — if summary_payload starts naming the
        # figures individually — then tests/smoke.test.js covers them and this
        # file is duplicated work rather than the only thing looking.
        text = (ROOT / 'cv' / 'publish.py').read_text(encoding='utf-8')
        assert "'reconciliation': report_json.get('reconciliation')" in text


class TestEveryFigureLands:

    def test_every_produced_field_is_rendered_or_excused(self, made, seen):
        orphans = sorted(
            k for k in made if not readers(k, seen) and k not in UNREAD_BY_DESIGN
        )
        assert not orphans, (
            'the reconciliation block publishes %s and no page reads it. '
            'Render it, or add it to UNREAD_BY_DESIGN with the reason.'
            % ', '.join(orphans)
        )

    def test_nothing_reads_a_field_the_pipeline_never_produces(self, made, seen):
        known = set(made) | {camel(k) for k in made}
        ghosts = sorted(k for k in seen if k not in known)
        assert not ghosts, (
            'a page reads reconciliation.%s, which cv/reconcile.py never '
            'writes — it will be undefined on every real match'
            % ', reconciliation.'.join(ghosts)
        )


class TestTheExcuseList:
    """The list has to cost something, and it has to stay current."""

    def test_no_stale_entries(self, seen):
        rendered = sorted(k for k in UNREAD_BY_DESIGN if readers(k, seen))
        assert not rendered, (
            '%s is listed as unrendered but %s reads it now — delete the entry'
            % (', '.join(rendered),
               ', '.join(sorted(readers(rendered[0], seen))))
        )

    def test_no_entries_for_fields_that_do_not_exist(self, made):
        gone = sorted(set(UNREAD_BY_DESIGN) - set(made))
        assert not gone, (
            '%s is excused here and no longer produced — delete the entry'
            % ', '.join(gone)
        )

    def test_every_entry_gives_a_reason(self):
        # A word or two would let the next person silence this file without
        # thinking. A sentence is small enough to write and large enough that
        # writing a false one is uncomfortable.
        thin = sorted(k for k, why in UNREAD_BY_DESIGN.items() if len(why) < 40)
        assert not thin, '%s is excused without a reason' % ', '.join(thin)


class TestTheScannerActuallyScans:
    """A regex that matched nothing would make every assertion above pass.

    Four of the scanner's guards are pinned below, because loosening each one
    was measured to change the answer on this repo: the hop-two step, the loop
    binding, the per-function split and the builtin exclusion. Two more are not,
    on purpose:

    * **Comment stripping** and **the method-call filter** change nothing here
      today: loosen either one and the answer is the same twelve figures and no
      ghosts. Both guard the direction that fails *silently* — a scanner that
      invents a reader makes an orphan look rendered — so they are kept and
      documented as currently inert rather than deleted, and deliberately not
      given tests. A pin for a guard that cannot fire is the same mistake as a
      gate that cannot fail, one level down. Both fire on the other three
      blocks' gates, which is why the shape is shared.
    """

    def test_the_producer_parse_found_all_three_producers(self, made):
        assert len(made) == 15
        # One from each source, so a parse that lost a whole producer fails
        # here rather than looking like a repo that stopped rendering things.
        assert made['goal_agreement'] == 'Reconciliation.to_json'
        assert made['cv_only'] == 'the counts() keys'
        assert made['tag_type'] == 'Disagreement.to_json'
        # A dataclass field on Reconciliation, deliberately not a to_json key.
        # Its arrival here means the parse read a class body instead of a
        # return dict.
        assert 'entries' not in made
        # `at_s` is a property, not a published figure. Same check, other class.
        assert 'at_s' not in made

    def test_the_scan_found_both_pages_that_read_the_block(self, seen):
        # Two readers, and they read different halves: `cvQualityNotes` in
        # assets/report.js turns the rates and counts into a sentence, and
        # `renderConflicts` in coach/review.js draws the rows. A scanner that
        # lost either would look exactly like a repo that had stopped
        # rendering half the block.
        assert 'assets/report.js' in readers('goal_agreement', seen)
        assert 'coach/review.js' in readers('disagreements', seen)

    def test_the_second_hop_is_load_bearing(self, seen):
        # `const goals = rec?.goals || {}` and then `goals.agreed`. Without
        # following an alias of an alias, the three count keys look orphaned,
        # and somebody 'fixes' that by deleting the half of the sentence that
        # says how many goals were compared — leaving a bare percentage, which
        # is the thing the note was written to avoid.
        without = read(hops=False)
        for key in ('agreed', 'cv_only', 'tag_only'):
            assert not readers(key, without), key
            assert readers(key, seen), key
        # And only those three: the rates are read straight off the first
        # alias, so a failure here is about the count keys specifically.
        assert readers('exit_agreement', without)

    def test_the_loop_binding_is_load_bearing(self, seen):
        # `for (const entry of entries)` is the only way a row field is ever
        # reached, because the rows are a list. Three of the twelve figures
        # that reach a page are row fields.
        without = read(loops=False)
        for key in ('status', 'cv_s', 'tag_s'):
            assert not readers(key, without)
            assert readers(key, seen)

    def test_the_per_function_split_is_load_bearing(self, made):
        # Without it, a name bound in one function claims every field read off
        # the same name anywhere else in a 4000-line file. `coach/review.js`
        # binds `entry` in the conflict loop and reads `item.entry.playerId` in
        # the misses list four hundred lines down, which is a different entry
        # entirely — so the whole-file scan reports a figure the pipeline never
        # produced, on a page doing nothing wrong.
        whole = read(chunked=False)
        known = set(made) | {camel(k) for k in made}
        assert [k for k in whole if k not in known] == ['playerId']

    def test_the_builtin_exclusion_is_load_bearing(self, made):
        # `entries.length` is how renderConflicts decides whether to draw
        # anything. It is a property of every array and can never be a payload
        # figure, so without the exclusion the ghost check fails permanently on
        # a page that is doing nothing wrong.
        loose = _without_the_builtin_exclusion()
        known = set(made) | {camel(k) for k in made}
        assert 'length' in loose
        assert [k for k in loose if k not in known] == ['length']

    def test_the_ghost_check_is_capable_of_failing(self, made):
        # The ghost direction has never found anything, and a check that has
        # never fired is indistinguishable from one that cannot. So loosen what
        # counts as the block until every declaration seeds a row, and confirm
        # the reads that invents really are called ghosts. If this stops
        # failing, the empty result above stopped being good news.
        loose = read(block=re.compile(r''))
        known = set(made) | {camel(k) for k in made}
        assert [k for k in loose if k not in known]


def _without_the_builtin_exclusion() -> dict[str, set[str]]:
    """The scan with the builtin exclusion switched off.

    A function rather than a knob on `read`, because the exclusion lives inside
    `_fields` and the point of the test above is to see what `_fields` drops.
    """
    keep = set(BUILTIN)
    BUILTIN.clear()
    try:
        return read()
    finally:
        BUILTIN.update(keep)
