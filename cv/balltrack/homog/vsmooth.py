"""Perspective-aware smoothing of the ball on the pitch.

  smooth_H   every frame's pixel -> StatsBomb homography from ALL half-second
             registrations within +-K, each carried to the frame by the pan
             (frame-to-frame, accurate) and averaged robustly (Gaussian in time,
             outlying registrations dropped). vballsb uses only the two nearest.
  jac        d(SB)/d(pixel) at a point: far side = big, along the view ray = biggest
  rts        Kalman + RTS smoother, constant velocity in SB units. Measurement
             noise = J (pixel noise) J^T, plus extra image-vertical noise for small
             unseen hops. Big innovations: a kick (the next frames agree with the
             jump -> velocity reset) or an outlier (dropped).

    python vsmooth.py [tag]   -> <tag>_smooth.npz (abs, x, y, has, H)
"""
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
sys.path.insert(0, S2); sys.path.insert(0, HERE)

Q = np.float32([[200, 500], [1720, 500], [1720, 1000], [200, 1000]])
P = dict(rdp=1.5, min_frag=15, bridge=6, margin=3.0, K=5, sig=1.5, sig_px=1.5, fill_px=4.0, hop=0.0, acc=30.0, gate=100.0, look=3, kick_v=30.0, vmax=40.0)
for k_ in P:
    if 'VS_' + k_.upper() in os.environ:
        P[k_] = float(os.environ['VS_' + k_.upper()])


def proj(H, p):
    q = np.c_[p, np.ones(len(p))].dot(H.T); return q[:, :2] / q[:, 2:]


def smooth_H(tag, K=None, sig=None):
    K = int(P['K'] if K is None else K); sig = P['sig'] if sig is None else sig
    r = np.load(os.path.join(HERE, '%s_reg.npz' % tag)); p = np.load(os.path.join(S2, '%span.npz' % tag))
    lo, hi, cum = int(p['lo']), int(p['hi']), p['cum']
    ab, Hs = r['abs'][r['ok']], r['Hsb'][r['ok']]
    G = [Hs[i].dot(np.linalg.inv(cum[ab[i] - lo])) for i in range(len(ab))]   # reference -> SB
    out = np.zeros((hi - lo + 1, 3, 3))
    for f in range(lo, hi + 1):
        j = np.searchsorted(ab, f)
        idx = np.arange(max(0, j - K), min(len(ab), j + K))
        cf = cum[f - lo]
        pts = np.array([proj(G[i].dot(cf), Q) for i in idx])          # (m, 4, 2)
        w = np.exp(-0.5 * ((ab[idx] - f) / (30.0 * sig)) ** 2)
        for _ in range(2):
            mu = (w[:, None, None] * pts).sum(0) / w.sum()
            d = np.sqrt(((pts - mu) ** 2).sum(2)).max(1)
            s = max(1.0, 3 * np.median(d))
            w = w * (d < s)
            if w.sum() == 0:
                w = np.exp(-0.5 * ((ab[idx] - f) / (30.0 * sig)) ** 2)
                break
        mu = (w[:, None, None] * pts).sum(0) / w.sum()
        H = cv2.getPerspectiveTransform(Q, np.float32(mu)).astype(float)
        H = H / H[2, 2]
        if H.dot([960, 900, 1])[2] < 0:
            H = -H
        out[f - lo] = H
    return out


def jac(H, u):
    q = H.dot([u[0], u[1], 1.0]); s = q[:2] / q[2]
    return (H[:2, :2] - np.outer(s, H[2, :2])) / q[2]


def meas(Hs, ix, iy, fill):
    """SB reading and its 2x2 covariance per frame (nan where no ball)."""
    n = len(ix); z = np.full((n, 2), np.nan); R = np.zeros((n, 2, 2))
    for i in range(n):
        if not np.isfinite(ix[i]):
            continue
        u = (ix[i], iy[i]); H = Hs[i]
        z[i] = proj(H, np.array([u]))[0]
        J = jac(H, u)
        sp = P['fill_px'] if fill[i] else P['sig_px']
        # a small unseen hop lifts the ball in the image: pixels per SB unit of height ~ vertical ppu.
        # Off by default (hop=0): the loose along-ray noise let the constant-velocity filter slide the
        # ball toward the camera, where the rays converge and wobble costs less (vid2: 20-26 units off).
        ppu = np.linalg.norm(np.linalg.inv(J)[:, 0])     # pixels per SB unit across the view (~ per unit of height)
        C = np.diag([sp ** 2, sp ** 2 + (P['hop'] * ppu) ** 2])
        R[i] = J.dot(C).dot(J.T)
    return z, R


