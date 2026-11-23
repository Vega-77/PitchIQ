"""A report from the top-down map: `report_export.map_report_json`, `attach_map`
and `python -m pitchiq map_report`.

Run:  .venv/Scripts/python.exe -m pytest tests/test_map_report.py -q
"""

import json

import numpy as np

from pitchiq import map_stats as M
from pitchiq.cli import map_report as cli
from pitchiq.report_export import (
    MAP_ONLY_WARNING,
    SCHEMA_VERSION,
    TeamStats,
    attach_map,
    map_report_json,
)
from test_map_stats import _table


def _block():
    return M.report_block(_table(), ours=0)


def test_map_report_carries_every_team_key_and_nulls_the_events():
    r = map_report_json(_block(), source='wb1', length_m=105.0, width_m=68.0, period=1)
    assert r['schema_version'] == SCHEMA_VERSION == 17
    assert r['warnings'] == [MAP_ONLY_WARNING] and r['trustworthy'] is False
    for team in ('team_a', 'team_b'):
        side = r['teams'][team]
        assert set(side) == set(TeamStats(team).to_json())
        assert side['team'] == team
        # the map has players, not events: not counted is null, never 0
        for key in ('touches', 'passes_attempted', 'tackles', 'possession_pct', 'xg'):
            assert side[key] is None, key
        assert side['field_position']['height_m'] == 32.0
    json.dumps(r, allow_nan=False)


def test_attach_map_puts_the_map_into_a_pipeline_report():
    pipeline = {
        'schema_version': 15,
        'teams': {
            'team_a': {'team': 'team_a', 'touches': 512, 'shape': {'width_m': 41.2}},
            'team_b': {'team': 'team_b', 'touches': 471},
        },
        'warnings': [],
    }
    r = attach_map(pipeline, _block())
    assert r['schema_version'] == 17 and pipeline['schema_version'] == 15
    assert r['teams']['team_a']['touches'] == 512
    assert r['teams']['team_a']['shape']['width_m'] is None  # the map's shape replaces it
    assert r['teams']['team_b']['field_position']['height_m'] == 32.0
    assert 'field_position' not in pipeline['teams']['team_a']  # the input is not changed


def _save(path, light_team: int):
    t = _table()
    kit = np.zeros((1, 2, 3))
    kit[0, light_team, 0] = 200.0
    kit[0, 1 - light_team, 0] = 60.0
    np.savez(
        path,
        **{k: getattr(t, k) for k in ('f', 'trk', 'x', 'y', 'seen', 'alpha', 'team', 'role')},
        fps=30.0,
        length_m=105.0,
        width_m=68.0,
        win_lo=np.array([0]),
        win_hi=np.array([60]),
        kit_lab=kit,
        swap=np.array([False]),
    )


def test_ours_by_shirt_shade(tmp_path):
    p = tmp_path / 'half.npz'
    _save(p, light_team=1)
    assert cli.which_team(p, 'lighter') == 1 and cli.which_team(p, 'darker') == 0
    assert cli.which_team(p, '0') == 0


def test_command_writes_a_report(tmp_path):
    p = tmp_path / 'half.npz'
    _save(p, light_team=1)
    out = tmp_path / 'report.json'
    assert cli.main([str(p), '--ours', 'darker', '--period', '2', '--out', str(out)]) == 0
    r = json.loads(out.read_text(encoding='utf-8'))
    assert r['period'] == 2 and r['source'] == 'half'
    assert r['teams']['team_a']['attacking_end'] == 'right'  # map team 0 defends x = 0
    assert r['window'] == {'start_s': 0.0, 'end_s': 1.0}


def test_command_refuses_a_missing_map(tmp_path, capsys):
    assert cli.main([str(tmp_path / 'nope.npz'), '--ours', '0']) == 2
    assert 'no such map' in capsys.readouterr().err


