"""Re-acquisition that remembers where the ball was lost.

Alex, after watching the 27-35 s span: "maybe keep track of where it was
lost.  Then, when the camera goes to pick it up, it must be near the region
we lost it at."

That is a description of a bug in viterbi4, and a precise one.  Its restart
is:

    start, sp = run - brk, runat      # runat = (frame, candidate)
    take = start > cur                # the SAME score for every candidate

`run` is the best score anywhere before now and `runat` is where that was,
so the model knows exactly where it lost the ball - and then offers every
candidate in the frame the identical price to become the new ball.  A
re-acquisition 1200 px away costs what one 20 px away costs.  That is why
the 30.8 s loss lands 1244 px from the mark: nothing was charging for it.

So charge for it.  A restart from a source at (xs, ys), `age` frames back,
onto a candidate `d` px away, gets

    allowed = rad0 + drift * age      # a widening circle, not a fixed one
    penalty = gate * (max(0, d - allowed) / allowed) ** 2

free inside the circle and quadratically dear outside, in the same shape as
the frame-to-frame motion term so the two are on one scale.  The circle
widens because the longer the ball is gone the less its last position says:
at rad0 0 drift 37 it is exactly the speed gate the motion term already
uses, applied across the gap.

GATE 0 IS A REAL CONTROL.  Unlike kmax 1, which I wrongly called one, gate
0 makes the penalty identically zero and the restart identically viterbi4's,
so `gate 0 -> 41` is a harness check and anything above it is this idea.

One source is not enough.  viterbi4 keeps only the single best prior score,
which is fine when every restart costs the same and wrong as soon as
distance matters: a slightly worse source sitting next to the re-entry point
can beat the best one across the pitch.  So keep RESTK of them, decayed the
same way.

HONEST LIMIT, STATED BEFORE THE NUMBERS.  These are IMAGE pixels.  The ball
usually leaves the frame because the CAMERA moved, and after a pan the same
patch of grass has a different x - so "near where we lost it" is true on the
pitch and only approximately true on the sensor.  Registration exists
(dense.py fits ORB per frame) but the manifest never stored the transforms,
so field coordinates need another video pass.  If a widening circle in image
space helps, that is a floor on what the same idea does in field space, not
a ceiling.

    PIQ_DENSE=dense200.npz python dtrack5.py
"""
import os

import numpy as np

import dtrack2 as D2
from dtrack2 import FPS, SPEED
from dtrack4 import NEG

RESTK = 8                     # restart sources carried forward

RAD0S = [0.0, 60.0, 150.0]
DRIFTS = [12.0, 37.0, 90.0]
GATES = [0.0, 0.5, 2.0, 8.0]
LAM, SKIP, BRK, KMAX = 0.125, 8.0, 32.0, 4