def rts(z, R, use=None, dt=1 / 30.):
    """Returns smoothed xy (n,2), flags (0 none, 1 used, 2 outlier, 3 kick)."""
    n = len(z)
    use = np.isfinite(z[:, 0]) if use is None else use & np.isfinite(z[:, 0])
    F = np.eye(4); F[0, 2] = F[1, 3] = dt
    q = P['acc'] ** 2
    Qm = q * np.array([[dt ** 4 / 4, 0, dt ** 3 / 2, 0], [0, dt ** 4 / 4, 0, dt ** 3 / 2],
                       [dt ** 3 / 2, 0, dt ** 2, 0], [0, dt ** 3 / 2, 0, dt ** 2]])
    Hm = np.zeros((2, 4)); Hm[0, 0] = Hm[1, 1] = 1
    xs = np.zeros((n, 4)); Ps = np.zeros((n, 4, 4)); xp = np.zeros((n, 4)); Pp = np.zeros((n, 4, 4))
    flag = np.zeros(n, int)
    x = None; Pc = None; last = -999
    VK = P['kick_v'] ** 2
    for i in range(n):
        if x is not None:
            gap = i - last
            if gap > 30:        # long gap: restart
                x = None
        if x is None:
            if not use[i]:
                continue
            x = np.r_[z[i], 0, 0]; Pc = np.diag([R[i][0, 0], R[i][1, 1], VK, VK]); Pc[:2, :2] = R[i]
            xp[i], Pp[i] = x, Pc; xs[i], Ps[i] = x, Pc; flag[i] = 1; last = i; seg_start = i
            continue
        x = F.dot(x); Pc = F.dot(Pc).dot(F.T) + Qm
        xp[i], Pp[i] = x, Pc
        if use[i]:
            S = Hm.dot(Pc).dot(Hm.T) + R[i]; v = z[i] - x[:2]
            d2 = v.dot(np.linalg.solve(S, v))
            if d2 > P['gate']:
                # kick if the next frames continue from the jump, else outlier
                nxt = [k for k in range(i + 1, min(n, i + 1 + int(P['look']) * 3)) if use[k]][:int(P['look'])]
                cont = len(nxt) >= 2 and all(
                    np.linalg.norm(z[k] - z[i]) < 3 * np.sqrt(np.trace(R[k]) + np.trace(R[i])) + P['kick_v'] * (k - i) * dt
                    for k in nxt)
                if cont:
                    Pc = Pc.copy(); Pc[2:, 2:] += np.eye(2) * VK; Pc[:2, :2] += np.eye(2) * v.dot(v)
                    S = Hm.dot(Pc).dot(Hm.T) + R[i]; flag[i] = 3
                else:
                    flag[i] = 2
            if flag[i] != 2:
                Kg = Pc.dot(Hm.T).dot(np.linalg.inv(S))
                x = x + Kg.dot(v); Pc = (np.eye(4) - Kg.dot(Hm)).dot(Pc)
                flag[i] = flag[i] or 1; last = i
        xs[i], Ps[i] = x, Pc
    # RTS backward, within stretches that are alive
    out = np.full((n, 2), np.nan)
    alive = np.zeros(n, bool)
    i = 0
    while i < n:
        if flag[i] in (1, 3) and (i == 0 or not alive[i - 1]):
            j = i; lastu = i
            while j + 1 < n and (j + 1 - lastu) <= 30 and (flag[j + 1] or xs[j + 1].any()):
                j += 1
                if flag[j] in (1, 3):
                    lastu = j
            j = lastu
            alive[i:j + 1] = True
            xb, Pb = xs[j].copy(), Ps[j].copy(); out[j] = xb[:2]
            for k in range(j - 1, i - 1, -1):
                C = Ps[k].dot(F.T).dot(np.linalg.inv(Pp[k + 1]))
                xb = xs[k] + C.dot(xb - xp[k + 1]); Pb = Ps[k] + C.dot(Pb - Pp[k + 1]).dot(C.T)
                out[k] = xb[:2]
            i = j + 1
        else:
            i += 1
    return out, flag


def too_fast(xy, w=3, pad=2):
    """Frames where the path moves faster than VMAX SB units/s (over +-w frames): no ground
    ball goes that fast, so it is a raised ball read flat that no flight claimed. Better a gap."""
    n = len(xy); bad = np.zeros(n, bool)
    for i in range(n):
        a, b = max(0, i - w), min(n - 1, i + w)
        if b > a and np.isfinite(xy[a, 0]) and np.isfinite(xy[b, 0]):
            bad[i] = np.hypot(*(xy[b] - xy[a])) * 30.0 / (b - a) > P['vmax']
    k = np.where(bad)[0]
    for d in range(-pad, pad + 1):
        bad[np.clip(k + d, 0, n - 1)] = True
    return bad


def rdp(p, eps):
    """Douglas-Peucker on a polyline: indices of the corners kept."""
    keep = [0, len(p) - 1]; st = [(0, len(p) - 1)]
    while st:
        a, b = st.pop()
        if b - a < 2:
            continue
        d = p[b] - p[a]; L = np.hypot(*d)
        q = p[a + 1:b] - p[a]
        dist = np.abs(d[0] * q[:, 1] - d[1] * q[:, 0]) / L if L > 1e-9 else np.hypot(*q.T)
        k = int(np.argmax(dist))
        if dist[k] > eps:
            c = a + 1 + k; keep.append(c); st += [(a, c), (c, b)]
    return sorted(keep)


