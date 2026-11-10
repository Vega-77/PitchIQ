"""Tracker variants with motion evidence (velocity), for replay.py through variants.run.

  acc:<w>[:<a0>[:<cap>]]     second-order motion cost.  Every state carries the velocity it was reached
                             with (px/frame, stabilised).  Continuing from it with velocity v' costs
                             w * min((|v' - v| / a0)^2, cap): a hop that changes velocity by a0 px/frame
                             costs w, any kick or bounce at most w * cap.  A state entered from OFF or by
                             a return has no velocity, so its first step is free.
  smooth:<b>[:<as>[:<vmin>]] reward: a continuation whose velocity changes by at most <as> px/frame and
                             that moves at least <vmin> px/frame earns +b (a moving, steady track may
                             then run through weak candidates; still clutter earns nothing).
  Both can be joined with '+', and with off:<v>.  acc:0 is the shipped tracker exactly.
"""
import numpy as np

from pitchiq.balltrack import ball_tracker as B


def track_acc(frames, acc_w=0.0, a0=4.0, cap=4.0, sm_b=0.0, sm_a=2.0, sm_v=1.5, motion_weight=0.125, skip_cost=4.0,
              max_step=4, off_score=-0.5, leave_cost=2.0, enter_cost=12.0, return_cost=6.0, return_radius=0.0,
              return_drift=12.0, return_gate=2.0, remembered=B.REMEMBERED):
    kx, ky = 'sx', 'sy'
    T = len(frames)
    on = [None] * T
    bk = [None] * T
    VX = [None] * T
    VY = [None] * T
    off = np.zeros(T)
    offbk = np.zeros(T, np.int64)
    ps = np.zeros(0)
    pt = np.zeros(0, np.int64)
    pj = np.zeros(0, np.int64)
    px = np.zeros(0)
    py = np.zeros(0)
    for t in range(T):
        fr = frames[t]
        n = len(fr['s'])
        cur = np.full(n, B.NEG)
        src = np.full((n, 2), -1, np.int64)
        vx = np.full(n, np.nan)
        vy = np.full(n, np.nan)
        for k in range(1, min(max_step, t) + 1):
            pf = frames[t - k]
            dtf = float(fr['abs'] - pf['abs'])
            reach = max(1.0, B.SPEED * dtf / B.FPS)
            dx = fr[kx][:, None] - pf[kx][None, :]
            dy = fr[ky][:, None] - pf[ky][None, :]
            d = np.hypot(dx, dy)
            tot = on[t - k][None, :] - motion_weight * (d / reach) ** 2 - skip_cost * (k - 1)
            nvx, nvy = dx / dtf, dy / dtf
            if acc_w or sm_b:
                a = np.hypot(nvx - VX[t - k][None, :], nvy - VY[t - k][None, :])  # nan where no velocity
                has = np.isfinite(a)
                if acc_w:
                    tot = tot - np.where(has, acc_w * np.minimum((a / a0) ** 2, cap), 0.0)
                if sm_b:
                    tot = tot + np.where(has & (a <= sm_a) & (d / dtf >= sm_v), sm_b, 0.0)
            tot = np.where(d > reach, B.NEG, tot)
            i = tot.argmax(axis=1)
            r = np.arange(n)
            v = tot[r, i]
            take = v > cur
            cur = np.where(take, v, cur)
            src[take, 0] = t - k
            src[take, 1] = i[take]
            vx = np.where(take, nvx[r, i], vx)
            vy = np.where(take, nvy[r, i], vy)
        if len(ps):
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
            vx = np.where(take, np.nan, vx)
            vy = np.where(take, np.nan, vy)
        v = (off[t - 1] if t else 0.0) - enter_cost
        take = v > cur
        cur = np.where(take, v, cur)
        src[take, 0] = t - 1
        src[take, 1] = -1
        vx = np.where(take, np.nan, vx)
        vy = np.where(take, np.nan, vy)
        on[t] = fr['s'] + cur
        bk[t] = src
        VX[t], VY[t] = vx, vy
        if t == 0:
            off[t], offbk[t] = off_score, -1
        else:
            j = int(np.argmax(on[t - 1]))
            a, b = off[t - 1], on[t - 1][j] - leave_cost
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
    j = int(np.argmax(on[t]))
    state = j if on[t][j] > off[t] else -1
    if len(ps):
        g = ps + off_score * (T - 1 - pt)
        q = int(np.argmax(g))
        if g[q] > max(on[t][j], off[t]) and pt[q] < T - 1:
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
    return out


def parse(name):
    """-> track_acc keyword arguments, or None when the name has no motion part."""
    kw, hit = {}, False
    for part in name.split('+'):
        k = part.split(':')
        if k[0] == 'acc':
            hit = True
            kw['acc_w'] = float(k[1])
            if len(k) > 2:
                kw['a0'] = float(k[2])
            if len(k) > 3:
                kw['cap'] = float(k[3])
        elif k[0] == 'smooth':
            hit = True
            kw['sm_b'] = float(k[1])
            if len(k) > 2:
                kw['sm_a'] = float(k[2])
            if len(k) > 3:
                kw['sm_v'] = float(k[3])
        elif k[0] == 'off':
            kw['off_score'] = float(k[1])
        elif k[0] in ('enter', 'ret', 'leave', 'skip'):  # plain first-order costs, for reference sweeps
            hit = True
            kw[dict(enter='enter_cost', ret='return_cost', leave='leave_cost', skip='skip_cost')[k[0]]] = float(k[1])
    return kw if hit else None


