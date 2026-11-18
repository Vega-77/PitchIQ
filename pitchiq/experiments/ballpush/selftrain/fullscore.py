"""The real thing for one run: its fold models on EVERY candidate, streamed from video (no cache shortcut).

Confirms the cache approximation (uncached candidates keeping their oof score).  CPU only, one process per
piece, so it can run beside a GPU job.  Single-frame runs of train.py only.

    python fullscore.py <run> <cal: global|game> [half ...]  -> runs/<run>_full/<piece>cls.npz
"""
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

import st


def one(job):
    pt, video, run, cal = job
    out = st.OUT + '/runs/%s_full/%scls.npz' % (run, pt)
    if os.path.exists(out):
        return pt, 0.0, 0.0
    import cv2
    import torch

    torch.set_num_threads(1)
    cv2.setNumThreads(1)
    st.common.point_at_data()
    from pitchiq.balltrack import ball_tracker, candidate_crops as cc, train_ball_classifier as T

    t0 = time.time()
    dev = torch.device('cpu')
    ck = torch.load(st.OUT + '/runs/%s/fold_%s.pt' % (run, st.game(pt)), map_location=dev, weights_only=True)
    net = T.BallNet(len(ck['ctx_names'])).to(dev)
    net.load_state_dict(ck['state'])
    a, b = json.load(open(st.OUT + '/runs/%s/cal_%s.json' % (run, cal)))[st.game(pt)]
    sc = T.Scorer(net, np.asarray(ck['mu'], np.float32), np.asarray(ck['sd'], np.float32), ck['ctx_names'], dev, (a, b))
    frames = ball_tracker.load_frames(pt)
    ppl = cc.load_ppl(pt)
    has = ppl is not None
    F, J, P = [], [], []
    bc, bk = [], []
    cap = cv2.VideoCapture(video)
    pos = frames[0]['abs']
    cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
    for q, fr in enumerate(frames):
        ab = fr['abs']
        while pos < ab:
            cap.grab()
            pos += 1
        ok, img = cap.read()
        assert ok and pos == ab
        pos += 1
        n = len(fr['s'])
        c = np.empty((n, 32, 32, 3), np.uint8)
        for j in range(n):
            cc.cut(img, fr['x'][j], fr['y'][j], 32, c[j])
        bc.append(c)
        bk.append(cc.context(fr['x'], fr['y'], fr['s'], ppl.get(ab) if has else None, has))
        F.append(np.full(n, ab, np.int32))
        J.append(np.arange(n, dtype=np.int16))
        if len(bc) == 32 or q == len(frames) - 1:
            P.append(sc.score(np.concatenate(bc), np.concatenate(bk), ctx_names=cc.CTX_NAMES))
            bc, bk = [], []
    cap.release()
    f, j, p = np.concatenate(F), np.concatenate(J), np.concatenate(P)
    # how far from the cache-assembled file
    z = np.load(st.OUT + '/runs/%s_%s/%scls.npz' % (run, cal, pt))
    o = np.lexsort((z['j'], z['f']))
    d = np.abs(z['p'][o] - p)
    ca = st.load_cache(pt, crops=False)
    inc = np.zeros(len(p), bool)
    inc[np.searchsorted(f.astype(np.int64) * 64 + j, ca['f'] * 64 + ca['j'])] = True
    np.savez(out, f=f, j=j, p=p)
    return pt, float(d[inc].max()), time.time() - t0


def main():
    run, cal = sys.argv[1], sys.argv[2]
    os.makedirs(st.OUT + '/runs/%s_full' % run, exist_ok=True)
    vid = st.videos()
    jobs = [(pt, vid[pt.split('_q')[0]], run, cal) for pt in st.pieces(sys.argv[3:] or st.DEV)]
    t0 = time.time()
    with Pool(int(os.environ.get('ST_PROCS', '7'))) as pool:
        for pt, d, dt in pool.imap_unordered(one, jobs):
            print('%s  cached candidates: max |full - cache| %.4f  %.0f s (total %.0f s)' % (pt, d, dt, time.time() - t0), flush=True)
    print('FULLSCORE DONE', flush=True)


if __name__ == '__main__':
    main()
