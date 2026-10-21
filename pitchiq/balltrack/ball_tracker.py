"""Pick the ball among each frame's candidates: a Viterbi path with an off-screen state.

Input is a window of frames as `load_frames` returns them: per frame the
absolute frame number and every candidate's image position (x, y), its
pan-stabilised position (sx, sy) and its score s (higher = more ball-like;
render_vid uses classifier log-odds blended with the detector score).

The path chooses, in each frame, one candidate or OFF (no ball on screen):

  ON -> ON   from a candidate up to `max_step` frames back, costing
             motion_weight * (d / reach)^2 plus skip_cost per frame stepped
             over; d beyond reach (SPEED px/s over the elapsed time) is
             forbidden.
  ON -> OFF  leave_cost.  Every OFF frame scores off_score.
  OFF -> ON  re-entry anywhere for enter_cost, or near one of `remembered`
             positions where the ball was last seen for return_cost, plus a
             penalty once the distance passes a circle that starts at
             return_radius and widens by return_drift px per frame.

A run of weak, motion-inconsistent candidates therefore loses to OFF
instead of being strung together, and a ball that left and came back
resumes near where it was lost.  Coordinates are image pixels.
"""

import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FPS = 30.0
SPEED = 1100.0  # px/s: the farthest a ball moves between frames, in stabilised pixels
NEG = -1e18  # score of a forbidden transition
REMEMBERED = 8  # positions where the ball was lost that a return may start from


def load_frames(tag):
    """<tag>.npz candidates + <tag>pan.npz pan -> [{abs, x, y, s, sx, sy}], oldest first.

    Frames with no candidate are left out.  sx, sy are x, y through the
    frame's cumulative pan homography, so motion is judged in one frame of
    reference while the camera turns.
    """
    z = np.load(os.path.join(HERE, '%s.npz' % tag), allow_pickle=True)
    f, x, y, s = z['f'], z['x'], z['y'], z['s']
    lo, hi = int(z['lo']), int(z['hi'])
    o = np.argsort(f, kind='stable')
    f, x, y, s = f[o], x[o], y[o], s[o]
    edges = np.searchsorted(f, np.arange(lo, hi + 2))

    p = np.load(os.path.join(HERE, '%span.npz' % tag))
    cum, plo = p['cum'], int(p['lo'])
    assert plo <= lo and int(p['hi']) >= hi, 'pan does not cover the window'

    frames = []
    for k, ab in enumerate(range(lo, hi + 1)):
        a, b = edges[k], edges[k + 1]
        if b <= a:
            continue
        cx = x[a:b].astype(np.float64)
        cy = y[a:b].astype(np.float64)
        H = cum[ab - plo]
        q = np.column_stack([cx, cy, np.ones(len(cx))]).dot(H[:2].T)
        frames.append(dict(abs=ab, x=cx, y=cy, s=s[a:b].astype(np.float64), sx=q[:, 0], sy=q[:, 1]))
    return frames


def track_ball(
    frames,
    motion_weight=0.125,
    skip_cost=8.0,
    max_step=4,
    off_score=-0.5,
    leave_cost=2.0,
    enter_cost=12.0,
    return_cost=2.0,
    return_radius=0.0,
    return_drift=12.0,
    return_gate=0.5,
    remembered=REMEMBERED,
    stabilised=True,
):
    """frames -> per frame the picked candidate's index, or None (ball off screen).

    Parameters are the costs in the module docstring, in score units, except
    max_step (frames), return_radius and return_drift (px, px per frame) and
    return_gate (weight of the squared overshoot past the return circle, in
    circle radii).  remembered=0 turns returns off: re-entry is then only
    "anywhere, for enter_cost".  stabilised=False judges motion in raw image
    pixels instead of pan-stabilised ones.
    """
    kx, ky = ('sx', 'sy') if stabilised else ('x', 'y')
    T = len(frames)
    on = [None] * T  # best path score ending on each candidate
    bk = [None] * T  # (n, 2): source frame, source candidate; -1 = OFF
    off = np.zeros(T)
    offbk = np.zeros(T, np.int64)  # -1 = from OFF, else candidate at t-1
    # the remembered loss positions: score, frame, candidate, x, y
    ps = np.zeros(0)
    pt = np.zeros(0, np.int64)
    pj = np.zeros(0, np.int64)
    px = np.zeros(0)
    py = np.zeros(0)

    for t in range(T):
        fr = frames[t]
        n = len(fr['s'])
        cur = np.full(n, NEG)
        src = np.full((n, 2), -1, np.int64)
        for k in range(1, min(max_step, t) + 1):
            pf = frames[t - k]
            reach = max(1.0, SPEED * (fr['abs'] - pf['abs']) / FPS)
            d = np.hypot(fr[kx][:, None] - pf[kx][None, :], fr[ky][:, None] - pf[ky][None, :])
            tot = on[t - k][None, :] - motion_weight * (d / reach) ** 2 - skip_cost * (k - 1)
            tot = np.where(d > reach, NEG, tot)
            i = tot.argmax(axis=1)
            v = tot[np.arange(n), i]
            take = v > cur
            cur = np.where(take, v, cur)
            src[take, 0] = t - k
            src[take, 1] = i[take]
        if len(ps):
            age = np.maximum(1.0, t - pt).astype(float)
            allowed = np.maximum(1.0, return_radius + return_drift * age)
            d = np.hypot(fr[kx][:, None] - px[None, :], fr[ky][:, None] - py[None, :])
            over = np.maximum(0.0, d - allowed[None, :]) / allowed[None, :]
            tot = (
                ps[None, :]
                + off_score * (t - pt - 1)[None, :]
                - return_cost
                - return_gate * over**2
            )
            i = tot.argmax(axis=1)
            v = tot[np.arange(n), i]
            take = v > cur
            cur = np.where(take, v, cur)
            src[take, 0] = pt[i[take]]
            src[take, 1] = pj[i[take]]
        # from OFF (or from nothing, at t = 0): anywhere, for enter_cost
        v = (off[t - 1] if t else 0.0) - enter_cost
        take = v > cur
        cur = np.where(take, v, cur)
        src[take, 0] = t - 1
        src[take, 1] = -1
        on[t] = fr['s'] + cur
        bk[t] = src

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

    # the path may also END in a remembered gap: close it at T-1
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
