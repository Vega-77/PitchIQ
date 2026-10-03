"""Air Lab labels -> per-frame truth, and the flight detector scored on it.

    python vairscore.py [tag]      (default vid1)
    -> airtruth.npz  abs, truth ('ground'|'air'|'hidden'|'none'), flight id (-1 = none)
"""
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
tag = sys.argv[1] if len(sys.argv) > 1 else 'vid1'

recs = sorted((json.load(open(p)) for p in glob.glob(os.path.join(HERE, 'airlabels', 'clips', '*.json'))),
              key=lambda r: r['start'])
A0, A1 = recs[0]['start'], max(r['end'] for r in recs)
n = A1 - A0 + 1
truth = np.full(n, 'none', '<U6')
fid = np.full(n, -1)
marks = [(m['f'], m['s']) for r in recs for m in r.get('marks', [])]
marks.sort()
notdone = [r['id'] for r in recs if not r.get('done')]
print('clips %d, marks %d, not done: %s' % (len(recs), len(marks), notdone or 'none'))
for s in ('ground', 'air', 'touch', 'hidden'):
    print('  %-6s %d' % (s, sum(1 for m in marks if m[1] == s)))

cur, flight = 'none', -1
mi = 0
for i in range(n):
    f = A0 + i
    while mi < len(marks) and marks[mi][0] <= f:
        s = marks[mi][1]
        if s in ('air', 'touch'):
            flight += 1
        cur = 'air' if s == 'touch' else s
        mi += 1
    truth[i] = cur
    fid[i] = flight if cur == 'air' else -1
np.savez(os.path.join(HERE, 'airtruth.npz'), abs=np.arange(A0, A1 + 1), truth=truth, fid=fid)

# flights: contiguous same-id runs; note how each ends
fl = []
for k in range(flight + 1):
    idx = np.where(fid == k)[0]
    if not len(idx):
        continue
    a, b = idx[0], idx[-1]
    nxt = truth[b + 1] if b + 1 < n else 'end'
    if nxt == 'air':
        nxt = 'touch'
    prv = truth[a - 1] if a > 0 else 'start'
    if a > 0 and fid[a - 1] >= 0:
        prv = 'touch'
    fl.append((A0 + a, A0 + b, prv, nxt))
d = np.array([(b - a + 1) / 30 for a, b, _, _ in fl])
u, c = np.unique(truth, return_counts=True)
print('frames:', dict(zip(u, c)))
print('true flights %d: median %.1f s, max %.1f s; start after %s; end in %s' % (
    len(fl), np.median(d), d.max(),
    dict(zip(*np.unique([f[2] for f in fl], return_counts=True))),
    dict(zip(*np.unique([f[3] for f in fl], return_counts=True)))))

# ---- detector
B = np.load(os.path.join(HERE, '%s_ball3d.npz' % tag))
F = np.load(os.path.join(HERE, '%s_flights.npz' % tag))['F']
st = dict(zip(B['abs'].tolist(), B['state'].tolist()))
pred = np.array([st.get(A0 + i, 'none') for i in range(n)])
vis = truth != 'hidden'
T_air, P_air = (truth == 'air'), (pred == 'air')
print('\nper frame (visible frames only, %d):' % vis.sum())
print('  %-8s' % 'truth\\det' + ''.join('%8s' % s for s in ('air', 'ground', 'line', 'out', 'none')))
for t in ('air', 'ground'):
    m = vis & (truth == t)
    print('  %-8s' % t + ''.join('%8d' % (m & (pred == s)).sum() for s in ('air', 'ground', 'line', 'out', 'none')))
tp = (T_air & P_air & vis).sum()
print('  air precision %.2f  recall %.2f' % (tp / max(1, (P_air & vis).sum()), tp / max(1, (T_air & vis).sum())))

# per true flight: covered by a detected flight?
det = [(int(round(r[0])), int(round(r[1]))) for r in F]
print('\ndetected flights %d' % len(det))
hit = 0
rows = []
for a, b, prv, nxt in fl:
    ov = [(da, db) for da, db in det if min(b, db) - max(a, da) > 0]
    cov = sum(min(b, db) - max(a, da) for da, db in ov) / max(1, b - a)
    hit += cov >= 0.5
    rows.append((a, b, prv, nxt, cov, ov))
print('true flights with >=50%% covered: %d / %d' % (hit, len(fl)))
false = [(da, db) for da, db in det if not any(min(b, db) - max(a, da) > 0 for a, b, _, _ in fl)]
print('detected flights overlapping no true flight: %d  %s' % (len(false), false))
for da, db in det:
    ov = [(a, b) for a, b, _, _ in fl if min(b, db) - max(a, da) > 0]
    if ov:
        print('  det %d-%d (%.1fs): true %s  take err %+d, land err %+d frames' % (
            da, db, (db - da) / 30, ov, da - ov[0][0], db - ov[-1][1]))
print('\ntrue flights (start end dur from->to cover):')
for a, b, prv, nxt, cov, _ in rows:
    print('  %d-%d  %4.1fs  %-6s -> %-6s  %3.0f%%' % (a, b, (b - a + 1) / 30, prv, nxt, 100 * cov))
