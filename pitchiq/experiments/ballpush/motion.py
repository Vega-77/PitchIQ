"""Motion evidence for ball candidates: is there a constant-velocity chain of candidates through this one?

A piece's candidates are packed per abs frame (padded to N per frame) in pan-stabilised px.  For a
candidate c at frame t every candidate d at t+dt0 (|dt0| <= 2) within the ball's reach gives a velocity
hypothesis v = (d - c) / dt0.  The hypothesis is supported at frame t+dt (|dt| <= K) when a candidate lies
within tol0 + tol1 * |dt| px of c + v * dt.  The best hypothesis (most supported frames, then least
residual) gives the candidate's features.

    pack(F)                     -> dict of padded arrays over abs frames
    chain_feats(Pk, ts, ...)    -> per (t in ts, candidate) feature arrays
"""
import numpy as np

VMAX = 1100.0 / 30.0  # px per frame, the tracker's reach


def pack(F):
    lo, hi = F[0]['abs'], F[-1]['abs']
    T = hi - lo + 1
    N = max(len(fr['s']) for fr in F)
    X = np.full((T, N), np.nan)
    Y = np.full((T, N), np.nan)
    P = np.full((T, N), np.nan)
    IX = np.full((T, N), np.nan)
    IY = np.full((T, N), np.nan)
    fi = np.full(T, -1)
    for k, fr in enumerate(F):
        i = fr['abs'] - lo
        n = len(fr['s'])
        X[i, :n], Y[i, :n], P[i, :n] = fr['sx'], fr['sy'], fr['p']
        IX[i, :n], IY[i, :n] = fr['x'], fr['y']
        fi[i] = k
    return dict(lo=lo, T=T, N=N, X=X, Y=Y, P=P, IX=IX, IY=IY, fi=fi)


def chain_feats(Pk, ts, K=6, tol0=3.0, tol1=1.5, vmin=1.5, dt0s=(-2, -1, 1, 2), vmax=VMAX):
    """For each frame offset t in ts -> dict of (len(ts), N) arrays.

    nhit   frames (of 2K) supporting the best moving hypothesis (speed >= vmin px/frame)
    nb,nf  of those, behind / ahead
    speed  its speed after a least-squares refit over the supporters, px/frame
    rms    rms residual of the refit, px
    mp     mean classifier score of the supporters;  xp  their max
    nstat  frames (of 2K) with a candidate within tol0 px of the candidate's own position (stillness)
    """
    X, Y, P, T, N = Pk['X'], Pk['Y'], Pk['P'], Pk['T'], Pk['N']
    dts = [d for d in range(-K, K + 1) if d != 0]
    out = {k: np.full((len(ts), N), np.nan) for k in ('nhit', 'nb', 'nf', 'speed', 'rms', 'mp', 'xp', 'nstat', 'run')}
    for q, t in enumerate(ts):
        cx, cy = X[t], Y[t]
        ok = np.isfinite(cx)
        if not ok.any():
            continue
        # stillness
        ns = np.zeros(N)
        for dt in dts:
            u = t + dt
            if not (0 <= u < T):
                continue
            d = np.hypot(cx[:, None] - X[u][None, :], cy[:, None] - Y[u][None, :])
            ns += np.nanmin(np.where(np.isfinite(d), d, 1e9), axis=1) <= tol0
        out['nstat'][q] = np.where(ok, ns, np.nan)
        # hypotheses
        hi, hvx, hvy = [], [], []
        for d0 in dt0s:
            u = t + d0
            if not (0 <= u < T):
                continue
            vx = (X[u][None, :] - cx[:, None]) / d0
            vy = (Y[u][None, :] - cy[:, None]) / d0
            sp = np.hypot(vx, vy)
            i, j = np.nonzero((sp <= vmax) & (sp >= vmin))
            hi.append(i)
            hvx.append(vx[i, j])
            hvy.append(vy[i, j])
        if not hi:
            continue
        hi = np.concatenate(hi)
        if not len(hi):
            out['nhit'][q] = np.where(ok, 0, np.nan)
            out['run'][q] = np.where(ok, 0, np.nan)
            continue
        hvx, hvy = np.concatenate(hvx), np.concatenate(hvy)
        H = len(hi)
        hit = np.zeros((H, len(dts)), bool)
        res = np.zeros((H, len(dts)))
        hp = np.zeros((H, len(dts)))
        hx = np.zeros((H, len(dts)))
        hy = np.zeros((H, len(dts)))
        for a, dt in enumerate(dts):
            u = t + dt
            if not (0 <= u < T) or not np.isfinite(X[u]).any():
                continue
            px, py = cx[hi] + hvx * dt, cy[hi] + hvy * dt
            d = np.hypot(px[:, None] - X[u][None, :], py[:, None] - Y[u][None, :])
            d = np.where(np.isfinite(d), d, 1e9)
            j = d.argmin(axis=1)
            e = d[np.arange(H), j]
            hit[:, a] = e <= tol0 + tol1 * abs(dt)
            res[:, a] = e
            hp[:, a] = P[u][j]
            hx[:, a] = X[u][j]
            hy[:, a] = Y[u][j]
        nh = hit.sum(axis=1)
        # longest unbroken supported stretch through t (one missing frame per side forgiven)
        run = np.zeros(H)
        for sgn in (-1, 1):
            alive = np.ones(H, bool)
            miss = np.zeros(H, int)
            cnt = np.zeros(H)
            for k in range(1, K + 1):
                a = dts.index(sgn * k)
                h = hit[:, a]
                miss = np.where(h, 0, miss + 1)
                alive &= miss <= 1
                cnt += alive & h
            run += cnt
        mrun = np.zeros(N)
        np.maximum.at(mrun, hi, run)
        out['run'][q] = np.where(ok, mrun, np.nan)
        mr = np.where(hit, res / (tol0 + tol1 * np.abs(np.array(dts)))[None, :], 0).sum(axis=1) / np.maximum(1, nh)
        key = nh - 0.5 * mr
        best = np.full(N, -1)
        bkey = np.full(N, -np.inf)
        o = np.argsort(key)
        best[hi[o]] = o  # the last write per candidate is its highest key
        bkey[hi[o]] = key[o]
        dta = np.array(dts, float)
        for c in np.flatnonzero(ok):
            b = best[c]
            if b < 0:
                out['nhit'][q, c] = 0
                continue
            m = hit[b]
            out['nhit'][q, c] = m.sum()
            out['nb'][q, c] = (m & (dta < 0)).sum()
            out['nf'][q, c] = (m & (dta > 0)).sum()
            if m.sum():
                out['mp'][q, c] = hp[b][m].mean()
                out['xp'][q, c] = hp[b][m].max()
                tt = np.r_[0.0, dta[m]]
                xx = np.r_[cx[c], hx[b][m]]
                yy = np.r_[cy[c], hy[b][m]]
                if len(tt) >= 3:
                    ax = np.polyfit(tt, xx, 1)
                    ay = np.polyfit(tt, yy, 1)
                    r = np.hypot(np.polyval(ax, tt) - xx, np.polyval(ay, tt) - yy)
                    out['rms'][q, c] = np.sqrt((r ** 2).mean())
                    out['speed'][q, c] = np.hypot(ax[0], ay[0])
                else:
                    out['speed'][q, c] = np.hypot(hvx[b], hvy[b])
    return out
