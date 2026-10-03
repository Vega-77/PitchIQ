"""The ball in 3D: ground rolls and ballistic flights from one camera.

Geometry.  The venue map's top-down plane (vtop) is in camera heights: the
camera sits at height 1 above the ground, and for frame f the matrix
M_f = inv(A) . Hsb_f  (A = vpitch.P . vtop.T) sends an image pixel to the
ray (u, v, w) in the world basis (e1, e2, -n).  A point at ground position
(u, v) and height z lies on the ray (u, v, 1 - z), so

    pixel  ~  inv(M_f) . (u, v, 1 - z)

z = 0 is the ground homography used so far; z > 0 is what it gets wrong.

Gravity in these units comes from the centre circle (its radius is 1.78
camera heights on the map and 10 yd on the pitch), so no length ever leaves
this file in metres.

    python vflight.py <tag>        -> <tag>_flights.npz, <tag>_ball3d.csv
"""
import os
import sys

import numpy as np
from scipy.optimize import least_squares

HERE = os.path.dirname(os.path.abspath(__file__))
FPS = 30.0
R_CH = 71.2 / 40.0                  # centre-circle radius in camera heights
G = 9.81 * R_CH / 9.15              # gravity, camera heights / s^2
SIG = 2.0                           # pixel noise of the tracker's ball


class Track:
    def __init__(self, tag):
        z = np.load(os.path.join(HERE, '%s_ballsb.npz' % tag))
        im = np.load(os.path.join(HERE, '%s_img.npz' % tag))
        t = np.load(os.path.join(HERE, 'vtop.npz'))
        p = np.load(os.path.join(HERE, 'vpitch.npz'))
        self.A = p['P'].dot(t['T'])                  # CH ground -> SB
        self.Ai = np.linalg.inv(self.A)
        self.abs = z['abs']
        self.M = np.einsum('ij,fjk->fik', self.Ai, z['H'])   # pixel -> CH ray
        for f in range(len(self.M)):                 # w > 0 for rays below
            if self.M[f].dot([960, 900, 1])[2] < 0:
                self.M[f] = -self.M[f]
        self.Mi = np.linalg.inv(self.M)
        self.px = np.c_[im['ix'], im['iy']]
        self.has = np.isfinite(self.px[:, 0])
        self.src_track = z['tracked']

    def ground(self, i):
        """The ground (z = 0) reading of observation i, in CH."""
        r = self.M[i].dot([self.px[i, 0], self.px[i, 1], 1.0])
        return r[:2] / r[2]

    def project(self, idx, P):
        """Pixels of 3D points P (n, 3: u, v, z) seen in frames idx."""
        q = np.c_[P[:, 0], P[:, 1], 1 - P[:, 2]]
        x = np.einsum('fij,fj->fi', self.Mi[idx], q)
        return x[:, :2] / x[:, 2:3]

    def to_sb(self, uv):
        q = np.c_[uv, np.ones(len(uv))].dot(self.A.T)
        return q[:, :2] / q[:, 2:3]


def ballistic(th, t):
    """th = (u0, v0, vu, vv, tau, Tf): takeoff at ground (u0, v0) at time tau,
    lands Tf later.  Height is exactly 0 at both ends and >= 0 between."""
    u0, v0, vu, vv, tau, Tf = th
    s = t - tau
    vz = G * Tf / 2
    z = np.where((s >= 0) & (s <= Tf), vz * s - 0.5 * G * s * s, 0.0)
    return np.c_[u0 + vu * s, v0 + vv * s, z]


def ground_all(tr):
    """Ground reading and d pixel / d ground Jacobian for every observation."""
    n = len(tr.abs)
    g = np.full((n, 2), np.nan)
    J = np.full((n, 2, 2), np.nan)
    for i in np.where(tr.has)[0]:
        g[i] = tr.ground(i)
        p0 = tr.project([i], np.r_[g[i], 0][None])[0]
        for c in range(2):
            d = np.zeros(2); d[c] = 1e-3
            J[i, :, c] = (tr.project([i], np.r_[g[i] + d, 0][None])[0] - p0) / 1e-3
    tr.g, tr.J = g, J
    return g, J


def roll_rms(tr, half=7):
    """For each observation: pixel rms of a constant-velocity ground roll
    fitted to the observations within +-half frames."""
    n = len(tr.abs)
    out = np.full(n, np.nan)
    for i in np.where(tr.has)[0]:
        idx = np.arange(max(0, i - half), min(n, i + half + 1))
        idx = idx[tr.has[idx]]
        if len(idx) < 6:
            continue
        t = (idx - i) / FPS
        Z = np.zeros_like(t); O = np.ones_like(t)
        Arow = np.stack([np.stack([O, Z, t, Z], 1), np.stack([Z, O, Z, t], 1)], 1)
        Am = np.einsum('kij,kjl->kil', tr.J[idx], Arow).reshape(-1, 4)
        bm = np.einsum('kij,kj->ki', tr.J[idx], tr.g[idx]).ravel()
        th = np.linalg.lstsq(Am, bm, rcond=None)[0]
        e = (Am.dot(th) - bm).reshape(-1, 2)
        out[i] = np.sqrt(np.mean(np.sum(e ** 2, 1)))
    return out


