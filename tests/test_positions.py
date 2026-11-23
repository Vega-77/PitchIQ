"""positions: stats per position, the position named by where a player stands in the team."""

import json

import numpy as np
import pytest

from pitchiq import map_stats as M
from pitchiq import positions as P
from pitchiq.experiments.cached_report import id_shifts
from pitchiq.metrics import PositionSeries, clean_track, track_movement

FPS = 30.0
L, W = 105.0, 68.0
# A 4-4-2 for the team defending x = 0, its left wing at y = 0 (map_stats._attack_right).
SHAPE = [
    (25, 10, 'back_left'),
    (25, 25, 'back_centre'),
    (25, 45, 'back_centre'),
    (25, 60, 'back_right'),
    (45, 10, 'middle_left'),
    (45, 25, 'middle_centre'),
    (45, 45, 'middle_centre'),
    (45, 60, 'middle_right'),
    (65, 28, 'front_centre'),
    (65, 42, 'front_centre'),
]


def _table(seconds=30, frames=None, drift=0.0):
    """Team 0 in SHAPE, track ids 0..9; team 1 the same shape turned to defend x = L,
    track ids 10..19. A keeper each, a referee and a grey dot, every frame.
    `drift` moves team 0's first striker (track 8) up the pitch, metres a second."""
    frames = np.arange(int(seconds * FPS)) if frames is None else np.asarray(frames)
    rows = []
    for f in frames.tolist():
        for k, (x, y, _) in enumerate(SHAPE):
            x0 = x + (drift * f / FPS if k == 8 else 0.0)
            rows.append((f, k, x0, y, True, 1.0, 0, M.OUTFIELD))
            rows.append((f, 10 + k, L - x, W - y, True, 1.0, 1, M.OUTFIELD))
        rows.append((f, 50, 3.0, 34.0, True, 1.0, 0, M.KEEPER))
        rows.append((f, 51, L - 3.0, 34.0, True, 1.0, 1, M.KEEPER))
        rows.append((f, 60, 50.0, 30.0, True, 1.0, 2, 2))  # referee
        rows.append((f, 61, 52.0, 1.0, True, 1.0, 2, M.OUTFIELD))  # grey: no team
    c = list(zip(*rows))
    return M.MapTable(
        f=np.array(c[0]),
        trk=np.array(c[1]),
        x=np.array(c[2], float),
        y=np.array(c[3], float),
        seen=np.array(c[4]),
        alpha=np.array(c[5]),
        team=np.array(c[6]),
        role=np.array(c[7]),
        fps=FPS,
        length_m=L,
        width_m=W,
    )


def _sid(trk, team):
    return trk * P.TEAMS_PER_TRACK + team


def _roles_of(placed):
    """Each sid's roles, as names, over the half."""
    out = {}
    for sid, r in zip(placed.sid.tolist(), placed.role.tolist()):
        out.setdefault(sid, set()).add(P.ROLES[r])
    return out


def _nested(x, inside=False):
    if isinstance(x, list):
        return inside or any(_nested(v, True) for v in x)
    if isinstance(x, dict):
        return any(_nested(v, False) for v in x.values())
    return False


def test_nine_roles():
    assert len(P.ROLES) == 9 and P.ROLES[0] == 'back_left' and P.ROLES[-1] == 'front_right'


def test_place_names_a_442_for_both_teams():
    t = _table()
    placed = P.place(t)
    roles = _roles_of(placed)
    for k, (_, _, want) in enumerate(SHAPE):
        assert roles[_sid(k, 0)] == {want}, k
        # Team 1 defends the other goal: turned half a turn, its left back stands at y = 58.
        assert roles[_sid(10 + k, 1)] == {want}, k
    # Every half second, ten outfielders a side; nobody else.
    assert len(placed) == 60 * 20
    assert set(placed.team.tolist()) == {0, 1}
    assert len(placed.shaped[0]) == len(placed.shaped[1]) == 60


def test_keepers_referees_and_greys_get_no_role():
    placed = P.place(_table())
    assert not set(placed.sid.tolist()) & {_sid(50, 0), _sid(51, 1), _sid(60, 2), _sid(61, 2)}


def test_too_few_outfielders_is_no_shape():
    t = _table()
    short = t.subset(~((t.team == 1) & (t.trk >= 10 + P.MIN_TEAM - 1)))  # team 1 down to 6
    placed = P.place(short, left={0: True, 1: False})
    assert set(placed.team.tolist()) == {0}
    assert len(placed.shaped[1]) == 0 and len(placed.shaped[0]) == 60


