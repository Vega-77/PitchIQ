"""Can track-shape features together flag wrong rings?  Leave-one-game-out logistic / boosted model on
runfeat's features of every ringed click; rings whose out-of-game wrong-probability passes a bar are
dropped.  The bar is also chosen without the held game (best wrong removed - 2 * right removed).

    python runfit.py [variant] [window]
"""
import sys

import numpy as np

import runfeat


def main():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    var = sys.argv[1] if len(sys.argv) > 1 else 'base'
    W = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    rows = runfeat.table(var, W)
    X = np.nan_to_num(np.array([r[4] for r in rows], float), nan=0.0)
    wrong = np.array([not r[2] for r in rows])
    game = np.array([runfeat.GAME[r[1]] for r in rows])
    N = runfeat.NAMES
    sets = {'shape only (acc acc90 res1 res2 speed turn)': [N.index(k) for k in ('acc', 'acc90', 'res1', 'res2', 'speed', 'turn')],
            'score only (pmean pmin p0)': [N.index(k) for k in ('pmean', 'pmin', 'p0')],
            'all': list(range(len(N)))}
    print('%s, window +-%d: %d right, %d wrong rings' % (var, W, (~wrong).sum(), wrong.sum()))
    for model in ('lr', 'gb'):
        for nm, ix in sets.items():
            rm_w = rm_r = 0
            for g in sorted(set(game)):
                tr, te = game != g, game == g

                def fit(m):
                    if model == 'lr':
                        c = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=2000))
                    else:
                        c = HistGradientBoostingClassifier(max_depth=2, max_iter=60, learning_rate=0.05)
                    return c.fit(X[m][:, ix], wrong[m])
                # the bar: from out-of-game scores inside the training games
                sc = np.zeros(tr.sum())
                gt = game[tr]
                for g2 in sorted(set(gt)):
                    m2 = tr & (game != g2)
                    sc[gt == g2] = fit(m2).predict_proba(X[tr & (game == g2)][:, ix])[:, 1]
                bo, bar = 0, np.inf
                for th in np.unique(sc):
                    o = (wrong[tr] & (sc >= th)).sum() - 2 * (~wrong[tr] & (sc >= th)).sum()
                    if o > bo:
                        bo, bar = o, th
                p = fit(tr).predict_proba(X[te][:, ix])[:, 1]
                rm_w += (wrong[te] & (p >= bar)).sum()
                rm_r += (~wrong[te] & (p >= bar)).sum()
            print('  %s %-45s leave-one-game-out: -%d wrong / -%d right' % (model, nm, rm_w, rm_r))


if __name__ == '__main__':
    main()
