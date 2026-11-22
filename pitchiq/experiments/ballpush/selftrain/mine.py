"""The shipped 2D chain's picks on dev halves under chosen scores: pseudo-label material.

python mine.py oof            every half under its own out-of-fold scores (for the cleanliness check
                              against that half's clicks: the model never saw them)
python mine.py fold <game>    the halves of every OTHER game under fold_<game>'s scores (foldp/), so
                              nothing about <game> reaches the labels its fold trains on
-> selftrain/mined/<oof | no_<game>>/<half>.npz: per abs frame the pick's piece, candidate j, src
   (1 tracker, 2 gap fill), p, x, y, stabilised sx, sy, the gate (lost), short-run flag (flash)
"""

import os
import sys
import time
from multiprocessing import Pool

import numpy as np

import st

sys.path.insert(0, os.path.dirname(st.HERE))
FOLDP = os.environ.get('ST_FOLDP', 'foldp')  # round 2: foldp_<run>, with ST_PRE=r2no_
PRE = os.environ.get('ST_PRE', 'no_')


def write_scores(g, pts):
    """<piece>cls.npz for mining without game g: stored oof scores (all <= KEEP_P off the cache), the cached
    candidates rescored by fold_g."""
    d = st.OUT + '/minecls/%s%s' % (PRE, g)
    os.makedirs(d, exist_ok=True)
    gi = st.GAMES.index(g)
    for pt in pts:
        o = os.path.join(d, '%scls.npz' % pt)
        if os.path.exists(o):
            continue
        f, j, p = st.load_oof(pt)
        c = st.load_cache(pt, crops=False)
        P = np.load(st.OUT + '/%s/%s.npy' % (FOLDP, pt))[gi]
        p = p.copy()
        p[np.searchsorted(f * 64 + j, c['f'] * 64 + c['j'])] = P
        np.savez(o, f=f.astype(np.int32), j=j.astype(np.int16), p=p)
    return d


def piece(job):
    pt, d = job
    import replay
    import variants as V

    st.common.point_at_data()
    from pitchiq.balltrack import ball_tracker, gap_fill

    os.chdir(st.BT)
    F = ball_tracker.load_frames(pt)
    replay.attach(F, pt, d)
    gap_fill.attach_pan(F, pt)
    o, s = V.run('base', F)
    ab = np.array([fr['abs'] for fr in F])
    J = np.array([-1 if j is None else j for j in o])

    def g(k):
        return np.array([np.nan if j is None else F[i][k][j] for i, j in enumerate(o)])

    best = np.array([fr['p'].max() for fr in F])
    return pt, ab, J, s, g('p'), g('x'), g('y'), g('sx'), g('sy'), best


def run(name, d, tags):
    st.common.point_at_data()
    from pitchiq.balltrack import ballgate
    import replay

    out = st.OUT + '/mined/%s' % name
    os.makedirs(out, exist_ok=True)
    jobs = [(pt, d) for pt in st.pieces(tags)]
    t0 = time.time()
    with Pool(int(os.environ.get('RP_PROCS', '4'))) as pool:
        res = pool.map(piece, jobs, chunksize=1)
    for tag in tags:
        R = [r for r in res if r[0].startswith(tag + '_q')]
        lo, n, owner = replay.stitch(tag, [(r[0], r[1], None, None, None) for r in R])
        J = np.full(n, -1)
        S = np.zeros(n, np.int8)
        P, X, Y, SX, SY = (np.full(n, np.nan) for _ in range(5))
        bp = {}
        for k, (pt, ab, j, s, p, x, y, sx, sy, best) in enumerate(R):
            i = ab - lo
            m = owner[i] == k
            J[i[m]], S[i[m]], P[i[m]], X[i[m]], Y[i[m]], SX[i[m]], SY[i[m]] = (
                j[m],
                s[m],
                p[m],
                x[m],
                y[m],
                sx[m],
                sy[m],
            )
            for f, v in zip(ab.tolist(), best.tolist()):
                bp[f] = max(v, bp.get(f, -np.inf))
        ballgate.best_p = lambda t, bp=bp: bp
        on = (S > 0) & np.isfinite(X)
        lost = ballgate.lost(tag, lo, on)
        flash = ballgate.short_runs(on)
        np.savez(
            os.path.join(out, '%s.npz' % tag),
            lo=lo,
            owner=owner,
            pieces=np.array([r[0] for r in R]),
            j=J,
            src=S,
            p=P,
            x=X,
            y=Y,
            sx=SX,
            sy=SY,
            lost=lost,
            flash=flash,
        )
        print(
            '%s  %s  %d frames  ringed %d  gate-blanked %d  flash %d'
            % (name, tag, n, (on & ~lost & ~flash).sum(), (on & lost).sum(), (on & flash).sum()),
            flush=True,
        )
    print('mined %s in %.0f s' % (name, time.time() - t0), flush=True)


def main():
    if sys.argv[1] == 'oof':
        # ST_MINE_NAME / ST_MINE_DIR: mine under another run's out-of-fold scores (round 2 own labels)
        run(os.environ.get('ST_MINE_NAME', 'oof'), os.environ.get('ST_MINE_DIR', st.OOF), st.DEV)
    else:
        for g in sys.argv[2:]:
            tags = [t for t in st.DEV if st.game(t) != g]
            run('%s%s' % (PRE, g), write_scores(g, st.pieces(tags)), tags)


if __name__ == '__main__':
    main()
