"""Temporal features for every candidate, from the stabilised clip sheets.

The still-frame ranker tops out at 23% because a ball and a sock look the
same in one frame.  They do not look the same across twenty-one: the sheets
built for the labelling page are already warped into each bench frame's own
coordinates, so the pitch stands still and only players and the ball move.
A painted line is bright at all 21 offsets; a ball is bright at offset 0 and
gone by offset 3.

That makes a temporal median across the 21 tiles a background plate of the
static scene, and everything that matters is what stands above it.

No video decode: still4.npz already stores (frame, cx, cy) per row, so these
features attach to the rows that exist, by index.  Sheet i//5, row i%5,
column = offset index, tile 640x360, so source pixels divide by three.

    python feat_t4.py            # -> temporal4.npz
"""
import io
import json
import math
import os
import time

import cv2
import numpy as np

SP = ('C:/Users/alexv/AppData/Local/Temp/claude/'
      'C--Users-alexv-Desktop-Repos-PitchIQ/'
      '7f31f241-b244-4121-aa07-4ef3b9736b2a/scratchpad')
S2 = os.path.dirname(os.path.abspath(__file__))
CLIPS = os.path.join(SP, 'clips')
NPZ = os.path.join(S2, 'still4.npz')
OUT = os.path.join(S2, 'temporal4.npz')

TW, TH, PER = 640, 360, 5
SCALE = 1920.0 / TW          # 3.0
RP = 2                       # patch radius in tile pixels (~15 source px)
RW = 6                       # search radius for the moving-peak track
FG = 25                      # a pixel this far above the background plate is
                             # something that was not there a moment ago

TNAMES = ['t_v0', 't_med', 't_min', 't_blink', 't_dip', 't_std',
          't_nbright', 't_persist', 't_run0',
          't_fg0', 't_fgmean', 't_fgratio', 't_bgmax',
          't_left', 't_right', 't_asym', 't_slope', 't_mad', 't_nfg',
          't_speed', 't_straight', 't_disp',
          # the same questions asked of a 3x3 tile patch (~9 source px), so
          # that two candidates 15 px apart stop sharing one answer
          't1_v0', 't1_med', 't1_blink', 't1_persist', 't1_fg0', 't1_nfg',
          't1_bgmax', 't1_mad']


def run_around(mask, c):
    """Length of the consecutive True run containing index c (0 if not set)."""
    if not mask[c]:
        return 0
    n = 1
    i = c - 1
    while i >= 0 and mask[i]:
        n += 1; i -= 1
    i = c + 1
    while i < len(mask) and mask[i]:
        n += 1; i += 1
    return n


