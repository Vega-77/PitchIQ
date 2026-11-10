"""Tracker variants for replay.py.  Each takes a piece's frames (p, s0, pan attached) and returns
(per-frame candidate index or None, src per frame: 1 tracker, 2 gap fill, 0 none).

  base                  the shipped chain (render_review_video.picks_for)
  off:<v>               base with off_score v (one bar for every pick, the plain loosening)
  floor:<f>[:W[:conf]]  track-aware: a candidate reached by continuing a track at a believable speed
                        (the tracker's own reach) gets emission max(s, f); entering from OFF or a
                        return still needs s itself; with W, only within W frames of the path's last
                        pick with classifier p > conf (default 2, the ring bar).  So a track coasts over weak frames at a cost of
                        off_score - f each, instead of off_score - s.
  pool:<K>:<r>:<c>      neighbouring-frame evidence: p becomes max(p, best p within r + 4|dt| px
                        (pan-stabilised) in the +-K frames, minus c per frame of distance)
  floor:<f>+pool:...    both
  win:<N>:<R>:<K>[:<M>] local search: each frame keeps only the candidates within R stabilised px of the
                        tracker's own (forward, causal) best M states at the last frame it held the ball, R
                        growing 12 px per frame of age; a full frame every N frames, and whenever the
                        tracker has held nothing for K frames (the ball has to be found again).
                        gap_fill sees full frames (its own search is local).  run() returns the share of
                        full frames as fr['_full'] on F[0].
"""
import numpy as np

from pitchiq.balltrack import ball_tracker as B
from pitchiq.balltrack import gap_fill
from pitchiq.balltrack import render_review_video as rv


