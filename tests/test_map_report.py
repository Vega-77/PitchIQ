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
    assert r['schema_version'] == SCHEMA_VERSION == 16
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
    assert r['schema_version'] == 16 and pipeline['schema_version'] == 15
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
