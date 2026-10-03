"""Refit the chosen flights with each end either ON THE GROUND (take-off /
landing at the end of the seen stretch, as before) or IN THE AIR (a touch,
or the ball lost from view: the arc runs on past the seen stretch and its
take-off / landing is inferred).  An end may be in the air only where it is
open: another flight starts or ends there, or the ball is not seen beyond it.

    python vflfix.py [tag]  -> <tag>_fixed.npy (list of flight dicts)
"""
import os
import sys

import numpy as np
from scipy.optimize import least_squares

import vflight as V
import vdrag as D

tag = sys.argv[1] if len(sys.argv) > 1 else 'vid1'
HERE = V.HERE
EXT = 1.5         # s: how far an in-air end may run past the seen stretch
LOST = 6          # frames without the ball beyond an end = ball lost there
GAIN = 0.75       # an in-air end must cut the error to this fraction ...
GAIN_PX = 1.0     # ... or by this many px


def trim(e, keep=0.8):
    e = np.sort(e ** 2)
    return float(np.sqrt(np.mean(e[:max(3, int(np.ceil(keep * len(e))))])))


def ray_point(tr, i, z):
    r = tr.M[i].dot([tr.px[i, 0], tr.px[i, 1], 1.0])
    return r[:2] / r[2] * (1 - z)


def radial(th):
    """+1 moving away from the camera (camera sits over uv = 0), -1 towards."""
    P = D.drag(th, np.array([th[4], th[4] + th[5]]))
    return np.sign(np.hypot(*P[1, :2]) - np.hypot(*P[0, :2]))


def fit_mode(tr, a, b, m0, m1):
    idx = np.arange(a, b + 1); idx = idx[tr.has[idx]]
    t = (idx - a) / V.FPS
    obs = tr.px[idx]
    i0, i1 = idx[0], idx[-1]
    t0, t1 = (i0 - a) / V.FPS, (i1 - a) / V.FPS
    # take-off: near the first seen frame if on the ground, before it if in the air
    tau_lo, tau_hi = (t0 - 0.1, t0 + 0.05) if m0 == 'g' else (t0 - EXT, t0 - 1 / V.FPS)
    # landing time: near the last seen frame, or after it
    land_lo, land_hi = (t1 - 0.05, t1 + 0.15) if m1 == 'g' else (t1 + 1 / V.FPS, t1 + EXT)
    lo = [-np.inf] * 4 + [tau_lo, max(0.15, land_lo - tau_hi), 0.0]
    hi = [np.inf] * 4 + [tau_hi, land_hi - tau_lo, 1.5]

    def res(th):
        r = ((tr.project(idx, D.drag(th, t)) - obs) / V.SIG).ravel()
        land = th[4] + th[5]                     # soft keep landing inside its range
        pen = max(0, land_lo - land) + max(0, land - land_hi)
        return np.r_[r, 30 * pen]
    best = None
    for z0 in ((0.0,) if m0 == 'g' else (0.1, 0.3, 0.7)):
        for z1 in ((0.0,) if m1 == 'g' else (0.1, 0.3, 0.7)):
            P0, P1 = ray_point(tr, i0, z0), ray_point(tr, i1, z1)
            s0 = 0.0 if m0 == 'g' else 0.6 * np.sqrt(2 * z0 / V.G)
            s1 = 0.0 if m1 == 'g' else 0.6 * np.sqrt(2 * z1 / V.G)
            vel = (P1 - P0) / max(t1 - t0, 0.1)
            u0 = P0 - vel * s0
            th0 = np.clip([u0[0], u0[1], vel[0], vel[1], t0 - s0, (t1 + s1) - (t0 - s0), 0.2],
                          np.add(lo, 1e-6), np.subtract(hi, 1e-6))
            try:
                s = least_squares(res, th0, bounds=(lo, hi), loss='soft_l1', f_scale=2.0, max_nfev=300)
            except Exception:
                continue
            if best is None or s.cost < best.cost:
                best = s
    th = best.x
    e = np.hypot(*(tr.project(idx, D.drag(th, t)) - obs).T)
    return th, trim(e)


