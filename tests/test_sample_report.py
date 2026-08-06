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

from pathlib import Path

import pytest

from cv.publish import player_report_fields, summary_payload
from cv.report_json import TeamStats

SAMPLE_JS = Path(__file__).resolve().parents[1] / 'assets' / 'sample-report.js'


@pytest.fixture(scope='module')
def source() -> str:
    return SAMPLE_JS.read_text(encoding='utf-8')


def missing(keys, source: str) -> list[str]:
    """Which of `keys` never appear in the fixture."""
    return sorted(k for k in keys if f"'{k}'" not in source and f'{k}:' not in source)


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

    def test_every_team_statistic_is_represented(self, source):
        """The team rows are most of the coach's match view.

        `cvTeamRows` filters nulls, so a statistic absent from the fixture does
        not error — its row just never appears, and the preview looks complete
        while covering one row fewer than it claims.
        """
        keys = TeamStats(team='team_a').to_json().keys()
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
