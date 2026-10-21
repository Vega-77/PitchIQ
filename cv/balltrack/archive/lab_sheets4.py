"""A croplab round where motion and the classifier disagree.

On the training clips, viterbi7 (classifier emission) then gapfill.fill.
Every pick the fill ADDED is a crop motion calls the ball; the ones the
classifier scores below PMAX log-odds are the disagreements - the blurred,
airborne, half-hidden balls diag_none.py found the classifier throws away,
or the fill's mistakes.  Alex's label settles each, and either answer is a
useful training example.

  FILL   fill-added picks with p < PMAX              (most of the round)
  AGREE  fill-added picks with p >= PMAX             (check on the fill)

Hint = 'ball' (motion's claim), so a wrong hint is a fill mistake.
Never the wm test clips.

    python lab_sheets4.py [n_fill n_agree]      (defaults 170 30)
    env PREFIX (x), LABDB (labdb5)
    -> croplab/sheets/<PREFIX>NNN.jpg, manifest.json holds only this round
"""
import glob
import json
import os
import sys

import numpy as np

S2 = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, S2)
import dfree                                      # noqa: E402
import dtrack6 as D6                              # noqa: E402
import gapfill as G                               # noqa: E402
import lab_sheets2 as L2                          # noqa: E402
import wm_cls as C                                # noqa: E402

OUT = os.path.join(S2, 'croplab')
SEED = 20261001
PMAX = 0.0
BASE = dict(e_off=-0.5, c_enter=12.0, c_near=6.0, skip=4.0)
A = 0.75


def main():
    n = [int(v) for v in sys.argv[1:3]] or [170, 30]
    rng = np.random.default_rng(SEED)
    seen = set()
    for q in glob.glob(os.path.join(S2, os.environ.get('LABDB', 'labdb5'),
                                    'crops', '*.json')):
        d = json.load(open(q))
        seen.add('%s_%d_%d' % (d['tag'], d['f'], d['j']))

    fill, agree = [], []
    tot = dict(on=0, added=0)
    for path in sorted(glob.glob(os.path.join(S2, 'tr[0-9][0-9]cls.npz'))):
        tag = os.path.basename(path)[:4]
        F = dfree.load(tag)
        C.attach(F, tag)
        G.attach_pan(F, tag)
        for fr in F:
            fr['s'] = A * fr['p'] + (1 - A) * fr['s0']
        base = D6.viterbi7(F, drift=12.0, gate=2.0, **BASE)
        out = G.fill(F, base)
        for t, (b, o) in enumerate(zip(base, out)):
            tot['on'] += b is not None
            if b is not None or o is None:
                continue
            tot['added'] += 1
            fr = F[t]
            it = dict(id='%s_%d_%d' % (tag, fr['abs'], o), tag=tag,
                      f=int(fr['abs']), j=int(o), x=float(fr['x'][o]),
                      y=float(fr['y'][o]), hint='ball',
                      p=round(float(fr['p'][o]), 2))
            if it['id'] in seen:
                continue
            (fill if fr['p'][o] < PMAX else agree).append(it)
        print(tag, 'on', sum(b is not None for b in base),
              'added', sum(1 for b, o in zip(base, out)
                           if b is None and o is not None), flush=True)
    print('all: on %(on)d, fill added %(added)d' % tot,
          '| unlabelled disagree %d agree %d' % (len(fill), len(agree)))

    def take(L, k):
        # spread over clips and time: no two items within 9 frames
        L = [L[i] for i in rng.permutation(len(L))]
        got, used = [], {}
        for it in L:
            fs = used.setdefault(it['tag'], [])
            if any(abs(it['f'] - f) < 9 for f in fs):
                continue
            fs.append(it['f'])
            got.append(it)
            if len(got) == k:
                break
        return got

    a, b = take(fill, n[0]), take(agree, n[1])
    for it in a:
        it['grp'] = 'fill'
    for it in b:
        it['grp'] = 'agree'
    items = a + b
    items = [items[i] for i in rng.permutation(len(items))]
    print('round: fill %d agree %d' % (len(a), len(b)))
    new = L2.build(items, os.environ.get('PREFIX', 'x'))
    man = json.load(open(os.path.join(OUT, 'manifest.json')))
    man['sheets'] = new
    json.dump(man, open(os.path.join(OUT, 'manifest.json'), 'w'))
    print('%d sheets' % len(new))


if __name__ == '__main__':
    main()
