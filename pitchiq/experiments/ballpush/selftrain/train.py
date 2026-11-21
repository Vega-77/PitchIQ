"""Leave-one-game-out classifier folds on labels + pseudo-labels, scored on the left-out game's cache.

For game G the fold trains on: the human crops (tr00..tr16), the click crops (ckL) of the other games,
and (unless --pseudo none) pseudo-labels of the other games mined under fold_G's scores
(labels/no_G.npz; mine.py fold G + pseudo.py no_G).  Nothing of G: no clicks, no pseudo-labels.
--pseudo own adds pseudo-labels mined from G ITSELF under fold_G's scores (no label of G is used, but it
breaks the brief's rule: reported separately as "transductive").

    python train.py fit <run> [--games a,b] [--pseudo strict|none|own|strict+own] [--w 0.5] [--epochs 12]
                             [--maxpos N] [--maxneg N] [--seed-offset 0]
        -> runs/<run>/fold_<G>.pt, runs/<run>/raw/<piece>.npy (raw log-odds on G's cached candidates)
    python train.py finish <run> [--cal global|game|none]
        -> runs/<run>/cls/<piece>cls.npz (calibrated; uncached candidates keep the oof score), prints the
           ball-ranked-first table against the baseline oof scores
"""
import argparse
import json
import os
import time

import numpy as np

import st

HUMAN = ['tr%02d' % i for i in range(17)]
Q = np.arange(10, 91, 5)


def base_data():
    """The retrain's own data: {tag: crops dict with 'lab'}, ctx names, click tags."""
    from pitchiq.balltrack import train_ball_classifier as T

    T.S2 = st.BT
    recs = json.load(open(st.FIT + '/ckLlabels.json'))
    for line in open(st.BT + '/crop_labels/crops.jsonl'):
        d = json.loads(line)[1]
        if d['label'] != 'unsure':
            recs.append(dict(tag=d['tag'], f=int(d['f']), j=int(d['j']), label=d['label']))
    os.makedirs(st.OUT + '/tmp', exist_ok=True)
    lp = st.OUT + '/tmp/labels_all_%d.json' % os.getpid()
    json.dump(recs, open(lp, 'w'))
    labels = T.load_labels(lp)
    os.remove(lp)
    ck = sorted({r['tag'] for r in recs if r['tag'].startswith('ckL')})
    data, names = {}, None
    for t in HUMAN + ck:
        d = T.load_crops(t)
        names = names or d['ctx_names']
        d['lab'] = T.join(t, d, labels)
        data[t] = d
    return data, names, ck


def pseudo_rows(name, rng, maxpos, maxneg):
    """Crops, ctx, y, hard of a labels file, read from the cache (subsampled; hard rows first)."""
    z = np.load(st.OUT + '/labels/%s.npz' % name)
    y, hard, pt, row = z['y'], z['hard'], z['pt'], z['row']
    keep = np.ones(len(y), bool)
    for cls, cap in ((0, maxpos), (4, maxneg)):
        idx = np.flatnonzero(y == cls)
        if cap and len(idx) > cap:
            h = idx[hard[idx]]
            e = idx[~hard[idx]]
            if len(h) >= cap:
                sel = rng.choice(h, cap, replace=False)
            else:
                sel = np.r_[h, rng.choice(e, cap - len(h), replace=False)]
            keep[idx] = False
            keep[sel] = True
    C, K, Y, H = [], [], [], []
    for pi, p in enumerate(z['names'].tolist()):
        m = np.flatnonzero(keep & (pt == pi))
        if not len(m):
            continue
        ca = st.load_cache(p)
        C.append(ca['crop'][row[m]])
        K.append(ca['ctx'][row[m]])
        Y.append(y[m])
        H.append(hard[m])
    return np.concatenate(C), np.concatenate(K), np.concatenate(Y), np.concatenate(H)


