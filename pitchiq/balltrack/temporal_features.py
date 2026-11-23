"""temporal_rows, with the per-candidate Python loop taken out.

reference/single_process_run.py spends 39.8% of every frame in temporal_rows.  cProfile says the
arithmetic is not the problem: 5901 candidates cost 1.93 MILLION function
calls, 327 per candidate, and they are all reductions over 21-element and
25-element arrays.  np.median on 21 floats is ~60 us of dispatch around
~0.2 us of work.  The loop is paying overhead roughly 100x the arithmetic.

So this computes exactly the same 30 columns with the candidate axis kept
as an ARRAY axis: one np.median over an (ncand, 21) block instead of ncand
medians over 21, and so on for every reduction.

Two implementation notes, because both change results if done carelessly:

  * BORDERS.  The original clips each window at the tile edge.  Here every
    window is read through a flat index whose off-tile cells read a 0, as
    if the tiles were zero-padded, so every window is the same shape and
    can be gathered at once.  max() is unaffected (tiles and fgs are both >= 0,
    so a padded zero never wins).  mean() WOULD be, so every mean divides
    by the true clipped area, computed the same way the original computes
    its slice bounds.  The padded cells contribute 0 to the sum, so the
    result is identical, not approximate.

  * THE LINE FIT.  The original calls np.linalg.lstsq twice per candidate -
    ~4000 SVDs per frame, 11% of the stage - to fit 2 parameters.  Here it
    is the closed-form least squares, masked to the same `seen` offsets.
    Same estimator, different rounding: lstsq goes through an SVD in
    float32, this accumulates in float64.  Agreement is ~1e-6 relative,
    which is checked rather than assumed - see tcheck.py.

Faster, same bits (2026-11-23, session step 3.13: about 1.9x).  The tiles
are whole grey levels, so the plate, the 5x5 blocks and the moving-peak
search run on uint8: medians, maxima, a saturating subtract and an argmax of
integers are the same integers, and every sum of them is under 2^24, so the
float32 sums taken before were exact and an integer sum gives the same
number.  Each window is read through one flat index into a row with a
trailing 0 instead of padding four whole stacks, and the plate's median is a
comparator network on whole rows instead of a partition - the same middle
value, checked on every 0/1 input, which by the 0-1 principle covers all.

Drop-in: temporal_features.temporal_rows(tiles, pts) for single_process_run.temporal_rows(tiles, pts).
"""

import cv2
import numpy as np

from .candidate_features import CZERO, NOFF, SCALE, TH, TW

RP = 2  # patch radius in tile pixels (~15 source px)
RW = 6  # search radius for the moving-peak track
FG = 25  # a pixel this far above the background plate is
# something that was not there a moment ago

TNAMES = [
    't_v0',
    't_med',
    't_min',
    't_blink',
    't_dip',
    't_std',
    't_nbright',
    't_persist',
    't_run0',
    't_fg0',
    't_fgmean',
    't_fgratio',
    't_bgmax',
    't_left',
    't_right',
    't_asym',
    't_slope',
    't_mad',
    't_nfg',
    't_speed',
    't_straight',
    't_disp',
    # the same questions asked of a 3x3 tile patch (~9 source px), so
    # that two candidates 15 px apart stop sharing one answer
    't1_v0',
    't1_med',
    't1_blink',
    't1_persist',
    't1_fg0',
    't1_nfg',
    't1_bgmax',
    't1_mad',
]


def _windows(cyc, cxc, r, H, W):
    """(nc, (2r+1)^2) flat indices of the window around each clamped centre.

    Row-major, into an H*W plane with one more cell at the end: a window cell
    off the tile points at that cell, which holds 0 (the zero padding).
    """
    d = np.arange(-r, r + 1)
    yy, xx = cyc[:, None] + d, cxc[:, None] + d
    ok = ((yy >= 0) & (yy < H))[:, :, None] & ((xx >= 0) & (xx < W))[:, None, :]
    f = np.clip(yy, 0, H - 1)[:, :, None] * W + np.clip(xx, 0, W - 1)[:, None, :]
    return np.where(ok, f, H * W).reshape(len(cyc), -1)


