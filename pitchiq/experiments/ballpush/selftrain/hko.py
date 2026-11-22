"""Held-out own labels, round 2, on finder-rule candidates.  Reads no held-out answer.

Round 1 (held.py) took each held-out half's confident tracks under the SHIPPED scores on the shipped candidates.
Here they are mined again on the new candidates under the scores the half now has (hkr3a+hkr3b applied), and one
model per game is refitted with them; everything else in the recipe is held.py's.

    mine   BP_BT=<giant/bt_T_k40> ST_HELD=1 ST_OUT=<giant/st_T> ST_OOF=<giant/st_T/cls> python hko.py mine <T>
           -> <st_T>/mined/oof/<T>.npz, <st_T>/labels/own_<game>.npz
    fit    python hko.py fit <run> <seed offset> <giant dir>          (no ST_ env: the dev rows come from selftrain/)
           -> <giant>/st_T/runs/<run>/model_<game>.pt and raw/<piece>.npy for the five halves
    finish python hko.py finish <giant dir> <out name> <run,run>
           the runs' raw scores averaged; ONE label-free affine (frame-top quantiles of the new raw against the
           shipped base scores, the five halves pooled, as held.py finish) -> <giant>/st_T/<out name>/<piece>cls.npz
"""

import argparse
import glob
import json
import os
import sys

import numpy as np

import st


def mine_cmd(tag):
    """tag: one half, or several of one game joined by commas (ball17.py: all of a game's halves)."""
    import mine
    import pseudo

    tags = tag.split(',')
    assert st.HELD_MODE and all(t in st.DEV for t in tags) and len({st.game(t) for t in tags}) == 1
    mine.run('oof', st.OOF, tags)
    pseudo.select('own_' + st.game(tags[0]), tags, src='oof')
    print('HKO MINE DONE', tag, flush=True)


def fit_cmd(run, off, giant):
    import held

    assert not st.HELD_MODE and not os.environ.get('ST_OUT')
    for g in held.HGAMES:
        tag = held.HALF[g]
        held.HO = '%s/st_%s' % (giant, tag)
        bt = '%s/bt_%s_k40' % (giant, tag)
        held.hpieces = lambda g2, bt=bt, tag=tag: [
            os.path.basename(p)[:-4]
            for p in sorted(glob.glob(os.path.join(bt, '%s_q[0-9][0-9][0-9].npz' % tag)))
        ]
        a = argparse.Namespace(
            cmd='fit',
            run=run,
            games=g,
            w=0.5,
            epochs=12,
            seed_offset=off,
            dev_pseudo='oof_k3',
            arch='resnet18',
            conv_lr=0.3,
            wide=0,
            rank=0.0,
            kmax=24,
            gbatch=16,
        )
        held.fit(a)
    print('HKO FIT DONE', run, flush=True)


def finish_cmd(giant, name, runs):
    import held
    import train as T1

    P = []
    for g in held.HGAMES:
        tag = held.HALF[g]
        root, bt = '%s/st_%s' % (giant, tag), '%s/bt_%s_k40' % (giant, tag)
        for pp in sorted(glob.glob(os.path.join(bt, '%s_q[0-9][0-9][0-9].npz' % tag))):
            pt = os.path.basename(pp)[:-4]
            z = np.load('%s/cache/%s.npz' % (root, pt))
            cf, cj = z['f'].astype(np.int64), z['j'].astype(np.int64)
            raw = np.mean([np.load('%s/runs/%s/raw/%s.npy' % (root, r, pt)) for r in runs], 0)
            f, j, p = st.load_oof(pt, bt)
            P.append((root, pt, cf, cj, raw, f, j, p))
    tn = np.concatenate([T1.frame_top(cf, raw) for _, _, cf, cj, raw, f, j, p in P])
    to = np.concatenate([T1.frame_top(f, p) for _, _, cf, cj, raw, f, j, p in P])
    aa, bb = np.polyfit(np.percentile(tn, T1.Q), np.percentile(to, T1.Q), 1)
    print('cal: a %.4f b %.4f  (hkr3ab was a 0.5031 b -1.2765)' % (aa, bb))
    for root, pt, cf, cj, raw, f, j, p in P:
        os.makedirs('%s/%s' % (root, name), exist_ok=True)
        i = np.searchsorted(f * 1024 + j, cf * 1024 + cj)
        assert (f[i] == cf).all() and (j[i] == cj).all(), pt
        old = np.load('%s/cls/%scls.npz' % (root, pt))['p']
        q = p.copy()
        q[i] = aa * raw + bb
        np.savez(
            '%s/%s/%scls.npz' % (root, name, pt),
            f=f.astype(np.int32),
            j=j.astype(np.int16),
            p=q.astype(np.float32),
        )
        print(
            '%s  cached %d  new vs hkr3ab corr %.2f  frame-top changed on %.1f%% of cached rows'
            % (
                pt,
                len(raw),
                float(np.corrcoef(q[i], old[i])[0, 1]),
                100 * float(np.mean(np.abs(q[i] - old[i]) > 1)),
            ),
            flush=True,
        )
    json.dump([float(aa), float(bb)], open('%s/hko_%s_cal.json' % (giant, name), 'w'))
    print('HKO FINISH DONE', flush=True)


if __name__ == '__main__':
    c = sys.argv[1]
    if c == 'mine':
        mine_cmd(sys.argv[2])
    elif c == 'fit':
        fit_cmd(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    else:
        finish_cmd(sys.argv[2], sys.argv[3], sys.argv[4].split(','))
