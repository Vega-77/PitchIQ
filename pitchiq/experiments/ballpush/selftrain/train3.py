"""Classifier angles 1 and 2 (2026-11-13, after the user picked the classifier as the place to find balls).

Same leave-one-game-out folds, rows and labels as train2.py (ko1 = --stack 1 --pseudo strict+own); two new knobs:

  --rank L     angle 1: add L x a per-frame listwise loss.  Rows that share a frame (a clicked frame, or a mined
               frame with one pseudo ball and its pseudo negatives) form a group; the loss is the cross-entropy of
               the softmax over the group's ball log-odds at the ball.  It punishes "a cleat outscored the ball"
               directly.  The pointwise loss stays (the tracker needs the margin, not only the rank: kp2).
  --arch resnet18   angle 2: an ImageNet-pretrained ResNet-18 trunk (torchvision layout, weights downloaded to
               ball-push/models/resnet18_imagenet, loaded with weights_only=True) on the 32 px window upsampled
               to 96 px; the 4 neighbour-frame grey channels enter conv1 through zero-initialised filters.

    python train3.py fit <run> [train2 args] [--rank 1.0] [--arch resnet18] ;  then  train.py finish <run>
"""
import argparse
import json
import os
import time

import numpy as np

import st
import train as T1
import train2 as T2

R18 = 'C:/Users/alexv/Desktop/Repos/PitchIQ-data/ball-push/models/resnet18_imagenet/resnet18-f37072fd.pth'


# ------------------------------------------------------------------ rows with frame groups

def click_rows(g, stack):
    """train2.click_rows plus a frame key per row (-1 on out frames: no ball to rank)."""
    st.common.point_at_data()
    from pitchiq.balltrack import ball_tracker

    ck = st.clicks()
    C, K, Y, G = [], [], [], []
    gid = 0
    for tag in st.DEV:
        if st.game(tag) == g:
            continue
        for pt in st.pieces([tag]):
            ca = st.load_cache(pt, crops=False)
            key = ca['f'] * 64 + ca['j']
            F = None
            rows, ys, gs = [], [], []
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
                            rows.append(a0 + q), ys.append(0), gs.append(gid)
                        elif d[q] > 16:
                            rows.append(a0 + q), ys.append(4), gs.append(gid)
                    gid += 1
                else:
                    for q in range(len(jj)):
                        rows.append(a0 + q), ys.append(4), gs.append(-1)
            if rows:
                c, k = T2.piece_rows(pt, np.array(rows), stack)
                C.append(c), K.append(k), Y.append(np.array(ys)), G.append(np.array(gs))
    return np.concatenate(C), np.concatenate(K), np.concatenate(Y), np.concatenate(G)


def pseudo_rows(name, stack):
    """train2.pseudo_rows plus a frame key per row (piece index * 2^24 + frame)."""
    z = np.load(st.OUT + '/labels/%s.npz' % name)
    C, K, Y, G = [], [], [], []
    for pi, p in enumerate(z['names'].tolist()):
        m = np.flatnonzero(z['pt'] == pi)
        if not len(m):
            continue
        rows = z['row'][m]
        c, k = T2.piece_rows(p, rows, stack)
        f = st.load_cache(p, crops=False)['f'][rows].astype(np.int64)
        C.append(c), K.append(k), Y.append(z['y'][m]), G.append(pi * (1 << 24) + f)
    return np.concatenate(C), np.concatenate(K), np.concatenate(Y), np.concatenate(G)


def groups(y, gk, kmax, rng):
    """-> list of (ball row, negative rows) for frame groups with exactly one ball and >= 1 negative."""
    m = gk >= 0
    idx = np.flatnonzero(m)
    order = idx[np.argsort(gk[idx], kind='stable')]
    ks = gk[order]
    cut = np.flatnonzero(np.diff(ks)) + 1
    out = []
    for blk in np.split(order, cut):
        b = blk[y[blk] == 0]
        n = blk[y[blk] != 0]
        if len(b) == 1 and len(n):
            if len(n) > kmax - 1:
                n = rng.choice(n, kmax - 1, replace=False)
            out.append((int(b[0]), n))
    return out


def feet_zone(ctx, names, near=0.1, mode='feet'):
    """mode feet: candidates inside a person box in its lower half, or outside but within `near` box heights of one.
    mode player: inside any person box, or within `near` of one (on ko1 the upper-body balls are the worst group:
    6 of 70 first and confident).  The zone model (--zone) trains on these rows only; feetmerge.py routes them."""
    names = list(names)
    ki, kv, kd = names.index('inside'), names.index('v'), names.index('dist_rel')
    ins = ctx[:, ki] > 0.5
    if mode == 'player':
        return ins | (ctx[:, kd] <= near)
    return (ins & (ctx[:, kv] >= 0.5)) | (~ins & (ctx[:, kd] <= near))


# ------------------------------------------------------------------ pretrained trunk