def fit(a):
    import torch

    from pitchiq.balltrack import train_ball_classifier as T

    out = st.OUT + '/runs/%s' % a.run
    os.makedirs(out + '/raw', exist_ok=True)
    json.dump(vars(a), open(out + '/args.json', 'w'))
    dev = T.device()
    data, names, ck = base_data()
    assert list(names) == list(__import__('pitchiq.balltrack.candidate_crops', fromlist=['x']).CTX_NAMES)
    for g in (a.games.split(',') if a.games else st.GAMES):
        i = st.GAMES.index(g)
        t0 = time.time()
        rng = np.random.default_rng(100 + i)
        tr = [t for t in HUMAN + ck if not (t.startswith('ckL') and st.game(t[3:]) == g) and len(data[t]['lab'][0])]
        crops, ctx, y, w = T.gather(data, tr, names)
        crops = crops[:, 4:44, 4:44]
        nb = (len(y), int((y == 0).sum()))
        parts = [p for p in a.pseudo.split('+') if p != 'none']
        np_, nn_ = 0, 0
        for p in parts:
            name = dict(strict='no_%s', own='own_%s', r2='r2no_%s')[p] % g
            c2, k2, y2, h2 = pseudo_rows(name, rng, a.maxpos, a.maxneg)
            w2 = np.full(len(y2), a.w, np.float32)
            if a.hardw:
                w2[h2 & (y2 == 0)] *= a.hardw
            crops = np.concatenate([crops, c2])
            ctx = np.concatenate([ctx, k2])
            y = np.concatenate([y, y2])
            w = np.concatenate([w, w2])
            np_ += int((y2 == 0).sum())
            nn_ += int((y2 != 0).sum())
        mu, sd = T.ctx_stats(ctx)
        net = T.fit(crops, T.ctx_norm(ctx, mu, sd), y, w, a.epochs, dev, i + a.seed_offset, log='fold %s' % g)
        sc = T.Scorer(net, mu, sd, names, dev)
        torch.save(dict(state=net.state_dict(), mu=mu.tolist(), sd=sd.tolist(), ctx_names=list(names), tags=tr, epochs=a.epochs,
                        pseudo=a.pseudo), out + '/fold_%s.pt' % g)
        for pt in st.pieces([t for t in st.DEV if st.game(t) == g]):
            ca = st.load_cache(pt)
            np.save(out + '/raw/%s.npy' % pt, sc.score(ca['crop'], ca['ctx']))
        print('fold %s: base rows %d (ball %d) + pseudo ball %d other %d, %d epochs, %.0f s' % (g, nb[0], nb[1], np_, nn_, a.epochs,
                                                                                               time.time() - t0), flush=True)
        del crops, ctx, net, sc
    print('FIT DONE', a.run, flush=True)


def frame_top(f, p):
    """Best score per frame (f sorted)."""
    u, s = np.unique(f, return_index=True)
    return np.maximum.reduceat(p, s)


def click_index():
    """[(half, piece, abs frame, answer, n candidates, index of the ball blob or -1)] for every dev click, cached."""
    import pickle

    p = st.OUT + '/tmp/clickidx.pkl'
    if os.path.exists(p):
        return pickle.load(open(p, 'rb'))
    st.common.point_at_data()
    from pitchiq.balltrack import ball_tracker

    ck = st.clicks()
    out = []
    for tag in st.DEV:
        for pt in st.pieces([tag]):
            F = {fr['abs']: fr for fr in ball_tracker.load_frames(pt)}
            for iid, f, a in ck[tag]:
                fr = F.get(f)
                if fr is None:
                    continue
                jb = -1
                if a['v'] == 'ball':
                    d = np.hypot(fr['x'] - a['x'], fr['y'] - a['y'])
                    jb = int(np.argmin(d)) if d.min() <= 8 else -1
                out.append((tag, pt, f, a['v'], len(fr['s']), jb))
    os.makedirs(st.OUT + '/tmp', exist_ok=True)
    pickle.dump(out, open(p, 'wb'))
    return out


def rank_table(dirs):
    """Per half and score folder: clicked balls with a blob (candidate within 8 px), how many of them are the
    frame's top candidate, top and > 2; 'out' frames whose top is > 2.  -> {name: {half: counts}}"""
    res = {n: {t: dict(ball=0, blob=0, first=0, first2=0, p2=0, out=0, out2=0) for t in st.DEV} for n in dirs}
    S = {}
    for tag, pt, f, v, n, jb in click_index():
        for nm, d in dirs.items():
            if (nm, pt) not in S:
                S[(nm, pt)] = None
                if os.path.exists(os.path.join(d, '%scls.npz' % pt)):
                    f0, j0, p0 = st.load_oof(pt, d)
                    S[(nm, pt)] = (f0 * 64 + j0, p0)
            if S[(nm, pt)] is None:
                continue
            key, p0 = S[(nm, pt)]
            p = p0[np.searchsorted(key, f * 64 + np.arange(n))]
            c = res[nm][tag]
            if v == 'out':
                c['out'] += 1
                c['out2'] += int(p.max() > 2)
                continue
            c['ball'] += 1
            if jb < 0:
                continue
            c['blob'] += 1
            first = not (p > p[jb]).sum()
            c['first'] += first
            c['p2'] += int(p[jb] > 2)
            c['first2'] += int(first and p[jb] > 2)
    return res


