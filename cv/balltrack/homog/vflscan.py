"""Find the ball's flights: scan ballistic fits over every (start, length)
window, keep the ones a flight explains and a ground roll does not, pick a
non-overlapping set, and write every frame's ball in 3D.

    python vflscan.py <tag> [procs]
        -> <tag>_scan.npz     every window's fit
           <tag>_flights.npz  the chosen flights
           <tag>_ball3d.npz / .csv  per frame: SB x, y, height, state
"""
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
from scipy.optimize import least_squares

import vflight as V
import vdrag as D

LENS = tuple(range(9, 76, 3))
STEP = 2
MAX_RMS = 3.0        # px: a flight must explain the window this well
MIN_APEX = 0.08      # camera heights: lower than this is a roll
ROLL_RATIO = 2.5     # ground roll must be this much worse ...
ROLL_MIN = 4.0       # ... and at least this bad (px)
FILL_MAX = 90        # frames: straight-line fallback across unexplained gaps


def quick_fit(tr, a, b):
    idx = np.arange(a, b + 1)
    idx = idx[tr.has[idx]]
    t = (idx - a) / V.FPS
    T = (b - a) / V.FPS
    ga, gb = tr.g[idx[0]], tr.g[idx[-1]]
    obs = tr.px[idx]
    lo = [-np.inf] * 4 + [-0.1, T * 0.8]
    hi = [np.inf] * 4 + [0.05, T + 0.15]

    def res(th):
        return ((tr.project(idx, V.ballistic(th, t)) - obs) / V.SIG).ravel()
    best = None
    for Tf, tau in ((T, 0.0), (T + 0.1, -0.05)):
        th0 = np.clip([ga[0], ga[1], (gb[0] - ga[0]) / Tf, (gb[1] - ga[1]) / Tf, tau, Tf],
                      np.add(lo, 1e-6), np.subtract(hi, 1e-6))
        try:
            s = least_squares(res, th0, bounds=(lo, hi), loss='soft_l1', f_scale=2.0, max_nfev=150)
        except Exception:
            continue
        if best is None or s.cost < best.cost:
            best = s
    if best is None:
        return None
    e = np.hypot(*(tr.project(idx, V.ballistic(best.x, t)) - obs).T)
    return best.x, float(np.sqrt(np.mean(e ** 2))), float(np.median(e))


def roll_fit(tr, a, b):
    idx = np.arange(a, b + 1)
    idx = idx[tr.has[idx]]
    t = (idx - a) / V.FPS
    Z = np.zeros_like(t); O = np.ones_like(t)
    Arow = np.stack([np.stack([O, Z, t, Z], 1), np.stack([Z, O, Z, t], 1)], 1)
    Am = np.einsum('kij,kjl->kil', tr.J[idx], Arow).reshape(-1, 4)
    bm = np.einsum('kij,kj->ki', tr.J[idx], tr.g[idx]).ravel()
    th = np.linalg.lstsq(Am, bm, rcond=None)[0]
    e = (Am.dot(th) - bm).reshape(-1, 2)
    return float(np.sqrt(np.mean(np.sum(e ** 2, 1))))


_TR = None


def work(args):
    global _TR
    tag, starts = args
    if _TR is None:
        _TR = V.Track(tag)
        V.ground_all(_TR)
    tr = _TR
    n = len(tr.abs)
    out = []
    for a in starts:
        for L in LENS:
            b = a + L
            if b >= n or not tr.has[b]:
                continue
            k = tr.has[a:b + 1].sum()
            if k < max(8, 0.7 * (L + 1)):
                continue
            r = quick_fit(tr, a, b)
            if r is None:
                continue
            th, rms, med = r
            apex = V.G * th[5] ** 2 / 8
            out.append((a, b, k, rms, med, apex, roll_fit(tr, a, b), *th))
    return out


