"""Every leave-one-game-out baseline model (oof_cls/fold_<game>.pt) on every cached dev crop.

fold_G never saw game G, so its scores on the OTHER games are what a no-G world would have mined
pseudo-labels from.  One pass over the cache, all eight models:

    python foldscore.py   -> selftrain/foldp/<piece>.npy  (8 x N float32, GAMES order, calibrated)
    python foldscore.py <run>  -> selftrain/foldp_<run>/ : the same with a self-trained run's folds (round 2;
                                  the own-game check line is then meaningless)

The piece's own game's row must equal the stored out-of-fold scores (the cache check).
"""
import os
import time

import numpy as np

import st


def load_scorer(path, dev=None):
    """A train_ball_classifier.Scorer from a checkpoint, read with weights_only=True."""
    import torch

    from pitchiq.balltrack import train_ball_classifier as T

    dev = dev or T.device()
    sg = [np._core.multiarray._reconstruct, np.ndarray, np.dtype, type(np.dtype('float32')), type(np.dtype('float64'))]
    with torch.serialization.safe_globals(sg):
        ck = torch.load(path, map_location=dev, weights_only=True)
    net = T.BallNet(len(ck['ctx_names'])).to(dev)
    net.load_state_dict(ck['state'])
    return T.Scorer(net, np.asarray(ck['mu'], np.float32), np.asarray(ck['sd'], np.float32), ck['ctx_names'], dev,
                    ck.get('cal', (1.0, 0.0)))


def run_scorers(run):
    """Round 2: the folds of a self-trained run (runs/<run>/fold_<G>.pt) with that run's one affine."""
    import json

    cal = json.load(open(st.OUT + '/runs/%s/cal_global.json' % run))
    M = []
    for g in st.GAMES:
        m = load_scorer(st.OUT + '/runs/%s/fold_%s.pt' % (run, g))
        m.cal = (float(cal[g][0]), float(cal[g][1]))
        M.append(m)
    return M


def main():
    import sys

    run = sys.argv[1] if len(sys.argv) > 1 else None
    out = st.OUT + ('/foldp_%s' % run if run else '/foldp')
    os.makedirs(out, exist_ok=True)
    M = run_scorers(run) if run else [load_scorer(os.path.join(st.OOF, 'fold_%s.pt' % g)) for g in st.GAMES]
    t0 = time.time()
    worst = 0.0
    for pt in st.pieces():
        d = st.load_cache(pt)
        P = np.stack([m.score(d['crop'], d['ctx']) for m in M])
        np.save(os.path.join(out, '%s.npy' % pt), P)
        f, j, p = st.load_oof(pt)
        own = P[st.GAMES.index(st.game(pt))]
        ref = p[np.searchsorted(f * 64 + j, d['f'] * 64 + d['j'])]
        e = float(np.abs(own - ref).max())
        worst = max(worst, e)
        print('%s  %d crops  own-fold vs stored oof: max |diff| %.4f  (%.0f s)' % (pt, len(own), e, time.time() - t0), flush=True)
    print('FOLDSCORE DONE  worst diff %.4f' % worst, flush=True)


if __name__ == '__main__':
    main()
