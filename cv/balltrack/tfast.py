"""temporal_rows, with the per-candidate Python loop taken out.

dense.py spends 39.8% of every frame in temporal_rows.  cProfile says the
arithmetic is not the problem: 5901 candidates cost 1.93 MILLION function
calls, 327 per candidate, and they are all reductions over 21-element and
25-element arrays.  np.median on 21 floats is ~60 us of dispatch around
~0.2 us of work.  The loop is paying overhead roughly 100x the arithmetic.

So this computes exactly the same 30 columns with the candidate axis kept
as an ARRAY axis: one np.median over an (ncand, 21) block instead of ncand
medians over 21, and so on for every reduction.

Two implementation notes, because both change results if done carelessly:

  * BORDERS.  The original clips each window at the tile edge.  Here the
    tiles are zero-padded by RW so every window is the same shape and can
    be gathered at once.  max() is unaffected (tiles and fgs are both >= 0,
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

Drop-in: tfast.temporal_rows(tiles, pts) for dense.temporal_rows(tiles, pts).
"""

import numpy as np

import feat_t4 as TT

RP, RW, FG = TT.RP, TT.RW, TT.FG


def _gather(padded, iy, ix, r):
    """padded[..., iy+dy, ix+dx] for every candidate, as (nc, n0, (2r+1)^2).

    padded is (n0, H2, W2) or (H2, W2); iy/ix are padded-space centres.
    """
    d = np.arange(-r, r + 1)
    yy = iy[:, None] + d[None, :]                 # (nc, 2r+1)
    xx = ix[:, None] + d[None, :]
    if padded.ndim == 2:
        out = padded[yy[:, :, None], xx[:, None, :]]          # (nc, k, k)
        return out.reshape(len(iy), -1)
    out = padded[:, yy[:, :, None], xx[:, None, :]]           # (n0, nc, k, k)
    return np.moveaxis(out, 0, 1).reshape(len(iy), padded.shape[0], -1)


