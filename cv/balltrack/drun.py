"""The detector, run for real over an arbitrary window, at the shipping speed.

dense.py is the reference implementation and it is single process, writes
once at the very end, and reads its range from module constants.  None of
those are usable for a production pass:

  * one process is 1.4 fps, so five minutes of play is 1.8 hours;
  * a single write at the end means a crash at minute 90 leaves nothing;
  * LO/HI are not to be edited.

So this is dense.main's loop, unchanged in what it computes, wrapped in the
operating point dpar8 confirmed - 16 processes x 1 thread, bfast2 blobs and
the rfast2 registrar with the batched GPU matcher - and cut into contiguous
chunks of anchors, one per worker.  LO/HI are never touched: the window is
an argument and each worker carries its own.

Two things are different from dense.main, and only two:

  * each worker flushes a part file every FLUSH anchors, so the work that
    survives a crash is bounded by FLUSH frames rather than by the run;
  * every anchor also keeps mats[CZERO + 1], the affine that maps the next
    frame onto this one.  pan.py computes exactly that affine, with the same
    fit_pair on the same ORB features, as a separate 9000-frame pass.  It is
    already sitting in the buffer here, so the stabilised coordinates the
    tracker needs come out of this run for free instead of costing another
    four minutes.

Everything else - the blob pass, the feature rows, the two boosters, the
top-KEEP write - is dense's, and bfast2/rfast2 were checked bit-identical
against it before any of this.

    python drun.py <lo> <hi> [procs] [tag]
"""
import io
import json
import multiprocessing as mp
import os
import sys
import time

S2 = os.path.dirname(os.path.abspath(__file__))
FLUSH = 250              # anchors between part files
PROCS = 16               # dpar8's confirmed point: 29.07 fps, 6607 MiB peak


