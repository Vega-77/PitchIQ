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
    MAX_PARTICIPANT_NOTES,
    events_payload,
    identity_payload,
    merge_tracks,
    thumbs_payload,
    participant_notes,
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


def a_report(tracks=None, clusters=None, **extra):
    return {**{
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
    }, **extra}


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

    def test_events_stay_out_of_the_summary(self):
        """The summary is what renders a team total, and no total is computed
        from an event list. Events get their own document — see TestEvents."""
        client = FakeClient()
        publish(a_report(), 'team1', 'match1', client=client)
        summary = client.store['teams/team1/matches/match1/cvStats/summary']
        assert 'events' not in summary
        assert 'touches' not in summary


def an_event(index=0, kind='pass', confidence=0.8, timestamp_s=None):
    return {
        'event_id': f'e{index}',
        'type': kind,
        'timestamp_s': float(index) if timestamp_s is None else timestamp_s,
        'frame_index': index * 30,
        'team': 'team_a',
        'track_id': 7,
        'start_m': None,
        'end_m': None,
        'confidence': confidence,
        'tags': [],
        'in_play': True,
    }


class TestEvents:
    """The review tool's only source. Without this document a coach can see
    that the pipeline found 84 passes and never see one of them."""

    def test_events_get_their_own_document(self):
        client = FakeClient()
        report = a_report()
        report['events'] = [an_event(0), an_event(1)]

        written = publish(report, 'team1', 'match1', client=client)
        doc = client.store['teams/team1/matches/match1/cvStats/events']

        assert written['events'] == 2
        assert [e['id'] for e in doc['events']] == ['e0', 'e1']
        assert doc['counts'] == {'pass': 2}
        assert doc['truncated'] is False
        assert doc['droppedBelowConfidence'] is None

    def test_a_run_with_no_events_still_writes_the_document(self):
        """An empty list and a missing document read very differently to a
        coach opening the review tool."""
        client = FakeClient()
        publish(a_report(), 'team1', 'match1', client=client)
        doc = client.store['teams/team1/matches/match1/cvStats/events']
        assert doc['events'] == []

    def test_only_the_fields_the_review_tool_needs_survive(self):
        doc = events_payload({'events': [an_event()]})
        assert set(doc['events'][0]) == {
            'id', 'type', 'timestampS', 'trackId', 'team', 'confidence',
            'inPlay', 'outcome', 'xg', 'xgHeader', 'receiverTrackId', 'startM',
        }

    def test_where_the_event_happened_travels(self):
        """The passing network is drawn from these, so a pass with no position
        is a player with counts and nowhere to stand."""
        event = an_event()
        event['start_m'] = (41.27, 10.94)
        doc = events_payload({'events': [event]})
        # One decimal place: a tenth of a metre is already finer than the
        # homography behind it, and there are up to 1500 of these in one
        # document.
        assert doc['events'][0]['startM'] == [41.3, 10.9]

    def test_an_uncalibrated_event_has_no_position_rather_than_zero(self):
        # Zero is the corner flag. Every consumer has to see absent as absent.
        doc = events_payload({'events': [an_event()]})
        assert doc['events'][0]['startM'] is None

    def test_the_in_play_flag_reaches_the_client(self):
        event = an_event()
        event['in_play'] = False
        assert events_payload({'events': [event]})['events'][0]['inPlay'] is False


