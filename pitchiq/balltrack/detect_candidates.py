"""The detector, run for real over an arbitrary window, at the shipping speed.

reference/single_process_run.py is the reference implementation and it is single process, writes
once at the very end, and reads its range from module constants.  None of
those are usable for a production pass:

  * one process is 1.4 fps, so five minutes of play is 1.8 hours;
  * a single write at the end means a crash at minute 90 leaves nothing;
  * LO/HI are not to be edited.

So this is single_process_run.main's loop, unchanged in what it computes, wrapped in the
operating point dpar8 confirmed - 16 processes x 1 thread, blob_candidates.blobs and
the pan_registration registrar with the batched GPU matcher - and cut into contiguous
chunks of anchors, one per worker.  LO/HI are never touched: the window is
an argument and each worker carries its own.

Two things are different from single_process_run.main, and only two:

  * each worker flushes a part file every FLUSH anchors, so the work that
    survives a crash is bounded by FLUSH frames rather than by the run;
  * every anchor also keeps mats[CZERO + 1], the affine that maps the next
    frame onto this one.  pan.py computes exactly that affine, with the same
    fit_pair on the same ORB features, as a separate 9000-frame pass.  It is
    already sitting in the buffer here, so the stabilised coordinates the
    tracker needs come out of this run for free instead of costing another
    four minutes.

Everything else - the blob pass, the feature rows, the two boosters, the
top-KEEP write - is single_process_run's, and blob_candidates/pan_registration were checked bit-identical
against it before any of this.

The window is also cut into 5-minute chunks (CHUNK frames, on absolute
boundaries).  When a chunk's last worker returns, <tag>_k<chunk>.json records
its part files; a rerun of the same command skips every chunk with a complete
manifest, so a crash or a reboot costs at most the chunks in flight.

    python -m pitchiq.balltrack.detect_candidates <lo> <hi> [procs] [tag]
"""

import io
import json
import multiprocessing as mp
import os
import sys
import time

from . import config

S2 = os.path.dirname(os.path.abspath(__file__))
FLUSH = 250  # anchors between part files
CHUNK = int(os.environ.get('DRUN_CHUNK', 9000))  # 5 min of video: a finished chunk is never redone
CPU_MATCH = os.environ.get('DRUN_CPU_MATCH') == '1'  # match on the CPU: no torch, no CUDA context
SHARED = (
    os.environ.get('DRUN_SHARED_MATCH', '1') == '1'
)  # one GPU matcher for the pool (shared_matcher.py); '0' = one per worker
PROCS = config.WORKERS


