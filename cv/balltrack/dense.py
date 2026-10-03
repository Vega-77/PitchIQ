"""The detector run on EVERY frame, because that is what continuity needs.

Alex's instruction was: a frame we are sure about tells us where the ball is
in the next frame, because it must be close.  That is right, and the reason it
has not paid off yet is arithmetic, not principle.  The labelled test frames
are a median 23 frames apart:

    23 frames / 30 fps = 0.77 s;  at 1100 px/s the ball may travel 843 px

843 px is most of the frame width, so "it must be close" has been constraining
almost nothing.  Between CONSECUTIVE frames the same speed gate allows 37 px.
Twenty-three times tighter.  So track.py's Viterbi is not underpowered - it
has been asked to bridge gaps no continuity prior can bridge, and every gain
it shows (19 -> 28 of 47) was won against that handicap.

This runs the generator and the ranker over the whole test stretch, every
frame, and hands dtrack.py a dense candidate stream.

    abs 186600 .. 188275   1676 frames   55.8 s   the 50 labelled frames live
                                                  inside this span

Three costs were feared before this was written.  Two turned out not to be
real, and saying so is the point of writing them down.

REGISTRATION.  The temporal features want 21 frames (+-10) stabilised onto
the anchor, which is 20 ORB fits per anchor - 33,520 over the run.  The plan
was to fit each CONSECUTIVE pair once and compose, since the composition is
algebraically the same transform.  regcheck.py then scored the chain against
the direct fits on correspondences neither was fitted to: the chain lands a
median 1.79 and worst 6.56 source px from where the matches say it should,
against a patch half-width of 7.5 source px.  Marginal - and clipbuild built
the TRAINING sheets with direct fits, so the chain would also have been a
train/serve skew.  Then the direct fits were timed: 110 ms for all 20, because
each frame's keypoints are already computed once in the rolling buffer and
only bf.match and RANSAC repeat.  About 3 minutes over the whole run.  So the
saving was never worth having.  Direct fits, the same ones clipbuild made; the
chain survives only as the fallback for a direct fit that will not converge.

PRUNING.  rank6.py measured how deep a still-only ranking can be cut before it
throws the ball away: top 500 holds 43 of 45 findable test frames, and top
1000 holds the same 43 - so the 2 it loses are lost at every depth, and
pruning deeper buys nothing.  Pruning to 500 saves roughly 7 minutes and costs
2 frames of ceiling on the most important number in the project.  Refused.
STAGE1 stays as a knob and defaults to no prune.

MEMORY.  1676 frames x ~1600 candidates x 66 columns is ~700 MB before the
ranker sees a row.  Nothing is materialised: one anchor at a time, and only
the surviving top KEEP leave the loop.

    python dense.py --check          # composed vs direct, kept for the record
    python dense.py --n 40           # a short run, for timing
    python dense.py                  # -> dense.npz
"""
import math
import os
import sys
import time

import cv2
import numpy as np
import xgboost as xgb

import feat4 as F
import feat_t4 as TT

S2 = os.path.dirname(os.path.abspath(__file__))
SP = F.SP
VIDEO = F.VIDEO
OUT = os.path.join(S2, 'dense.npz')

LO, HI = 186600, 188275       # the test stretch, inclusive, absolute frames
OFFS = list(range(-10, 11))
NOFF = len(OFFS)
CZERO = OFFS.index(0)
TW, TH = 640, 360
SCALE = 1920.0 / TW           # 3.0
JPEGQ = 72                    # clipbuild wrote the sheets at this quality, and
                              # the model learnt on that noise; match it
STAGE1 = 10 ** 6              # no prune - see the header
KEEP = 40                     # candidates written out per frame (track.py's K)
MINMATCH, MININL = 12, 10     # clipbuild's guards, kept identical


