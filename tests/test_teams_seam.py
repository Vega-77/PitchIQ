"""Every figure inside the teams block reaches a page, or says why not.

`tests/smoke.test.js` already checks that every figure the pipeline publishes is
one some page reads. It does that over the top-level keys of the payload
builders, and for one of those keys the check is empty:

    'teams': report_json.get('teams') or {},   # cv/publish.py

One name, one reader, gate satisfied — and thirty-one figures inside it, most of
the coach's match view, that nothing has ever looked at in either direction.
`tests/test_quality_seam.py` is the same file for the same reason one block over,
and `assets/report.js` carries a comment about how the coach and player pages
once drifted apart and *four new quality fields ended up reaching neither*. This
is that failure waiting to happen in the larger block.

So this reads both ends as text. `TeamStats.to_json` in `cv/report_json.py` says
what is produced; the JavaScript under the module directories says what is read.
A field that is deliberately not rendered has to be listed here with a reason
somebody can argue with.

Three read shapes exist in this repo and a scanner that handles two of them
reports the third as an orphan, so all three are followed:

    const ours = cv?.teams?.team_a;  ...  ours.shape     a declared alias
    match.cv?.teams?.team_a?.attacking_end                an inline chain
    cv?.teams?.[key]?.shot_map                            a computed side
    row('possession', 'Touches', COUNT, (t) => t.touches) an arrow parameter

The last one is a guess: `t` is a parameter, and only the surrounding
`pick(ours)` says it holds a team. That guess is load-bearing — twelve fields
including `ppda`, `touches` and `phase_of_play` are reached no other way — but it
is a guess, so the two directions use different scans. **Coverage** uses the
loose scan, parameters included, and is pinned below by the rule that every key
the parameter step adds must be a field the pipeline really produces. **Ghosts**
use the strict scan only, aliases and chains, no parameter guessing, so a page
can never be accused of reading something it does not.

This deliberately does not share the quality gate's scanner. Each reads its own
end its own way — that block is read off one flat alias, this one off two sides
and a callback — and the quality gate's regexes are mutation-proved where they
stand. Only `camel` is duplicated, and it is three lines.

Reading text rather than executing it is crude and is the right crudeness here:
there is no build step in this project and no JS runtime is a Python test
dependency. The cost is that the scanner can be fooled, so the last class pins it
against known reads by name and by file.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_teams_seam.py -q
"""

import re
from pathlib import Path

import pytest

import stamp_version

ROOT = Path(__file__).resolve().parents[1]

# A chunk is one top-level function body, so an alias declared in one function is
# not still in scope for the next. Without this, a `const ours = ...` anywhere in
# a 4000-line file would claim every `ours.foo` in it.
CHUNK = re.compile(r'^(?:export\s+)?(?:async\s+)?function\s', re.M)
DECL = re.compile(r'\b(?:const|let|var)\s+(\w+)\s*=\s*([^;\n]*)')

# One side of the block: `teams.team_a`, `teams?.team_b`, `teams?.[key]`.
SIDE = r'teams\s*\??\.\s*(?:team_[ab]\b|\[[^\]\n]*\])'
# A declaration ends there, so the name it binds holds a side.
SEED = re.compile(SIDE + r'\s*$')
# A field read straight off the chain, with no alias in between.
DIRECT = re.compile(SIDE + r"\s*\??(?:\.\s*(\w+)|\[\s*'(\w+)'\s*\])")
ARROW = re.compile(r'(?:\(([^()]*)\)|(\w+))\s*=>')

LINE_COMMENT = re.compile(r'//[^\n]*')
BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)


# Produced and deliberately not rendered. The reason is the point: it has to say
# where the figure went instead, or why a coach is better off without it.
UNREAD_BY_DESIGN = {
    'team':
        'the dict key repeated inside its own value — cv/report_json.py builds '
        'teams[team] = team_stats(...) with TeamStats(team=team), so a page '
        'holding cv.teams.team_a already knows which side it holds',
}


def produced() -> list[str]:
    """Every key `TeamStats.to_json` puts in one side of the block."""
    text = (ROOT / 'cv' / 'report_json.py').read_text(encoding='utf-8')
    body = text.split('class TeamStats:', 1)[1]
    body = body.split('def to_json(self) -> dict:', 1)[1]
    # Bounded at the closing brace: without it the split runs on into the next
    # class and reports fields TeamStats does not have.
    body = body.split('return {', 1)[1].split('\n        }', 1)[0]
    return sorted(set(re.findall(r"^ {12}'([a-z_0-9]+)':", body, re.M)))


