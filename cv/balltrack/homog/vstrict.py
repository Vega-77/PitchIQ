"""Stricter flights: a wrong flight costs more than a missed one.

  unseen   no flight time where the tracker has no ball: inferred ends are cut
           to the first / last seen frame, and a flight needs MIN_SEEN seen frames
           and SEEN_FRAC of its frames seen
  out      a flight that starts beyond the lines (most of its first START_N seen frames), or spends most of its seen
           frames beyond them (by MARGIN), is a ball out of play: dropped
  short    a flight shorter than MIN_LEN frames after trimming is dropped

Dropped flight frames go back to 'ground' where the tracker has a ball, else 'none'.

    python vstrict.py in.npz out.npz [tag]
"""
import os
import sys

import numpy as np

P = dict(min_seen=8, seen_frac=0.6, margin=2.0, out_frac=0.5, min_len=8, edge=2, start_n=1)
for k_ in P:
    if 'VX_' + k_.upper() in os.environ:
        P[k_] = float(os.environ['VX_' + k_.upper()])


def runs(m):
    r = []; i = 0
    while i < len(m):
        if m[i]:
            j = i
            while j + 1 < len(m) and m[j + 1]:
                j += 1
            r.append((i, j)); i = j + 1
        else:
            i += 1
    return r


def outside(x, y, mg):
    return ~((x > -mg) & (x < 120 + mg) & (y > -mg) & (y < 80 + mg))


def strict(B, has):
    st = B['state'].copy(); x, y = B['x'], B['y']
    why = {'unseen': 0, 'out': 0, 'short': 0, 'kept': 0}
    for a, b in runs(st == 'air'):
        s = np.where(has[a:b + 1])[0] + a
        if len(s) == 0:
            st[a:b + 1] = 'none'; why['unseen'] += 1; continue
        a2, b2 = max(a, s[0] - int(P['edge'])), min(b, s[-1] + int(P['edge']))
        st[a:a2] = np.where(has[a:a2], 'ground', 'none'); st[b2 + 1:b + 1] = np.where(has[b2 + 1:b + 1], 'ground', 'none')
        n = b2 - a2 + 1
        ok_xy = np.isfinite(x[s])
        with np.errstate(invalid='ignore'):
            o = outside(x[s][ok_xy], y[s][ok_xy], P['margin'])
        bad = None
        if len(s) < P['min_seen'] or len(s) / n < P['seen_frac']:
            bad = 'unseen'
        elif n < P['min_len']:
            bad = 'short'
        elif len(o) and (o.mean() > P['out_frac'] or o[:int(P['start_n'])].mean() > 0.5):
            bad = 'out'
        if bad:
            st[a2:b2 + 1] = np.where(has[a2:b2 + 1], 'ground', 'none'); why[bad] += 1
        else:
            why['kept'] += 1
    return st, why


def main():
    src, dst = sys.argv[1], sys.argv[2]
    tag = sys.argv[3] if len(sys.argv) > 3 else 'vid1'
    B = dict(np.load(src))
    has = np.load('%s_ballsb.npz' % tag)['has'].astype(bool)
    st, why = strict(B, has)
    B['state'] = st
    np.savez(dst, **B)
    print('%s -> %s  air frames %d -> %d  %s' % (src, dst, (np.load(src)['state'] == 'air').sum(), (st == 'air').sum(), why))


if __name__ == '__main__':
    main()
