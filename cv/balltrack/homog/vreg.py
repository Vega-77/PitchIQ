"""Register frames to the pitch every STEP frames.

Each sampled frame is SIFT-matched against the nearest venue-map keyframes
(and the previous sample); the best RANSAC fit gives frame -> keyframe, and
the keyframe's own map placement gives frame -> StatsBomb 120x80.

    python vreg.py lo hi tag [procs]   -> <tag>_reg.npz (abs, Hsb, ninl, ok)
"""
import os, sys, time
from multiprocessing import Pool
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = 'C:/Users/alexv/Desktop/Repos/PitchIQ'
STEP = 15
MIN_INL = 30


def work(args):
    frames, = args
    import cv2
    sys.path.insert(0, REPO); sys.path.insert(0, HERE)
    from cv import lines as L
    import vfeats, vmap, vovl
    K = vmap.load()
    z = np.load(os.path.join(HERE, 'vmap.npz'))
    kab = np.array([k['abs'] for k in K]); ok = z['ok']
    sift = cv2.SIFT_create(nfeatures=vfeats.NFEAT)
    cap = cv2.VideoCapture(vfeats.VIDEO)
    out = []; prev = None
    for ab in frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, ab); good, im = cap.read()
        if not good:
            out.append((ab, np.full((3, 3), np.nan), 0)); continue
        m = cv2.erode(L.field_mask(im), np.ones((15, 15), np.uint8))
        kp, d = sift.detectAndCompute(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), m)
        F = dict(xy=np.float32([p.pt for p in kp]).reshape(-1, 2),
                 d=(np.zeros((0, 128)) if d is None else d).astype(np.float32))
        best = (0, None)
        near = [i for i in np.argsort(abs(kab - ab))[:4] if ok[i]]
        for i in near:
            r = vmap.match(K[i], F)                  # F px -> keyframe px
            if r is not None and len(r[1]) > best[0]:
                best = (len(r[1]), vovl.frame_to_sb(i).dot(r[0]))
        if prev is not None and prev[0] >= MIN_INL:  # chain from last sample
            r = vmap.match(prev[2], F)
            if r is not None and len(r[1]) > best[0] * 1.5:
                best = (len(r[1]), prev[1].dot(r[0]))
        H = best[1] / best[1][2, 2] if best[1] is not None else np.full((3, 3), np.nan)
        out.append((ab, H, best[0]))
        prev = (best[0], H, F) if best[1] is not None else None
    return out


def main():
    lo, hi, tag = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    procs = int(sys.argv[4]) if len(sys.argv) > 4 else 6
    frames = list(range(lo, hi + 1, STEP))
    n = -(-len(frames) // procs)
    chunks = [(frames[i:i + n],) for i in range(0, len(frames), n)]
    t0 = time.time()
    with Pool(len(chunks)) as p:
        res = sum(p.map(work, chunks), [])
    ab = np.array([r[0] for r in res]); H = np.array([r[1] for r in res]); ninl = np.array([r[2] for r in res])
    np.savez(os.path.join(HERE, '%s_reg.npz' % tag), abs=ab, Hsb=H, ninl=ninl, ok=ninl >= MIN_INL)
    print('%d samples, ok %d, inliers median %d  p10 %d  (%.0f s)' % (
        len(ab), (ninl >= MIN_INL).sum(), np.median(ninl), np.percentile(ninl, 10), time.time() - t0))


if __name__ == '__main__':
    main()