def straighten(xy, z, R, use):
    """Between touches a rolling ball goes straight: cut each stretch at its corners
    (Douglas-Peucker on the smoothed path), fit a line to the readings of each piece
    (weighted by their covariance), and put the ball on it. Short stretches go."""
    out = np.full_like(xy, np.nan)
    ok = np.isfinite(xy[:, 0])
    for a, b in [(a, b) for a, b in _runs(ok)]:
        if b - a + 1 < P['min_frag']:
            continue
        p = xy[a:b + 1]
        cs = rdp(p, P['rdp'])
        for c0, c1 in zip(cs[:-1], cs[1:]):
            i0, i1 = a + c0, a + c1
            seg = np.arange(i0, i1 + 1)
            m = seg[use[seg] & np.isfinite(z[seg, 0])]
            A, B_ = p[c0], p[c1]
            if len(m) >= 3:
                # line through the readings, anchored to pass between the corners
                d = B_ - A; L = np.hypot(*d)
                if L > 1e-6:
                    n_ = np.array([-d[1], d[0]]) / L
                    W = np.array([1.0 / max(1e-6, n_.dot(R[k]).dot(n_)) for k in m])
                    off = np.sum(W * (z[m] - A).dot(n_)) / W.sum()
                    off = np.clip(off, -P['rdp'], P['rdp'])
                    A, B_ = A + off * n_, B_ + off * n_
            d = B_ - A; L2 = max(d.dot(d), 1e-12)
            t = np.clip((p[c0:c1 + 1] - p[c0]).dot(d) / L2, 0, 1)
            out[i0:i1 + 1] = A + t[:, None] * d
    return out


def _runs(m):
    r = []; i = 0
    while i < len(m):
        if m[i]:
            j = i
            while j + 1 < len(m) and m[j + 1]:
                j += 1
            r.append((i, j)); i = j + 1
        else:
            i += 1
    return r


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else 'vid1'
    import render_vid as RV
    Z = np.load(os.path.join(HERE, '%s_ballsb.npz' % tag)); ab = Z['abs']
    Hs = smooth_H(tag)
    by = RV.picks_for(tag)
    ix = np.array([by.get(f, {}).get('x', np.nan) if by.get(f, {}).get('src') else np.nan for f in ab])
    iy = np.array([by.get(f, {}).get('y', np.nan) if by.get(f, {}).get('src') else np.nan for f in ab])
    fill = np.array([by.get(f, {}).get('src') == 'fill' for f in ab])
    z, R = meas(Hs, ix, iy, fill)
    use = None
    b3 = os.environ.get('VS_B3', os.path.join(HERE, '%s_ball3d2.npz' % tag))
    if os.path.exists(b3):
        use = np.load(b3)['state'] != 'air'          # the chains own the air frames
    with np.errstate(invalid='ignore'):
        # far beyond the lines a 'ground' reading is a raised ball read flat: not a ground position
        inp = (z[:, 0] > -P['margin']) & (z[:, 0] < 120 + P['margin']) & (z[:, 1] > -P['margin']) & (z[:, 1] < 80 + P['margin'])
    use = inp if use is None else use & inp
    xy, flag = rts(z, R, use)
    # report only near a real reading: no straight lines across long unseen gaps
    u = np.where(np.isin(flag, (1, 3)))[0]
    if len(u):
        j = np.searchsorted(u, np.arange(len(flag)))
        d = np.minimum(np.abs(np.arange(len(flag)) - u[np.clip(j, 0, len(u) - 1)]),
                       np.abs(np.arange(len(flag)) - u[np.clip(j - 1, 0, len(u) - 1)]))
        xy[d > P['bridge']] = np.nan
    if P['rdp'] > 0:
        xy = straighten(xy, z, R, use)
    with np.errstate(invalid='ignore'):
        far = ~((xy[:, 0] > -P['margin']) & (xy[:, 0] < 120 + P['margin']) & (xy[:, 1] > -P['margin']) & (xy[:, 1] < 80 + P['margin']))
    xy[far] = np.nan
    xy[too_fast(xy)] = np.nan
    np.savez(os.path.join(HERE, os.environ.get('VS_OUT', '%s_smooth.npz' % tag)), abs=ab, x=xy[:, 0], y=xy[:, 1], zx=z[:, 0], zy=z[:, 1],
             flag=flag, H=Hs, R=R)
    print('frames %d  used %d  outliers %d  kicks %d' % (len(ab), (flag == 1).sum(), (flag == 2).sum(), (flag == 3).sum()))


if __name__ == '__main__':
    main()
