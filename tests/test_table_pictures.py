"""Players' pictures for a report from the table: chosen boxes, a short read, the sharpest kept."""

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from pitchiq import table_pictures as tp

W, H = 320, 240
# Pixels -> StatsBomb units, the same on every frame; the pitch is 120 x 80 m, so metres too.
SCALE = np.diag([120 / W, 80 / H, 1.0])
CAM = SimpleNamespace(length_m=120.0, width_m=80.0, abs=np.array([0]), H=np.stack([SCALE] * 400))


def metres(u, v):
    return u * 120 / W, v * 80 / H


def half_of(dots):
    """A table with these (frame, trk, u, v) seen dots, all team 0."""
    f, trk, u, v = (np.array(c) for c in zip(*dots))
    x, y = metres(u.astype(float), v.astype(float))
    players = dict(f=f, trk=trk, x=x, y=y, seen=np.ones(len(f), bool), team=np.zeros(len(f), int))
    return SimpleNamespace(tag='zz', players=players, track_ids=lambda: players['trk'] * 3)


def dets_of(rows):
    """Detections from (frame, x1, y1, x2, y2, conf) rows."""
    rows = sorted(rows)
    return dict(
        frame=np.array([r[0] for r in rows]),
        box=np.array([r[1:5] for r in rows], np.float32),
        conf=np.array([r[5] for r in rows]),
        w=W,
        h=H,
    )


def test_a_dot_goes_back_to_its_box_feet():
    u, v = tp.feet_px(CAM, np.array([0, 5]), *metres(np.array([100.0, 7.0]), np.array([50.0, 9.0])))
    assert np.allclose(u, [100, 7]) and np.allclose(v, [50, 9])


def test_the_tallest_box_alone_on_the_dot_wins():
    half = half_of([(0, 1, 100, 150), (15, 1, 100, 160), (30, 1, 166, 150), (45, 1, 15, 150)])
    dets = dets_of(
        [
            (0, 90, 100, 110, 150, 0.9),  # 50 px, alone
            (15, 85, 90, 115, 160, 0.9),  # 70 px, but someone stands over half of it
            (15, 88, 95, 112, 140, 0.2),
            (30, 150, 70, 180, 150, 0.9),  # 80 px, alone: the dot is on its feet
            (30, 185, 70, 215, 150, 0.9),  # beside it, not over it
            (45, 0, 60, 30, 150, 0.9),  # 90 px, but touching the frame's edge
        ]
    )
    got = tp.choose(half, CAM, dets, {3})
    assert [(k.frame, k.score) for k in got[3]] == [(30, 80.0), (0, 50.0)]
    assert got[3][0].box == (150.0, 70.0, 180.0, 150.0)


def test_a_box_far_from_the_dot_is_not_the_dot():
    half = half_of([(0, 1, 100, 150)])
    dets = dets_of([(0, 140, 100, 160, 150, 0.9)])  # its feet 40 px away, the box 50 px tall
    assert tp.choose(half, CAM, dets, {3}) == {}


def test_faint_boxes_below_the_bar_do_not_count_as_someone_in_the_way():
    half = half_of([(0, 1, 100, 150)])
    dets = dets_of([(0, 90, 100, 110, 150, 0.9), (0, 92, 105, 108, 140, tp.OTHER_CONF / 2)])
    assert [k.frame for k in tp.choose(half, CAM, dets, {3})[3]] == [0]


def test_the_shortlist_is_different_moments():
    ks = [tp.Pick(3, f, (0, 0, 1, 1), s) for f, s in ((0, 90), (10, 89), (100, 80), (300, 70))]
    assert [k.frame for k in tp.shortlist(ks, n=3, gap=60)] == [0, 100, 300]


@pytest.fixture
def video(tmp_path):
    """200 grey frames; a figure 60 px tall at (100, 90) on frames 30 (blurred) and 120 (sharp)."""
    path = tmp_path / 'v.avi'
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), 30, (W, H))
    rng = np.random.default_rng(0)
    sharp = (rng.integers(0, 2, (60, 24, 1)) * 255).astype(np.uint8).repeat(3, 2)
    for f in range(200):
        img = np.full((H, W, 3), 120, np.uint8)
        if f == 120:
            img[90:150, 100:124] = sharp
        elif f == 30:
            img[90:150, 100:124] = cv2.GaussianBlur(sharp, (9, 9), 4)
        cv2.putText(img, str(f), (250, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        out.write(img)
    out.release()
    return path


def test_the_sharpest_of_the_tries_is_kept(video):
    half = half_of([(30, 1, 112, 150), (120, 1, 112, 150)])
    dets = dets_of([(30, 100, 90, 124, 150, 0.9), (120, 100, 90, 124, 150, 0.9)])
    figure = SimpleNamespace(track_ids={3}, sightings=50, thumb=None, thumb_height_px=None)
    nobody = SimpleNamespace(track_ids={99}, sightings=10, thumb=None, thumb_height_px=None)
    report = SimpleNamespace(clusters=[nobody, figure])
    said = tp.attach(report, half, video, cam=CAM, dets=dets)
    assert said['asked'] == 2 and said['pictured'] == 1 and said['frames'] == 2
    assert said['kept'] == 1 and nobody.thumb is None
    assert figure.thumb.startswith('data:image/jpeg;base64,') and figure.thumb_height_px == 60
    cuts = tp.read(video, tp.choose(half, CAM, dets, {3})[3])
    by_frame = {k.frame: s for k, (_, s) in cuts.items()}
    assert by_frame[120] > 3 * by_frame[30]


def test_reading_lands_on_the_frames_asked_for(video):
    """Each cut is its own frame, whether it was reached by decoding forward or by a seek."""
    picks = [tp.Pick(3, f, (240, 5, 316, 40), 35.0) for f in (5, 6, 150, 7, 2)]
    cuts = tp.read(video, picks)
    cap = cv2.VideoCapture(str(video))
    for k in picks:
        cap.set(cv2.CAP_PROP_POS_FRAMES, k.frame)
        ok, img = cap.read()
        assert ok and np.array_equal(cuts[k][0], tp.cut(img, k.box)), k.frame
    cap.release()