def test_a_moment_forward_is_still_a_back():
    t = _table()
    x = t.x.copy()
    step = (t.trk == 0) & (t.f >= 300) & (t.f < 315)  # one sampled instant, at x = 45
    x[step] = 45.0
    t.x = x
    held = _roles_of(P.place(t))[_sid(0, 0)]
    assert held == {'back_left'}
    # With no window the same instant is midfield.
    assert _roles_of(P.place(t, window_s=0.0))[_sid(0, 0)] == {'back_left', 'middle_left'}


def test_bad_split_window_gives_no_roles():
    t = _table()
    t.windows = np.array([[0, 450], [450, 900]])
    seen = t.seen.copy()
    seen[(t.team == 1) & (t.f < 450)] = False  # team 1 unseen there: the split failed
    t.seen = seen
    placed = P.place(t, left={0: True, 1: False})
    assert placed.f.min() >= 450
    assert len(P.place(t, left={0: True, 1: False}, drop_bad_split=False)) == 60 * 20


def test_lookup_is_the_nearest_instant_within_a_step():
    placed = P.place(_table())
    look = placed.lookup()
    back_left = P.ROLES.index('back_left')
    assert look.at(_sid(0, 0), [0, 7, 22, 885]).tolist() == [back_left] * 4
    assert look.at(_sid(0, 0), [885 + 16]).tolist() == [P.NO_ROLE]  # past the last by > a step
    assert look.at(999, [0]).tolist() == [P.NO_ROLE]
    assert look.team_of(_sid(12, 1)) == 1 and look.team_of(999) is None


def test_distance_by_role_adds_up_to_the_stats_distance():
    t = _table(drift=0.2)  # the striker walks 6 m up the pitch
    placed = P.place(t)
    dist = P.distance_by_role(t, placed)
    sid = _sid(8, 0)
    m = (t.trk == 8) & (t.team == 0)
    series = PositionSeries(sid, t.f[m] / FPS, np.stack([t.x[m], t.y[m]], 1))
    want = track_movement(clean_track(series), smooth=False)[0].distance_m
    assert want == pytest.approx(0.2 * (899 / FPS), rel=1e-6)
    assert dist[sid].sum() == pytest.approx(want, rel=1e-9)
    assert dist[sid][P.ROLES.index('front_centre')] == pytest.approx(want, rel=1e-9)
    assert dist[_sid(0, 0)].sum() == 0.0  # standing still


def _report():
    """A half report on this table's ids: clusters of tracks, events and touches."""
    clusters = [{'cluster_id': k, 'track_ids': [_sid(k, 0)]} for k in range(10)]
    clusters += [{'cluster_id': 10 + k, 'track_ids': [_sid(10 + k, 1)]} for k in range(10)]
    return {
        'clusters': clusters,
        'tracks': [
            {'cluster_id': 0, 'touch_times_s': [2.0, 5.0, 20.0]},
            {'cluster_id': 8, 'touch_times_s': [11.0]},
        ],
        'events': [
            {'type': 'pass', 'frame_index': 60, 'track_id': _sid(0, 0), 'outcome': 'completed'},
            {'type': 'pass', 'frame_index': 90, 'track_id': _sid(0, 0), 'outcome': 'failed'},
            {'type': 'carry', 'frame_index': 120, 'track_id': _sid(4, 0)},
            {'type': 'shot', 'frame_index': 330, 'track_id': _sid(8, 0), 'outcome': 'goal'},
            {'type': 'tackle', 'frame_index': 400, 'track_id': _sid(13, 1)},
            {'type': 'pass', 'frame_index': 400, 'track_id': None},  # nobody: not counted
            {'type': 'pass', 'frame_index': 400, 'track_id': _sid(50, 0)},  # keeper: no role
        ],
    }


