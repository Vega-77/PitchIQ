"""The step that had no way to be typed.

`cv/publish.py` has been guarded and tested since the pipeline started
producing reports, and had no entry point: no `main`, no `__main__`, and no
caller anywhere outside `tests/`. Every piece worked and nothing called them,
which is a shape a unit test cannot see by construction — the audit that found
it walked the call graph, not the coverage.

So these tests are about the wrapper and only the wrapper: does it read the two
file shapes a mapping actually arrives in, does it refuse the ways a person
mistypes an argument, and does `--dry-run` write nothing. What gets written and
what gets refused is `tests/test_publish.py`'s job, and duplicating it here
would mean two files to change the day a rule does.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_publish_report_cli.py -q
"""

from __future__ import annotations

import json

import pytest

from cv.experiments.publish_report import (
    DryRunClient,
    build_parser,
    describe,
    describe_writes,
    load_mapping,
    main,
)


def a_report(**extra) -> dict:
    """Small, and the same shape `tests/test_publish.py` publishes."""
    return {**{
        'schema_version': 12,
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
        'clusters': [{'cluster_id': 0, 'track_ids': [1, 2]}],
        'tracks': [{
            'cluster_id': 0, 'team': 'team_a', 'touches': 9,
            'passes_attempted': 5, 'passes_completed': 4, 'pass_accuracy': 0.8,
            'carries': 2, 'tackles': 1, 'interceptions': 0, 'recoveries': 1,
            'shots': None, 'xg': None, 'distance_m': None,
            'top_speed_kmh': None, 'sprint_count': None, 'minutes_tracked': 12.0,
        }],
        'events': [],
    }, **extra}


@pytest.fixture
def report_path(tmp_path):
    path = tmp_path / 'report.json'
    path.write_text(json.dumps(a_report()), encoding='utf-8')
    return path


# ------------------------------------------------------------------ mapping


class TestTheMappingFile:
    """Two shapes, because the file arrives two ways.

    Exported from Firestore it is the `cvMapping/current` document, with the
    map nested under `byCluster` beside the audit fields. Typed by hand it is
    the map itself. A conversion step between them would be one more thing to
    get wrong at the side of a field.
    """

    def test_the_firestore_document_shape_is_unwrapped(self, tmp_path):
        path = tmp_path / 'm.json'
        path.write_text(json.dumps({
            'byCluster': {'0': 'playerA', '1': 'playerB'},
            'updatedAt': '2026-08-18T00:00:00Z',
            'updatedBy': 'coachUid',
        }), encoding='utf-8')
        assert load_mapping(path) == {'0': 'playerA', '1': 'playerB'}

    def test_a_bare_map_is_taken_as_is(self, tmp_path):
        path = tmp_path / 'm.json'
        path.write_text(json.dumps({'0': 'playerA'}), encoding='utf-8')
        assert load_mapping(path) == {'0': 'playerA'}

    def test_numeric_cluster_ids_are_stringified(self, tmp_path):
        """`publish` looks clusters up by `str(cluster_id)`, so a hand-typed
        `{0: "playerA"}` would silently match nothing."""
        path = tmp_path / 'm.json'
        path.write_text('{"0": "playerA", "1": "playerB"}', encoding='utf-8')
        assert set(load_mapping(path)) == {'0', '1'}

    def test_a_list_is_refused_rather_than_iterated(self, tmp_path):
        path = tmp_path / 'm.json'
        path.write_text('[["0", "playerA"]]', encoding='utf-8')
        with pytest.raises(ValueError, match='expected an object'):
            load_mapping(path)

    def test_a_bycluster_that_is_not_a_map_is_refused(self, tmp_path):
        path = tmp_path / 'm.json'
        path.write_text('{"byCluster": ["playerA"]}', encoding='utf-8')
        with pytest.raises(ValueError, match='byCluster'):
            load_mapping(path)


# ------------------------------------------------------------------ arguments


