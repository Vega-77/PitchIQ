import numpy as np

from cv.pitchtrack import (
    Detections,
    PitchProjector,
    PitchTracker,
    TrackerSettings,
    body_descriptor,
    foot_covariances,
    grass_hue,
)

W, H = 1920, 1080
# 20 px per metre: the picture shows 96 x 54 m
SCALE = np.diag([1 / 20, 1 / 20, 1.0])


def flat_projector(frames, pan=None):
    """Every frame px -> m by scaling, optionally panned by pan(f) metres in x."""
    hs = {}
    for f in frames:
        h = SCALE.copy()
        if pan is not None:
            h = np.array([[1, 0, pan(f)], [0, 1, 0], [0, 0, 1.0]]) @ h
        hs[f] = h
    return PitchProjector(hs, W, H)


def box_at(proj, f, xy_m, height=80.0):
    u, v = proj.to_px(f, [xy_m])[0]
    return [u - height / 4, v - height, u + height / 4, v]


def make_dets(rows):
    """rows: (frame, box, conf, team)"""
    rows = sorted(rows, key=lambda r: r[0])
    return Detections(
        frame=[r[0] for r in rows],
        box=[r[1] for r in rows],
        conf=[r[2] for r in rows],
        team=[r[3] for r in rows],
        desc=np.full((len(rows), 6), np.nan),
    )


def people_of(tracklet, owner):
    return {owner[r] for r in tracklet.rows}


def test_jacobian_matches_finite_differences():
    h = np.array([[0.05, 0.01, -3.0], [0.002, 0.2, -40.0], [0.00001, 0.002, 1.0]])
    p = PitchProjector({0: h}, W, H)
    J = p.jacobian(0, 900.0, 700.0)
    e = 1e-3
    num = np.c_[
        (p.to_m(0, [[900 + e, 700]]) - p.to_m(0, [[900 - e, 700]]))[0] / (2 * e),
        (p.to_m(0, [[900, 700 + e]]) - p.to_m(0, [[900, 700 - e]]))[0] / (2 * e),
    ]
    assert np.allclose(J, num, rtol=1e-5, atol=1e-9)


def test_in_view_and_behind_the_camera():
    # rows near the bottom approach this projection's horizon, and beyond it
    # lies "behind the camera"
    h = np.array([[0.05, 0.0, 0.0], [0.0, 0.1, 0.0], [0.0, -0.0009, 1.0]])
    p = PitchProjector({0: h, 1: h}, W, H)
    inside = p.to_m(0, [[960, 900]])
    assert p.in_view(0, inside)[0]
    assert not p.in_view(0, inside + [500, 0])[0]
    # a point beyond the horizon maps "behind" and must not count as visible
    behind = np.array([[48.0, -500.0]])
    px = p.to_px(0, behind)[0]
    assert not p.in_view(0, behind)[0], px
    assert not p.in_view(5, inside)[0]  # unregistered frame
    many = p.in_view_many([0, 1, 7], np.vstack([inside, inside, inside]))
    assert many.tolist() == [True, True, False]


def test_far_side_noise_is_long_along_the_ray():
    # a broadcast-style camera: the top of the picture is the far side
    h = np.array([[0.05, 0.0, 0.0], [0.0, 0.1, 0.0], [0.0, 0.0008, 0.2]])
    p = PitchProjector({0: h}, W, H)
    near = foot_covariances(p, 0, [[950, 900, 970, 1000]])[0]
    far = foot_covariances(p, 0, [[955, 160, 965, 200]])[0]
    assert np.linalg.eigvalsh(far)[-1] > 4 * np.linalg.eigvalsh(near)[-1]


def test_panning_camera_keeps_one_track_per_still_player():
    frames = range(60)
    proj = flat_projector(frames, pan=lambda f: 0.15 * f)  # camera pans 9 m
    rows, owner = [], {}
    for f in frames:
        for pid, xy in enumerate([(40.0, 20.0), (55.0, 30.0)]):
            owner[len(rows)] = pid
            rows.append((f, box_at(proj, f, xy), 0.9, 0))
    det = make_dets(rows)
    owner = {i: owner[i] for i in range(len(rows))}
    out = PitchTracker(proj).run(det)
    assert len(out) == 2
    assert all(len(people_of(t, owner)) == 1 for t in out)
    assert sorted(len(t.rows) for t in out) == [60, 60]


