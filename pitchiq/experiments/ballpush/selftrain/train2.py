"""The five-frame-stack rework: the same leave-one-game-out folds as train.py, on 7-channel crops.

Channels: the candidate's BGR crop + grey crops of the same ground spot at t-2, t-1, t+1, t+2 (tcache.py).
The click crops of the retrain (ckL) have no neighbours, so here the click labels are rebuilt on the cache
(every candidate of a clicked frame is cached): ball = the candidate nearest a ball click (<= 8 px), other =
every candidate > 16 px from it and every candidate of an "out" frame.  The 1,520 human crops (tr00..16) get
their own grey crop repeated as neighbours.  --stack 0 trains the single-frame net on exactly the same rows
(the control for the stack).

    python train2.py fit <run> [--stack 1] [--pseudo strict|none|own|strict+own] [--w 0.5] [--epochs 12]
                               [--games a,b] [--wide 1]
       -> runs/<run>/fold_<G>.pt, raw/<piece>.npy ; then  train.py finish <run>
"""
import argparse
import json
import os
import time

import numpy as np

import st
import train as T1


def grey(c):
    return (0.114 * c[..., 0] + 0.587 * c[..., 1] + 0.299 * c[..., 2] + 0.5).astype(np.uint8)


def with_stack(crop, tc):
    """N x 40 x 40 x 3 + N x 4 x 40 x 40 (or None: repeat own grey) -> N x 40 x 40 x 7."""
    if tc is None:
        g = grey(crop)
        tc = np.repeat(g[:, None], 4, 1)
    return np.concatenate([crop, tc.transpose(0, 2, 3, 1)], 3)


def piece_rows(pt, rows, stack):
    ca = st.load_cache(pt)
    c = ca['crop'][rows]
    if stack:
        c = with_stack(c, np.load(st.OUT + '/tcache/%s.npy' % pt, mmap_mode='r')[rows])
    return c, ca['ctx'][rows]


def click_rows(g, stack):
    """Click labels of every game but g, on the cache."""
    st.common.point_at_data()
    from pitchiq.balltrack import ball_tracker

    ck = st.clicks()
    C, K, Y = [], [], []
    for tag in st.DEV:
        if st.game(tag) == g:
            continue
        for pt in st.pieces([tag]):
            ca = st.load_cache(pt, crops=False)
            key = ca['f'] * 64 + ca['j']
            F = None
            rows, ys = [], []
            for iid, f, a in ck[tag]:
                a0, a1 = np.searchsorted(key, [f * 64, f * 64 + 64])
                if a1 <= a0:
                    continue
                if F is None:
                    F = {fr['abs']: fr for fr in ball_tracker.load_frames(pt)}
                fr = F[f]
                jj = ca['j'][a0:a1]
                if a['v'] == 'ball':
                    d = np.hypot(fr['x'][jj] - a['x'], fr['y'][jj] - a['y'])
                    jb = int(np.argmin(d))
                    for q in range(len(jj)):
                        if q == jb and d[q] <= 8:
                            rows.append(a0 + q), ys.append(0)
                        elif d[q] > 16:
                            rows.append(a0 + q), ys.append(4)
                else:
                    for q in range(len(jj)):
                        rows.append(a0 + q), ys.append(4)
            if rows:
                c, k = piece_rows(pt, np.array(rows), stack)
                C.append(c), K.append(k), Y.append(np.array(ys))
    return np.concatenate(C), np.concatenate(K), np.concatenate(Y)


def pseudo_rows(name, rng, stack):
    z = np.load(st.OUT + '/labels/%s.npz' % name)
    C, K, Y, H = [], [], [], []
    for pi, p in enumerate(z['names'].tolist()):
        m = np.flatnonzero(z['pt'] == pi)
        if not len(m):
            continue
        c, k = piece_rows(p, z['row'][m], stack)
        C.append(c), K.append(k), Y.append(z['y'][m]), H.append(z['hard'][m])
    return np.concatenate(C), np.concatenate(K), np.concatenate(Y), np.concatenate(H)


def prop_rows(g, stack):
    """Click-propagation positives (prop.py) whose click is in another game than g."""
    P = st.OUT + '/prop'
    per = {}
    for r in json.load(open(P + '/chains.json')):
        if r['game'] != g:
            per.setdefault(r['piece'], set()).add(r['f'] * 64 + r['j'])
    C, K = [], []
    for pt, keys in per.items():
        z = np.load(P + '/cache/%s.npz' % pt)
        key = z['f'].astype(np.int64) * 64 + z['j'].astype(np.int64)
        rows = np.flatnonzero(np.isin(key, np.array(sorted(keys))))
        assert len(rows) == len(keys), pt
        c = z['crop'][rows]
        if stack:
            c = with_stack(c, np.load(P + '/tcache/%s.npy' % pt)[rows])
        C.append(c), K.append(z['ctx'][rows])
    return np.concatenate(C), np.concatenate(K)


