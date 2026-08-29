"""A field name assembled in Python, and the screen that has to spell it back.

`cv/publish.py` does not write its field names. It builds them:

    f'{CV_FIELD_PREFIX}TopSpeedKmh': track_stats.get('top_speed_kmh')

So the string `cvTopSpeedKmh` exists nowhere in that file, and grepping the
repo for it finds only the browser. That is deliberate — the prefix is what
tells a coach which numbers a human tapped and which the pipeline estimated,
and one constant is the honest way to guarantee it. It also means a rename on
either side is invisible to every tool that works by matching text.

Four places have to agree about these names, and no two of them can see each
other:

    cv/publish.py    player_report_fields()   21 fields, prefix + suffix
    assets/report.js cvReportFields(stats)    23 fields, written out in full
    assets/report.js CV_REPORT_KEYS           26 names, the clearing list
    the pages        report.cvTopSpeedKmh     26 names, read off a document

The document in the middle is `teams/{t}/matches/{m}/playerReports/{p}`, and
it is the one document in the app with **no field allowlist** — `firestore.rules`
holds it to a key count instead, because the pipeline merges its own fields on
top through the Admin SDK, which bypasses rules entirely
(`tests/test_write_seam.py` covers that side). Nothing rejects a misspelt name.
Nothing logs one. The two failures look like this:

  * **Written and never read.** A field the pipeline sends that no page names
    is a few hundred bytes on a document with eighty keys of room, spent on a
    number nobody will ever see. It costs nothing visible, which is why it
    would survive every rename around it.

  * **Read and never written.** `report.cvTopSpeedKph` on a report that carries
    `cvTopSpeedKmh` is `undefined`, and every one of these numbers reaches a
    screen through `??` or a null check, because absent-is-not-zero is the rule
    everywhere in this repo. So the dash that means *nothing could measure this*
    and the dash that means *someone typo'd the key* are the same dash. A
    student opens their report and one row is simply blank, forever, and the
    page is behaving exactly as designed.

`tests/test_player_merge_parity.py` already guards one edge of this — every
field the pipeline writes must be one `cvReportFields` can null, or un-mapping
a cluster leaves last week's numbers standing. That is a subset check between
two of the four corners. This file closes the square: the union of what the two
writers send is exactly the clearing list, and exactly what the pages read.

Not every `cv`-prefixed property is a report field. Nine are not, and they are
listed below with the reason, checked in both directions — a name here that
becomes a report field fails, and a name here that nothing reads any more fails.
That is the rule `tests/test_call_graph.py` set for every allowlist in this
repo, so the list cannot rot into a graveyard nobody dares touch.

Limits
------

Names only. That a report carries `cvTopSpeedKmh` says nothing about whether
the number under it is in km/h — `tests/test_player_merge_parity.py` is where
the arithmetic is compared, and it compares the two implementations against
each other, not against a stopwatch.

The scan reads property syntax, so a bracket read (`report['cv' + suffix]`)
would be invisible to it. Rather than leave that as a promise, there is a test
that the repo contains no bracket read of a `cv` name, which is what makes the
limit harmless today.

Skipped automatically when Node isn't installed.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_cv_field_seam.py -q
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cv.publish import player_report_fields
from test_free_names import blank

REPO = Path(__file__).resolve().parents[1]

JS_REPORT = REPO / 'assets' / 'report.js'

pytestmark = pytest.mark.skipif(
    shutil.which('node') is None, reason='Node is not installed',
)

# Every directory of served JavaScript. `cv/` is the writing side and is
# imported directly; there is nothing to scan there.
JS_DIRS = ['assets', 'coach', 'player', 'live-tagging', 'halftime',
           'calibrate', 'xg-sandbox']

# `cv`-prefixed properties that are not fields of a player's match report.
# Each one is read off a different object entirely, and the reason matters more
# than the name: it is what a future reader needs in order to decide whether a
# new name belongs here or is a bug.
NOT_A_REPORT_FIELD = {
    'cvTracks': 'a key of the `extra` bag publishReports takes, in memory only',
    'cvMapping': 'the cvMapping/players document, a sibling of the report',
    'cvEvents': 'the cvEvents document, joined onto the match in memory',
    'cvReview': 'the cvReview/decisions document, likewise',
    'cvClusters': 'the thumbnail clusters, passed in beside the tracks',
    'cvWindow': 'the processed window, passed in to date the coverage',
    'cvShotRows': "the coach's shot ledger, passed in to correct the map",
    'cvPreview': 'a boolean on the coach page: is the sample plot showing',
    'cvMatches': 'a season accumulator the browser derives from a run of '
                 'reports by counting the ones that carry cvTouches; it is '
                 'never stored on a report and never written by anything',
}

# Written by the pipeline, cleared by the browser, and written by nothing in
# the browser — the three fields one camera can produce and a coach's screen
# has no way to compute for itself.
PIPELINE_ONLY = {'cvHeatmap', 'cvAttackingEnd', 'cvCalibrationErrorM'}

READ = re.compile(r'\.\s*(cv[A-Z]\w*)')
BRACKET = re.compile(r'(?:\w|\)|\])\s*\[')
QUOTED_CV = re.compile(r"""^\s*['"]cv[A-Z]""")


def js_files():
    for name in JS_DIRS:
        folder = REPO / name
        if folder.is_dir():
            for path in sorted(folder.glob('*.js')):
                yield '%s/%s' % (name, path.name), path


def reads(source):
    """`{name: [offset]}` for every `.cvName` read in one file's source.

    Scanned over `blank`ed text, so a name inside a comment or a string is not
    a read — which matters here, because `CV_REPORT_KEYS` is a list of these
    names as string literals and reading it as twenty-six reads would make the
    whole check circular.

    A spread is not a read. `...cvTallies()` ends in the same three characters
    as a property access and is a call to a local function.
    """
    code = blank(source)
    found = {}
    for m in READ.finditer(code):
        if code[max(0, m.start() - 2):m.start()] == '..':
            continue
        found.setdefault(m.group(1), []).append(m.start())
    return found


def bracket_reads(raw, code):
    """Lines where a `cv` name is reached as `thing['cvName']`, not `.cvName`.

    The bracket is located in the blanked text, so one inside a comment is not
    a hit. The name itself has to be read out of `raw`, because `blank` empties
    string literals — the quote characters included. A scan for `['cvName']`
    over blanked source matches nothing at all, ever, and reports that as a
    clean bill of health. This test was written that way first.
    """
    out = []
    for m in BRACKET.finditer(code):
        close = code.find(']', m.end() - 1)
        if close >= 0 and QUOTED_CV.match(raw[m.end():close]):
            out.append(code[:m.start()].count('\n') + 1)
    return out


def node_keys(expression):
    """Keys of an object `assets/report.js` builds, as that module builds it."""
    script = (
        'import { cvReportFields } from %s;'
        'console.log(JSON.stringify(Object.keys(%s)));'
        % (json.dumps(JS_REPORT.as_uri()), expression)
    )
    out = subprocess.run(
        ['node', '--input-type=module', '-e', script],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        pytest.fail('node failed: %s' % out.stderr.strip())
    return set(json.loads(out.stdout))


@pytest.fixture(scope='module')
def sources():
    return {rel: path.read_text(encoding='utf-8', errors='replace')
            for rel, path in js_files()}


@pytest.fixture(scope='module')
def sites(sources):
    """`{name: {file: count}}` across every served module."""
    out = {}
    for rel, source in sources.items():
        for name, hits in reads(source).items():
            out.setdefault(name, {})[rel] = len(hits)
    return out


@pytest.fixture(scope='module')
def written_by_python():
    return set(player_report_fields({}))


@pytest.fixture(scope='module')
def written_by_browser():
    return node_keys('cvReportFields({touches: 1}, '
                     '{onPitchS: 1, watchedS: 1, share: 1}, null)')


@pytest.fixture(scope='module')
def cleared():
    return node_keys('cvReportFields(null)')


@pytest.fixture(scope='module')
def read_fields(sites):
    return {n for n in sites if n not in NOT_A_REPORT_FIELD}


# ------------------------------------------------------- the four corners

def test_the_two_writers_together_are_the_clearing_list(
        written_by_python, written_by_browser, cleared):
    """Neither writer alone covers it, and the gap between them is the point.

    A field on the list that nothing writes is a null published over and over
    for no reason. A field written that the list does not carry is the bug
    `tests/test_player_merge_parity.py` describes at length: it survives a
    coach un-naming the tracked figure it came from.
    """
    both = written_by_python | written_by_browser
    assert both == cleared, (
        'the writers and the clearing list disagree; written and not cleared: '
        '%s, cleared and not written: %s'
        % (sorted(both - cleared), sorted(cleared - both))
    )


def test_the_fields_only_the_pipeline_writes_are_the_documented_three(
        written_by_python, written_by_browser):
    """A heatmap, which way they were kicking, and how good the homography was.

    The coach's page cannot compute any of the three, so `cvReportFields` does
    not write them — but it must still clear them, which is why they are in
    `CV_REPORT_KEYS` under a comment saying so.
    """
    assert written_by_python - written_by_browser == PIPELINE_ONLY


def test_every_field_written_is_read_by_some_page(cleared, read_fields):
    """Otherwise it is payload on a document with a key cap and no reader."""
    assert cleared <= read_fields, (
        'written and read by nothing: %s' % sorted(cleared - read_fields))


def test_every_cv_field_read_is_one_something_writes(cleared, read_fields):
    """The direction that hurts: a name nothing writes reads as undefined, and
    undefined reaches the screen as the same dash as a real absence."""
    assert read_fields <= cleared, (
        'read off a report that nothing writes: %s'
        % sorted(read_fields - cleared))


# --------------------------------------------- the exclusions, both ways

def test_the_non_report_names_are_not_report_fields(cleared):
    """Forward: nothing on the exclusion list may become a written field."""
    overlap = set(NOT_A_REPORT_FIELD) & cleared
    assert not overlap, (
        'excluded as "not a report field" and written as one: %s'
        % sorted(overlap))


def test_the_non_report_names_are_all_still_read(sites):
    """Backward: an entry nothing reads any more is a stale entry to delete.

    Without this the list only ever grows, and the next person to add a name to
    it cannot tell which of the others are still load-bearing.
    """
    stale = set(NOT_A_REPORT_FIELD) - set(sites)
    assert not stale, (
        'listed as read-but-not-a-report-field, and read nowhere: %s'
        % sorted(stale))


def test_no_report_field_is_reached_through_a_bracket(sources):
    """The scan reads property syntax, so this is the shape it would miss.

    There are none today. The test is here so the limit stays harmless rather
    than staying a promise: write one and this fails, pointing at the file that
    the property scan can no longer speak for.
    """
    found = ['%s:%d' % (rel, line)
             for rel, src in sources.items()
             for line in bracket_reads(src, blank(src))]
    assert not found, 'bracket reads of a cv name, invisible to this scan: %s' \
        % found


# ------------------------------------------------------------ the scanner

def test_the_scanner_tells_a_read_from_a_spread():
    found = reads('const a = x.cvTouches + [...cvTallies()].length;')
    assert set(found) == {'cvTouches'}


def test_the_scanner_reads_through_optional_chaining():
    assert set(reads('state.match?.cvEvents?.events')) == {'cvEvents'}


def test_the_scanner_ignores_a_name_in_a_comment_or_a_string():
    assert reads('// x.cvTouches\nconst k = ".cvTouches";') == {}


def test_the_scanner_ignores_an_object_literal_key():
    """`cvTouches: n` is a write, not a read, and counting it as one would let
    a field satisfy the read check by being written."""
    assert reads('return { cvTouches: n };') == {}


# --------------------------------------------------------- anti-vacuum

def test_the_scan_actually_scanned_something(sources, sites, cleared,
                                             written_by_python, read_fields):
    """A pass here means nothing unless all four corners were really read."""
    assert len(sources) >= 20, len(sources)
    assert all(len(s) > 200 for s in sources.values())
    assert len(sites) >= 30, sorted(sites)
    assert sum(sum(f.values()) for f in sites.values()) >= 60
    assert len(cleared) >= 24, sorted(cleared)
    assert len(written_by_python) >= 20, sorted(written_by_python)
    assert len(read_fields) >= 24, sorted(read_fields)
    # Read across the coach's page and the player's, not one file talking to
    # itself: the two screens are the reason these names have to travel.
    files = {f for name in read_fields for f in sites[name]}
    assert len({f.split('/')[0] for f in files}) >= 3, sorted(files)
