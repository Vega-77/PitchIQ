"""The union generator: component centroids, plus peaks inside merged blobs.

gates.py walked feat2.py's five gates over all 163 hand-marked balls and found
the loss is not spread across them.  open, size and fill lose nothing at all;
threshold loses one and contrast loses one.  Seventeen of the nineteen die at
the connected-component stage, where white pixels DO survive within 8 px of the
ball but no component's CENTROID lands there.  Splitting those 17 by hand gives
two populations:

    merged with a player   8 cases, centroid 9-26 px off, blob area 122-1400.
                           Ball and sock share one component and the centroid
                           sits between them.
    swallowed whole        7 cases, area 100k-150k px, up to 1889 px across.
                           A white region covering a tenth of the frame, in
                           runs - test frames 46, 47, 48 consecutively.

No threshold fixes either: the ball's pixels are already in the mask.  What
fails is the assumption that one component means one object.

So candidates come from two sources now and the union is taken:

    centroid   exactly feat2.py's generator, unchanged, so nothing regresses.
    peak       local maxima of a white top-hat, but ONLY inside components
               already too big to be a ball at that row (major > 1.3x the
               size-vs-row fit), and at most 12 per component.

The top-hat is what makes this work: it subtracts a morphological opening, so
anything WIDER than its structuring element goes to zero while a ball keeps its
full peak.  A ball resting against a sock survives; the sock does not.

Both bounds were measured, not chosen.  Unrestricted top-hat peaks reach 46/47
on test but cost 4723 candidates a frame against 855 - 5.7x the haystack for
the ranker.  Restricting to oversized components and capping at 12 holds 45/47
at 1844.  Capping lower is cheaper and loses balls fast (6 per component gives
42/47), and ranking the peaks by their own top-hat response does not help: the
ball sits at median rank 1017 of 4723, so there is no cheap prefix to keep.

A split candidate gets its own mask - the top-hat response re-thresholded at
half the peak inside a 25 px window - so its ring measures contrast against the
SOCK rather than against the grass beyond the merged blob.  That is a different
measurement from a centroid candidate's, so is_split is a feature and the
ranker can learn separate rules rather than being told they are the same thing.

One thing fixed in passing: feat2.py fits size-vs-row on every ball label,
test pool included.  Two parameters is a small leak but a free one to remove,
so the fit here is train-only.

    python feat4.py            # -> still4.npz
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
VIDEO = os.environ.get('PIQ_VIDEO', 'C:/Users/alexv/Downloads/'
                       '8-26-26 Scrimmage v Gov Livingston - Tactical.mp4')
OUT = os.path.join(S2, 'still4.npz')

POS_TOL = 8.0
AMB_TOL = 20.0
PAD = 4
CROP = 32
NEG_CROP_KEEP = 0.03

SAT = 60                  # white is v>150 and s<SAT.  satsweep.py measured
                          # this against 40/45/50/55/60/65/70: the shipped
                          # 70 admits sun-washed turf, which carries the
                          # SAME hue as grass (46.1 vs 46.8) and so cannot
                          # be excluded by hue at all.  60 beats 70 on both
                          # pools at once - train 109/116 vs 106, test
                          # 42/47 vs 38 - and costs 684 candidates a frame
                          # instead of 855.  Tighter is not free: 45 breaks
                          # every flood but drops train to 102.
MINOR, MAJOR, FILL, CON = 2, 60, 0.30, 10.0
SCALES = (9, 17)          # top-hat structuring elements: a far ball and a near one
NMS = 4                   # peak suppression radius, source pixels
THR = 25                  # minimum top-hat response
OVER = 1.3                # a component this many times the expected ball is merged
PERCOMP = 12              # at most this many peaks from one merged component
WIN = 12                  # half-window for re-thresholding a split candidate

NAMES = ['minor', 'major', 'area', 'fill', 'elong', 'circ', 'extent',
         'v_in', 'v_max', 'v_std', 'v_ring', 'v_ring_max', 'con', 'rel_con',
         's_in', 's_ring', 'h_in', 'grass_ring', 'other_ring', 'white_far',
         'lap', 'cx_n', 'cy_n',
         'major_over_exp', 'minor_over_exp', 'area_over_exp',
         'nn_dist', 'n_within_100', 'n_cands', 'is_split',
         'th_peak', 'th_ratio', 'th_r', 'th_over', 'th_iso', 'th_frac']

K2 = np.ones((2, 2), np.uint8)


DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
TR = 12          # half-window for the shared top-hat readings
IR = 4           # radius at which the response falloff is sampled


def tophat_stats(resp, cx, cy, e_maj):
    """Six numbers read off the top-hat response, identically for both sources.

    A component candidate and a split candidate disagree about what their ring
    contains - grass for one, a sock for the other - so every contrast feature
    means a different thing depending on where the candidate came from.  These
    do not: the response is the same operator everywhere, it already has the
    surround subtracted, and it is blind to whether anything is connected to
    anything.  They give the ranker one vocabulary that covers both.
    """
    if resp is None:
        return [0.0] * 6
    H, W = resp.shape
    ix = min(max(int(round(cx)), 0), W - 1)
    iy = min(max(int(round(cy)), 0), H - 1)
    pk = float(resp[iy, ix])
    wy0, wy1 = max(0, iy - TR), min(H, iy + TR + 1)
    wx0, wx1 = max(0, ix - TR), min(W, ix + TR + 1)
    win = resp[wy0:wy1, wx0:wx1].astype(np.float32)
    bw = win >= 0.5 * max(1.0, pk)
    ry0, ry1 = max(0, iy - 6), min(H, iy + 7)
    rx0, rx1 = max(0, ix - 6), min(W, ix + 7)
    core = resp[ry0:ry1, rx0:rx1] >= 0.5 * max(1.0, pk)
    rr = math.sqrt(max(1, int(core.sum())) / math.pi)
    arm = [float(resp[min(max(iy + dy * IR, 0), H - 1),
                      min(max(ix + dx * IR, 0), W - 1)]) for dx, dy in DIRS]
    return [pk, pk / max(1.0, float(win.mean())), rr,
            rr / max(0.5, 0.5 * e_maj), float(np.std(arm)) / max(1.0, pk),
            float(bw.mean())]


def stats(m, y0, y1, x0, x1, ring, vf, s, h, grass, wf, grey, cx, cy,
          mn, mj, area, w, hh, split, resp=None, cmaj=None):
    """The 21 appearance numbers, from a mask and its ring, for either source."""
    vv = vf[y0:y1, x0:x1]
    v_in = float(vv[m].mean())
    if ring.any():
        v_ring = float(vv[ring].mean())
        v_ring_max = float(vv[ring].max())
        g_ring = float(grass[y0:y1, x0:x1][ring].mean())
        s_ring = float(s[y0:y1, x0:x1][ring].mean())
    else:
        v_ring, v_ring_max, g_ring, s_ring = v_in, v_in, 0.0, 0.0
    H, W = vf.shape
    fy0, fy1 = max(0, y0 - 3 * hh), min(H, y1 + 3 * hh)
    fx0, fx1 = max(0, x0 - 3 * w), min(W, x1 + 3 * w)
    con = v_in - v_ring
    e_maj = max(1.0, cmaj[0] * cy + cmaj[1]) if cmaj is not None else 1.0
    return dict(
        th=tophat_stats(resp, cx, cy, e_maj),
        cx=float(cx), cy=float(cy),
        minor=mn, major=mj, area=int(area), fill=area / float(max(1, w * hh)),
        elong=mj / float(max(1, mn)), circ=area / (math.pi * (mj / 2.0) ** 2),
        extent=area / float(max(1, w * hh)),
        v_in=v_in, v_max=float(vv[m].max()), v_std=float(vv[m].std()),
        v_ring=v_ring, v_ring_max=v_ring_max, con=con,
        rel_con=con / max(1.0, v_ring),
        s_in=float(s[y0:y1, x0:x1][m].mean()), s_ring=s_ring,
        h_in=float(h[y0:y1, x0:x1][m].mean()),
        grass_ring=g_ring,
        other_ring=float(wf[y0:y1, x0:x1].mean()) - float(m.mean()),
        white_far=float(wf[fy0:fy1, fx0:fx1].mean()),
        lap=float(cv2.Laplacian(grey[y0:y1, x0:x1], cv2.CV_32F).var()),
        split=split)


def blobs(img, cmaj=None):
    """Centroid candidates, and - once the size fit is known - split peaks."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    white = ((v > 150) & (s < SAT)).astype(np.uint8) * 255
    white = cv2.morphologyEx(white, cv2.MORPH_OPEN, K2)
    grass = ((h > 30) & (h < 95) & (s > 60))
    n, lab, st, cent = cv2.connectedComponentsWithStats(white, 8)
    H, W = v.shape
    vf = v.astype(np.float32)
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    wf = (lab > 0)

    resp = None
    if cmaj is not None:
        for k in SCALES:
            se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            th = cv2.morphologyEx(v, cv2.MORPH_TOPHAT, se)
            resp = th if resp is None else np.maximum(resp, th)
        d = cv2.dilate(resp, np.ones((2 * NMS + 1, 2 * NMS + 1), np.uint8))
        ispeak = (resp == d) & (resp >= THR) & (v > 150) & (s < SAT)

    out = []
    for i in range(1, n):
        x, y, w, hh, area = st[i]
        mn, mj = min(w, hh), max(w, hh)
        ok = mn >= MINOR and mj <= MAJOR and area >= FILL * w * hh
        if ok:
            y0, y1 = max(0, y - PAD), min(H, y + hh + PAD)
            x0, x1 = max(0, x - PAD), min(W, x + w + PAD)
            sub = lab[y0:y1, x0:x1]
            m, ring = sub == i, ~(sub > 0)
            b = stats(m, y0, y1, x0, x1, ring, vf, s, h, grass, wf, grey,
                      cent[i][0], cent[i][1], mn, mj, area, w, hh, 0.0,
                      resp, cmaj)
            if b['con'] >= CON:
                out.append(b)
        if cmaj is None:
            continue
        e_maj = max(1.0, cmaj[0] * cent[i][1] + cmaj[1])
        if mj <= OVER * e_maj:
            continue
        pk = ispeak[y:y + hh, x:x + w] & (lab[y:y + hh, x:x + w] == i)
        py, px = np.where(pk)
        if not len(px):
            continue
        order = np.argsort(-resp[py + y, px + x])[:PERCOMP]
        for j in order:
            cx, cy = int(px[j]) + x, int(py[j]) + y
            wy0, wy1 = max(0, cy - WIN), min(H, cy + WIN + 1)
            wx0, wx1 = max(0, cx - WIN), min(W, cx + WIN + 1)
            win = resp[wy0:wy1, wx0:wx1]
            bw = (win >= 0.5 * float(resp[cy, cx])).astype(np.uint8)
            n2, l2 = cv2.connectedComponents(bw, 8)
            m = (l2 == l2[cy - wy0, cx - wx0])
            ys2, xs2 = np.where(m)
            sw = int(xs2.max() - xs2.min()) + 1
            sh = int(ys2.max() - ys2.min()) + 1
            out.append(stats(m, wy0, wy1, wx0, wx1, ~m, vf, s, h, grass, wf,
                             grey, cx, cy, min(sw, sh), max(sw, sh),
                             int(m.sum()), sw, sh, 1.0, resp, cmaj))
    return out, grey


