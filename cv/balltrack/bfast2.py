"""bfast.blobs with pass 2 batched as well - the last 113 ms of the 180.

bprof2 put 62.7% of blobs inside the `_row` loop, and bprof3 split that
loop into what it actually spends:

    maskred  44.1 ms   v_in / v_max / v_std / s_in / h_in over the mask
    ringred  29.7 ms   v_ring / v_ring_max / grass_ring / s_ring over the ring
    lap      26.6 ms   cv2.Laplacian on the box, then .var()
    dict      8.8 ms   the 24-key dict
    ringany   2.3 ms   ring.any()

The lever is the one bfast already found - dispatch, not bytes.  bshape.py
measured why the obvious alternative is wrong: all 2317 boxes together touch
0.98 Mpx, less than half a 1920x1080 frame, so ANY whole-image precompute
(an integral image, a bincount over the label image) costs more than the
per-candidate work it would replace.  Only collapsing the loop can pay.

It pays because the boxes cluster.  Half of every frame's candidates are
the split branch's fixed 25x25 window, and the component branch's boxes are
a bbox padded by 4, so they are small and repetitive: twelve shapes cover
three quarters of the frame.  So each reduction happens once per GROUP:

  * component rows are gathered into a padded (n, B, B) block, B the
    smallest of 14/20/28/44/68 that fits.  Their masks are not carried from
    pass 1 at all - the label image is gathered alongside and the mask is
    `LAB == i`, the ring is `LAB == 0`, which is where `ring.any()` goes too
    (it becomes a count that was computed anyway).
  * split rows group by EXACT shape, since their masks are real arrays that
    have to be stacked, and they are almost all the same 25x25.
  * lap groups by exact shape across both branches, because a Laplacian
    needs the true box: reflect-101 off a padded edge is not the same
    border the original saw.

Exactness.  Not "close" - most of these come back bit for bit, and the
reasons are worth writing down because they are what makes the batching
legitimate rather than merely fast:

  * v, s, h and grass are uint8/bool, so a masked sum is a sum of integers.
    The largest box is 68x68 and values reach 255, so the sum never exceeds
    1179120 - inside float32's exact-integer range (2^24) and far inside
    float64's.  Summation ORDER cannot round what never rounds, so the
    batched sum equals the original's whatever order numpy walks it in,
    and the padded zeros contribute nothing.
  * that leaves the divide, so each mean is divided in the same precision
    numpy's own .mean() would have used: float32 for v (a float32 array),
    float64 for s/h/grass (integer arrays promote).  Same two values, same
    IEEE division, same answer.
  * lap is bit-exact and measured to be: lapprobe.py found numpy's
    reflect-padded 4-neighbour kernel identical to cv2.Laplacian on 400
    random boxes, and varprobe.py found a batched .var(axis=1) identical to
    the per-row one on 300 random stacks.  A whole-image Laplacian would
    NOT have been - see bfast's own docstring - but a stack of real boxes
    is just the same convolution done n times at once.

  * v_std is the one number that cannot come back bit for bit.  It is a sum
    of SQUARES, which do round, so the padded zeros and the different walk
    order move it in the last float32 ulp.  It is computed by the same
    float32 algorithm numpy uses (subtract the same float32 mean, square,
    sum, sqrt), so the drift is ~1e-7 relative, and it feeds no gate.
    Set EXACT_STD to fall back to the per-candidate gather if bcheck ever
    shows that moving the top-200.

The one place a last-ulp difference could change the OUTPUT rather than a
feature is the component branch's `con >= CON` gate, where a candidate
sitting on 10.0 could fall the other way and change the list length.  The
integer argument above says it cannot, but "cannot" is cheap to insure: any
row landing within GATE_EPS of the threshold is recomputed by the original
scalar path and decided on that.  It costs nothing because ~0 rows qualify,
and it turns a proof into a guarantee.
"""
import math
import time

import cv2
import numpy as np

import feat4 as F
from bfast import tophat_batch, box_mean          # unchanged, already exact

BUCKETS = (14, 20, 28, 44, 68)   # padded sizes for the component gather
LAPMIN = 3                       # batch a lap group only if it has this many
GATE_EPS = 1e-3                  # recheck rows this close to F.CON by hand
EXACT_STD = False                # True = per-candidate v_std, bit for bit

# Set to a dict to accumulate per-phase seconds in place.  The timing is
# eight perf_counter pairs per FRAME, not per candidate, so unlike bprof3 it
# costs nothing measurable and can profile the real function rather than a
# copy of it that might drift from it.
PROF = None


def _bucket(need):
    for b in BUCKETS:
        if b >= need:
            return b
    return int(need)