def worker(args):
    wid, alo, ahi, tag = args  # tag here is '<tag>_k<chunk>', so part names never collide
    for v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[v] = '1'
    import cv2
    import numpy as np
    import xgboost as xgb

    from . import blob_candidates
    from . import candidate_features
    from . import temporal_features
    from . import pan_registration

    cv2.setNumThreads(1)

    log = io.open(os.path.join(S2, '%s_w%02d.log' % (tag, wid)), 'w', encoding='utf-8')

    def say(m):
        log.write(m + '\n')
        log.flush()

    # CPU_MATCH workers leave the GPU (and torch) alone: bf.match is the
    # reference the batched matcher reproduces exactly, so the parts are the
    # same; the worker just commits no CUDA context, host or device.
    M = None
    if SHARED:
        from . import shared_matcher

        M = shared_matcher.proxy()
    elif not CPU_MATCH:
        from . import gpu_matcher
        import torch

        torch.set_num_threads(1)
        M = gpu_matcher.Matcher()
    reg = pan_registration.Registrar(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True), M)
    if M is not None and not SHARED:
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

    first, last = alo + candidate_features.OFFS[0], ahi + candidate_features.OFFS[-1]
    cap = cv2.VideoCapture(config.VIDEO)
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
            f=np.array(FI, np.int32),
            x=np.array(CX, np.float32),
            y=np.array(CY, np.float32),
            s=np.array(SC, np.float32),
            frames=fr,
            ncand=np.array([NC[int(i)] for i in fr], np.int32),
            how=np.array([HOW.get(int(i), [0] * candidate_features.NOFF) for i in fr], np.int8),
            hstep=np.array([HST.get(int(i), np.eye(3)) for i in fr], np.float64),
        )
        parts.append(name)
        say('  flushed %s  %d frames' % (name, len(fr)))

    FI, CX, CY, SC = [], [], [], []
    NC, HOW, HST = {}, {}, {}
    bgrs, kds, fids = [], [], []
    small = {}  # frame id -> its tile-size resize, shared by its 21 anchors
    done = 0
    t0 = time.time()
    # seconds spent in each step, summed over the worker's anchors
    PT = dict.fromkeys(
        ('read', 'orb', 'blobs', 'still', 'register', 'tiles', 'temporal', 'rank'), 0.0
    )
    clock = time.perf_counter
    for ab in range(first, last + 1):
        t = clock()
        ok, img = cap.read()
        if not ok:
            say('  read failed at %d - stopping' % ab)
            break
        t1 = clock()
        PT['read'] += t1 - t
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        kds.append(reg_detect(cv2, g))
        PT['orb'] += clock() - t1
        bgrs.append(img)
        fids.append(ab)
        if len(bgrs) > candidate_features.NOFF:
            bgrs.pop(0)
            kds.pop(0)
            fids.pop(0)
        if len(bgrs) < candidate_features.NOFF:
            continue

        anchor = ab + candidate_features.OFFS[0]
        t = clock()
        bs, _ = blob_candidates.blobs(bgrs[candidate_features.CZERO], cmaj)
        t1 = clock()
        PT['blobs'] += t1 - t
        NC[anchor] = len(bs)
        if bs:
            Xs, pts = candidate_features.still_rows(bs, cmaj, cara)
            t = clock()
            PT['still'] += t - t1
            mats, how = reg.transforms(kds, fids, candidate_features.CZERO)
            t1 = clock()
            PT['register'] += t1 - t
            HOW[anchor] = how
            if mats[candidate_features.CZERO + 1] is not None:
                HST[anchor] = mats[candidate_features.CZERO + 1]
            tiles = candidate_features.tiles_for(bgrs, mats, fids, small)
            t = clock()
            PT['tiles'] += t - t1
            Tt = temporal_features.temporal_rows(tiles, pts)
            t1 = clock()
            PT['temporal'] += t1 - t
            s2 = bst.predict(xgb.DMatrix(np.hstack([Xs, Tt])))
            PT['rank'] += clock() - t1
            for j in np.argsort(-s2)[: candidate_features.KEEP]:
                FI.append(anchor)
                CX.append(float(pts[j][0]))
                CY.append(float(pts[j][1]))
                SC.append(float(s2[j]))
        reg.evict(fids[0])
        small.pop(fids[0], None)
        done += 1
        if done % FLUSH == 0:
            flush(FI, CX, CY, SC, NC, HOW, HST)
            FI, CX, CY, SC = [], [], [], []
            NC, HOW, HST = {}, {}, {}
            say(
                '  %d/%d  %.0fs  %.2f fps'
                % (done, ahi - alo + 1, time.time() - t0, done / (time.time() - t0))
            )
    cap.release()
    flush(FI, CX, CY, SC, NC, HOW, HST)
    el = time.time() - t0
    say(
        'worker %d done: %d anchors, %.1fs, %.2f fps, %d cached / %d batched'
        % (wid, done, el, done / el if el else 0.0, reg.hits, reg.batched)
    )
    say('  seconds by step: ' + '  '.join('%s %.1f' % kv for kv in PT.items()))
    log.close()
    return dict(
        wid=wid,
        tag=tag,
        lo=alo,
        hi=ahi,
        done=done,
        secs=el,
        parts=parts,
        prof=PT,
        cached=reg.hits,
        batched=reg.batched,
        misses=reg.misses,
    )


def reg_detect(cv2, g):
    """ORB exactly as reference/single_process_run.py creates it, one detector per process."""
    global _ORB
    try:
        orb = _ORB
    except NameError:
        orb = _ORB = cv2.ORB_create(2000)
    return orb.detectAndCompute(g, None)


