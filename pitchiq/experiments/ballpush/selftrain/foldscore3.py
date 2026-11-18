"""Round 2 for the ResNet-18 recipe: every fold model of an averaged run (e.g. kres12 = kres1 + kres2) on every
cached dev crop, so mine.py can mine each game's OTHER games under a model that never saw it.

    python foldscore3.py <avg run> <run> [<run> ...]
        -> selftrain/foldp_<avg run>/<piece>.npy  (8 x N float32, GAMES order)
        row G = cal(<avg run>) applied to the mean raw score of fold_G over the given runs

Check: the piece's own game's row must equal runs/<avg run>_global (the stored out-of-fold scores of the average).
"""
import json
import os
import sys
import time

import numpy as np

import st
import train2 as T2
import train3 as T3


def load_net(path, dev):
    import torch

    from pitchiq.balltrack import train_ball_classifier as T

    sg = [np._core.multiarray._reconstruct, np.ndarray, np.dtype, type(np.dtype('float32')), type(np.dtype('float64'))]
    with torch.serialization.safe_globals(sg):
        ck = torch.load(path, map_location=dev, weights_only=True)
    assert ck.get('arch') == 'resnet18' and ck.get('stack') == 1, path
    net = T3.make_resnet18(len(ck['ctx_names']), 7, len(T.CLASSES)).to(dev)
    net.load_state_dict(ck['state'])
    return T.Scorer(net.eval(), np.asarray(ck['mu'], np.float32), np.asarray(ck['sd'], np.float32), ck['ctx_names'], dev)


def main():
    from pitchiq.balltrack import train_ball_classifier as T

    avg, runs = sys.argv[1], sys.argv[2:]
    dev = T.device()
    cal = json.load(open(st.OUT + '/runs/%s/cal_global.json' % avg))
    out = st.OUT + '/foldp_%s' % avg
    os.makedirs(out, exist_ok=True)
    M = [[load_net(st.OUT + '/runs/%s/fold_%s.pt' % (r, g), dev) for r in runs] for g in st.GAMES]
    t0, worst = time.time(), 0.0
    for pt in st.pieces():
        o = os.path.join(out, '%s.npy' % pt)
        n = len(st.load_cache(pt, crops=False)['f'])
        c, k = T2.piece_rows(pt, np.arange(n), 1)
        P = np.stack([np.mean([m.score(c, k, batch=1024) for m in M[gi]], 0) * cal[g][0] + cal[g][1]
                      for gi, g in enumerate(st.GAMES)]).astype(np.float32)
        np.save(o, P)
        f, j, p = st.load_oof(pt, st.OUT + '/runs/%s_global' % avg)
        ca = st.load_cache(pt, crops=False)
        ref = p[np.searchsorted(f * 64 + j, ca['f'] * 64 + ca['j'])]
        e = float(np.abs(P[st.GAMES.index(st.game(pt))] - ref).max())
        worst = max(worst, e)
        print('%s  %d crops  own fold vs %s_global: max |diff| %.4f  (%.0f s)' % (pt, n, avg, e, time.time() - t0), flush=True)
    print('FOLDSCORE3 DONE  worst diff %.4f' % worst, flush=True)


if __name__ == '__main__':
    main()
