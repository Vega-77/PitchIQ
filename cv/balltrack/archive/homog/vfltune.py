"""Tune the flight gates on Air Lab labels: fit on even clips, report odd.

    python vfltune.py [tag]
"""
import itertools
import json
import os
import sys

import numpy as np

import vflight as V
import vdrag as D

HERE = os.path.dirname(os.path.abspath(__file__))
tag = sys.argv[1] if len(sys.argv) > 1 else 'vid1'
Z = np.load(os.path.join(HERE, '%s_scan2.npz' % tag))
S, C = Z['S'], {c: i for i, c in enumerate(Z['cols'].tolist())}
S = S[np.isfinite(S[:, C['d_rms']])]
T = np.load(os.path.join(HERE, 'airtruth.npz'))
A0 = int(T['abs'][0])
truth, tfid = T['truth'], T['fid']
n = len(truth)
tr = V.Track(tag)
off = A0 - int(tr.abs[0])          # truth index = track index - off
assert off == 0, off

# per-window take-off / landing time (track frames) and pitch check
TH = S[:, [C[c] for c in 'u0 v0 vu vv tau Tf k'.split()]]
t_take = S[:, C['a']] + TH[:, 4] * V.FPS
t_land = S[:, C['a']] + (TH[:, 4] + TH[:, 5]) * V.FPS
P0 = np.array([D.drag(th, np.array([th[4]]))[0, :2] for th in TH])
P1 = np.array([D.drag(th, np.array([th[4] + th[5]]))[0, :2] for th in TH])
sb0, sb1 = tr.to_sb(P0), tr.to_sb(P1)
SL = 10
onpitch = ((sb0[:, 0] > -SL) & (sb0[:, 0] < 120 + SL) & (sb0[:, 1] > -SL) & (sb0[:, 1] < 80 + SL)
           & (sb1[:, 0] > -SL) & (sb1[:, 0] < 120 + SL) & (sb1[:, 1] > -SL) & (sb1[:, 1] < 80 + SL))

man = json.load(open(os.path.join(HERE, 'airlab', 'manifest.json')))
split = np.full(n, -1)
for i, c in enumerate(man['clips']):
    split[max(0, c['start'] - A0):min(n, c['end'] - A0 + 1)] = i % 2
vis = truth != 'hidden'
Tair = truth == 'air'

# true flights
TF = []
for k in range(tfid.max() + 1):
    idx = np.where(tfid == k)[0]
    if len(idx):
        TF.append((idx[0], idx[-1], split[idx[0]]))


def select(ok, w):
    idx = np.where(ok)[0]
    if not len(idx):
        return idx
    a = S[idx, C['a']]; b = S[idx, C['b']]
    o = np.argsort(b); idx, a, b, w = idx[o], a[o], b[o], w[idx][o]
    p = np.searchsorted(b, a, side='right')
    best = np.zeros(len(idx) + 1)
    for j in range(len(idx)):
        best[j + 1] = max(best[j], best[p[j]] + w[j])
    ch = []; j = len(idx)
    while j > 0:
        if best[j] == best[j - 1]:
            j -= 1
        else:
            ch.append(idx[j - 1]); j = p[j - 1]
    return np.array(ch[::-1], int)


def frames(ch):
    pa = np.zeros(n, bool)
    for r in ch:
        a = max(int(np.floor(t_take[r])), int(S[r, C['a']]) - 6, 0)
        b = min(int(np.ceil(t_land[r])), int(S[r, C['b']]) + 6, n - 1)
        pa[a:b + 1] = True
    return pa


def score(ch, part):
    pa = frames(ch)
    m = vis & (split == part)
    tp = (pa & Tair & m).sum(); fp = (pa & ~Tair & m).sum(); fn = (~pa & Tair & m).sum()
    P = tp / max(1, tp + fp); R = tp / max(1, tp + fn); F = 2 * P * R / max(1e-9, P + R)
    tf = [f for f in TF if f[2] == part]
    hit = sum(pa[a:b + 1].mean() >= 0.5 for a, b, _ in tf)
    det = [(max(int(np.floor(t_take[r])), 0), int(np.ceil(t_land[r]))) for r in ch
           if split[min(n - 1, int(S[r, C['a']]))] == part]
    false = sum(not Tair[a:b + 1].any() for a, b in det)
    return dict(P=P, R=R, F=F, hit=hit, nt=len(tf), nd=len(det), false=false)


def run(model, E, AP, RR, RM, c):
    e = S[:, C[model + '_trim']]
    ap = S[:, C[model + '_apex']]
    ok = (e <= E) & (ap >= AP) & (S[:, C['r_trim']] >= np.maximum(RR * e, RM)) & onpitch
    return select(ok, S[:, C['n']] - c * e)


grid = list(itertools.product(('q', 'd'), (2, 3, 4, 5, 6, 8), (0.03, 0.05, 0.08, 0.12),
                              (1.2, 1.5, 2, 2.5, 3), (1.5, 2.5, 4), (0.5, 1.5)))
res = []
for g in grid:
    ch = run(*g)
    res.append((score(ch, 0), g))
res.sort(key=lambda r: -r[0]['F'])
fmt = lambda s: 'F %.2f P %.2f R %.2f | flights %d/%d, detected %d, false %d' % (
    s['F'], s['P'], s['R'], s['hit'], s['nt'], s['nd'], s['false'])
print('top on train (even clips):')
for s, g in res[:8]:
    print('  %-40s %s' % (g, fmt(s)))
g = res[0][1]
ch = run(*g)
print('\nchosen %s' % (g,))
print('  train: ' + fmt(score(ch, 0)))
print('  TEST:  ' + fmt(score(ch, 1)))
old = ('q', 3.0, 0.08, 2.5, 4.0, 0.5)
cho = run(*old)
print('old gates %s\n  train: %s\n  test:  %s' % (old, fmt(score(cho, 0)), fmt(score(cho, 1))))
np.savez(os.path.join(HERE, '%s_tuned.npz' % tag), chosen=ch, gates=np.array(g, object))
