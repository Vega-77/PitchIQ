"""A new croplab round whose hints are the retrained classifier's guesses.

Pool: every candidate in the tr*crops.npz sampled frames that is not already
on a croplab sheet.  Three groups, most useful first:

  LOST     frames the precision tracker did not commit on: the classifier's
           top candidate there.  These are the frames the tracker loses the
           ball in today.
  DISPUTE  committed frames where the classifier's top candidate is NOT the
           tracker's pick (> 60 px away): one of the two is wrong.
  RANDOM   other top-5 candidates, for an unbiased check of the hints.

Hint = the classifier's most likely class.  The hint accuracy Alex's labels
give back is the honest number to watch; items from the model's own training
clips could flatter it slightly, but none of these crops was hand-labelled.

    python lab_sheets3.py <model.pt> [n_lost n_dispute n_random]
    -> croplab/sheets/uNNN.jpg, new sheets put FIRST in croplab/manifest.json
    env: PREFIX=v (sheet name), REPLACE=1 (drop older rounds), LABDB=labdb3
"""
import glob
import json
import os
import sys

import numpy as np

S2 = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, S2)
import lab_sheets2 as L2                          # noqa: E402
from train_cls import CLASSES, load_model         # noqa: E402

OUT = os.path.join(S2, 'croplab')
SEED = 20260930


def main():
    model = load_model(sys.argv[1])
    n = [int(v) for v in sys.argv[2:5]] or [100, 60, 40]
    rng = np.random.default_rng(SEED)
    man = json.load(open(os.path.join(OUT, 'manifest.json')))
    seen = {it['id'] for sh in man['sheets'] for it in sh['items']}
    for q in glob.glob(os.path.join(S2, os.environ.get('LABDB', 'labdb3'),
                                    'crops', '*.json')):
        d = json.load(open(q))
        seen.add('%s_%d_%d' % (d['tag'], d['f'], d['j']))
    pseudo = json.load(open(os.path.join(S2, 'pseudo_labels.json')))
    pick = {(r['tag'], r['f']): r['j'] for r in pseudo if r['label'] == 'ball'}
    committed = {(r['tag'], r['f']) for r in pseudo}

    lost, dispute, rand = [], [], []
    for path in sorted(glob.glob(os.path.join(S2, 'tr[0-9][0-9]crops.npz'))):
        tag = os.path.basename(path)[:4]
        z = np.load(path)
        pr = model.probs(z['crop'], z['ctx'], ctx_names=list(z['ctx_names']))
        lo = np.log(pr[:, 0] + 1e-9) - np.log(1 - pr[:, 0] + 1e-9)
        f, j, x, y = z['f'], z['j'], z['x'], z['y']
        for ab in np.unique(f):
            rows = np.where(f == ab)[0]
            order = rows[np.argsort(-lo[rows])]
            top = order[0]
            key = (tag, int(ab))

            def item(i):
                return dict(id='%s_%d_%d' % (tag, int(f[i]), int(j[i])),
                            tag=tag, f=int(f[i]), j=int(j[i]),
                            hint=CLASSES[int(np.argmax(pr[i]))],
                            p=round(float(pr[i, 0]), 3))
            if key not in committed:
                lost.append(item(top))
            elif key in pick:
                pi = rows[j[rows] == pick[key]]
                if len(pi) and np.hypot(x[top] - x[pi[0]],
                                        y[top] - y[pi[0]]) > 60:
                    dispute.append(item(top))
            for i in order[1:5]:
                rand.append(item(i))

    def take(L, k):
        L = [it for it in L if it['id'] not in seen]
        return [L[i] for i in rng.permutation(len(L))[:k]]

    a, b, c = take(lost, n[0]), take(dispute, n[1]), None
    used = {it['id'] for it in a + b}
    c = take([it for it in rand if it['id'] not in used], n[2])
    print('pool: lost %d dispute %d random %d' % (len(lost), len(dispute),
                                                  len(rand)))
    items = a + b + c
    for it, grp in zip(items, ['lost'] * len(a) + ['dispute'] * len(b)
                       + ['random'] * len(c)):
        it['grp'] = grp
    items = [items[i] for i in rng.permutation(len(items))]
    hints = {}
    for it in items:
        hints[it['hint']] = hints.get(it['hint'], 0) + 1
    print('hints:', hints)
    new = L2.build(items, os.environ.get('PREFIX', 'u'))
    # REPLACE=1: only the new round on the page (older rounds were hinted by
    # outdated models; their labels stay in the db)
    man['sheets'] = new + ([] if os.environ.get('REPLACE') else man['sheets'])
    json.dump(man, open(os.path.join(OUT, 'manifest.json'), 'w'))
    print('%d new sheets first, %d sheets in all' % (len(new),
                                                      len(man['sheets'])))


if __name__ == '__main__':
    main()
