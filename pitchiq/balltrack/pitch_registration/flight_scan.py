"""Scan every (start, length) window with the plain arc AND the drag model,
keeping outlier-proof (trimmed) errors, so the gates can be tuned offline.

    python -m pitchiq.balltrack.pitch_registration.flight_scan <tag> [procs] [limit_starts]
        -> <tag>_scan2.npz  S: one row per window, COLS below
"""

import os
import sys
import time
from multiprocessing import Pool

import numpy as np
from scipy.optimize import least_squares

from . import flight_model
from . import drag_flight
from . import flight_scan_ballistic

LENS = tuple(range(6, 76, 3))
STEP = 2
SCREEN = 20.0  # px trimmed: windows the arc misses worse than this skip the drag fit
COLS = ('a b n q_rms q_trim q_apex r_rms r_trim d_rms d_trim d_apex u0 v0 vu vv tau Tf k').split()


def trim(e, keep=0.8):
    """RMS of the smallest `keep` share of the errors e (at least 3 of them)."""
    e = np.sort(e**2)
    return float(np.sqrt(np.mean(e[: max(3, int(np.ceil(keep * len(e))))])))


def roll(tr, a, b):
    """Straight constant-speed roll over frames a..b, fitted on the ground plane: its pixel
    RMS error and trimmed error.
    """
    idx = np.arange(a, b + 1)
    idx = idx[tr.has[idx]]
    t = (idx - a) / flight_model.FPS
    Z = np.zeros_like(t)
    ONE = np.ones_like(t)
    Arow = np.stack([np.stack([ONE, Z, t, Z], 1), np.stack([Z, ONE, Z, t], 1)], 1)
    Am = np.einsum('kij,kjl->kil', tr.J[idx], Arow).reshape(-1, 4)
    bm = np.einsum('kij,kj->ki', tr.J[idx], tr.g[idx]).ravel()
    th = np.linalg.lstsq(Am, bm, rcond=None)[0]
    e = np.sqrt(np.sum(((Am.dot(th) - bm).reshape(-1, 2)) ** 2, 1))
    return float(np.sqrt(np.mean(e**2))), trim(e)


def dfit(tr, a, b, kmax=1.5):
    """Drag fit, 2 starts (cheaper than drag_flight.fit's 6)."""
    idx = np.arange(a, b + 1)
    idx = idx[tr.has[idx]]
    t = (idx - a) / flight_model.FPS
    T = (b - a) / flight_model.FPS
    ga, gb = tr.g[idx[0]], tr.g[idx[-1]]
    obs = tr.px[idx]
    lo = [-np.inf] * 4 + [-0.1, T * 0.8, 0.0]
    hi = [np.inf] * 4 + [0.05, T + 0.15, kmax]

    def res(th):
        return ((tr.project(idx, drag_flight.drag(th, t)) - obs) / flight_model.SIG).ravel()

    best = None
    for Tf, k in ((T, 0.1), (T + 0.1, 0.6)):
        th0 = np.clip(
            [ga[0], ga[1], (gb[0] - ga[0]) / Tf, (gb[1] - ga[1]) / Tf, 0.0, Tf, k],
            np.add(lo, 1e-6),
            np.subtract(hi, 1e-6),
        )
        try:
            s = least_squares(res, th0, bounds=(lo, hi), loss='soft_l1', f_scale=2.0, max_nfev=200)
        except Exception:
            continue
        if best is None or s.cost < best.cost:
            best = s
    if best is None:
        return None
    th = best.x
    e = np.hypot(*(tr.project(idx, drag_flight.drag(th, t)) - obs).T)
    apex = drag_flight.drag(th, th[4] + np.linspace(0, th[5], 40))[:, 2].max()
    return th, float(np.sqrt(np.mean(e**2))), trim(e), float(apex)


_TR = None


def work(args):
    """One pool process: every (start, length) window from `starts` -> rows of COLS.

    A window needs its end frame and 70% of its frames tracked.  Each row holds the plain
    arc's fit, the straight roll's, and (when the arc's trimmed error is within SCREEN) the
    drag fit and its parameters; NaN otherwise.
    """
    global _TR
    tag, starts = args
    if _TR is None:
        _TR = flight_model.Track(tag)
        flight_model.ground_all(_TR)
    tr = _TR
    n = len(tr.abs)
    out = []
    for a in starts:
        for L in LENS:
            b = a + L
            if b >= n or not tr.has[b]:
                continue
            k = tr.has[a : b + 1].sum()
            if k < max(6, 0.7 * (L + 1)):
                continue
            q = flight_scan_ballistic.quick_fit(tr, a, b)
            if q is None:
                continue
            idx = np.arange(a, b + 1)
            idx = idx[tr.has[idx]]
            eq = np.hypot(
                *(
                    tr.project(idx, flight_model.ballistic(q[0], (idx - a) / flight_model.FPS))
                    - tr.px[idx]
                ).T
            )
            qt = trim(eq)
            rr, rt = roll(tr, a, b)
            row = [a, b, k, q[1], qt, flight_model.G * q[0][5] ** 2 / 8, rr, rt]
            d = dfit(tr, a, b) if qt <= SCREEN else None
            if d is None:
                row += [np.nan] * 3 + [np.nan] * 7
            else:
                th, dr, dt, ap = d
                row += [dr, dt, ap] + list(th)
            out.append(row)
    return out


def main():
    """Command line: <tag> [procs] [limit_starts] - scan every window -> <tag>_scan2.npz
    (S, cols), or <tag>_scan2_lim.npz with a limit.
    """
    tag = sys.argv[1]
    procs = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    lim = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    tr = flight_model.Track(tag)
    flight_model.ground_all(tr)
    starts = [a for a in range(0, len(tr.abs), STEP) if tr.has[a]]
    if lim:
        starts = starts[:lim]
    # 32 pieces whatever the process count, so the rows come out in the same order
    chunks = [(tag, starts[i::32]) for i in range(32)]
    t0 = time.time()
    with Pool(procs) as p:
        # (n, cols) even when no window fits: a piece before kickoff has no ball at all
        S = np.array(sum(p.map(work, chunks), []), dtype=float).reshape(-1, len(COLS))
    out = os.path.join(flight_model.HERE, '%s_scan2%s.npz' % (tag, '_lim' if lim else ''))
    np.savez(out, S=S, cols=np.array(COLS))
    print(
        '%d starts, %d windows, %d drag-fitted  (%.0f s)'
        % (len(starts), len(S), np.isfinite(S[:, 8]).sum(), time.time() - t0),
        flush=True,
    )


if __name__ == '__main__':
    main()