def test_command_joins_two_halves_into_one_match_report(tmp_path):
    h1, h2 = tmp_path / 'wb1.npz', tmp_path / 'wb2.npz'
    _save(h1, light_team=1)
    _save(h2, light_team=0)  # each half's map numbers its teams on its own
    out = tmp_path / 'report.json'
    assert cli.main([str(h1), str(h2), '--ours', 'darker', '--out', str(out)]) == 0
    r = json.loads(out.read_text(encoding='utf-8'))
    assert r['source'] == 'wb1+wb2' and r['period'] is None and r['period_source'] is None
    assert r['duration_s'] == 2.1  # frames 0..30 at 30 fps, twice
    assert r['window'] == {'start_s': 0.0, 'end_s': 1.0}
    assert r['teams']['team_a']['field_position']['height_m'] == 32.0
    # darker is map team 0 in the first half and team 1 in the second, which here
    # defends the other end: the ends "changed", so the match has no single end
    assert r['teams']['team_a']['attacking_end'] is None


def test_command_refuses_a_period_for_two_halves(tmp_path, capsys):
    p = tmp_path / 'half.npz'
    _save(p, light_team=1)
    assert cli.main([str(p), str(p), '--ours', '0', '--period', '1']) == 2
    assert 'one map' in capsys.readouterr().err


def _half_report(path, cluster_ids):
    rep = {
        'clusters': [{'cluster_id': c, 'track_ids': [3 * c]} for c in cluster_ids],
        'tracks': [],
        'events': [{'type': 'pass', 'frame_index': 15, 'track_id': 0, 'outcome': 'completed'}],
    }
    path.write_text(json.dumps(rep), encoding='utf-8')


def test_command_adds_stats_per_position_from_the_half_reports(tmp_path):
    h1, h2 = tmp_path / 'wb1.npz', tmp_path / 'wb2.npz'
    _save(h1, light_team=1)
    _save(h2, light_team=1)
    r1, r2 = tmp_path / 'pipe_wb1.json', tmp_path / 'pipe_wb2.json'
    _half_report(r1, [0, 1])
    _half_report(r2, [0, 1])
    into = tmp_path / 'pipe.json'
    # merge keeps first-half cluster 0 and second-half cluster 0, renumbered 2
    into.write_text(
        json.dumps(
            {
                'teams': {'team_a': {}, 'team_b': {}},
                'clusters': [{'cluster_id': 0}, {'cluster_id': 2}],
            }
        ),
        encoding='utf-8',
    )
    out = tmp_path / 'report.json'
    args = [str(h1), str(h2), '--ours', '0', '--into', str(into)]
    assert cli.main([*args, '--halves', str(r1), str(r2), '--out', str(out)]) == 0
    r = json.loads(out.read_text(encoding='utf-8'))
    pos = r['teams']['team_a']['positions']
    assert pos['method'] == 'place' and len(pos['roles']) == 9
    # track 0 of map team 0 is a back: its pass in each half lands there
    assert sum(row['passes_attempted'] for row in pos['roles'].values()) == 2
    held = [h for row in pos['roles'].values() for h in row['holders'] or []]
    assert {c for h in held for c in h['cluster_id']} <= {0, 2}
    assert r['teams']['team_b']['positions']['roles']['back_left']['holders'] is None
    # without --halves there is no positions block
    assert cli.main([*args, '--out', str(out)]) == 0
    assert 'positions' not in json.loads(out.read_text(encoding='utf-8'))['teams']['team_a']


def test_command_refuses_halves_that_do_not_match_the_maps(tmp_path, capsys):
    p = tmp_path / 'half.npz'
    _save(p, light_team=1)
    rep = tmp_path / 'pipe_half.json'
    _half_report(rep, [0])
    assert cli.main([str(p), '--ours', '0', '--halves', str(rep), str(rep)]) == 2
    assert 'one half report per map' in capsys.readouterr().err
    assert cli.main([str(p), '--ours', '0', '--halves', str(tmp_path / 'nope.json')]) == 2
    assert 'no such half report' in capsys.readouterr().err
