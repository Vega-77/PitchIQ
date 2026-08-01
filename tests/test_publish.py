"""The guards on the one credential that bypasses every security rule.

cv/publish.py authenticates with a Firebase service account, which means the
Firestore rules — the entire security boundary of an app holding minors' names
and email addresses — simply do not apply to it. Nothing it does is checked by
anything except this file.

So these tests are not about whether the writing works. They are about the
four refusals: a key inside the repo, a path that is not a document id,
per-player stats without a human's confirmation, and creating a player report
that no coach published.

The Firestore client is faked. It has to be — the real one would need a
credential, and a test that needs a credential is a test nobody runs.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_publish.py -q
"""

from __future__ import annotations

import pytest

from cv.publish import (
    CV_FIELD_PREFIX,
    PublishError,
    _check_key,
    _check_path,
    identity_payload,
    player_report_fields,
    publish,
    summary_payload,
)


# ------------------------------------------------------------------ fakes


class FakeDoc:
    def __init__(self, client, path):
        self.client = client
        self.path = path

    def set(self, data):
        self.client.store[self.path] = dict(data)

    def update(self, data):
        self.client.store.setdefault(self.path, {}).update(data)

    def get(self):
        return self

    @property
    def exists(self):
        """Existence by name, so a test can say which reports were published.

        Matching on the trailing document id rather than the whole path keeps
        the fixtures readable; the paths themselves are asserted separately.
        """
        return self.path.rsplit('/', 1)[-1] not in self.client.missing

    def collection(self, name):
        return FakeCollection(self.client, f'{self.path}/{name}')


class FakeCollection:
    def __init__(self, client, path):
        self.client = client
        self.path = path

    def document(self, name):
        return FakeDoc(self.client, f'{self.path}/{name}')


class FakeClient:
    def __init__(self, missing_reports=()):
        self.store: dict[str, dict] = {}
        self.missing = set(missing_reports)

    def collection(self, name):
        return FakeCollection(self, name)


# Kept as a name so the tests that care about missing reports read clearly.
GuardedClient = FakeClient


def a_report(tracks=None, clusters=None):
    return {
        'schema_version': 1,
        'source': 'clip.mp4',
        'window': {'start_s': 0, 'end_s': 15},
        'duration_s': 15.0,
        'calibrated': False,
        'calibration_error_m': None,
        'quality': {'ball_seen_share': 0.65},
        'warnings': ['no calibration supplied'],
        'trustworthy': False,
        'teams': {'team_a': {'passes_attempted': 12}},
        'keepers': [],
        'clusters': clusters or [{'cluster_id': 0, 'track_ids': [1, 2]}],
        'tracks': tracks or [{
            'cluster_id': 0, 'team': 'team_a', 'touches': 9,
            'passes_attempted': 5, 'passes_completed': 4, 'pass_accuracy': 0.8,
            'carries': 2, 'tackles': 1, 'interceptions': 0, 'recoveries': 1,
            'shots': None, 'xg': None, 'distance_m': None,
            'top_speed_kmh': None, 'sprint_count': None, 'minutes_tracked': 12.0,
        }],
        'events': [],
    }


# ------------------------------------------------------------------ guards


class TestKeyLocation:
    def test_a_key_inside_the_repo_is_refused(self, tmp_path):
        """The check that matters most.

        This repo publishes to GitHub Pages. A service account JSON committed
        here is full read and write access to every team's data, for anyone who
        clones it, and nothing downstream would notice.
        """
        from cv.publish import REPO_ROOT

        key = REPO_ROOT / 'accidentally-here.sa.json'
        key.write_text('{}', encoding='utf-8')
        try:
            with pytest.raises(PublishError, match='inside the repository'):
                _check_key(str(key))
        finally:
            key.unlink()

    def test_a_key_outside_the_repo_is_accepted(self, tmp_path):
        key = tmp_path / 'pitchiq.sa.json'
        key.write_text('{}', encoding='utf-8')
        assert _check_key(str(key)) == key.resolve()

    def test_a_missing_env_var_explains_itself(self):
        with pytest.raises(PublishError, match='PITCHIQ_SA_KEY'):
            _check_key(None)

    def test_a_path_to_nothing_is_refused(self, tmp_path):
        with pytest.raises(PublishError, match='no service account key'):
            _check_key(str(tmp_path / 'nope.json'))


