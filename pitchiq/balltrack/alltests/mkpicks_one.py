"""The shipped tracker's picks for one window -> p2d/cls_B/<tag>.npz.

cls_B is ball_tracker.track_ball + gap_fill.

    python -m pitchiq.balltrack.alltests.mkpicks_one <tag>

src 1 = tracker pick, 2 = gap-fill pick, 0 = no ball.  This is what tn_engine.py bridges
and hybrid.py start from.
"""

import os
import sys

import numpy as np

from .. import render_review_video as RV

HERE = os.path.dirname(os.path.abspath(__file__))
S2 = os.path.dirname(HERE)

tag = sys.argv[1]
os.chdir(S2)
p = RV.picks_for(tag)
ab = np.array(sorted(p))
x = np.full(len(ab), np.nan)
y = x.copy()
s = np.zeros(len(ab), np.int8)
for i, f in enumerate(ab):
    r = p[int(f)]
    if r.get('src') in ('track', 'fill'):
        x[i], y[i], s[i] = r['x'], r['y'], 1 if r['src'] == 'track' else 2
d = os.path.join(HERE, 'p2d', 'cls_B')
os.makedirs(d, exist_ok=True)
np.savez(os.path.join(d, '%s.npz' % tag), abs=ab, x=x, y=y, src=s)
print('frames', len(ab), ab[0], ab[-1], 'tracked', (s == 1).sum(), 'fill', (s == 2).sum())
