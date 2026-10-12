from types import SimpleNamespace

import numpy as np

from cv import topdown as TD
from cv.linking import PLAYER

S = TD.MapSettings()
FPS = int(S.fps)


def track(fs, xy, sd=0.3):
    fs = np.asarray(fs)
    return fs, np.asarray(xy, dtype=float), np.broadcast_to(np.eye(2) * sd ** 2, (len(fs), 2, 2)).copy()


def run(T, *tracks, drift=None, s=S):
    z, R, mask = TD._timeline(list(tracks), 0, T)
    return TD.smooth(z, R, mask, s, drift)


def test_smoother_bridges_a_gap_along_the_run():
    T = 5 * FPS
    t = np.arange(T) / FPS
    line = np.c_[10 + 2.0 * t, 20 + 0 * t]
    seen = (t < 2) | (t >= 3)
    xs, Ps = run(T, track(np.flatnonzero(seen), line[seen]))
    gap = ~seen
    assert np.abs(xs[0, gap, :2] - line[gap]).max() < 0.5
    # less sure in the middle of the gap than where seen
    assert Ps[0, gap, 0, 0].max() > 2 * Ps[0, seen, 0, 0].max()


def test_unseen_player_glides_to_a_stop():
    T = 8 * FPS
    t = np.arange(T) / FPS
    line = np.c_[10 + 3.0 * t, 20 + 0 * t]
    seen = t < 2
    xs, _ = run(T, track(np.flatnonzero(seen), line[seen]))
    v = np.hypot(*xs[0, :, 2:].T)
    assert v[2 * FPS] > 2.0
    assert v[-1] < 0.3
    # it went on a little past where it was last seen, not across the pitch
    assert 1.0 < xs[0, -1, 0] - line[2 * FPS - 1, 0] < 6.0


def test_unseen_player_moves_with_the_team():
    T = 8 * FPS
    seen = np.arange(2 * FPS)
    drift = np.zeros((1, T, 2)); drift[0, :, 0] = 2.0
    still, _ = run(T, track(seen, np.tile([30.0, 20.0], (len(seen), 1))))
    moved, _ = run(T, track(seen, np.tile([30.0, 20.0], (len(seen), 1))), drift=drift)
    assert abs(still[0, -1, 0] - 30.0) < 0.5
    # six unseen seconds at the team's 2 m/s, less the second it takes to pick up speed
    assert 9.0 < moved[0, -1, 0] - 30.0 < 13.0


def test_one_wild_foot_is_not_followed():
    T = 3 * FPS
    xy = np.tile([30.0, 20.0], (T, 1))
    xy[FPS] = [36.0, 20.0]
    xs, _ = run(T, track(np.arange(T), xy, sd=0.2))
    assert abs(xs[0, FPS, 0] - 30.0) < 0.5


def visit(pid, first, last, start, end, k=0, group=('team_a', PLAYER)):
    return TD._Visit(pid, k, group, first, last, (last - first + 1) / FPS,
                     np.r_[start, 0.0, 0.0], np.r_[end, 0.0, 0.0])


def test_chain_joins_a_player_who_leaves_and_comes_back():
    lo, hi = 0, 30 * FPS
    polys = [None] * (hi - lo)
    a = visit(1, 0, 10 * FPS, [20, 30], [20, 30])
    b = visit(2, 15 * FPS, hi - 1, [24, 32], [24, 32])
    slots, left = TD.chain([a, b], polys, lo, hi, 1, S)
    assert [[v.pid for v in r] for r in slots] == [[1, 2]] and not left


def test_chain_keeps_two_people_seen_together_apart():
    lo, hi = 0, 30 * FPS
    polys = [None] * (hi - lo)
    a = visit(1, 0, hi - 1, [20, 30], [20, 30])
    b = visit(2, 0, hi - 1, [60, 30], [60, 30])
    slots, left = TD.chain([a, b], polys, lo, hi, 2, S)
    assert sorted(v.pid for r in slots for v in r) == [1, 2] and all(len(r) == 1 for r in slots)
    # with one slot only, one of them is left over
    slots, left = TD.chain([a, b], polys, lo, hi, 1, S)
    assert len(slots) == 1 and len(left) == 1


