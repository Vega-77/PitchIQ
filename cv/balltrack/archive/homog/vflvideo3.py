"""Check video for the label-free chains (vflchain2): magenta = the single-flight
fit before chaining, cyan = chain, yellow = inferred take-off / landing.

    python vflvideo3.py [tag] [ver] -> chains<ver>.webm   (ver 2 = vflchain2, 3k = vflchain3 run 3k)
"""
import os
import sys
import time

import cv2
import numpy as np

import vflight as V
import vdrag as D
import vflchain as Ch
import vflvideo2 as W

tag = sys.argv[1] if len(sys.argv) > 1 else 'vid1'
ver = sys.argv[2] if len(sys.argv) > 2 else '2'


def main():
    import vfeats
    tr = V.Track(tag)
    A0 = int(tr.abs[0])
    T = np.load(os.path.join(V.HERE, 'airtruth.npz'))
    truth = dict(zip(T['abs'].tolist(), T['truth'].tolist()))
    chains = list(np.load(os.path.join(V.HERE, '%s_chains%s.npy' % (tag, ver)), allow_pickle=True))
    no = 0; sel = []
    for c in chains:
        if not any(c['seg_air']):
            continue
        nodes, ts, ks = Ch.unpack(c['x'], c['m'])
        c['nos'] = list(range(no + 1, no + c['m'] + 1)); no += c['m']
        c['flips'] = []
        for j, th in enumerate(c['old']):
            P = D.drag(th, np.array([th[4], th[4] + th[5]]))
            c['flips'].append(W.radial(P[0], P[1]) != W.radial(nodes[j], nodes[j + 1]))
        sel.append(c)
    part = os.environ.get('PART')                  # 'i/n': the i-th of n parts (keeps each file small)
    if part:
        i_, n_ = map(int, part.split('/'))
        sel = [c for q, c in enumerate(sel) if q * n_ // len(sel) == i_ - 1]
    cap = cv2.VideoCapture(vfeats.VIDEO)
    p = os.path.join(V.HERE, 'chains%s%s.webm' % (ver, '_part%s' % part.split('/')[0] if part else ''))
    vw = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*'VP80'), 30 // W.STEP, (W.W, W.H))
    t0 = time.time(); nfr = sum(W.segment(cap, vw, tr, c, no, truth, A0) for c in sel)
    vw.release()
    print('%s: %d chains, %d frames, %.1f MB, %.0f s' % (p, len(sel), nfr, os.path.getsize(p) / 1e6, time.time() - t0))


if __name__ == '__main__':
    main()
