"""Does the crop classifier help the tracker?  Scored on Alex's 269 labels.

Two measurements, in this order, because the second is only worth reading
if the first moved:

  RANK   on labelled ball frames with a candidate within TOL of the mark, the
         rank of the best such candidate by the detector score s and by the
         classifier log-odds p.  No tracker involved - is the ball nearer the
         top of the list?
  TRACK  viterbi7 with each candidate's emission replaced by
         a * p + (1 - a) * s, over a small grid, tuned on one match half and
         reported on the other (wm_tune.py's split).

    python wm_cls.py          # needs wmNNcls.npz from clsscore.py
"""
import itertools
import os

import numpy as np

import dtrack6 as D6
import wm_score as W
import wm_tune as U

S2 = os.path.dirname(os.path.abspath(__file__))


def attach(F, tag):
    z = np.load(os.path.join(S2, '%scls.npz' % tag))
    p = {}
    for f, j, v in zip(z['f'], z['j'], z['p']):
        p[(int(f), int(j))] = float(v)
    for fr in F:
        fr['p'] = np.array([p[(fr['abs'], j)] for j in range(len(fr['s']))])
        fr['s0'] = fr['s'].copy()


def ranks(T, F, idx):
    r_s, r_p = [], []
    for t in T:
        if t['kind'] != 'ball':
            continue
        k = idx[t['clip']].get(t['abs'])
        if k is None:
            continue
        fr = F[t['clip']][k]
        d = np.hypot(fr['x'] - t['x'], fr['y'] - t['y'])
        ok = d <= W.TOL
        if not ok.any():
            continue
        for sc, out in ((fr['s0'], r_s), (fr['p'], r_p)):
            b = sc[ok].max()
            out.append(int((sc[~ok] > b).sum()))
    for name, r in (('detector s', r_s), ('classifier p', r_p)):
        r = np.array(r)
        print('  %-13s n %3d  rank0 %3.0f%%  top3 %3.0f%%  top5 %3.0f%%  '
              'median %d' % (name, len(r), 100 * (r == 0).mean(),
                             100 * (r < 3).mean(), 100 * (r < 5).mean(),
                             np.median(r)))


def main():
    T, tags, F, idx = U.load_all()
    for tag in tags:
        attach(F[tag], tag)
    print('RANK of the true ball among its frame\'s candidates')
    ranks(T, F, idx)

    halves = {'H1': {t for t in tags if int(t[2:]) < 9},
              'H2': {t for t in tags if int(t[2:]) >= 9}}
    grid = dict(a=[0.0, 0.5, 0.75, 1.0], e_off=[-1.0, -0.5, 0.0, 0.5],
                c_enter=[12.0, 20.0], c_near=[2.0, 6.0], skip=[4.0, 8.0])
    keys = list(grid)
    res = []
    for vals in itertools.product(*(grid[k] for k in keys)):
        cfg = dict(zip(keys, vals))
        a = cfg.pop('a')
        for tag in tags:
            for fr in F[tag]:
                fr['s'] = a * fr['p'] + (1 - a) * fr['s0']
        p = {tag: D6.viterbi7(F[tag], drift=12.0, gate=2.0, **cfg)
             for tag in tags}
        cfg['a'] = a
        res.append((cfg, {h: U.score(T, F, idx, p, cl)
                          for h, cl in halves.items()},
                    U.score(T, F, idx, p, set(tags))))
    print('\nTRACK  %d grid points' % len(res))
    for a in grid['a']:
        cfg, r, al = max((z for z in res if z[0]['a'] == a),
                         key=lambda z: z[2]['ok'])
        print('  a %.2f best-on-all  %s' % (a, U.fmt(al)))
    for tune, test in (('H1', 'H2'), ('H2', 'H1')):
        cfg, r, al = max(res, key=lambda z: z[1][tune]['ok'])
        print('\ntuned on %s: %s' % (tune, cfg))
        print('  tune %s %s' % (tune, U.fmt(r[tune])))
        print('  TEST %s %s' % (test, U.fmt(r[test])))


if __name__ == '__main__':
    main()
