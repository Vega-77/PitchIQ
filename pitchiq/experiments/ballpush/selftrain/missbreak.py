"""Where do the missed dev balls go?  For every 'ball' click the final rings missed (tnclick score EXPORT dir):

    no blob      no candidate within 15 px of the click on that frame (the proposer's loss)
    not first    a candidate is on the ball but another candidate outscores it (the classifier's loss)
    first, low   the ball's candidate is first but scores <= 2 (classifier unsure)
    first, sure  first and > 2, yet no ring (the tracker's / gate's loss)
  and, for each, whether the export had a pick there that the gate/flash rule hid.

    python missbreak.py <export dir> <scores dir (runs/<run>_global)>
"""
import glob
import os
import sys

import numpy as np

import st

R = 15.0


def main():
    ex, sc = sys.argv[1], sys.argv[2]
    st.common.point_at_data()
    from pitchiq.balltrack import ball_tracker

    ck = st.clicks()
    tot = {}
    rows = []
    for tag in st.DEV:
        z = np.load(os.path.join(ex, '%s.npz' % tag))
        ab, ring, X, Y, on = z['abs'], z['ring'], z['x'], z['y'], np.isfinite(z['x'])
        lo = int(ab[0])
        want = {f: a for _, f, a in ck.get(tag, []) if a['v'] == 'ball'}
        miss = {f: a for f, a in want.items() if not (0 <= f - lo < len(ab) and ring[f - lo])}
        if not miss:
            continue
        for pt in st.pieces([tag]):
            fr = [q for q in ball_tracker.load_frames(pt) if q['abs'] in miss]
            if not fr:
                continue
            f0, j0, p0 = st.load_oof(pt, sc)
            k0 = f0 * 64 + j0
            for q in fr:
                f, a = q['abs'], miss.pop(q['abs'])
                xs, ys = np.asarray(q['x'], float), np.asarray(q['y'], float)
                i = f - lo
                hidden = bool(0 <= i < len(ab) and on[i])
                if not len(xs):
                    kind, rank, pb = 'no blob', -1, np.nan
                else:
                    d = np.hypot(xs - a['x'], ys - a['y'])
                    jb = int(np.argmin(d))
                    if d[jb] > R:
                        kind, rank, pb = 'no blob', -1, np.nan
                    else:
                        jj = np.arange(len(xs))
                        r = np.searchsorted(k0, f * 64 + jj)
                        r = np.minimum(r, len(k0) - 1)
                        p = np.where(k0[r] == f * 64 + jj, p0[r], -99.0)
                        pb = float(p[jb])
                        rank = int((p > pb).sum())
                        kind = 'not first' if rank > 0 else ('first, low' if pb <= 2 else 'first, sure')
                c = tot.setdefault(kind, dict(n=0, hidden=0))
                c['n'] += 1
                c['hidden'] += hidden
                rows.append((tag, f, kind, rank, pb, hidden, len(xs)))
        for f in miss:  # frame not in any piece
            tot.setdefault('no frame', dict(n=0, hidden=0))['n'] += 1
            rows.append((tag, f, 'no frame', -1, np.nan, False, 0))
    n = sum(c['n'] for c in tot.values())
    print('missed ball clicks: %d' % n)
    for k in ('no blob', 'not first', 'first, low', 'first, sure', 'no frame'):
        if k in tot:
            print('  %-12s %4d  (%4.1f%%)   a pick there but hidden by gate/flash: %d' % (k, tot[k]['n'], 100.0 * tot[k]['n'] / n, tot[k]['hidden']))
    nf = [r[3] for r in rows if r[2] == 'not first']
    if nf:
        print('  not first: rank of the ball (0 = first) median %d, <=2: %d, <=5: %d, >10: %d' % (
            np.median(nf), sum(x <= 2 for x in nf), sum(x <= 5 for x in nf), sum(x > 10 for x in nf)))
    print('per half (no blob / not first / first low / first sure):')
    for tag in st.DEV:
        r = [x for x in rows if x[0] == tag]
        if r:
            print('  %-8s %3d %3d %3d %3d' % (tag, *[sum(x[2] == k for x in r) for k in ('no blob', 'not first', 'first, low', 'first, sure')]))
    np.save(os.path.join(ex, 'missbreak.npy'), np.array(rows, dtype=object), allow_pickle=True)


if __name__ == '__main__':
    main()