def synth_rows(crops, ctx, y, n, rng, keep=None):
    """New-brief 2.3: n synthetic positives, a mined ball's centre disc pasted onto a negative crop (legs, cleats,
    paint) with a soft edge and a random dimming.  All channels (the neighbour-frame greys too); ctx is the ball's,
    or with keep (column indices) the background's with only those columns taken from the ball."""
    pos, neg = np.flatnonzero(y == 0), np.flatnonzero(y != 0)
    bi, gi = rng.choice(pos, n), rng.choice(neg, n)
    S = crops.shape[1]
    yy, xx = np.mgrid[:S, :S]
    d = np.hypot(yy - (S - 1) / 2, xx - (S - 1) / 2)
    out = np.empty((n,) + crops.shape[1:], np.uint8)
    for k in range(n):
        al = np.clip((rng.uniform(4, 8) - d) / 1.5 + 0.5, 0, 1)[..., None]
        out[k] = np.clip(al * crops[bi[k]].astype(np.float32) * rng.uniform(0.7, 1.0)
                         + (1 - al) * crops[gi[k]].astype(np.float32) + 0.5, 0, 255)
    if keep is None:
        return out, ctx[bi], np.zeros(n, np.int64)
    k2 = ctx[gi].copy()
    k2[:, keep] = ctx[bi][:, keep]
    return out, k2, np.zeros(n, np.int64)


def fit_net(crops, ctx, y, w, epochs, dev, seed, wide, log='', bs=256, lr=2e-3, init=None, conv_lr=1.0):
    """train_ball_classifier.fit with the first conv sized to the crops' channels (and optional wider net)."""
    import torch
    import torch.nn.functional as Fn

    from pitchiq.balltrack import train_ball_classifier as T

    T.seed_all(seed)
    rng = np.random.default_rng(seed)
    net = T.BallNet(ctx.shape[1], w=(32, 64, 96, 128) if wide else (16, 32, 48, 64))
    ch = crops.shape[-1]
    if ch != 3:
        net.conv[0][0] = torch.nn.Conv2d(ch, net.conv[0][0].out_channels, 3, padding=1, bias=False)
    if init:  # new-brief 2.1: the conv trunk starts from a pretrained checkpoint (sn_pre.py, now in git history)
        net.conv.load_state_dict(torch.load(init, weights_only=True)['conv'])
    net = net.to(dev)
    rest = [q for n, q in net.named_parameters() if not n.startswith('conv.')]
    opt = torch.optim.AdamW([dict(params=net.conv.parameters(), lr=lr * conv_lr), dict(params=rest, lr=lr)], weight_decay=1e-4)
    steps = max(1, epochs * ((len(y) + bs - 1) // bs))
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[lr * conv_lr, lr], total_steps=steps, pct_start=0.15)
    sw = w * T.class_weights(y)[y]
    ctx_t = torch.as_tensor(ctx, device=dev)
    y_t = torch.as_tensor(y, device=dev)
    sw_t = torch.as_tensor(sw, device=dev)
    for ep in range(epochs):
        net.train()
        perm = rng.permutation(len(y))
        tot, t0 = 0.0, time.time()
        for a in range(0, len(perm), bs):
            b = perm[a:a + bs]
            if len(b) < 2:
                continue
            x = T.augment(crops[b], dev, rng)
            bt = torch.as_tensor(b, device=dev)
            loss = (Fn.cross_entropy(net(x, ctx_t[bt]), y_t[bt], reduction='none') * sw_t[bt]).sum() / sw_t[bt].sum()
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss.detach()) * len(b)
        if ep == 0 or ep + 1 == epochs:
            print('%s  epoch %2d/%d  loss %.4f  %.1fs' % (log, ep + 1, epochs, tot / len(y), time.time() - t0), flush=True)
    return net.eval()