def main():
    tr = V.Track(tag); V.ground_all(tr)
    n = len(tr.abs)
    Z = np.load(os.path.join(HERE, '%s_scan2.npz' % tag))
    S, C = Z['S'], {c: i for i, c in enumerate(Z['cols'].tolist())}
    S = S[np.isfinite(S[:, C['d_rms']])]
    ch = np.load(os.path.join(HERE, '%s_tuned.npz' % tag), allow_pickle=True)['chosen']
    rows = sorted(ch, key=lambda r: S[r, C['a']])
    AB = [(int(S[r, C['a']]), int(S[r, C['b']])) for r in rows]
    out = []
    for k, (a, b) in enumerate(AB):
        nb0 = k > 0 and AB[k - 1][1] >= a - 1
        nb1 = k + 1 < len(AB) and AB[k + 1][0] <= b + 1
        lost0 = not tr.has[max(0, a - LOST):a].any()
        lost1 = not tr.has[b + 1:min(n, b + 1 + LOST)].any()
        open0, open1 = nb0 or lost0, nb1 or lost1
        fits = {}
        for m0 in ('g', 'a') if open0 else ('g',):
            for m1 in ('g', 'a') if open1 else ('g',):
                fits[m0 + m1] = fit_mode(tr, a, b, m0, m1)
        mode = 'gg'
        base = fits['gg'][1]
        good = lambda e, ref: e <= GAIN * ref or e <= ref - GAIN_PX
        for m in ('ag', 'ga'):
            if m in fits and good(fits[m][1], fits[mode][1]):
                if fits[m][1] < fits[mode][1]:
                    mode = m
        if 'aa' in fits and good(fits['aa'][1], fits[mode][1]):
            mode = 'aa'
        th, err = fits[mode]
        out.append(dict(a=a, b=b, th=th, err=err, mode=mode, err_gg=base,
                        th_old=S[rows[k], [C[c] for c in 'u0 v0 vu vv tau Tf k'.split()]],
                        open=(open0, open1), why=('touch' if nb0 else 'lost' if lost0 else '-',
                                                  'touch' if nb1 else 'lost' if lost1 else '-')))
    # report + 3D continuity at shared boundaries
    print(' no  frames        mode  err(gg->new)  dir old->new   take -> land (SB)       ends')
    for k, f in enumerate(out):
        th = f['th']
        P = D.drag(th, np.array([th[4], th[4] + th[5]]))
        sb = tr.to_sb(P[:, :2])
        d0, d1 = radial(f['th_old']), radial(th)
        print('%3d  %5d-%5d   %s   %5.1f -> %5.1f   %s -> %s   (%5.1f,%5.1f) -> (%5.1f,%5.1f)   %s%s' % (
            k + 1, tr.abs[f['a']], tr.abs[f['b']], f['mode'], f['err_gg'], f['err'],
            'away' if d0 > 0 else 'twd ', 'away' if d1 > 0 else 'twd ',
            sb[0, 0], sb[0, 1], sb[1, 0], sb[1, 1], '/'.join(f['why']), '  <- dir changed' if d0 != d1 else ''))
    print('\n3D gap where one flight hands over to the next (SB units, height in circle radii):')
    gaps = {'old': [], 'new': []}
    for k in range(len(out) - 1):
        f, g = out[k], out[k + 1]
        if g['a'] > f['b'] + 1:
            continue
        j = g['a']
        for name, key in (('old', 'th_old'), ('new', 'th')):
            p = D.drag(f[key], np.array([(j - f['a']) / V.FPS]))[0]
            q = D.drag(g[key], np.array([(j - g['a']) / V.FPS]))[0]
            d = np.linalg.norm(tr.to_sb(p[None, :2])[0] - tr.to_sb(q[None, :2])[0])
            gaps[name].append(d)
        print('  %d|%d  old %5.1f  new %5.1f   heights new %.2f / %.2f' % (
            k + 1, k + 2, gaps['old'][-1], gaps['new'][-1],
            D.drag(f['th'], np.array([(j - f['a']) / V.FPS]))[0, 2] / V.R_CH,
            D.drag(g['th'], np.array([(j - g['a']) / V.FPS]))[0, 2] / V.R_CH))
    print('median hand-over gap: old %.1f, new %.1f SB units' % (np.median(gaps['old']), np.median(gaps['new'])))
    print('modes:', {m: sum(f['mode'] == m for f in out) for m in ('gg', 'ag', 'ga', 'aa')})
    np.save(os.path.join(HERE, '%s_fixed.npy' % tag), np.array(out, dtype=object), allow_pickle=True)


if __name__ == '__main__':
    main()
