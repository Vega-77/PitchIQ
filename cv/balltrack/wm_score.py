"""Score the tracker against Alex's whole-match labels (Ball Truth artifact).

    python wm_score.py [suffix]     # reads wmNN<suffix>picks.json, default ''

Truth is truthdb/truth/fNNN.json (ArtifactData list with out_dir).  Kinds:
ball (x, y in source pixels, optional air), off, hidden, skip.

Per stratum:
  ball   - tracker right (<= TOL px), wrong (committed elsewhere), no pick;
           the same for the detector's raw top candidate; and the ceiling:
           is ANY candidate within TOL (what a perfect tracker could reach).
  off    - a commit is an error; no pick is right.
  hidden - commit rate only; there is no position to be right or wrong about.
"""
import collections
import glob
import json
import os
import sys

import numpy as np

S2 = os.path.dirname(os.path.abspath(__file__))
TOL = 60.0


def truth():
    out = []
    for f in sorted(glob.glob(os.path.join(S2, 'truthdb', 'truth', '*.json'))):
        t = json.load(open(f))
        out.append(t.get('data', t))
    return out


def cands(tag, cache={}):
    if tag not in cache:
        z = np.load(os.path.join(S2, '%s.npz' % tag), allow_pickle=True)
        f = z['f']
        cache[tag] = (f, z['x'], z['y'], z['s'])
    return cache[tag]


def main():
    suf = sys.argv[1] if len(sys.argv) > 1 else ''
    T = truth()
    picks = {}
    for tag in sorted({t['clip'] for t in T}):
        picks[tag] = json.load(open(os.path.join(
            S2, '%s%spicks.json' % (tag, suf))))['picks']

    R = collections.defaultdict(collections.Counter)
    miss = []
    for t in T:
        st = t['stratum'].split('-')[0]
        p = picks[t['clip']].get(str(t['abs']))
        tx = None if p is None else p['tx']
        for g in (st, 'ALL'):
            c = R[g]
            c[t['kind']] += 1
            if t['kind'] == 'ball':
                f, x, y, s = cands(t['clip'])
                m = f == t['abs']
                d = np.hypot(x[m] - t['x'], y[m] - t['y'])
                c['ceil'] += bool(len(d) and d.min() <= TOL)
                if p is not None and np.hypot(p['rx'] - t['x'],
                                              p['ry'] - t['y']) <= TOL:
                    c['raw'] += 1
                if tx is None:
                    c['none'] += 1
                elif np.hypot(tx - t['x'], p['ty'] - t['y']) <= TOL:
                    c['right'] += 1
                else:
                    c['wrong'] += 1
                if g == 'ALL' and tx is not None and np.hypot(
                        tx - t['x'], p['ty'] - t['y']) > TOL:
                    miss.append(t['i'])
            elif t['kind'] == 'off':
                c['offcommit'] += tx is not None
            elif t['kind'] == 'hidden':
                c['hidcommit'] += tx is not None

    print('picks%s  TOL %d px' % (suf or '(base)', TOL))
    print('%-11s %5s %6s %6s %6s %6s %6s | %4s %8s | %4s %8s' % (
        'stratum', 'ball', 'right', 'wrong', 'none', 'raw', 'ceil',
        'off', 'commit', 'hid', 'commit'))
    for g in ('random', 'continuous', 'edge', 'ALL'):
        c = R[g]
        n = max(c['ball'], 1)
        print('%-11s %5d %5.0f%% %5.0f%% %5.0f%% %5.0f%% %5.0f%% | %4d %8d |'
              ' %4d %8d' % (g, c['ball'], 100. * c['right'] / n,
                            100. * c['wrong'] / n, 100. * c['none'] / n,
                            100. * c['raw'] / n, 100. * c['ceil'] / n,
                            c['off'], c['offcommit'], c['hidden'],
                            c['hidcommit']))
    a = R['ALL']
    ok = a['right'] + a['off'] - a['offcommit']
    print('\nframe accuracy over ball+off: %d / %d = %.0f%%' % (
        ok, a['ball'] + a['off'], 100. * ok / (a['ball'] + a['off'])))
    print('wrong-commit frames:', miss)


if __name__ == '__main__':
    main()