if __name__ == '__main__':
    import time
    tr = Track(sys.argv[1] if len(sys.argv) > 1 else 'vid1')
    t0 = time.time()
    ground_all(tr)
    rr = roll_rms(tr)
    print('roll rms px pct 50/75/90/95/99:', np.nanpercentile(rr, [50, 75, 90, 95, 99]).round(1),
          '(%.0f s)' % (time.time() - t0))
    np.save(os.path.join(HERE, 'vid1_rollrms.npy'), rr)


def chain_pos(th, K, t):
    """Chained flights: th = (u0, v0, t0, [vu, vv, Tf] * K).  Flight k
    starts where and when flight k-1 lands.  Returns (n, 3) positions for
    times t (seconds, same origin as t0); outside the chain z = 0 and the
    ball sits at the chain's start / end point."""
    u, v, tk = th[0], th[1], th[2]
    P = np.zeros((len(t), 3))
    P[:, 0], P[:, 1] = u, v
    for k in range(K):
        vu, vv, Tf = th[3 + 3 * k: 6 + 3 * k]
        s = t - tk
        m = s >= 0
        sc = np.clip(s, 0, Tf)
        P[m, 0] = u + vu * sc[m]
        P[m, 1] = v + vv * sc[m]
        P[m, 2] = np.where(s[m] <= Tf, G * Tf / 2 * sc[m] - 0.5 * G * sc[m] ** 2, 0)
        u, v, tk = u + vu * Tf, v + vv * Tf, tk + Tf
    return P


def fit_chain(tr, flights, anchor=4):
    """Joint fit of consecutive flights (dicts from fit_flight, in order).
    The last ground observation before takeoff and the first after the
    final landing (within `anchor` frames) pin the ends as z = 0 points."""
    K = len(flights)
    a = int(np.floor(flights[0]['t_take'])) - anchor
    b = int(np.ceil(flights[-1]['t_land'])) + anchor
    a, b = max(a, 0), min(b, len(tr.abs) - 1)
    ta, tb = flights[0]['t_take'], flights[-1]['t_land']
    idx = np.arange(a, b + 1)
    idx = idx[tr.has[idx]]
    # observations inside the chain, plus at most one anchor on each side
    before = idx[idx < ta]; after = idx[idx > tb]
    # only the frames the single fits explained (a gap between a landing and
    # the next take-off may hold a roll or a bad pick)
    inw = np.zeros(len(idx), bool)
    for f in flights:
        inw |= (idx >= f['a']) & (idx <= f['b'])
    use = idx[inw & (idx >= ta) & (idx <= tb)]
    use = np.r_[before[-1:], use, after[:1]]
    t = (use - a) / FPS
    obs = tr.px[use]
    th0 = [flights[0]['take'][0], flights[0]['take'][1], (ta - a) / FPS]
    lo = [-np.inf, -np.inf, th0[2] - 0.2]; hi = [np.inf, np.inf, th0[2] + 0.2]
    for f in flights:
        Tf = (f['t_land'] - f['t_take']) / FPS
        d = f['land'] - f['take']
        th0 += [d[0] / Tf, d[1] / Tf, Tf]
        lo += [-np.inf, -np.inf, Tf * 0.6]; hi += [np.inf, np.inf, Tf * 1.4 + 0.1]
    th0 = np.clip(th0, np.add(lo, 1e-6), np.subtract(hi, 1e-6))

    def res(th):
        return ((tr.project(use, chain_pos(th, K, t)) - obs) / SIG).ravel()
    s = least_squares(res, th0, bounds=(lo, hi), loss='soft_l1', f_scale=2.0, max_nfev=400)
    e = np.hypot(*(tr.project(use, chain_pos(s.x, K, t)) - obs).T)
    out = []
    u, v, tk = s.x[0], s.x[1], a + s.x[2] * FPS
    for k in range(K):
        vu, vv, Tf = s.x[3 + 3 * k: 6 + 3 * k]
        land = np.array([u + vu * Tf, v + vv * Tf])
        out.append(dict(take=np.array([u, v]), land=land, t_take=tk, t_land=tk + Tf * FPS,
                        apex=G * Tf * Tf / 8, vel=np.array([vu, vv])))
        u, v, tk = land[0], land[1], tk + Tf * FPS
    return dict(flights=out, th=s.x, a=a, K=K, rms=float(np.sqrt(np.mean(e ** 2))),
                med=float(np.median(e)))