def parse_fill(name):
    """fill:<pmin>[:<rad0>[:<rgrow>[:<maxgap>[:<ext>]]]]   gap_fill.fill with these settings (shipped: -99, 20, 5, 32, 0)
    sfill:<g>[:<pmin>[:<rad0>[:<rgrow>[:<maxgap>]]]]   smooth_fill: the same search circle, but the filled
    picks must form one chain from anchor to anchor whose offset from the straight line drifts at most
    g px per frame; the longest such chain wins."""
    for part in name.split('+'):
        k = part.split(':')
        if k[0] in ('fill', 'sfill', 'fillk'):
            v = [float(x) for x in k[1:]]
            kw = {}
            if k[0] == 'sfill':
                kw['g'] = v.pop(0)
            for key, x in zip(('pmin', 'rad0', 'rgrow', 'maxgap', 'ext'), v):
                kw[key] = int(x) if key in ('maxgap', 'ext') else x
            return kw
    return None


def keeps_runs(name):
    """fillk:... is fill:... with one difference downstream: a frame the score floor (pmin) blanks still
    counts as ringed for the short-run rule, so blanking a weak filled frame does not also delete the
    good rings on either side of it for being a 'short run'."""
    return any(q.split(':')[0] == 'fillk' for q in name.split('+'))


def smooth_fill(frames, picks, g=4.0, maxgap=32, rad0=20.0, rgrow=5.0, pmin=-99.0):
    """gap_fill.fill, but a gap's picks are chosen together: the longest chain of candidates (inside the
    same circles round the straight line between the two anchors) in which the offset from the line
    changes by at most g px per frame, starting and ending at offset 0 on the anchors.  Ties go to the
    chain with the least total change."""
    out = list(picks)
    T = len(frames)
    on = [t for t in range(T) if picks[t] is not None]
    for a, b in zip(on, on[1:]):
        if b - a < 2 or b - a - 1 > maxgap:
            continue
        fa, fb = frames[a], frames[b]
        xa, ya, ta = fa['sx'][picks[a]], fa['sy'][picks[a]], fa['abs']
        xb, yb, tb = fb['sx'][picks[b]], fb['sy'][picks[b]], fb['abs']
        nodes = [(ta, 0.0, 0.0, a, -1)]  # abs, rx, ry, frame index, candidate
        for t in range(a + 1, b):
            fr = frames[t]
            if not len(fr['p']):
                continue
            w = (fr['abs'] - ta) / max(1, tb - ta)
            px, py = xa + w * (xb - xa), ya + w * (yb - ya)
            rx, ry = fr['sx'] - px, fr['sy'] - py
            R = rad0 + rgrow * min(t - a, b - t)
            for j in np.flatnonzero((np.hypot(rx, ry) <= R) & (fr['p'] >= pmin)):
                nodes.append((fr['abs'], rx[j], ry[j], t, int(j)))
        nodes.append((tb, 0.0, 0.0, b, -1))
        n = len(nodes)
        if n == 2:
            continue
        A = np.array([(q[0], q[1], q[2]) for q in nodes], float)
        cnt = np.full(n, -1.0)
        cst = np.zeros(n)
        prev = np.full(n, -1)
        cnt[0] = 0
        for k in range(1, n):
            dt = A[k, 0] - A[:k, 0]
            dr = np.hypot(A[k, 1] - A[:k, 1], A[k, 2] - A[:k, 2])
            ok = (dt > 0) & (dr <= g * dt) & (cnt[:k] >= 0)
            if not ok.any():
                continue
            key = np.where(ok, cnt[:k] - 1e-4 * (cst[:k] + dr), -np.inf)
            i = int(np.argmax(key))
            cnt[k], cst[k], prev[k] = cnt[i] + 1, cst[i] + dr[i], i
        k = prev[n - 1]
        while k > 0:
            out[nodes[k][3]] = nodes[k][4]
            k = prev[k]
    return out


def parse_fillb(name):
    """fillb:<pmin>:<w>[:<rad0>[:<rgrow>[:<maxgap>]]] -> best_fill settings, or None."""
    for part in name.split('+'):
        k = part.split(':')
        if k[0] == 'fillb':
            v = [float(x) for x in k[1:]]
            kw = {}
            for key, x in zip(('pmin', 'w', 'rad0', 'rgrow', 'maxgap'), v):
                kw[key] = int(x) if key == 'maxgap' else x
            return kw
    return None


def best_fill(frames, picks, pmin=-8.0, w=0.25, maxgap=32, rad0=20.0, rgrow=5.0):
    """gap_fill.fill's interior gaps, but inside the circle the candidate with the best
    classifier score - w * (px from the straight line) is taken instead of the nearest one."""
    out = list(picks)
    T = len(frames)
    on = [t for t in range(T) if picks[t] is not None]
    for a, b in zip(on, on[1:]):
        if b - a < 2 or b - a - 1 > maxgap:
            continue
        fa, fb = frames[a], frames[b]
        xa, ya, ta = fa['sx'][picks[a]], fa['sy'][picks[a]], fa['abs']
        xb, yb, tb = fb['sx'][picks[b]], fb['sy'][picks[b]], fb['abs']
        for t in range(a + 1, b):
            fr = frames[t]
            if not len(fr['p']):
                continue
            u = (fr['abs'] - ta) / max(1, tb - ta)
            d = np.hypot(fr['sx'] - (xa + u * (xb - xa)), fr['sy'] - (ya + u * (yb - ya)))
            ok = (d <= rad0 + rgrow * min(t - a, b - t)) & (fr['p'] >= pmin)
            if ok.any():
                out[t] = int(np.argmax(np.where(ok, fr['p'] - w * d, -np.inf)))
    return out
