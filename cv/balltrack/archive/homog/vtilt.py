import numpy as np
S = np.load('vsegs.npz'); f = float(np.load('fref.npy'))
def rays(p): return np.c_[(p[:, 0] - 960) / f, (p[:, 1] - 540) / f, np.ones(len(p))]
R0, R1, W = rays(S['p0']), rays(S['p1']), S['len']
def up(tau, rho):
    u = np.array([0, -np.cos(tau), -np.sin(tau)])
    c, s = np.cos(rho), np.sin(rho)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]).dot(u)
def ground(u, R):
    X = R / -(R.dot(u))[:, None]            # camera height 1
    e1 = np.cross(u, [0, 0, 1.]); e1 /= np.linalg.norm(e1); e2 = np.cross(u, e1)
    return np.c_[X.dot(e1), X.dot(e2)]
def score(tau, rho):
    u = up(tau, rho)
    ok = (R0.dot(u) < -1e-3) & (R1.dot(u) < -1e-3)
    a, b = ground(u, R0[ok]), ground(u, R1[ok])
    th = np.arctan2(b[:, 1] - a[:, 1], b[:, 0] - a[:, 0])
    w = W[ok]
    return abs((w * np.exp(4j * th)).sum()) / w.sum(), ok.mean(), th, w
best = max(((score(t, r)[0], t, r) for t in np.radians(np.arange(0, 70, 1))
            for r in np.radians(np.arange(-10, 10.5, 0.5))))
s, t, r = best
print('coarse: concentration %.3f  tilt %.1f deg  roll %.1f deg' % (s, np.degrees(t), np.degrees(r)))
best = max(((score(tt, rr)[0], tt, rr) for tt in t + np.radians(np.arange(-1, 1.01, 0.1))
            for rr in r + np.radians(np.arange(-0.5, 0.51, 0.05))))
s, t, r = best
c, frac, th, w = score(t, r)
print('fine:   concentration %.3f  tilt %.2f deg  roll %.2f deg  (segments below horizon %.0f%%)' % (s, np.degrees(t), np.degrees(r), 100 * frac))
d = np.degrees(np.mod(th - np.angle((w * np.exp(4j * th)).sum()) / 4 + np.pi / 4, np.pi / 2) - np.pi / 4)
print('segment angle off the two main axes, weighted p50/p80/p95: %s deg' % np.percentile(np.abs(d), [50, 80, 95]).round(2))
print('for reference, tilt 0/20/40 deg give', [round(score(np.radians(x), r)[0], 3) for x in (0, 20, 40)])
np.savez('vtilt.npz', tau=t, rho=r, f=f)