def track_floor(frames, floor, W=np.inf, conf=2.0, motion_weight=0.125, skip_cost=4.0, max_step=4, off_score=-0.5, leave_cost=2.0,
                enter_cost=12.0, return_cost=6.0, return_radius=0.0, return_drift=12.0, return_gate=2.0,
                remembered=B.REMEMBERED, win=None):
    """ball_tracker.track_ball (stabilised) with the continuation floor; identical when floor = -inf."""
    kx, ky = 'sx', 'sy'
    T = len(frames)
    if win is not None:
        frames = list(frames)
        wN, wR, wK, wM = win
        full = np.zeros(T, bool)
        last_on = None  # (t, centres)
    on = [None] * T
    bk = [None] * T
    off = np.zeros(T)
    offbk = np.zeros(T, np.int64)
    ps = np.zeros(0)
    pt = np.zeros(0, np.int64)
    pj = np.zeros(0, np.int64)
    px = np.zeros(0)
    py = np.zeros(0)
    agp = [None] * T  # frames since the path's last confident pick (p > conf)
    for t in range(T):
        if win is not None:
            fr0 = frames[t]
            if t and len(on[t - 1]) and on[t - 1].max() > off[t - 1]:
                o = on[t - 1]
                top = np.argsort(-o)[:wM]
                top = top[o[top] > off[t - 1]]
                last_on = (t - 1, frames[t - 1][kx][top], frames[t - 1][ky][top])
            if t % wN == 0 or last_on is None or t - last_on[0] > wK:
                full[t] = True
                keep = np.arange(len(fr0['s']))
            else:
                r = wR + 12.0 * (t - last_on[0] - 1)
                d = np.hypot(fr0[kx][:, None] - last_on[1][None, :], fr0[ky][:, None] - last_on[2][None, :])
                keep = np.flatnonzero((d <= r).any(axis=1))
            n0 = len(fr0['s'])
            sub = {k: (v[keep] if isinstance(v, np.ndarray) and v.ndim >= 1 and len(v) == n0 else v) for k, v in fr0.items()}
            sub['_idx'] = keep
            frames[t] = sub
        fr = frames[t]
        n = len(fr['s'])
        cur = np.full(n, B.NEG)
        src = np.full((n, 2), -1, np.int64)
        cont = np.zeros(n, bool)
        ag = np.full(n, np.inf)
        for k in range(1, min(max_step, t) + 1):
            pf = frames[t - k]
            if not len(pf['s']) or not n:
                continue
            reach = max(1.0, B.SPEED * (fr['abs'] - pf['abs']) / B.FPS)
            d = np.hypot(fr[kx][:, None] - pf[kx][None, :], fr[ky][:, None] - pf[ky][None, :])
            tot = on[t - k][None, :] - motion_weight * (d / reach) ** 2 - skip_cost * (k - 1)
            tot = np.where(d > reach, B.NEG, tot)
            i = tot.argmax(axis=1)
            v = tot[np.arange(n), i]
            take = v > cur
            cur = np.where(take, v, cur)
            src[take, 0] = t - k
            src[take, 1] = i[take]
            cont |= take
            ag = np.where(take, agp[t - k][i] + k, ag)
        if len(ps) and n:
            age = np.maximum(1.0, t - pt).astype(float)
            allowed = np.maximum(1.0, return_radius + return_drift * age)
            d = np.hypot(fr[kx][:, None] - px[None, :], fr[ky][:, None] - py[None, :])
            over = np.maximum(0.0, d - allowed[None, :]) / allowed[None, :]
            tot = ps[None, :] + off_score * (t - pt - 1)[None, :] - return_cost - return_gate * over ** 2
            i = tot.argmax(axis=1)
            v = tot[np.arange(n), i]
            take = v > cur
            cur = np.where(take, v, cur)
            src[take, 0] = pt[i[take]]
            src[take, 1] = pj[i[take]]
            cont &= ~take
        v = (off[t - 1] if t else 0.0) - enter_cost
        take = v > cur
        cur = np.where(take, v, cur)
        src[take, 0] = t - 1
        src[take, 1] = -1
        cont &= ~take
        ag = np.where(cont, ag, np.inf)
        em = np.where(cont & (ag <= W), np.maximum(fr['s'], floor), fr['s'])
        agp[t] = np.where(fr['p_raw'] > conf, 0.0, ag)
        on[t] = em + cur
        bk[t] = src
        if t == 0:
            off[t], offbk[t] = off_score, -1
        else:
            j = int(np.argmax(on[t - 1])) if len(on[t - 1]) else -1
            a, b = off[t - 1], (on[t - 1][j] - leave_cost if j >= 0 else B.NEG)
            off[t], offbk[t] = (off_score + a, -1) if a >= b else (off_score + b, j)
        if not remembered:
            continue
        m = min(remembered, n)
        top = np.argpartition(-on[t], m - 1)[:m] if n > m else np.arange(n)
        ps = np.concatenate([ps, on[t][top] - leave_cost])
        pt = np.concatenate([pt, np.full(len(top), t, np.int64)])
        pj = np.concatenate([pj, top.astype(np.int64)])
        px = np.concatenate([px, fr[kx][top]])
        py = np.concatenate([py, fr[ky][top]])
        if len(ps) > remembered:
            keep = np.argsort(-(ps + off_score * (t - pt)))[:remembered]
            ps, pt, pj, px, py = (ps[keep], pt[keep], pj[keep], px[keep], py[keep])
    out = [None] * T
    t = T - 1
    j = int(np.argmax(on[t])) if len(on[t]) else -1
    state = j if j >= 0 and on[t][j] > off[t] else -1
    if len(ps):
        g = ps + off_score * (T - 1 - pt)
        q = int(np.argmax(g))
        if g[q] > max(on[t][j] if j >= 0 else B.NEG, off[t]) and pt[q] < T - 1:
            t, state = int(pt[q]), int(pj[q])
    while t >= 0:
        if state < 0:
            nj = int(offbk[t])
            t -= 1
            state = nj
        else:
            out[t] = state
            p0, p1 = bk[t][state]
            if p1 < 0:
                t, state = int(p0), -1
            else:
                t, state = int(p0), int(p1)
    if win is not None:
        out = [None if o is None else int(frames[t]['_idx'][o]) for t, o in enumerate(out)]
        return out, full
    return out


