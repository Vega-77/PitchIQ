"""Per-frame 2D ball for one version over labelled windows -> p2d/<ver>/<tag>.npz

    python mkpicks.py <ver> <tag> [<tag> ...]

  abs, x, y (source px, nan = no ball), src (0 none, 1 confident pick, 2 gap fill, 3 TAPNext bridge)

Versions built here (detector candidates are drun's for every version):
  blob_raw        rank6 blob detector alone: its top candidate, every frame
  blob_v7         rank6 score + viterbi7 tracker (no classifier, no fill)
  cls_<name>      crop classifier <name> + viterbi7 + gapfill, the shipped settings
                  (cls_B is what ships); log-odds from cls/<name>/<tag>cls.npz
  cls_B_nofill    shipped, without the gap fill
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
sys.path.insert(0, S2)
import dfree                                       # noqa: E402
import dtrack6 as D6                               # noqa: E402
import gapfill as G                                # noqa: E402
import wm_cls as C                                 # noqa: E402

BASE = dict(e_off=-0.5, c_enter=12.0, c_near=6.0, skip=4.0)
A = 0.75


def window(tag):
    z = np.load(os.path.join(S2, '%s.npz' % tag), allow_pickle=True)
    return int(z['lo']), int(z['hi'])


def build(ver, tag):
    F = dfree.load(tag)
    if ver.startswith('cls_'):
        name = ver[4:].replace('_nofill', '')
        C.S2 = os.path.join(HERE, 'cls', name) if name != 'B' else S2
        C.attach(F, tag)
        G.attach_pan(F, tag)
        for fr in F:
            fr['s'] = A * fr['p'] + (1 - A) * fr['s0']
        base = D6.viterbi7(F, drift=12.0, gate=2.0, **BASE)
        out = base if ver.endswith('_nofill') else G.fill(F, base)
    elif ver == 'blob_v7':
        G.attach_pan(F, tag)
        for fr in F:
            fr['s0'] = fr['s'].copy()
        base = D6.viterbi7(F, drift=12.0, gate=2.0, **BASE)
        out = base
    elif ver == 'blob_raw':
        base = [int(np.argmax(fr['s'])) if len(fr['s']) else None for fr in F]
        out = base
    else:
        raise SystemExit('unknown version ' + ver)
    lo, hi = window(tag)
    n = hi - lo + 1
    x = np.full(n, np.nan); y = np.full(n, np.nan); src = np.zeros(n, np.int8)
    for fr, b, o in zip(F, base, out):
        i = fr['abs'] - lo
        if o is None or not (0 <= i < n):
            continue
        x[i], y[i] = fr['x'][o], fr['y'][o]
        src[i] = 1 if b is not None else 2
    d = os.path.join(HERE, 'p2d', ver); os.makedirs(d, exist_ok=True)
    np.savez(os.path.join(d, '%s.npz' % tag), abs=np.arange(lo, hi + 1), x=x, y=y, src=src)
    print('%-14s %-5s  conf %.3f  fill %.3f' % (ver, tag, (src == 1).mean(), (src == 2).mean()), flush=True)


if __name__ == '__main__':
    for t in sys.argv[2:]:
        build(sys.argv[1], t)
