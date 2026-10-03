"""Flight with linear air drag k (1/s): velocity decays as exp(-k s)."""
import numpy as np
from scipy.optimize import least_squares
import vflight as V


def drag(th, t):
    """th = (u0, v0, vu, vv, tau, Tf, k); vu, vv = take-off horizontal speed.
    Height is 0 at s = 0 and s = Tf."""
    u0, v0, vu, vv, tau, Tf, k = th
    s = t - tau
    k = max(k, 1e-4)
    E = lambda x: (1 - np.exp(-k * x)) / k          # integral of exp(-k s)
    vz = V.G * Tf / (k * E(Tf)) - V.G / k
    sc = np.clip(s, 0, Tf)
    z = np.where((s >= 0) & (s <= Tf), (vz + V.G / k) * E(sc) - V.G * sc / k, 0.0)
    # outside the flight the ball rolls on with the speed it had at the ends
    h = E(sc) + np.where(s > Tf, (s - Tf) * np.exp(-k * Tf), 0) + np.where(s < 0, s, 0)
    return np.c_[u0 + vu * h, v0 + vv * h, z]


def fit(tr, a, b, kmax=1.5):
    idx = np.arange(a, b + 1); idx = idx[tr.has[idx]]
    t = (idx - a) / V.FPS; T = (b - a) / V.FPS
    ga, gb = tr.ground(idx[0]), tr.ground(idx[-1]); obs = tr.px[idx]
    lo = [-np.inf] * 4 + [-0.1, T * 0.8, 0.0]; hi = [np.inf] * 4 + [0.05, T + 0.15, kmax]
    res = lambda th: ((tr.project(idx, drag(th, t)) - obs) / V.SIG).ravel()
    best = None
    for Tf in (T, T + 0.1):
        for k in (0.05, 0.5, 1.0):
            k = min(k, kmax / 2)
            th0 = np.clip([ga[0], ga[1], (gb[0] - ga[0]) / Tf, (gb[1] - ga[1]) / Tf, 0.0, Tf, k],
                          np.add(lo, 1e-6), np.subtract(hi, 1e-6))
            s = least_squares(res, th0, bounds=(lo, hi), loss='soft_l1', f_scale=2.0, max_nfev=400)
            if best is None or s.cost < best.cost: best = s
    th = best.x
    e = np.hypot(*(tr.project(idx, drag(th, t)) - obs).T)
    return th, e


if __name__ == '__main__':
    tr = V.Track('vid1'); V.ground_all(tr)
    for a, b in [(1479, 1536), (33, 96), (968, 1038), (492, 510)]:
        for km in (0.0001, 1.5, 4.0):
            th, e = fit(tr, a, b, km)
            P = drag(th, np.array([th[4], th[4] + th[5]]))
            sb = tr.to_sb(P[:, :2])
            print(a, b, 'kmax %.1f k %.2f rms %.1f med %.1f' % (km, th[6], np.sqrt(np.mean(e ** 2)), np.median(e)),
                  'take', sb[0].round(1), 'land', sb[1].round(1), 'apexR %.2f' % (drag(th, th[4] + np.linspace(0, th[5], 50))[:, 2].max() / V.R_CH))
