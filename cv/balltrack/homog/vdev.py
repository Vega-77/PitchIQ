"""One-line scores of ball3d files on the even (dev) clips; --holdout adds the odd clips.

  air     Air Lab frames: precision / recall / F, true flights hit, false runs
  pos     Flight Lab: median in-flight error (SB) with the flat reading where
          no air is claimed, and the error on claimed air frames only

    python vdev.py [--holdout] a.npz b.npz ...
"""
import json
import sys

import numpy as np

import vflscore as S

A0 = 86475
man = json.load(open('airlab/manifest.json'))['clips']
T = np.load('airtruth.npz')
tt = dict(zip(T['abs'].tolist(), T['truth'].tolist()))
z = np.load('vid1_ballsb.npz')
flat = np.c_[z['x'], z['y']]; has = z['has'].astype(bool)
n = len(flat)
clipof = np.full(n, -1)
for i, c in enumerate(man):
    clipof[c['start'] - A0:c['end'] - A0 + 1] = i
lab = np.array([tt.get(a + A0, '') for a in range(n)])
FL = S.load_truth()
# flights you deleted in the Flight Lab: your later call, so their frames are not scored
import glob, os
for p_ in glob.glob(os.path.join('flightlabels', 'flights', '*.json')):
    f_ = json.load(open(p_))
    if f_.get('deleted'):
        lab[f_['f0'] - A0:f_['f1'] - A0 + 1] = 'x'


def runs_of(m):
    r = []; i = 0
    while i < len(m):
        if m[i]:
            j = i
            while j + 1 < len(m) and m[j + 1]:
                j += 1
            r.append((i, j)); i = j + 1
        else:
            i += 1
    return r


def score(p, par):
    B = np.load(p)
    st = B['state']; air = st == 'air'
    xy = np.c_[B['x'], B['y']]
    ok_air = air & np.isfinite(xy[:, 0])
    inc = (clipof >= 0) & (clipof % 2 == par)
    vis = inc & (lab != 'hidden') & (lab != '') & (lab != 'x')
    TA = vis & (lab == 'air'); PA = vis & air
    tp = (TA & PA).sum()
    P, R = tp / max(1, PA.sum()), tp / max(1, TA.sum())
    tr = [(a, b) for a, b in runs_of(inc & (lab == 'air'))]
    hit = sum(air[a:b + 1].mean() >= 0.5 for a, b in tr)
    fr = sum(not np.isin(lab[a:b + 1], ('air', 'x')).any() for a, b in runs_of(inc & air))
    E, EA, cov = [], [], 0; nf = 0
    for f in FL:
        if clipof[f['f0'] - A0] % 2 != par:
            continue
        Tk = S.track(f)
        if len(Tk) < 2:
            continue
        fr_ = np.arange(int(Tk[0, 0]) + 1, int(Tk[-1, 0])) - A0
        if not len(fr_):
            continue
        nf += 1
        tx = np.interp(fr_, Tk[:, 0] - A0, Tk[:, 1]); ty = np.interp(fr_, Tk[:, 0] - A0, Tk[:, 2])
        use = np.where(ok_air[fr_][:, None], xy[fr_], flat[fr_])
        v = ok_air[fr_] | has[fr_]
        e = np.hypot(use[:, 0] - tx, use[:, 1] - ty)
        if v.any():
            E.append(np.median(e[v]))
        if ok_air[fr_].any():
            cov += 1; EA.append(np.median(e[ok_air[fr_]]))
    return ('%-26s %s  air P %.2f R %.2f F %.2f hit %2d/%2d false %2d | pos med %5.1f  air-only %4.1f on %2d/%2d' % (
        p[-26:], 'dev ' if par == 0 else 'HOLD', P, R, 2 * P * R / max(P + R, 1e-9), hit, len(tr), fr,
        np.median(E), np.median(EA) if EA else np.nan, cov, nf))


if __name__ == '__main__':
    hold = '--holdout' in sys.argv
    for p in [a for a in sys.argv[1:] if not a.startswith('--')]:
        print(score(p, 0))
        if hold:
            print(score(p, 1))
