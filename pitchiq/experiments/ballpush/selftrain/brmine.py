"""Bridge-mined hard positives: frames the shipped two-sided TAPNext bridge filled (evaluation/p2d/hyb90, src 3)
where a cached blob candidate sits within NEAR px of the bridged position.  The tracker had no pick there, so
these are balls the classifier scored too low to track: the positives self-training on picks cannot reach.

    python brmine.py <game>     -> selftrain/labels/br_<game>.npz, from the halves of every OTHER game
    python brmine.py check      every dev half, compared with its clicks (how clean are the labels?); writes nothing

Same file layout as pseudo.py (names, pt, row, y, hard, f); positives only (y = ball).  The bridged positions come
from the shipped chain; for fold <game> only other games' frames are used, as for the 'strict' pseudo-labels.
"""
import os
import sys

import numpy as np

import st

sys.path.insert(0, os.path.dirname(st.HERE))
NEAR = 6.0
P2D = 'C:/Users/alexv/Desktop/Repos/PitchIQ/pitchiq/balltrack/evaluation/p2d/hyb90'


def main():
    name = sys.argv[1]
    check = name == 'check'
    st.common.point_at_data()
    from pitchiq.balltrack import ball_tracker, gap_fill
    import replay as RP

    os.chdir(RP.BT)
    tags = [t for t in st.DEV if check or st.game(t) != name]
    ck = st.clicks() if check else {}
    rec = dict(pt=[], row=[], f=[])
    names = []
    chk = dict(n=0, right=0, out=0)
    for tag in tags:
        z = np.load(os.path.join(P2D, '%s.npz' % tag))
        k = z['src'] == 3
        B = dict(zip(z['abs'][k].tolist(), zip(z['x'][k].tolist(), z['y'][k].tolist())))
        click = {f: a for _, f, a in ck.get(tag, [])}
        seen = set()
        n = 0
        for pt in st.pieces([tag]):
            ca = st.load_cache(pt, crops=False)
            key = ca['f'] * 64 + ca['j']
            F = ball_tracker.load_frames(pt)
            gap_fill.attach_pan(F, pt)
            pi = None
            for fr in F:
                ab = int(fr['abs'])
                if ab not in B or ab in seen or not len(fr['s']):
                    continue
                seen.add(ab)
                xy = np.array([gap_fill.xy(fr, j) for j in range(len(fr['s']))], float).reshape(-1, 2)
                d = np.hypot(xy[:, 0] - B[ab][0], xy[:, 1] - B[ab][1])
                j = int(d.argmin())
                if d[j] > NEAR:
                    continue
                r = int(np.searchsorted(key, ab * 64 + j))
                if r >= len(key) or key[r] != ab * 64 + j:
                    continue
                if pi is None:
                    pi = len(names)
                    names.append(pt)
                rec['pt'].append(pi), rec['row'].append(r), rec['f'].append(ab)
                n += 1
                a = click.get(ab)
                if a is not None:
                    chk['n'] += 1
                    chk['right'] += a['v'] == 'ball' and np.hypot(xy[j, 0] - a['x'], xy[j, 1] - a['y']) <= 15
                    chk['out'] += a['v'] == 'out'
        print('%-8s bridged frames %6d  labelled %6d' % (tag, len(B), n), flush=True)
    print('ALL labelled', len(rec['row']))
    if check:
        print('check vs clicks: bridge labels on clicked frames %(n)d, within 15 px of the click %(right)d, on "out" frames %(out)d' % chk)
        return
    os.makedirs(st.OUT + '/labels', exist_ok=True)
    m = len(rec['row'])
    np.savez(st.OUT + '/labels/br_%s.npz' % name, names=np.array(names), pt=np.array(rec['pt']), row=np.array(rec['row']),
             y=np.zeros(m, np.int64), hard=np.ones(m, bool), f=np.array(rec['f']))


if __name__ == '__main__':
    main()
