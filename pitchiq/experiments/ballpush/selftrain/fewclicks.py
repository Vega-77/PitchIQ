"""Step 3 (dev only): do a few clicks from a NEW game help the classifier on the rest of that game?

For each dev game G: the saved fold models (trained without G) are fine-tuned on K clicked frames of G (every
cached candidate of those frames: the ball row if a candidate is within 8 px of the click, the others as clutter;
out frames are all clutter), a few low-rate steps.  Judged on G's OTHER clicked frames, before and after:
ball first (the top-scoring candidate is within 15 px of the click, of the ball frames that have such a
candidate), and how many out frames' best candidate outscores the median test ball (a wrong-ring proxy).
Several random splits per game; the same split is scored before and after.

    PYTHONPATH=<bp-selftrain> python fewclicks.py <K> <steps> <lr> <splits> <run,run> [game ...]
"""
import sys

import numpy as np

import st
import train2 as T2
import train3 as T3


def frames_of(g):
    """[(crops, ctx, ball row or -1, kind)] for every clicked frame of game g that has cached candidates."""
    from pitchiq.balltrack import ball_tracker
    ck = st.clicks()
    out = []
    for tag in st.DEV:
        if st.game(tag) != g:
            continue
        for pt in st.pieces([tag]):
            ca = st.load_cache(pt)
            key = ca['f'] * 64 + ca['j']
            tc = None
            F = None
            for iid, f, a in ck[tag]:
                a0, a1 = np.searchsorted(key, [f * 64, f * 64 + 64])
                if a1 <= a0:
                    continue
                if F is None:
                    F = {fr['abs']: fr for fr in ball_tracker.load_frames(pt)}
                    tc = np.load(st.OUT + '/tcache/%s.npy' % pt)
                rows = np.arange(a0, a1)
                c = T2.with_stack(ca['crop'][rows], tc[rows])
                k = ca['ctx'][rows]
                jj = ca['j'][rows]
                if a['v'] == 'ball':
                    d = np.hypot(F[f]['x'][jj] - a['x'], F[f]['y'][jj] - a['y'])
                    jb = int(np.argmin(d))
                    out.append(dict(c=c, k=k, d=d, ball=jb if d[jb] <= 8 else -1, has=bool(d[jb] <= 15), v='ball'))
                else:
                    out.append(dict(c=c, k=k, d=None, ball=-1, has=False, v='out'))
    return out


def main():
    import torch
    import torch.nn.functional as Fn

    st.common.point_at_data()
    from pitchiq.balltrack import train_ball_classifier as T

    K, steps, lr, splits = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4])
    runs = sys.argv[5].split(',')
    games = sys.argv[6:] or st.GAMES
    dev = T.device()
    tot = np.zeros(6)
    for g in games:
        FR = frames_of(g)
        if len(FR) < K + 10:
            print('%-5s only %d clicked frames, skipped' % (g, len(FR)))
            continue
        cks = [torch.load(st.OUT + '/runs/%s/fold_%s.pt' % (r, g), map_location=dev, weights_only=True) for r in runs]
        gt = np.zeros(6)
        for sp in range(splits):
            rng = np.random.default_rng(1000 * st.GAMES.index(g) + sp)
            perm = rng.permutation(len(FR))
            giv, tst = [FR[i] for i in perm[:K]], [FR[i] for i in perm[K:]]
            sc = {0: [], 1: []}
            for ck in cks:
                net = T3.make_resnet18(len(ck['ctx_names']), 7, len(T.CLASSES)).to(dev)
                net.load_state_dict(ck['state'])
                mu, sd = np.asarray(ck['mu'], np.float32), np.asarray(ck['sd'], np.float32)

                def score(fr):
                    net.eval()
                    with torch.no_grad():
                        x = T.to_input(fr['c'], dev) if hasattr(T, 'to_input') else None
                        return T.ball_logodds(net(x, torch.as_tensor(T.ctx_norm(fr['k'], mu, sd), device=dev))).float().cpu().numpy()

                sc[0].append([score(fr) for fr in tst])
                # fine-tune on the given frames
                c = np.concatenate([fr['c'] for fr in giv])
                k = T.ctx_norm(np.concatenate([fr['k'] for fr in giv]), mu, sd)
                y, keep = [], []
                for fr in giv:
                    yy = np.full(len(fr['k']), 4)
                    kk = np.ones(len(fr['k']), bool)
                    if fr['v'] == 'ball':
                        kk = fr['d'] > 16  # the 8-16 px ring around the click is neither
                        if fr['ball'] >= 0:
                            yy[fr['ball']] = 0
                            kk[fr['ball']] = True
                    y.append(yy), keep.append(kk)
                y, keep = np.concatenate(y), np.concatenate(keep)
                c, k, y = c[keep], k[keep], y[keep]
                w = np.where(y == 0, (y != 0).sum() / max(1, (y == 0).sum()), 1.0).astype(np.float32)
                w = np.minimum(w, 20.0)
                opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=1e-4)
                kt, yt, wt = torch.as_tensor(k, device=dev), torch.as_tensor(y, device=dev), torch.as_tensor(w, device=dev)
                net.train()
                for m in net.modules():  # 20 frames are too few to re-estimate batch statistics
                    if isinstance(m, torch.nn.BatchNorm2d):
                        m.eval()
                for s in range(steps):
                    b = rng.choice(len(y), min(256, len(y)), replace=False)
                    bt = torch.as_tensor(b, device=dev)
                    loss = (Fn.cross_entropy(net(T.augment(c[b], dev, rng), kt[bt]), yt[bt], reduction='none') * wt[bt]).sum() / wt[bt].sum()
                    opt.zero_grad(set_to_none=True)
                    loss.backward()
                    opt.step()
                sc[1].append([score(fr) for fr in tst])
                del net
            res = []
            for ph in (0, 1):
                S = [np.mean([sc[ph][m][i] for m in range(len(cks))], 0) for i in range(len(tst))]
                first = sum(1 for fr, s in zip(tst, S) if fr['v'] == 'ball' and fr['has'] and fr['d'][int(np.argmax(s))] <= 15)
                bs = [float(s[fr['d'] <= 15].max()) for fr, s in zip(tst, S) if fr['v'] == 'ball' and fr['has']]
                thr = float(np.median(bs)) if bs else 0.0
                outhi = sum(1 for fr, s in zip(tst, S) if fr['v'] == 'out' and s.max() > thr)
                res += [first, outhi]
            nball = sum(1 for fr in tst if fr['v'] == 'ball' and fr['has'])
            nout = sum(1 for fr in tst if fr['v'] == 'out')
            gt += np.array([nball, nout] + res)
        gt /= splits
        tot += gt
        print('%-5s test ball frames %5.1f out %5.1f | ball first %5.1f -> %5.1f | out above median ball %4.1f -> %4.1f   (mean of %d splits, %d given)' % (
            g, gt[0], gt[1], gt[2], gt[4], gt[3], gt[5], splits, K), flush=True)
    print('ALL   test ball frames %5.1f out %5.1f | ball first %5.1f -> %5.1f | out above median ball %4.1f -> %4.1f' % (
        tot[0], tot[1], tot[2], tot[4], tot[3], tot[5]))
    print('FEWCLICKS DONE', flush=True)


if __name__ == '__main__':
    main()
