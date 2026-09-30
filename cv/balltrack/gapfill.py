"""Motion fills the gaps the classifier leaves.

diag_none.py: of 68 labelled balls the tracker leaves at None, 41 have a
candidate on the ball that the classifier scores ~-9 log-odds (blur, air,
occlusion), 20 have no candidate at all, 7 are top but weak.  The first
group is what motion is for: when the tracker holds the ball on both
sides of a gap, the ball in between is near the line joining the two
anchors, whatever the classifier thinks of its crop.

  fill    a None run of at most `maxgap` source frames, bounded by ON picks
          on both sides: predict each frame's position by linear
          interpolation in the pan-stabilised frame (sx, sy) over abs
          time, and take the candidate nearest the prediction if it is
          within  rad0 + rgrow * (frames to the nearer anchor)  and its
          log-odds is at least pmin.  Else leave None.
  coast   also extend each ON segment up to `ext` frames past its ends at
          the velocity of its last two picks, same acceptance rule.
  synth   in a filled gap, a frame with no acceptable candidate gets the
          interpolated point itself, mapped back to the image through the
          frame's pan (a pick is then ('syn', x, y), not an index).  The
          20 balls the detector never proposed can only be had this way.

The ON segments come from viterbi7 on classifier emission, unchanged.

    python gapfill.py           # grid, split-half tuned, needs wmNNcls.npz
"""
import itertools

import numpy as np

import dtrack6 as D6
import wm_cls as C
import wm_tune as U

KX, KY = 'sx', 'sy'


def _take(fr, px, py, R, pmin):
    if not len(fr['p']):
        return None
    d = np.hypot(fr[KX] - px, fr[KY] - py)
    ok = (d <= R) & (fr['p'] >= pmin)
    if not ok.any():
        return None
    return int(np.where(ok)[0][np.argmin(d[ok])])


def attach_pan(frames, tag):
    import os
    p = np.load(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '%span.npz' % tag))
    cum, plo = p['cum'], int(p['lo'])
    for fr in frames:
        fr['Hinv'] = np.linalg.inv(cum[fr['abs'] - plo])


def _synth(fr, px, py):
    q = fr['Hinv'].dot([px, py, 1.0])
    return ('syn', float(q[0] / q[2]), float(q[1] / q[2]))


def xy(fr, j):
    if isinstance(j, tuple):
        return j[1], j[2]
    return fr['x'][j], fr['y'][j]


def score(T, F, idx, picks, clips):
    import collections
    c = collections.Counter()
    for t in T:
        if t['clip'] not in clips or t['kind'] not in ('ball', 'off'):
            continue
        k = idx[t['clip']].get(t['abs'])
        j = None if k is None else picks[t['clip']][k]
        c[t['kind']] += 1
        if t['kind'] == 'off':
            c['ok'] += j is None
            continue
        if j is None:
            c['none'] += 1
            continue
        x, y = xy(F[t['clip']][k], j)
        if np.hypot(x - t['x'], y - t['y']) <= U.W.TOL:
            c['right'] += 1
            c['ok'] += 1
            c['syn_right'] += isinstance(j, tuple)
        else:
            c['wrong'] += 1
            c['syn_wrong'] += isinstance(j, tuple)
    return c


