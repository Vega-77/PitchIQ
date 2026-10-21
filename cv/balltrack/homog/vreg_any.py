"""vreg.py for any video shot at this venue, not just the one the map was built from.

vreg.py tries the venue-map keyframes nearest in frame number, which only
means something in the video the map was built from. Here the candidates are
picked by where the camera is pointing: once a sample is registered, the next
one tries the keyframes whose view centre on the pitch is nearest the last
sample's; a sample with nothing to go on (the first, or after a failure)
searches every fourth keyframe, then the neighbours of the best. Matching,
inlier threshold and the output are vreg.py's.

    python vreg_any.py lo hi tag [procs]   -> <tag>_reg.npz (abs, Hsb, ninl, ok)
"""

import os
import sys
import time
from multiprocessing import Pool

import numpy as np

from vreg import HERE, MIN_INL, REPO, STEP

CENTRE = np.array([960.0, 800.0])  # a point on the ground in the lower middle of the picture
NEAR = 6  # keyframes tried when the view is known
COARSE = 4  # every n-th keyframe when it is not


def _centre(H):
    q = H.dot([CENTRE[0], CENTRE[1], 1.0])
    return q[:2] / q[2]


def work(args):
    (frames,) = args
    import cv2

    sys.path.insert(0, REPO)
    sys.path.insert(0, HERE)
    from cv import lines as L
    import vfeats, vmap, vovl

    K = vmap.load()
    ok = np.load(os.path.join(HERE, 'vmap.npz'))['ok']
    ids = np.flatnonzero(ok)
    to_sb = {i: vovl.frame_to_sb(i) for i in ids}
    cen = np.array([_centre(to_sb[i]) for i in ids])
    sift = cv2.SIFT_create(nfeatures=vfeats.NFEAT)
    cap = cv2.VideoCapture(vfeats.VIDEO)

    def best_of(cands, F):
        best = (0, None, None)
        for i in cands:
            r = vmap.match(K[i], F)  # F px -> keyframe px
            if r is not None and len(r[1]) > best[0]:
                best = (len(r[1]), to_sb[i].dot(r[0]), i)
        return best

    def nearest(p, n):
        return ids[np.argsort(np.hypot(*(cen - p).T))[:n]]

    out = []
    prev = None
    for ab in frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, ab)
        good, im = cap.read()
        if not good:
            out.append((ab, np.full((3, 3), np.nan), 0))
            prev = None
            continue
        m = cv2.erode(L.field_mask(im), np.ones((15, 15), np.uint8))
        kp, d = sift.detectAndCompute(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), m)
        F = dict(
            xy=np.float32([p.pt for p in kp]).reshape(-1, 2),
            d=(np.zeros((0, 128)) if d is None else d).astype(np.float32),
        )
        if prev is not None:
            best = best_of(nearest(_centre(prev[1]), NEAR), F)
        else:
            best = best_of(ids[::COARSE], F)
            if best[2] is not None:
                best = max(
                    best,
                    best_of(nearest(cen[np.searchsorted(ids, best[2])], NEAR), F),
                    key=lambda b: b[0],
                )
        if prev is not None and prev[0] >= MIN_INL:  # chain from last sample, as vreg.py
            r = vmap.match(prev[2], F)
            if r is not None and len(r[1]) > best[0] * 1.5:
                best = (len(r[1]), prev[1].dot(r[0]), None)
        H = best[1] / best[1][2, 2] if best[1] is not None else np.full((3, 3), np.nan)
        out.append((ab, H, best[0]))
        prev = (best[0], H, F) if best[1] is not None and best[0] >= MIN_INL else None
    return out


def main():
    lo, hi, tag = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    procs = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    frames = list(range(lo, hi + 1, STEP))
    n = -(-len(frames) // procs)
    chunks = [(frames[i : i + n],) for i in range(0, len(frames), n)]
    t0 = time.time()
    with Pool(len(chunks)) as p:
        res = sum(p.map(work, chunks), [])
    ab = np.array([r[0] for r in res])
    H = np.array([r[1] for r in res])
    ninl = np.array([r[2] for r in res])
    np.savez(os.path.join(HERE, '%s_reg.npz' % tag), abs=ab, Hsb=H, ninl=ninl, ok=ninl >= MIN_INL)
    print(
        '%d samples, ok %d, inliers median %d  p10 %d  (%.0f s)'
        % (
            len(ab),
            (ninl >= MIN_INL).sum(),
            np.median(ninl),
            np.percentile(ninl, 10),
            time.time() - t0,
        )
    )


if __name__ == '__main__':
    main()