def pooled(F, K, r, c):
    out = []
    for t, fr in enumerate(F):
        q = fr['p_raw'].copy()
        for dt in range(-K, K + 1):
            if dt == 0 or not (0 <= t + dt < len(F)):
                continue
            g = F[t + dt]
            if not len(g['p_raw']) or not len(q):
                continue
            d = np.hypot(fr['sx'][:, None] - g['sx'][None, :], fr['sy'][:, None] - g['sy'][None, :])
            v = np.where(d <= r + 4 * abs(dt), g['p_raw'][None, :], -np.inf).max(axis=1) - c * abs(dt)
            q = np.maximum(q, v)
        out.append(q)
    return out


def run(name, F):
    for fr in F:
        if 'p_raw' not in fr:
            fr['p_raw'] = fr['p'].copy()
        fr['p'] = fr['p_raw'].copy()
    floor, off, win = None, -0.5, None
    for part in name.split('+'):
        k = part.split(':')
        if k[0] == 'pool':
            P = pooled(F, int(k[1]), float(k[2]), float(k[3]))
            for fr, q in zip(F, P):
                fr['p'] = q
        elif k[0] == 'floor':
            floor = float(k[1])
            W = float(k[2]) if len(k) > 2 else np.inf
            conf = float(k[3]) if len(k) > 3 else 2.0
        elif k[0] == 'off':
            off = float(k[1])
        elif k[0] == 'win':
            win = (int(k[1]), float(k[2]), int(k[3]), int(k[4]) if len(k) > 4 else 1)
    for fr in F:
        fr['s'] = rv.A * fr['p'] + (1 - rv.A) * fr['s0']
    import motionvar

    mkw = motionvar.parse(name)
    # fill-only variants share the tracker's picks: keep them per piece, keyed by the tracker part
    tkey = '+'.join(q for q in name.split('+') if q.split(':')[0] not in ('fill', 'sfill', 'fillk', 'fillb')) or 'base'
    memo = F[0].setdefault('_trk', {})
    if tkey in memo and win is None:
        base = memo[tkey]
    elif mkw is not None:
        base = motionvar.track_acc(F, **mkw)
    elif win is not None:
        base, full = track_floor(F, -np.inf if floor is None else floor, W if floor is not None else np.inf,
                                 conf if floor is not None else 2.0, off_score=off, win=win)
        F[0]['_full'] = full
    elif floor is None:
        base = B.track_ball(F, return_drift=12.0, return_gate=2.0, **dict(rv.BASE, off_score=off))
    else:
        base = track_floor(F, floor, W, conf, off_score=off)
    memo[tkey] = base
    fkw = motionvar.parse_fill(name)
    bkw = motionvar.parse_fillb(name)
    if bkw is not None:
        out = motionvar.best_fill(F, base, **bkw)
        F[0]['_cont'] = np.array([o is not None for o in gap_fill.fill(F, base, **{k: v for k, v in bkw.items() if k not in ('pmin', 'w')})])
    elif fkw is not None and 'g' in fkw:
        out = motionvar.smooth_fill(F, base, **fkw)
    else:
        out = gap_fill.fill(F, base, **(fkw or {}))
    if motionvar.keeps_runs(name):
        k0 = {k: v for k, v in (fkw or {}).items() if k not in ('pmin', 'g')}
        F[0]['_cont'] = np.array([o is not None for o in gap_fill.fill(F, base, **k0)])
    src = np.array([0 if o is None else (1 if b is not None else 2) for b, o in zip(base, out)], np.int8)
    for fr in F:
        fr['s'] = fr['s0'].copy()
        fr['p'] = fr['p_raw'].copy()
    return out, src
