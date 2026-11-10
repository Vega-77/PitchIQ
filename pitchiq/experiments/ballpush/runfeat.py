"""Do wrong rings move less smoothly than right ones?  Trajectory-shape features of the ringed track around
every ringed dev click (from a replay's cache), right vs wrong, and a leave-one-game-out removal rule.

    python runfeat.py [variant, default base]
Features over the ringed frames within +-W of the click (same piece, so stabilised px are comparable):
  cov    share of the 2W+1 frames that are ringed
  acc    median |second difference| of the stabilised position over consecutive ringed triples, px/frame^2
  acc90  its 90th percentile
  res1   rms residual of a straight constant-velocity fit, px;  res2  of a constant-acceleration fit
  speed  median step, px/frame;  turn  share of consecutive steps (> 1 px) that reverse direction (cos < 0)
  pmean / pmin / p0   classifier score over the window / at the click frame;  fill  share of gap-filled frames
  runlen length of the unbroken ringed run through the click (capped at 300)
"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402
from measure_report import auc  # noqa: E402

OUTD = os.path.join(common.OUT, 'tracker')
GAME = dict(nb1='nb', ww1='ww', ww2='ww', eb1='eb', eb2='eb', scrimh1='scrim', scrimh2='scrim', wh2='wh', wb1='wb', pi2='pi', co2='co')
NAMES = ['cov', 'acc', 'acc90', 'res1', 'res2', 'speed', 'turn', 'pmean', 'pmin', 'p0', 'fill', 'runlen']


def feats(z, i, W):
    n = len(z['x'])
    a, b = max(0, i - W), min(n, i + W + 1)
    k = np.arange(a, b)
    m = np.isfinite(z['x'][k]) & (z['src'][k] > 0) & (z['owner'][k] == z['owner'][i])
    k = k[m]
    sx, sy, p = z['sx'][k], z['sy'][k], z['p'][k]
    f = dict(cov=len(k) / (2 * W + 1.0), pmean=np.nanmean(p), pmin=np.nanmin(p), p0=z['p'][i], fill=(z['src'][k] == 2).mean())
    on = np.isfinite(z['x']) & (z['src'] > 0)
    lo = i
    while lo > 0 and on[lo - 1] and i - lo < 150:
        lo -= 1
    hi = i
    while hi < n - 1 and on[hi + 1] and hi - i < 150:
        hi += 1
    f['runlen'] = hi - lo + 1
    c = (np.diff(k) == 1)
    tri = np.flatnonzero(c[:-1] & c[1:]) if len(k) >= 3 else []
    if len(tri):
        ax = sx[tri + 2] - 2 * sx[tri + 1] + sx[tri]
        ay = sy[tri + 2] - 2 * sy[tri + 1] + sy[tri]
        am = np.hypot(ax, ay)
        f['acc'], f['acc90'] = np.median(am), np.percentile(am, 90)
        v1 = np.column_stack([sx[tri + 1] - sx[tri], sy[tri + 1] - sy[tri]])
        v2 = np.column_stack([sx[tri + 2] - sx[tri + 1], sy[tri + 2] - sy[tri + 1]])
        mv = (np.hypot(*v1.T) > 1) & (np.hypot(*v2.T) > 1)
        f['turn'] = ((v1 * v2).sum(axis=1)[mv] < 0).mean() if mv.any() else 0.0
    st = np.flatnonzero(c)
    if len(st):
        f['speed'] = np.median(np.hypot(sx[st + 1] - sx[st], sy[st + 1] - sy[st]))
    if len(k) >= 5:
        t = (k - i).astype(float)
        for deg, nm in ((1, 'res1'), (2, 'res2')):
            rx = sx - np.polyval(np.polyfit(t, sx, deg), t)
            ry = sy - np.polyval(np.polyfit(t, sy, deg), t)
            f[nm] = np.sqrt((rx ** 2 + ry ** 2).mean())
    return f


def table(var='base', W=10):
    d = json.load(open(os.environ.get('RF_JSON') or os.path.join(OUTD, 'replay', 'replay_oof_cls.json')))
    per = d['per'][var]
    items = {it['id']: it for it in json.load(open(os.path.join(common.DATA, 'clicker', 'items_all.json')))['items']}
    Z = {}
    rows = []
    for iid, r in per.items():
        if not r['ring']:
            continue
        tag = r['half']
        if tag not in Z:
            Z[tag] = dict(np.load(os.path.join(OUTD, 'replay', 'replay_cache', 'oof_cls_%s_%s.npz' % (var.replace(':', '_'), tag))))
        z = Z[tag]
        i = int(items[iid]['f']) - int(z['lo'])
        f = feats(z, i, W)
        rows.append((iid, tag, bool(r['right']), r['v'], [f.get(k, np.nan) for k in NAMES]))
    return rows


def main():
    var = sys.argv[1] if len(sys.argv) > 1 else 'base'
    for W in (5, 10, 20):
        rows = table(var, W)
        X = np.array([r[4] for r in rows], float)
        y = np.array([r[2] for r in rows])
        out = np.array([r[3] == 'out' for r in rows])
        game = np.array([GAME[r[1]] for r in rows])
        print('\n== %s, window +-%d: %d right, %d wrong on ball, %d out rings' % (var, W, y.sum(), (~y & ~out).sum(), out.sum()))
        print('%-7s | right q25 med q75 | wrong q25 med q75 | AUC(wrong > right)' % 'feat')
        for j, k in enumerate(NAMES):
            a, b = X[y, j], X[~y, j]
            print('%-7s | %7.2f %7.2f %7.2f | %7.2f %7.2f %7.2f | %.3f' % (k, *np.nanpercentile(a, [25, 50, 75]), *np.nanpercentile(b, [25, 50, 75]), auc(b, a)))
        # removal rule: one feature, one threshold; in-sample best and leave-one-game-out
        print('removal rules (drop the ring when feature beyond threshold); objective wrong removed - 2 * right removed')
        for j, k in enumerate(NAMES):
            for sgn in (1, -1):
                v = np.nan_to_num(sgn * X[:, j], nan=-np.inf)

                def best(m):
                    bo, bt = 0, np.inf
                    for th in np.unique(v[m]):
                        o = (~y[m] & (v[m] >= th)).sum() - 2 * (y[m] & (v[m] >= th)).sum()
                        if o > bo:
                            bo, bt = o, th
                    return bt
                th = best(np.ones(len(y), bool))
                ins = ((~y & (v >= th)).sum(), (y & (v >= th)).sum())
                lw = lr = 0
                for g in sorted(set(game)):
                    tg = best(game != g)
                    te = game == g
                    lw += (~y[te] & (v[te] >= tg)).sum()
                    lr += (y[te] & (v[te] >= tg)).sum()
                if ins[0] >= 3:
                    print('  %-7s %s %8.2f: in-sample -%d wrong / -%d right | leave-one-game-out -%d wrong / -%d right' % (
                        k, '>=' if sgn > 0 else '<=', sgn * th, ins[0], ins[1], lw, lr))


if __name__ == '__main__':
    main()