class TestEventCap:
    def test_a_runaway_run_is_capped_rather_than_failing_to_write(self):
        events = [an_event(i) for i in range(50)]
        doc = events_payload({'events': events}, limit=10)

        assert len(doc['events']) == 10
        assert doc['truncated'] is True

    def test_the_most_confident_are_kept(self):
        events = [an_event(i, confidence=i / 100) for i in range(50)]
        doc = events_payload({'events': events}, limit=5)

        assert {e['id'] for e in doc['events']} == {'e45', 'e46', 'e47', 'e48', 'e49'}
        assert doc['droppedBelowConfidence'] == pytest.approx(0.45)

    def test_but_they_come_back_in_clock_order(self):
        """A reviewer works down a timeline, not a ranking."""
        events = [
            an_event(0, confidence=0.1, timestamp_s=10.0),
            an_event(1, confidence=0.9, timestamp_s=20.0),
            an_event(2, confidence=0.5, timestamp_s=5.0),
        ]
        doc = events_payload({'events': events}, limit=2)
        assert [e['timestampS'] for e in doc['events']] == [5.0, 20.0]

    def test_a_truncated_list_says_so_rather_than_stopping_silently(self):
        """A list that just stops is indistinguishable from a pipeline that
        stopped finding things."""
        doc = events_payload({'events': [an_event(i) for i in range(20)]}, limit=3)
        assert doc['truncated'] is True
        assert doc['droppedBelowConfidence'] is not None

    def test_a_list_at_exactly_the_limit_is_not_truncated(self):
        doc = events_payload({'events': [an_event(i) for i in range(10)]}, limit=10)
        assert doc['truncated'] is False
        assert len(doc['events']) == 10


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

    def test_the_calibration_error_travels_to_each_player(self):
        """A player never reads the team document.

        Without this field the player portal cannot apply `xgTrust`, and would
        size its shot map by an xG the coach's own page had already decided was
        too loose to size by — the same match told two different ways, on the
        page with the least context to notice.
        """
        report = a_report()
        report['calibration_error_m'] = 2.5
        client = GuardedClient()
        publish(report, 'team1', 'match1', {'0': 'playerA'}, client=client)

        fields = client.store['teams/team1/matches/match1/playerReports/playerA']
        assert fields[f'{CV_FIELD_PREFIX}CalibrationErrorM'] == 2.5

    def test_an_uncalibrated_run_says_so_rather_than_omitting_it(self):
        """None, not absent. A missing key and a known-absent calibration read
        identically to the browser, and only one of them is a fact."""
        fields = player_report_fields({'touches': 1})
        assert fields[f'{CV_FIELD_PREFIX}CalibrationErrorM'] is None

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


