"""Data for the Flight Lab page:

  flightlab/frames.json  per shown frame (every 3rd): pixel -> StatsBomb homography
  flightlab/seed.json    flights from the Air Lab marks (times only)
  flightlab/model.json   the model's air frames (vflchain2) for the compare toggle

    python vflightlab.py
"""
import glob
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TAG = os.environ.get('TAG', 'vid1')
OUT = os.path.join(HERE, os.environ.get('LAB', 'flightlab'))
AIRLAB = os.environ.get('AIRLAB', 'airlab')
MODEL = os.environ.get('MODEL', TAG + '_ball3d2.npz')
STEP = 3
R_CH = 71.2 / 40.0


def main():
    os.makedirs(OUT, exist_ok=True)
    man = json.load(open(os.path.join(HERE, AIRLAB, 'manifest.json')))
    z = np.load(os.path.join(HERE, TAG + '_ballsb.npz'))
    ab = z['abs']; H = z['H']
    A0 = int(ab[0])
    frames = {}
    for c in man['clips']:
        for f in range(c['start'], c['end'] + 1, STEP):
            h = H[f - A0]
            q = h.dot([960, 1000, 1.0])
            h = h / q[2]                              # w > 0 on the grass
            frames[f] = [float('%.7g' % v) for v in h.ravel()]
    json.dump(dict(step=STEP, H=frames), open(os.path.join(OUT, 'frames.json'), 'w'), separators=(',', ':'))

    # seed flights from the Air Lab marks (all clips, in order)
    marks = []
    for p in sorted((glob.glob(os.path.join(HERE, 'airlabels', 'clips', '*.json')) if TAG == 'vid1' else [])):
        marks += json.load(open(p)).get('marks', [])
    marks.sort(key=lambda m: m['f'])
    fl = []
    prev = None
    for i, m in enumerate(marks):
        if m['s'] in ('air', 'touch'):
            if m['s'] == 'touch':
                k0 = 'touch'
            else:
                k0 = {'ground': 'kick', 'hidden': 'appears'}.get(prev, 'unknown')
            nxt = marks[i + 1] if i + 1 < len(marks) else None
            if nxt is None:
                continue
            k1 = {'ground': 'lands', 'touch': 'touch', 'hidden': 'lost', 'air': 'touch'}[nxt['s']]
            fl.append(dict(id=str(m['f']), f0=m['f'], f1=nxt['f'], k0=k0, k1=k1))
        prev = 'air' if m['s'] == 'touch' else m['s']
    json.dump(dict(flights=fl), open(os.path.join(OUT, 'seed.json'), 'w'), indent=0)

    # the model (label-free chains) for comparison
    mp = os.path.join(HERE, MODEL)
    B = np.load(mp) if os.path.exists(mp) else dict(abs=[], x=[], y=[], z=[], state=[])
    air = [[int(a), round(float(x), 2), round(float(y), 2), round(float(h) / R_CH, 3)]
           for a, x, y, h, s in zip(B['abs'], B['x'], B['y'], B['z'], B['state'])
           if s == 'air' and np.isfinite(x)]
    json.dump(dict(air=air), open(os.path.join(OUT, 'model.json'), 'w'), separators=(',', ':'))
    print('frames %d, flights %d (%s), model air frames %d' % (
        len(frames), len(fl), {k: sum(f['k0'] == k for f in fl) for k in ('kick', 'touch', 'appears', 'unknown')}, len(air)))
    for n in ('frames', 'seed', 'model'):
        print(n, os.path.getsize(os.path.join(OUT, n + '.json')))


if __name__ == '__main__':
    main()
