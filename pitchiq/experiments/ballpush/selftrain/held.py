"""The kept recipe (ko1: stack net + mined balls + the game's own) for the held-out halves.

Build first, with ST_HELD=1 (reads no held-out answer; shipped scores stand in for the out-of-fold ones):
    ST_HELD=1 python cache.py ; ST_HELD=1 python tcache.py ; ST_HELD=1 python mine.py oof
    ST_HELD=1 python pseudo.py own_<game>      for nb pi wh co wb
Then, WITHOUT ST_HELD:
    python held.py fit <run> [--seed-offset 0]
        one model per held-out game G: human crops + every dev click row + dev pseudo-labels (labels/oof: every
        dev half's confident tracks under its out-of-fold scores) + G's own held-out half's confident tracks
        under the shipped scores (held/labels/own_G).  Scores G's held-out cache -> held/runs/<run>/raw
    python held.py finish <run>
        one label-free affine (frame-top quantiles, new raw vs shipped, all held-out frames) ->
        held/runs/<run>/cls/<piece>cls.npz ; uncached candidates keep the shipped score
Scoring against the held-out answers is replay.py with RP_TAGS: that is the look, logged on the page.
"""

import argparse
import glob
import json
import os
import time

import numpy as np

import st
import train as T1
import train2 as T2
import train3 as T3

HO = st.OUT + '/held'
HGAMES = ['nb', 'pi', 'wh', 'co', 'wb']
HALF = dict(nb='nb2', pi='pi1', wh='wh1', co='co1', wb='wb2')


def hpieces(g):
    return [
        os.path.basename(p)[:-4]
        for p in sorted(glob.glob(os.path.join(st.BT, '%s_q[0-9][0-9][0-9].npz' % HALF[g])))
    ]


def hcache(pt, crops=True):
    z = np.load(HO + '/cache/%s.npz' % pt)
    d = dict(f=z['f'].astype(np.int64), j=z['j'].astype(np.int64), ctx=z['ctx'])
    if crops:
        d['crop'] = z['crop']
    return d


def hrows(pt, rows=None):
    ca = hcache(pt)
    tc = np.load(HO + '/tcache/%s.npy' % pt, mmap_mode='r')
    if rows is None:
        return T2.with_stack(ca['crop'], np.asarray(tc)), ca['ctx']
    return T2.with_stack(ca['crop'][rows], tc[rows]), ca['ctx'][rows]


def own_rows(g):
    z = np.load(HO + '/labels/own_%s.npz' % g)
    C, K, Y = [], [], []
    for pi, p in enumerate(z['names'].tolist()):
        m = np.flatnonzero(z['pt'] == pi)
        if len(m):
            c, k = hrows(p, z['row'][m])
            C.append(c), K.append(k), Y.append(z['y'][m])
    if not C:
        return None
    return np.concatenate(C), np.concatenate(K), np.concatenate(Y)


