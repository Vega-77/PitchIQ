"""Retrain the candidate ranker (rank6.model.json's job) from Ball Clicker answers.

The first ranker's trainer was never kept. This one learns from the frames the user
clicked: every blob the blob finder gives there, with the detector's own 66 columns
(still_rows + temporal_rows over the 21-frame window, exactly as detect_candidates
computes them), and no top-KEEP cut.

  positive   the blob nearest a 'ball' click, if it is within R px
  negative   every other blob on a 'ball' frame, and every blob on an 'out' frame
  skipped    'dead', 'cant' and 'hidden' answers

    python -m pitchiq.balltrack.train_ranker feats <items.json> <out.npz> [--procs 14] [--dv 30 --small 6]
    python -m pitchiq.balltrack.train_ranker logo <feats.npz> [--obj binary|pairwise] [--oof oof.npz]
    python -m pitchiq.balltrack.train_ranker train <feats.npz> <out.model.json> [--obj ...]

items.json: {item id: {"video": path, "f": abs frame, "v": answer, "x": .., "y": .., "half": tag}}.
feats also scores each frame with the shipped blobs and rank6 (the old top-KEEP and its scores),
so `logo` can compare old and new top-KEEP recall on the same clicks, and `train` can match the
new score's scale to the old one (the tracker blends the ranker score with the classifier's
log-odds, ball_tracker / render_review_video.A).
"""

import argparse
import json
import os
from collections import defaultdict
from multiprocessing import Pool

import numpy as np

S2 = os.path.dirname(os.path.abspath(__file__))
R = 8.0  # px: a blob this close to the click is the ball
USE = ('ball', 'out')
PARAMS = dict(max_depth=6, eta=0.3, rounds=400)  # rank6's own settings


def game(tag):
    """The game a half belongs to: 'scrimh1' -> 'scrim', 'nb1' -> 'nb'."""
    return tag[:-1] if not tag.startswith('scrim') else 'scrim'


def _init(dv, small):
    for v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[v] = '1'
    import cv2

    cv2.setNumThreads(1)
    global G
    G = dict(dv=dv, small=small)


def _rows(bgrs, mats, fids, cmaj, cara, bc, cf, tf):
    bs, _ = bc.blobs(bgrs[cf.CZERO], cmaj)
    if not bs:
        return np.zeros((0, 66), np.float32), np.zeros((0, 2), np.float32)
    Xs, pts = cf.still_rows(bs, cmaj, cara)
    tiles = cf.tiles_for(bgrs, mats, fids, {})
    return np.hstack([Xs, tf.temporal_rows(tiles, pts)]).astype(np.float32), pts


def _one(job):
    """Both blob sets (new cut, shipped cut) for one clicked frame, with the old ranker's scores on the shipped set."""
    import cv2
    import xgboost as xgb

    from . import blob_candidates as bc
    from . import candidate_features as cf
    from . import pan_registration
    from . import temporal_features as tf
    from .detect_candidates import reg_detect

    iid, it = job
    if 'old' not in G:
        G['old'] = xgb.Booster()
        G['old'].load_model(os.path.join(S2, 'rank6.model.json'))
        z = np.load(os.path.join(S2, 'still4.npz'), allow_pickle=True)
        G['cm'] = (z['cmaj'], z['cara'])
    cmaj, cara = G['cm']
    cap = cv2.VideoCapture(it['video'])
    f0 = int(it['f']) + cf.OFFS[0]
    cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    bgrs, kds, fids = [], [], []
    for k in range(cf.NOFF):
        ok, img = cap.read()
        if not ok:
            return iid, None
        bgrs.append(img)
        kds.append(reg_detect(cv2, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)))
        fids.append(f0 + k)
    cap.release()
    reg = pan_registration.Registrar(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True), None)
    mats, _ = reg.transforms(kds, fids, cf.CZERO)
    out = {}
    for name, dv, small in (('new', G['dv'], G['small']), ('old', None, 0)):
        bc.DV, bc.SMALL = dv, small
        out[name] = _rows(bgrs, mats, fids, cmaj, cara, bc, cf, tf)
    X, _ = out['old']
    out['old_s'] = G['old'].predict(xgb.DMatrix(X)) if len(X) else np.zeros(0, np.float32)
    return iid, out


