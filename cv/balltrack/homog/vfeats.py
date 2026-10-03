"""Ground features for the venue map: SIFT on the grass of one frame every
STEP frames across both play periods.

    python vfeats.py [procs]      -> vfeats.npz  (per keyframe: abs, xy, desc)

Features are masked to the field (cv.lines.field_mask, eroded so the fence
edge and crowd are out) - everything on the ground plane is a valid match
under one homography, players are not but move, so RANSAC drops them.
"""
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = 'C:/Users/alexv/Desktop/Repos/PitchIQ'
VIDEO = os.environ.get('PIQ_VIDEO', 'C:/Users/alexv/Downloads/'
                       '8-26-26 Scrimmage v Gov Livingston - Tactical.mp4')
STEP = 150
PERIODS = [(32400, 108000), (127350, 201150)]
NFEAT = 1500


def work(frames):
    import cv2
    sys.path.insert(0, REPO)
    from cv import lines as L
    sift = cv2.SIFT_create(nfeatures=NFEAT)
    cap = cv2.VideoCapture(VIDEO)
    out = []
    for ab in frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, ab)
        ok, im = cap.read()
        if not ok:
            continue
        m = cv2.erode(L.field_mask(im), np.ones((15, 15), np.uint8))
        kp, d = sift.detectAndCompute(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), m)
        if d is None:
            d = np.zeros((0, 128), np.float32)
        xy = np.float32([k.pt for k in kp]).reshape(-1, 2)
        out.append((ab, xy, np.clip(d, 0, 255).astype(np.uint8)))
    return out


def main():
    procs = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    frames = [ab for lo, hi in PERIODS for ab in range(lo, hi, STEP)]
    chunks = [frames[i::procs] for i in range(procs)]
    t0 = time.time()
    with Pool(procs) as p:
        res = sorted(sum(p.map(work, chunks), []), key=lambda r: r[0])
    ab = np.array([r[0] for r in res], np.int32)
    n = np.array([len(r[1]) for r in res], np.int32)
    np.savez(os.path.join(HERE, 'vfeats.npz'), abs=ab, n=n,
             xy=np.concatenate([r[1] for r in res]),
             desc=np.concatenate([r[2] for r in res]))
    print('%d keyframes, median %d features, %.0f s' % (
        len(ab), np.median(n), time.time() - t0))


if __name__ == '__main__':
    main()