def test_chain_prefers_the_return_that_fits():
    lo, hi = 0, 30 * FPS
    polys = [None] * (hi - lo)
    a = visit(1, 0, 10 * FPS, [20, 30], [20, 30])
    near = visit(2, 14 * FPS, hi - 1, [22, 31], [22, 31])
    far = visit(3, 14 * FPS, hi - 1, [50, 60], [50, 60])
    slots, _ = TD.chain([a, near, far], polys, lo, hi, 2, S)
    runs = [[v.pid for v in r] for r in slots]
    assert [1, 2] in runs and [3] in runs


def test_chain_will_not_walk_a_guess_through_the_picture():
    # the straight path from a's exit to b's return crosses ground the camera showed the whole time
    lo, hi = 0, 30 * FPS
    seen_ground = np.array([[25.0, 20.0], [45.0, 20.0], [45.0, 40.0], [25.0, 40.0]])
    a = visit(1, 0, 10 * FPS, [20, 30], [20, 30])
    b = visit(2, 20 * FPS, hi - 1, [50, 30], [50, 30])
    s = TD.MapSettings(view_cost=5.0)
    walls = TD.chain([a, b], [seen_ground] * (hi - lo), lo, hi, 2, s)[0]
    free = TD.chain([a, b], [None] * (hi - lo), lo, hi, 2, s)[0]
    assert len(free) == 1 and len(walls) == 2


def ident(pid, team='team_a', role=PLAYER):
    return SimpleNamespace(pid=pid, team=team, role=role)


def test_a_blink_is_one_visit_unless_someone_was_seen_there():
    T = 4 * FPS
    fs = np.r_[np.arange(0, 60), np.arange(70, 120)]
    xs = np.zeros((T, 4)); xs[:, :2] = [30.0, 20.0]
    assert len(TD.visits(ident(1), fs, xs, 0, S)) == 1
    others = {f: [(2, ('team_a', PLAYER), np.array([30.5, 20.0]))] for f in range(60, 70)}
    assert len(TD.visits(ident(1), fs, xs, 0, S, others)) == 2
    # someone of the other team standing there is no reason to split
    others = {f: [(3, ('team_b', PLAYER), np.array([30.5, 20.0]))] for f in range(60, 70)}
    assert len(TD.visits(ident(1), fs, xs, 0, S, others)) == 1
    # a long gap always splits
    fs = np.r_[np.arange(0, 30), np.arange(80, 120)]
    assert len(TD.visits(ident(1), fs, xs, 0, S)) == 2


def ghosts(T, seen_until, view=None, other=None):
    """One track seen for its first frames, then a guess standing at (30, 20)."""
    N = 1 + (other is not None)
    xs = np.zeros((N, T, 4)); xs[0, :, :2] = [30.0, 20.0]
    Ps = np.broadcast_to(np.diag([0.5, 0.5, 1.0, 1.0]), (N, T, 4, 4)).copy()
    owner = np.full((N, T), -1); owner[0, :seen_until] = 7
    if other is not None:
        xs[1, :, :2] = other; owner[1] = 8
    mask = owner >= 0
    groups = [('team_a', PLAYER)] * N
    return TD._ghost_alpha(xs, Ps, owner, mask, np.ones((N, T)), groups, [view] * T, S)[0]


def test_guess_is_drawn_while_out_of_view():
    a = ghosts(4 * FPS, FPS)
    assert (a[:FPS] == 0).all()                  # seen: drawn as seen, not as a guess
    assert a[2 * FPS:].min() > 0.3


def test_guess_fades_where_the_picture_shows_nobody():
    view = np.array([[0.0, 0.0], [60.0, 0.0], [60.0, 40.0], [0.0, 40.0]])
    a = ghosts(4 * FPS, FPS, view=view)
    assert a[FPS + 5] > 0.2                       # just gone: the detector may only have blinked
    assert (a[3 * FPS:] == 0).all()               # a second and more with the spot in view and empty


