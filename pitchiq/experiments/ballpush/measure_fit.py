"""Does any combination of classifier score + chain features beat the score alone?  Leave-one-game-out.

Fits (logistic regression, gradient boosting) ball-vs-clutter on the candidates of the click frames of 7
games, scores the 8th; pooled over folds: share of missed / right balls above the threshold that passes
0.1 / 0.3 / 1 / 3 % of clutter, next to the classifier score alone.
    python measure_fit.py [measure name]
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402

OUTD = os.path.join(common.OUT, 'tracker')
GAME = dict(nb1='nb', ww1='ww', ww2='ww', eb1='eb', eb2='eb', scrimh1='scrim', scrimh2='scrim', wh2='wh', wb1='wb', pi2='pi', co2='co')


def main():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    per = json.load(open(os.path.join(OUTD, 'replay', 'replay_oof_cls.json')))['per']['base']
    name = sys.argv[1] if len(sys.argv) > 1 else 'measure'
    z = np.load(os.path.join(OUTD, name + '.npz'))
    ids, R, cols = z['ids'], z['R'], list(z['cols'])
    C = {k: R[:, i] for i, k in enumerate(cols)}
    st = np.array([('right' if per[i]['right'] else 'wrong' if per[i]['ring'] else 'miss') if per[i]['v'] == 'ball' else 'out' for i in ids])
    game = np.array([GAME[per[i]['half']] for i in ids])
    isb = C['d'] <= 15
    truth = np.zeros(len(ids), bool)
    for i in np.unique(ids):
        m = np.flatnonzero((ids == i) & isb)
        if len(m):
            truth[m[np.argmin(C['d'][m])]] = True
    use = truth | ~isb  # drop second blobs on the ball
    fk = [c for c in cols if c not in ('q', 'c', 'd', 'x', 'y')]
    X = np.nan_to_num(np.column_stack([C[k] for k in fk]), nan=-1.0)
    sets = {'p alone': [fk.index('p')], 'p + geometry': [i for i, k in enumerate(fk) if not k.startswith(('mp', 'xp'))],
            'p + geometry + neighbours\' p': list(range(len(fk))), 'geometry alone': [i for i, k in enumerate(fk) if k != 'p' and not k.startswith(('mp', 'xp'))]}
    for model in ('lr', 'gb'):
        for nm, ix in sets.items():
            sc = np.full(len(ids), np.nan)
            for g in sorted(set(game)):
                tr, te = use & (game != g), game == g
                if model == 'lr':
                    m = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
                else:
                    m = HistGradientBoostingClassifier(max_depth=3, max_iter=150, learning_rate=0.05)
                m.fit(X[tr][:, ix], truth[tr])
                sc[te] = m.decision_function(X[te][:, ix])
            row = []
            for r in (0.1, 0.3, 1, 3):
                # the threshold per held game would be tuned on the others; pooled here (optimistic)
                th = np.percentile(sc[~isb], 100 - r)
                row.append('%4.2f %4.2f' % ((sc[truth & (st == 'miss')] > th).mean(), (sc[truth & (st == 'right')] > th).mean()))
            print('%s %-30s | miss right at clutter 0.1%%: %s | 0.3%%: %s | 1%%: %s | 3%%: %s' % (model, nm, *row))


if __name__ == '__main__':
    main()
