"""Replay the 2D ball chain on the stored dev pieces with a chosen set of classifier scores, for several
tracker variants, and score every dev Ball Clicker answer.  Read-only (writes replay_<cls>.json here).

The chain replayed: render_review_video.picks_for (track_ball + gap_fill) per piece, pieces stitched at
the frame furthest from both edges, then match_video's filters on the 2D picks (short_runs, ballgate.lost
with the same scores).  Not replayed: TAPNext bridges (stale on these passes anyway) and the 3D flight
(air rings), so the counts are a 2D proxy of clickscore's, the same for every variant.

    python replay.py <cls dir | shipped> [variant ...]
    RP_GATE=base  every variant gets the base chain's ballgate stretches (else each its own)
    RP_TAGS=a,b   only these halves
"""
import glob
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402

BT, SCR = common.BT, common.SCR
OUT = os.environ.get('RP_OUT') or os.path.join(common.OUT, 'tracker', 'replay')
DEV = os.environ.get('RP_TAGS', 'nb1,ww1,ww2,eb1,eb2,scrimh1,scrimh2,wh2,wb1,pi2,co2').split(',')
CLS = None


def attach(F, pt, d):
    z = np.load(os.path.join(d, '%scls.npz' % pt))
    p = {}
    for f, j, v in zip(z['f'].tolist(), z['j'].tolist(), z['p'].tolist()):
        p[(f, j)] = v
    for fr in F:
        fr['p'] = np.array([p[(fr['abs'], j)] for j in range(len(fr['s']))])
        fr['s0'] = fr['s'].copy()


def piece(job):
    pt, d, names = job
    import variants as V

    common.point_at_data()
    from pitchiq.balltrack import ball_tracker, gap_fill

    os.chdir(BT)
    t0 = time.time()
    F = ball_tracker.load_frames(pt)
    attach(F, pt, d)
    gap_fill.attach_pan(F, pt)
    ab = np.array([fr['abs'] for fr in F])
    from pitchiq.balltrack import candidate_crops
    ppl = candidate_crops.load_ppl(pt) or {}

    def feet(i, j):
        """Distance from the pick to the nearest player's feet, in that player's heights; 1 if inside a box."""
        bx = ppl.get(int(F[i]['abs']))
        if j is None or bx is None or not len(bx):
            return np.nan, np.nan
        x, y = F[i]['x'][j], F[i]['y'][j]
        h = np.maximum(1.0, bx[:, 3] - bx[:, 1])
        d = np.hypot(x - (bx[:, 0] + bx[:, 2]) / 2, y - bx[:, 3]) / h
        ins = (x >= bx[:, 0]) & (x <= bx[:, 2]) & (y >= bx[:, 1]) & (y <= bx[:, 3])
        return float(d.min()), float(ins.any())
    out = {}
    for nm in names:
        o, s = V.run(nm, F)
        fl = F[0].pop('_full', None)
        if fl is not None:
            out[nm + '#full'] = (int(fl.sum()), len(fl))
        # frames that count as ringed for the short-run rule though the variant draws nothing there
        out[nm + '#cont'] = F[0].pop('_cont', None)
        syn = [isinstance(j, tuple) for j in o]  # synthetic picks (kin variants): ('syn', x, y) in source px
        x = np.array([np.nan if j is None else gap_fill.xy(F[i], j)[0] for i, j in enumerate(o)])
        y = np.array([np.nan if j is None else gap_fill.xy(F[i], j)[1] for i, j in enumerate(o)])
        o = [None if k else j for j, k in zip(o, syn)]
        q = np.array([np.nan if j is None else F[i]['p'][j] for i, j in enumerate(o)])
        sx = np.array([np.nan if j is None else F[i]['sx'][j] for i, j in enumerate(o)])
        sy = np.array([np.nan if j is None else F[i]['sy'][j] for i, j in enumerate(o)])
        s0 = np.array([np.nan if j is None else F[i]['s0'][j] for i, j in enumerate(o)])
        ft = np.array([feet(i, j) for i, j in enumerate(o)]).reshape(-1, 2)
        out[nm] = (x, y, s, q, sx, sy, s0, ft[:, 0], ft[:, 1])
    best = np.array([fr['p'].max() if len(fr['p']) else -np.inf for fr in F])
    return pt, ab, out, best, time.time() - t0


def stitch(tag, res):
    """Per abs frame of the half: from the piece in which the frame is furthest from both edges."""
    lo = min(r[1][0] for r in res)
    hi = max(r[1][-1] for r in res)
    n = hi - lo + 1
    edge = np.full(n, -1)
    sel = {}
    for k, (pt, ab, out, best, _) in enumerate(res):
        e = np.minimum(ab - ab[0], ab[-1] - ab)
        i = ab - lo
        take = e > edge[i]
        edge[i[take]] = e[take]
        sel[k] = (i, take)
    owner = np.full(n, -1)
    for k, (i, take) in sel.items():
        owner[i[take]] = k
    return lo, n, owner


