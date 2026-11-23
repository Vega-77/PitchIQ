"""Ball-candidate blobs for one frame, with the per-candidate statistics batched.

Profiled one candidate at a time, 62.7% of blobs went inside its `_row`
loop, spent like this:

    maskred  44.1 ms   v_in / v_max / v_std / s_in / h_in over the mask
    ringred  29.7 ms   v_ring / v_ring_max / grass_ring / s_ring over the ring
    lap      26.6 ms   cv2.Laplacian on the box, then .var()
    dict      8.8 ms   the 24-key dict
    ringany   2.3 ms   ring.any()

The lever is dispatch, not bytes (pass 1 is batched for the same reason).
A measurement showed why the obvious alternative is wrong: all 2317 boxes together touch
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
  * lap is bit-exact and measured to be: a probe found numpy's
    reflect-padded 4-neighbour kernel identical to cv2.Laplacian on 400
    random boxes, and another found a batched .var(axis=1) identical to
    the per-row one on 300 random stacks.  A whole-image Laplacian would
    NOT have been - its border at a box edge differs - but a stack of real boxes
    is just the same convolution done n times at once.

  * v_std is the one number that cannot come back bit for bit.  It is a sum
    of SQUARES, which do round, so the padded zeros and the different walk
    order move it in the last float32 ulp.  It is computed by the same
    float32 algorithm numpy uses (subtract the same float32 mean, square,
    sum, sqrt), so the drift is ~1e-7 relative, and it feeds no gate.
    Set EXACT_STD to fall back to the per-candidate gather if
    reference/check_fast.py ever shows that moving the top-200.

The one place a last-ulp difference could change the OUTPUT rather than a
feature is the component branch's `con >= CON` gate, where a candidate
sitting on 10.0 could fall the other way and change the list length.  The
integer argument above says it cannot, but "cannot" is cheap to insure: any
row landing within GATE_EPS of the threshold is recomputed by the original
scalar path and decided on that.  It costs nothing because ~0 rows qualify,
and it turns a proof into a guarantee.

Faster again, and still bit for bit (2026-11-23, session step 3.13: about
1.3x, every candidate's fields the same type and the same bytes on 56 frames
of seven games, with and without cmaj):

  * the grass and white masks come from cv2.inRange on the HSV image:
    (h > 30) & (h < 95) & (s > 60) is h in [31, 94] and s in [61, 255] for
    uint8, and (v > vcut) & (s < SAT) is v in [vcut + 1, 255], s in
    [0, SAT - 1];
  * the small-component restore reads each component from its own
    SMALL x SMALL box instead of the whole frame;
  * pass 1's component rows are one vectorised filter, and a split peak's
    region is cv2.floodFill with a fixed range (win >= s0 / 2 is
    win >= s0 - s0 // 2 for integers) instead of labelling its window;
  * every gather reads a contiguous plane through one flat int64 index:
    the same pixels in the same layout;
  * the groups come from searchsorted / unique in a stable order, so each
    group holds the same rows in the same order and even the float32 v_std
    sums see identical arrays;
  * the dicts read Python lists instead of float(arr[k]): the same doubles.
"""

import math
import os
import time

import cv2
import numpy as np

