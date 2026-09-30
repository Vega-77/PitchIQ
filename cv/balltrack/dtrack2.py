"""The dense tracker, after dgap.py said the pool was not the problem.

dgap.py settled where the 27/47 goes.  The ball is inside the 40 kept
candidates in 43 of the 47 labelled frames, so a perfect tracker on this
exact pool would score 43.  The tracker reaches 27.  Sixteen frames are lost
by the PATH, not by the detector, and they are lost in runs - 186692 to
186807, 187037 to 187060, 187524 to 187839 - which is the signature of a path
that walks off the ball and cannot get back.

Two things stop it getting back, and this file fixes both.

MISS WAS PRICED FOR A DIFFERENT PROBLEM.  Leaving the ball and re-acquiring
costs MISS twice - 6.0 - against emission scores that live around 1.  So
staying on the wrong object is always cheaper than switching to the right
one, and the tracker proved it: at every lambda in the first sweep, across
1676 frames, the null state was used exactly zero times.  It is not a null
state, it is decoration.  In track.py 3.0 was reasonable because a step was
23 frames and a switch was rare; at one frame it forbids recovery outright.
MISS is swept here instead of assumed.

THE GATE WAS BEING CHARGED FOR THE CAMERA.  Candidates live in the source
pixels of their own frame and the camera pans, so the displacement between
consecutive frames is ball motion PLUS camera motion, while the 1100 px/s the
gate enforces is a statement about a ball alone.  pan.py measured it: median
1.4 px per frame, but p90 14.5 and max 28.4 against a 36.7 px budget.  So in
the top decile of frames the camera is spending 40-77% of the ball's travel
allowance - and those are exactly the frames where the camera is panning
because the ball is moving fast, which is when the allowance is needed.
pan.npz carries a transform per frame into a common frame; the transition
term and the anchor fit both run there, while every score stays in the raw
source pixels the hand marks were made in.

Both are printed as separate columns, because a fix that only works
alongside another fix is worth knowing about.

    python dtrack2.py
"""
import io
import json
import math
import os

import numpy as np

SP = ('C:/Users/alexv/AppData/Local/Temp/claude/'
      'C--Users-alexv-Desktop-Repos-PitchIQ/'
      '7f31f241-b244-4121-aa07-4ef3b9736b2a/scratchpad')
S2 = os.path.dirname(os.path.abspath(__file__))

TOL = 60.0
SPEED = 1100.0
FPS = 30.0
LAMS = [0.5, 1, 2, 4, 8, 16]
MISSES = [0.25, 0.5, 1.0, 2.0, 3.0]

W = 4
RES = 12.0
NEED = 7
RA = 20.0


def viterbi(frames, lam, miss, anchors=None, stab=True):
    """Best path.  `stab` picks which coordinates the transition is judged in."""
    kx, ky = ('sx', 'sy') if stab else ('x', 'y')
    bks, prev_sc = [], None
    for t, fr in enumerate(frames):
        n = len(fr['s'])
        em = np.concatenate([fr['s'], [0.0]])
        if anchors is not None and anchors[t] is not None:
            ax, ay = anchors[t]
            far = np.hypot(fr[kx] - ax, fr[ky] - ay) > RA
            em[:n][far] = -1e9
            em[n] = -1e9
        if prev_sc is None:
            cur, bk = em.copy(), None
        else:
            pf = frames[t - 1]
            np_ = len(pf['s'])
            dt = (fr['abs'] - pf['abs']) / FPS
            reach = max(1.0, SPEED * dt)
            d = np.hypot(fr[kx][:, None] - pf[kx][None, :],
                         fr[ky][:, None] - pf[ky][None, :])
            pen = lam * (d / reach) ** 2
            pen[d > reach] = np.inf
            full = np.full((n + 1, np_ + 1), miss)
            full[:n, :np_] = pen
            full[n, :] = miss
            full[:, np_] = miss
            tot = prev_sc[None, :] - full
            fin = np.where(np.isfinite(tot), tot, -1e18)
            bk = fin.argmax(axis=1)
            cur = em + fin[np.arange(n + 1), bk]
        bks.append(bk)
        prev_sc = cur
    j = int(np.argmax(prev_sc))
    out = [None] * len(frames)
    for t in range(len(frames) - 1, -1, -1):
        out[t] = None if j == len(frames[t]['s']) else j
        if bks[t] is not None:
            j = int(bks[t][j])
    return out


def find_anchors(frames, w=W, res=RES, need=NEED, minspd=0.0, stab=True):
    """Frames whose top pick sits on a straight, physical line with its
    neighbours.  No label enters this; the precision is measured after."""
    kx, ky = ('sx', 'sy') if stab else ('x', 'y')
    n = len(frames)
    px = np.array([fr[kx][0] if len(fr['s']) else np.nan for fr in frames])
    py = np.array([fr[ky][0] if len(fr['s']) else np.nan for fr in frames])
    out = [None] * n
    for t in range(n):
        if not np.isfinite(px[t]):
            continue
        lo, hi = max(0, t - w), min(n, t + w + 1)
        u = np.arange(lo, hi, dtype=np.float64)
        ok = np.isfinite(px[lo:hi])
        if ok.sum() < need:
            continue
        A = np.vstack([u[ok] - t, np.ones(int(ok.sum()))]).T
        bx = np.linalg.lstsq(A, px[lo:hi][ok], rcond=None)[0]
        by = np.linalg.lstsq(A, py[lo:hi][ok], rcond=None)[0]
        r = np.hypot(px[lo:hi][ok] - A.dot(bx), py[lo:hi][ok] - A.dot(by))
        spd = float(math.hypot(bx[0], by[0])) * FPS
        if spd > SPEED or spd < minspd:
            continue
        if (r <= res).sum() < need:
            continue
        if abs(px[t] - bx[1]) > res or abs(py[t] - by[1]) > res:
            continue
        out[t] = (float(bx[1]), float(by[1]))
    return out