def main():
    d = sys.argv[1]
    names = sys.argv[2:] or ['base']
    if os.environ.get('RP_GATE') == 'base' and 'base' not in names:
        names = ['base'] + names
    cdir = BT if d == 'shipped' else (d if os.path.isabs(d) else os.path.join(SCR, d))
    common.point_at_data()
    from pitchiq.balltrack import ballgate
    from pitchiq.experiments.clicker import clickscore as cs

    if os.environ.get('RP_LOSTP'):  # the gate's bar on the rolling median score (shipped 1.5); -99 = no gate
        ballgate.LOST_P = float(os.environ['RP_LOSTP'])

    items = {it['id']: it for it in json.load(open(os.path.join(cs.ROOT, 'items_all.json')))['items']}
    A = cs.answers(os.path.join(cs.ROOT, 'db'))
    jobs = []
    for tag in DEV:
        for pp in sorted(glob.glob(os.path.join(BT, '%s_q[0-9][0-9][0-9].npz' % tag))):
            pt = os.path.basename(pp)[:-4]
            if os.path.exists(os.path.join(cdir, '%scls.npz' % pt)):
                jobs.append((pt, cdir, names))
    t0 = time.time()
    with Pool(int(os.environ.get('RP_PROCS', '6'))) as pool:
        res = pool.map(piece, jobs, chunksize=1)
    print('replayed %d pieces in %.0f s (%.0f s per piece)' % (len(res), time.time() - t0, np.mean([r[4] for r in res])), flush=True)
    tot = {nm: dict(right=0, wrong_ball=0, missed=0, out_ring=0, ball=0, out=0) for nm in names}
    per = {nm: {} for nm in names}
    for tag in DEV:
        R = [r for r in res if r[0].startswith(tag + '_q')]
        if not R:
            continue
        lo, n, owner = stitch(tag, R)
        bp = {}
        for pt, ab, out, best, _ in R:
            for f, v in zip(ab.tolist(), best.tolist()):
                bp[f] = max(v, bp.get(f, -np.inf))
        os.makedirs(os.path.join(OUT, 'replay_cache'), exist_ok=True)
        kk = np.array(sorted(bp))
        np.savez(os.path.join(OUT, 'replay_cache', 'bp_%s_%s.npz' % (os.path.basename(d.rstrip('/')), tag)),
                 f=kk, p=np.array([bp[k] for k in kk.tolist()]))
        ballgate.best_p = lambda t, bp=bp: bp
        want = [(i, a) for i, a in A.items() if items.get(i, {}).get('half') == tag and a['v'] in ('ball', 'out')]
        gate0 = None
        for nm in (['base'] if os.environ.get('RP_GATE') == 'base' else []) + names:
            X = np.full(n, np.nan)
            Y = X.copy()
            S = np.zeros(n, np.int8)
            Q, SX, SY, S0, FD, IN = (X.copy() for _ in range(6))
            CT = np.zeros(n, bool)
            for k, (pt, ab, out, best, _) in enumerate(R):
                i = ab - lo
                m = owner[i] == k
                x, y, s, q, sx, sy, s0, fd, inb = out[nm]
                X[i[m]], Y[i[m]], S[i[m]], Q[i[m]] = x[m], y[m], s[m], q[m]
                if out.get(nm + '#cont') is not None:
                    CT[i[m]] = out[nm + '#cont'][m]
                # stabilised coordinates are per piece: kept for speeds within a piece only
                SX[i[m]], SY[i[m]], S0[i[m]], FD[i[m]], IN[i[m]] = sx[m], sy[m], s0[m], fd[m], inb[m]
            os.makedirs(os.path.join(OUT, 'replay_cache'), exist_ok=True)
            np.savez(os.path.join(OUT, 'replay_cache', '%s_%s_%s.npz' % (os.path.basename(d.rstrip('/')), nm.replace(':', '_'), tag)),
                     lo=lo, x=X, y=Y, src=S, p=Q, sx=SX, sy=SY, s0=S0, feet=FD, inbox=IN, owner=owner, cont=CT)
            on = (S > 0) & np.isfinite(X)
            g = ballgate.lost(tag, lo, on) if gate0 is None else gate0
            if gate0 is None and os.environ.get('RP_GATE') == 'base':
                gate0 = g  # the first pass: base's gate, then held for every variant
                continue
            flash = ballgate.short_runs(on | CT) | g
            c = tot[nm]
            for iid, a in want:
                f = int(items[iid]['f'])
                i = f - lo
                ring = bool(0 <= i < n and on[i] and not flash[i])
                p = (X[i], Y[i]) if ring else None
                chk = cs.judge(a, p, 'ground' if ring else '')
                c[a['v']] += 1
                if a['v'] == 'ball':
                    c['right'] += chk[1]
                    c['wrong_ball'] += ring and not chk[1]
                    c['missed'] += not ring
                else:
                    c['out_ring'] += ring
                dd = float(np.hypot(X[i] - a['x'], Y[i] - a['y'])) if (a['v'] == 'ball' and 0 <= i < n and np.isfinite(X[i])) else None
                per[nm][iid] = dict(half=tag, v=a['v'], ring=ring, right=bool(chk[1]), src=int(S[i]) if 0 <= i < n else 0, d=dd)
            c.setdefault('on_frames', 0)
            c['on_frames'] += int((on & ~flash).sum())
    for nm in names:
        fl = [r[2][nm + '#full'] for r in res if nm + '#full' in r[2]]
        if fl:
            tot[nm]['full_share'] = sum(a for a, b in fl) / max(1, sum(b for a, b in fl))
            print('%-28s full-frame searches: %.1f%% of candidate frames' % (nm, 100 * tot[nm]['full_share']))
    for nm in names:
        c = tot[nm]
        print('%-28s right %3d  wrong on ball %3d  missed %3d  | out rings %2d/%d  | wrong total %3d  | ringed frames %d' % (
            nm, c['right'], c['wrong_ball'], c['missed'], c['out_ring'], c['out'], c['wrong_ball'] + c['out_ring'], c['on_frames']), flush=True)
    json.dump(dict(tot=tot, per=per), open(os.path.join(OUT, 'replay_%s.json' % os.path.basename(d.rstrip('/'))), 'w'))


if __name__ == '__main__':
    main()
