"""stats_noise: each kind of wrong input does what it says to the map table."""

import numpy as np

from pitchiq import map_stats as M
from pitchiq.experiments import stats_noise as N
from test_map_stats import _table  # tests/ is on the path (no __init__)


def _outfield(t, team):
    return (t.role == M.OUTFIELD) & (t.team == team)


def test_drop_and_flip_and_stray():
    t = _table()
    rng = np.random.default_rng(0)
    d = N.perturb(t, 'drop1', rng)
    for team in M.TEAMS:
        assert _outfield(d, team).sum() == _outfield(t, team).sum() - 3  # one a frame, 3 frames
    f = N.perturb(t, 'flip1', rng)
    assert (f.team != t.team).sum() == 3  # one outfielder a frame
    for kind in ('stray1', 'edge1'):
        s = N.perturb(t, kind, rng)
        assert len(s.f) == len(t.f) + 6
    e = N.perturb(t, 'edge1', rng)
    new = e.trk == -1
    assert ((e.y[new] <= 0) | (e.y[new] >= t.width_m)).all()


def test_territory_values_and_heatmap_distance():
    t = _table()
    left = {0: True, 1: False}
    v = N._terr_values(t, left, t)
    assert v['play_att'] == 0 and v['play_def'] == 0  # centre of play in the middle third
    assert v[0]['def'] == 40 and v[0]['att'] == 0
    # a flip moves one dot a frame to the other team: no change to the centre of play
    f = N._terr_values(N.perturb(t, 'flip1', np.random.default_rng(0)), left, t)
    assert N._terr_diff(f, v)['play_att'] == 0
    a, b = np.zeros((6, 5)), np.zeros((6, 5))
    a[0, 0], b[0, 0], b[5, 4] = 1.0, 0.75, 0.25
    assert N._tv(a, b) == 25.0  # a quarter of the time is in another zone


def test_cal_and_jitter_move_positions_only():
    t = _table()
    c = N.perturb(t, 'cal2', np.random.default_rng(0))
    assert np.allclose(c.x - t.length_m / 2, (t.x - t.length_m / 2) * 1.02)
    j = N.perturb(t, 'jitter1', np.random.default_rng(0))
    assert (j.team == t.team).all() and not np.allclose(j.x, t.x)