# The generator's thresholds (shared with blobs_per_candidate.blobs, the per-candidate reference).
PAD = 4
SAT = 60  # white is v>150 and s<SAT.  satsweep.py measured
# this against 40/45/50/55/60/65/70: the shipped
# 70 admits sun-washed turf, which carries the
# SAME hue as grass (46.1 vs 46.8) and so cannot
# be excluded by hue at all.  60 beats 70 on both
# pools at once - train 109/116 vs 106, test
# 42/47 vs 38 - and costs 684 candidates a frame
# instead of 855.  Tighter is not free: 45 breaks
# every flood but drops train to 102.
VWHITE = 150  # the white cut: v > VWHITE ...
DV = 30  # ... or, when set (None: off), v > min(VWHITE, the frame's median grass V + DV): East Brunswick and
# Piscataway balls peak at V 128-144 on grass at 40-80, so a fixed 150 never sees them (2026-10-26)
GSTEP = 4  # the grass median reads every GSTEP-th pixel each way
MINOR, MAJOR, FILL, CON = 2, 60, 0.30, 10.0
SCALES = (9, 17)  # top-hat structuring elements: a far ball and a near one
NMS = 4  # peak suppression radius, source pixels
THR = 25  # minimum top-hat response
OVER = 1.3  # a component this many times the expected ball is merged
PERCOMP = 12  # at most this many peaks from one merged component
WIN = 12  # half-window for re-thresholding a split candidate
K2 = np.ones((2, 2), np.uint8)
OPEN = True  # open the white mask with K2 (drops specks; also drops a far ball smaller than 2x2)
SMALL = 6  # if set (0: off), a raw white component no bigger than SMALL px each way that the opening wiped out
# entirely comes back (a far ball of a few pixels; 56 of the 156 dev clicks lost at DV 30 went this way)
GIANT = 0  # if set (px; 0: off, the shipped behaviour), a white component at least this big is a pale surface (a
# washed-out pitch), not a blob: inside it only pixels brighter than its own median V + GDV stay white, so a ball
# lying on it becomes a blob of its own instead of one of PERCOMP peaks in half a frame (ball-push 2026-11-15)
GDV = 30
if os.environ.get('PIQ_GIANT'):  # an experiment's detector workers turn the rule on through this
    GIANT = int(os.environ['PIQ_GIANT'])

DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
TR = 12  # half-window for the shared top-hat readings
IR = 4  # radius at which the response falloff is sampled
CR = 6  # half-width of the core window, from tophat_stats

_DX = np.array([p[0] for p in DIRS], np.int64)
_DY = np.array([p[1] for p in DIRS], np.int64)
# a split peak's region: 8-connected, a fixed range from the seed, mask only, filled with 1
FLOOD = 8 | cv2.FLOODFILL_FIXED_RANGE | cv2.FLOODFILL_MASK_ONLY | (1 << 8)


def tophat_batch(resp, cx, cy, e_maj):
    """blobs_per_candidate.tophat_stats for every candidate at once, as (n, 6) float64."""
    nc = len(cx)
    out = np.zeros((nc, 6), np.float64)
    if resp is None or nc == 0:
        return out
    H, W = resp.shape
    ix = np.clip(np.round(np.asarray(cx, np.float64)), 0, W - 1).astype(np.int64)
    iy = np.clip(np.round(np.asarray(cy, np.float64)), 0, H - 1).astype(np.int64)
    pk = resp[iy, ix].astype(np.float64)
    thr = 0.5 * np.maximum(1.0, pk)

    # One gather of the 25x25 window for every candidate, from the zero-padded
    # response through one flat index (the same pixels, row-major, as a 2-D
    # fancy index lays them out).  The core is the middle 13x13 of the same
    # block, so it costs nothing extra.
    pr = cv2.copyMakeBorder(resp, TR, TR, TR, TR, cv2.BORDER_CONSTANT, value=0)
    Wp = W + 2 * TR
    d = np.arange(-TR, TR + 1)
    off = (d[:, None] * Wp + d[None, :]).ravel()
    win = pr.ravel()[((iy + TR) * Wp + (ix + TR))[:, None] + off[None, :]]  # (nc, 625)

    # The original clips the window at the image edge, so the divisor is the
    # clipped area - not 25*25, and not the padded area.
    wy0, wy1 = np.maximum(0, iy - TR), np.minimum(H, iy + TR + 1)
    wx0, wx1 = np.maximum(0, ix - TR), np.minimum(W, ix + TR + 1)
    warea = ((wy1 - wy0) * (wx1 - wx0)).astype(np.float64)

    wmean = win.sum(axis=1, dtype=np.int64) / warea
    # a uint8 is >= thr exactly when it is >= ceil(thr), at most 128
    bw = win >= np.ceil(thr).astype(np.uint8)[:, None]
    bwmean = bw.sum(axis=1, dtype=np.int64) / warea
    S = 2 * TR + 1
    core = bw.reshape(nc, S, S)[:, TR - CR : TR + CR + 1, TR - CR : TR + CR + 1]
    csum = core.reshape(nc, -1).sum(axis=1, dtype=np.int64)
    rr = np.sqrt(np.maximum(1, csum) / math.pi)

    # The falloff samples clamp to the border rather than reading padding.
    ay = np.clip(iy[:, None] + _DY[None, :] * IR, 0, H - 1)
    ax = np.clip(ix[:, None] + _DX[None, :] * IR, 0, W - 1)
    astd = resp[ay, ax].astype(np.float64).std(axis=1)

    em = np.asarray(e_maj, np.float64)
    out[:, 0] = pk
    out[:, 1] = pk / np.maximum(1.0, wmean)
    out[:, 2] = rr
    out[:, 3] = rr / np.maximum(0.5, 0.5 * em)
    out[:, 4] = astd / np.maximum(1.0, pk)
    out[:, 5] = bwmean
    return out