def feats(items_path, out_path, procs, dv, small):
    """Write <out.npz>: per new-cut blob X (n, 66), its centre pts (n, 2) in source px, item index, label, distance to the click;
    per item its id, half, answer; and for the shipped cut, per item, whether its top-KEEP holds the
    click (old_hit) and its top-KEEP scores (old_s, old_si)."""
    from .candidate_features import KEEP

    items = json.load(open(items_path))
    jobs = [(i, it) for i, it in sorted(items.items()) if it['v'] in USE]
    X, PT, IT, Y, D, OS, OSI = [], [], [], [], [], [], []
    ids, halves, ans, old_hit, nnew, nold = [], [], [], [], [], []
    with Pool(procs, initializer=_init, initargs=(dv, small)) as pool:
        for n, (iid, o) in enumerate(pool.imap_unordered(_one, jobs)):
            if o is None:
                print('read failed', iid, flush=True)
                continue
            it = items[iid]
            k = len(ids)
            ids.append(iid)
            halves.append(it['half'])
            ans.append(it['v'])
            Xn, pn = o['new']
            Xo, po = o['old']
            y = np.zeros(len(Xn), np.int8)
            d = np.full(len(Xn), np.inf, np.float32)
            hit = False
            if it['v'] == 'ball':
                if len(pn):
                    d = np.hypot(pn[:, 0] - it['x'], pn[:, 1] - it['y']).astype(np.float32)
                    j = int(np.argmin(d))
                    if d[j] <= R:
                        y[j] = 1
                if len(po):
                    top = np.argsort(-o['old_s'])[:KEEP]
                    hit = bool((np.hypot(po[top, 0] - it['x'], po[top, 1] - it['y']) <= R).any())
            old_hit.append(hit)
            nnew.append(len(Xn))
            nold.append(len(Xo))
            X.append(Xn)
            PT.append(pn.reshape(-1, 2))
            IT.append(np.full(len(Xn), k, np.int32))
            Y.append(y)
            D.append(d)
            s = np.sort(o['old_s'])[::-1][:KEEP]
            OS.append(s)
            OSI.append(np.full(len(s), k, np.int32))
            if n % 50 == 0:
                print(
                    '%d/%d %s  new %d blobs, old %d' % (n, len(jobs), iid, len(Xn), len(Xo)),
                    flush=True,
                )
    np.savez_compressed(
        out_path,
        X=np.concatenate(X),
        pts=np.concatenate(PT),
        item=np.concatenate(IT),
        y=np.concatenate(Y),
        d=np.concatenate(D),
        ids=np.array(ids),
        half=np.array(halves),
        ans=np.array(ans),
        old_hit=np.array(old_hit),
        nnew=np.array(nnew),
        nold=np.array(nold),
        old_s=np.concatenate(OS),
        old_si=np.concatenate(OSI),
        dv=-1 if dv is None else dv,
        small=small,
    )


def with_old(X):
    """X with rank6's own margin appended as one more column (STACK)."""
    import xgboost as xgb

    b = xgb.Booster()
    b.load_model(os.path.join(S2, 'rank6.model.json'))
    return np.hstack([X, b.predict(xgb.DMatrix(X), output_margin=True)[:, None]]).astype(np.float32)


def fit(X, y, grp, obj, rounds=None):
    """An xgboost booster on rows X, labels y; grp (item index, sorted) groups rows by frame for 'pairwise'."""
    import xgboost as xgb

    dm = xgb.DMatrix(X, label=y)
    p = dict(
        max_depth=PARAMS['max_depth'],
        eta=PARAMS['eta'],
        nthread=os.cpu_count(),
        verbosity=0,
        min_child_weight=PARAMS.get('mcw', 1),
        subsample=PARAMS.get('sub', 1.0),
        colsample_bytree=PARAMS.get('col', 1.0),
    )
    if obj == 'pairwise':
        p['objective'] = 'rank:pairwise'
        dm.set_group(np.bincount(grp)[np.unique(grp)])
    else:
        p['objective'] = 'binary:logistic'
    return xgb.train(p, dm, rounds or PARAMS['rounds'])


def margin(bst, X):
    import xgboost as xgb

    return bst.predict(xgb.DMatrix(X), output_margin=True)


def topk_stats(z, s, keep):
    """Per item: rank of the positive blob in s (None if no positive), out of the item's blobs."""
    rank = {}
    it, y = z['item'], z['y']
    order = np.lexsort((-s, it))
    pos = np.zeros(len(s), np.int64)
    starts = np.r_[0, np.flatnonzero(np.diff(it[order])) + 1]
    for a, b in zip(starts, np.r_[starts[1:], len(order)]):
        pos[order[a:b]] = np.arange(b - a)
    for j in np.flatnonzero(y == 1):
        rank[int(it[j])] = int(pos[j])
    return rank


