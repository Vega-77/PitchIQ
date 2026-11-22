"""Click propagation: hard ball positives from the frames around every human ball click.

The classifier scores ~125 clicked balls at p <= 0 (funnel2.py); self-training never mines such balls because
the tracker never picks them.  A click says where the ball is in ONE frame; the same ball is in the frames
around it, looking just as hard.  From the clicked blob, chain to the nearest candidate in each neighbouring
frame (pan-stabilised px, constant-velocity prediction), stopping at the first miss or ambiguity.

    python prop.py check [K]    the same chaining started from picks on confident tracker runs, compared with
                                the tracker's own picks k frames away (how often does the chain stay on the ball)
    python prop.py rows [K]     -> selftrain/prop/rows/<piece>.npz (f, j) to cache, and
                                   selftrain/prop/chains.json [{game, half, piece, f0, f, j, k}]
Honest use: fold G trains only on chains whose click is in another game.
"""

import json
import os
import sys

import numpy as np

import st

R0, R1 = (
    25.0,
    10.0,
)  # search radius: first step (velocity unknown), later steps (around the prediction)


def load(pt):
    st.common.point_at_data()
    from pitchiq.balltrack import ball_tracker, gap_fill

    os.chdir(st.BT)
    F = ball_tracker.load_frames(pt)
    gap_fill.attach_pan(F, pt)
    return {int(fr['abs']): fr for fr in F}


def chain(F, f, j, K, step):
    from pitchiq.balltrack import gap_fill

    KX, KY = gap_fill.KX, gap_fill.KY
    pos = np.array([F[f][KX][j], F[f][KY][j]], float)
    v = None
    out = []
    for k in range(1, K + 1):
        fr = F.get(f + step * k)
        if fr is None or not len(fr['s']):
            break
        pred = pos if v is None else pos + v
        d = np.hypot(fr[KX] - pred[0], fr[KY] - pred[1])
        o = np.argsort(d)[:2]
        if d[o[0]] > (R0 if v is None else R1):
            break
        if len(o) > 1 and d[o[1]] < max(2 * d[o[0]], d[o[0]] + 8):
            break
        new = np.array([fr[KX][o[0]], fr[KY][o[0]]], float)
        v, pos = new - pos, new
        out.append((f + step * k, int(o[0]), k))
    return out


def check(K):
    rng = np.random.default_rng(0)
    agree = np.zeros(K + 1)
    reach = np.zeros(K + 1)
    n = 0
    for tag in st.DEV:
        z = np.load(st.OUT + '/mined/oof/%s.npz' % tag)
        lo = int(z['lo'])
        on = (z['src'] == 1) & np.isfinite(z['x'])
        d = np.diff(np.r_[0, on.astype(np.int8), 0])
        cand = []
        for b, e in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
            if e - b >= 60 and np.median(z['p'][b:e]) >= 3:
                cand += [
                    i
                    for i in range(b + K, e - K)
                    if (z['owner'][i - K : i + K + 1] == z['owner'][i]).all()
                ]
        cand = rng.choice(cand, min(300, len(cand)), replace=False) if len(cand) else []
        Fs = {}
        for i in cand:
            pt = z['pieces'][z['owner'][i]]
            if pt not in Fs:
                Fs[pt] = load(pt)
            F = Fs[pt]
            n += 1
            for step in (-1, 1):
                for f2, j2, k in chain(F, lo + i, int(z['j'][i]), K, step):
                    reach[k] += 1
                    agree[k] += int(z['j'][f2 - lo]) == j2
        print(tag, 'starts so far', n, flush=True)
    for k in range(1, K + 1):
        print(
            'k %2d: chain alive %5.1f%% of %d, on the tracker pick %5.1f%%'
            % (k, 100 * reach[k] / (2 * n), 2 * n, 100 * agree[k] / max(1, reach[k]))
        )


def rows(K):
    import train as T1

    seen = {}
    for tag, pt, f, v, n, jb in T1.click_index():
        if v == 'ball' and jb >= 0:
            seen.setdefault((tag, f), (pt, jb))
    Fs, out, per = {}, [], {}
    for (tag, f), (pt, jb) in sorted(seen.items(), key=lambda kv: kv[1][0]):
        if pt not in Fs:
            Fs = {pt: load(pt)}
        for step in (-1, 1):
            for f2, j2, k in chain(Fs[pt], f, jb, K, step):
                out.append(
                    dict(game=st.game(tag), half=tag, piece=pt, f0=f, f=f2, j=j2, k=k * step)
                )
                per.setdefault(pt, set()).add((f2, j2))
    os.makedirs(st.OUT + '/prop/rows', exist_ok=True)
    for pt, s in per.items():
        a = np.array(sorted(s))
        np.savez(st.OUT + '/prop/rows/%s.npz' % pt, f=a[:, 0], j=a[:, 1])
    json.dump(out, open(st.OUT + '/prop/chains.json', 'w'))
    g = {}
    for r in out:
        g[r['game']] = g.get(r['game'], 0) + 1
    print(
        'clicks with a blob %d, chained rows %d (%.1f per click), per game %s'
        % (len(seen), len(out), len(out) / len(seen), g)
    )
    ks = np.array([abs(r['k']) for r in out])
    print('rows by distance:', {k: int((ks == k).sum()) for k in range(1, K + 1)})


if __name__ == '__main__':
    K = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    (check if sys.argv[1] == 'check' else rows)(K)