class TestOnePlayerSeveralFigures:
    """A student tracked as two figures is one student.

    The tracker loses people when they leave frame and `cv/identity.py` only
    rejoins fragments a couple of seconds apart, so anyone who went off and came
    back stays split. The cluster picker lets a coach map both figures to the
    same player on purpose — `cvStatsByPlayer` in assets/report.js says so in
    its docstring, and sums them.

    This side wrote one document per cluster, so the second write replaced the
    first and the student's own report showed whichever fragment came last in
    the mapping. The coach's screen said 54 touches and 7.3 km; the student's
    page said 24 and 3.1, for the same afternoon.
    """

    @staticmethod
    def two_fragments():
        return [
            {'cluster_id': 0, 'team': 'team_a', 'touches': 30,
             'passes_attempted': 20, 'passes_completed': 16, 'pass_accuracy': 0.8,
             'carries': 5, 'tackles': 2, 'interceptions': 1, 'recoveries': 3,
             'shots': 2, 'xg': 0.3, 'distance_m': 4200.0, 'top_speed_kmh': 27.0,
             'sprint_count': 9, 'minutes_tracked': 28.0, 'position_noise_m': 0.4,
             'touch_times_s': [90.0, 30.0], 'heatmap': [[1.0, 0.0], [0.0, 2.0]]},
            {'cluster_id': 1, 'team': 'team_a', 'touches': 24,
             'passes_attempted': 15, 'passes_completed': 10, 'pass_accuracy': 0.667,
             'carries': 4, 'tackles': 1, 'interceptions': 0, 'recoveries': 2,
             'shots': 1, 'xg': 0.1, 'distance_m': 3100.0, 'top_speed_kmh': 29.5,
             'sprint_count': 7, 'minutes_tracked': 21.0, 'position_noise_m': 1.9,
             'touch_times_s': [60.0], 'heatmap': [[0.0, 3.0], [1.0, 0.0]]},
        ]

    def published(self):
        client = GuardedClient()
        report = a_report(
            tracks=self.two_fragments(),
            clusters=[{'cluster_id': 0, 'track_ids': [1]},
                      {'cluster_id': 1, 'track_ids': [2]}],
        )
        written = publish(
            report, 'team1', 'match1',
            {'0': 'playerA', '1': 'playerA'}, client=client,
        )
        return written, client.store[
            'teams/team1/matches/match1/playerReports/playerA'
        ]

    def test_one_player_is_one_report(self):
        written, _ = self.published()
        assert written['playerReports'] == 1, (
            'two figures of one player were counted as two published reports'
        )

    def test_what_they_did_adds_up(self):
        _, fields = self.published()
        assert fields[f'{CV_FIELD_PREFIX}Touches'] == 54
        assert fields[f'{CV_FIELD_PREFIX}DistanceM'] == 7300.0
        assert fields[f'{CV_FIELD_PREFIX}MinutesTracked'] == 49.0
        assert fields[f'{CV_FIELD_PREFIX}Shots'] == 3

    def test_top_speed_is_the_fastest_they_ran_not_the_last(self):
        _, fields = self.published()
        assert fields[f'{CV_FIELD_PREFIX}TopSpeedKmh'] == 29.5

    def test_the_wobble_is_taken_at_the_worst_fragment(self):
        """A player assembled from a clean track and a jittery one is only as
        trustworthy as the jittery one, and an average would hide that."""
        _, fields = self.published()
        assert fields[f'{CV_FIELD_PREFIX}PositionNoiseM'] == 1.9

    def test_accuracy_is_recomputed_rather_than_averaged(self):
        """26 of 35, not the mean of 0.8 and 0.667."""
        _, fields = self.published()
        assert fields[f'{CV_FIELD_PREFIX}PassAccuracy'] == pytest.approx(26 / 35)

    def test_the_touches_come_back_as_a_timeline(self):
        """Fragments arrive in mapping order, and a touch strip in mapping order
        is not a timeline — the player portal marks these on the video."""
        _, fields = self.published()
        assert fields[f'{CV_FIELD_PREFIX}TouchTimes'] == [30.0, 60.0, 90.0]

    def test_the_heatmap_covers_both_halves_of_their_match(self):
        _, fields = self.published()
        assert fields[f'{CV_FIELD_PREFIX}Heatmap']['values'] == [1.0, 3.0, 1.0, 2.0]

    def test_a_question_no_fragment_answered_stays_unanswered(self):
        """None is not zero. A fragment too short to measure a burst contributes
        nothing to the count rather than pulling it towards none."""
        tracks = self.two_fragments()
        for track in tracks:
            track['accelerations'] = None
        merged = merge_tracks(tracks)
        assert merged['accelerations'] is None

    def test_one_fragment_answering_is_enough(self):
        tracks = self.two_fragments()
        tracks[0]['accelerations'] = None
        tracks[1]['accelerations'] = 4
        assert merge_tracks(tracks)['accelerations'] == 4

    def test_grids_of_different_shapes_are_no_grid(self):
        """Every heatmap comes from the same `Pitch`, so a mismatch means two
        runs got mixed. Half a heatmap is worse than none."""
        tracks = self.two_fragments()
        tracks[1]['heatmap'] = [[1.0, 2.0, 3.0]]
        assert merge_tracks(tracks)['heatmap'] is None

    def test_one_figure_is_passed_through_untouched(self):
        """The overwhelmingly common case, and it must not go near the merge."""
        one = self.two_fragments()[0]
        assert merge_tracks([one]) is one


