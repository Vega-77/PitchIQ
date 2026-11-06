"""The Player ID lab's clip planning: which stretch of a tracked player is shown, and how."""

from types import SimpleNamespace

import numpy as np

from pitchiq.experiments import idlab


def ident(heights, interp=None, start=1000):
    n = len(heights)
    h = np.asarray(heights, float)
    boxes = np.stack([np.full(n, 100.0), 500 - h, np.full(n, 140.0), np.full(n, 500.0)], axis=1)
    return SimpleNamespace(
        frames=np.arange(start, start + n),
        boxes=boxes,
        interp=np.zeros(n, bool) if interp is None else np.asarray(interp, bool),
    )


def test_the_clip_is_the_stretch_where_the_player_is_biggest():
    n = int(idlab.CLIP_S * 30)
    h = [60] * 600 + [150] * n + [60] * 600
    P = idlab.plan(ident(h), 30.0)
    assert P[0]['f'] == 1600
    assert len(P) == n // 2  # every other frame at 30 fps: 15 shown a second
    assert all(p['f'] < 1600 + n for p in P)


def test_guessed_frames_count_for_nothing_when_choosing_the_stretch():
    n = int(idlab.CLIP_S * 30)
    h = [200] * n + [100] * n
    interp = [True] * n + [False] * n
    P = idlab.plan(ident(h, interp), 30.0)
    assert P[0]['f'] == 1000 + n
    assert not any(p['hidden'] for p in P)


def test_a_short_player_is_shown_whole():
    P = idlab.plan(ident([80] * 100), 30.0)
    assert [p['f'] for p in P] == list(range(1000, 1100, 2))


def test_the_crop_is_smoothed_but_keeps_its_ends():
    v = np.array([0.0] * 30 + [10.0] * 30)
    s = idlab._smooth(v)
    assert s[0] == 0 and s[-1] == 10
    assert np.all(np.diff(s) >= 0) and 0 < s[30] < 10


def test_a_frame_is_close_up_beside_wider_view():
    img = np.zeros((1080, 1920, 3), np.uint8)
    p = dict(box=np.array([900.0, 400, 940, 500]), hidden=False, cx=920.0, cy=450.0, h=100.0)
    out = idlab.render(img, p)
    assert out.shape == (idlab.ZOOM, idlab.ZOOM + 4 + idlab.CTX, 3)
    p_edge = dict(p, box=np.array([1890.0, 980, 1919, 1079]), cx=1905.0, cy=1030.0)
    assert idlab.render(img, p_edge).shape == out.shape  # a player at the frame's corner still fits
