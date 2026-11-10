"""From measure*.npz: how many missed balls a chain feature recovers at a clutter rate the tracker could live with.

A frame has ~39 clutter blobs, so a cue has to fire on well under 1% of clutter before it can pick the ball.
For each feature: the threshold at which 0.1 / 0.3 / 1 / 3 % of clutter blobs pass, and the share of missed
(and of ringed-right) balls that pass it.
    python measure_lowfp.py [measure name ...]
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402

OUTD = os.path.join(common.OUT, 'tracker')


def main():
    per = json.load(open(os.path.join(OUTD, 'replay', 'replay_oof_cls.json')))['per']['base']
    for name in sys.argv[1:] or ['measure']:
        z = np.load(os.path.join(OUTD, name + '.npz'))
        ids, R, cols = z['ids'], z['R'], list(z['cols'])
        C = {k: R[:, i] for i, k in enumerate(cols)}
        st = np.array([('right' if per[i]['right'] else 'wrong' if per[i]['ring'] else 'miss') if per[i]['v'] == 'ball'
                       else 'out' for i in ids])
        isb = C['d'] <= 15
        truth = np.zeros(len(ids), bool)
        for i in np.unique(ids):
            m = np.flatnonzero((ids == i) & isb)
            if len(m):
                truth[m[np.argmin(C['d'][m])]] = True
        clut = ~isb
        miss, right = truth & (st == 'miss'), truth & (st == 'right')
        print('\n#### %s: missed balls %d, right balls %d, clutter %d' % (name, miss.sum(), right.sum(), clut.sum()))
        feats = {}
        for K in (4, 8):
            nh, run, sp, rms, mp = (np.nan_to_num(C['%s%d' % (k, K)], nan=0.0) for k in ('nhit', 'run', 'speed', 'rms', 'mp'))
            feats['nhit%d' % K] = nh
            feats['run%d' % K] = run
            feats['nhit%d, speed>=4' % K] = np.where(sp >= 4, nh, 0)
            feats['nhit%d - rms' % K] = nh - np.nan_to_num(C['rms%d' % K], nan=9.0)
            feats['nhit%d + p/4' % K] = nh + np.clip(C['p'], -12, 6) / 4
            feats['nhit%d + mp/4' % K] = nh + np.clip(np.nan_to_num(C['mp%d' % K], nan=-12), -12, 6) / 4
        feats['p alone'] = C['p']
        print('%-22s | %s' % ('feature', ' | '.join('clutter %.1f%%: miss  right' % r for r in (0.1, 0.3, 1, 3))))
        for nm, v in feats.items():
            row = []
            for r in (0.1, 0.3, 1, 3):
                th = np.percentile(v[clut], 100 - r)
                row.append('   > %6.2f: %4.2f  %4.2f' % (th, (v[miss] > th).mean(), (v[right] > th).mean()))
            print('%-22s | %s' % (nm, ' | '.join(row)))


if __name__ == '__main__':
    main()