def print_rank(res):
    for n, r in res.items():
        tot = {}
        for tag, c in r.items():
            for k, v in c.items():
                tot[k] = tot.get(k, 0) + v
        print('%-14s balls with a blob %d: ranked first %d, first and p > 2 %d, p > 2 %d | out frames %d, top > 2 on %d' % (
            n, tot['blob'], tot['first'], tot['first2'], tot['p2'], tot['out'], tot['out2']))
    names = list(res)
    for tag in st.DEV:
        print('  %-8s' % tag, '   '.join('%s first %d/%d f&p>2 %d out>2 %d' % (
            n, res[n][tag]['first'], res[n][tag]['blob'], res[n][tag]['first2'], res[n][tag]['out2']) for n in names))


def finish(a):
    out = st.OUT + '/runs/%s' % a.run
    os.makedirs(out + '/cls', exist_ok=True)
    pts = [pt for pt in st.pieces() if os.path.exists(out + '/raw/%s.npy' % pt)]
    tops = {}
    for pt in pts:
        ca = st.load_cache(pt, crops=False)
        raw = np.load(out + '/raw/%s.npy' % pt)
        f, j, p = st.load_oof(pt)
        tops[pt] = (frame_top(ca['f'], raw), frame_top(f, p))
    cal = {}
    if a.cal == 'global':
        tn = np.concatenate([tops[pt][0] for pt in pts])
        to = np.concatenate([tops[pt][1] for pt in pts])
        ab = np.polyfit(np.percentile(tn, Q), np.percentile(to, Q), 1)
        cal = {st.game(pt): ab for pt in pts}
    elif a.cal == 'game':
        for g in sorted({st.game(pt) for pt in pts}):
            tn = np.concatenate([tops[pt][0] for pt in pts if st.game(pt) == g])
            to = np.concatenate([tops[pt][1] for pt in pts if st.game(pt) == g])
            cal[g] = np.polyfit(np.percentile(tn, Q), np.percentile(to, Q), 1)
    else:
        cal = {st.game(pt): (1.0, 0.0) for pt in pts}
    print('cal (%s):' % a.cal, {g: (round(float(v[0]), 3), round(float(v[1]), 3)) for g, v in cal.items()})
    json.dump({g: [float(v[0]), float(v[1])] for g, v in cal.items()}, open(out + '/cal_%s.json' % a.cal, 'w'))
    for pt in pts:
        ca = st.load_cache(pt, crops=False)
        raw = np.load(out + '/raw/%s.npy' % pt)
        f, j, p = st.load_oof(pt)
        p = p.copy()
        aa, bb = cal[st.game(pt)]
        p[np.searchsorted(f * 64 + j, ca['f'] * 64 + ca['j'])] = aa * raw + bb
        np.savez(out + '/cls/%scls.npz' % pt, f=f.astype(np.int32), j=j.astype(np.int16), p=p.astype(np.float32))
    res = rank_table(dict(baseline=st.OOF, new=out + '/cls'))
    print_rank(res)
    json.dump(res, open(out + '/rank.json', 'w'))
    print('scores:', out + '/cls')


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    f = sp.add_parser('fit')
    f.add_argument('run')
    f.add_argument('--games', default='')
    f.add_argument('--pseudo', default='strict')
    f.add_argument('--w', type=float, default=0.5)
    f.add_argument('--hardw', type=float, default=0.0)
    f.add_argument('--epochs', type=int, default=12)
    f.add_argument('--maxpos', type=int, default=0)
    f.add_argument('--maxneg', type=int, default=0)
    f.add_argument('--seed-offset', type=int, default=0)
    g = sp.add_parser('finish')
    g.add_argument('run')
    g.add_argument('--cal', default='global')
    a = ap.parse_args()
    {'fit': fit, 'finish': finish}[a.cmd](a)


if __name__ == '__main__':
    main()