def _code(text: str, strip: bool = True) -> str:
    """The file with its prose removed.

    A field named only in a comment must not count as rendered, which is the
    direction that would hurt: an orphan explained away by the sentence
    explaining it. Nothing in the repo relies on this today — every field named
    in a comment is also read in code — so this guards the future, and the last
    class pins the two guards that are load-bearing now.
    """
    if not strip:
        return text
    return LINE_COMMENT.sub('', BLOCK_COMMENT.sub('', text))


def _seeded(chunk: str, seed) -> set[str]:
    """Names bound to one side of the block inside one function body."""
    names = set()
    for name, expr in DECL.findall(chunk):
        expr = expr.strip()
        for sep in ('||', '??'):
            if sep in expr:
                expr = expr.split(sep)[0]
        if seed.search(expr.strip()):
            names.add(name)
    return names


def _params(chunk: str) -> set[str]:
    """Every arrow parameter in the chunk.

    `row(..., (t) => t.touches)` binds `t` to a side inside `row`, three hundred
    lines away from the `const ours` it came from. Nothing short of following the
    call would prove that, so the name is taken on trust here and the trust is
    bounded: this only runs in chunks that already alias a side, and only in the
    coverage direction.
    """
    names = set()
    for grouped, bare in ARROW.findall(chunk):
        if bare:
            names.add(bare)
            continue
        for part in grouped.split(','):
            part = part.strip()
            if re.fullmatch(r'\w+', part):
                names.add(part)
    return names


def _fields(chunk: str, names: set[str], skip_methods: bool) -> set[str]:
    keys = set()
    for name in names:
        escaped = re.escape(name)
        pattern = re.compile(
            r'\b%s\s*\??\s*(?:\.\s*(\w+)|\[\s*\'(\w+)\'\s*\])' % escaped)
        for match in pattern.finditer(chunk):
            # `(v) => v.toFixed(2)` is a formatter calling a method, not a page
            # reading a figure. Without this the scan invents `toFixed`.
            if skip_methods and chunk[match.end():match.end() + 1] == '(':
                continue
            keys.add(match.group(1) or match.group(2))
    return keys


def read(*, params: bool = True, direct: bool = True, seed=SEED,
         skip_methods: bool = True, strip: bool = True) -> dict[str, set[str]]:
    """{field: {files that read it}} across every page's JavaScript.

    Every keyword is a knob only so the last class can loosen it and prove that
    the checks above are capable of failing.
    """
    found: dict[str, set[str]] = {}

    def note(key: str, rel: str) -> None:
        found.setdefault(key, set()).add(rel)

    for directory in stamp_version.MODULE_DIRS:
        for path in sorted((ROOT / directory).glob('*.js')):
            rel = path.relative_to(ROOT).as_posix()
            text = _code(path.read_text(encoding='utf-8'), strip)
            if direct:
                for match in DIRECT.finditer(text):
                    note(match.group(1) or match.group(2), rel)
            for chunk in CHUNK.split(text):
                names = _seeded(chunk, seed)
                if not names:
                    continue
                if params:
                    names |= _params(chunk)
                for key in _fields(chunk, names, skip_methods):
                    note(key, rel)
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
    """The loose scan: what a page could be reading, parameters included."""
    return read()


@pytest.fixture(scope='module')
def named() -> dict[str, set[str]]:
    """The strict scan: what a page unambiguously reads off the block."""
    return read(params=False)


def readers(key: str, seen: dict[str, set[str]]) -> set[str]:
    return seen.get(key, set()) | seen.get(camel(key), set())


class TestForwarded:
    """The premise: the JS gate cannot see inside this block."""

    def test_publish_forwards_the_block_whole(self):
        # If this stops being true — if summary_payload starts naming the
        # figures individually — then tests/smoke.test.js covers them and this
        # file is duplicated work rather than the only thing looking.
        text = (ROOT / 'cv' / 'publish.py').read_text(encoding='utf-8')
        assert "'teams': report_json.get('teams')" in text