class TestPayloads:
    def test_the_mapping_starts_empty(self):
        assert identity_payload(a_report())['playerByCluster'] == {}

    def test_summary_keeps_the_schema_version(self):
        """There is no build step, so a reader cannot be updated in lockstep."""
        assert summary_payload(a_report())['schemaVersion'] == 1

    def test_the_reconciliation_travels_to_the_coach(self):
        """Small enough to carry whole, and the one part of the report that
        says where a reviewer should start."""
        block = {'goal_agreement': 0.5, 'disagreements': [{'status': 'tag_only'}]}
        payload = summary_payload(a_report(reconciliation=block))
        assert payload['reconciliation'] == block

    def test_a_run_with_no_tagged_log_publishes_null_not_an_empty_object(self):
        assert summary_payload(a_report())['reconciliation'] is None

    def test_the_pressing_blocks_reach_the_coach_with_the_team(self):
        """Six blocks of four small numbers, nowhere near the document cap, and
        the chart on the match view has no other source for them."""
        blocks = [
            {'start_s': 0, 'end_s': 900, 'allowed': 48, 'actions': 12, 'ppda': 4.0},
            {'start_s': 900, 'end_s': 1800, 'allowed': 45, 'actions': 3, 'ppda': None},
        ]
        payload = summary_payload(a_report(teams={
            'team_a': {'ppda': 6.2, 'pressing_segments': blocks},
        }))
        assert payload['teams']['team_a']['pressing_segments'] == blocks

    def test_an_empty_report_still_produces_a_valid_payload(self):
        payload = summary_payload({})
        assert payload['teams'] == {}
        assert payload['trustworthy'] is False

    def test_a_figures_picture_still_reaches_the_app(self):
        """The crop is the whole point of the picker and still travels.

        It travels in `thumbs` now rather than inside `identity`, so that a
        coach can delete the photographs of their players without destroying
        every statistic in the same document. See TestThePicturesAreKeptApart
        below, and THUMBS_DOC in cv/publish.py for why.
        """
        report = a_report(clusters=[{
            'cluster_id': 0, 'sightings': 900,
            'thumb': 'data:image/jpeg;base64,abc', 'thumb_height_px': 71.0,
        }])
        picture = thumbs_payload(report)['byCluster']['0']
        assert picture['thumb'] == 'data:image/jpeg;base64,abc'
        assert picture['thumb_height_px'] == 71.0

    def test_a_figure_never_seen_cleanly_has_no_entry_at_all(self):
        """It used to publish an explicit null; now it is simply absent.

        Both read the same to the picker, which draws "no clear view" — and
        absent is what a deleted set looks like too, so the two cases stay
        indistinguishable by design.
        """
        report = a_report(clusters=[{'cluster_id': 0, 'thumb': None}])
        assert thumbs_payload(report)['byCluster'] == {}
        assert 'thumb' not in identity_payload(report)['clusters'][0]


