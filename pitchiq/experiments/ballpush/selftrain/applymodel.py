"""Saved classifier checkpoints on a rebuilt cache: no training, no calibration fit.

For a half whose candidates changed (re-detected under BP_BT), with cache.py and tcache.py rebuilt under ST_OUT:
score the cache with the saved models (train3 resnet18 checkpoints: runs/<run>/fold_<game>.pt on dev,
held/runs/<run>/model_<game>.pt on held out), average their raw scores, apply the affine the run was shipped
with (a, b: read from its cal file, fitted before on the old candidates, label-free), and write
<out>/<piece>cls.npz.  Candidates outside the cache keep the base score (ST_OOF).

    BP_BT=<dir> ST_OUT=<dir> ST_OOF=<dir> [ST_HELD=1] python applymodel.py <a> <b> <out cls dir> <model.pt,model.pt> <half ...>
"""
import os
import sys

import numpy as np

import st
import train2 as T2
import train3 as T3


def main():
    import torch

    st.common.point_at_data()
    from pitchiq.balltrack import train_ball_classifier as T

    a, b = float(sys.argv[1]), float(sys.argv[2])
    out = sys.argv[3]
    os.makedirs(out, exist_ok=True)
    dev = T.device()
    scorers = []
    for mp in sys.argv[4].split(','):
        ck = torch.load(mp, map_location=dev, weights_only=True)
        assert ck['arch'] == 'resnet18' and ck['stack'] == 1, mp
        net = T3.make_resnet18(len(ck['ctx_names']), 7, len(T.CLASSES)).to(dev)
        net.load_state_dict(ck['state'])
        scorers.append(T.Scorer(net, np.asarray(ck['mu'], np.float32), np.asarray(ck['sd'], np.float32), ck['ctx_names'], dev))
    for pt in st.pieces(sys.argv[5:]):
        ca = st.load_cache(pt)
        c = T2.with_stack(ca['crop'], np.load(st.OUT + '/tcache/%s.npy' % pt))
        raw = np.mean([s.score(c, ca['ctx'], batch=1024) for s in scorers], 0)
        f, j, p = st.load_oof(pt)
        p = p.copy()
        i = np.searchsorted(f.astype(np.int64) * 1024 + j, ca["f"].astype(np.int64) * 1024 + ca["j"])  # 1024: any cut up to 1023 per frame
        assert (f[i] == ca['f']).all() and (j[i] == ca['j']).all(), pt
        p[i] = a * raw + b
        np.savez(os.path.join(out, '%scls.npz' % pt), f=f.astype(np.int32), j=j.astype(np.int16), p=p.astype(np.float32))
        print('%s  cached %d of %d  raw median %.2f  base-vs-new corr %.2f' % (
            pt, len(raw), len(p), float(np.median(raw)), float(np.corrcoef(raw, st.load_oof(pt)[2][i])[0, 1])), flush=True)
    print('APPLY DONE', flush=True)


if __name__ == '__main__':
    main()
