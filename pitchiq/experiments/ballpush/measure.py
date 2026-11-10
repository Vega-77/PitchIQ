"""Is the motion evidence there?  For every dev click frame: every candidate's chain features
(motion.chain_feats), whether it is the clicked ball (within 15 px), and what the base replay did there.

    python measure.py            -> tracker/measure.npz  (one row per candidate on a click frame)
Needs tracker/replay/replay_oof_cls.json and replay_cache from `replay.py oof_cls base`.
"""
import glob
import json
import os
import sys
from multiprocessing import Pool

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402

OUTD = os.path.join(common.OUT, 'tracker')
CLS = os.path.join(common.SCR, 'oof_cls')
KS = (4, 8)
PAR = json.loads(os.environ.get('MS_PAR', '{}'))  # chain_feats settings
NAME = os.environ.get('MS_NAME', 'measure')


def job(a):
    pt, want = a  # want: [(iid, f, v, x, y)]
    import motion
    import replay

    common.point_at_data()
    from pitchiq.balltrack import ball_tracker

    os.chdir(common.BT)
    F = ball_tracker.load_frames(pt)
    replay.attach(F, pt, CLS)
    Pk = motion.pack(F)
    rows = []
    ts = [f - Pk['lo'] for _, f, _, _, _ in want]
    feats = {K: motion.chain_feats(Pk, ts, K=K, **PAR) for K in KS}
    for q, (iid, f, v, x, y) in enumerate(want):
        t = ts[q]
        for c in range(Pk['N']):
            if not np.isfinite(Pk['X'][t, c]):
                continue
            d = np.hypot(Pk['IX'][t, c] - x, Pk['IY'][t, c] - y) if v == 'ball' else np.nan
            r = [q, c, d, Pk['P'][t, c], Pk['IX'][t, c], Pk['IY'][t, c]]
            for K in KS:
                r += [feats[K][k][q, c] for k in ('nhit', 'nb', 'nf', 'speed', 'rms', 'mp', 'xp', 'nstat', 'run')]
            rows.append((iid, r))
    return rows


def main():
    common.point_at_data()
    from pitchiq.experiments.clicker import clickscore as cs

    items = {it['id']: it for it in json.load(open(os.path.join(cs.ROOT, 'items_all.json')))['items']}
    A = cs.answers(os.path.join(cs.ROOT, 'db'))
    jobs = {}
    for tag in common.DEV:
        pcs = []
        for pp in sorted(glob.glob(os.path.join(common.BT, '%s_q[0-9][0-9][0-9].npz' % tag))):
            z = np.load(pp, allow_pickle=True)
            pcs.append((os.path.basename(pp)[:-4], int(z['lo']), int(z['hi'])))
        for iid, a in A.items():
            it = items.get(iid)
            if not it or it['half'] != tag or a['v'] not in ('ball', 'out'):
                continue
            f = int(it['f'])
            best = max(pcs, key=lambda p: min(f - p[1], p[2] - f))
            if not (best[1] <= f <= best[2]):
                continue
            jobs.setdefault(best[0], []).append((iid, f, a['v'], a.get('x', np.nan), a.get('y', np.nan)))
    with Pool(4) as pool:
        res = pool.map(job, list(jobs.items()), chunksize=1)
    ids, R = [], []
    for rows in res:
        for iid, r in rows:
            ids.append(iid)
            R.append(r)
    cols = ['q', 'c', 'd', 'p', 'x', 'y'] + ['%s%d' % (k, K) for K in KS for k in ('nhit', 'nb', 'nf', 'speed', 'rms', 'mp', 'xp', 'nstat', 'run')]
    np.savez(os.path.join(OUTD, NAME + '.npz'), ids=np.array(ids), R=np.array(R, float), cols=np.array(cols))
    print('rows', len(R), 'clicks', len(set(ids)))


if __name__ == '__main__':
    main()