def collect(use, frames, cmaj):
    """One decode of the video, returning every frame's candidate list."""
    cap = cv2.VideoCapture(VIDEO)
    assert cap.isOpened(), 'cannot open video'
    per_frame, t0 = [], time.time()
    for k, L in enumerate(use):
        f = frames[L['i']]
        cap.set(cv2.CAP_PROP_POS_FRAMES, f['abs'])
        ok, img = cap.read()
        if not ok:
            print('  frame %d abs %d unreadable' % (L['i'], f['abs']))
            continue
        bs, grey = blobs(img, cmaj)
        per_frame.append((L, bs, grey))
        if k % 50 == 49:
            print('  blobs %d/%d  %.0fs' % (k + 1, len(use), time.time() - t0),
                  flush=True)
    cap.release()
    return per_frame


def main():
    man = json.load(io.open(os.path.join(SP, 'label', 'manifest.json'),
                            encoding='utf-8'))
    frames = {f['i']: f for f in man['frames']}
    labdir = os.path.join(S2, 'lab5', 'labels')
    labels = {}
    for fn in sorted(os.listdir(labdir)):
        L = json.load(io.open(os.path.join(labdir, fn), encoding='utf-8'))
        labels[L['i']] = L
    use = [L for L in labels.values() if L['kind'] in ('ball', 'none')]
    use.sort(key=lambda L: frames[L['i']]['abs'])

    t0 = time.time()
    # pass A: centroids only, because the size-vs-row fit decides what is merged
    print('pass A: size-vs-row fit (train pool only)')
    fit = []
    for L, bs, _ in collect(use, frames, None):
        if L['kind'] != 'ball' or L['pool'] != 'train':
            continue
        best, bb = 1e9, None
        for b in bs:
            d = math.hypot(b['cx'] - L['x'], b['cy'] - L['y'])
            if d < best:
                best, bb = d, b
        if best <= POS_TOL:
            fit.append((L['y'], bb['major'], bb['area']))
    ys = np.array([p[0] for p in fit], float)
    A = np.vstack([ys, np.ones_like(ys)]).T
    cmaj = np.linalg.lstsq(A, np.array([p[1] for p in fit], float), rcond=None)[0]
    cara = np.linalg.lstsq(A, np.sqrt([p[2] for p in fit]), rcond=None)[0]
    print('  ball major vs row: %.5f*y + %.2f   (n=%d, train only)'
          % (cmaj[0], cmaj[1], len(fit)))

    print('pass B: union candidates')
    per_frame = collect(use, frames, cmaj)

    rng = np.random.RandomState(0)
    F, Y, POOL, XS, YS_, FEAT = [], [], [], [], [], []
    CI, CROPS = [], []
    for L, bs, grey in per_frame:
        pts = np.array([[b['cx'], b['cy']] for b in bs], np.float32) \
            if bs else np.zeros((0, 2), np.float32)
        for b in bs:
            if L['kind'] == 'ball':
                dist = math.hypot(b['cx'] - L['x'], b['cy'] - L['y'])
                if dist <= POS_TOL:
                    y = 1
                elif dist <= AMB_TOL:
                    continue
                else:
                    y = 0
            else:
                y = 0
            dxy = np.hypot(pts[:, 0] - b['cx'], pts[:, 1] - b['cy'])
            nn = float(np.sort(dxy)[1]) if len(dxy) > 1 else 999.0
            e_maj = max(1.0, cmaj[0] * b['cy'] + cmaj[1])
            e_side = max(1.0, cara[0] * b['cy'] + cara[1])
            row = [b['minor'], b['major'], b['area'], b['fill'], b['elong'],
                   b['circ'], b['extent'], b['v_in'], b['v_max'], b['v_std'],
                   b['v_ring'], b['v_ring_max'], b['con'], b['rel_con'],
                   b['s_in'], b['s_ring'], b['h_in'], b['grass_ring'],
                   b['other_ring'], b['white_far'], b['lap'],
                   b['cx'] / 1920.0, b['cy'] / 1080.0,
                   b['major'] / e_maj, b['minor'] / e_maj,
                   math.sqrt(b['area']) / e_side,
                   nn, float((dxy < 100).sum() - 1), float(len(bs)),
                   b['split']] + b['th']
            F.append(L['i']); Y.append(y); POOL.append(L['pool'])
            XS.append(b['cx']); YS_.append(b['cy']); FEAT.append(row)
            if y == 1 or rng.rand() < NEG_CROP_KEEP:
                CROPS.append(cv2.getRectSubPix(grey, (CROP, CROP),
                                               (float(b['cx']), float(b['cy']))))
                CI.append(len(FEAT) - 1)

    X = np.array(FEAT, np.float32)
    y = np.array(Y, np.int8)
    pool = np.array(POOL)
    np.savez_compressed(OUT, X=X, y=y, f=np.array(F, np.int32), pool=pool,
                        cx=np.array(XS, np.float32), cy=np.array(YS_, np.float32),
                        crops=np.array(CROPS, np.uint8),
                        crop_idx=np.array(CI, np.int32),
                        names=np.array(NAMES), cmaj=cmaj, cara=cara)
    print()
    assert X.shape[1] == len(NAMES), '%d cols vs %d names' % (X.shape[1], len(NAMES))
    fa = np.array(F)
    for p in ('train', 'test'):
        mk = pool == p
        nfr = len(set(fa[mk]))
        nball = len(set(fa[mk][y[mk] == 1]))
        print('%-5s rows %7d  pos %4d  frames %3d  frames with the ball %3d'
              % (p, mk.sum(), (y[mk] == 1).sum(), nfr, nball))
    SPL = NAMES.index('is_split')
    print('split rows %d of %d  (%.1f%%)  candidates/frame %.0f'
          % ((X[:, SPL] == 1).sum(), len(X),
             100.0 * (X[:, SPL] == 1).sum() / len(X), len(X) / float(len(per_frame))))
    print('saved %s  %.1f s' % (OUT, time.time() - t0))


if __name__ == '__main__':
    main()