class TestPathValidation:
    @pytest.mark.parametrize('bad', ['', 'a/b', '.', '..', None])
    def test_ids_that_are_not_document_ids_are_refused(self, bad):
        """A slash silently retargets the write at a different collection,
        which is how an allowlist stops being one."""
        with pytest.raises(PublishError):
            _check_path(bad, 'match1')
        with pytest.raises(PublishError):
            _check_path('team1', bad)

    def test_plain_ids_pass(self):
        assert _check_path('team1', 'match1') is None


# ------------------------------------------------------------------ writing


class TestPublish:
    def test_team_stats_land_under_cvstats(self):
        client = FakeClient()
        publish(a_report(), 'team1', 'match1', client=client)
        assert 'teams/team1/matches/match1/cvStats/summary' in client.store
        assert 'teams/team1/matches/match1/cvStats/identity' in client.store

    def test_nothing_outside_the_allowlist_is_touched(self):
        """A bug here must not be able to reach players, users or invites —
        the documents holding personal data."""
        client = FakeClient()
        publish(a_report(), 'team1', 'match1', {'0': 'playerA'}, client=client)
        for path in client.store:
            assert '/cvStats/' in path or '/playerReports/' in path, path
        assert not any(
            path.startswith(('users', 'invites')) or '/players/' in path
            for path in client.store
        )

    def test_the_quality_block_and_warnings_travel_with_the_stats(self):
        client = FakeClient()
        publish(a_report(), 'team1', 'match1', client=client)
        summary = client.store['teams/team1/matches/match1/cvStats/summary']
        assert summary['quality']['ball_seen_share'] == 0.65
        assert summary['warnings']
        assert summary['trustworthy'] is False

    def test_events_are_not_written_to_firestore(self):
        """A half of football is tens of thousands of touches and a document
        caps at a megabyte. They stay in the JSON file."""
        client = FakeClient()
        publish(a_report(), 'team1', 'match1', client=client)
        summary = client.store['teams/team1/matches/match1/cvStats/summary']
        assert 'events' not in summary
        assert 'touches' not in summary


class TestPerPlayerGate:
    def test_no_mapping_means_no_per_player_writes(self):
        """A cluster is a guess about identity until a human agrees with it.

        Writing guesses into a named player's season is the single thing that
        would make the whole feature untrustworthy.
        """
        client = FakeClient()
        written = publish(a_report(), 'team1', 'match1', client=client)
        assert written['playerReports'] == 0
        assert any('mapping' in note for note in written['skipped'])
        assert not any('/playerReports/' in path for path in client.store)

    def test_a_confirmed_mapping_writes_prefixed_fields(self):
        client = GuardedClient()
        written = publish(a_report(), 'team1', 'match1', {'0': 'playerA'}, client=client)
        assert written['playerReports'] == 1

        fields = client.store['teams/team1/matches/match1/playerReports/playerA']
        assert fields[f'{CV_FIELD_PREFIX}Touches'] == 9
        assert fields[f'{CV_FIELD_PREFIX}PassesCompleted'] == 4

    def test_every_written_field_is_prefixed(self):
        """So a coach can always tell an estimate from something a human tapped,
        and so removing them later is one filter."""
        assert all(
            key.startswith(CV_FIELD_PREFIX)
            for key in player_report_fields({'touches': 1})
        )

    def test_a_missing_player_report_is_reported_not_created(self):
        """A report exists because a coach published the match.

        Inventing one hides a mismatch between the mapping and the roster.
        """
        client = GuardedClient(missing_reports=('playerA',))
        written = publish(a_report(), 'team1', 'match1', {'0': 'playerA'}, client=client)
        assert written['playerReports'] == 0
        assert any('no published report' in note for note in written['skipped'])

    def test_a_mapping_to_a_cluster_with_no_stats_is_reported(self):
        client = GuardedClient()
        written = publish(a_report(), 'team1', 'match1', {'99': 'playerA'}, client=client)
        assert written['playerReports'] == 0
        assert any('no stats' in note for note in written['skipped'])


class TestPayloads:
    def test_the_mapping_starts_empty(self):
        assert identity_payload(a_report())['playerByCluster'] == {}

    def test_summary_keeps_the_schema_version(self):
        """There is no build step, so a reader cannot be updated in lockstep."""
        assert summary_payload(a_report())['schemaVersion'] == 1

    def test_an_empty_report_still_produces_a_valid_payload(self):
        payload = summary_payload({})
        assert payload['teams'] == {}
        assert payload['trustworthy'] is False