def _lap_batch(grey32, y0, x0, bh, bw):
    """cv2.Laplacian(box).var() for a stack of identically shaped boxes.

    Bit-identical to the per-box cv2 call: same 4-neighbour kernel, same
    BORDER_REFLECT_101 (numpy's 'reflect' does not repeat the edge sample),
    and a variance along a contiguous last axis walks the same pairwise sum
    numpy would walk on one box alone.
    """
    n = len(y0)
    g = grey32[(y0[:, None] + np.arange(bh))[:, :, None],
               (x0[:, None] + np.arange(bw))[:, None, :]]
    p = np.pad(g, ((0, 0), (1, 1), (1, 1)), mode='reflect')
    lp = (p[:, :-2, 1:-1] + p[:, 2:, 1:-1] + p[:, 1:-1, :-2] + p[:, 1:-1, 2:]
          - 4.0 * p[:, 1:-1, 1:-1])
    return lp.reshape(n, -1).var(axis=1)


def _reduce(M, R, V, S, Hh, G):
    """The nine masked/ring numbers for one group, as arrays down the stack.

    Every sum is over integers and so is exact; only the divide rounds, and
    it rounds in the precision numpy's own .mean() would have used.
    """
    ax = (1, 2)
    cm = M.sum(axis=ax, dtype=np.int64)
    cr = R.sum(axis=ax, dtype=np.int64)
    cm1 = np.maximum(cm, 1)
    cr1 = np.maximum(cr, 1)

    vm = np.where(M, V, 0)
    v_in = vm.sum(axis=ax, dtype=np.int64).astype(np.float32) \
        / cm1.astype(np.float32)
    v_max = vm.max(axis=ax).astype(np.float64)
    s_in = np.where(M, S, 0).sum(axis=ax, dtype=np.int64) / cm1
    h_in = np.where(M, Hh, 0).sum(axis=ax, dtype=np.int64) / cm1

    if EXACT_STD:
        v_std = None
    else:
        d = np.where(M, V.astype(np.float32) - v_in[:, None, None],
                     np.float32(0.0))
        v_std = np.sqrt((d * d).sum(axis=ax, dtype=np.float32)
                        / cm1.astype(np.float32))

    rv = np.where(R, V, 0)
    v_ring = rv.sum(axis=ax, dtype=np.int64).astype(np.float32) \
        / cr1.astype(np.float32)
    v_ring_max = rv.max(axis=ax).astype(np.float64)
    g_ring = np.where(R, G, False).sum(axis=ax, dtype=np.int64) / cr1
    s_ring = np.where(R, S, 0).sum(axis=ax, dtype=np.int64) / cr1

    # The original's empty-ring branch, applied down the stack.
    e = (cr == 0)
    if e.any():
        v_ring = np.where(e, v_in, v_ring)
        v_ring_max = np.where(e, v_in.astype(np.float64), v_ring_max)
        g_ring = np.where(e, 0.0, g_ring)
        s_ring = np.where(e, 0.0, s_ring)
    return (v_in, v_max, v_std, s_in, h_in,
            v_ring, v_ring_max, g_ring, s_ring)