def main():
    meta = json.load(io.open(os.path.join(CLIPS, 'clip.json'), encoding='utf-8'))
    offs = meta['offs']
    ncol = len(offs)
    czero = offs.index(0)
    assert meta['w'] == TW and meta['per'] == PER, 'clip geometry moved'

    z = np.load(NPZ, allow_pickle=True)
    f, cx, cy = z['f'], z['cx'], z['cy']
    n = len(f)
    T = np.zeros((n, len(TNAMES)), np.float32)
    ok = np.zeros(n, np.int8)

    order = np.argsort(f, kind='stable')
    bounds = {}
    for idx in order:
        bounds.setdefault(int(f[idx]), []).append(idx)

    t0 = time.time()
    cur_sheet, sheet = -1, None
    for k, fi in enumerate(sorted(bounds)):
        sh, row = fi // PER, fi % PER
        if sh != cur_sheet:
            p = os.path.join(CLIPS, 'clip%03d.jpg' % sh)
            sheet = cv2.imread(p, cv2.IMREAD_COLOR)
            assert sheet is not None, 'missing %s' % p
            cur_sheet = sh
        if meta['flags'].get(str(fi), 1) == 0:
            continue
        y0 = row * TH
        band = sheet[y0:y0 + TH]
        tiles = np.stack([cv2.cvtColor(band[:, c * TW:(c + 1) * TW],
                                       cv2.COLOR_BGR2GRAY)
                          for c in range(ncol)]).astype(np.float32)
        bg = np.median(tiles, axis=0)
        fgs = np.clip(tiles - bg, 0, None)
        cur = tiles[czero]

        H, W = TH, TW
        for idx in bounds[fi]:
            tx, ty = cx[idx] / SCALE, cy[idx] / SCALE
            ix, iy = int(round(tx)), int(round(ty))
            x0, x1 = max(0, ix - RP), min(W, ix + RP + 1)
            yy0, yy1 = max(0, iy - RP), min(H, iy + RP + 1)
            if x1 <= x0 or yy1 <= yy0:
                continue
            series = tiles[:, yy0:yy1, x0:x1].reshape(ncol, -1).max(axis=1)
            fgser = fgs[:, yy0:yy1, x0:x1].reshape(ncol, -1).max(axis=1)
            v0 = float(series[czero])
            t_med = float(np.median(series))
            t_min = float(series.min())
            bgmax = float(bg[yy0:yy1, x0:x1].max())
            bright = series > 150
            persist = series > (v0 - 25)
            left = float(series[:czero].mean())
            right = float(series[czero + 1:].mean())
            mad = float(np.abs(tiles[:, yy0:yy1, x0:x1]
                               - cur[yy0:yy1, x0:x1]).mean())

            # where the brightest new thing sits at each offset: a ball walks
            # a short straight line through offset 0, scenery does not move
            wx0, wx1 = max(0, ix - RW), min(W, ix + RW + 1)
            wy0, wy1 = max(0, iy - RW), min(H, iy + RW + 1)
            win = fgs[:, wy0:wy1, wx0:wx1]
            flat = win.reshape(ncol, -1)
            am = flat.argmax(axis=1)
            pw = wx1 - wx0
            px = (am % pw).astype(np.float32) + wx0
            py = (am // pw).astype(np.float32) + wy0
            seen = flat.max(axis=1) > FG
            if seen.sum() >= 4:
                tt = np.arange(ncol, dtype=np.float32)[seen]
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

            # a tighter patch: at 1/3 scale RP=2 spans 15 source pixels, wide
            # enough that neighbouring candidates read the same series
            qx0, qx1 = max(0, ix - 1), min(W, ix + 2)
            qy0, qy1 = max(0, iy - 1), min(H, iy + 2)
            q = tiles[:, qy0:qy1, qx0:qx1].reshape(ncol, -1).max(axis=1)
            qf = fgs[:, qy0:qy1, qx0:qx1].reshape(ncol, -1).max(axis=1)
            q0 = float(q[czero])
            qmed = float(np.median(q))

            T[idx] = [v0, t_med, t_min, v0 - t_med, v0 - t_min,
                      float(series.std()),
                      bright.mean(), persist.mean(),
                      run_around(persist, czero),
                      float(fgser[czero]),
                      float(fgs[czero, yy0:yy1, x0:x1].mean()),
                      float(fgser[czero]) / max(1.0, v0),
                      bgmax, left, right, abs(left - right),
                      float(series[min(ncol - 1, czero + 1)]
                            - series[max(0, czero - 1)]),
                      mad, float((fgser > FG).sum()),
                      speed, straight, disp,
                      q0, qmed, q0 - qmed, float((q > (q0 - 25)).mean()),
                      float(qf[czero]), float((qf > FG).sum()),
                      float(bg[qy0:qy1, qx0:qx1].max()),
                      float(np.abs(tiles[:, qy0:qy1, qx0:qx1]
                                   - cur[qy0:qy1, qx0:qx1]).mean())]
            ok[idx] = 1
        if k % 25 == 24:
            print('  %d/%d frames  %.0fs' % (k + 1, len(bounds), time.time() - t0),
                  flush=True)

    np.savez_compressed(OUT, T=T, ok=ok, tnames=np.array(TNAMES))
    print()
    print('rows with temporal features: %d / %d' % (int(ok.sum()), n))
    y = z['y'].astype(int)
    for nm in ('t_blink', 't_persist', 't_fg0', 't_nfg', 't_speed',
               't1_blink', 't1_persist', 't1_fg0'):
        i = TNAMES.index(nm)
        p = T[(y == 1) & (ok == 1), i]
        q = T[(y == 0) & (ok == 1), i]
        print('  %-10s positives %8.2f    negatives %8.2f'
              % (nm, float(p.mean()), float(q.mean())))
    print('saved %s  %.1f s' % (OUT, time.time() - t0))


if __name__ == '__main__':
    main()