def box_mean(integ, y0, y1, x0, x1):
    """Mean of the integrated binary image over each [y0:y1, x0:x1] box."""
    s = integ[y1, x1].astype(np.float64) - integ[y0, x1] - integ[y1, x0] + integ[y0, x0]
    return s / ((y1 - y0) * (x1 - x0)).astype(np.float64)


BUCKETS = (14, 20, 28, 44, 68)  # padded sizes for the component gather
LAPMIN = 3  # batch a lap group only if it has this many
GATE_EPS = 1e-3  # recheck rows this close to CON by hand
EXACT_STD = False  # True = per-candidate v_std, bit for bit

# Set to a dict to accumulate per-phase seconds in place.  The timing is
# eight perf_counter pairs per FRAME, not per candidate, so unlike an outside profiler it
# costs nothing measurable and can profile the real function rather than a
# copy of it that might drift from it.
PROF = None


def _reduce(M, R, V, S, Hh, G):
    """The nine masked/ring numbers for one group, as arrays down the stack.

    Every sum is over integers and so is exact; only the divide rounds, and
    it rounds in the precision numpy's own .mean() would have used.
    """
    ax = (1, 2)
    cm = np.count_nonzero(M, axis=ax)
    cr = np.count_nonzero(R, axis=ax)
    cm1 = np.maximum(cm, 1)
    cr1 = np.maximum(cr, 1)

    vm = np.where(M, V, 0)
    v_in = vm.sum(axis=ax, dtype=np.int64).astype(np.float32) / cm1.astype(np.float32)
    v_max = vm.max(axis=ax).astype(np.float64)
    s_in = np.where(M, S, 0).sum(axis=ax, dtype=np.int64) / cm1
    h_in = np.where(M, Hh, 0).sum(axis=ax, dtype=np.int64) / cm1

    if EXACT_STD:
        v_std = None
    else:
        # (V - v_in) where M, else 0, squared, in place: the same float32
        # values in the same layout as before
        d = np.subtract(V, v_in[:, None, None], dtype=np.float32)
        d *= M
        d *= d
        v_std = np.sqrt(d.sum(axis=ax, dtype=np.float32) / cm1.astype(np.float32))

    rv = np.where(R, V, 0)
    v_ring = rv.sum(axis=ax, dtype=np.int64).astype(np.float32) / cr1.astype(np.float32)
    v_ring_max = rv.max(axis=ax).astype(np.float64)
    g_ring = np.count_nonzero(R & G, axis=ax) / cr1
    s_ring = np.where(R, S, 0).sum(axis=ax, dtype=np.int64) / cr1

    # The original's empty-ring branch, applied down the stack.
    e = cr == 0
    if e.any():
        v_ring = np.where(e, v_in, v_ring)
        v_ring_max = np.where(e, v_in.astype(np.float64), v_ring_max)
        g_ring = np.where(e, 0.0, g_ring)
        s_ring = np.where(e, 0.0, s_ring)
    return (v_in, v_max, v_std, s_in, h_in, v_ring, v_ring_max, g_ring, s_ring)