def chunk_edges(lo, hi):
    """[lo, hi] cut into CHUNK-frame pieces, aligned to absolute multiples of
    CHUNK so the same frames make the same chunk whatever window asks."""
    out, a = [], lo
    while a <= hi:
        b = min(hi, (a // CHUNK + 1) * CHUNK - 1)
        out.append((a, b))
        a = b + 1
    return out


def _manifest(ctag):
    return os.path.join(S2, '%s.json' % ctag)


def save_chunk(ctag, res):
    path = _manifest(ctag)
    io.open(path + '.part', 'w', encoding='utf-8').write(json.dumps(res))
    os.replace(path + '.part', path)


def load_chunk(tag, c, clo, chi):
    """A finished chunk's worker results, or None.  Only a manifest that covers
    exactly this chunk, with every part file present, counts as finished."""
    path = _manifest('%s_k%03d' % (tag, c))
    if not os.path.exists(path):
        return None
    res = json.loads(io.open(path, encoding='utf-8').read())
    lo = min(r['lo'] for r in res)
    hi = max(r['hi'] for r in res)
    if (lo, hi) != (clo, chi) or not all(
        os.path.exists(os.path.join(S2, p)) for r in res for p in r['parts']
    ):
        return None
    return res


def merge(tag, lo, hi, res):
    import numpy as np

    parts = []
    for r in sorted(res, key=lambda r: (r['lo'], r['wid'])):
        parts.extend(r['parts'])
    F, X, Y, S, FR, NCn, HW, HS = [], [], [], [], [], [], [], []
    # Parts are kept to lo..hi, so a window cut from finished chunks (half.py's
    # pieces) is exactly what detect_candidates over that window would have written.
    for name in parts:
        z = np.load(os.path.join(S2, name))
        m = (z['f'] >= lo) & (z['f'] <= hi)
        k = (z['frames'] >= lo) & (z['frames'] <= hi)
        F.append(z['f'][m])
        X.append(z['x'][m])
        Y.append(z['y'][m])
        S.append(z['s'][m])
        FR.append(z['frames'][k])
        NCn.append(z['ncand'][k])
        HW.append(z['how'][k])
        HS.append(z['hstep'][k])
    fr = np.concatenate(FR)
    o = np.argsort(fr, kind='stable')
    fr = fr[o]
    assert len(np.unique(fr)) == len(fr), 'an anchor was detected twice'
    dense = os.path.join(S2, '%s.npz' % tag)
    np.savez_compressed(
        dense,
        f=np.concatenate(F),
        x=np.concatenate(X),
        y=np.concatenate(Y),
        s=np.concatenate(S),
        frames=fr,
        ncand=np.concatenate(NCn)[o],
        how=np.concatenate(HW)[o],
        lo=lo,
        hi=hi,
        keep=40,
        stage1=10**6,
    )

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
    np.savez_compressed(os.path.join(S2, '%span.npz' % tag), cum=cum, step=step, lo=lo, hi=hi)
    return dense, fr, np.concatenate(NCn)[o], step, nfail


def main():
    lo = int(sys.argv[1])
    hi = int(sys.argv[2])
    procs = int(sys.argv[3]) if len(sys.argv) > 3 else PROCS
    tag = sys.argv[4] if len(sys.argv) > 4 else 'drun'

    n = hi - lo + 1
    chunks = chunk_edges(lo, hi)
    todo, res = [], []
    for clo, chi in chunks:
        c = clo // CHUNK  # the absolute 5-minute slot: same frames, same name
        done = load_chunk(tag, c, clo, chi)
        if done is not None:
            res.extend(done)
            continue
        m = chi - clo + 1
        edges = [clo + (m * i) // procs for i in range(procs)] + [chi + 1]
        todo += [
            (i, edges[i], edges[i + 1] - 1, '%s_k%03d' % (tag, c))
            for i in range(procs)
            if edges[i + 1] > edges[i]
        ]
    print('DETECTOR over abs %d..%d  (%d frames, %.1f s of video)' % (lo, hi, n, n / 30.0))
    print(
        '  %d processes x 1 thread, blob_candidates.blobs + pan_registration/gpu_matcher registrar'
        % procs
    )
    print(
        '  %d chunks of <= %d frames, %d already done; part file every %d'
        % (len(chunks), CHUNK, len(chunks) - len({j[3] for j in todo}), FLUSH)
    )
    sys.stdout.flush()

    # One pool for every chunk; a chunk's manifest is written the moment its
    # last worker returns, so a crash or reboot loses at most the chunks in
    # flight.  Workers split anchors with their own lookahead frames, so the
    # output does not depend on where the chunk or worker edges fall.
    t0 = time.time()
    pending = {}
    for j in todo:
        pending.setdefault(j[3], []).append(j)
    left = {k: len(v) for k, v in pending.items()}
    got_c = {k: [] for k in pending}
    if todo:
        if SHARED:
            from . import shared_matcher

            server, req, init = shared_matcher.start(procs)
            pool_args = dict(initializer=shared_matcher.init_worker, initargs=init)
        else:
            pool_args = {}
        with mp.Pool(procs, **pool_args) as pool:
            for r in pool.imap_unordered(worker, todo):
                ct = r['tag']
                got_c[ct].append(r)
                left[ct] -= 1
                print(
                    '  %s worker %2d  %d anchors  %6.1fs  %5.2f fps'
                    % (
                        ct,
                        r['wid'],
                        r['done'],
                        r['secs'],
                        r['done'] / r['secs'] if r['secs'] else 0.0,
                    ),
                    flush=True,
                )
                if not left[ct]:
                    save_chunk(ct, got_c[ct])
                    res.extend(got_c[ct])
                    print('  chunk %s done  [%.0f s]' % (ct, time.time() - t0), flush=True)
        if SHARED:
            shared_matcher.stop(server, req)
    wall = max(time.time() - t0, 1e-9)

    got = sum(r['done'] for r in res)
    dense, fr, nc, step, nfail = merge(tag, lo, hi, res)
    import numpy as np

    print()
    print('WALL CLOCK  %.1f s  (%.2f min) for %d frames' % (wall, wall / 60.0, got))
    print('  %.2f frames/s   %.1f ms/frame' % (got / wall, 1000.0 * wall / got))
    print(
        '  the video itself is %.1f s long, so this ran at %.2fx real time'
        % (n / 30.0, (n / 30.0) / wall)
    )
    print()
    print('DETECTED  %d of %d anchors' % (len(fr), n))
    print('  candidates/frame  mean %.0f  min %d  max %d' % (nc.mean(), nc.min(), nc.max()))
    print('  frames with no candidate at all  %d' % int((nc == 0).sum()))
    prof = {}
    for r in res:
        for k, v in r.get('prof', {}).items():
            prof[k] = prof.get(k, 0.0) + v
    if prof and got:
        print('WORKER TIME by step, ms per anchor (one process):')
        print(
            '  '
            + '  '.join('%s %.0f' % (k, 1000.0 * v / got) for k, v in prof.items())
            + '  | total %.0f' % (1000.0 * sum(prof.values()) / got)
        )
    s = step[np.isfinite(step)]
    print('CAMERA between consecutive frames, source px at the centre')
    print(
        '  median %.1f  p90 %.1f  p99 %.1f  max %.1f   (%d steps not fit)'
        % (np.median(s), np.percentile(s, 90), np.percentile(s, 99), s.max(), nfail)
    )
    io.open(os.path.join(S2, '%s_timing.json' % tag), 'w', encoding='utf-8').write(
        json.dumps(
            dict(
                lo=lo,
                hi=hi,
                frames=n,
                detected=int(len(fr)),
                procs=procs,
                wall_s=wall,
                fps=got / wall,
                realtime=(n / 30.0) / wall,
                workers=[{k: v for k, v in r.items() if k != 'parts'} for r in res],
            )
        )
    )
    print('saved %s, %span.npz, %s_timing.json' % (dense, tag, tag))


if __name__ == '__main__':
    mp.freeze_support()
    main()