class TestEveryFigureLands:

    def test_every_produced_field_is_rendered_or_excused(self, made, seen):
        orphans = sorted(
            k for k in made if not readers(k, seen) and k not in UNREAD_BY_DESIGN
        )
        assert not orphans, (
            'TeamStats produces %s and no page reads it. Render it, or add it '
            'to UNREAD_BY_DESIGN with the reason.' % ', '.join(orphans)
        )

    def test_nothing_reads_a_field_the_pipeline_never_produces(self, made, named):
        # The strict scan on purpose: a false ghost accuses a page of a bug it
        # does not have, and the arrow-parameter guess is not certain enough to
        # make that accusation with.
        known = set(made) | {camel(k) for k in made}
        ghosts = sorted(k for k in named if k not in known)
        assert not ghosts, (
            'a page reads teams.team_a.%s, which TeamStats never writes — it '
            'will be undefined on every real match' % ', teams.team_a.'.join(ghosts)
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
    """A regex that matched nothing would make every assertion above pass."""

    def test_the_producer_parse_found_the_whole_class(self, made):
        assert len(made) > 25
        assert 'possession_pct' in made
        assert 'shot_map' in made
        # Bounded at the closing brace: `heatmap` belongs to a later class, and
        # its arrival here means the split ran past the end of TeamStats.
        assert 'heatmap' not in made

    def test_the_scan_reaches_past_the_shared_module(self, seen):
        # assets/report.js builds most of these rows. If the walk quietly
        # stopped there, the other pages could drift exactly as the coach and
        # player pages already did once, so pin a read from each.
        assert 'assets/report.js' in readers('turnovers_by_third', seen)
        assert 'halftime/halftime.js' in readers('interceptions', seen)
        assert 'coach/coach.js' in readers('pressing_segments', seen)

    def test_the_arrow_parameter_step_is_load_bearing(self, seen, named):
        # ppda is only ever reached as `(t) => t.ppda`, where t is bound by
        # `pick(ours)` inside row(). A scanner that followed declarations alone
        # would call it an orphan and somebody would 'fix' that by deleting the
        # row.
        assert not readers('ppda', named)
        assert 'assets/report.js' in readers('ppda', seen)

    def test_the_inline_chain_step_is_load_bearing(self, seen):
        # shot_map is read as cv?.teams?.[key]?.shot_map — a computed side and
        # no alias at all. attacking_end is the same shape with the side spelled
        # out. Both were reported as orphans until the chain was followed.
        assert 'coach/coach.js' in readers('shot_map', seen)
        assert 'coach/coach.js' in readers('attacking_end', seen)
        without = read(direct=False)
        assert not readers('shot_map', without)

    def test_the_parameter_step_invents_nothing(self, made, seen, named):
        # The loose half is trusted for coverage only because it is clean: today
        # every key it adds over the strict scan is a real TeamStats field. The
        # moment it starts contaminating — a formatter, a helper, a local that
        # happens to share a name — this says so, before a contaminated read is
        # ever mistaken for a rendered figure.
        known = set(made) | {camel(k) for k in made}
        invented = sorted(k for k in set(seen) - set(named) if k not in known)
        assert not invented, (
            'the arrow-parameter guess produced %s, which TeamStats does not '
            'produce — it is reading something that is not a team'
            % ', '.join(invented)
        )

    def test_the_method_filter_is_load_bearing(self, made):
        # `{ format: (v) => v.toFixed(2) }` sits inside a chunk that aliases a
        # side, so v looks exactly like a team to the parameter step. Counting
        # method calls as figures invents it, and this is the one guard that
        # actually fires: without it the scan reads toFixed off a team.
        known = set(made) | {camel(k) for k in made}
        loose = read(skip_methods=False)
        assert [k for k in loose if k not in known]

    def test_the_ghost_check_is_capable_of_failing(self, made):
        # The ghost direction has never found anything, and a check that has
        # never fired is indistinguishable from one that cannot. So loosen the
        # rule for what counts as a side until every plain local qualifies, and
        # confirm the reads it invents do get called ghosts. If this stops
        # failing, the empty result above stopped being good news.
        loose = read(params=False, seed=re.compile(r'^\w+$'))
        known = set(made) | {camel(k) for k in made}
        assert [k for k in loose if k not in known]
