"""Every figure inside the quality block reaches a page, or says why not.

`tests/smoke.test.js` already checks that every figure the pipeline publishes is
one some page reads. It does that over the top-level keys of the five payload
builders, and for one of those keys the check is empty:

    'quality': report_json.get('quality') or {},   # cv/publish.py

One name, one reader, gate satisfied — and twenty-nine figures inside it that
nothing has ever looked at, in either direction. That is not a hypothetical.
`assets/report.js` carries a comment about how the coach and player pages drifted
apart and *four new quality fields ended up reaching neither*, which is the same
failure this file exists to catch, found the slow way.

So this reads both ends as text. `_quality` in `cv/report_json.py` says what is
produced; the JavaScript under the module directories says what is read. The two
have to agree, and a key that is deliberately not rendered has to be listed here
with a reason somebody can argue with.

Both directions, because a check that only runs one way rots. An unlisted key
nobody renders is a finding. A listed key somebody has since started rendering is
a stale entry to delete — otherwise the list below turns into a graveyard nobody
dares touch, which is worse than not having it.

Reading text rather than executing it is crude and is the right crudeness here:
there is no build step in this project and no JS runtime is a Python test
dependency. The cost is that the scanner can be fooled, so the last class pins it
against known reads by name and by file.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_quality_seam.py -q
"""

import re
from pathlib import Path

import pytest

import stamp_version

ROOT = Path(__file__).resolve().parents[1]

# A chunk is one top-level function body, so an alias declared in one function is
# not still in scope for the next. Without this, a `const q = ...` anywhere in a
# 3000-line file would claim every `q.foo` in it.
CHUNK = re.compile(r'^(?:export\s+)?(?:async\s+)?function\s', re.M)

# `const q = quality || {}` aliases the block. `const bits = notes(quality, {})`
# does not — it is a derived value that happens to mention the word, and treating
# it as an alias invents readers for keys nobody reads.
DECL = re.compile(r'\b(?:const|let|var)\s+(\w+)\s*=\s*([^;\n]*)')
IS_ALIAS = re.compile(r'^[\w?.()\[\]\s]*\bquality$')


# Produced and deliberately not rendered. The reason is the point: it has to say
# where the figure went instead, or why a coach is better off without it.
UNREAD_BY_DESIGN = {
    'ball_filled_share':
        'kept apart from ball_seen_share on purpose — filled-in points are a '
        'straight line drawn between two sightings, and the seen share is the '
        'one the pages caption',
    'clusters':
        'a count of what the pipeline built; the pages count the cluster rows '
        'they actually render',
    'dead_ball_s':
        'live_share is the rendered form of the same measurement, and it is the '
        'share rather than the raw seconds that a coach can read',
    'exit_agreement':
        'the reconciliation block carries the same rate, and assets/report.js '
        'reads it from there, next to the disagreements it belongs with',
    'goal_agreement':
        'the reconciliation block carries the same rate, and assets/report.js '
        'reads it from there, next to the disagreements it belongs with',
    'kit_separation':
        'already reaches the coach as a pipeline warning when it falls below '
        'MIN_KIT_SEPARATION, which is the only value at which it means anything',
    'touch_confidence_p10':
        'the median is what assets/db.js renders; the tenth percentile is the '
        'tail, kept for tuning rather than for a match page',
    'touches':
        'a count of what the pipeline built; the pages count the touches they '
        'actually render',
    'tracks':
        'a count of what the pipeline built; the pages count the track rows '
        'they actually render',
    'unseen_spans':
        'this is len(touches.gaps); no_ball_s is the same absence expressed in '
        'seconds, and seconds are what the note says',
}


def produced() -> list[str]:
    """Every key `_quality` puts in the block."""
    text = (ROOT / 'cv' / 'report_json.py').read_text(encoding='utf-8')
    body = text.split('def _quality(')[1].split('\ndef ')[0]
    return sorted(set(re.findall(r"^ {8}'([a-z_0-9]+)':", body, re.M)))


def _aliases(chunk: str, is_alias) -> set[str]:
    """Names bound to the quality block itself inside one function body."""
    names = {'quality'}
    for name, expr in DECL.findall(chunk):
        expr = expr.strip()
        for sep in ('||', '??'):
            if sep in expr:
                expr = expr.split(sep)[0]
        if is_alias.match(expr.strip()):
            names.add(name)
    return names


def read(is_alias=IS_ALIAS) -> dict[str, set[str]]:
    """{key: {files that read it}} across every page's JavaScript.

    `is_alias` is a parameter only so the last class can loosen it and prove
    that the checks above are capable of failing.
    """
    found: dict[str, set[str]] = {}
    for directory in stamp_version.MODULE_DIRS:
        for path in sorted((ROOT / directory).glob('*.js')):
            rel = path.relative_to(ROOT).as_posix()
            for chunk in CHUNK.split(path.read_text(encoding='utf-8')):
                for name in _aliases(chunk, is_alias):
                    escaped = re.escape(name)
                    dot = re.findall(r'\b%s\s*\??\.\s*(\w+)' % escaped, chunk)
                    sq = re.findall(
                        r"\b%s\s*\??\[\s*'(\w+)'\s*\]" % escaped, chunk)
                    for key in dot + sq:
                        found.setdefault(key, set()).add(rel)
    return found