def fit(a):
    import torch

    from pitchiq.balltrack import train_ball_classifier as T

    out = HO + '/runs/%s' % a.run
    os.makedirs(out + '/raw', exist_ok=True)
    json.dump(vars(a), open(out + '/args.json', 'w'))
    dev = T.device()
    data, names, _ = T1.base_data()
    hc, hk, hy, hw = T.gather(data, [t for t in T1.HUMAN if len(data[t]['lab'][0])], names)
    hc = T2.with_stack(hc[:, 4:44, 4:44], None)
    cc_, ck_, cy = T2.click_rows(None, 1)
    pc, pk, py, _ = T2.pseudo_rows(a.dev_pseudo, None, 1)
    print(
        'shared rows: human %d, dev clicks %d (ball %d), dev pseudo %d (ball %d)'
        % (len(hy), len(cy), int((cy == 0).sum()), len(py), int((py == 0).sum())),
        flush=True,
    )
    for g in a.games.split(',') if a.games else HGAMES:
        i = HGAMES.index(g)
        t0 = time.time()
        crops, ctx, y = [hc, cc_, pc], [hk, ck_, pk], [hy, cy, py]
        w = [hw, np.ones(len(cy), np.float32), np.full(len(py), a.w, np.float32)]
        own = own_rows(g)
        no = 0
        if own is not None:
            (
                crops.append(own[0]),
                ctx.append(own[1]),
                y.append(own[2]),
                w.append(np.full(len(own[2]), a.w, np.float32)),
            )
            no = len(own[2])
        crops, ctx, y, w = (
            np.concatenate(crops),
            np.concatenate(ctx),
            np.concatenate(y),
            np.concatenate(w),
        )
        mu, sd = T.ctx_stats(ctx)
        if (
            a.arch == 'resnet18'
        ):  # train3's pretrained ResNet-18 (kres recipe); no rank loss, no frame groups
            net = T3.fit_net(
                crops,
                T.ctx_norm(ctx, mu, sd),
                y,
                w,
                None,
                a,
                dev,
                i + a.seed_offset,
                log='held %s' % g,
            )
        else:
            net = T2.fit_net(
                crops,
                T.ctx_norm(ctx, mu, sd),
                y,
                w,
                a.epochs,
                dev,
                i + a.seed_offset,
                0,
                log='held %s' % g,
            )
        sc = T.Scorer(net, mu, sd, names, dev)
        torch.save(
            dict(
                state=net.state_dict(),
                mu=mu.tolist(),
                sd=sd.tolist(),
                ctx_names=list(names),
                epochs=a.epochs,
                stack=1,
                arch=a.arch,
            ),
            out + '/model_%s.pt' % g,
        )
        for pt in hpieces(g):
            c, k = hrows(pt)
            np.save(out + '/raw/%s.npy' % pt, sc.score(c, k, batch=1024))
        print(
            'held %s: rows %d, own pseudo %d, %.0f s' % (g, len(y), no, time.time() - t0),
            flush=True,
        )
        del crops, ctx, net, sc
    print('HELD FIT DONE', a.run, flush=True)


def shipped(pt):
    return st.load_oof(pt, st.BT)


def finish(a):
    out = HO + '/runs/%s' % a.run
    os.makedirs(out + '/cls', exist_ok=True)
    pts = [pt for g in HGAMES for pt in hpieces(g)]
    tn, to, cor = [], [], []
    for pt in pts:
        ca = hcache(pt, crops=False)
        raw = np.load(out + '/raw/%s.npy' % pt)
        f, j, p = shipped(pt)
        tn.append(T1.frame_top(ca['f'], raw)), to.append(T1.frame_top(f, p))
        old = p[np.searchsorted(f * 64 + j, ca['f'] * 64 + ca['j'])]
        cor.append(np.corrcoef(raw, old)[0, 1])
    aa, bb = np.polyfit(
        np.percentile(np.concatenate(tn), T1.Q), np.percentile(np.concatenate(to), T1.Q), 1
    )
    print(
        'cal: a %.3f b %.3f ; raw vs shipped score correlation on the cache: min %.2f median %.2f'
        % (aa, bb, min(cor), float(np.median(cor)))
    )
    json.dump([float(aa), float(bb)], open(out + '/cal.json', 'w'))
    for pt in pts:
        ca = hcache(pt, crops=False)
        raw = np.load(out + '/raw/%s.npy' % pt)
        f, j, p = shipped(pt)
        p = p.copy()
        p[np.searchsorted(f * 64 + j, ca['f'] * 64 + ca['j'])] = aa * raw + bb
        np.savez(
            out + '/cls/%scls.npz' % pt,
            f=f.astype(np.int32),
            j=j.astype(np.int16),
            p=p.astype(np.float32),
        )
    print('scores:', out + '/cls')


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    f = sp.add_parser('fit')
    f.add_argument('run')
    f.add_argument('--games', default='')
    f.add_argument('--w', type=float, default=0.5)
    f.add_argument('--epochs', type=int, default=12)
    f.add_argument('--seed-offset', type=int, default=0)
    f.add_argument(
        '--dev-pseudo', default='oof'
    )  # oof (round 1) | oof_k2 (round 2: picks of kres12 tracks)
    f.add_argument('--arch', default='ballnet')  # ballnet (hk1) | resnet18 (kres)
    f.add_argument('--conv-lr', type=float, default=1.0)
    f.set_defaults(wide=0, rank=0.0, kmax=24, gbatch=16)
    q = sp.add_parser('finish')
    q.add_argument('run')
    a = ap.parse_args()
    (fit if a.cmd == 'fit' else finish)(a)


if __name__ == '__main__':
    main()