def _small_back(raw, opened):
    """Puts back into `opened`, in place, every raw white component at most SMALL
    px each way and at least MINOR each way that the opening left no pixel of.

    Each such component is read from its own SMALL x SMALL box, where its pixels
    are exactly `lab == i`: no whole-frame pass.
    """
    n, lab, st, _ = cv2.connectedComponentsWithStats(raw, 8)
    w, h = st[:, 2], st[:, 3]
    keep = (np.maximum(w, h) <= SMALL) & (np.minimum(w, h) >= MINOR)
    keep[0] = False
    ki = np.nonzero(keep)[0]
    if not len(ki):
        return opened
    H, W = raw.shape
    d = np.arange(SMALL)
    yr, xr = st[ki, 1][:, None] + d, st[ki, 0][:, None] + d
    inb = (d < h[ki][:, None])[:, :, None] & (d < w[ki][:, None])[:, None, :]
    fi = np.minimum(yr, H - 1)[:, :, None] * W + np.minimum(xr, W - 1)[:, None, :]
    mine = (np.take(lab, fi) == ki[:, None, None]) & inb
    gone = ~(mine & (np.take(opened, fi) != 0)).any(axis=(1, 2))
    opened.ravel()[fi[mine & gone[:, None, None]]] = 255
    return opened


def _groups(keys):
    """[(key, rows)] per distinct key, keys ascending and rows ascending: the
    same groups, in the same order inside each, that a dict of appends gave."""
    u, inv = np.unique(keys, return_inverse=True)
    if len(u) == 1:
        return [(u[0], np.arange(len(keys)))]
    order = np.argsort(inv, kind='stable')
    cuts = np.cumsum(np.bincount(inv, minlength=len(u)))[:-1]
    return list(zip(u, np.split(order, cuts)))


def _flat(y0, x0, gh, gw, W):
    """Flat indices of the gh x gw box at each (y0, x0) in a plane W wide."""
    return (y0[:, None] + np.arange(gh))[:, :, None] * W + (x0[:, None] + np.arange(gw))[:, None, :]


