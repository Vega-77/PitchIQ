import numpy as np

from cv import checks as C
from cv.topdown import TopDown

L, W, FPS = 105.0, 68.0, 30.0


def td(paths, groups, seen=None):
    """A TopDown with only the per-track arrays the checks read."""
    p = np.asarray(paths, float)
    owner = np.where(np.ones(p.shape[:2], bool) if seen is None else seen, 0, -1)
    return TopDown(0, p.shape[1], [], path=p, owner=owner, alpha=np.zeros(p.shape[:2]), group=list(groups))


def walk(x0, y0, vx, T, vy=0.0):
    t = np.arange(T) / FPS
    return np.c_[x0 + vx * t, y0 + vy * t]


def test_feet_on_pitch_counts_feet_inside_the_lines():
    H = np.diag([0.1, 0.1, 1.0])                         # 1 px = 0.1 StatsBomb units
    feet = [np.array([[100, 100], [1100, 700], [1300, 100]])]   # (10, 10) in; (110, 70) in; (130, 10) out
    r = C.feet_on_pitch(H[None], feet)
    assert r['feet'] == 3 and abs(r['on_pitch'] - 2 / 3) < 1e-9


def test_feet_on_pitch_ignores_the_homographys_sign():
    H = np.diag([0.1, 0.1, 1.0])
    feet = [np.array([[100, 100], [1100, 700], [1300, 100]])]
    assert C.feet_on_pitch(-H[None], feet) == C.feet_on_pitch(H[None], feet)


def test_speeds_flags_a_jump_but_not_a_sprint():
    T = 90
    sprint = walk(10, 10, 8.0, T)                         # 8 m/s: fast, human
    jumpy = walk(50, 30, 1.0, T)
    jumpy[45:] += [5.0, 0.0]                              # 5 m in one frame
    r = C.speeds(td([sprint, jumpy], [(0, 'player'), (1, 'player')]), FPS)
    assert r['jumps'] == 1
    assert r['worst_ms'] > 100
    assert 0 < r['fast'] < 0.01


def test_speeds_skips_frames_the_map_does_not_draw():
    T = 60
    a = walk(10, 10, 1.0, T)
    a[30:] += [20.0, 0.0]                                 # moved 20 m while not drawn
    seen = np.ones((1, T), bool); seen[0, 25:35] = False
    a[25:35] = np.nan
    r = C.speeds(td([a], [(0, 'player')], seen), FPS)
    assert r['jumps'] == 0 and r['fast'] == 0


def test_crowding_counts_frames_with_twelve_of_a_team():
    T = 30
    paths = [walk(5 + 7 * i, 30, 0.0, T) for i in range(12)]
    groups = [(0, 'player')] * 11 + [(0, 'keeper')]
    seen = np.ones((12, T), bool); seen[11, :20] = False  # the twelfth is drawn for 10 frames
    r = C.crowding(td(paths, groups, seen), L, W)
    assert r['over11'] == 10 and r['most'] == {0: 12}


def test_crowding_ignores_people_off_the_pitch_and_referees():
    T = 10
    paths = [walk(5 + 7 * i, 30, 0.0, T) for i in range(11)] + [walk(50, -10, 0.0, T), walk(60, 30, 0.0, T)]
    groups = [(0, 'player')] * 12 + [(None, 'referee')]
    assert C.crowding(td(paths, groups), L, W)['over11'] == 0


def test_still_outside_finds_a_sub_on_the_bench_not_a_winger_on_the_line():
    T = 90
    bench = walk(50, -6, 0.0, T)                          # 6 m outside, standing
    winger = walk(40, 0.5, 4.0, T)                        # on the line, running
    out_running = walk(40, -5, 4.0, T)                    # outside but moving: chasing a ball out
    r = C.still_outside(td([bench, winger, out_running], [(0, 'player')] * 3), L, W, FPS)
    assert r['tracks'] == [0]