def viterbi5(frames, lam, skip, brk, kmax, rad0, drift, gate,
             stab=True, ret_obj=False):
    """viterbi4, with the restart priced by distance from where it broke.

    gate 0.0 reproduces viterbi4 exactly (the penalty term vanishes and the
    pool's best source is the old `run`), which is the control.
    """
    kx, ky = ('sx', 'sy') if stab else ('x', 'y')
    T = len(frames)
    best = [None] * T
    bk = [None] * T
    virt = 0.0                                  # -skip*t, nothing started yet

    # restart pool: parallel arrays of score-at-entry, frame, index, position
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

        # --- ordinary edges, up to kmax-1 frames stepped over -------------
        for k in range(1, min(kmax, t) + 1):
            pf = frames[t - k]
            if best[t - k] is None:
                continue
            reach = max(1.0, SPEED * (fr['abs'] - pf['abs']) / FPS)
            d = np.hypot(fr[kx][:, None] - pf[kx][None, :],
                         fr[ky][:, None] - pf[ky][None, :])
            tot = best[t - k][None, :] - lam * (d / reach) ** 2 - skip * (k - 1)
            tot = np.where(d > reach, NEG, tot)
            i = tot.argmax(axis=1)
            v = tot[np.arange(n), i]
            take = v > cur
            cur = np.where(take, v, cur)
            src[take, 0] = t - k
            src[take, 1] = i[take]

        # --- restart, now paying for how far it reaches -------------------
        if len(ps):
            age = np.maximum(1.0, t - pt).astype(float)
            allowed = np.maximum(1.0, rad0 + drift * age)
            d = np.hypot(fr[kx][:, None] - px[None, :],
                         fr[ky][:, None] - py[None, :])
            over = np.maximum(0.0, d - allowed[None, :]) / allowed[None, :]
            # ps already carries the -skip decay for every frame it waited
            tot = (ps[None, :] - skip * (t - pt - 1)[None, :] - brk
                   - gate * over ** 2)
            i = tot.argmax(axis=1)
            v = tot[np.arange(n), i]
            take = v > cur
            cur = np.where(take, v, cur)
            src[take, 0] = pt[i[take]]
            src[take, 1] = pj[i[take]]

        # the virtual start before any segment exists is never gated: there
        # is no "where we lost it" yet to be near.
        take = virt > cur
        cur = np.where(take, virt, cur)
        src[take, 0] = -1
        src[take, 1] = -1

        best[t] = fr['s'] + cur
        bk[t] = src

        # --- refresh the pool with this frame's strongest candidates ------
        m = min(RESTK, n)
        top = np.argpartition(-best[t], m - 1)[:m] if n > m else np.arange(n)
        ps = np.concatenate([ps, best[t][top]])
        pt = np.concatenate([pt, np.full(len(top), t, np.int64)])
        pj = np.concatenate([pj, top.astype(np.int64)])
        px = np.concatenate([px, fr[kx][top]])
        py = np.concatenate([py, fr[ky][top]])
        if len(ps) > RESTK:
            # rank by score decayed to NOW; the decay is uniform, so this
            # ordering does not change as t advances
            keep = np.argsort(-(ps - skip * (t - pt)))[:RESTK]
            ps, pt, pj, px, py = (ps[keep], pt[keep], pj[keep],
                                  px[keep], py[keep])
        virt -= skip

    # --- backtrace ------------------------------------------------------
    end = max(range(T), key=lambda i: best[i].max() - skip * (T - 1 - i))
    j = int(np.argmax(best[end]))
    obj = best[end][j]
    out = [None] * T
    t = end
    while True:
        out[t] = int(j)
        p0, p1 = bk[t][j]
        if p0 < 0:
            break
        nt, j = int(p0), int(p1)
        assert nt < t, 'backtrace must go backwards'
        t = nt
    return (out, float(obj)) if ret_obj else out


def cover(pick):
    return 100.0 * sum(1 for j in pick if j is not None) / len(pick)


def main():
    frames, lab, absi = D2.load()
    T = len(frames)
    nb = sum(1 for fr in frames
             if lab.get(absi.get(fr['abs']), {}).get('kind') == 'ball')
    print('%s: %d frames, %d hand marks, pool ceiling 46.'
          % (os.environ.get('PIQ_DENSE', 'dense.npz'), T, nb))
    print('viterbi4 at these knobs scores 41.  gate 0 below MUST reproduce')
    print('it - the penalty vanishes and the restart is the old one.  If it')
    print('does not, the pool rewrite is wrong and nothing else here counts.')
    print()

    best = None
    for rad0 in RAD0S:
        for drift in DRIFTS:
            row = []
            for g in GATES:
                p = viterbi5(frames, LAM, SKIP, BRK, KMAX, rad0, drift, g)
                h = D2.score(frames, p, lab, absi)[0]
                row.append('%2d %3.0f%%' % (h, cover(p)))
                if best is None or h > best[0]:
                    best = (h, rad0, drift, g)
            print('  rad0 %-6s drift %-6s  %s'
                  % (rad0, drift, '  '.join('%-9s' % c for c in row)))
    print('  %-22s %s' % ('', '  '.join('%-9s' % ('gate ' + str(g))
                                        for g in GATES)))
    print()
    h, rad0, drift, g = best
    print('  best %d / 47 at rad0 %s drift %s gate %s' % (h, rad0, drift, g))
    print('  this is a 5-knob grid on 47 labels.  It is not a result until')
    print('  dfold3.py has tuned it on half the marks and scored the other.')


if __name__ == '__main__':
    main()
