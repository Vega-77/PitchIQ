"""Every figure inside the keepers block reaches a page, or says why not.

`tests/smoke.test.js` walks the top-level keys of the payload builders and
checks that some page reads each one. For four of those keys the check is
empty, because the key is a whole block forwarded whole:

    'keepers': report_json.get('keepers') or [],   # cv/publish.py

One name, one reader, gate satisfied — and sixteen figures inside it that
nothing has ever looked at in either direction. `tests/test_quality_seam.py`
and `tests/test_teams_seam.py` are this file for the other two blocks that have
been closed so far.

So this reads both ends as text. `KeeperReport.to_json` in `cv/keeper.py` says
what is produced; the JavaScript under the module directories says what is read.
A field that is deliberately not rendered has to be listed here with a reason
somebody can argue with.

**This block is read through a callback, and that is the whole scanner.** The
keepers block is an array, not a pair of named sides, so nothing aliases a
keeper directly. `assets/report.js` binds one instead, one hop back:

    const forTeam = (team) => (keepers || []).find((k) => k && k.team === team);
    ...
    row('Saves', COUNT, (k) => k.saves)

`k` is bound inside the declaration that names `keepers`, and every `k.<field>`
in the same function body is a read. Take that step away and coverage falls from
thirteen figures to one — `found.team` in `keeperOfTrack`, the only field in the
block read off a plain alias.

**One scan serves both directions here, unlike the teams gate.** That gate needed
a loose scan for coverage (every arrow parameter in a chunk taken on trust) and a
strict one for ghosts. Reusing it here would have been wrong, and that was
measured rather than assumed: `teamStatRows` mentions `keepers` in passing and
contains every `(t) => t.xg` team callback, so the blanket guess produces
twenty-five keys that are not keeper fields — `ppda`, `touches`, `xg`, the whole
team block. The declaration scan above already covers thirteen of sixteen with
no ghosts at all, so it is trusted for both.

Reading text rather than executing it is crude and is the right crudeness here:
there is no build step in this project and no JS runtime is a Python test
dependency. The cost is that the scanner can be fooled, so the last class pins it
against known reads by name and by file — and pins only the one guard that was
measured to fire. See `TestTheScannerActuallyScans` for the three that are inert
today, why they are kept anyway, and the fourth that was deleted.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_keepers_seam.py -q
"""

import re
from pathlib import Path

import pytest

import stamp_version

ROOT = Path(__file__).resolve().parents[1]

# A chunk is one top-level function body, so a name bound in one function is not
# still in scope for the next. Without it, a `const found = (keepers || [])...`
# anywhere in a 4000-line file would claim every `found.foo` in it. Currently
# inert — see TestTheScannerActuallyScans.
CHUNK = re.compile(r'^(?:export\s+)?(?:async\s+)?function\s', re.M)
DECL = re.compile(r'\b(?:const|let|var)\s+(\w+)\s*=\s*([^;\n]*)')

# The block itself. There is no `keepers.team_a` to anchor on — it is an array.
BLOCK = re.compile(r'\bkeepers\b')
ARROW = re.compile(r'(?:\(([^()]*)\)|(\w+))\s*=>')

LINE_COMMENT = re.compile(r'//[^\n]*')
BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)


# Produced and deliberately not rendered. The reason is the point: it has to say
# where the figure went instead, or why a coach is better off without it.
#
# All three here are one argument, already written out in `keeperStatRows`: the
# shots a keeper faced are the opposition's shots and the goals he conceded are
# the opposition's goals, and both are already on the screen in the attacking
# group with the columns the other way round. Printing them again under a
# keeper's name would look like a second measurement and would be the same one.
UNREAD_BY_DESIGN = {
    'shots_faced':
        "the opposition's shots, already rendered as teams.<them>.shots with "
        'the columns the other way round — a second copy under the keeper would '
        'look like a second measurement of the same thing',
    'shots_on_target_faced':
        'the same argument as shots_faced, against teams.<them>.shots_on_target '
        'which TeamStats.to_json already renders beside it',
    'goals_conceded':
        "the opposition's goals, already rendered as teams.<them>.goals — and it "
        'is the denominator of save_pct, which is rendered, so the figure is on '
        'the screen in the form that means something',
}


def produced() -> list[str]:
    """Every key `KeeperReport.to_json` puts on one keeper."""
    text = (ROOT / 'cv' / 'keeper.py').read_text(encoding='utf-8')
    body = text.split('class KeeperReport:', 1)[1]
    body = body.split('def to_json(self) -> dict:', 1)[1]
    # Bounded at the closing brace: without it the split runs on into the rest
    # of the module and reports names KeeperReport does not publish.
    body = body.split('return {', 1)[1].split('\n        }', 1)[0]
    return sorted(set(re.findall(r"^ {12}'([a-z_0-9]+)':", body, re.M)))


def _code(text: str, strip: bool = True) -> str:
    """The file with its prose removed.

    A field named only in a comment must not count as rendered — an orphan
    explained away by the sentence explaining it. `assets/heatmap.js` names
    `keepers` in prose alone and nowhere in code, which is the shape this is
    watching for, though it happens to seed nothing today.
    """
    if not strip:
        return text
    return LINE_COMMENT.sub('', BLOCK_COMMENT.sub('', text))


