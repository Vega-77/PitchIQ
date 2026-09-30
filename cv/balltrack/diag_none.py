"""Why does the tracker say None on a labelled ball frame?

For each 'ball' label the tracker leaves at None (cls_B emission, the
config tuned on H1), sort it into:

  MISS     no candidate within TOL of the mark - the detector never saw it
  TOP      the ball candidate is the frame's top classifier score
  LOWER    it is there but some other candidate outscores it

and print the ball candidate's log-odds p, so we can see whether a
"commit when confident" rule could pick it up.  Also prints the same
breakdown for the frames the tracker gets right, and the p of the top
candidate on 'off' frames (what a threshold would wrongly take).

    python diag_none.py
"""
import collections

import numpy as np

import dtrack6 as D6
import wm_cls as C
import wm_score as W
import wm_tune as U

CFG = dict(e_off=-0.5, c_enter=12.0, c_near=6.0, skip=4.0)


def main():
    T, tags, F, idx = U.load_all()
    for tag in tags:
        C.attach(F[tag], tag)
        for fr in F[tag]:
            fr['s'] = fr['p']
    picks = {tag: D6.viterbi7(F[tag], drift=12.0, gate=2.0, **CFG)
             for tag in tags}
    cat = collections.Counter()
    ps = collections.defaultdict(list)
    for t in T:
        k = idx[t['clip']].get(t['abs'])
        if k is None or t['kind'] not in ('ball', 'off'):
            continue
        fr = F[t['clip']][k]
        j = picks[t['clip']][k]
        top = float(fr['p'].max()) if len(fr['p']) else -99
        if t['kind'] == 'off':
            ps['off_top'].append(top)
            continue
        d = np.hypot(fr['x'] - t['x'], fr['y'] - t['y'])
        ok = d <= W.TOL
        state = ('none' if j is None else
                 'right' if ok[j] else 'wrong')
        if not ok.any():
            cat[(state, 'MISS')] += 1
            continue
        b = float(fr['p'][ok].max())
        where = 'TOP' if b >= top else 'LOWER'
        cat[(state, where)] += 1
        ps[(state, where)].append(b)
        if state != 'right':
            ps[(state, where, 'top')].append(top)
    for k in sorted(cat):
        print(k, cat[k])
    q = lambda v: np.percentile(v, [10, 25, 50, 75, 90]).round(1)
    for k in sorted(ps, key=str):
        print(k, 'n', len(ps[k]), 'p10/25/50/75/90', q(ps[k]))


if __name__ == '__main__':
    main()
