"""Feet specialist: route feet-zone candidates to a model trained only on them (train3 --zone feet).

    python feetmerge.py <base run> <feet run> <out run> [--shift 0] [--near 0.1] [--map lin|quant]

For every dev piece: raw = base raw outside the feet zone; inside it the feet model's raw, put on the base
model's in-zone scale by a label-free map (pooled over all dev feet-zone candidates: the feet model's score
quantiles onto the base model's), plus --shift.  Writes runs/<out>/raw/; then  train.py finish <out> --cal global.

Also prints the direct test of the idea, on the clicked balls whose candidate is in the feet zone: how often the
ball is the top feet-zone candidate of its frame, and the top candidate of the whole frame, under each model.
"""
import argparse
import os

import numpy as np

import st
import train as T1
import train3 as T3

Q = np.linspace(1, 99, 99)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('base')
    ap.add_argument('feet')
    ap.add_argument('out')
    ap.add_argument('--shift', type=float, default=0.0)
    ap.add_argument('--near', type=float, default=0.1)
    ap.add_argument('--map', default='lin')
    ap.add_argument('--mode', default='feet')  # feet | player (train3.feet_zone)
    a = ap.parse_args()
    _, names, _ = T1.base_data()
    R = st.OUT + '/runs/'
    pts = [pt for pt in st.pieces() if os.path.exists(R + a.feet + '/raw/%s.npy' % pt)]
    Z, B, F = {}, {}, {}
    for pt in pts:
        ca = st.load_cache(pt, crops=False)
        Z[pt] = T3.feet_zone(ca['ctx'], names, a.near, a.mode)
        B[pt] = np.load(R + a.base + '/raw/%s.npy' % pt)
        F[pt] = np.load(R + a.feet + '/raw/%s.npy' % pt)
    bz = np.concatenate([B[pt][Z[pt]] for pt in pts])
    fz = np.concatenate([F[pt][Z[pt]] for pt in pts])
    if a.map == 'lin':
        aa, bb = np.polyfit(np.percentile(fz, Q), np.percentile(bz, Q), 1)
        fmap = lambda x: aa * x + bb
        print('feet zone: %d of %d candidates; map feet -> base: %.3f x + %.3f' % (
            len(bz), sum(len(B[pt]) for pt in pts), aa, bb))
    else:
        qf, qb = np.percentile(fz, np.linspace(0, 100, 1001)), np.percentile(bz, np.linspace(0, 100, 1001))
        fmap = lambda x: np.interp(x, qf, qb)
        print('feet zone: %d candidates; quantile map' % len(bz))
    os.makedirs(R + a.out + '/raw', exist_ok=True)
    M = {}
    for pt in pts:
        m = B[pt].copy()
        m[Z[pt]] = fmap(F[pt][Z[pt]]) + a.shift
        M[pt] = m.astype(np.float32)
        np.save(R + a.out + '/raw/%s.npy' % pt, M[pt])
    # direct test on clicked balls in the zone
    cnt = dict(n=0, zb=0, zf=0, ab=0, af=0)
    keys = {}
    for tag, pt, f, v, n, jb in T1.click_index():
        if v != 'ball' or jb < 0 or pt not in Z:
            continue
        if pt not in keys:
            ca = st.load_cache(pt, crops=False)
            keys[pt] = ca['f'] * 64 + ca['j']
        rows = np.searchsorted(keys[pt], f * 64 + np.arange(n))
        z = Z[pt][rows]
        if not z[jb]:
            continue
        cnt['n'] += 1
        b, mm = B[pt][rows], M[pt][rows]
        cnt['zb'] += int(not (b[z] > b[jb]).sum())
        cnt['zf'] += int(not (mm[z] > mm[jb]).sum())
        cnt['ab'] += int(not (b > b[jb]).sum())
        cnt['af'] += int(not (mm > mm[jb]).sum())
    print('clicked balls in the feet zone: %d | top of the feet zone: base %d, feet %d | top of the frame: base %d, '
          'merged %d' % (cnt['n'], cnt['zb'], cnt['zf'], cnt['ab'], cnt['af']))
    print('MERGE DONE', a.out)


if __name__ == '__main__':
    main()
