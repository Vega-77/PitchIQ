"""The players-and-ball table (pitchiq/map_table.py) on a synthetic half."""

import numpy as np
import pytest

from pitchiq import map_table
from pitchiq.map_table import PLAYER_COLS, MapTable


def _half(tmp_path, ball_x=None, ball_abs=None):
    """A 3-frame map with two dots a frame and a 5-frame v17 ball, pitch 110 x 60 m."""
    mp = tmp_path / 'map' / 'zz1.npz'
    mp.parent.mkdir()
    f = np.array([100, 100, 101, 101, 102, 102])
    cols = {
        'f': f,
        'trk': np.array([1, 2, 1, 2, 1, 2]),
        'x': np.array([10.0, 50, 11, 51, 12, 52]),
        'y': np.array([5.0, 30, 5, 30, 5, 30]),
        'seen': np.array([1, 1, 1, 0, 1, 1], bool),
        'alpha': np.ones(6),
        'team': np.array([0, 1, 0, 1, 0, 1]),
        'role': np.zeros(6, int),
        'slot': np.full(6, -1),
    }
    np.savez(mp, fps=30.0, length_m=110.0, width_m=60.0, **cols)
    track = tmp_path / 'balltrack'
    (track / 'h3d_v17').mkdir(parents=True)
    (track / 'evaluation' / 'p2d' / 'v17').mkdir(parents=True)
    a = np.arange(99, 104) if ball_abs is None else ball_abs
    x = np.array([60.0, 12.0, np.nan, 24.0, 0.0]) if ball_x is None else ball_x
    np.savez(
        track / 'h3d_v17' / 'zz1_ball3dd.npz',
        abs=a,
        x=x,
        y=np.array([40.0, 8.0, 8.0, 16.0, 0.0]),
        z=np.zeros(5),
        state=np.array(['ground', 'ground', 'none', 'ground', 'none']),
    )
    np.savez(
        track / 'evaluation' / 'p2d' / 'v17' / 'zz1.npz',
        abs=a,
        x=np.zeros(5),
        y=np.zeros(5),
        src=np.array([1, 2, 1, 3, 0]),
        ring=np.zeros(5),
    )
    return mp, track


def test_the_table_joins_map_and_ball_in_metres(tmp_path):
    mp, track = _half(tmp_path)
    t = MapTable.load('zz1', mp, track)
    assert (t.fps, t.length_m, t.width_m, t.ball_f0) == (30.0, 110.0, 60.0, 99)
    assert t.frames == range(100, 103)
    assert set(t.players) == set(PLAYER_COLS)
    rows = t.players_at(101)
    assert rows['trk'].tolist() == [1, 2] and rows['seen'].tolist() == [True, False]
    assert len(t.players_at(99)['x']) == 0
    # StatsBomb 120 x 80 -> metres on a 110 x 60 pitch
    assert t.ball_at(99) == pytest.approx((55.0, 30.0, 1))
    assert t.ball_at(100) == pytest.approx((11.0, 6.0, 2))
    x, y, src = t.ball_at(103)  # no ball: src 0 blanks the position whatever the file holds
    assert np.isnan(x) and np.isnan(y) and src == 0
    x, _, src = t.ball_at(500)  # outside the ball arrays
    assert np.isnan(x) and src == 0


def test_seen_ball_is_a_placed_pick(tmp_path):
    """Bridges, frames with no ball and rings the 3D chain could not place are not seen."""
    t = MapTable.load('zz1', *_half(tmp_path))
    assert t.ball_seen().tolist() == [True, True, False, False, False]
    x, _, src = t.ball_at(101)  # a pick with no 3D position keeps its source, NaN metres
    assert np.isnan(x) and src == 1


def test_the_table_names_what_makes_a_missing_file(tmp_path):
    mp, track = _half(tmp_path)
    (track / 'h3d_v17' / 'zz1_ball3dd.npz').unlink()
    with pytest.raises(FileNotFoundError, match='ball17'):
        MapTable.load('zz1', mp, track)
    with pytest.raises(FileNotFoundError, match='wholemap'):
        MapTable.load('zz1', tmp_path / 'nothing.npz', track)


def test_the_table_refuses_a_ball_that_skips_frames(tmp_path):
    mp, track = _half(tmp_path, ball_abs=np.array([99, 100, 101, 103, 104]))
    with pytest.raises(ValueError, match='skips'):
        MapTable.load('zz1', mp, track)


def test_default_paths_point_at_the_stats_map_and_the_ball_tree():
    assert map_table.MAPS.parts[-3:] == ('PitchIQ-data', 'stats', 'map')
    assert map_table.TRACK.parts[-2:] == ('pitchiq', 'balltrack')
