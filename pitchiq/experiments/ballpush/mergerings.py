"""Merge the ring exports of two runs of the same half (ball push idea c25).

Rule "drop" (the default): a frame keeps a ring if either run rings there.  Where both runs ring and sit more than
TOL px apart, the frame gets no ring (a wrong ring is worse than a missed one).  Where both ring within TOL, run A's
position is kept.  Rule "keepA" keeps A's ring on disagreement instead.

Measured 2026-11-16/17 (15 px, answered clicks):
  dev, seven halves, shipped candidates + finder-rule candidates: 211 / 8 / 156 and 211 / 7 / 157 -> 220 / 8 / 147
  held out (look #17), round-1 + round-3 models: 128 / 12 / 126 and 124 / 10 / 132 -> 133 / 12 / 121
The merge inherits both runs' wrong rings: with a poor run (c24, 167 / 13 / 142) dev went 171 / 8 / 141 -> 173 / 17 / 133.

Usage: python -m pitchiq.experiments.ballpush.mergerings A.npz B.npz OUT.npz [--rule drop|keepA] [--tol 15]
Each npz holds abs (absolute frame per row), x, y, src, ring, as written by tnclick.py score with EXPORT set.
"""
import argparse

import numpy as np


def merge(a, b, rule='drop', tol=15.0):
    """a, b: dicts (or npz) with abs, x, y, src, ring.  Returns a dict over the frames both runs cover."""
    a0, b0 = int(a['abs'][0]), int(b['abs'][0])
    lo, hi = max(a0, b0), min(a0 + len(a['ring']), b0 + len(b['ring']))
    if hi <= lo:
        raise ValueError('runs do not overlap')
    sa, sb = slice(lo - a0, hi - a0), slice(lo - b0, hi - b0)
    if not (np.array_equal(a['abs'][sa], b['abs'][sb])):
        raise ValueError('frame numbers differ between the runs')
    ra, rb = np.asarray(a['ring'][sa], bool), np.asarray(b['ring'][sb], bool)
    xa, ya, xb, yb = (np.asarray(v, float) for v in (a['x'][sa], a['y'][sa], b['x'][sb], b['y'][sb]))
    both = ra & rb
    far = both & (np.hypot(xa - xb, ya - yb) > tol)
    useb = rb & ~ra
    x, y = np.where(useb, xb, xa), np.where(useb, yb, ya)
    src = np.where(useb, b['src'][sb], a['src'][sa]).astype(np.int8)
    ring = ra | rb
    if rule == 'drop':
        ring = ring & ~far
    elif rule != 'keepA':
        raise ValueError('rule must be drop or keepA')
    x, y = np.where(ring, x, np.nan), np.where(ring, y, np.nan)
    return {'abs': np.asarray(a['abs'][sa]), 'x': x, 'y': y, 'src': src, 'ring': ring,
            'stats': dict(a=int(ra.sum()), b=int(rb.sum()), both=int(both.sum()), apart=int(far.sum()),
                          out=int(ring.sum()))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('a'); ap.add_argument('b'); ap.add_argument('out')
    ap.add_argument('--rule', default='drop'); ap.add_argument('--tol', type=float, default=15.0)
    o = ap.parse_args()
    m = merge(np.load(o.a), np.load(o.b), o.rule, o.tol)
    st = m.pop('stats')
    np.savez_compressed(o.out, **m)
    print('rings A %(a)d B %(b)d both %(both)d apart %(apart)d -> merged %(out)d' % st)


if __name__ == '__main__':
    main()
