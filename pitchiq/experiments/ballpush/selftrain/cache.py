"""One decode of every dev piece: 40 px crops + context of the candidates that can matter.

Scoring all 37 M dev candidates from video takes ~2 h per model.  The tracker never reads the bottom
of the list, so this keeps, per frame: every candidate with out-of-fold score > KEEP_P, the frame's top
KEEP_TOP by that score, and every candidate of a clicked frame.  A retrained model then scores the
cache in a minute; the candidates left out keep their out-of-fold score (all <= KEEP_P).

    python cache.py [half ...]     -> selftrain/cache/<piece>.npz  (f, j, crop N x 40 x 40 x 3, ctx)
"""

import os
import sys
import time
from multiprocessing import Pool

import numpy as np

import st


def build(job):
    pt, video, clicked = job
    out = os.path.join(st.CACHE, '%s.npz' % pt)
    if os.path.exists(out):
        return pt, 0, 0, 0.0
    import cv2

    st.common.point_at_data()
    from pitchiq.balltrack import ball_tracker, candidate_crops as cc

    cv2.setNumThreads(1)
    t0 = time.time()
    frames = ball_tracker.load_frames(pt)
    ppl = cc.load_ppl(pt)
    has = ppl is not None
    f, j, p = st.load_oof(pt)
    edges = np.searchsorted(f, [fr['abs'] for fr in frames] + [10**12])
    want = None
    if st.SUB:
        zr = np.load(os.path.join(st.OUT, 'rows', '%s.npz' % pt))
        want = {}
        for ff, jj in zip(zr['f'].tolist(), zr['j'].tolist()):
            want.setdefault(ff, []).append(jj)
    F, J, C, K = [], [], [], []
    cap = cv2.VideoCapture(video)
    assert cap.isOpened(), video
    pos = frames[0]['abs']
    cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
    ntot = 0
    for q, fr in enumerate(frames):
        ab = fr['abs']
        n = len(fr['s'])
        ntot += n
        a = edges[q]
        pp = p[a : a + n]
        assert f[a] == ab and j[a + n - 1] == n - 1, (pt, ab)
        if want is not None:
            if ab not in want:
                continue
            keep = np.array(sorted(want[ab]))
        elif ab in clicked:
            keep = np.arange(n)
        else:
            keep = np.flatnonzero(pp > st.KEEP_P)
            top = np.argsort(-pp, kind='stable')[: st.KEEP_TOP]
            keep = np.union1d(keep, top)
        while pos < ab:
            if not cap.grab():
                break
            pos += 1
        ok, img = cap.read()
        assert ok and pos == ab, 'read failed at %d' % ab
        pos += 1
        c = np.empty((len(keep), st.CSIZE, st.CSIZE, 3), np.uint8)
        for i, jj in enumerate(keep):
            cc.cut(img, fr['x'][jj], fr['y'][jj], st.CSIZE, c[i])
        ctx = cc.context(fr['x'], fr['y'], fr['s'], ppl.get(ab) if has else None, has)
        F.append(np.full(len(keep), ab, np.int32))
        J.append(keep.astype(np.int16))
        C.append(c)
        K.append(ctx[keep])
    cap.release()
    tmp = out + '.tmp.npz'
    np.savez(
        tmp, f=np.concatenate(F), j=np.concatenate(J), crop=np.concatenate(C), ctx=np.concatenate(K)
    )
    os.replace(tmp, out)
    return pt, sum(len(x) for x in F), ntot, time.time() - t0


def main():
    os.makedirs(st.CACHE, exist_ok=True)
    tags = sys.argv[1:] or st.DEV
    vid = st.videos()
    ck = st.clicks()
    jobs = [
        (pt, vid[pt.split('_q')[0]], {f for _, f, _ in ck[pt.split('_q')[0]]})
        for pt in st.pieces(tags)
        if not st.SUB or os.path.exists(os.path.join(st.OUT, 'rows', '%s.npz' % pt))
    ]
    t0 = time.time()
    with Pool(int(os.environ.get('ST_PROCS', '6'))) as pool:
        for pt, k, n, dt in pool.imap_unordered(build, jobs):
            print(
                '%s  kept %d of %d  %.0f s  (total %.0f s)' % (pt, k, n, dt, time.time() - t0),
                flush=True,
            )
    print('CACHE DONE', flush=True)


if __name__ == '__main__':
    main()
