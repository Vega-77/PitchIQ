"""Tables from measure.npz: chain support of the clicked ball vs clutter, by what the base replay did."""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402

OUTD = os.path.join(common.OUT, 'tracker')


def auc(a, b):
    """P(a > b) + half ties."""
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if not len(a) or not len(b):
        return np.nan
    s = np.sort(b)
    return float((np.searchsorted(s, a, 'left') + np.searchsorted(s, a, 'right')).sum() / (2.0 * len(a) * len(b)))


def main():
    z = np.load(os.path.join(OUTD, 'measure.npz'))
    ids, R, cols = z['ids'], z['R'], list(z['cols'])
    C = {k: R[:, i] for i, k in enumerate(cols)}
    per = json.load(open(os.path.join(OUTD, 'replay', 'replay_oof_cls.json')))['per']['base']
    st = np.array([('right' if per[i]['right'] else 'wrong' if per[i]['ring'] else 'miss') if per[i]['v'] == 'ball'
                   else ('outring' if per[i]['ring'] else 'outok') for i in ids])
    isb = C['d'] <= 15
    # one true candidate per click: the nearest within 15 px
    truth = np.zeros(len(ids), bool)
    for i in np.unique(ids):
        m = np.flatnonzero((ids == i) & isb)
        if len(m):
            truth[m[np.argmin(C['d'][m])]] = True
    clut = ~isb
    print('clicks with a candidate on the ball, by base outcome:')
    for s in ('right', 'wrong', 'miss'):
        n = len(np.unique(ids[st == s]))
        print('  %-6s %3d clicks, %3d with a blob on the ball' % (s, n, (truth & (st == s)).sum()))
    for K in (4, 8):
        nh, sp, rms, mp, ns = (C['%s%d' % (k, K)] for k in ('nhit', 'speed', 'rms', 'mp', 'nstat'))
        print('\n== window +-%d frames (support out of %d)' % (K, 2 * K))
        print('%-34s %5s | nhit quartiles | share nhit>=%d | >=%d | still(nstat>=%d) | speed med | rms med | mp med' % ('group', 'n', K, int(1.5 * K), K))
        groups = [('ball, base right', truth & (st == 'right')), ('ball, base missed', truth & (st == 'miss')),
                  ('ball, base wrong', truth & (st == 'wrong')),
                  ('ball missed, p<=0', truth & (st == 'miss') & (C['p'] <= 0)),
                  ('ball missed, 0<p<=2', truth & (st == 'miss') & (C['p'] > 0) & (C['p'] <= 2)),
                  ('ball missed, p>2', truth & (st == 'miss') & (C['p'] > 2)),
                  ('clutter, all', clut), ('clutter, p>0', clut & (C['p'] > 0)), ('clutter, p>2', clut & (C['p'] > 2)),
                  ('clutter on out frames, p>0', clut & np.isin(st, ['outok', 'outring']) & (C['p'] > 0)),
                  ('clutter -2<p<=0', clut & (C['p'] > -2) & (C['p'] <= 0))]
        for nm, m in groups:
            if not m.sum():
                continue
            v = nh[m]
            print('%-34s %5d | %4.0f %4.0f %4.0f | %5.2f | %5.2f | %5.2f | %5.1f | %5.2f | %5.2f' % (
                nm, m.sum(), *np.nanpercentile(v, [25, 50, 75]), (v >= K).mean(), (v >= 1.5 * K).mean(),
                (ns[m] >= K).mean(), np.nanmedian(sp[m]), np.nanmedian(rms[m]), np.nanmedian(mp[m])))
        mb = truth & (st == 'miss')
        for lo, hi in ((-99, -2), (-2, 0), (0, 2), (2, 99)):
            a, b = mb & (C['p'] > lo) & (C['p'] <= hi), clut & (C['p'] > lo) & (C['p'] <= hi)
            print('  p in (%g, %g]: missed ball n=%d vs clutter n=%d: AUC nhit %.3f, AUC mp %.3f, AUC nstat %.3f, AUC speed %.3f' % (
                lo, hi, a.sum(), b.sum(), auc(nh[a], nh[b]), auc(mp[a], mp[b]), auc(ns[a], ns[b]), auc(sp[a], sp[b])))
        # per click: is the ball the candidate with the most support on its frame?  among p > -2 candidates
        for s in ('miss', 'right'):
            n = top = 0
            for i in np.unique(ids[truth & (st == s)]):
                m = (ids == i)
                tb = np.flatnonzero(m & truth)[0]
                oth = m & ~isb & (C['p'] > C['p'][tb] - 1e-9)
                # clutter scoring at least the ball's p with at least the ball's support
                top += not (nh[oth] >= nh[tb]).any()
                n += 1
            print('  %s balls: on %d/%d frames no clutter blob has both p >= the ball\'s and support >= the ball\'s' % (s, top, n))
    # wrong rings: the picked candidate's support vs right rings'
    print('\n(wrong rings are measured from the replay cache in runfeat.py)')


if __name__ == '__main__':
    main()
