"""Cache: for every scan2 row, the ground speed (SB/s) a slowing roll would need
to explain its pixels, and that roll's trimmed px error.

    python -m pitchiq.balltrack.homog.vrowspd [tag] [procs]  -> <tag>_rowspd.npy

Rows are independent, so they are split over processes; the output is the same
whatever the count (8 by default)."""

import sys
from multiprocessing import Pool

import numpy as np

from . import flight_model as V
from . import vroll as R

_TR = None


def rows(args):
    global _TR
    tag, a0, a1 = args
    if _TR is None:
        _TR = V.Track(tag)
        V.ground_all(_TR)
    tr = _TR
    Z = np.load('%s_scan2.npz' % tag)
    S, C = Z['S'], {c: i for i, c in enumerate(Z['cols'].tolist())}
    out = np.full((a1 - a0, 2), np.nan)
    for r in range(a0, a1):
        if not np.isfinite(S[r, C['d_rms']]):
            continue
        a, b = int(S[r, C['a']]), int(S[r, C['b']])
        e, th = R.droll(tr, a, b)
        if th is None:
            continue
        uv0, v, k = th
        Tt = (b - a) / V.FPS
        E = Tt if k == 0 else (1 - np.exp(-k * Tt)) / k
        p = tr.to_sb(np.array([uv0, uv0 + v * E]))
        out[r - a0] = (np.hypot(*(p[1] - p[0])) / Tt, e)
    return out


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else 'vid1'
    procs = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    n = len(np.load('%s_scan2.npz' % tag)['S'])
    edges = np.linspace(0, n, procs * 4 + 1).astype(int)
    with Pool(procs) as p:
        parts = p.map(rows, [(tag, int(a), int(b)) for a, b in zip(edges[:-1], edges[1:])])
    out = np.concatenate(parts) if parts else np.full((0, 2), np.nan)
    np.save('%s_rowspd.npy' % tag, out)
    print('done', np.isfinite(out[:, 0]).sum())


if __name__ == '__main__':
    main()