def test_short_miss_in_view_is_coasted():
    frames = range(50)
    proj = flat_projector(frames)
    rows = [(f, box_at(proj, f, (30 + 0.1 * f, 20.0)), 0.9, 0) for f in frames if not 20 <= f < 28]
    out = PitchTracker(proj).run(make_dets(rows))
    assert len(out) == 1


def test_leaving_the_picture_ends_the_track():
    frames = range(80)
    proj = flat_projector(frames)
    # walks off the right edge (96 m) at 6 m/s; someone else keeps the window going
    rows = [(f, box_at(proj, f, (30.0, 20.0)), 0.9, 0) for f in frames]
    for f in frames:
        x = 90 + 0.2 * f
        if x < 95:
            rows.append((f, box_at(proj, f, (x, 20.0)), 0.9, 0))
    out = PitchTracker(proj).run(make_dets(rows))
    assert len(out) == 2
    walker = min(out, key=lambda t: len(t.rows))
    assert walker.end_reason == 'left view'
    assert max(out, key=lambda t: len(t.rows)).end_reason == 'window end'


def test_weak_detections_continue_but_never_start():
    frames = range(40)
    proj = flat_projector(frames)
    rows = [(f, box_at(proj, f, (30.0, 20.0)), 0.9 if f < 10 else 0.2, 0) for f in frames]
    rows += [(f, box_at(proj, f, (60.0, 20.0)), 0.2, 0) for f in frames]
    out = PitchTracker(proj).run(make_dets(rows))
    assert len(out) == 1
    assert len(out[0].rows) == 40


def test_crossing_teammates_are_never_swapped():
    frames = range(60)
    proj = flat_projector(frames)
    rows, owner = [], []
    for f in frames:
        t = f / 30
        for pid, (x0, vx) in enumerate([(40.0, 4.0), (48.0, -4.0)]):
            owner.append(pid)
            rows.append((f, box_at(proj, f, (x0 + vx * t, 20.0)), 0.9, 0))
    det = make_dets(rows)
    out = PitchTracker(proj).run(det)
    assert out
    assert all(len(people_of(t, owner)) == 1 for t in out)


def test_body_descriptor_ignores_grass():
    img = np.zeros((200, 100, 3), np.uint8)
    img[:] = (40, 160, 40)  # grass (BGR)
    img[40:100, 35:65] = (30, 30, 200)  # red shirt
    d = body_descriptor(img, [20, 10, 80, 190])
    assert np.isfinite(d[:3]).all()
    assert d[1] > 150  # Lab a: strongly red
    assert np.isnan(body_descriptor(img, [0, 0, 2, 2])).all()


def test_a_neon_shirt_at_the_grass_hue_is_not_grass():
    import cv2

    rng = np.random.default_rng(0)
    hsv = np.zeros((400, 400, 3), np.uint8)
    hsv[..., 0] = 47 + rng.integers(-3, 4, (400, 400))  # grass, as on the dev window
    hsv[..., 1] = 70 + rng.integers(-15, 16, (400, 400))
    hsv[..., 2] = 140 + rng.integers(-20, 21, (400, 400))
    hsv[120:220, 180:220] = (47, 195, 192)  # the referee close up: same hue
    img = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    grass = grass_hue(img)
    assert abs(grass[0] - 47) <= 3 and grass[2] < 195
    d = body_descriptor(img, [170, 100, 230, 300], grass)
    assert np.isfinite(d[:3]).all() and d[1] < 110 and d[2] > 160  # Lab: green and yellow
    assert np.isnan(
        body_descriptor(img, [170, 100, 230, 300], grass[:2])[:3]
    ).all()  # hue alone eats it


def test_tracker_settings_are_plain():
    s = TrackerSettings(fps=25.0)
    assert s.fps == 25.0 and s.high_conf > s.low_conf