def worker(args):
    wid, alo, ahi, tag = args
    for v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[v] = '1'
    import cv2
    import numpy as np
    import xgboost as xgb
    sys.path.insert(0, S2)
    import bfast2
    import dense as D
    import tfast
    import rfast2
    import gmatch2
    import torch

    cv2.setNumThreads(1)
    torch.set_num_threads(1)
    D.LO, D.HI = alo, ahi          # set at runtime; dense.py is not edited

    log = io.open(os.path.join(S2, '%s_w%02d.log' % (tag, wid)), 'w',
                  encoding='utf-8')

    def say(m):
        log.write(m + '\n')
        log.flush()

    M = gmatch2.Matcher()
    reg = rfast2.Registrar(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True), M)
    # burn the CUDA context in before the clock starts, on junk descriptors,
    # so the first real anchor is not paying for cuBLAS handle creation
    d0 = np.random.randint(0, 256, (2000, 32), dtype=np.uint8)
    d1 = np.random.randint(0, 256, (2000, 32), dtype=np.uint8)
    M.match_many([(-2, d0)], -1, d1)
    torch.cuda.synchronize()
    M._bits.clear()
    M._pop.clear()
    M.calls = M.pairs = 0

    z = np.load(os.path.join(S2, 'still4.npz'), allow_pickle=True)
    cmaj, cara = z['cmaj'], z['cara']
    bst = xgb.Booster()
    bst.load_model(os.path.join(S2, 'rank6.model.json'))

    first, last = alo + D.OFFS[0], ahi + D.OFFS[-1]
    cap = cv2.VideoCapture(D.VIDEO)
    assert cap.isOpened(), 'cannot open video'
    cap.set(cv2.CAP_PROP_POS_FRAMES, first)

    parts = []

    def flush(FI, CX, CY, SC, NC, HOW, HST):
        if not NC:
            return
        fr = np.array(sorted(NC), np.int32)
        name = '%s_w%02d_p%03d.npz' % (tag, wid, len(parts))
        np.savez_compressed(
            os.path.join(S2, name),
            f=np.array(FI, np.int32), x=np.array(CX, np.float32),
            y=np.array(CY, np.float32), s=np.array(SC, np.float32),
            frames=fr, ncand=np.array([NC[int(i)] for i in fr], np.int32),
            how=np.array([HOW.get(int(i), [0] * D.NOFF) for i in fr], np.int8),
            hstep=np.array([HST.get(int(i), np.eye(3)) for i in fr],
                           np.float64))
        parts.append(name)
        say('  flushed %s  %d frames' % (name, len(fr)))

    FI, CX, CY, SC = [], [], [], []
    NC, HOW, HST = {}, {}, {}
    bgrs, kds, fids = [], [], []
    done = 0
    t0 = time.time()
    for ab in range(first, last + 1):
        ok, img = cap.read()
        if not ok:
            say('  read failed at %d - stopping' % ab)
            break
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        kds.append(reg_detect(cv2, g))
        bgrs.append(img)
        fids.append(ab)
        if len(bgrs) > D.NOFF:
            bgrs.pop(0)
            kds.pop(0)
            fids.pop(0)
        if len(bgrs) < D.NOFF:
            continue

        anchor = ab + D.OFFS[0]
        bs, _ = bfast2.blobs(bgrs[D.CZERO], cmaj)
        NC[anchor] = len(bs)
        if bs:
            Xs, pts = D.still_rows(bs, cmaj, cara)
            mats, how = reg.transforms(kds, fids, D.CZERO)
            HOW[anchor] = how
            if mats[D.CZERO + 1] is not None:
                HST[anchor] = mats[D.CZERO + 1]
            tiles = D.tiles_for(bgrs, mats)
            Tt = tfast.temporal_rows(tiles, pts)
            s2 = bst.predict(xgb.DMatrix(np.hstack([Xs, Tt])))
            for j in np.argsort(-s2)[:D.KEEP]:
                FI.append(anchor)
                CX.append(float(pts[j][0]))
                CY.append(float(pts[j][1]))
                SC.append(float(s2[j]))
        reg.evict(fids[0])
        done += 1
        if done % FLUSH == 0:
            flush(FI, CX, CY, SC, NC, HOW, HST)
            FI, CX, CY, SC = [], [], [], []
            NC, HOW, HST = {}, {}, {}
            say('  %d/%d  %.0fs  %.2f fps'
                % (done, ahi - alo + 1, time.time() - t0,
                   done / (time.time() - t0)))
    cap.release()
    flush(FI, CX, CY, SC, NC, HOW, HST)
    el = time.time() - t0
    say('worker %d done: %d anchors, %.1fs, %.2f fps, %d cached / %d batched'
        % (wid, done, el, done / el if el else 0.0, reg.hits, reg.batched))
    log.close()
    return dict(wid=wid, lo=alo, hi=ahi, done=done, secs=el, parts=parts,
                cached=reg.hits, batched=reg.batched, misses=reg.misses)


def reg_detect(cv2, g):
    """ORB exactly as dense.py:283 creates it, one detector per process."""
    global _ORB
    try:
        orb = _ORB
    except NameError:
        orb = _ORB = cv2.ORB_create(2000)
    return orb.detectAndCompute(g, None)


def merge(tag, lo, hi, res):
    import numpy as np
    parts = []
    for r in sorted(res, key=lambda r: r['wid']):
        parts.extend(r['parts'])
    F, X, Y, S, FR, NCn, HW, HS = [], [], [], [], [], [], [], []
    for name in parts:
        z = np.load(os.path.join(S2, name))
        F.append(z['f'])
        X.append(z['x'])
        Y.append(z['y'])
        S.append(z['s'])
        FR.append(z['frames'])
        NCn.append(z['ncand'])
        HW.append(z['how'])
        HS.append(z['hstep'])
    fr = np.concatenate(FR)
    o = np.argsort(fr, kind='stable')
    fr = fr[o]
    assert len(np.unique(fr)) == len(fr), 'an anchor was detected twice'
    dense = os.path.join(S2, '%s.npz' % tag)
    np.savez_compressed(dense,
                        f=np.concatenate(F), x=np.concatenate(X),
                        y=np.concatenate(Y), s=np.concatenate(S),
                        frames=fr, ncand=np.concatenate(NCn)[o],
                        how=np.concatenate(HW)[o],
                        lo=lo, hi=hi, keep=40, stage1=10 ** 6)

    # pan.npz's own shape: cum[k] maps frame lo+k onto frame lo.  pan.py
    # builds it as acc = acc.dot(H) over consecutive fits and carries the
    # last transform forward when a fit fails; an identity step is exactly
    # that carry-forward.
    hs = np.concatenate(HS)[o]
    n = hi - lo + 1
    cum = np.zeros((n, 3, 3), np.float64)
    step = np.full(n, np.nan)
    idx = {int(a): i for i, a in enumerate(fr)}
    acc = np.eye(3)
    nfail = 0
    for k in range(n):
        cum[k] = acc
        i = idx.get(lo + k)
        H = np.eye(3) if i is None else hs[i]
        if np.allclose(H, np.eye(3)):
            nfail += 1
        elif k + 1 < n:
            # pan.py's convention: step[k] is the motion INTO frame lo+k
            p = np.array([960.0, 540.0, 1.0]).dot(H[:2].T)
            step[k + 1] = float(np.hypot(p[0] - 960.0, p[1] - 540.0))
        acc = acc.dot(H)
    np.savez_compressed(os.path.join(S2, '%span.npz' % tag),
                        cum=cum, step=step, lo=lo, hi=hi)
    return dense, fr, np.concatenate(NCn)[o], step, nfail


