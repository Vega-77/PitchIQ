"""viterbi5 with a place for the ball to be when it is not on screen.

Scored against Alex's 269 whole-match labels (wm_score.py), viterbi5 names a
position on 54 of the 55 frames marked "off screen".  It cannot do
otherwise: its only non-candidate state is the virtual start before the
first segment, so once a path begins it has to sit on SOME candidate in
every frame to the end.  And on ball frames it is wrong 52% of the time
while a candidate within 60 px exists 87% of the time.

Two changes, both Alex's:

  OFF STATE ("the ball isn't ALWAYS on screen").  Each frame gets one extra
  state with emission e_off in place of a candidate's score.  Leaving the
  ball for OFF costs c_leave, coming back costs c_enter, and the re-entry
  may be anywhere - which is what a ball coming in from off frame does.
  A run of weak, motion-inconsistent candidates now loses to OFF instead
  of being strung together.

  ANCHORS ("if we know we found the ball, the next frame must be close").
  A candidate scoring s >= A gets emission s + ab*(s - A): the path bends
  hard to go through it, and the motion term then pins its neighbours near
  it.  It is a bonus, not a hard constraint, because ~18% of such anchors
  are white cleats (tiles 1,4,5,29,33,37,38) - a cleat nothing can reach
  by plausible motion can still be dropped.

Ordinary candidate-to-candidate edges are viterbi5's unchanged: up to
kmax-1 frames stepped over, lam*(d/reach)^2 inside the speed gate, NEG
outside.  viterbi5's distance-gated restart pool is gone; a break is now
ON -> OFF -> ON.
"""
import numpy as np

from dtrack2 import FPS, SPEED
from dtrack4 import NEG


def viterbi6(frames, lam=0.125, skip=8.0, kmax=4, e_off=0.0, c_leave=4.0,
             c_enter=4.0, A=2.3, ab=0.0, stab=True):
    """-> list of candidate index or None (None = ball not on screen)."""
    kx, ky = ('sx', 'sy') if stab else ('x', 'y')
    T = len(frames)
    on = [None] * T
    bk = [None] * T                  # (n, 2): source frame, source cand; -1 = OFF
    off = np.zeros(T)
    offbk = np.zeros(T, np.int64)    # -1 = from OFF, else candidate at t-1

    for t in range(T):
        fr = frames[t]
        s = fr['s']
        n = len(s)
        emit = s + ab * np.maximum(0.0, s - A)
        cur = np.full(n, NEG)
        src = np.full((n, 2), -1, np.int64)
        for k in range(1, min(kmax, t) + 1):
            pf = frames[t - k]
            reach = max(1.0, SPEED * (fr['abs'] - pf['abs']) / FPS)
            d = np.hypot(fr[kx][:, None] - pf[kx][None, :],
                         fr[ky][:, None] - pf[ky][None, :])
            tot = on[t - k][None, :] - lam * (d / reach) ** 2 - skip * (k - 1)
            tot = np.where(d > reach, NEG, tot)
            i = tot.argmax(axis=1)
            v = tot[np.arange(n), i]
            take = v > cur
            cur = np.where(take, v, cur)
            src[take, 0] = t - k
            src[take, 1] = i[take]
        # from OFF (or from nothing, at t = 0): anywhere, for c_enter
        v = (off[t - 1] if t else 0.0) - c_enter
        take = v > cur
        cur = np.where(take, v, cur)
        src[take, 0] = t - 1
        src[take, 1] = -1
        on[t] = emit + cur
        bk[t] = src

        if t == 0:
            off[t], offbk[t] = e_off, -1
        else:
            j = int(np.argmax(on[t - 1]))
            a, b = off[t - 1], on[t - 1][j] - c_leave
            off[t], offbk[t] = (e_off + a, -1) if a >= b else (e_off + b, j)

    out = [None] * T
    t = T - 1
    j = int(np.argmax(on[t]))
    state = j if on[t][j] > off[t] else -1
    while t >= 0:
        if state < 0:
            out[t] = None
            nj = int(offbk[t])
            t -= 1
            state = nj
        else:
            out[t] = state
            p0, p1 = bk[t][state]
            if p1 < 0:                 # came from OFF at p0 (= t-1) or start
                t, state = int(p0), -1
                if t < 0:
                    break
            else:
                t, state = int(p0), int(p1)
    return out