def temporal_rows(tiles, pts):
    """Vectorised twin of dense.temporal_rows.  Same inputs, same 30 columns."""
    import dense as D                      # late, to avoid a circular import
    NOFF, CZERO, SCALE = D.NOFF, D.CZERO, D.SCALE
    TH, TW = D.TH, D.TW
    H, W = TH, TW

    nc = len(pts)
    ncol = len(TT.TNAMES)
    T = np.zeros((nc, ncol), np.float32)
    if nc == 0:
        return T

    bg = np.median(tiles, axis=0)
    fgs = np.clip(tiles - bg, 0, None)
    cur = tiles[CZERO]

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
    area5[~live] = 1.0                      # avoid 0/0; masked out at the end

    qx0 = np.maximum(0, ix - 1)
    qx1 = np.minimum(W, ix + 2)
    qy0 = np.maximum(0, iy - 1)
    qy1 = np.minimum(H, iy + 2)
    area3 = ((qx1 - qx0) * (qy1 - qy0)).astype(np.float64)
    area3[area3 <= 0] = 1.0

    # RW + 3, not RW: a candidate at tx = 639.7 rounds to ix = W, which the
    # original clips to a 2-wide window but a uniform gather would walk past
    # the edge of.  The extra 3 makes the widest window fit for every ix the
    # `live` test can accept, so the clamp below never fires on a candidate
    # whose row is actually used - it only keeps a dead one from raising.
    pad = RW + 3
    pt = np.pad(tiles, ((0, 0), (pad, pad), (pad, pad)))
    pf = np.pad(fgs, ((0, 0), (pad, pad), (pad, pad)))
    pb = np.pad(bg, ((pad, pad), (pad, pad)))
    pc = np.pad(cur, ((pad, pad), (pad, pad)))
    pH, pW = H + 2 * pad, W + 2 * pad
    piy = np.clip(iy + pad, RW, pH - 1 - RW)
    pix = np.clip(ix + pad, RW, pW - 1 - RW)

    # ---- the RP patch (5x5): series, fg series, bg, mad -------------------
    t5 = _gather(pt, piy, pix, RP)          # (nc, NOFF, 25)
    f5 = _gather(pf, piy, pix, RP)
    b5 = _gather(pb, piy, pix, RP)          # (nc, 25)
    c5 = _gather(pc, piy, pix, RP)

    series = t5.max(axis=2)                 # (nc, NOFF)
    fgser = f5.max(axis=2)
    v0 = series[:, CZERO]
    t_med = np.median(series, axis=1)
    t_min = series.min(axis=1)
    t_std = series.std(axis=1)
    bgmax = b5.max(axis=1)
    # Padded cells are 0 in both t5 and c5, so they add 0 to the sum; the
    # divisor is the true clipped cell count, exactly as the original's.
    mad = np.abs(t5 - c5[:, None, :]).sum(axis=(1, 2)) / (NOFF * area5)
    fg_mean = f5[:, CZERO, :].sum(axis=1) / area5

    bright = (series > 150).mean(axis=1)
    persist = series > (v0[:, None] - 25)
    persist_m = persist.mean(axis=1)
    left = series[:, :CZERO].mean(axis=1)
    right = series[:, CZERO + 1:].mean(axis=1)
    step = (series[:, min(NOFF - 1, CZERO + 1)]
            - series[:, max(0, CZERO - 1)])

    # run_around: the consecutive True run through CZERO, both directions.
    runl = np.cumprod(persist[:, CZERO - 1::-1], axis=1).sum(axis=1) \
        if CZERO > 0 else np.zeros(nc)
    runr = np.cumprod(persist[:, CZERO + 1:], axis=1).sum(axis=1) \
        if CZERO + 1 < NOFF else np.zeros(nc)
    runa = np.where(persist[:, CZERO], 1.0 + runl + runr, 0.0)

    # ---- the RW window (13x13): the moving-peak track ---------------------
    k = 2 * RW + 1
    fw = _gather(pf, piy, pix, RW)          # (nc, NOFF, 169)
    am = fw.argmax(axis=2)
    mx = fw.max(axis=2)
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
    res = np.hypot(px - (bx0[:, None] * t + bx1[:, None]),
                   py - (by0[:, None] * t + by1[:, None]))
    straight = (res * sm).sum(axis=1) / nn
    speed = np.hypot(bx0, by0)

    first = seen.argmax(axis=1)
    last = (NOFF - 1) - seen[:, ::-1].argmax(axis=1)
    r = np.arange(nc)
    disp = np.hypot(px[r, last] - px[r, first], py[r, last] - py[r, first])

    speed = np.where(ok, speed, 0.0)
    straight = np.where(ok, straight, 99.0)
    disp = np.where(ok, disp, 0.0)

    # ---- the 3x3 block: the centre of the 5x5 gather ----------------------
    cen = np.array([6, 7, 8, 11, 12, 13, 16, 17, 18])   # 3x3 inside 5x5
    q = t5[:, :, cen].max(axis=2)
    qf = f5[:, :, cen].max(axis=2)
    q0 = q[:, CZERO]
    qmed = np.median(q, axis=1)
    qbg = b5[:, cen].max(axis=1)
    qmad = (np.abs(t5[:, :, cen] - c5[:, None, cen]).sum(axis=(1, 2))
            / (NOFF * area3))

    cols = [v0, t_med, t_min, v0 - t_med, v0 - t_min, t_std,
            bright, persist_m, runa,
            fgser[:, CZERO],
            fg_mean,
            fgser[:, CZERO] / np.maximum(1.0, v0),
            bgmax, left, right, np.abs(left - right),
            step,
            mad, (fgser > FG).sum(axis=1),
            speed, straight, disp,
            q0, qmed, q0 - qmed, (q > (q0[:, None] - 25)).mean(axis=1),
            qf[:, CZERO], (qf > FG).sum(axis=1),
            qbg, qmad]
    assert len(cols) == ncol, 'column count drifted: %d vs %d' % (len(cols),
                                                                 ncol)
    out = np.stack([np.asarray(c, np.float32) for c in cols], axis=1)
    out[~live] = 0.0
    return np.ascontiguousarray(out, np.float32)
