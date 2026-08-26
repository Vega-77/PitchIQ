"""The sample fixture has to stay the same shape as a real published run.

`assets/sample-report.js` exists so the CV blocks on the coach, player and
half-time pages can be looked at before there is any footage. That is only worth
anything while it is an honest stand-in: the moment Python starts publishing a
field the sample does not carry, the preview quietly stops covering whatever
reads it, and nobody finds out until the first real match.

So this compares the two directly. It reads the JavaScript as text rather than
executing it — there is no build step in this project and no JS runtime is a
Python test dependency — and checks that every key `cv/publish.py` writes appears
somewhere in the fixture. Crude, and right for the failure it is guarding:
a field added on the Python side and forgotten on the JavaScript one.

The reverse direction is deliberately not checked. The fixture is allowed extra
keys — `isSample` is one, and a field removed from the pipeline can sit in the
fixture harmlessly until someone tidies it.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_sample_report.py -q
"""

import re
from pathlib import Path

import pytest

from cv.keeper import KeeperReport
from cv.publish import player_report_fields, summary_payload
from cv.report_json import TeamStats

SAMPLE_JS = Path(__file__).resolve().parents[1] / 'assets' / 'sample-report.js'


@pytest.fixture(scope='module')
def source() -> str:
    return SAMPLE_JS.read_text(encoding='utf-8')


def missing(keys, source: str) -> list[str]:
    """Which of `keys` never appear in the fixture."""
    return sorted(k for k in keys if f"'{k}'" not in source and f'{k}:' not in source)


def _quality_keys() -> list[str]:
    """Every field `_quality` writes, read off the function rather than listed.

    A hand-kept list here would need updating by the same person who forgot to
    update the fixture, which is no guard at all. Building a report is not
    possible without footage, so the names are read out of the source — crude in
    the same way `missing` is, and aimed at the same failure.
    """
    text = (Path(__file__).resolve().parents[1] / 'cv' / 'report_json.py').read_text(
        encoding='utf-8'
    )
    body = text.split('def _quality(')[1].split('\ndef ')[0]
    return sorted(set(re.findall(r"^ {8}'([a-z_0-9]+)':", body, re.M)))


class TestItExists:
    def test_the_fixture_is_where_the_pages_import_it_from(self):
        assert SAMPLE_JS.is_file()

    def test_it_imports_nothing(self, source):
        """Same rule as report.js, for the same reason.

        tests/video.test.js loads these modules straight off disk with no
        bundler and no DOM. One import of db.js would open a Firestore
        connection at module scope and take the whole pure suite with it.
        """
        assert '\nimport ' not in source
        assert not source.startswith('import ')


class TestTheTeamDocument:
    def test_every_summary_key_is_represented(self, source):
        """A key `summary_payload` writes and the fixture lacks is a block the
        preview silently cannot check."""
        keys = summary_payload({}).keys()
        assert keys, 'summary_payload produced nothing to compare against'
        assert not missing(keys, source)

    def test_every_quality_key_is_represented(self, source):
        """The guard reached the top level only, and `quality` is where fields
        actually get added.

        Found by adding one. `quality.pitch_coverage` went in on the Python side
        and the fixture knew nothing about it, while the test above stayed green
        — because `quality` itself was present and nothing looked inside. That
        is precisely the failure this file exists to catch, one level down.
        """
        quality = summary_payload({'quality': {}})
        keys = _quality_keys()
        assert keys, 'no quality keys to compare against'
        assert 'quality' in quality
        assert not missing(keys, source)

    def test_every_team_statistic_is_represented(self, source):
        """The team rows are most of the coach's match view.

        `teamStatRows` builds them and `groupStats` drops the ones that are null
        on both sides, so a statistic absent from the fixture does not error —
        its row just never appears, and the preview looks complete while
        covering one row fewer than it claims.

        That every one of these fields reaches a page at all is the other end of
        the same seam, and `tests/test_teams_seam.py` is where it is checked.
        """
        keys = TeamStats(team='team_a').to_json().keys()
        assert not missing(keys, source)

    def test_every_keeper_statistic_is_represented(self, source):
        """The keeping block is the same shape of hole one group down.

        `keeperStatRows` builds eleven rows off these fields and `groupStats`
        drops the ones that are null on both sides, so a field the fixture
        forgets does not error — its row just never appears, and the preview
        looks complete while covering one row fewer than it claims. Exactly the
        failure `test_every_quality_key_is_represented` was written for, in the
        block next door.

        That every one of these fields reaches a page at all is the other end of
        the same seam, and `tests/test_keepers_seam.py` is where it is checked.
        """
        keys = KeeperReport(team='team_a').to_json().keys()
        assert not missing(keys, source)


class TestThePlayerDocument:
    def test_every_published_player_field_is_represented(self, source):
        keys = player_report_fields({}).keys()
        assert keys
        assert not missing(keys, source)

    def test_the_calibration_error_reaches_the_fixture(self, source):
        """Specifically called out because it is the newest of them and the one
        the player portal needs to apply the same xG trust band the coach's page
        does. A preview missing it would size a shot map the real page flattens.
        """
        assert 'cvCalibrationErrorM' in source


class TestItSaysWhatItIs:
    def test_every_sample_object_is_marked(self, source):
        """`isSample: true` is what makes "was this real?" a grep rather than a
        judgement call."""
        assert source.count('isSample: true') >= 2

    def test_it_never_writes(self, source):
        """Nothing in this file may reach Firestore.

        The fixture is invented numbers in the real schema, which is exactly the
        shape that would be accepted by a write path without complaint.
        """
        for forbidden in ('setDoc', 'updateDoc', 'addDoc', 'writeBatch', 'firebase'):
            assert forbidden not in source