def _names(grouped: str, bare: str) -> set[str]:
    if bare:
        return {bare}
    out = set()
    for part in (grouped or '').split(','):
        part = part.strip()
        if re.fullmatch(r'\w+', part):
            out.add(part)
    return out


def _seeded(chunk: str, block, params: bool) -> set[str]:
    """Names bound to something out of the keepers array.

    The declaration itself is the first half: `const found = (keepers || [])
    .find(...)` binds `found` to a keeper. The second half, the one that matters,
    is the parameter of any callback inside that same declaration — the `k` of
    `(k) => ...` handed to `.find`, which is how every rendered figure is
    actually reached.
    """
    names = set()
    for name, expr in DECL.findall(chunk):
        if not block.search(expr):
            continue
        names.add(name)
        if params:
            for grouped, bare in ARROW.findall(expr):
                names |= _names(grouped, bare)
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
            keys.add(match.group(1) or match.group(2))
    return keys


def read(*, params: bool = True, skip_methods: bool = True, strip: bool = True,
         chunked: bool = True, block=BLOCK) -> dict[str, set[str]]:
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
                names = _seeded(chunk, block, params)
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
def made() -> list[str]:
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
        assert "'keepers': report_json.get('keepers')" in text


class TestEveryFigureLands:

    def test_every_produced_field_is_rendered_or_excused(self, made, seen):
        orphans = sorted(
            k for k in made if not readers(k, seen) and k not in UNREAD_BY_DESIGN
        )
        assert not orphans, (
            'KeeperReport produces %s and no page reads it. Render it, or add '
            'it to UNREAD_BY_DESIGN with the reason.' % ', '.join(orphans)
        )

    def test_nothing_reads_a_field_the_pipeline_never_produces(self, made, seen):
        known = set(made) | {camel(k) for k in made}
        ghosts = sorted(k for k in seen if k not in known)
        assert not ghosts, (
            'a page reads keeper.%s, which KeeperReport never writes — it will '
            'be undefined on every real match' % ', keeper.'.join(ghosts)
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

    Two of the scanner's guards are pinned below because loosening each one was
    measured to change the answer on this repo. Three more are not, on purpose:

    * **Comment stripping**, **the method-call filter** and **the per-function
      split** all change nothing here today: loosen any one of them and the
      answer is the same thirteen figures and no ghosts. All three guard the
      direction that fails *silently* — a scanner that invents a reader makes an
      orphan look rendered — so they are kept and documented as currently inert
      rather than deleted, and deliberately not given tests. A pin for a guard
      that cannot fire is the same mistake as a gate that cannot fail, one level
      down. Two of them do fire against a *blanket* arrow-parameter scan, the
      one this gate rejected: without the split, `teamStatRows` contributes
      twelve team fields, and without the method filter, `toFixed`.
    * **Hop-two alias resolution** — following `const ours = forTeam('team_a')`
      to say that `ours` holds a keeper — was written, measured, and deleted. It
      resolves nothing here, because no page reads `ours.field` directly, and it
      guards the direction that fails *loudly*: a missed reader shows up as an
      orphan, which is noisy and safe. Dead machinery in the loud direction is
      just dead.
    """

    def test_the_producer_parse_found_the_whole_class(self, made):
        assert len(made) == 16
        assert 'saves' in made
        assert 'track_ids' in made
        # A dataclass field, deliberately not a to_json key — it says whether
        # the other figures could be measured at all. Its arrival here means the
        # parse read the class body instead of the return dict.
        assert 'end_known' not in made
        # Belongs to KeeperDistribution, the class above. Its arrival means the
        # split ran past the closing brace.
        assert 'hold_duration_s' not in made

    def test_the_scan_found_the_two_functions_that_read_the_block(self, seen):
        # Everything that reads a keeper figure lives in assets/report.js:
        # `keeperStatRows` builds the eleven rendered rows, and `keeperOfTrack`
        # is the only reader of `track_ids`. Pin one field from each, because a
        # scanner that lost either would look exactly like a repo that had
        # stopped rendering half the block.
        assert 'assets/report.js' in readers('saves', seen)
        assert 'assets/report.js' in readers('track_ids', seen)

    def test_the_callback_parameter_step_is_load_bearing(self, made, seen):
        # `(k) => k.saves` is how twelve of the thirteen rendered figures are
        # reached. Without following the parameter of the callback inside the
        # declaration that names `keepers`, the scan sees only `found.team` and
        # calls the rest of the block orphaned — and somebody 'fixes' that by
        # deleting rows a coach uses.
        without = read(params=False)
        covered = [k for k in made if readers(k, without)]
        assert covered == ['team']
        assert not readers('saves', without)
        assert readers('saves', seen)

    def test_the_ghost_check_is_capable_of_failing(self, made):
        # The ghost direction has never found anything, and a check that has
        # never fired is indistinguishable from one that cannot. So loosen what
        # counts as the block until every declaration seeds a keeper, and
        # confirm the reads that invents really are called ghosts. If this stops
        # failing, the empty result above stopped being good news.
        loose = read(block=re.compile(r''))
        known = set(made) | {camel(k) for k in made}
        assert [k for k in loose if k not in known]