def _median_ops(n):
    """A comparator network whose output n // 2 is the median of n inputs (n odd).

    Batcher's odd-even merge sort on the next power of two, cut to the first n
    wires (the missing ones read as +inf, which no comparator moves), then
    pruned to the comparators that one output needs.  Returns
    [(i, j, keep_min, keep_max)]: the min goes to wire i, the max to wire j.
    """

    def merge(lo, hi, r):
        step = r * 2
        if step < hi - lo:
            yield from merge(lo, hi, step)
            yield from merge(lo + r, hi, step)
            yield from [(i, i + r) for i in range(lo + r, hi - r, step)]
        else:
            yield (lo, lo + r)

    def msort(lo, hi):
        if hi - lo >= 1:
            mid = lo + (hi - lo) // 2
            yield from msort(lo, mid)
            yield from msort(mid + 1, hi)
            yield from merge(lo, hi, 1)

    p = 1
    while p < n:
        p *= 2
    need, ops = {n // 2}, []
    for i, j in reversed([(i, j) for i, j in msort(0, p - 1) if j < n]):
        if i in need or j in need:
            ops.append((i, j, i in need, j in need))
            need |= {i, j}
    return ops[::-1]


assert NOFF % 2 == 1, 'the plate takes the middle tile by size'
# 91 comparators (162 min/max) for 21 tiles; checked on all 2^21 0/1 inputs
MEDIAN_OPS = _median_ops(NOFF)


def _median_rows(X):
    """Each column's median of the NOFF rows of X, as min/max of whole rows.

    The same value as np.partition(X, NOFF // 2, axis=0)[NOFF // 2]: the network
    picks the middle one by size exactly (a 0/1 check of every input covers all
    inputs, by the 0-1 principle), ~13x faster on 50k columns.
    """
    a = list(X)
    for i, j, lo, hi in MEDIAN_OPS:
        x, y = a[i], a[j]
        if lo:
            a[i] = np.minimum(x, y)
        if hi:
            a[j] = np.maximum(x, y)
    return a[NOFF // 2]


def _plate(tu, pts, scale, H, W):
    """The background plate: the per-pixel median of the 21 tiles, but only
    where some candidate's RW window can read it (about a fifth of the tile).
    Elsewhere it is 0 and nothing ever gathers it. A median of 21 values is
    the 11th by size, so the selection returns exactly np.median's answer;
    the rows come back bit for bit (checked on fm01 anchors, 37 -> 5 ms).

    tu is the (NOFF, H*W + 1) uint8 tile stack; the plate is one row like it.
    """
    iy = np.clip(np.rint(pts[:, 1] / scale).astype(np.int64), 0, H - 1)
    ix = np.clip(np.rint(pts[:, 0] / scale).astype(np.int64), 0, W - 1)
    m = np.zeros((H, W), np.uint8)
    m[iy, ix] = 1
    # centres clipped onto the tile only widen the window, never miss a pixel
    m = cv2.dilate(m, np.ones((2 * RW + 1, 2 * RW + 1), np.uint8)).astype(bool).ravel()
    bg = np.zeros(H * W + 1, np.uint8)
    bg[: H * W][m] = _median_rows(tu[:, : H * W][:, m])
    return bg


def temporal_rows(tiles, pts):
    """Vectorised twin of single_process_run.temporal_rows.  Same inputs, same 30 columns.

    tiles: the (NOFF, TH, TW) float32 stack from candidate_features.tiles_for,
    whole grey levels 0..255.
    """
    H, W = TH, TW

    nc = len(pts)
    ncol = len(TNAMES)
    T = np.zeros((nc, ncol), np.float32)
    if nc == 0:
        return T

    HW = H * W
    tu = np.empty((NOFF, HW + 1), np.uint8)  # the tiles as uint8, each with a trailing 0
    tu[:, HW] = 0
    np.copyto(tu[:, :HW], tiles.reshape(NOFF, HW), casting='unsafe')
    bg = _plate(tu, pts, SCALE, H, W)

    tx = pts[:, 0] / SCALE
    ty = pts[:, 1] / SCALE
    ix = np.rint(tx).astype(np.int64)
    iy = np.rint(ty).astype(np.int64)

    # The original's clipped bounds, kept verbatim so the mean denominators
    # and the "skip this candidate" test match exactly.
    x0 = np.maximum(0, ix - RP)
    x1 = np.minimum(W, ix + RP + 1)
    y0 = np.maximum(0, iy - RP)
    y1 = np.minimum(H, iy + RP + 1)
    live = (x1 > x0) & (y1 > y0)
    area5 = ((x1 - x0) * (y1 - y0)).astype(np.float64)
    area5[~live] = 1.0  # avoid 0/0; masked out at the end

    qx0 = np.maximum(0, ix - 1)
    qx1 = np.minimum(W, ix + 2)
    qy0 = np.maximum(0, iy - 1)
    qy1 = np.minimum(H, iy + 2)
    area3 = ((qx1 - qx0) * (qy1 - qy0)).astype(np.float64)
    area3[area3 <= 0] = 1.0

    # The window centres, clamped as the original clamped them inside RW + 3
    # of zero padding: a candidate at tx = 639.7 rounds to ix = W, which the
    # original clips to a 2-wide window.  The clamp never fires on a candidate
    # whose row is actually used - it only keeps a dead one in bounds.
    pad = RW + 3
    cyc = np.clip(iy + pad, RW, H + 2 * pad - 1 - RW) - pad
    cxc = np.clip(ix + pad, RW, W + 2 * pad - 1 - RW) - pad

    # ---- the RP patch (5x5): series, fg series, bg, mad -------------------
    # Read as uint8, since the tiles are whole grey levels: every max is the
    # same integer, the foreground is the saturating t - bg (= max(t - bg, 0)),
    # and every sum below is of integers under 2^24, so the float32 sums taken
    # before were exact and an integer sum gives the same number.
    i5 = _windows(cyc, cxc, RP, H, W)
    rows = (np.arange(NOFF) * (HW + 1))[None, :, None]
    t5 = np.take(tu, i5[:, None, :] + rows)  # (nc, NOFF, 25)
    b5 = np.take(bg, i5)  # (nc, 25)
    f5 = np.maximum(t5, b5[:, None, :])
    f5 -= b5[:, None, :]
    d5 = np.abs(t5.astype(np.int16) - t5[:, CZERO : CZERO + 1, :])  # |t - cur|

    series = t5.max(axis=2).astype(np.float32)  # (nc, NOFF)
    fgser = f5.max(axis=2).astype(np.float32)
    v0 = series[:, CZERO]
    t_med = np.median(series, axis=1)
    t_min = series.min(axis=1)
    t_std = series.std(axis=1)
    bgmax = b5.max(axis=1).astype(np.float32)
    # Padded cells are 0 in both t5 and cur, so they add 0 to the sum; the
    # divisor is the true clipped cell count, exactly as the original's.
    mad = d5.sum(axis=(1, 2), dtype=np.int64) / (NOFF * area5)
    fg_mean = f5[:, CZERO, :].sum(axis=1, dtype=np.int64) / area5

    bright = (series > 150).mean(axis=1)
    persist = series > (v0[:, None] - 25)
    persist_m = persist.mean(axis=1)
    left = series[:, :CZERO].mean(axis=1)
    right = series[:, CZERO + 1 :].mean(axis=1)
    step = series[:, min(NOFF - 1, CZERO + 1)] - series[:, max(0, CZERO - 1)]

    # run_around: the consecutive True run through CZERO, both directions.
    runl = (
        np.cumprod(persist[:, CZERO - 1 :: -1], axis=1).sum(axis=1) if CZERO > 0 else np.zeros(nc)
    )
    runr = (
        np.cumprod(persist[:, CZERO + 1 :], axis=1).sum(axis=1)
        if CZERO + 1 < NOFF
        else np.zeros(nc)
    )
    runa = np.where(persist[:, CZERO], 1.0 + runl + runr, 0.0)

    # ---- the RW window (13x13): the moving-peak track ---------------------
    # Tile by tile: the whole tile's uint8 foreground (saturating), then each
    # candidate's window of it.  The argmax of the same integers in the same
    # order is the same first maximum.
    k = 2 * RW + 1
    iw = _windows(cyc, cxc, RW, H, W)
    am = np.empty((nc, NOFF), np.int64)
    mx = np.empty((nc, NOFF), np.uint8)
    fg = np.empty(HW + 1, np.uint8)
    r = np.arange(nc)
    for o in range(NOFF):
        cv2.subtract(tu[o], bg, dst=fg)
        fw = np.take(fg, iw)
        a = fw.argmax(axis=1)
        am[:, o] = a
        mx[:, o] = fw[r, a]
    seen = mx > FG
    px = (am % k).astype(np.float64) + (ix - RW)[:, None]
    py = (am // k).astype(np.float64) + (iy - RW)[:, None]

    t = np.arange(NOFF, dtype=np.float64)[None, :]
    sm = seen.astype(np.float64)
    n = sm.sum(axis=1)
    ok = n >= 4
    nn = np.where(ok, n, 1.0)
    St = (t * sm).sum(axis=1)
    Stt = (t * t * sm).sum(axis=1)
    den = nn * Stt - St * St
    den = np.where(np.abs(den) < 1e-12, 1.0, den)

    def _fit(p):
        Sp = (p * sm).sum(axis=1)
        Stp = (t * p * sm).sum(axis=1)
        slope = (nn * Stp - St * Sp) / den
        icept = (Sp - slope * St) / nn
        return slope, icept

    bx0, bx1 = _fit(px)
    by0, by1 = _fit(py)
    res = np.hypot(px - (bx0[:, None] * t + bx1[:, None]), py - (by0[:, None] * t + by1[:, None]))
    straight = (res * sm).sum(axis=1) / nn
    speed = np.hypot(bx0, by0)

    first = seen.argmax(axis=1)
    last = (NOFF - 1) - seen[:, ::-1].argmax(axis=1)
    disp = np.hypot(px[r, last] - px[r, first], py[r, last] - py[r, first])

    speed = np.where(ok, speed, 0.0)
    straight = np.where(ok, straight, 99.0)
    disp = np.where(ok, disp, 0.0)

    # ---- the 3x3 block: the centre of the 5x5 gather ----------------------
    cen = np.array([6, 7, 8, 11, 12, 13, 16, 17, 18])  # 3x3 inside 5x5
    q = t5[:, :, cen].max(axis=2).astype(np.float32)
    qf = f5[:, :, cen].max(axis=2).astype(np.float32)
    q0 = q[:, CZERO]
    qmed = np.median(q, axis=1)
    qbg = b5[:, cen].max(axis=1).astype(np.float32)
    qmad = d5[:, :, cen].sum(axis=(1, 2), dtype=np.int64) / (NOFF * area3)

    cols = [
        v0,
        t_med,
        t_min,
        v0 - t_med,
        v0 - t_min,
        t_std,
        bright,
        persist_m,
        runa,
        fgser[:, CZERO],
        fg_mean,
        fgser[:, CZERO] / np.maximum(1.0, v0),
        bgmax,
        left,
        right,
        np.abs(left - right),
        step,
        mad,
        (fgser > FG).sum(axis=1),
        speed,
        straight,
        disp,
        q0,
        qmed,
        q0 - qmed,
        (q > (q0[:, None] - 25)).mean(axis=1),
        qf[:, CZERO],
        (qf > FG).sum(axis=1),
        qbg,
        qmad,
    ]
    assert len(cols) == ncol, 'column count drifted: %d vs %d' % (len(cols), ncol)
    out = np.stack([np.asarray(c, np.float32) for c in cols], axis=1)
    out[~live] = 0.0
    return np.ascontiguousarray(out, np.float32)