def blobs(img, cmaj=None):
    """blobs_per_candidate.blobs, same candidates in the same order, pass 2 batched too."""

    def tick(t0, name):
        if PROF is None:
            return 0.0
        t1 = time.perf_counter()
        PROF[name] = PROF.get(name, 0.0) + t1 - t0
        return t1

    _t = time.perf_counter() if PROF is not None else 0.0
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    # grass is (h > 30) & (h < 95) & (s > 60), white (v > vcut) & (s < SAT):
    # for uint8 both are ranges, as 0/255 masks
    grassu = cv2.inRange(hsv, (31, 61, 0), (94, 255, 255))
    if DV is None:
        vcut = VWHITE
    else:  # min(VWHITE, median grass V + DV), grass read every GSTEP-th px each way
        gs = v[::GSTEP, ::GSTEP][grassu[::GSTEP, ::GSTEP] > 0]
        vcut = VWHITE if not len(gs) else int(min(VWHITE, int(np.median(gs)) + DV))
    raw = cv2.inRange(hsv, (0, 0, vcut + 1), (255, SAT - 1, 255))
    white = raw
    if OPEN:
        white = cv2.morphologyEx(raw, cv2.MORPH_OPEN, K2)
        if SMALL:
            white = _small_back(raw, white)
    if GIANT:
        if white is raw:
            white = raw.copy()
        n0, lab0, st0, _ = cv2.connectedComponentsWithStats(white, 8)
        for i in np.where(st0[1:, 4] >= GIANT)[0] + 1:
            gx, gy, gw, gh = (int(q) for q in st0[i][:4])
            sub = lab0[gy : gy + gh, gx : gx + gw] == i
            vv = v[gy : gy + gh, gx : gx + gw]
            c2 = int(np.median(vv[sub][::16])) + GDV
            white[gy : gy + gh, gx : gx + gw][sub & (vv <= c2)] = 0
    n, lab, st, cent = cv2.connectedComponentsWithStats(white, 8)
    H, W = v.shape
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _t = tick(_t, 'front')

    resp = dil = None
    if cmaj is not None:
        for k in SCALES:
            se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            th = cv2.morphologyEx(v, cv2.MORPH_TOPHAT, se)
            resp = th if resp is None else np.maximum(resp, th)
        dil = cv2.dilate(resp, np.ones((2 * NMS + 1, 2 * NMS + 1), np.uint8))
    _t = tick(_t, 'tophat')

    # --- pass 1: geometry only, and no masks built for component rows -----
    # A component row carries its label id instead of a boolean array: the
    # mask and the ring both fall out of the label image once it is gathered,
    # which is cheaper than building them here and stacking them later.
    # The component rows are one vectorised filter (the same int32 products).
    st1 = st[1:n]
    xs, ys, ws_, hs_, ars_ = st1[:, 0], st1[:, 1], st1[:, 2], st1[:, 3], st1[:, 4]
    mn_ = np.minimum(ws_, hs_)
    mj_ = np.maximum(ws_, hs_)
    ci = np.nonzero((mn_ >= MINOR) & (mj_ <= MAJOR) & (ars_ >= FILL * ws_ * hs_))[0]

    # Only a component bigger than OVER x the expected ball is split at its
    # top-hat peaks.  A peak's blob is the 8-connected region of win >= s0 / 2
    # around it; for uint8 that is win >= s0 - s0 // 2, floodFill's fixed range.
    sjobs, slab = [], []
    if cmaj is not None and n > 1:
        e_maj = np.maximum(1.0, cmaj[0] * cent[1:n, 1] + cmaj[1])
        for i0 in np.nonzero(mj_ > OVER * e_maj)[0].tolist():
            i = i0 + 1
            x, y, w, hh = (int(q) for q in st[i][:4])
            rs = resp[y : y + hh, x : x + w]
            pk = (rs == dil[y : y + hh, x : x + w]) & (rs >= THR)
            pk &= raw[y : y + hh, x : x + w] != 0
            pk &= lab[y : y + hh, x : x + w] == i
            py, px = np.where(pk)
            if not len(px):
                continue
            order = np.argsort(-resp[py + y, px + x])[:PERCOMP]
            for j in order:
                cx, cy = int(px[j]) + x, int(py[j]) + y
                wy0, wy1 = max(0, cy - WIN), min(H, cy + WIN + 1)
                wx0, wx1 = max(0, cx - WIN), min(W, cx + WIN + 1)
                s0 = int(resp[cy, cx])
                mk = np.zeros((wy1 - wy0 + 2, wx1 - wx0 + 2), np.uint8)
                sa, _, _, (_, _, sw, sh) = cv2.floodFill(
                    resp[wy0:wy1, wx0:wx1], mk, (cx - wx0, cy - wy0), 0, s0 // 2, 255, FLOOD
                )
                m = mk[1:-1, 1:-1].view(bool)
                sjobs.append((wy0, wy1, wx0, wx1, cx, cy, min(sw, sh), max(sw, sh), sa, sw, sh, m))
                slab.append(i)
    _t = tick(_t, 'pass1')

    nci, ns = len(ci), len(sjobs)
    nc = nci + ns
    if not nc:
        return [], grey
    # the original's order: label by label, a component's own row before its peaks
    order = np.argsort(np.concatenate([ci + 1, np.array(slab, np.int64)]), kind='stable')

    def col(cvals, j, dt):
        a = np.empty(nc, dt)
        a[:nci] = cvals
        if ns:
            a[nci:] = [r[j] for r in sjobs]
        return a[order]

    y0 = col(np.maximum(0, ys[ci] - PAD), 0, np.int64)
    y1 = col(np.minimum(H, ys[ci] + hs_[ci] + PAD), 1, np.int64)
    x0 = col(np.maximum(0, xs[ci] - PAD), 2, np.int64)
    x1 = col(np.minimum(W, xs[ci] + ws_[ci] + PAD), 3, np.int64)
    cxs = col(cent[ci + 1, 0], 4, np.float64)
    cys = col(cent[ci + 1, 1], 5, np.float64)
    ars = col(ars_[ci], 8, np.float64)
    ws = col(ws_[ci], 9, np.int64)
    hs = col(hs_[ci], 10, np.int64)
    isplit = order >= nci
    # per row: the label id (component) or the mask (split), and (minor, major,
    # area, w, h, split) typed as the original's: np.int32 from the stats for a
    # component, Python ints for a split
    meta_c = list(zip(list(mn_[ci]), list(mj_[ci]), list(ars_[ci]), list(ws_[ci]), list(hs_[ci])))
    cid = (ci + 1).tolist()
    ident, meta = [], []
    for o in order.tolist():
        if o < nci:
            ident.append(cid[o])
            meta.append(meta_c[o] + (0.0,))
        else:
            r = sjobs[o - nci]
            ident.append(r[11])
            meta.append((r[6], r[7], r[8], r[9], r[10], 1.0))

    # --- the first-pass stages, batched per frame ------------------------
    em = np.maximum(1.0, cmaj[0] * cys + cmaj[1]) if cmaj is not None else np.ones(nc)
    TH = tophat_batch(resp, cxs, cys, em)
    integ = cv2.integral(cv2.threshold(white, 0, 1, cv2.THRESH_BINARY)[1])  # labelled = white
    boxarea = ((y1 - y0) * (x1 - x0)).astype(np.float64)
    OTH = box_mean(integ, y0, y1, x0, x1) - ars / boxarea
    FY0, FY1 = np.maximum(0, y0 - 3 * hs), np.minimum(H, y1 + 3 * hs)
    FX0, FX1 = np.maximum(0, x0 - 3 * ws), np.minimum(W, x1 + 3 * ws)
    WFAR = box_mean(integ, FY0, FY1, FX0, FX1)
    _t = tick(_t, 'batch')

    # --- pass 2a: the nine masked/ring numbers, once per group ------------
    # Every block is read from a contiguous plane through one flat index: the
    # same pixels in the same layout as a 2-D fancy index, so every reduction
    # sees identical arrays.
    bh, bw = y1 - y0, x1 - x0
    hp, sp, vp = cv2.split(hsv)
    grassb = grassu != 0
    VIN, VMAX, VSTD, SIN, HIN = (np.zeros(nc, np.float64) for _ in range(5))
    VRING, VRMAX, GRING, SRING = (np.zeros(nc, np.float64) for _ in range(4))

    def store(g, r):
        for arr, val in zip((VIN, VMAX, VSTD, SIN, HIN, VRING, VRMAX, GRING, SRING), r):
            if val is not None:
                arr[g] = val

    def gather(fi):
        return np.take(vp, fi), np.take(sp, fi), np.take(hp, fi), np.take(grassb, fi)

    idx = np.arange(nc)
    comp = idx[~isplit]
    if len(comp):
        # each component row in the smallest BUCKETS size holding its box (or the box)
        need = np.maximum(bh[comp], bw[comp])
        bk = np.asarray(BUCKETS, np.int64)
        bi = np.searchsorted(bk, need)
        bsz = np.where(bi < len(bk), bk[np.minimum(bi, len(bk) - 1)], need)
        for B in sorted(set(bsz.tolist())):
            g = comp[bsz == B]
            d = np.arange(B)
            yr, xr = y0[g][:, None] + d, x0[g][:, None] + d
            ok = (yr < y1[g][:, None])[:, :, None] & (xr < x1[g][:, None])[:, None, :]
            fi = np.minimum(yr, H - 1)[:, :, None] * W + np.minimum(xr, W - 1)[:, None, :]
            L = np.take(lab, fi)
            ids = np.array([ident[k] for k in g], np.int64)[:, None, None]
            store(g, _reduce((L == ids) & ok, (L == 0) & ok, *gather(fi)))

    spl = idx[isplit]
    if len(spl):
        for key, gi in _groups(bh[spl] * 100000 + bw[spl]):
            g = spl[gi]
            M = np.stack([ident[k] for k in g])
            fi = _flat(y0[g], x0[g], int(key) // 100000, int(key) % 100000, W)
            store(g, _reduce(M, ~M, *gather(fi)))

    _t = tick(_t, 'groups')
    if EXACT_STD:
        vf = v.astype(np.float32)
        for k in range(nc):
            m = (lab[y0[k] : y1[k], x0[k] : x1[k]] == ident[k]) if not isplit[k] else ident[k]
            VSTD[k] = float(vf[y0[k] : y1[k], x0[k] : x1[k]][m].std())

    # --- pass 2b: lap, grouped by exact box shape -------------------------
    # A stack of same-shaped boxes is bit-identical to the per-box
    # cv2.Laplacian(box).var(): same 4-neighbour kernel, same
    # BORDER_REFLECT_101 (numpy's 'reflect' does not repeat the edge sample),
    # and a variance along a contiguous last axis walks the same pairwise sum
    # numpy would walk on one box alone.
    grey32 = grey.astype(np.float32)
    LAP = np.zeros(nc, np.float64)
    y0l, x0l, y1l, x1l = y0.tolist(), x0.tolist(), y1.tolist(), x1.tolist()
    for key, g in _groups(bh * 100000 + bw):
        if len(g) < LAPMIN:
            for k in g.tolist():
                box = grey[y0l[k] : y1l[k], x0l[k] : x1l[k]]
                LAP[k] = float(cv2.Laplacian(box, cv2.CV_32F).var())
            continue
        fi = _flat(y0[g], x0[g], int(key) // 100000, int(key) % 100000, W)
        p = np.pad(np.take(grey32, fi), ((0, 0), (1, 1), (1, 1)), mode='reflect')
        lp = p[:, :-2, 1:-1] + p[:, 2:, 1:-1] + p[:, 1:-1, :-2] + p[:, 1:-1, 2:]
        lp -= 4.0 * p[:, 1:-1, 1:-1]
        LAP[g] = lp.reshape(len(g), -1).var(axis=1)

    # --- the gate insurance -----------------------------------------------
    _t = tick(_t, 'lap')
    # A component row within GATE_EPS of CON is recomputed the original way,
    # so the kept/dropped decision is the original's decision, not a batched
    # approximation of it.  In practice this fires for no rows at all.
    con = VIN - VRING
    near = idx[(~isplit) & (np.abs(con - CON) < GATE_EPS)]
    if len(near):
        vf = v.astype(np.float32)
        for k in near:
            sub = lab[y0[k] : y1[k], x0[k] : x1[k]]
            vv = vf[y0[k] : y1[k], x0[k] : x1[k]]
            m, ring = (sub == ident[k]), ~(sub > 0)
            VIN[k] = float(vv[m].mean())
            VRING[k] = float(vv[ring].mean()) if ring.any() else VIN[k]

    # --- pass 2c: the dicts, in the original's order ----------------------
    # read from Python lists: the same doubles float(arr[k]) gave
    _t = tick(_t, 'gate')
    th_list = TH.tolist()
    oth, wfar = OTH.tolist(), WFAR.tolist()
    cxl, cyl = cxs.tolist(), cys.tolist()
    vin, vring = VIN.tolist(), VRING.tolist()
    vmax, vstd, vrmax = VMAX.tolist(), VSTD.tolist(), VRMAX.tolist()
    sin_, sring, hin = SIN.tolist(), SRING.tolist(), HIN.tolist()
    gring, lapl = GRING.tolist(), LAP.tolist()
    gate = (~isplit).tolist()
    pi = math.pi
    out = []
    for k in range(nc):
        mn, mj, area, w, hh, split = meta[k]
        v_in, v_ring = vin[k], vring[k]
        c = v_in - v_ring
        if gate[k] and c < CON:
            continue
        out.append(
            {
                'th': th_list[k],
                'cx': cxl[k],
                'cy': cyl[k],
                'minor': mn,
                'major': mj,
                'area': int(area),
                'fill': area / float(max(1, w * hh)),
                'elong': mj / float(max(1, mn)),
                'circ': area / (pi * (mj / 2.0) ** 2),
                'extent': area / float(max(1, w * hh)),
                'v_in': v_in,
                'v_max': vmax[k],
                'v_std': vstd[k],
                'v_ring': v_ring,
                'v_ring_max': vrmax[k],
                'con': c,
                'rel_con': c / max(1.0, v_ring),
                's_in': sin_[k],
                's_ring': sring[k],
                'h_in': hin[k],
                'grass_ring': gring[k],
                'other_ring': oth[k],
                'white_far': wfar[k],
                'lap': lapl[k],
                'split': split,
            }
        )
    _t = tick(_t, 'dicts')
    return out, grey
