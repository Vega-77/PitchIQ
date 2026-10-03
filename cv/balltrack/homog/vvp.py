import numpy as np
S = np.load('vsegs.npz'); f = float(np.load('fref.npy'))
K = np.array([[f, 0, 960], [0, f, 540], [0, 0, 1.]]); Ki = np.linalg.inv(K)
def h(p): return np.c_[p, np.ones(len(p))]
# work on rays (normalised camera coords) so points at infinity are fine
a, b = h(S['p0']).dot(Ki.T), h(S['p1']).dot(Ki.T)
Lh = np.cross(a, b); Lh /= np.linalg.norm(Lh[:, :2], axis=1)[:, None]   # line through segment
dirn = b - a; dirn /= np.linalg.norm(dirn, axis=1)[:, None]
mid = (a + b) / 2
W = S['len']
def support(v, tol):
    # angle between segment direction and direction from midpoint towards v (v homogeneous ray)
    if abs(v[2]) > 1e-9:
        t = v[:2] / v[2] - mid[:, :2] / mid[:, 2:3]
    else:
        t = np.tile(v[:2], (len(mid), 1))
    t /= np.linalg.norm(t, axis=1)[:, None] + 1e-12
    c = np.abs((t * dirn[:, :2]).sum(1))
    return c > np.cos(np.radians(tol))
rng = np.random.default_rng(1)
def ransac(mask, tol=0.7, it=6000):
    idx = np.where(mask)[0]; p = W[idx] / W[idx].sum()
    best, bv = 0, None
    for _ in range(it):
        i, j = rng.choice(idx, 2, replace=False, p=p)
        v = np.cross(Lh[i], Lh[j])
        if np.linalg.norm(v) < 1e-12: continue
        s = W[support(v, tol) & mask].sum()
        if s > best: best, bv = s, v
    return bv / np.linalg.norm(bv), best
v1, s1 = ransac(np.ones(len(W), bool))
in1 = support(v1, 0.7)
v2, s2 = ransac(~in1)
in2 = support(v2, 0.7) & ~in1
d1, d2 = v1 / np.linalg.norm(v1), v2 / np.linalg.norm(v2)
print('VP1 support %.0f%% of segment length, VP2 %.0f%%' % (100 * s1 / W.sum(), 100 * W[in2].sum() / W.sum()))
ang = np.degrees(np.arccos(abs(d1.dot(d2))))
print('angle between the two ground directions (should be 90): %.2f deg' % ang)
n = np.cross(d1, d2); n /= np.linalg.norm(n)
if n[1] > 0: n = -n        # up points toward -y (image up)
print('ground normal (camera coords, y down, z forward):', n.round(4))
print('camera tilt below horizontal %.1f deg, roll %.1f deg' % (np.degrees(np.arcsin(-n[2])), np.degrees(np.arctan2(n[0], -n[1]))))
for ff in (2400, 2600, 2800, 3000, 3200):
    Kf = np.array([[ff, 0, 960], [0, ff, 540], [0, 0, 1.]])
    # vps in pixel coords of ref plane are fixed; re-express with another focal
    vp1, vp2 = K.dot(v1), K.dot(v2)
    e1, e2 = np.linalg.inv(Kf).dot(vp1), np.linalg.inv(Kf).dot(vp2)
    print('  if f=%d: angle %.2f' % (ff, np.degrees(np.arccos(abs(e1.dot(e2) / np.linalg.norm(e1) / np.linalg.norm(e2))))))
np.savez('vvp.npz', v1=v1, v2=v2, n=n, f=f, in1=in1, in2=in2)
