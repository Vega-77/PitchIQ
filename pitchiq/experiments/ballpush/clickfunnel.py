"""Where is each practice-game ball click lost, on the retrained chain?  Read-only.

For every visible-ball click on a dev half, the first stage that loses it:
  blob      no blob within R px (blobsweep_30_s60_c10_SMALL6.json: the shipped DV30/SMALL6 cut)
  ranker    a blob, but not among the frame's top-40 candidates
  cls rank  in the top 40, but the classifier prefers another candidate
  cls low   the classifier's top pick, but at or under the ring threshold (2)
  drawn     classifier top and confident: then whether clickscore drew a ring there (clicks.json)

    python -m ... clickfunnel.py   (run from the repo root so pitchiq imports)
"""
import glob
import json
import os
from collections import Counter, defaultdict

import numpy as np

from pitchiq.experiments.clicker import clickscore as cs

HERE = os.path.dirname(os.path.abspath(__file__))
BT = 'C:/Users/alexv/Desktop/Repos/PitchIQ/pitchiq/balltrack'
DEV = ['nb1', 'ww1', 'ww2', 'eb1', 'eb2', 'scrimh1', 'scrimh2', 'wh2', 'wb1', 'pi2', 'co2']
R = 8.0
RING = 2.0

items = {it['id']: it for it in json.load(open(os.path.join(cs.ROOT, 'items_all.json')))['items']}
A = cs.answers(os.path.join(cs.ROOT, 'db'))
blob = {r[0]: r[4] for r in json.load(open(os.path.join('C:/Users/alexv/Desktop/Repos/PitchIQ-data/scratch-2026-10-26', 'blobsweep_30_s60_c10_SMALL6.json')))}
clicks = json.load(open(os.path.join(cs.ROOT, 'clicks.json')))
why = {w['id']: w['why'] for t in DEV for w in clicks[t]['wrong']}

want = defaultdict(list)
for i, a in A.items():
    it = items.get(i)
    if it and it['half'] in DEV and a['v'] == 'ball':
        want[it['half']].append((i, int(it['f']), a))

ORDER = ['blob', 'ranker', 'cls rank', 'cls low', 'tracker/gate', 'ringed, off', 'ringed, right']
tot = Counter()
LOW = []
for tag in DEV:
    pieces = sorted(glob.glob(os.path.join(BT, '%s_q[0-9][0-9][0-9].npz' % tag)))
    c = Counter()
    for i, f, a in want[tag]:
        w = why.get(i, '')
        drawn = 'ring on the ball' if not w else ('off' if 'px off' in w else None)
        for pp in pieces:
            z = np.load(pp, allow_pickle=True)
            if int(z['lo']) <= f <= int(z['hi']):
                break
        else:
            c['no piece'] += 1
            continue
        bd = blob.get(i)
        if bd is None or bd > R:
            st = 'blob'
        else:
            o = np.argsort(z['f'], kind='stable')
            sel = o[z['f'][o] == f]
            k = np.load(pp.replace('.npz', 'cls.npz'))
            m = k['f'] == f
            p = np.full(len(sel), -99.0)
            p[k['j'][m]] = k['p'][m]
            d = np.hypot(z['x'][sel] - a['x'], z['y'][sel] - a['y']) if len(sel) else np.array([1e9])
            j = int(np.argmin(d))
            if d[j] > R:
                st = 'ranker'
            elif (p > p[j]).sum():
                st = 'cls rank'
            elif p[j] <= RING:
                st = 'cls low'
                LOW.append(float(p[j]))
            else:
                st = 'tracker/gate'
        # a ring drawn on the ball wins whatever the per-frame stages say (the tracker can carry it)
        if drawn == 'ring on the ball':
            st = 'ringed, right'
        elif drawn == 'off':
            st = 'ringed, off'
        c[st] += 1
    tot.update(c)
    print('%-8s' % tag, '  '.join('%s %d' % (s, c[s]) for s in ORDER if c[s]))
print('ALL     ', '  '.join('%s %d' % (s, tot[s]) for s in ORDER), ' of', sum(tot.values()))
print('cls low scores: quartiles', np.percentile(LOW, [25, 50, 75]).round(2), ' in (1, 2]:', sum(1 for v in LOW if v > 1), ' in (0, 1]:', sum(1 for v in LOW if 0 < v <= 1), ' <= 0:', sum(1 for v in LOW if v <= 0))
