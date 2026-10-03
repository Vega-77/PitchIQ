"""The shipped tracker's picks (cls_B: viterbi7 + gapfill) for one window -> p2d/cls_B/<tag>.npz.

    python mkpicks_one.py <tag>

src 1 = tracker pick, 2 = gap-fill pick, 0 = no ball.  This is what tn_engine.py bridges
and hybrid.py start from.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
sys.path.insert(0, S2)
import render_vid as RV      # noqa: E402

tag = sys.argv[1]
os.chdir(S2)
p = RV.picks_for(tag); ab = np.array(sorted(p))
x = np.full(len(ab), np.nan); y = x.copy(); s = np.zeros(len(ab), np.int8)
for i, f in enumerate(ab):
    r = p[int(f)]
    if r.get('src') in ('track', 'fill'):
        x[i], y[i], s[i] = r['x'], r['y'], 1 if r['src'] == 'track' else 2
d = os.path.join(HERE, 'p2d', 'cls_B'); os.makedirs(d, exist_ok=True)
np.savez(os.path.join(d, '%s.npz' % tag), abs=ab, x=x, y=y, src=s)
print('frames', len(ab), ab[0], ab[-1], 'tracked', (s == 1).sum(), 'fill', (s == 2).sum())