def main():
    lo = int(sys.argv[1])
    hi = int(sys.argv[2])
    procs = int(sys.argv[3]) if len(sys.argv) > 3 else PROCS
    tag = sys.argv[4] if len(sys.argv) > 4 else 'drun'

    n = hi - lo + 1
    edges = [lo + (n * i) // procs for i in range(procs)] + [hi + 1]
    jobs = [(i, edges[i], edges[i + 1] - 1, tag) for i in range(procs)]
    print('DETECTOR over abs %d..%d  (%d frames, %.1f s of video)'
          % (lo, hi, n, n / 30.0))
    print('  %d processes x 1 thread, bfast2 blobs + rfast2/gmatch2 registrar'
          % procs)
    print('  chunks of %d-%d anchors, part file every %d'
          % (min(j[2] - j[1] + 1 for j in jobs),
             max(j[2] - j[1] + 1 for j in jobs), FLUSH))
    sys.stdout.flush()

    t0 = time.time()
    with mp.Pool(procs) as pool:
        res = []
        for r in pool.imap_unordered(worker, jobs):
            res.append(r)
            print('  worker %2d  %d anchors  %6.1fs  %5.2f fps   [%d/%d done]'
                  % (r['wid'], r['done'], r['secs'],
                     r['done'] / r['secs'] if r['secs'] else 0.0,
                     len(res), procs), flush=True)
    wall = time.time() - t0

    got = sum(r['done'] for r in res)
    dense, fr, nc, step, nfail = merge(tag, lo, hi, res)
    import numpy as np

    print()
    print('WALL CLOCK  %.1f s  (%.2f min) for %d frames' % (wall, wall / 60.0, got))
    print('  %.2f frames/s   %.1f ms/frame' % (got / wall, 1000.0 * wall / got))
    print('  the video itself is %.1f s long, so this ran at %.2fx real time'
          % (n / 30.0, (n / 30.0) / wall))
    print()
    print('DETECTED  %d of %d anchors' % (len(fr), n))
    print('  candidates/frame  mean %.0f  min %d  max %d'
          % (nc.mean(), nc.min(), nc.max()))
    print('  frames with no candidate at all  %d' % int((nc == 0).sum()))
    s = step[np.isfinite(step)]
    print('CAMERA between consecutive frames, source px at the centre')
    print('  median %.1f  p90 %.1f  p99 %.1f  max %.1f   (%d steps not fit)'
          % (np.median(s), np.percentile(s, 90), np.percentile(s, 99),
             s.max(), nfail))
    io.open(os.path.join(S2, '%s_timing.json' % tag), 'w',
            encoding='utf-8').write(json.dumps(dict(
                lo=lo, hi=hi, frames=n, detected=int(len(fr)), procs=procs,
                wall_s=wall, fps=got / wall, realtime=(n / 30.0) / wall,
                workers=[{k: v for k, v in r.items() if k != 'parts'}
                         for r in res])))
    print('saved %s, %span.npz, %s_timing.json' % (dense, tag, tag))


if __name__ == '__main__':
    mp.freeze_support()
    main()