def fill(frames, picks, maxgap=32, rad0=20.0, rgrow=5.0, pmin=-99.0,
         ext=0, synth=0):
    out = list(picks)
    T = len(frames)
    on = [t for t in range(T) if picks[t] is not None]

    def pos(t):
        fr = frames[t]
        return fr[KX][picks[t]], fr[KY][picks[t]], fr['abs']

    # interior gaps
    for a, b in zip(on, on[1:]):
        if b - a < 2 or b - a - 1 > maxgap:
            continue
        xa, ya, ta = pos(a)
        xb, yb, tb = pos(b)
        for t in range(a + 1, b):
            fr = frames[t]
            w = (fr['abs'] - ta) / max(1, tb - ta)
            px, py = xa + w * (xb - xa), ya + w * (yb - ya)
            R = rad0 + rgrow * min(t - a, b - t)
            out[t] = _take(fr, px, py, R, pmin)
            if out[t] is None and synth:
                out[t] = _synth(fr, px, py)
    if ext <= 0:
        return out
    # coast past segment ends (never into another segment)
    seg_end = [t for t in on if t + 1 < T and picks[t + 1] is None]
    seg_beg = [t for t in on if t > 0 and picks[t - 1] is None]
    for t0, step in [(t, 1) for t in seg_end] + [(t, -1) for t in seg_beg]:
        t1 = t0 - step
        if not (0 <= t1 < T) or picks[t1] is None:
            continue
        x0, y0, a0 = pos(t0)
        x1, y1, a1 = pos(t1)
        vx, vy = (x0 - x1) / (a0 - a1), (y0 - y1) / (a0 - a1)
        lx, ly, la = x0, y0, a0
        for k in range(1, ext + 1):
            t = t0 + step * k
            if not (0 <= t < T) or out[t] is not None:
                break
            fr = frames[t]
            dt = fr['abs'] - la
            px, py = lx + vx * dt, ly + vy * dt
            j = _take(fr, px, py, rad0 + rgrow * k, pmin)
            if j is None:
                break
            out[t] = j
            lx, ly, la = fr[KX][j], fr[KY][j], fr['abs']
    return out


def main():
    T, tags, F, idx = U.load_all()
    for tag in tags:
        C.attach(F[tag], tag)
        attach_pan(F[tag], tag)
    halves = {'H1': {t for t in tags if int(t[2:]) < 9},
              'H2': {t for t in tags if int(t[2:]) >= 9}}
    base_grid = dict(a=[0.75, 1.0], e_off=[-1.0, -0.5, 0.0],
                     c_enter=[12.0, 20.0], c_near=[2.0, 6.0], skip=[4.0])
    fill_grid = dict(maxgap=[0, 32, 64, 128, 100000], rad0=[10.0, 20.0],
                     rgrow=[2.0, 5.0], pmin=[-99.0], ext=[0, 2, 5],
                     synth=[0, 1])
    res = []
    bk = list(base_grid)
    fk = list(fill_grid)
    for bv in itertools.product(*(base_grid[k] for k in bk)):
        bc = dict(zip(bk, bv))
        a = bc.pop('a')
        for tag in tags:
            for fr in F[tag]:
                fr['s'] = a * fr['p'] + (1 - a) * fr['s0']
        base = {tag: D6.viterbi7(F[tag], drift=12.0, gate=2.0, **bc)
                for tag in tags}
        bc['a'] = a
        seen = set()
        for fv in itertools.product(*(fill_grid[k] for k in fk)):
            fc = dict(zip(fk, fv))
            if fc['maxgap'] == 0 and fc['ext'] == 0:
                fc = dict(maxgap=0, rad0=0, rgrow=0, pmin=0, ext=0, synth=0)
            key = tuple(sorted(fc.items()))
            if key in seen:
                continue
            seen.add(key)
            p = {tag: fill(F[tag], base[tag], **fc) for tag in tags}
            r = {h: score(T, F, idx, p, cl) for h, cl in halves.items()}
            res.append((dict(bc, **fc), r, score(T, F, idx, p, set(tags))))
    print('%d configs' % len(res))

    def obj(c):
        return c['ok'] - c['wrong']

    nofill = [z for z in res if z[0]['maxgap'] == 0 and z[0]['ext'] == 0]
    nosyn = [z for z in res if not z[0]['synth']]
    for name, pool in (('viterbi7 alone', nofill), ('fill, candidates only',
                       nosyn), ('fill + synth', res)):
        print('\n== %s' % name)
        cfg, r, al = max(pool, key=lambda z: obj(z[2]))
        print('best-on-all %s\n  %s' % (cfg, U.fmt(al)))
        for tune, test in (('H1', 'H2'), ('H2', 'H1')):
            cfg, r, al = max(pool, key=lambda z: obj(z[1][tune]))
            print('tuned on %s: %s' % (tune, cfg))
            print('  tune %s %s' % (tune, U.fmt(r[tune])))
            print('  TEST %s %s  (synth right %d wrong %d)' % (
                test, U.fmt(r[test]), r[test]['syn_right'],
                r[test]['syn_wrong']))


if __name__ == '__main__':
    main()
