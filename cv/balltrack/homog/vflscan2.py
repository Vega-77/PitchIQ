"""Scan every (start, length) window with the plain arc AND the drag model,
keeping outlier-proof (trimmed) errors, so the gates can be tuned offline.

    python vflscan2.py <tag> [procs] [limit_starts]
        -> <tag>_scan2.npz  S: one row per window, COLS below
"""
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
from scipy.optimize import least_squares

import vflight as V
import vdrag as D
import vflscan as S1

LENS = tuple(range(6, 76, 3))
STEP = 2
SCREEN = 20.0        # px trimmed: windows the arc misses worse than this skip the drag fit
COLS = ('a b n q_rms q_trim q_apex r_rms r_trim d_rms d_trim d_apex '
        'u0 v0 vu vv tau Tf k').split()


def trim(e, keep=0.8):
    e = np.sort(e ** 2)
    return float(np.sqrt(np.mean(e[:max(3, int(np.ceil(keep * len(e))))])))


def roll(tr, a, b):
    idx = np.arange(a, b + 1); idx = idx[tr.has[idx]]
    t = (idx - a) / V.FPS
    Z = np.zeros_like(t); O = np.ones_like(t)
    Arow = np.stack([np.stack([O, Z, t, Z], 1), np.stack([Z, O, Z, t], 1)], 1)
    Am = np.einsum('kij,kjl->kil', tr.J[idx], Arow).reshape(-1, 4)
    bm = np.einsum('kij,kj->ki', tr.J[idx], tr.g[idx]).ravel()
    th = np.linalg.lstsq(Am, bm, rcond=None)[0]
    e = np.sqrt(np.sum(((Am.dot(th) - bm).reshape(-1, 2)) ** 2, 1))
    return float(np.sqrt(np.mean(e ** 2))), trim(e)


def dfit(tr, a, b, kmax=1.5):
    """Drag fit, 2 starts (cheaper than vdrag.fit's 6)."""
    idx = np.arange(a, b + 1); idx = idx[tr.has[idx]]
    t = (idx - a) / V.FPS; T = (b - a) / V.FPS
    ga, gb = tr.g[idx[0]], tr.g[idx[-1]]; obs = tr.px[idx]
    lo = [-np.inf] * 4 + [-0.1, T * 0.8, 0.0]; hi = [np.inf] * 4 + [0.05, T + 0.15, kmax]
    res = lambda th: ((tr.project(idx, D.drag(th, t)) - obs) / V.SIG).ravel()
    best = None
    for Tf, k in ((T, 0.1), (T + 0.1, 0.6)):
        th0 = np.clip([ga[0], ga[1], (gb[0] - ga[0]) / Tf, (gb[1] - ga[1]) / Tf, 0.0, Tf, k],
                      np.add(lo, 1e-6), np.subtract(hi, 1e-6))
        try:
            s = least_squares(res, th0, bounds=(lo, hi), loss='soft_l1', f_scale=2.0, max_nfev=200)
        except Exception:
            continue
        if best is None or s.cost < best.cost:
            best = s
    if best is None:
        return None
    th = best.x
    e = np.hypot(*(tr.project(idx, D.drag(th, t)) - obs).T)
    apex = D.drag(th, th[4] + np.linspace(0, th[5], 40))[:, 2].max()
    return th, float(np.sqrt(np.mean(e ** 2))), trim(e), float(apex)


_TR = None


def work(args):
    global _TR
    tag, starts = args
    if _TR is None:
        _TR = V.Track(tag); V.ground_all(_TR)
    tr = _TR; n = len(tr.abs); out = []
    for a in starts:
        for L in LENS:
            b = a + L
            if b >= n or not tr.has[b]:
                continue
            k = tr.has[a:b + 1].sum()
            if k < max(6, 0.7 * (L + 1)):
                continue
            q = S1.quick_fit(tr, a, b)
            if q is None:
                continue
            idx = np.arange(a, b + 1); idx = idx[tr.has[idx]]
            eq = np.hypot(*(tr.project(idx, V.ballistic(q[0], (idx - a) / V.FPS)) - tr.px[idx]).T)
            qt = trim(eq)
            rr, rt = roll(tr, a, b)
            row = [a, b, k, q[1], qt, V.G * q[0][5] ** 2 / 8, rr, rt]
            d = dfit(tr, a, b) if qt <= SCREEN else None
            if d is None:
                row += [np.nan] * 3 + [np.nan] * 7
            else:
                th, dr, dt, ap = d
                row += [dr, dt, ap] + list(th)
            out.append(row)
    return out


def main():
    tag = sys.argv[1]
    procs = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    lim = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    tr = V.Track(tag); V.ground_all(tr)
    starts = [a for a in range(0, len(tr.abs), STEP) if tr.has[a]]
    if lim:
        starts = starts[:lim]
    chunks = [(tag, starts[i::procs * 4]) for i in range(procs * 4)]
    t0 = time.time()
    with Pool(procs) as p:
        S = np.array(sum(p.map(work, chunks), []))
    out = os.path.join(V.HERE, '%s_scan2%s.npz' % (tag, '_lim' if lim else ''))
    np.savez(out, S=S, cols=np.array(COLS))
    print('%d starts, %d windows, %d drag-fitted  (%.0f s)' % (
        len(starts), len(S), np.isfinite(S[:, 8]).sum(), time.time() - t0), flush=True)


if __name__ == '__main__':
    main()