def fit(a):
    import torch

    from pitchiq.balltrack import train_ball_classifier as T

    out = st.OUT + '/runs/%s' % a.run
    os.makedirs(out + '/raw', exist_ok=True)
    json.dump(vars(a), open(out + '/args.json', 'w'))
    dev = T.device()
    data, names, _ = T1.base_data()
    hc, hk, hy, hw = T.gather(data, [t for t in T1.HUMAN if len(data[t]['lab'][0])], names)
    hc = hc[:, 4:44, 4:44]
    if a.stack:
        hc = with_stack(hc, None)
    for g in (a.games.split(',') if a.games else st.GAMES):
        i = st.GAMES.index(g)
        t0 = time.time()
        rng = np.random.default_rng(100 + i + a.seed_offset)
        cc_, ck_, cy = click_rows(g, a.stack)
        crops, ctx, y = [hc, cc_], [hk, ck_], [hy, cy]
        w = [hw, np.ones(len(cy), np.float32)]
        for p in [p for p in a.pseudo.split('+') if p != 'none']:
            if p == 'prop':
                c2, k2 = prop_rows(g, a.stack)
                crops.append(c2), ctx.append(k2), y.append(np.zeros(len(c2), np.int64)), w.append(np.full(len(c2), a.pw, np.float32))
                continue
            c2, k2, y2, h2 = pseudo_rows('no_%s' % g if p == 'strict' else 'br_%s' % g if p == 'br' else 'own_%s%s' % (g, a.lab), rng, a.stack)
            crops.append(c2), ctx.append(k2), y.append(y2), w.append(np.full(len(y2), a.w, np.float32))
        if a.synth:
            # sources: the first pseudo set (mined balls of the other games and their negatives)
            keep = [list(names).index(q) for q in a.synth_keep.split(',')] if a.synth_keep else None
            c3, k3, y3 = synth_rows(crops[2], ctx[2], y[2], a.synth, rng, keep)
            crops.append(c3), ctx.append(k3), y.append(y3), w.append(np.full(a.synth, a.sw, np.float32))
        crops, ctx, y, w = np.concatenate(crops), np.concatenate(ctx), np.concatenate(y), np.concatenate(w)
        if a.cleat_w != 1.0:
            # new-brief 2.2: not-ball rows in the bottom quarter of a person box (cleats, socks) weigh more
            ki, kv = list(names).index('inside'), list(names).index('v')
            cl = (y != 0) & (ctx[:, ki] > 0.5) & (ctx[:, kv] >= 0.75)
            w = np.where(cl, w * a.cleat_w, w).astype(np.float32)
            print('fold %s: cleat-zone negatives %d of %d not-ball rows, weight x%g' % (g, int(cl.sum()), int((y != 0).sum()), a.cleat_w), flush=True)
        mu, sd = T.ctx_stats(ctx)
        net = fit_net(crops, T.ctx_norm(ctx, mu, sd), y, w, a.epochs, dev, i + a.seed_offset, a.wide, log='fold %s' % g,
                      init=a.init or None, conv_lr=a.conv_lr)
        sc = T.Scorer(net, mu, sd, names, dev)
        torch.save(dict(state=net.state_dict(), mu=mu.tolist(), sd=sd.tolist(), ctx_names=list(names), epochs=a.epochs,
                        pseudo=a.pseudo, stack=a.stack, wide=a.wide), out + '/fold_%s.pt' % g)
        for pt in st.pieces([t for t in st.DEV if st.game(t) == g]):
            ca = st.load_cache(pt)
            c = ca['crop']
            if a.stack:
                c = with_stack(c, np.load(st.OUT + '/tcache/%s.npy' % pt))
            np.save(out + '/raw/%s.npy' % pt, sc.score(c, ca['ctx'], batch=1024))
        print('fold %s: human %d + click rows %d (ball %d) + pseudo %d, stack %d, %.0f s' % (
            g, len(hy), len(cy), int((cy == 0).sum()), len(y) - len(hy) - len(cy), a.stack, time.time() - t0), flush=True)
        del crops, ctx, net, sc
    print('FIT DONE', a.run, flush=True)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    f = sp.add_parser('fit')
    f.add_argument('run')
    f.add_argument('--games', default='')
    f.add_argument('--pseudo', default='strict')
    f.add_argument('--synth', type=int, default=0)  # synthetic pasted-ball positives per fold
    f.add_argument('--sw', type=float, default=0.5)  # their loss weight
    f.add_argument('--synth-keep', default='')  # e.g. s,rank: ctx is the background's except these columns
    f.add_argument('--cleat-w', type=float, default=1.0)  # loss weight factor of cleat-zone negatives
    f.add_argument('--init', default='')  # pretrained checkpoint for the conv trunk (sn_pre.py, in git history)
    f.add_argument('--conv-lr', type=float, default=1.0)  # the trunk's learning rate as a share of the head's
    f.add_argument('--lab', default='')  # label-set suffix (pseudo.py ST_LAB), e.g. _l30
    f.add_argument('--stack', type=int, default=1)
    f.add_argument('--wide', type=int, default=0)
    f.add_argument('--w', type=float, default=0.5)
    f.add_argument('--epochs', type=int, default=12)
    f.add_argument('--seed-offset', type=int, default=0)
    f.add_argument('--pw', type=float, default=1.0)
    fit(ap.parse_args())


if __name__ == '__main__':
    main()
