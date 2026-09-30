"""Vectorised twin of feat4.blobs - 792 ms/frame at sustained clock.

blobs spends ~77% of itself inside stats(), called 2306 times per frame for
21 numbers off a box that is usually smaller than 30x30.  sprof.py showed
the cost is reduction DISPATCH, not arithmetic: gathering vv[m] once instead
of three times saves only 10%, because the expense is the million tiny
numpy calls, not the bytes they touch.  So the only real lever is the same
one that worked on temporal_rows - keep the candidate axis as an ARRAY axis
and let each reduction happen once over an (ncand, k) block.

Three pieces move here, chosen because they are exactly batchable:

  * tophat_stats, 26.4% of stats.  Fixed 25x25 window, fixed 13x13 core,
    8 fixed DIRS samples - the same padded-gather trick as tfast.
  * white_far, 8.1%.  A mean of a binary image over a box that reaches
    130824 px; cv2.integral once per frame answers all 2306 in one go.
  * other_ring, 5.3%.  wf[box].mean() comes from the same integral, and
    m.mean() turns out to be free - it is area / boxarea, and both numbers
    are already in hand before stats is called.

Everything else is left alone.  In particular cv2.Laplacian stays: its
per-box border replication is not what a whole-image Laplacian produces at
the same pixels, so an integral-image substitution would silently change
the feature rather than speed it up.

Exactness, since a speedup that moves a feature is not a speedup:

  * zero padding cannot perturb the window mean - the padded cells add 0
    to the sum and the divisor is the TRUE clipped area, as in the original;
  * bw and core threshold at >= 0.5 * max(1, pk) >= 0.5, so a padded zero
    is False exactly as an out-of-image pixel was never counted;
  * the arm samples CLAMP rather than pad, so they read the real border
    pixel the original read;
  * win.mean() is summed in int64 here instead of float32, which differs
    from the original in the 7th significant figure and in the accurate
    direction.

bcheck.py holds this to the standard tcheck.py set for tfast: per-column
equality, then the xgboost score, then the top-200 ranked set.
"""
import math

import cv2
import numpy as np

import feat4 as F

DIRS = F.DIRS
TR = F.TR
IR = F.IR
CR = 6                      # half-width of the core window, from tophat_stats

_DX = np.array([p[0] for p in DIRS], np.int64)
_DY = np.array([p[1] for p in DIRS], np.int64)


def tophat_batch(resp, cx, cy, e_maj):
    """feat4.tophat_stats for every candidate at once, as (n, 6) float64."""
    nc = len(cx)
    out = np.zeros((nc, 6), np.float64)
    if resp is None or nc == 0:
        return out
    H, W = resp.shape
    ix = np.clip(np.round(np.asarray(cx, np.float64)), 0, W - 1).astype(np.int64)
    iy = np.clip(np.round(np.asarray(cy, np.float64)), 0, H - 1).astype(np.int64)
    pk = resp[iy, ix].astype(np.float64)
    thr = 0.5 * np.maximum(1.0, pk)

    # One padded gather of the 25x25 window for every candidate.  The core
    # is the middle 13x13 of the same block, so it costs nothing extra.
    pr = np.pad(resp, ((TR, TR), (TR, TR)))
    d = np.arange(-TR, TR + 1)
    yy = (iy[:, None] + TR) + d[None, :]
    xx = (ix[:, None] + TR) + d[None, :]
    win = pr[yy[:, :, None], xx[:, None, :]]                  # (nc, 25, 25)

    # The original clips the window at the image edge, so the divisor is the
    # clipped area - not 25*25, and not the padded area.
    wy0, wy1 = np.maximum(0, iy - TR), np.minimum(H, iy + TR + 1)
    wx0, wx1 = np.maximum(0, ix - TR), np.minimum(W, ix + TR + 1)
    warea = ((wy1 - wy0) * (wx1 - wx0)).astype(np.float64)

    wmean = win.reshape(nc, -1).sum(axis=1, dtype=np.int64) / warea
    bw = win >= thr[:, None, None]
    bwmean = bw.reshape(nc, -1).sum(axis=1, dtype=np.int64) / warea
    core = bw[:, TR - CR:TR + CR + 1, TR - CR:TR + CR + 1]
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
    s = (integ[y1, x1].astype(np.float64) - integ[y0, x1]
         - integ[y1, x0] + integ[y0, x0])
    return s / ((y1 - y0) * (x1 - x0)).astype(np.float64)