def make_resnet18(k_ctx, ch, n_cls):
    import torch
    import torch.nn as nn
    import torchvision

    class PreNet(nn.Module):
        def __init__(self):
            super().__init__()
            r = torchvision.models.resnet18()
            r.load_state_dict(torch.load(R18, weights_only=True))
            w = r.conv1.weight.data  # 64 x 3 (RGB) x 7 x 7
            c1 = nn.Conv2d(ch, 64, 7, 2, 3, bias=False)
            c1.weight.data.zero_()
            c1.weight.data[:, :3] = w.flip(1)  # our crops are BGR
            r.conv1 = c1
            self.conv = nn.Sequential(r.conv1, r.bn1, r.relu, r.maxpool, r.layer1, r.layer2, r.layer3, r.layer4)
            self.ctx = nn.Sequential(nn.Linear(k_ctx, 32), nn.ReLU(inplace=True))
            self.head = nn.Sequential(nn.Linear(2 * 512 + 32, 128), nn.ReLU(inplace=True), nn.Dropout(0.2),
                                      nn.Linear(128, n_cls))
            mean = torch.tensor([0.406, 0.456, 0.485] + [0.449] * (ch - 3)).view(1, ch, 1, 1)
            std = torch.tensor([0.225, 0.224, 0.229] + [0.226] * (ch - 3)).view(1, ch, 1, 1)
            self.register_buffer('mean', mean)
            self.register_buffer('std', std)

        def forward(self, img, ctx):
            x = ((img + 0.5) - self.mean) / self.std  # to_input gives x/255 - 0.5
            x = nn.functional.interpolate(x, scale_factor=3, mode='bilinear', align_corners=False)  # 32 -> 96
            with torch.autocast('cuda', dtype=torch.bfloat16):
                m = self.conv(x)  # B x 512 x 3 x 3
            m = m.float()
            g = m.mean((2, 3))
            c = m[:, :, 1, 1]
            return self.head(torch.cat([g, c, self.ctx(ctx)], 1))

    return PreNet()


# ------------------------------------------------------------------ training