def camel(key: str) -> str:
    """snake_case as the emulator fixtures spell it.

    Both spellings are live and both are read: Python writes the block
    snake_cased, the fixtures write it camelCased, and every browser read is
    `q.foo_bar ?? q.fooBar`. Either spelling counts as a reader here.
    """
    head, *rest = key.split('_')
    return head + ''.join(p[:1].upper() + p[1:] for p in rest)


@pytest.fixture(scope='module')
def made() -> list[str]:
    return produced()


@pytest.fixture(scope='module')
def seen() -> dict[str, set[str]]:
    return read()


def readers(key: str, seen: dict[str, set[str]]) -> set[str]:
    return seen.get(key, set()) | seen.get(camel(key), set())


class TestForwarded:
    """The premise: the JS gate cannot see inside this block."""

    def test_publish_forwards_the_block_whole(self):
        # If this stops being true — if summary_payload starts naming the
        # figures individually — then tests/smoke.test.js covers them and this
        # file is duplicated work rather than the only thing looking.
        text = (ROOT / 'cv' / 'publish.py').read_text(encoding='utf-8')
        assert "'quality': report_json.get('quality')" in text


class TestEveryFigureLands:

    def test_every_produced_key_is_rendered_or_excused(self, made, seen):
        orphans = sorted(
            k for k in made if not readers(k, seen) and k not in UNREAD_BY_DESIGN
        )
        assert not orphans, (
            'cv/report_json.py produces %s and no page reads it. Render it, or '
            'add it to UNREAD_BY_DESIGN with the reason.' % ', '.join(orphans)
        )

    def test_nothing_reads_a_figure_the_pipeline_never_produces(self, made, seen):
        known = set(made) | {camel(k) for k in made}
        ghosts = sorted(k for k in seen if k not in known)
        assert not ghosts, (
            'a page reads quality.%s, which _quality never writes — it will be '
            'undefined on every real match' % ', quality.'.join(ghosts)
        )


class TestTheExcuseList:
    """The list has to cost something, and it has to stay current."""

    def test_no_stale_entries(self, made, seen):
        rendered = sorted(k for k in UNREAD_BY_DESIGN if readers(k, seen))
        assert not rendered, (
            '%s is listed as unrendered but %s reads it now — delete the entry'
            % (', '.join(rendered),
               ', '.join(sorted(readers(rendered[0], seen))))
        )

    def test_no_entries_for_keys_that_do_not_exist(self, made):
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

    def test_the_producer_parse_found_the_block(self, made):
        assert len(made) > 20
        assert 'ball_seen_share' in made
        assert 'keeper_method' in made

    def test_a_known_figure_is_seen_reaching_a_known_page(self, seen):
        # live_share is the possession denominator, and it is read on both the
        # match report and the half-time view. If either drops out of this map,
        # the scanner broke, not the pages.
        where = readers('live_share', seen)
        assert 'assets/report.js' in where
        assert 'halftime/halftime.js' in where

    def test_a_figure_reached_only_through_an_alias_is_seen(self, seen):
        # no_ball_s is never read off a variable called `quality`. It is read as
        # `q.no_ball_s`, where q came from `const q = quality || {}`. A scanner
        # that only followed the literal name would report it as an orphan and
        # somebody would 'fix' that by deleting a figure the note depends on.
        assert readers('no_ball_s', seen) == {'assets/report.js'}

    def test_the_scan_reaches_past_the_shared_module(self, seen):
        # assets/report.js is where most of these are read. If the walk quietly
        # stopped there, the coach and player pages could drift again exactly as
        # they did before, so pin a read from each of the other directories.
        assert 'coach/coach.js' in readers('excluded_tracks', seen)
        assert 'assets/db.js' in readers('clear_holder_share', seen)

    def test_the_ghost_check_is_capable_of_failing(self, made):
        # The other direction is the one that has never yet found anything, and
        # a check that has never fired is indistinguishable from a check that
        # cannot. So loosen the alias rule until every plain local counts as the
        # quality block — `const bits = cvQualityNotes(quality, {...})` and
        # friends — and confirm that the reads it invents do get called ghosts.
        # If this stops failing, the ghost assertion above stopped meaning
        # anything and the empty result it reports is not good news.
        loose = read(re.compile(r'^\w+$'))
        known = set(made) | {camel(k) for k in made}
        assert [k for k in loose if k not in known]