def test_half_block_counts_each_role():
    t = _table()
    b = P.half_block(t, P.place(t), _report(), ours=0)
    a = b['team_a']['roles']
    assert b['team_a']['method'] == 'place' and b['team_a']['shaped_minutes'] == 0.5
    assert a['back_left']['passes_attempted'] == 2 and a['back_left']['passes_completed'] == 1
    assert a['back_left']['touches'] == 3
    assert a['middle_left']['carries'] == 1
    assert a['front_centre']['shots'] == 1 and a['front_centre']['goals'] == 1
    assert a['front_centre']['touches'] == 1
    assert b['team_b']['roles']['back_right']['defensive_actions'] == 1  # track 13: SHAPE[3]
    assert a['back_left']['minutes'] == 0.5 and a['back_centre']['minutes'] == 1.0
    assert a['back_left']['occupied_share'] == 1.0 and a['back_centre']['players_mean'] == 2.0
    assert a['front_left']['minutes'] == 0.0 and a['front_left']['heatmap'] is None
    heat = a['back_left']['heatmap']
    assert heat['cols'] * heat['rows'] == len(heat['values']) == len(heat['seen'])
    assert sum(heat['values']) == pytest.approx(1.0, abs=1e-3)
    total = sum(r['passes_attempted'] for r in a.values())
    assert total == 2  # the keeper's and the nobody's passes went nowhere


def test_ours_picks_team_a():
    t = _table()
    b = P.half_block(t, P.place(t), _report(), ours=1)
    assert b['team_b']['roles']['back_left']['passes_attempted'] == 2
    assert b['team_a']['roles']['back_right']['defensive_actions'] == 1
    with pytest.raises(ValueError):
        P.half_block(t, P.place(t), _report(), ours=2)


def test_holders_are_flat_for_our_side_only():
    t = _table()
    b = P.half_block(t, P.place(t), _report(), ours=0, cluster_shift=100, named={100, 108}, half=2)
    a = b['team_a']['roles']
    assert a['back_left']['holders'] == {
        'half': 2,
        'start_s': 0.0,
        'minute': [0],
        'cluster_id': [100],
        'seconds': [30.0],
    }
    assert a['back_left']['unnamed_minutes'] == 0.0
    # Clusters 101 and 102 were dropped from the identity: their minute is unnamed.
    assert a['back_centre']['holders']['cluster_id'] == []
    assert a['back_centre']['unnamed_minutes'] == 1.0
    assert all(r['holders'] is None for r in b['team_b']['roles'].values())
    assert not _nested(b)
    json.dumps(b, allow_nan=False)


def test_holders_by_minute_and_the_shortest_hold():
    # Cluster 5 for 20 s of minute 0 and 3 s of minute 1; cluster 7 for 6 s of minute 1.
    f0 = 900
    frames = np.r_[
        f0 + np.arange(40) * 15, f0 + 1800 + np.arange(6) * 15, f0 + 1900 + np.arange(12) * 15
    ]
    clusters = np.r_[[5] * 40, [5] * 6, [7] * 12]
    held, unnamed = P._holders(frames, clusters, f0, FPS, 0.5, half=1)
    assert held == {
        'half': 1,
        'start_s': 30.0,
        'minute': [0, 1],
        'cluster_id': [5, 7],
        'seconds': [20.0, 6.0],
    }
    assert unnamed == pytest.approx(3.0 / 60)
    none, unnamed = P._holders(frames[:4], np.full(4, -1), f0, FPS, 0.5, half=1)
    assert none['minute'] == [] and unnamed == pytest.approx(2.0 / 60)


def test_match_block_sums_the_halves():
    t = _table()
    placed = P.place(t)
    rep = _report()
    one = P.half_block(t, placed, rep, ours=0, named={0}, half=1)
    two = P.half_block(t, placed, rep, ours=0, cluster_shift=20, named={20}, half=2)
    m = P.match_block([one, two])
    a = m['team_a']['roles']['back_left']
    assert m['team_a']['shaped_minutes'] == 1.0
    assert a['minutes'] == 1.0 and a['passes_attempted'] == 4 and a['touches'] == 6
    assert a['occupied_share'] == 1.0
    assert [h['half'] for h in a['holders']] == [1, 2]
    assert [h['cluster_id'] for h in a['holders']] == [[0], [20]]
    assert m['team_b']['roles']['back_left']['holders'] is None
    assert m['team_a']['roles']['back_centre']['unnamed_minutes'] == 2.0
    assert (
        m['team_a']['roles']['back_left']['heatmap']
        == one['team_a']['roles']['back_left']['heatmap']
    )
    assert not _nested(m)
    with pytest.raises(ValueError):
        P.match_block([])


def test_id_shifts_are_merges():
    halves = [
        {
            'clusters': [
                {'cluster_id': 0, 'track_ids': [0, 3]},
                {'cluster_id': 4, 'track_ids': [12]},
            ]
        },
        {'clusters': [{'cluster_id': 1, 'track_ids': [2]}]},
        {'clusters': []},
    ]
    assert id_shifts(halves) == [(0, 0), (5, 13), (7, 16)]
