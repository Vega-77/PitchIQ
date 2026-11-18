"""New-brief 2.1: pretrain BallNet's conv trunk on the SoccerNet crops (sn_crops.py).

    python sn_pre.py <crops.npz> <out.pt> [--stack 1] [--wide 0] [--epochs 20] [--seed 0]

The net is train2.py's (7-channel first conv with --stack: own grey repeated as the neighbours, as for the
human crops).  There is no context for these crops: the ctx branch gets zeros and is not kept.  The last six
sequences are held back to report ball-vs-rest accuracy.  -> out.pt {'conv': state dict of net.conv, ...}
"""
import argparse
import time

import numpy as np

import train2 as T2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('crops')
    ap.add_argument('out')
    ap.add_argument('--stack', type=int, default=1)
    ap.add_argument('--wide', type=int, default=0)
    ap.add_argument('--epochs', type=int, default=20)
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()
    import torch

    from pitchiq.balltrack import train_ball_classifier as T

    z = np.load(a.crops)
    crop, y, seq = z['crop'], z['y'], z['seq']
    if a.stack:
        crop = T2.with_stack(crop, None)
    val = seq >= seq.max() - 5
    tr = ~val
    dev = T.device()
    k = 4  # any ctx width: zeros in, branch dropped
    t0 = time.time()
    net = T2.fit_net(crop[tr], np.zeros((int(tr.sum()), k), np.float32), y[tr], np.ones(int(tr.sum()), np.float32),
                     a.epochs, dev, a.seed, a.wide, log='sn_pre')
    with torch.no_grad():
        lo = []
        vi = np.flatnonzero(val)
        for b in range(0, len(vi), 2048):
            i = vi[b:b + 2048]
            x = T.to_input(crop[i], dev)
            lo.append(T.ball_logodds(net(x, torch.zeros((len(i), k), device=dev))).float().cpu().numpy())
        lo = np.concatenate(lo)
    yb = y[val] == 0
    print('held-back sequences: rows %d, balls %d; ball recall at 0: %.3f, not-ball passing 0: %.4f; at 2: %.3f / %.4f' % (
        len(yb), int(yb.sum()), float((lo[yb] > 0).mean()), float((lo[~yb] > 0).mean()),
        float((lo[yb] > 2).mean()), float((lo[~yb] > 2).mean())), flush=True)
    torch.save(dict(conv=net.conv.state_dict(), stack=a.stack, wide=a.wide, epochs=a.epochs, rows=int(tr.sum())), a.out)
    print('PRE DONE %s  %.0f s' % (a.out, time.time() - t0), flush=True)


if __name__ == '__main__':
    main()