def score(frames, pick, lab, absi):
    """Hits within TOL of the hand mark, over labelled ball frames only."""
    hit = tot = 0
    for t, fr in enumerate(frames):
        L = lab.get(absi.get(fr['abs']))
        if L is None or L['kind'] != 'ball':
            continue
        tot += 1
        j = pick[t]
        if j is not None and math.hypot(fr['x'][j] - L['x'],
                                        fr['y'][j] - L['y']) <= TOL:
            hit += 1
    return hit, tot


def nulls(pick):
    return sum(1 for j in pick if j is None)


def load():
    # PIQ_DENSE re-points every downstream script at another manifest
    # (dense200.npz) without editing any of them.
    z = np.load(os.path.join(S2, os.environ.get('PIQ_DENSE',
                                                'dense.npz')),
                allow_pickle=True)
    f, x, y, s = z['f'], z['x'], z['y'], z['s']
    lo, hi = int(z['lo']), int(z['hi'])
    o = np.argsort(f, kind='stable')
    f, x, y, s = f[o], x[o], y[o], s[o]
    edges = np.searchsorted(f, np.arange(lo, hi + 2))

    p = np.load(os.path.join(S2, 'pan.npz'))
    cum, plo = p['cum'], int(p['lo'])

    frames = []
    for k, ab in enumerate(range(lo, hi + 1)):
        a, b = edges[k], edges[k + 1]
        if b <= a:
            continue
        cx = x[a:b].astype(np.float64)
        cy = y[a:b].astype(np.float64)
        H = cum[ab - plo]
        q = np.column_stack([cx, cy, np.ones(len(cx))]).dot(H[:2].T)
        frames.append(dict(abs=ab, x=cx, y=cy, s=s[a:b].astype(np.float64),
                           sx=q[:, 0], sy=q[:, 1]))

    man = json.load(io.open(os.path.join(SP, 'label', 'manifest.json'),
                            encoding='utf-8'))
    absi = {fr['abs']: fr['i'] for fr in man['frames']}
    lab = {}
    labdir = os.path.join(S2, 'lab5', 'labels')
    for fn in sorted(os.listdir(labdir)):
        L = json.load(io.open(os.path.join(labdir, fn), encoding='utf-8'))
        lab[L['i']] = L
    return frames, lab, absi


def prec(frames, an, lab, absi):
    ok = tot = 0
    for t, fr in enumerate(frames):
        if an[t] is None:
            continue
        L = lab.get(absi.get(fr['abs']))
        if L is None or L['kind'] != 'ball':
            continue
        tot += 1
        # an[] is in stabilised coordinates; the hand mark is not.  Compare
        # against the raw pick the anchor was fitted through instead of
        # unwarping, which would need the inverse and add error for nothing.
        if math.hypot(fr['x'][0] - L['x'], fr['y'][0] - L['y']) <= TOL:
            ok += 1
    return ok, tot


def main():
    frames, lab, absi = load()
    nb = sum(1 for fr in frames
             if lab.get(absi.get(fr['abs']), {}).get('kind') == 'ball')
    print('%d frames, %d hand-marked balls, ceiling on this pool 43'
          % (len(frames), nb))

    raw = [0] * len(frames)
    print('raw per-frame argmax   %d / %d' % (score(frames, raw, lab, absi)[0],
                                              nb))

    an_s = find_anchors(frames, stab=True)
    an_r = find_anchors(frames, stab=False)
    ps, ts = prec(frames, an_s, lab, absi)
    pr, tr = prec(frames, an_r, lab, absi)
    print()
    print('ANCHORS  stabilised %d frames (%d/%d labelled right)'
          % (sum(a is not None for a in an_s), ps, ts))
    print('         raw        %d frames (%d/%d labelled right)'
          % (sum(a is not None for a in an_r), pr, tr))
    print('  stabilising should find MORE: a ball flying straight across the')
    print('  pitch is a straight line only once the camera is taken out.')

    for stab in (False, True):
        print()
        print('=' * 66)
        print('TRANSITION IN %s COORDINATES'
              % ('CAMERA-STABILISED' if stab else 'RAW SOURCE'))
        an = an_s if stab else an_r
        print('  %-7s %s' % ('', '   '.join('%-9s' % ('lam ' + str(l))
                                            for l in LAMS)))
        best = None
        for ms in MISSES:
            cells = []
            for lam in LAMS:
                p = viterbi(frames, float(lam), ms, stab=stab)
                h = score(frames, p, lab, absi)[0]
                cells.append('%2d  %4.0f%%' % (h, 100.0 * nulls(p) / len(p)))
                if best is None or h > best[0]:
                    best = (h, lam, ms, False)
                pa = viterbi(frames, float(lam), ms, anchors=an, stab=stab)
                ha = score(frames, pa, lab, absi)[0]
                if ha > best[0]:
                    best = (ha, lam, ms, True)
            print('  miss %-3s %s' % (ms, '   '.join('%-9s' % c
                                                     for c in cells)))
        print('  each cell: hits / 47, and the %% of frames the path called'
              ' empty')
        print('  best here, anchors allowed: %d / 47  at lambda %s miss %s%s'
              % (best[0], best[1], best[2], ', with anchors' if best[3]
                 else ', without'))

    print()
    print('track.py sparse: raw 19/47, tracked 28/47.  dtrack.py v1, dense')
    print('with miss 3.0 in raw coordinates: 27/47.  The pool ceiling is 43.')


if __name__ == '__main__':
    main()