def logo(path, obj, keep=40, oof=None, stack=False):
    """Leave one game out: for each dev game, train on the others; top-KEEP recall old vs new.
    oof: also save every row's out-of-fold margin there (s), for the classifier's own leave-one-game-out."""
    z = dict(np.load(path))
    if stack:
        z['X'] = with_old(z['X'])
    soof = np.zeros(len(z['y']), np.float32)
    games = np.array([game(h) for h in z['half']])
    rows_game = games[z['item']]
    res = {}
    for g in sorted(set(games)):
        tr = rows_game != g
        o = np.argsort(z['item'][tr], kind='stable')
        bst = fit(z['X'][tr][o], z['y'][tr][o], z['item'][tr][o], obj)
        te = ~tr
        s = np.full(len(z['y']), -np.inf, np.float32)
        s[te] = margin(bst, z['X'][te])
        soof[te] = s[te]
        rk = topk_stats(z, s, keep)
        balls = [k for k in np.flatnonzero(games == g) if z['ans'][k] == 'ball']
        for k in balls:
            h = z['half'][k]
            r = res.setdefault(h, dict(n=0, blob=0, new=0, old=0, new1=0, new10=0))
            r['n'] += 1
            r['old'] += int(z['old_hit'][k])
            if k in rk:
                r['blob'] += 1
                r['new'] += int(rk[k] < keep)
                r['new10'] += int(rk[k] < 10)
                r['new1'] += int(rk[k] == 0)
        print(g, {h: res[h] for h in sorted(res) if game(h) == g}, flush=True)
    tot = defaultdict(int)
    for r in res.values():
        for k, v in r.items():
            tot[k] += v
    print('ALL', dict(tot))
    if oof:
        np.savez_compressed(oof, s=soof, obj=obj)
    return res


def train(path, out, obj, stack=False):
    """Fit on every row; scale the margin so its top-KEEP mean and spread match rank6's on the
    same frames; save the booster and <out>.affine.json {a, b}: score = a * margin + b."""
    from .candidate_features import KEEP

    z = dict(np.load(path))
    if stack:
        z['X'] = with_old(z['X'])
    o = np.argsort(z['item'], kind='stable')
    bst = fit(z['X'][o], z['y'][o], z['item'][o], obj)
    s = margin(bst, z['X'])
    top = []
    for k in range(len(z['ids'])):
        m = z['item'] == k
        top.append(np.sort(s[m])[::-1][:KEEP])
    top = np.concatenate(top)
    a = float(np.std(z['old_s']) / max(1e-6, np.std(top)))
    b = float(np.mean(z['old_s']) - a * np.mean(top))
    bst.save_model(out)
    json.dump(
        dict(
            a=a, b=b, obj=obj, stack=stack, params=PARAMS, rows=int(len(s)), pos=int(z['y'].sum())
        ),
        open(out + '.affine.json', 'w'),
    )
    print('saved', out, 'affine a %.4f b %.4f' % (a, b))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    f = sp.add_parser('feats')
    f.add_argument('items')
    f.add_argument('out')
    f.add_argument('--procs', type=int, default=14)
    f.add_argument('--dv', type=int, default=30)
    f.add_argument('--small', type=int, default=6)
    lg = sp.add_parser('logo')
    lg.add_argument('feats')
    lg.add_argument('--obj', default='binary', choices=('binary', 'pairwise'))
    lg.add_argument('--oof')
    t = sp.add_parser('train')
    t.add_argument('feats')
    t.add_argument('out')
    t.add_argument('--obj', default='binary', choices=('binary', 'pairwise'))
    for q in (lg, t):
        q.add_argument('--stack', action='store_true', help="rank6's margin as a 67th column")
        q.add_argument(
            '--params', default='', help='e.g. max_depth=4,eta=0.1,rounds=300,mcw=5,sub=0.8,col=0.8'
        )
    o = ap.parse_args()
    for kv in filter(None, getattr(o, 'params', '').split(',')):
        k, v = kv.split('=')
        PARAMS[k] = float(v) if '.' in v else int(v)
    if o.cmd == 'feats':
        feats(o.items, o.out, o.procs, o.dv, o.small)
    elif o.cmd == 'logo':
        logo(o.feats, o.obj, oof=o.oof, stack=o.stack)
    else:
        train(o.feats, o.out, o.obj, o.stack)


if __name__ == '__main__':
    main()