def blobs(img, cmaj=None):
    """feat4.blobs, same candidates in the same order, pass 2 batched too."""
    def tick(t0, name):
        if PROF is None:
            return 0.0
        t1 = time.perf_counter()
        PROF[name] = PROF.get(name, 0.0) + t1 - t0
        return t1

    _t = time.perf_counter() if PROF is not None else 0.0
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    white = ((v > 150) & (s < F.SAT)).astype(np.uint8) * 255
    white = cv2.morphologyEx(white, cv2.MORPH_OPEN, F.K2)
    grass = ((h > 30) & (h < 95) & (s > 60))
    n, lab, st, cent = cv2.connectedComponentsWithStats(white, 8)
    H, W = v.shape
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    wf = (lab > 0)
    _t = tick(_t, 'front')

    resp = None
    if cmaj is not None:
        for k in F.SCALES:
            se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            th = cv2.morphologyEx(v, cv2.MORPH_TOPHAT, se)
            resp = th if resp is None else np.maximum(resp, th)
        d = cv2.dilate(resp, np.ones((2 * F.NMS + 1, 2 * F.NMS + 1), np.uint8))
        ispeak = (resp == d) & (resp >= F.THR) & (v > 150) & (s < F.SAT)
    _t = tick(_t, 'tophat')

    # --- pass 1: geometry only, and no masks built for component rows -----
    # A component row carries its label id instead of a boolean array: the
    # mask and the ring both fall out of the label image once it is gathered,
    # which is cheaper than building them here and stacking them later.
    jobs, gate, ident = [], [], []
    for i in range(1, n):
        x, y, w, hh, area = st[i]
        mn, mj = min(w, hh), max(w, hh)
        if mn >= F.MINOR and mj <= F.MAJOR and area >= F.FILL * w * hh:
            y0, y1 = max(0, y - F.PAD), min(H, y + hh + F.PAD)
            x0, x1 = max(0, x - F.PAD), min(W, x + w + F.PAD)
            jobs.append((y0, y1, x0, x1, cent[i][0], cent[i][1],
                         mn, mj, area, w, hh, 0.0))
            gate.append(True)
            ident.append(i)
        if cmaj is None:
            continue
        e_maj = max(1.0, cmaj[0] * cent[i][1] + cmaj[1])
        if mj <= F.OVER * e_maj:
            continue
        pk = ispeak[y:y + hh, x:x + w] & (lab[y:y + hh, x:x + w] == i)
        py, px = np.where(pk)
        if not len(px):
            continue
        order = np.argsort(-resp[py + y, px + x])[:F.PERCOMP]
        for j in order:
            cx, cy = int(px[j]) + x, int(py[j]) + y
            wy0, wy1 = max(0, cy - F.WIN), min(H, cy + F.WIN + 1)
            wx0, wx1 = max(0, cx - F.WIN), min(W, cx + F.WIN + 1)
            win = resp[wy0:wy1, wx0:wx1]
            bw = (win >= 0.5 * float(resp[cy, cx])).astype(np.uint8)
            n2, l2 = cv2.connectedComponents(bw, 8)
            m = (l2 == l2[cy - wy0, cx - wx0])
            ys2, xs2 = np.where(m)
            sw = int(xs2.max() - xs2.min()) + 1
            sh = int(ys2.max() - ys2.min()) + 1
            jobs.append((wy0, wy1, wx0, wx1, cx, cy,
                         min(sw, sh), max(sw, sh), int(m.sum()), sw, sh, 1.0))
            gate.append(False)
            ident.append(m)

    _t = tick(_t, 'pass1')
    if not jobs:
        return [], grey

    nc = len(jobs)
    y0 = np.array([j[0] for j in jobs], np.int64)
    y1 = np.array([j[1] for j in jobs], np.int64)
    x0 = np.array([j[2] for j in jobs], np.int64)
    x1 = np.array([j[3] for j in jobs], np.int64)
    cxs = np.array([j[4] for j in jobs], np.float64)
    cys = np.array([j[5] for j in jobs], np.float64)
    ars = np.array([j[8] for j in jobs], np.float64)
    ws = np.array([j[9] for j in jobs], np.int64)
    hs = np.array([j[10] for j in jobs], np.int64)
    isplit = np.array([j[11] == 1.0 for j in jobs], bool)

    # --- the stages bfast already batched, unchanged ----------------------
    em = np.maximum(1.0, cmaj[0] * cys + cmaj[1]) if cmaj is not None \
        else np.ones(nc)
    TH = tophat_batch(resp, cxs, cys, em)
    integ = cv2.integral(wf.view(np.uint8))
    boxarea = ((y1 - y0) * (x1 - x0)).astype(np.float64)
    OTH = box_mean(integ, y0, y1, x0, x1) - ars / boxarea
    FY0, FY1 = np.maximum(0, y0 - 3 * hs), np.minimum(H, y1 + 3 * hs)
    FX0, FX1 = np.maximum(0, x0 - 3 * ws), np.minimum(W, x1 + 3 * ws)
    WFAR = box_mean(integ, FY0, FY1, FX0, FX1)
    _t = tick(_t, 'batch')

    # --- pass 2a: the nine masked/ring numbers, once per group ------------
    bh, bw = y1 - y0, x1 - x0
    VIN = np.zeros(nc, np.float64)
    VMAX = np.zeros(nc, np.float64)
    VSTD = np.zeros(nc, np.float64)
    SIN = np.zeros(nc, np.float64)
    HIN = np.zeros(nc, np.float64)
    VRING = np.zeros(nc, np.float64)
    VRMAX = np.zeros(nc, np.float64)
    GRING = np.zeros(nc, np.float64)
    SRING = np.zeros(nc, np.float64)

    def store(g, r):
        for arr, val in zip((VIN, VMAX, VSTD, SIN, HIN,
                             VRING, VRMAX, GRING, SRING), r):
            if val is not None:
                arr[g] = val

    idx = np.arange(nc)
    comp = idx[~isplit]
    if len(comp):
        need = np.maximum(bh[comp], bw[comp])
        for B in sorted({_bucket(int(t)) for t in need}):
            g = comp[np.array([_bucket(int(t)) == B for t in need])]
            d = np.arange(B)
            yr, xr = y0[g][:, None] + d, x0[g][:, None] + d
            ok = (yr < y1[g][:, None])[:, :, None] \
                & (xr < x1[g][:, None])[:, None, :]
            yy = np.minimum(yr, H - 1)[:, :, None]
            xx = np.minimum(xr, W - 1)[:, None, :]
            L = lab[yy, xx]
            ids = np.array([ident[k] for k in g], np.int64)[:, None, None]
            store(g, _reduce((L == ids) & ok, (L == 0) & ok,
                             v[yy, xx], s[yy, xx], h[yy, xx], grass[yy, xx]))

    spl = idx[isplit]
    if len(spl):
        shapes = {}
        for k in spl:
            shapes.setdefault((int(bh[k]), int(bw[k])), []).append(k)
        for (gh, gw), ks in shapes.items():
            g = np.array(ks)
            M = np.stack([ident[k] for k in g])
            yy = (y0[g][:, None] + np.arange(gh))[:, :, None]
            xx = (x0[g][:, None] + np.arange(gw))[:, None, :]
            store(g, _reduce(M, ~M, v[yy, xx], s[yy, xx],
                             h[yy, xx], grass[yy, xx]))

    _t = tick(_t, 'groups')
    if EXACT_STD:
        vf = v.astype(np.float32)
        for k in range(nc):
            m = (lab[y0[k]:y1[k], x0[k]:x1[k]] == ident[k]) \
                if not isplit[k] else ident[k]
            VSTD[k] = float(vf[y0[k]:y1[k], x0[k]:x1[k]][m].std())

    # --- pass 2b: lap, grouped by exact box shape -------------------------
    grey32 = grey.astype(np.float32)
    LAP = np.zeros(nc, np.float64)
    shapes = {}
    for k in range(nc):
        shapes.setdefault((int(bh[k]), int(bw[k])), []).append(k)
    for (gh, gw), ks in shapes.items():
        if len(ks) < LAPMIN:
            for k in ks:
                LAP[k] = float(cv2.Laplacian(
                    grey[y0[k]:y1[k], x0[k]:x1[k]], cv2.CV_32F).var())
            continue
        g = np.array(ks)
        LAP[g] = _lap_batch(grey32, y0[g], x0[g], gh, gw)

    # --- the gate insurance -----------------------------------------------
    _t = tick(_t, 'lap')
    # A component row within GATE_EPS of CON is recomputed the original way,
    # so the kept/dropped decision is the original's decision, not a batched
    # approximation of it.  In practice this fires for no rows at all.
    con = VIN - VRING
    near = idx[(~isplit) & (np.abs(con - F.CON) < GATE_EPS)]
    if len(near):
        vf = v.astype(np.float32)
        for k in near:
            sub = lab[y0[k]:y1[k], x0[k]:x1[k]]
            vv = vf[y0[k]:y1[k], x0[k]:x1[k]]
            m, ring = (sub == ident[k]), ~(sub > 0)
            VIN[k] = float(vv[m].mean())
            VRING[k] = float(vv[ring].mean()) if ring.any() else VIN[k]

    # --- pass 2c: the dicts, in the original's order ----------------------
    _t = tick(_t, 'gate')
    th_list = TH.tolist()
    oth, wfar = OTH.tolist(), WFAR.tolist()
    out = []
    for k in range(nc):
        (_, _, _, _, _, _, mn, mj, area, w, hh, split) = jobs[k]
        v_in, v_ring = float(VIN[k]), float(VRING[k])
        c = v_in - v_ring
        if gate[k] and c < F.CON:
            continue
        out.append(dict(
            th=th_list[k], cx=float(cxs[k]), cy=float(cys[k]),
            minor=mn, major=mj, area=int(area),
            fill=area / float(max(1, w * hh)),
            elong=mj / float(max(1, mn)),
            circ=area / (math.pi * (mj / 2.0) ** 2),
            extent=area / float(max(1, w * hh)),
            v_in=v_in, v_max=float(VMAX[k]), v_std=float(VSTD[k]),
            v_ring=v_ring, v_ring_max=float(VRMAX[k]), con=c,
            rel_con=c / max(1.0, v_ring),
            s_in=float(SIN[k]), s_ring=float(SRING[k]), h_in=float(HIN[k]),
            grass_ring=float(GRING[k]), other_ring=oth[k], white_far=wfar[k],
            lap=float(LAP[k]), split=split))
    _t = tick(_t, 'dicts')
    return out, grey