def test_guess_fades_under_a_teammate_seen_on_it():
    a = ghosts(4 * FPS, FPS, other=[30.5, 20.0])
    assert (a[2 * FPS:] == 0).all()
    a = ghosts(4 * FPS, FPS, other=[40.0, 20.0])
    assert a[2 * FPS:].min() > 0.3


def test_ghost_alpha_fades_with_doubt():
    a = TD.ghost_alpha(np.array([0.0, 2.0, 8.0, 30.0]))
    assert (np.diff(a) <= 0).all() and a[0] <= 0.9 and a[-1] >= 0.4


def test_team_velocity_is_the_mean_of_seen_teammates():
    T = 2 * FPS
    xs = np.zeros((3, T, 4))
    xs[0, :, 2] = 2.0; xs[1, :, 2] = 4.0; xs[2, :, 2] = -5.0
    mask = np.ones((3, T), dtype=bool); mask[1, :FPS] = False
    groups = [('team_a', PLAYER), ('team_a', PLAYER), ('team_b', PLAYER)]
    v = TD._team_velocity(xs, mask, groups, S)
    a = v[('team_a', PLAYER)][:, 0] / S.team_drift
    assert abs(a[5] - 2.0) < 1e-6 and abs(a[-5] - 3.0) < 1e-6
    assert abs(v[('team_b', PLAYER)][T // 2, 0] / S.team_drift + 5.0) < 1e-6
    # the referee goes with everyone
    assert ('', TD.REFEREE) in v



def test_a_slot_hands_over_without_drawing_two_guesses():
    T = 200
    old = TD.handover_share(0, 59, T, None, 120)       # seen 0-59, the slot's next segment starts at 120
    new = TD.handover_share(120, 199, T, 59, None)
    assert (old[:60] == 1).all() and (new[120:] == 1).all()
    both = (old > 0.02) & (new > 0.02)
    assert not both.any()                               # never two dots for one slot
    assert old[60] > 0.9 and new[119] > 0.9             # each guess starts strong where it was last seen
    assert (np.diff(old[59:]) <= 0).all() and (np.diff(new[:121]) >= 0).all()


def test_a_slot_with_one_segment_guesses_to_both_edges():
    assert (TD.handover_share(50, 80, 120, None, None) == 1).all()


def _dots_for_cap(n_boxed, n_held, ghost_alphas, frame=5):
    boxed = SimpleNamespace(frames=np.array([frame]))
    held = SimpleNamespace(frames=np.array([frame - 3]))       # its box blinked out
    g = ('team_a', PLAYER)
    return ([TD.Dot(1.0 * k, 0, True, 1.0, g, boxed, k) for k in range(n_boxed)]
            + [TD.Dot(50.0 + k, 0, True, 1.0, g, held, -1) for k in range(n_held)]
            + [TD.Dot(80.0, 0, False, a, g, None, 20 + k) for k, a in enumerate(ghost_alphas)])


def test_the_map_never_draws_more_than_ten_of_a_team():
    res = SimpleNamespace(team_at=lambda ident, f: 'team_a')
    out = TD.cap_teams(_dots_for_cap(9, 2, [0.3, 0.8]), res, 5, 10)
    drawn = [d for d in out if d.drawn_team == 'team_a']
    assert len(drawn) == 10
    # the faintest guesses go first, then a held dot is greyed; boxes stay
    assert [d.alpha for d in out if not d.seen] == []
    assert sum(d.team == 'unknown' for d in out) == 1
    assert all(d.drawn_team == 'team_a' for d in out if d.seen and d.ident.frames[0] == 5)


def test_the_cap_leaves_a_legal_team_alone():
    res = SimpleNamespace(team_at=lambda ident, f: 'team_a')
    out = TD.cap_teams(_dots_for_cap(8, 1, [0.5]), res, 5, 10)
    assert len(out) == 10 and all(d.drawn_team == 'team_a' for d in out)


def test_a_box_the_linker_greyed_stays_grey():
    res = SimpleNamespace(team_at=lambda ident, f: 'unknown')
    out = TD.cap_teams(_dots_for_cap(3, 0, []), res, 5, 10)
    assert all(d.drawn_team == 'unknown' for d in out)