def select(S):
    """Weighted interval scheduling over valid windows (weight = obs)."""
    ok = ((S[:, 3] <= MAX_RMS) & (S[:, 5] >= MIN_APEX)
          & (S[:, 6] >= np.maximum(ROLL_RATIO * S[:, 3], ROLL_MIN)))
    C = S[ok]
    C = C[np.argsort(C[:, 1])]
    ends = C[:, 1]
    # p[j]: how many windows end at or before window j starts (a bounce
    # frame may be shared by the flight before and after it)
    p = np.searchsorted(ends, C[:, 0], side='right')
    w = C[:, 2] - 0.5 * C[:, 3]
    best = np.zeros(len(C) + 1)
    for j in range(len(C)):
        best[j + 1] = max(best[j], best[p[j]] + w[j])
    chosen = []
    j = len(C)
    while j > 0:
        if best[j] == best[j - 1]:
            j -= 1
        else:
            chosen.append(C[j - 1]); j = p[j - 1]
    return np.array(chosen[::-1]), ok.sum()


def main():
    tag = sys.argv[1]
    procs = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 6
    tr = V.Track(tag)
    V.ground_all(tr)
    n = len(tr.abs)
    starts = [a for a in range(0, n, STEP) if tr.has[a]]
    chunks = [(tag, starts[i::procs * 4]) for i in range(procs * 4)]
    t0 = time.time()
    sp = os.path.join(V.HERE, '%s_scan.npz' % tag)
    if 'reuse' in sys.argv and os.path.exists(sp):
        S = np.load(sp)['S']
    else:
        with Pool(procs) as p:
            S = np.array(sum(p.map(work, chunks), []))
        np.savez(sp, S=S)
    F, nvalid = select(S)
    print('scan: %d windows, %d valid, %d flights chosen  (%.0f s)' % (len(S), nvalid, len(F), time.time() - t0), flush=True)

    # refine: drag model, ends free to slide to the real take-off / landing
    out = [f for f in (refine(tr, int(r[0]), int(r[1])) for r in F) if f is not None]
    out.sort(key=lambda f: f['t_take'])
    keep = []
    for f in out:                       # overlaps after sliding: keep the better
        if keep and f['t_take'] < keep[-1]['t_land'] - 1:
            if f['n'] - 0.5 * f['rms'] > keep[-1]['n'] - 0.5 * keep[-1]['rms']:
                keep[-1] = f
            continue
        keep.append(f)
    out = keep
    print('refined: %d flights kept, rms median %.1f px, drag k median %.2f /s' % (
        len(out), np.median([f['rms'] for f in out]), np.median([f['th'][6] for f in out])), flush=True)
    per_frame(tr, tag, out)


PITCH_SLACK = 10     # take-off / landing must be within this of the lines


def refine(tr, a, b):
    base = None; cands = []
    for da in (0, -3, -6):
        for db in (0, 3, 6, 9):
            aa, bb = a + da, b + db
            if aa < 0 or bb >= len(tr.abs) or not tr.has[aa] or not tr.has[bb]:
                continue
            th, e = D.fit(tr, aa, bb)
            rms = float(np.sqrt(np.mean(e ** 2)))
            if da == 0 and db == 0:
                base = rms
            cands.append((rms, aa, bb, th, len(e)))
    if not cands or base is None:
        return None
    lim = max(MAX_RMS, 1.2 * base)
    ok = [c for c in cands if c[0] <= lim]
    rms, aa, bb, th, n = max(ok, key=lambda c: (c[4], -c[0]))
    P = D.drag(th, np.array([th[4], th[4] + th[5]]))
    sb = tr.to_sb(P[:, :2])
    if not ((sb[:, 0] > -PITCH_SLACK) & (sb[:, 0] < 120 + PITCH_SLACK)
            & (sb[:, 1] > -PITCH_SLACK) & (sb[:, 1] < 80 + PITCH_SLACK)).all():
        return None
    zmax = D.drag(th, th[4] + np.linspace(0, th[5], 60))[:, 2].max()
    return dict(th=th, a=aa, b=bb, n=n, rms=rms, take=P[0, :2], land=P[1, :2],
                t_take=aa + th[4] * V.FPS, t_land=aa + (th[4] + th[5]) * V.FPS, apex=float(zmax))


