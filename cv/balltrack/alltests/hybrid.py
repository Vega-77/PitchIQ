"""Our tracker + TAPNext++ bridges -> p2d/<ver>/<tag>.npz, and TAPNext alone -> p2d/tn_alone.

    python hybrid.py <tag> [<tag> ...]

Our tracker (cls_B: viterbi7 + gapfill) owns every confident pick, every kick and every
flight.  TAPNext only crosses the gaps between two confident picks a..b: seeded on a and run
forward, seeded on b and run backward (tn_engine.py bridges).  A bridge is accepted only when
  - the forward track lands within ANC px of our pick at b,
  - the backward track lands within ANC px of our pick at a,
  - the two tracks stay within AGR px of each other on every frame of the gap,
  - (vid windows) the gap does not touch a frame our 3D chain calls air.
An accepted gap takes the two tracks blended by distance from their seeds (src 3).
Thresholds were fixed before scoring; no label was used to set them.

  hyb32    bridges on gaps <= 32 frames (the gapfill's own reach), else shipped
  hyb90    bridges on gaps <= 90 frames, else shipped
  hyb90s   as hyb90, but a gap whose bridge is rejected is left EMPTY (shipped fill dropped):
           a wrong ring is worse than a missing one
  tn_alone TAPNext seeded once per window, no help (ring where it says visible)
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
ANC = 20.0
AGR = 24.0
STATS = {}


def air_mask(tag, ab):
    p = os.path.join(S2, 'homog', '%s_ball3dd.npz' % tag)
    if not os.path.exists(p):
        return np.zeros(len(ab), bool)
    z = np.load(p); air = z['state'] == 'air'
    m = np.zeros(len(ab), bool); i = z['abs'] - ab[0]; k = (i >= 0) & (i < len(ab))
    m[i[k]] = air[k]
    return m


def build(tag):
    z = np.load(os.path.join(HERE, 'p2d', 'cls_B', '%s.npz' % tag))
    ab, X, Y, SRC = z['abs'], z['x'], z['y'], z['src']
    R = np.load(os.path.join(HERE, 'tn', 'bridges_%s.npy' % tag), allow_pickle=True)
    tr = {}
    for a, b, d, xy, vis in R:
        tr[(int(a), int(b), int(d))] = np.asarray(xy, float)
    air = air_mask(tag, ab)
    outs = {}
    st = dict(gaps=0, acc=0, air=0, land=0, agree=0)
    for ver, maxgap, strict in (('hyb32', 32, False), ('hyb90', 90, False), ('hyb90s', 90, True)):
        x, y, s = X.copy(), Y.copy(), SRC.copy()
        anc = np.flatnonzero(SRC == 1)
        for i, j in zip(anc[:-1], anc[1:]):
            L = j - i - 1
            if not 1 <= L <= maxgap:
                continue
            a, b = int(ab[i]), int(ab[j])
            count = ver == 'hyb90'
            # air first: tn_engine.py does not run TAPNext on these gaps at all
            if air[i:j + 1].any():
                st['gaps'] += count; st['air'] += count
                if strict:
                    x[i + 1:j] = np.nan; y[i + 1:j] = np.nan; s[i + 1:j] = 0
                continue
            f = tr.get((a, b, 1)); g = tr.get((a, b, -1))
            if f is None:
                continue
            if g is None:
                # tn_engine.py skips the backward run once the forward track has
                # missed the pick at b, since that alone fails the landing test
                st['gaps'] += count; st['land'] += count
                if strict:
                    x[i + 1:j] = np.nan; y[i + 1:j] = np.nan; s[i + 1:j] = 0
                continue
            g = g[::-1]                                  # backward track, re-ordered a..b
            if count:
                st['gaps'] += 1
            ok = True
            if np.hypot(*(f[-1] - (X[j], Y[j]))) > ANC or np.hypot(*(g[0] - (X[i], Y[i]))) > ANC:
                ok = False; st['land'] += count
            elif np.hypot(*(f[1:-1] - g[1:-1]).T).max() > AGR:
                ok = False; st['agree'] += count
            if ok:
                w = np.arange(1, L + 1) / (L + 1.0)          # weight of the backward track grows toward b
                p = (1 - w)[:, None] * f[1:-1] + w[:, None] * g[1:-1]
                x[i + 1:j], y[i + 1:j] = p[:, 0], p[:, 1]; s[i + 1:j] = 3
                st['acc'] += count
            elif strict:
                x[i + 1:j] = np.nan; y[i + 1:j] = np.nan; s[i + 1:j] = 0
        outs[ver] = (x, y, s)
    for ver, (x, y, s) in outs.items():
        d = os.path.join(HERE, 'p2d', ver); os.makedirs(d, exist_ok=True)
        np.savez(os.path.join(d, '%s.npz' % tag), abs=ab, x=x, y=y, src=s)
    STATS[tag] = st
    print('%-5s gaps<=90 %4d  accepted %4d (%.0f%%)  rejected: air %d  landing %d  disagree %d   share bridged hyb90 %.3f' % (
        tag, st['gaps'], st['acc'], 100.0 * st['acc'] / max(1, st['gaps']), st['air'], st['land'], st['agree'],
        (outs['hyb90'][2] == 3).mean()), flush=True)


def alone(tag):
    p = os.path.join(HERE, 'tn', 'alone_%s.npz' % tag)
    if not os.path.exists(p):
        return
    z = np.load(p)
    v = z['vis'].astype(bool) & np.isfinite(z['x'])
    x = np.where(v, z['x'], np.nan); y = np.where(v, z['y'], np.nan)
    d = os.path.join(HERE, 'p2d', 'tn_alone'); os.makedirs(d, exist_ok=True)
    np.savez(os.path.join(d, '%s.npz' % tag), abs=z['abs'], x=x, y=y, src=v.astype(np.int8))
    print('tn_alone %-5s  visible %.3f' % (tag, v.mean()), flush=True)
    # its track everywhere after the seed, ignoring its own visibility call (2D only)
    k = np.isfinite(z['x'])
    d = os.path.join(HERE, 'p2d', 'tn_alone_raw'); os.makedirs(d, exist_ok=True)
    np.savez(os.path.join(d, '%s.npz' % tag), abs=z['abs'], x=np.where(k, z['x'], np.nan), y=np.where(k, z['y'], np.nan),
             src=k.astype(np.int8))


if __name__ == '__main__':
    for t in sys.argv[1:]:
        if os.path.exists(os.path.join(HERE, 'tn', 'bridges_%s.npy' % t)):
            build(t)
        alone(t)