class TestArguments:
    def test_team_and_match_are_required(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args(['report.json'])

    def test_a_missing_report_exits_two_not_one(self, tmp_path, capsys):
        """Two, so a script can tell "you typed the path wrong" apart from
        "the publish was refused"."""
        code = main([str(tmp_path / 'nope.json'), '--team', 't', '--match', 'm'])
        assert code == 2
        assert 'no such report' in capsys.readouterr().err

    def test_a_missing_mapping_exits_two(self, report_path, tmp_path, capsys):
        code = main([
            str(report_path), '--team', 't', '--match', 'm',
            '--mapping', str(tmp_path / 'nope.json'), '--dry-run',
        ])
        assert code == 2
        assert 'no such mapping' in capsys.readouterr().err

    def test_a_report_that_is_not_json_says_so(self, tmp_path, capsys):
        path = tmp_path / 'report.json'
        path.write_text('not json at all', encoding='utf-8')
        code = main([str(path), '--team', 't', '--match', 'm', '--dry-run'])
        assert code == 2
        assert 'not valid JSON' in capsys.readouterr().err

    def test_a_bad_team_id_is_refused_with_the_reason(self, report_path, capsys):
        """`_check_path` is what stops a slash retargeting the write. The
        wrapper's job is to print the reason instead of a traceback."""
        code = main([
            str(report_path), '--team', 'a/b', '--match', 'm', '--dry-run',
        ])
        assert code == 1
        assert 'refused:' in capsys.readouterr().err


# ------------------------------------------------------------------ dry runs


class TestDryRun:
    def test_it_needs_no_credential(self, report_path, capsys, monkeypatch):
        """The whole point. `PITCHIQ_SA_KEY` unset must not stop a dry run —
        otherwise nobody can look at a payload before publishing it."""
        monkeypatch.delenv('PITCHIQ_SA_KEY', raising=False)
        code = main([str(report_path), '--team', 't', '--match', 'm', '--dry-run'])
        assert code == 0
        assert 'would publish to teams/t/matches/m' in capsys.readouterr().out

    def test_the_cvstats_documents_are_all_accounted_for(self, report_path):
        client = DryRunClient()
        from cv.publish import publish

        publish(json.loads(report_path.read_text(encoding='utf-8')),
                't', 'm', client=client)
        paths = [path for _, path, _ in client.writes]
        assert paths == [
            'teams/t/matches/m/cvStats/summary',
            'teams/t/matches/m/cvStats/identity',
            'teams/t/matches/m/cvStats/events',
            'teams/t/matches/m/cvStats/thumbs',
        ]

    def test_without_a_mapping_it_says_why_there_are_no_player_stats(
        self, report_path, capsys,
    ):
        main([str(report_path), '--team', 't', '--match', 'm', '--dry-run'])
        out = capsys.readouterr().out
        assert 'playerReports  0' in out
        assert 'mapping' in out

    def test_an_empty_mapping_is_reported_before_anything_is_written(
        self, report_path, tmp_path, capsys,
    ):
        path = tmp_path / 'm.json'
        path.write_text('{"byCluster": {}}', encoding='utf-8')
        code = main([
            str(report_path), '--team', 't', '--match', 'm',
            '--mapping', str(path), '--dry-run',
        ])
        assert code == 0
        assert 'confirms no clusters' in capsys.readouterr().out

    def test_a_mapped_cluster_reaches_a_player_report(
        self, report_path, tmp_path, capsys,
    ):
        path = tmp_path / 'm.json'
        path.write_text('{"0": "playerA"}', encoding='utf-8')
        code = main([
            str(report_path), '--team', 't', '--match', 'm',
            '--mapping', str(path), '--dry-run',
        ])
        assert code == 0
        assert 'playerReports  1' in capsys.readouterr().out

    def test_it_admits_it_could_not_check_the_reports_exist(
        self, report_path, tmp_path, capsys,
    ):
        """The one thing a dry run cannot know, said out loud.

        `publish` skips any player whose report a coach never published, and
        finds that out by reading the document. A dry run reads nothing, so its
        player count is an upper bound. Printing it without the caveat would
        promise writes that the real run correctly declines to make.
        """
        path = tmp_path / 'm.json'
        path.write_text('{"0": "playerA"}', encoding='utf-8')
        main([
            str(report_path), '--team', 't', '--match', 'm',
            '--mapping', str(path), '--dry-run',
        ])
        assert 'may' in capsys.readouterr().out.rsplit('assumed', 1)[-1]

    def test_verbose_lists_a_size_for_every_write(
        self, report_path, capsys,
    ):
        main([
            str(report_path), '--team', 't', '--match', 'm',
            '--dry-run', '--verbose',
        ])
        out = capsys.readouterr().out
        assert out.count('bytes)') == 4
        assert 'set    teams/t/matches/m/cvStats/summary' in out


# ------------------------------------------------------------------ printing


class TestWhatItPrints:
    def test_the_caveat_is_absent_when_no_player_report_was_written(self):
        lines = describe(
            {'summary': True, 'identity': True, 'thumbs': 0, 'events': 0,
             'playerReports': 0, 'skipped': []},
            dry_run=True,
        )
        assert not any('assumed' in line for line in lines)

    def test_a_real_run_never_carries_the_dry_run_caveat(self):
        lines = describe(
            {'summary': True, 'identity': True, 'thumbs': 2, 'events': 30,
             'playerReports': 4, 'skipped': []},
            dry_run=False,
        )
        assert not any('assumed' in line for line in lines)

    def test_every_skip_reason_survives_to_the_output(self):
        lines = describe(
            {'summary': True, 'identity': True, 'thumbs': 0, 'events': 0,
             'playerReports': 0,
             'skipped': ['cluster 4: no stats for it', 'player p: no report']},
            dry_run=False,
        )
        assert sum('skipped:' in line for line in lines) == 2

    def test_sizes_are_measured_on_the_payload_not_the_path(self):
        client = DryRunClient()
        client.collection('a').document('b').set({'k': 'v' * 100})
        assert '(109 bytes)' in describe_writes(client)[0]
