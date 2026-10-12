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


def _sb_per_px(mpp=0.05):
    """A straight-down camera: mpp metres per pixel on a 105 x 68 m pitch, as px -> StatsBomb."""
    return np.diag([mpp * 120 / L, mpp * 80 / W, 1.0])


def test_paint_on_lines_finds_paint_on_the_halfway_line_only():
    H = _sb_per_px()                                       # halfway line at x = 52.5 m = 1050 px
    on = np.c_[np.full(50, 1050.0), np.linspace(100, 1200, 50)]
    off = np.c_[np.full(50, 500.0), np.linspace(300, 1000, 50)]   # 25 m in, mid-half
    assert C.paint_on_lines(H[None], [on], L, W)['on_lines'] == 1.0
    assert C.paint_on_lines(H[None], [off], L, W)['on_lines'] == 0.0
    r = C.paint_on_lines(np.stack([H, -H]), [on, off], L, W)
    assert r['paint'] == 100 and abs(r['on_lines'] - 0.5) < 1e-9


def test_paint_behind_the_camera_is_off_the_lines():
    H = _sb_per_px()
    on = np.c_[np.full(10, 1050.0), np.linspace(100, 1200, 10)]
    H2 = H.copy(); H2[2] = [0, 0.001, -0.5]               # rows above v = 500 map behind
    r = C.paint_on_lines(H2[None], [on], L, W)
    assert r['paint'] == 10 and r['on_lines'] < 1.0


def test_person_heights_on_a_straight_down_camera():
    H = _sb_per_px(0.05)                                  # a 36 px box spans 1.8 m of ground
    b = np.array([[100, 100, 112, 136], [800, 600, 812, 636]], float)
    assert np.allclose(C.person_heights(H, b, L, W), 1.8)


def test_far_near_is_one_without_perspective_and_moves_with_a_wrong_depth():
    rng = np.random.default_rng(0)
    v = rng.uniform(100, 1300, 200)
    b = np.c_[np.full(200, 500.0), v - 36, np.full(200, 512.0), v]
    r = C.far_near(_sb_per_px()[None], [b], L, W)
    assert r['people'] == 200 and abs(r['ratio'] - 1) < 1e-9 and abs(r['median_m'] - 1.8) < 1e-9
    # a calibration that stretches the bottom of the image (near) more than the top: near people come out taller
    Hp = _sb_per_px() @ np.array([[1, 0, 0], [0, 1, 0], [0, -0.0005, 1.0]])
    assert C.far_near(Hp[None], [b], L, W)['ratio'] < 0.6


def test_cal_flags():
    assert C.cal_flags(dict(people=200, ratio=0.98, median_m=1.75)) == []
    assert C.cal_flags(dict(people=200, ratio=0.67, median_m=1.44)) == ['far/near 0.67', 'height 1.44 m']
    assert C.cal_flags(dict(people=200, ratio=2.2, median_m=1.75)) == ['far/near 2.20']
    assert C.cal_flags(dict(people=5, ratio=2.2, median_m=1.0)) == []
