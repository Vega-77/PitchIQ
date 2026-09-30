"""The tracker on a window that has no hand marks.

dtrack2.load() is the loader every downstream script calls, and it hard
requires three things that only exist for the 1676-frame labelled range:
$SP/label/manifest.json, $S2/lab5/labels, and a pan.npz whose lo/hi cover
the window.  A random five minutes of play has none of them.

This is that loader with the label half removed and the pan half pointed
at whatever drun.py captured for the same range.  Everything that
produces coordinates is copied unchanged - same bucketing by frame, same
cum[ab - plo] applied to get the stabilised sx/sy the transition term is
judged in - so a frame dict out of here is the same object dtrack5 has
always been handed, minus the ground truth it never read anyway.

The picks file it writes is dmiss5.dump's schema exactly, so dspanf.py
and dtracef.py read it the way dspan.py reads dbest5.json.  One field is
deliberately different: `hits` is null, not a number.  There is nothing
to be right or wrong against in this window, and writing a 0 or a
plausible-looking count there would be inventing a score.

    python dfree.py drun            # drun.npz + drunpan.npz -> drunpicks.json
"""
import io
import json
import os
import sys
import time

import numpy as np

import dtrack5 as D5

S2 = os.path.dirname(os.path.abspath(__file__))

# dbest5.json's winner, found by the sweep over the labelled range at 42/47.
CFG = dict(lam=0.125, skip=8.0, brk=32.0, kmax=4,
           rad0=0.0, drift=12.0, gate=0.5)


def load(tag='drun'):
    """frames = [{abs, x, y, s, sx, sy}], oldest first.  No labels."""
    z = np.load(os.path.join(S2, '%s.npz' % tag), allow_pickle=True)
    f, x, y, s = z['f'], z['x'], z['y'], z['s']
    lo, hi = int(z['lo']), int(z['hi'])
    o = np.argsort(f, kind='stable')
    f, x, y, s = f[o], x[o], y[o], s[o]
    edges = np.searchsorted(f, np.arange(lo, hi + 2))

    p = np.load(os.path.join(S2, '%span.npz' % tag))
    cum, plo = p['cum'], int(p['lo'])
    assert plo <= lo and int(p['hi']) >= hi, 'pan does not cover the window'

    frames = []
    for k, ab in enumerate(range(lo, hi + 1)):
        a, b = edges[k], edges[k + 1]
        if b <= a:
            continue
        cx = x[a:b].astype(np.float64)
        cy = y[a:b].astype(np.float64)
        H = cum[ab - plo]
        q = np.column_stack([cx, cy, np.ones(len(cx))]).dot(H[:2].T)
        frames.append(dict(abs=ab, x=cx, y=cy, s=s[a:b].astype(np.float64),
                           sx=q[:, 0], sy=q[:, 1]))
    return frames


def dump(frames, pick, name, cfg):
    """dmiss5.dump's schema, with hits left null - see the docstring."""
    picks = {}
    for t, fr in enumerate(frames):
        r = int(np.argmax(fr['s']))
        j = pick[t]
        picks[str(int(fr['abs']))] = dict(
            rx=float(fr['x'][r]), ry=float(fr['y'][r]),
            tx=None if j is None else float(fr['x'][j]),
            ty=None if j is None else float(fr['y'][j]))
    io.open(os.path.join(S2, name), 'w', encoding='utf-8').write(
        json.dumps(dict(cfg=cfg, hits=None, picks=picks)))


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else 'drun'

    t = time.time()
    frames = load(tag)
    print('%s: %d frames with candidates, %.1f s of video'
          % (tag, len(frames), len(frames) / D5.FPS))
    print('  candidates per frame: min %d  median %d  max %d'
          % (min(len(fr['s']) for fr in frames),
             int(np.median([len(fr['s']) for fr in frames])),
             max(len(fr['s']) for fr in frames)))
    print('  loaded in %.1f s' % (time.time() - t))

    cfgs = ('viterbi5 lam %g skip %g break %g kmax %d rad0 %g drift %g'
            ' gate %g' % (CFG['lam'], CFG['skip'], CFG['brk'], CFG['kmax'],
                          CFG['rad0'], CFG['drift'], CFG['gate']))
    print()
    print('tracking: %s  (stabilised coordinates)' % cfgs, flush=True)
    t = time.time()
    pick, obj = D5.viterbi5(frames, CFG['lam'], CFG['skip'], CFG['brk'],
                            CFG['kmax'], CFG['rad0'], CFG['drift'],
                            CFG['gate'], stab=True, ret_obj=True)
    el = time.time() - t
    ncom = sum(1 for j in pick if j is not None)
    print('  %.1f s, objective %.1f' % (el, obj))
    print('  the path commits to a candidate in %d of %d frames (%.0f%%)'
          % (ncom, len(pick), 100.0 * ncom / len(pick)))

    out = '%spicks.json' % tag
    dump(frames, pick, out, cfgs)
    print('  wrote %s  (hits: null - this window has no hand marks)' % out)


if __name__ == '__main__':
    main()