class TestParticipantNotes:
    """Why figures were left out, carried to the screen with the counts.

    The classifier's thresholds are guesses that have never met a real
    touchline, so the sentence a guess produced has to travel with it. A coach
    shown "9 excluded" cannot tell a stationary parent from a wrongly-dropped
    goalkeeper; one shown "never moved more than 0.4 of a body length in 41
    minutes" can.
    """

    def report(self, participants):
        return {'participants': participants}

    def verdict(self, track_id, role, screen_time_s=100.0, reason='because'):
        return {
            'track_id': track_id, 'role': role, 'reason': reason,
            'screen_time_s': screen_time_s,
        }

    def test_players_are_not_news(self):
        notes = participant_notes(self.report([
            self.verdict(1, 'player'), self.verdict(2, 'offfield'),
        ]))
        assert [n['trackId'] for n in notes] == [2]

    def test_officials_are_reported_even_though_they_were_kept(self):
        """Being carried inside the counts is exactly why it needs saying."""
        notes = participant_notes(self.report([self.verdict(3, 'official')]))
        assert notes[0]['role'] == 'official'

    def test_unsure_is_not_a_third_kind_of_rejection(self):
        """`unsure` tracks are kept and counted as players. Listing them under
        a heading about figures left out would say the opposite."""
        assert participant_notes(self.report([self.verdict(4, 'unsure')])) == []

    def test_the_reason_survives_the_trip_unedited(self):
        notes = participant_notes(self.report([
            self.verdict(5, 'offfield', reason='never moved more than 0.4 of a '
                         'body length from one spot in 2460s'),
        ]))
        assert notes[0]['reason'].startswith('never moved more than 0.4')

    def test_longest_on_screen_first(self):
        """A figure watched for forty minutes and then dropped is a bigger
        claim than one seen for twenty-five seconds."""
        notes = participant_notes(self.report([
            self.verdict(1, 'offfield', screen_time_s=30.0),
            self.verdict(2, 'official', screen_time_s=2400.0),
            self.verdict(3, 'offfield', screen_time_s=600.0),
        ]))
        assert [n['trackId'] for n in notes] == [2, 3, 1]

    def test_a_crowd_is_capped(self):
        crowd = [self.verdict(i, 'offfield', screen_time_s=float(i))
                 for i in range(200)]
        notes = participant_notes(self.report(crowd))
        assert len(notes) == MAX_PARTICIPANT_NOTES
        # The longest-watched survive, and the authoritative totals are in
        # `quality`, so the cap costs the least significant reasons and never
        # the size of the correction.
        assert notes[0]['trackId'] == 199

    def test_a_run_from_before_the_classifier_is_empty_not_an_error(self):
        assert participant_notes({}) == []
        assert participant_notes({'participants': None}) == []

    def test_a_missing_screen_time_does_not_break_the_sort(self):
        notes = participant_notes(self.report([
            {'track_id': 1, 'role': 'offfield', 'reason': 'x'},
            self.verdict(2, 'offfield', screen_time_s=10.0),
        ]))
        assert [n['trackId'] for n in notes] == [2, 1]

    def test_the_summary_carries_them(self):
        payload = summary_payload(self.report([self.verdict(7, 'offfield')]))
        assert [n['trackId'] for n in payload['participants']] == [7]


class TestThePicturesAreKeptApart:
    """Photographs of children, in a document that can be deleted on its own.

    They ride into the app so a coach can look at a tracked figure and name it.
    While they lived inside `identity` next to every per-track statistic, no
    client could remove them without destroying the match — and `cvStats` is
    `allow write: if false`, so no client could remove them at all.
    """

    def report(self, thumbs=True):
        return {
            'clusters': [
                {'cluster_id': 0, 'team': 'team_a', 'sightings': 400,
                 'thumb': 'data:image/png;base64,AAA' if thumbs else None,
                 'thumb_height_px': 90.0 if thumbs else None},
                {'cluster_id': 1, 'team': 'team_b', 'sightings': 200,
                 'thumb': None, 'thumb_height_px': None},
            ],
            'tracks': [],
        }

    def test_no_picture_survives_in_the_stats_document(self):
        clusters = identity_payload(self.report())['clusters']
        assert all('thumb' not in c for c in clusters)
        assert all('thumb_height_px' not in c for c in clusters)

    def test_everything_else_about_a_figure_stays(self):
        # The split must cost nothing. A cluster is still a cluster.
        [first, _] = identity_payload(self.report())['clusters']
        assert first['cluster_id'] == 0
        assert first['team'] == 'team_a'
        assert first['sightings'] == 400

    def test_the_pictures_go_to_their_own_document_keyed_by_cluster(self):
        by_cluster = thumbs_payload(self.report())['byCluster']
        assert set(by_cluster) == {'0'}
        assert by_cluster['0']['thumb'].startswith('data:image/png')
        assert by_cluster['0']['thumb_height_px'] == 90.0

    def test_a_figure_never_seen_clearly_is_absent_rather_than_null(self):
        # Absent and deleted then read identically, which is the point: the
        # picker draws "no clear view" for both, and neither is a fault.
        assert '1' not in thumbs_payload(self.report())['byCluster']

    def test_a_run_with_no_pictures_at_all_is_an_empty_document(self):
        assert thumbs_payload(self.report(thumbs=False)) == {'byCluster': {}}
        assert thumbs_payload({}) == {'byCluster': {}}

    def test_both_payloads_survive_json(self):
        import json
        json.dumps(identity_payload(self.report()))
        json.dumps(thumbs_payload(self.report()))
