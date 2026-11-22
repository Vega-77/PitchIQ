"""The neighbouring frames of every cached candidate, for the five-frame-stack rework.

For each row of cache/<piece>.npz (candidate at x, y in frame t): a 40 px GREY crop from frames t-2, t-1,
t+1, t+2, each cut where the same spot of the ground is in that frame (the piece's pan homographies), so
paint and cones stand still in the stack and a rolling ball moves.  A neighbour outside the video or the
pan table repeats the centre frame.

    python tcache.py [half ...]   -> selftrain/tcache/<piece>.npy  (N x 4 x 40 x 40 uint8, cache row order)
"""

import os
import sys
import time
from multiprocessing import Pool

import numpy as np

import st

OFFS = (-2, -1, 1, 2)


def build(job):
    pt, video = job
    out = os.path.join(st.OUT, 'tcache', '%s.npy' % pt)
    if os.path.exists(out):
        return pt, 0, 0.0
    import cv2

    st.common.point_at_data()
    from pitchiq.balltrack import ball_tracker, candidate_crops as cc

    cv2.setNumThreads(1)
    t0 = time.time()
    frames = {fr['abs']: fr for fr in ball_tracker.load_frames(pt)}
    ca = st.load_cache(pt, crops=False)
    pan = np.load(os.path.join(st.BT, '%span.npz' % pt))
    cum, plo = pan['cum'], int(pan['lo'])
    phi = plo + len(cum) - 1
    N = len(ca['f'])
    T = np.zeros((N, 4, st.CSIZE, st.CSIZE), np.uint8)
    edges = np.searchsorted(ca['f'], np.r_[np.unique(ca['f']), 10**12])
    uf = np.unique(ca['f'])
    lo = int(uf[0])
    cap = cv2.VideoCapture(video)
    start = max(0, lo - 2)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    buf = {}
    k = 0  # index into uf: the next centre frame to finish
    pos = start
    dst = np.empty((st.CSIZE, st.CSIZE, 1), np.uint8)

    def finish(t, q):
        a, b = edges[q], edges[q + 1]
        fr = frames[t]
        jj = ca['j'][a:b]
        x, y = fr['x'][jj], fr['y'][jj]
        ok_t = plo <= t <= phi
        for oi, o in enumerate(OFFS):
            g = buf.get(t + o)
            xx, yy = x, y
            if g is None or not ok_t or not (plo <= t + o <= phi):
                g = buf[t]
            else:
                # the same ground spot in frame t+o: inv(cum[t+o]) . cum[t] . (x, y, 1)
                H = np.linalg.inv(cum[t + o - plo]).dot(cum[t - plo])
                q3 = np.column_stack([x, y, np.ones(len(x))]).dot(H.T)
                xx, yy = q3[:, 0] / q3[:, 2], q3[:, 1] / q3[:, 2]
                if not (np.isfinite(xx).all() and np.isfinite(yy).all()):
                    xx, yy, g = x, y, buf[t]
            for i in range(len(jj)):
                cc.cut(g, xx[i], yy[i], st.CSIZE, dst)
                T[a + i, oi] = dst[:, :, 0]

    while k < len(uf):
        ok, img = cap.read()
        if not ok:
            break
        buf[pos] = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)[:, :, None]
        while k < len(uf) and uf[k] + 2 <= pos:
            finish(int(uf[k]), k)
            k += 1
        for old in [f for f in buf if f < (uf[k] if k < len(uf) else pos) - 2]:
            del buf[old]
        pos += 1
    while k < len(uf):  # the video ended inside the last two frames
        if int(uf[k]) in buf:
            finish(int(uf[k]), k)
        k += 1
    cap.release()
    np.save(out + '.tmp.npy', T)
    os.replace(out + '.tmp.npy', out)
    return pt, N, time.time() - t0


def main():
    os.makedirs(st.OUT + '/tcache', exist_ok=True)
    vid = st.videos()
    jobs = [
        (pt, vid[pt.split('_q')[0]])
        for pt in st.pieces(sys.argv[1:] or st.DEV)
        if os.path.exists(os.path.join(st.CACHE, '%s.npz' % pt))
    ]
    t0 = time.time()
    with Pool(int(os.environ.get('ST_PROCS', '6'))) as pool:
        for pt, n, dt in pool.imap_unordered(build, jobs):
            print('%s  %d rows  %.0f s  (total %.0f s)' % (pt, n, dt, time.time() - t0), flush=True)
    print('TCACHE DONE', flush=True)


if __name__ == '__main__':
    main()