def fit_pair(ka, da, kb, db, bf):
    """Affine mapping frame a's pixels onto frame b's.  None if not trusted."""
    if da is None or db is None or len(ka) < MINMATCH or len(kb) < MINMATCH:
        return None
    mm = bf.match(da, db)
    if len(mm) < MINMATCH:
        return None
    p = np.float32([ka[x.queryIdx].pt for x in mm]).reshape(-1, 1, 2)
    d = np.float32([kb[x.trainIdx].pt for x in mm]).reshape(-1, 1, 2)
    M, inl = cv2.estimateAffinePartial2D(p, d, method=cv2.RANSAC,
                                         ransacReprojThreshold=3.0)
    if M is None or inl is None or inl.sum() < MININL:
        return None
    return M


def homog(M):
    H = np.eye(3)
    H[:2] = M
    return H


def F_scaled(M, s):
    """clipbuild.scaled: the same affine, expressed in a picture resized by s."""
    A = M[:, :2]
    t = M[:, 2]
    c = 0.5 * (s - 1.0)
    out = np.zeros((2, 3), np.float64)
    out[:, :2] = A
    out[:, 2] = s * t + c - A.dot(np.array([c, c]))
    return out


def transforms(kds, bf, c):
    """Offset -> anchor for all 21 buffer slots: (mats, how).

    `how` records which route produced each one, because a tile registered by
    the fallback and a tile not registered at all are different kinds of wrong,
    and the run should be able to say how much of each it did:

        2  direct fit - what clipbuild did at training time
        1  the consecutive chain, where the direct fit would not converge
        0  neither, so the tile goes in unwarped: clipbuild's flag-2 case
    """
    n = len(kds)
    mats = [None] * n
    how = [0] * n
    mats[c] = np.eye(3)
    how[c] = 2
    for k in range(n):
        if k == c:
            continue
        M = fit_pair(kds[k][0], kds[k][1], kds[c][0], kds[c][1], bf)
        if M is not None:
            mats[k] = homog(M)
            how[k] = 2

    # the fallback, walking outward from the anchor so that a slot whose direct
    # fit failed can still be reached through its already-solved neighbour
    for k in range(c + 1, n):
        if mats[k] is None and mats[k - 1] is not None:
            M = fit_pair(kds[k][0], kds[k][1], kds[k - 1][0], kds[k - 1][1], bf)
            if M is not None:
                mats[k] = mats[k - 1].dot(homog(M))
                how[k] = 1
    for k in range(c - 1, -1, -1):
        if mats[k] is None and mats[k + 1] is not None:
            M = fit_pair(kds[k][0], kds[k][1], kds[k + 1][0], kds[k + 1][1], bf)
            if M is not None:
                mats[k] = mats[k + 1].dot(homog(M))
                how[k] = 1
    return mats, how


