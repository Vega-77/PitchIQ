"""Tune viterbi6 on half the whole-match clips, score it on the other half.

The 269 labels are the only yardstick there is, so tuning and reporting on
all of them would grade the tracker on its own answer key.  Clips split by
match half (wm00-08 first half, wm09-17 second), which puts one continuous
clip on each side - a parity split put both on one side; each half picks
its best grid point, which is then scored on the OTHER half.

Objective: frame accuracy over ball + off frames (right position, or
correctly saying "not on screen").  Hidden frames are not scored.

    python wm_tune.py            # viterbi6
    python wm_tune.py 7          # viterbi7, GRID7
"""
import collections
import itertools
import json
import os
import sys

import numpy as np

import dfree
import dtrack5 as D5
import dtrack6 as D6
import wm_score as W

TOL = W.TOL


def load_all():
    T = W.truth()
    tags = sorted({t['clip'] for t in T})
    F = {tag: dfree.load(tag) for tag in tags}
    idx = {tag: {fr['abs']: k for k, fr in enumerate(F[tag])} for tag in tags}
    return T, tags, F, idx


def score(T, F, idx, picks, clips):
    c = collections.Counter()
    for t in T:
        if t['clip'] not in clips or t['kind'] not in ('ball', 'off'):
            continue
        k = idx[t['clip']].get(t['abs'])
        j = None if k is None else picks[t['clip']][k]
        c[t['kind']] += 1
        if t['kind'] == 'off':
            c['ok'] += j is None
            continue
        if j is None:
            c['none'] += 1
            continue
        fr = F[t['clip']][k]
        if np.hypot(fr['x'][j] - t['x'], fr['y'][j] - t['y']) <= TOL:
            c['right'] += 1
            c['ok'] += 1
        else:
            c['wrong'] += 1
    return c


def fmt(c):
    return ('acc %3d/%3d %3.0f%% | ball right %3d wrong %3d none %3d / %3d'
            ' | off ok %2d / %2d' % (
                c['ok'], c['ball'] + c['off'],
                100.0 * c['ok'] / max(1, c['ball'] + c['off']),
                c['right'], c['wrong'], c['none'], c['ball'],
                c['ok'] - c['right'], c['off']))


GRID = dict(lam=[0.125], skip=[4.0, 8.0], kmax=[4, 8],
            e_off=[-0.5, 0.0, 0.5, 1.0],
            c_leave=[2.0, 6.0, 12.0], c_enter=[2.0, 6.0, 12.0],
            A=[2.3], ab=[0.0, 2.0])


GRID7 = dict(skip=[2.0, 4.0, 8.0], kmax=[4], e_off=[-1.0, -0.5, 0.0],
             c_leave=[2.0], c_enter=[8.0, 12.0, 20.0],
             c_near=[0.0, 2.0, 6.0], drift=[12.0, 37.0], gate=[0.5, 2.0])


def main():
    v7 = len(sys.argv) > 1 and sys.argv[1] == '7'
    grid, fn, out = ((GRID7, D6.viterbi7, 'wm_tune7.json') if v7
                     else (GRID, D6.viterbi6, 'wm_tune.json'))
    T, tags, F, idx = load_all()
    halves = {'H1': {t for t in tags if int(t[2:]) < 9},
              'H2': {t for t in tags if int(t[2:]) >= 9}}

    base = {tag: D5.viterbi5(F[tag], **dfree.CFG, stab=True) for tag in tags}
    print('viterbi5 baseline')
    for h, cl in halves.items():
        print('  %-4s %s' % (h, fmt(score(T, F, idx, base, cl))))
    print('  all  %s' % fmt(score(T, F, idx, base, set(tags))), flush=True)

    keys = list(grid)
    res = []
    for vals in itertools.product(*(grid[k] for k in keys)):
        cfg = dict(zip(keys, vals))
        p = {tag: fn(F[tag], **cfg) for tag in tags}
        r = {h: score(T, F, idx, p, cl) for h, cl in halves.items()}
        r.update({tag: score(T, F, idx, p, {tag}) for tag in tags})
        res.append((cfg, r))
    print('\n%d grid points' % len(res))
    for tune, test in (('H1', 'H2'), ('H2', 'H1')):
        cfg, r = max(res, key=lambda z: z[1][tune]['ok'])
        print('\ntuned on %s: %s' % (tune, cfg))
        print('  tune %-4s %s' % (tune, fmt(r[tune])))
        print('  TEST %-4s %s' % (test, fmt(r[test])))
    json.dump([(cfg, {h: dict(c) for h, c in r.items()}) for cfg, r in res],
              open(os.path.join(W.S2, out), 'w'))


if __name__ == '__main__':
    main()