RESTK = 8


def viterbi7(frames, lam=0.125, skip=8.0, kmax=4, e_off=-0.5, c_leave=2.0,
             c_enter=12.0, c_near=2.0, rad0=0.0, drift=12.0, gate=0.5,
             stab=True):
    """viterbi6, plus viterbi5's memory of where the ball was lost.

    A dropped ball can come back two ways: anywhere, for c_enter (viterbi6's
    only way), or from one of RESTK remembered positions for c_near plus
    viterbi5's widening-circle distance penalty.  The frames in between are
    OFF - they emit e_off each and are reported as None.
    """
    kx, ky = ('sx', 'sy') if stab else ('x', 'y')
    T = len(frames)
    on = [None] * T
    bk = [None] * T
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
        cur = np.full(n, NEG)
        src = np.full((n, 2), -1, np.int64)
        for k in range(1, min(kmax, t) + 1):
            pf = frames[t - k]
            reach = max(1.0, SPEED * (fr['abs'] - pf['abs']) / FPS)
            d = np.hypot(fr[kx][:, None] - pf[kx][None, :],
                         fr[ky][:, None] - pf[ky][None, :])
            tot = on[t - k][None, :] - lam * (d / reach) ** 2 - skip * (k - 1)
            tot = np.where(d > reach, NEG, tot)
            i = tot.argmax(axis=1)
            v = tot[np.arange(n), i]
            take = v > cur
            cur = np.where(take, v, cur)
            src[take, 0] = t - k
            src[take, 1] = i[take]
        if len(ps):
            age = np.maximum(1.0, t - pt).astype(float)
            allowed = np.maximum(1.0, rad0 + drift * age)
            d = np.hypot(fr[kx][:, None] - px[None, :],
                         fr[ky][:, None] - py[None, :])
            over = np.maximum(0.0, d - allowed[None, :]) / allowed[None, :]
            tot = (ps[None, :] + e_off * (t - pt - 1)[None, :] - c_near
                   - gate * over ** 2)
            i = tot.argmax(axis=1)
            v = tot[np.arange(n), i]
            take = v > cur
            cur = np.where(take, v, cur)
            src[take, 0] = pt[i[take]]
            src[take, 1] = pj[i[take]]
        v = (off[t - 1] if t else 0.0) - c_enter
        take = v > cur
        cur = np.where(take, v, cur)
        src[take, 0] = t - 1
        src[take, 1] = -1
        on[t] = fr['s'] + cur
        bk[t] = src

        if t == 0:
            off[t], offbk[t] = e_off, -1
        else:
            j = int(np.argmax(on[t - 1]))
            a, b = off[t - 1], on[t - 1][j] - c_leave
            off[t], offbk[t] = (e_off + a, -1) if a >= b else (e_off + b, j)

        m = min(RESTK, n)
        top = np.argpartition(-on[t], m - 1)[:m] if n > m else np.arange(n)
        ps = np.concatenate([ps, on[t][top] - c_leave])
        pt = np.concatenate([pt, np.full(len(top), t, np.int64)])
        pj = np.concatenate([pj, top.astype(np.int64)])
        px = np.concatenate([px, fr[kx][top]])
        py = np.concatenate([py, fr[ky][top]])
        if len(ps) > RESTK:
            keep = np.argsort(-(ps + e_off * (t - pt)))[:RESTK]
            ps, pt, pj, px, py = (ps[keep], pt[keep], pj[keep],
                                  px[keep], py[keep])

    # the path may also END in a remembered gap: close it at T-1
    out = [None] * T
    t = T - 1
    j = int(np.argmax(on[t]))
    state = j if on[t][j] > off[t] else -1
    if len(ps):
        g = ps + e_off * (T - 1 - pt)
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