def _row(job, vf, s, h, grass, grey, th, other_ring, white_far):
    """The 21 numbers, given the three that were already batched."""
    (m, y0, y1, x0, x1, ring, cx, cy, mn, mj, area, w, hh, split) = job
    vv = vf[y0:y1, x0:x1]
    q = vv[m]                                   # gathered once, not three times
    v_in = float(q.mean())
    if ring.any():
        r = vv[ring]
        v_ring, v_ring_max = float(r.mean()), float(r.max())
        g_ring = float(grass[y0:y1, x0:x1][ring].mean())
        s_ring = float(s[y0:y1, x0:x1][ring].mean())
    else:
        v_ring = v_ring_max = v_in
        g_ring = s_ring = 0.0
    con = v_in - v_ring
    return dict(
        th=th, cx=float(cx), cy=float(cy),
        minor=mn, major=mj, area=int(area), fill=area / float(max(1, w * hh)),
        elong=mj / float(max(1, mn)), circ=area / (math.pi * (mj / 2.0) ** 2),
        extent=area / float(max(1, w * hh)),
        v_in=v_in, v_max=float(q.max()), v_std=float(q.std()),
        v_ring=v_ring, v_ring_max=v_ring_max, con=con,
        rel_con=con / max(1.0, v_ring),
        s_in=float(s[y0:y1, x0:x1][m].mean()), s_ring=s_ring,
        h_in=float(h[y0:y1, x0:x1][m].mean()),
        grass_ring=g_ring,
        other_ring=other_ring,
        white_far=white_far,
        lap=float(cv2.Laplacian(grey[y0:y1, x0:x1], cv2.CV_32F).var()),
        split=split)


def blobs(img, cmaj=None):
    """feat4.blobs, same candidates in the same order, three stages batched."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    white = ((v > 150) & (s < F.SAT)).astype(np.uint8) * 255
    white = cv2.morphologyEx(white, cv2.MORPH_OPEN, F.K2)
    grass = ((h > 30) & (h < 95) & (s > 60))
    n, lab, st, cent = cv2.connectedComponentsWithStats(white, 8)
    H, W = v.shape
    vf = v.astype(np.float32)
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    wf = (lab > 0)

    resp = None
    if cmaj is not None:
        for k in F.SCALES:
            se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            th = cv2.morphologyEx(v, cv2.MORPH_TOPHAT, se)
            resp = th if resp is None else np.maximum(resp, th)
        d = cv2.dilate(resp, np.ones((2 * F.NMS + 1, 2 * F.NMS + 1), np.uint8))
        ispeak = (resp == d) & (resp >= F.THR) & (v > 150) & (s < F.SAT)

    # --- pass 1: every candidate's geometry, no reductions yet -------------
    # `gate` marks the component-branch rows, which the original keeps only
    # if con >= CON.  Recording them in the same order and dropping them in
    # pass 2 leaves the output list identical, element for element.
    jobs, gate = [], []
    for i in range(1, n):
        x, y, w, hh, area = st[i]
        mn, mj = min(w, hh), max(w, hh)
        ok = mn >= F.MINOR and mj <= F.MAJOR and area >= F.FILL * w * hh
        if ok:
            y0, y1 = max(0, y - F.PAD), min(H, y + hh + F.PAD)
            x0, x1 = max(0, x - F.PAD), min(W, x + w + F.PAD)
            sub = lab[y0:y1, x0:x1]
            jobs.append((sub == i, y0, y1, x0, x1, ~(sub > 0),
                         cent[i][0], cent[i][1], mn, mj, area, w, hh, 0.0))
            gate.append(True)
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
            jobs.append((m, wy0, wy1, wx0, wx1, ~m, cx, cy,
                         min(sw, sh), max(sw, sh), int(m.sum()), sw, sh, 1.0))
            gate.append(False)

    if not jobs:
        return [], grey

    # --- the three batched stages -----------------------------------------
    y0 = np.array([j[1] for j in jobs], np.int64)
    y1 = np.array([j[2] for j in jobs], np.int64)
    x0 = np.array([j[3] for j in jobs], np.int64)
    x1 = np.array([j[4] for j in jobs], np.int64)
    cxs = np.array([j[6] for j in jobs], np.float64)
    cys = np.array([j[7] for j in jobs], np.float64)
    ars = np.array([j[10] for j in jobs], np.float64)
    ws = np.array([j[11] for j in jobs], np.int64)
    hs = np.array([j[12] for j in jobs], np.int64)

    em = np.maximum(1.0, cmaj[0] * cys + cmaj[1]) if cmaj is not None \
        else np.ones(len(jobs))
    TH = tophat_batch(resp, cxs, cys, em)

    integ = cv2.integral(wf.view(np.uint8))
    boxarea = ((y1 - y0) * (x1 - x0)).astype(np.float64)
    OTH = box_mean(integ, y0, y1, x0, x1) - ars / boxarea
    FY0, FY1 = np.maximum(0, y0 - 3 * hs), np.minimum(H, y1 + 3 * hs)
    FX0, FX1 = np.maximum(0, x0 - 3 * ws), np.minimum(W, x1 + 3 * ws)
    WFAR = box_mean(integ, FY0, FY1, FX0, FX1)

    th_list = TH.tolist()
    oth = OTH.tolist()
    wfar = WFAR.tolist()

    # --- pass 2: what is left, in the original's order --------------------
    out = []
    for k, job in enumerate(jobs):
        b = _row(job, vf, s, h, grass, grey, th_list[k], oth[k], wfar[k])
        if gate[k] and b['con'] < F.CON:
            continue
        out.append(b)
    return out, grey
