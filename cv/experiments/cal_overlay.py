"""Draw a calibration's pitch lines on the raw frames it was solved on.

    PIQ_VIDEO=<video> python -m cv.experiments.cal_overlay <tag> [--n 6]

Reads balltrack/homog/<tag>_reg.npz (per-frame px -> StatsBomb homographies),
takes n frames spread over it, and draws the touchlines, goal lines, halfway
line, boxes and centre circle back into each frame (the boxes and circle at
their metre sizes on the pitch size in <tag>_venue.npz, if there is one,
whose L and W are in centre-circle radii). Writes
plab/out/<tag>_overlay.jpg. For checking the far touchline by eye: paint that
sits off the drawn line is calibration error, not tracking error.

It also prints `checks.feet_on_pitch` for the people on those frames: well
under 0.8 means the calibration throws players off the pitch. A pitch drawn
too big passes that check, so look at the sheet as well.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

from cv.checks import feet_on_pitch
from cv.experiments.autocal_run import M_PER_R
from cv.experiments.player_lab import PLAB, VIDEO

HOMOG = PLAB.parent / 'homog'


def _lines(L: float = 105.0, W: float = 68.0) -> list[np.ndarray]:
    """Pitch markings, in metres scaled onto StatsBomb 120 x 80 for an L x W m pitch."""
    sx, sy = 120.0 / L, 80.0 / W
    out = [np.array([[0, 0], [120, 0], [120, 80], [0, 80], [0, 0]]), np.array([[60, 0], [60, 80]])]
    for x0, s in ((0, 1), (120, -1)):
        for d, w in ((16.5, 40.32), (5.5, 18.32)):
            a, b = 40 - w / 2 * sy, 40 + w / 2 * sy
            out.append(np.array([[x0, a], [x0 + s * d * sx, a], [x0 + s * d * sx, b], [x0, b]]))
    t = np.linspace(0, 2 * np.pi, 73)
    out.append(np.c_[60 + 9.15 * sx * np.cos(t), 40 + 9.15 * sy * np.sin(t)])
    return [np.asarray(l, float) for l in out]


def _densify(l: np.ndarray, step: float = 1.0) -> np.ndarray:
    out = [l[0]]
    for a, b in zip(l[:-1], l[1:]):
        n = max(1, int(np.hypot(*(b - a)) / step))
        out.extend(a + (b - a) * t for t in np.linspace(0, 1, n + 1)[1:])
    return np.array(out)


def overlay(tag: str, n: int):
    import cv2

    z = np.load(HOMOG / f'{tag}_reg.npz')
    v = HOMOG / f'{tag}_venue.npz'
    lines = _lines(*(M_PER_R * float(np.load(v)[k]) for k in ('L', 'W'))) if v.exists() else _lines()
    idx = np.flatnonzero(z['ok'])
    idx = idx[np.linspace(0, len(idx) - 1, n).round().astype(int)]
    from ultralytics import YOLO

    from cv.detector import CLASS_PERSON, DEFAULT_WEIGHTS
    model = YOLO(str(DEFAULT_WEIGHTS))
    cap = cv2.VideoCapture(VIDEO)
    tiles, Hs, feet = [], [], []
    for i in idx:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(z['abs'][i]))
        ok, img = cap.read()
        if not ok:
            continue
        H = z['Hsb'][i]
        b = model.predict(img, classes=[CLASS_PERSON], conf=0.4, imgsz=1280, verbose=False)[0].boxes.xyxy.cpu().numpy()
        b = b[b[:, 3] - b[:, 1] < 0.25 * img.shape[0]]    # the people on the pitch, not the camera's neighbours
        Hs.append(H); feet.append(np.c_[(b[:, 0] + b[:, 2]) / 2, b[:, 3]])
        Hi = np.linalg.inv(H)
        c = H @ [img.shape[1] / 2, img.shape[0] / 2, 1.0]  # the frame centre's pitch point must map back in front
        if (Hi @ (c / c[2]))[2] < 0:
            Hi = -Hi
        for l in lines:
            d = _densify(l)
            p = np.c_[d, np.ones(len(d))] @ Hi.T
            good = p[:, 2] > 0                            # points behind the camera do not project
            q = (p[:, :2] / p[:, 2:3])
            for a, b, ga, gb in zip(q[:-1], q[1:], good[:-1], good[1:]):
                if ga and gb and np.all(np.abs(np.r_[a, b]) < 1e5):
                    cv2.line(img, tuple(int(v) for v in a), tuple(int(v) for v in b), (255, 0, 255), 2, cv2.LINE_AA)
        cv2.putText(img, f'{tag} f{int(z["abs"][i])}', (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 255), 3)
        tiles.append(cv2.resize(img, (960, 540)))
    while len(tiles) % 2:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[k:k + 2]) for k in range(0, len(tiles), 2)])
    out = PLAB / 'out' / f'{tag}_overlay.jpg'
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return out, feet_on_pitch(np.array(Hs), feet)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='python -m cv.experiments.cal_overlay', description=__doc__.split('\n\n')[0])
    p.add_argument('tag')
    p.add_argument('--n', type=int, default=6, help='frames to draw')
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    out, feet = overlay(a.tag, a.n)
    print('overlay:', out)
    print('feet on the pitch: %.2f of %d' % (feet['on_pitch'], feet['feet']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