def fit_net(crops, ctx, y, w, gk, a, dev, seed, log=''):
    import torch
    import torch.nn.functional as Fn

    from pitchiq.balltrack import train_ball_classifier as T

    T.seed_all(seed)
    rng = np.random.default_rng(seed)
    ch = crops.shape[-1]
    if a.arch == 'resnet18':
        net = make_resnet18(ctx.shape[1], ch, len(T.CLASSES))
        bs, lr = 256, 1e-3
    else:
        net = T.BallNet(ctx.shape[1], w=(32, 64, 96, 128) if a.wide else (16, 32, 48, 64))
        if ch != 3:
            net.conv[0][0] = torch.nn.Conv2d(ch, net.conv[0][0].out_channels, 3, padding=1, bias=False)
        bs, lr = 256, 2e-3
    net = net.to(dev)
    rest = [q for n, q in net.named_parameters() if not n.startswith('conv.')]
    opt = torch.optim.AdamW([dict(params=net.conv.parameters(), lr=lr * a.conv_lr), dict(params=rest, lr=lr)],
                            weight_decay=1e-4)
    nb = (len(y) + bs - 1) // bs
    steps = max(1, a.epochs * nb)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[lr * a.conv_lr, lr], total_steps=steps, pct_start=0.15)
    sw = w * T.class_weights(y)[y]
    ctx_t = torch.as_tensor(ctx, device=dev)
    y_t = torch.as_tensor(y, device=dev)
    sw_t = torch.as_tensor(sw, device=dev)
    G = groups(y, gk, a.kmax, rng) if a.rank > 0 else []
    if a.rank > 0:
        print('%s  rank groups %d (frames with one ball and >= 1 other)' % (log, len(G)), flush=True)
    gpb = max(1, a.gbatch)
    for ep in range(a.epochs):
        net.train()
        perm = rng.permutation(len(y))
        gperm = rng.permutation(len(G)) if G else None
        tot, tr, t0 = 0.0, 0.0, time.time()
        for s, st0 in enumerate(range(0, len(perm), bs)):
            b = perm[st0:st0 + bs]
            if len(b) < 2:
                continue
            x = T.augment(crops[b], dev, rng)
            bt = torch.as_tensor(b, device=dev)
            loss = (Fn.cross_entropy(net(x, ctx_t[bt]), y_t[bt], reduction='none') * sw_t[bt]).sum() / sw_t[bt].sum()
            if G:
                # cycle through the groups: gpb frames per step
                sel = [G[gperm[(s * gpb + q) % len(G)]] for q in range(gpb)]
                rows = np.concatenate([np.concatenate([[bi], ni]) for bi, ni in sel])
                lens = [1 + len(ni) for _, ni in sel]
                xr = T.augment(crops[rows], dev, rng)
                rt = torch.as_tensor(rows, device=dev)
                lo = T.ball_logodds(net(xr, ctx_t[rt]))
                km = max(lens)
                mat = torch.full((len(sel), km), -1e4, device=dev)
                off = 0
                for q, L in enumerate(lens):
                    mat[q, :L] = lo[off:off + L]
                    off += L
                lr_ = Fn.cross_entropy(mat, torch.zeros(len(sel), dtype=torch.long, device=dev))  # ball is column 0
                loss = loss + a.rank * lr_
                tr += float(lr_.detach())
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss.detach()) * len(b)
        if ep == 0 or ep + 1 == a.epochs:
            print('%s  epoch %2d/%d  loss %.4f  rank %.4f  %.1fs' % (log, ep + 1, a.epochs, tot / len(y), tr / nb,
                                                                   time.time() - t0), flush=True)
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
        hc = T2.with_stack(hc, None)
    for g in (a.games.split(',') if a.games else st.GAMES):
        i = st.GAMES.index(g)
        t0 = time.time()
        cc_, ck_, cy, cg = click_rows(g, a.stack)
        crops, ctx, y = [hc, cc_], [hk, ck_], [hy, cy]
        w = [hw, np.where(cy == 0, a.cw, 1.0).astype(np.float32)]  # --cw: weight of clicked balls
        gk = [np.full(len(hy), -1, np.int64), cg.astype(np.int64)]
        base = int(cg.max()) + 1 if len(cg) else 0
        for si, p in enumerate([p for p in a.pseudo.split('+') if p != 'none']):
            c2, k2, y2, g2 = pseudo_rows('no_%s' % g if p == 'strict' else '%sno_%s' % (p, g) if p[:1] == 'k' and p[1:].isdigit() else 'own_%s%s' % (g, a.lab), a.stack)
            crops.append(c2), ctx.append(k2), y.append(y2), w.append(np.full(len(y2), a.w, np.float32))
            gk.append(base + (si + 1) * (1 << 40) + g2)
        crops, ctx, y, w, gk = (np.concatenate(crops), np.concatenate(ctx), np.concatenate(y), np.concatenate(w),
                                np.concatenate(gk))
        if a.zone:
            m = feet_zone(ctx, names, a.near, 'player' if a.zone in ('player', 'notplayer') else 'feet')
            if a.zone in ('rest', 'notplayer'):
                m = ~m
            crops, ctx, y, w, gk = crops[m], ctx[m], y[m], w[m], gk[m]
            print('fold %s: zone %s keeps %d rows (ball %d)' % (g, a.zone, len(y), int((y == 0).sum())), flush=True)
        drop = [list(names).index(q) for q in a.drop_ctx.split(',')] if a.drop_ctx else []
        if drop:  # these context columns are held at 0 in training and scoring (e.g. s,rank: no ranker input)
            ctx = ctx.copy()
            ctx[:, drop] = 0
        mu, sd = T.ctx_stats(ctx)
        sd = sd.copy() if hasattr(sd, 'copy') else sd
        if drop:
            sd[drop] = 1.0
        net = fit_net(crops, T.ctx_norm(ctx, mu, sd), y, w, gk, a, dev, i + a.seed_offset, log='fold %s' % g)
        sc = T.Scorer(net, mu, sd, names, dev)
        torch.save(dict(state=net.state_dict(), mu=mu.tolist(), sd=sd.tolist(), ctx_names=list(names), epochs=a.epochs,
                        pseudo=a.pseudo, stack=a.stack, arch=a.arch, rank=a.rank), out + '/fold_%s.pt' % g)
        for pt in st.pieces([t for t in st.DEV if st.game(t) == g]):
            ca = st.load_cache(pt)
            c = ca['crop']
            if a.stack:
                c = T2.with_stack(c, np.load(st.OUT + '/tcache/%s.npy' % pt))
            k = ca['ctx']
            if drop:
                k = np.array(k, copy=True)
                k[:, drop] = 0
            np.save(out + '/raw/%s.npy' % pt, sc.score(c, k, batch=1024))
        print('fold %s: human %d + click rows %d (ball %d) + pseudo %d, arch %s, rank %g, %.0f s' % (
            g, len(hy), len(cy), int((cy == 0).sum()), len(y) - len(hy) - len(cy), a.arch, a.rank,
            time.time() - t0), flush=True)
        del crops, ctx, net, sc
    print('FIT DONE', a.run, flush=True)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    f = sp.add_parser('fit')
    f.add_argument('run')
    f.add_argument('--games', default='')
    f.add_argument('--pseudo', default='strict+own')
    f.add_argument('--lab', default='')
    f.add_argument('--stack', type=int, default=1)
    f.add_argument('--wide', type=int, default=0)
    f.add_argument('--w', type=float, default=0.5)
    f.add_argument('--epochs', type=int, default=12)
    f.add_argument('--seed-offset', type=int, default=0)
    f.add_argument('--conv-lr', type=float, default=1.0)
    f.add_argument('--rank', type=float, default=0.0)
    f.add_argument('--kmax', type=int, default=24)  # rows per frame group (ball + up to kmax-1 others)
    f.add_argument('--gbatch', type=int, default=16)  # frame groups per step
    f.add_argument('--arch', default='ballnet')
    f.add_argument('--cw', type=float, default=1.0)  # loss weight of the other games' clicked balls
    f.add_argument('--zone', default='')  # feet | rest: train on that zone's rows only (feet_zone)
    f.add_argument('--near', type=float, default=0.1)
    f.add_argument('--drop-ctx', default='')  # context columns held at 0, e.g. s,rank
    fit(ap.parse_args())


if __name__ == '__main__':
    main()
