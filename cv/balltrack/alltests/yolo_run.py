"""The old shipped ball detector, COCO YOLOv8n (class 32), at the repo defaults
(imgsz 1280, conf 0.08), over labelled windows.

    python yolo_run.py <tag> [<tag> ...]   -> yolo/<tag>.npz (f, x, y, conf: every detection)
                                              p2d/yolo_raw/<tag>.npz   its top detection each frame
                                              p2d/yolo_trk/<tag>.npz   the repo's cv/ball.py build_trajectory
                                                                       (DP + static drop + interpolation),
                                                                       run per 1200-frame chunk as the
                                                                       repo pipeline would see a clip
"""
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
REPO = 'C:/Users/alexv/Desktop/Repos/PitchIQ'
sys.path.insert(0, S2); sys.path.insert(0, REPO)
from cv.ball import BallCandidate, build_trajectory   # noqa: E402

FPS = 30.0
CHUNK = 1200


def window(tag):
    z = np.load(os.path.join(S2, '%s.npz' % tag), allow_pickle=True)
    return int(z['lo']), int(z['hi'])


def detect(tag):
    p = os.path.join(HERE, 'yolo', '%s.npz' % tag)
    if os.path.exists(p):
        return np.load(p)
    import feat4
    from ultralytics import YOLO
    m = YOLO(os.path.join(REPO, 'cv', 'weights', 'yolov8n.pt'))
    lo, hi = window(tag)
    cap = cv2.VideoCapture(feat4.VIDEO); cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
    F, X, Y, Cf = [], [], [], []; t0 = time.time()
    for f in range(lo, hi + 1):
        ok, fr = cap.read()
        assert ok, f
        r = m.predict(fr, imgsz=1280, conf=0.08, classes=[32], verbose=False)[0]
        for (x1, y1, x2, y2), c in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy()):
            F.append(f); X.append((x1 + x2) / 2); Y.append((y1 + y2) / 2); Cf.append(c)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    np.savez(p, f=np.array(F, int), x=np.array(X), y=np.array(Y), conf=np.array(Cf))
    print('%s yolo  %d frames  %d dets  %.0f s' % (tag, hi - lo + 1, len(F), time.time() - t0), flush=True)
    return np.load(p)


def save(ver, tag, lo, hi, x, y, src):
    d = os.path.join(HERE, 'p2d', ver); os.makedirs(d, exist_ok=True)
    np.savez(os.path.join(d, '%s.npz' % tag), abs=np.arange(lo, hi + 1), x=x, y=y, src=src)
    print('%-14s %-5s  conf %.3f  fill %.3f' % (ver, tag, (src == 1).mean(), (src == 2).mean()), flush=True)


def versions(tag):
    lo, hi = window(tag); n = hi - lo + 1
    D = detect(tag)
    f, X, Y, C = D['f'], D['x'], D['y'], D['conf']
    x = np.full(n, np.nan); y = np.full(n, np.nan); src = np.zeros(n, np.int8); best = np.full(n, -1.0)
    for k in range(len(f)):
        i = f[k] - lo
        if C[k] > best[i]:
            best[i] = C[k]; x[i], y[i] = X[k], Y[k]; src[i] = 1
    save('yolo_raw', tag, lo, hi, x, y, src)
    x = np.full(n, np.nan); y = np.full(n, np.nan); src = np.zeros(n, np.int8)
    for c0 in range(lo, hi + 1, CHUNK):
        c1 = min(c0 + CHUNK, hi + 1)
        by = {}
        for k in np.flatnonzero((f >= c0) & (f < c1)):
            by.setdefault(int(f[k]), []).append(BallCandidate(frame_index=int(f[k]), timestamp_s=f[k] / FPS,
                                                              xy=(float(X[k]), float(Y[k])), confidence=float(C[k])))
        for p in build_trajectory(by, frame_width=1920).points:
            i = p.frame_index - lo
            x[i], y[i] = p.xy; src[i] = 1 if p.observed else 2
    save('yolo_trk', tag, lo, hi, x, y, src)


if __name__ == '__main__':
    for t in sys.argv[1:]:
        versions(t)