def tiles_for(bgrs, mats):
    """The 21 stabilised grey tiles, through the same JPEG the model saw."""
    out = np.empty((len(bgrs), TH, TW), np.float32)
    for k in range(len(bgrs)):
        small = cv2.resize(bgrs[k], (TW, TH), interpolation=cv2.INTER_AREA)
        if mats[k] is not None:
            small = cv2.warpAffine(
                small, F_scaled(mats[k][:2], 1.0 / SCALE), (TW, TH),
                flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        enc = cv2.imencode('.jpg', small,
                           [int(cv2.IMWRITE_JPEG_QUALITY), JPEGQ])[1]
        small = cv2.imdecode(enc, cv2.IMREAD_COLOR)
        out[k] = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return out


def still_rows(bs, cmaj, cara):
    """feat4.main's row construction, lifted out so dense.py can reuse it."""
    pts = np.array([[b['cx'], b['cy']] for b in bs], np.float32)
    rows = []
    for b in bs:
        dxy = np.hypot(pts[:, 0] - b['cx'], pts[:, 1] - b['cy'])
        nn = float(np.sort(dxy)[1]) if len(dxy) > 1 else 999.0
        e_maj = max(1.0, cmaj[0] * b['cy'] + cmaj[1])
        e_side = max(1.0, cara[0] * b['cy'] + cara[1])
        rows.append([b['minor'], b['major'], b['area'], b['fill'], b['elong'],
                     b['circ'], b['extent'], b['v_in'], b['v_max'], b['v_std'],
                     b['v_ring'], b['v_ring_max'], b['con'], b['rel_con'],
                     b['s_in'], b['s_ring'], b['h_in'], b['grass_ring'],
                     b['other_ring'], b['white_far'], b['lap'],
                     b['cx'] / 1920.0, b['cy'] / 1080.0,
                     b['major'] / e_maj, b['minor'] / e_maj,
                     math.sqrt(b['area']) / e_side,
                     nn, float((dxy < 100).sum() - 1), float(len(bs)),
                     b['split']] + b['th'])
    return np.array(rows, np.float32), pts


def temporal_rows(tiles, pts):
    """feat_t4.main's per-candidate block, for candidates given in SOURCE px."""
    bg = np.median(tiles, axis=0)
    fgs = np.clip(tiles - bg, 0, None)
    cur = tiles[CZERO]
    RP, RW, FG = TT.RP, TT.RW, TT.FG
    H, W = TH, TW
    T = np.zeros((len(pts), len(TT.TNAMES)), np.float32)
    for idx in range(len(pts)):
        tx, ty = pts[idx][0] / SCALE, pts[idx][1] / SCALE
        ix, iy = int(round(tx)), int(round(ty))
        x0, x1 = max(0, ix - RP), min(W, ix + RP + 1)
        yy0, yy1 = max(0, iy - RP), min(H, iy + RP + 1)
        if x1 <= x0 or yy1 <= yy0:
            continue
        series = tiles[:, yy0:yy1, x0:x1].reshape(NOFF, -1).max(axis=1)
        fgser = fgs[:, yy0:yy1, x0:x1].reshape(NOFF, -1).max(axis=1)
        v0 = float(series[CZERO])
        t_med = float(np.median(series))
        t_min = float(series.min())
        bgmax = float(bg[yy0:yy1, x0:x1].max())
        bright = series > 150
        persist = series > (v0 - 25)
        left = float(series[:CZERO].mean())
        right = float(series[CZERO + 1:].mean())
        mad = float(np.abs(tiles[:, yy0:yy1, x0:x1]
                           - cur[yy0:yy1, x0:x1]).mean())

        wx0, wx1 = max(0, ix - RW), min(W, ix + RW + 1)
        wy0, wy1 = max(0, iy - RW), min(H, iy + RW + 1)
        win = fgs[:, wy0:wy1, wx0:wx1]
        flat = win.reshape(NOFF, -1)
        am = flat.argmax(axis=1)
        pw = wx1 - wx0
        px = (am % pw).astype(np.float32) + wx0
        py = (am // pw).astype(np.float32) + wy0
        seen = flat.max(axis=1) > FG
        if seen.sum() >= 4:
            tt = np.arange(NOFF, dtype=np.float32)[seen]
            A = np.vstack([tt, np.ones_like(tt)]).T
            bx = np.linalg.lstsq(A, px[seen], rcond=None)[0]
            by = np.linalg.lstsq(A, py[seen], rcond=None)[0]
            res = np.hypot(px[seen] - A.dot(bx), py[seen] - A.dot(by))
            speed = float(math.hypot(bx[0], by[0]))
            straight = float(res.mean())
            disp = float(math.hypot(px[seen][-1] - px[seen][0],
                                    py[seen][-1] - py[seen][0]))
        else:
            speed, straight, disp = 0.0, 99.0, 0.0

        qx0, qx1 = max(0, ix - 1), min(W, ix + 2)
        qy0, qy1 = max(0, iy - 1), min(H, iy + 2)
        q = tiles[:, qy0:qy1, qx0:qx1].reshape(NOFF, -1).max(axis=1)
        qf = fgs[:, qy0:qy1, qx0:qx1].reshape(NOFF, -1).max(axis=1)
        q0 = float(q[CZERO])
        qmed = float(np.median(q))

        T[idx] = [v0, t_med, t_min, v0 - t_med, v0 - t_min, float(series.std()),
                  bright.mean(), persist.mean(),
                  TT.run_around(persist, CZERO),
                  float(fgser[CZERO]),
                  float(fgs[CZERO, yy0:yy1, x0:x1].mean()),
                  float(fgser[CZERO]) / max(1.0, v0),
                  bgmax, left, right, abs(left - right),
                  float(series[min(NOFF - 1, CZERO + 1)]
                        - series[max(0, CZERO - 1)]),
                  mad, float((fgser > FG).sum()),
                  speed, straight, disp,
                  q0, qmed, q0 - qmed, float((q > (q0 - 25)).mean()),
                  float(qf[CZERO]), float((qf > FG).sum()),
                  float(bg[qy0:qy1, qx0:qx1].max()),
                  float(np.abs(tiles[:, qy0:qy1, qx0:qx1]
                               - cur[qy0:qy1, qx0:qx1]).mean())]
    return T


def check(nsample=6):
    """Composed transforms against direct ones, at the frame corners.

    Superseded as an arbiter by regcheck.py, which scores both against
    correspondences neither was fitted to and is what actually decided the
    design.  Kept because it is the measurement that raised the question.
    """
    cap = cv2.VideoCapture(VIDEO)
    assert cap.isOpened(), 'cannot open video'
    orb = cv2.ORB_create(2000)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    corners = np.array([[0, 0, 1], [1919, 0, 1], [0, 1079, 1], [1919, 1079, 1]],
                       np.float64).T
    print('COMPOSED vs DIRECT registration  (source pixels, %d samples)'
          % nsample)
    print('  %-9s %-9s %-9s %s' % ('anchor', 'mean err', 'max err', 'verdict'))
    errs = []
    for j in range(nsample):
        t = LO + 120 + j * ((HI - LO - 300) // max(1, nsample - 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, t)
        kd = []
        for k in range(11):
            ok, img = cap.read()
            assert ok, 'read failed at %d' % (t + k)
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            kd.append(orb.detectAndCompute(g, None))
        comp = np.eye(3)
        bad = False
        for k in range(1, 11):
            M = fit_pair(kd[k][0], kd[k][1], kd[k - 1][0], kd[k - 1][1], bf)
            if M is None:
                bad = True
                break
            comp = comp.dot(homog(M))
        D = fit_pair(kd[10][0], kd[10][1], kd[0][0], kd[0][1], bf)
        if bad or D is None:
            print('  %-9d %s' % (t, 'a pair would not fit - offset drops out'))
            continue
        a = comp.dot(corners)[:2]
        b = homog(D).dot(corners)[:2]
        e = np.hypot(a[0] - b[0], a[1] - b[1])
        errs.append(float(e.max()))
        print('  %-9d %-9.2f %-9.2f %s'
              % (t, e.mean(), e.max(),
                 'ok' if e.max() < 6.0 else 'TOO LARGE'))
    if errs:
        print()
        print('worst corner disagreement over 10 composed steps: %.2f source px'
              % max(errs))
        print('this says only that they differ, not which one is wrong;')
        print('regcheck.py is the measurement that answers that.')
    cap.release()


def main():
    a = sys.argv[1:]
    if '--check' in a:
        check()
        return
    nlimit = int(a[a.index('--n') + 1]) if '--n' in a else None

    # KEEP is the number of candidates that survive to the tracker, and
    # dtrack found it binding: the ball is inside the top 40 in only 43 of
    # 47 labelled frames, and a path that has to stay continuous across 23
    # frames between labels breaks wherever the ball falls out of the pool.
    # Raising it writes a bigger file and costs the tracker time, nothing
    # else - the features are already computed by the time it applies.
    global KEEP, OUT
    if '--keep' in a:
        KEEP = int(a[a.index('--keep') + 1])
        OUT = os.path.join(S2, 'dense%d.npz' % KEEP)

    z = np.load(os.path.join(S2, 'still4.npz'), allow_pickle=True)
    cmaj, cara = z['cmaj'], z['cara']
    bst_s = xgb.Booster()
    bst_s.load_model(os.path.join(S2, 'rank6.still.model.json'))
    bst = xgb.Booster()
    bst.load_model(os.path.join(S2, 'rank6.model.json'))

    lo = LO
    hi = HI if nlimit is None else min(HI, LO + nlimit - 1)
    first, last = lo + OFFS[0], hi + OFFS[-1]
    cap = cv2.VideoCapture(VIDEO)
    assert cap.isOpened(), 'cannot open video'
    cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    orb = cv2.ORB_create(2000)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    print('dense detection over abs %d..%d  (%d frames, %.1f s of play)'
          % (lo, hi, hi - lo + 1, (hi - lo + 1) / 30.0))
    print('  %s; top %d written per frame'
          % ('no still-only prune' if STAGE1 > 10000
             else 'still-only prune to %d' % STAGE1, KEEP))

    bgrs, kds = [], []
    FI, CX, CY, SC = [], [], [], []
    NC, HOW = {}, {}
    t0 = time.time()
    done = 0
    for ab in range(first, last + 1):
        ok, img = cap.read()
        if not ok:
            print('  read failed at %d - stopping' % ab)
            break
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        kds.append(orb.detectAndCompute(g, None))
        bgrs.append(img)
        if len(bgrs) > NOFF:
            bgrs.pop(0)
            kds.pop(0)
        if len(bgrs) < NOFF:
            continue

        anchor = ab + OFFS[0]                  # the middle of the buffer
        bs, _ = F.blobs(bgrs[CZERO], cmaj)
        NC[anchor] = len(bs)
        if not bs:
            done += 1
            continue
        Xs, pts = still_rows(bs, cmaj, cara)
        if len(bs) > STAGE1:
            s1 = bst_s.predict(xgb.DMatrix(Xs))
            sel = np.argsort(-s1)[:STAGE1]
        else:
            sel = np.arange(len(bs))
        mats, how = transforms(kds, bf, CZERO)
        HOW[anchor] = how
        tiles = tiles_for(bgrs, mats)
        Tt = temporal_rows(tiles, pts[sel])
        s2 = bst.predict(xgb.DMatrix(np.hstack([Xs[sel], Tt])))
        for j in np.argsort(-s2)[:KEEP]:
            FI.append(anchor)
            CX.append(float(pts[sel][j][0]))
            CY.append(float(pts[sel][j][1]))
            SC.append(float(s2[j]))
        done += 1
        if done % 25 == 0:
            el = time.time() - t0
            print('  %d/%d frames  %.0fs  (%.2f s/frame, eta %.0f min)'
                  % (done, hi - lo + 1, el, el / done,
                     (hi - lo + 1 - done) * el / done / 60.0), flush=True)
    cap.release()

    fr = np.array(sorted(NC), np.int32)
    hw = np.array([HOW.get(int(i), [0] * NOFF) for i in fr], np.int8)
    nc = np.array([NC[int(i)] for i in fr], np.int32)
    np.savez_compressed(OUT, f=np.array(FI, np.int32),
                        x=np.array(CX, np.float32),
                        y=np.array(CY, np.float32),
                        s=np.array(SC, np.float32),
                        frames=fr, ncand=nc, how=hw,
                        lo=lo, hi=hi, keep=KEEP, stage1=STAGE1)
    print()
    print('%d frames detected, %d rows written, %.1f min'
          % (len(fr), len(FI), (time.time() - t0) / 60.0))
    print('  candidates/frame  mean %.0f  min %d  max %d'
          % (nc.mean(), nc.min(), nc.max()))
    print('  frames with no candidate at all   %d' % int((nc == 0).sum()))
    off = hw[nc > 0]
    if off.size:
        print('REGISTRATION, over %d offset tiles' % off.size)
        for v, what in ((2, 'direct fit (as clipbuild)'),
                        (1, 'chain fallback          '),
                        (0, 'unwarped (neither)      ')):
            print('  %s  %7.3f%%' % (what, 100.0 * (off == v).sum() / off.size))
    print('saved %s' % OUT)


if __name__ == '__main__':
    main()