def per_frame(tr, tag, fl):
    n = len(tr.abs)
    uv = tr.g.copy()                       # ground reading (z = 0)
    z = np.zeros(n); z[~tr.has] = np.nan
    state = np.where(tr.has, 'ground', 'none').astype('<U8')
    rows = []
    for r in fl:
        a0 = int(np.floor(r['t_take'])); b0 = int(np.ceil(r['t_land']))
        fr = np.arange(max(0, a0), min(n, b0 + 1))
        th = r['th']
        P = D.drag(th, np.clip((fr - r['a']) / V.FPS, th[4], th[4] + th[5]))
        uv[fr] = P[:, :2]; z[fr] = np.clip(P[:, 2], 0, None); state[fr] = 'air'
        ts, ls = tr.to_sb(r['take'][None])[0], tr.to_sb(r['land'][None])[0]
        rows.append((tr.abs[0] + r['t_take'], tr.abs[0] + r['t_land'], *ts, *ls, r['apex'], r['rms']))
    sb = np.full((n, 2), np.nan)
    m = np.isfinite(uv[:, 0])
    sb[m] = tr.to_sb(uv[m])
    # fallback: ground readings far off the pitch that no flight explains ->
    # straight line between the good ground points either side
    off = (state == 'ground') & ~((sb[:, 0] > -3) & (sb[:, 0] < 123) & (sb[:, 1] > -3) & (sb[:, 1] < 83))
    good = (state == 'air') | ((state == 'ground') & ~off)
    i = 0
    while i < n:
        if not off[i]:
            i += 1; continue
        j = i
        while j < n and (off[j] or state[j] == 'none'):
            j += 1
        pa = i - 1
        while pa >= 0 and not good[pa]:
            pa -= 1
        if pa >= 0 and j < n and good[j] and j - pa <= FILL_MAX and state[pa] == 'ground' and state[j] == 'ground':
            for k in range(pa + 1, j):
                w = (k - pa) / float(j - pa)
                sb[k] = (1 - w) * sb[pa] + w * sb[j]; z[k] = np.nan; state[k] = 'line'
        else:
            for k in range(i, j):
                if off[k]:
                    state[k] = 'out'; sb[k] = np.nan
        i = j
    np.savez(os.path.join(V.HERE, '%s_ball3d.npz' % tag), abs=tr.abs, x=sb[:, 0], y=sb[:, 1], z=z, state=state)
    np.savez(os.path.join(V.HERE, '%s_flights.npz' % tag), F=np.array(rows),
             cols='take_abs,land_abs,take_x,take_y,land_x,land_y,apex_ch,rms_px')
    R = V.R_CH
    with open(os.path.join(V.HERE, '%s_ball3d.csv' % tag), 'w') as fh:
        fh.write('frame,ball_x_sb,ball_y_sb,height_circle_radii,state\n')
        for k in range(n):
            f = lambda v: '' if not np.isfinite(v) else '%.1f' % v
            fh.write('%d,%s,%s,%s,%s\n' % (tr.abs[k], f(sb[k, 0]), f(sb[k, 1]),
                                           '' if not np.isfinite(z[k]) or state[k] != 'air' else '%.2f' % (z[k] / R), state[k]))
    u, c = np.unique(state, return_counts=True)
    print('frames by state:', dict(zip(u, c)))
    print('flights: %d  (median %.1f s, apex median %.2f circle radii)' % (
        len(rows), np.median([r[1] - r[0] for r in rows]) / V.FPS if rows else 0,
        np.median([r[6] for r in rows]) / R if rows else 0))


if __name__ == '__main__':
    main()
