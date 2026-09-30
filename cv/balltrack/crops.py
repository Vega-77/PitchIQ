"""Pixels and person context for every ball candidate - input to a crop classifier.

Four detectors have now been measured and every one of them finds the 12 px
ball some of the time and ranks it above scenery almost never.  The tracker
cannot convert a candidate list whose top entry is a cleat, a line crossing
or a marker disc, so the next thing to try is a second opinion on each
candidate that looks at what the detector's score throws away: the patch of
image around it, and where it sits relative to the nearest player.

This script produces that dataset and nothing else.  For a chosen subset of
frames it cuts a size x size BGR crop at native resolution around every
candidate (zero-padded at the image border, never resized - the ball is small
and interpolation is exactly what smears it into grass), and computes a short
context vector against that frame's person boxes from ppl.py:

    has_ppl    1 if <tag>ppl.npz exists at all, else 0 and the rest default
    n_people   boxes in the frame with conf >= 0.25
    inside     1 if the point is inside the chosen box
    u          (x - box_cx) / box_w      0 = centred on the player
    v          (y - y1) / box_h          0 = head, 1 = feet
    dist_px    point to box edge, 0 when inside
    dist_rel   dist_px / box_h           clipped to DIST_REL_MAX
    box_h      height of the chosen box in px
    s          detector score
    rank       rank of s within the frame, 0 = best
    y_rel      y / 1080

The chosen box is the smallest box containing the point if there is one,
else the box whose edge is nearest.  With no boxes in the frame (or no ppl
file) inside/u/v/box_h are 0 and dist_px/dist_rel take the "nobody near"
values below, so the vector is never NaN.

Candidate order is dfree.load's, taken from dfree.load itself: index j in a
frame here is index j in the frame dict the tracker picked from, so a crop
joins to a pick on (f, j) with no coordinate matching.

    python crops.py <tag> [--every N] [--frames file.json] [--size 48]
    python crops.py selftest        # synthetic numpy check, no video
"""
import io
import json
import os
import sys
import time

import numpy as np

S2 = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, S2)

import dfree                                   # noqa: E402

W, H = 1920, 1080
MINCONF = 0.25
DIST_NONE = float(np.hypot(W, H))    # nobody in the frame: a diagonal away
DIST_REL_MAX = 20.0                  # a tiny far box must not blow up dist_rel

CTX_NAMES = ['has_ppl', 'n_people', 'inside', 'u', 'v', 'dist_px',
             'dist_rel', 'box_h', 's', 'rank', 'y_rel']


def load_ppl(tag):
    """{abs frame: (m, 4) float64 boxes with conf >= MINCONF}, or None."""
    p = os.path.join(S2, '%sppl.npz' % tag)
    if not os.path.exists(p):
        return None
    z = np.load(p)
    keep = z['conf'] >= MINCONF
    f, box = z['f'][keep], z['box'][keep].astype(np.float64)
    o = np.argsort(f, kind='stable')
    f, box = f[o], box[o]
    u, a = np.unique(f, return_index=True)
    b = np.append(a[1:], len(f))
    return {int(ab): box[i:k] for ab, i, k in zip(u, a, b)}


def context(x, y, s, boxes, has_ppl):
    """(n, len(CTX_NAMES)) float32 for one frame's n candidates."""
    n = len(x)
    out = np.zeros((n, len(CTX_NAMES)), np.float32)
    out[:, 0] = float(has_ppl)
    rank = np.empty(n, np.int64)
    rank[np.argsort(-s, kind='stable')] = np.arange(n)
    out[:, 8] = s
    out[:, 9] = rank
    out[:, 10] = y / float(H)
    m = 0 if boxes is None else len(boxes)
    out[:, 1] = m
    if m == 0:
        out[:, 5] = DIST_NONE
        out[:, 6] = DIST_REL_MAX
        return out

    x1, y1, x2, y2 = (boxes[:, i][None, :] for i in range(4))
    px, py = x[:, None], y[:, None]
    bw = np.maximum(x2 - x1, 1.0)
    bh = np.maximum(y2 - y1, 1.0)
    ins = (px >= x1) & (px <= x2) & (py >= y1) & (py <= y2)       # (n, m)
    dx = np.maximum(np.maximum(x1 - px, px - x2), 0.0)
    dy = np.maximum(np.maximum(y1 - py, py - y2), 0.0)
    d = np.hypot(dx, dy)                                           # 0 inside
    area = np.where(ins, bw * bh, np.inf)
    any_in = ins.any(axis=1)
    best = np.where(any_in, np.argmin(area, axis=1), np.argmin(d, axis=1))

    r = np.arange(n)
    bx1, by1, bx2 = boxes[best, 0], boxes[best, 1], boxes[best, 2]
    w, h = bw[0, best], bh[0, best]
    dist = d[r, best]
    out[:, 2] = any_in
    out[:, 3] = (x - (bx1 + bx2) / 2.0) / w
    out[:, 4] = (y - by1) / h
    out[:, 5] = dist
    out[:, 6] = np.minimum(dist / h, DIST_REL_MAX)
    out[:, 7] = h
    return out


def cut(img, x, y, size, dst):
    """Write the size x size patch centred on (x, y) into dst, zero-padded."""
    ih, iw = img.shape[:2]
    x0 = int(round(x)) - size // 2
    y0 = int(round(y)) - size // 2
    a0, b0 = max(x0, 0), max(y0, 0)
    a1, b1 = min(x0 + size, iw), min(y0 + size, ih)
    dst[...] = 0
    if a1 > a0 and b1 > b0:
        dst[b0 - y0:b1 - y0, a0 - x0:a1 - x0] = img[b0:b1, a0:a1]


def choose(frames, every, fjson):
    if fjson is None:
        return frames[::every]
    want = set(int(v) for v in json.load(io.open(fjson, encoding='utf-8')))
    got = [fr for fr in frames if fr['abs'] in want]
    miss = len(want) - len(got)
    if miss:
        print('  %d requested frames have no candidates or lie outside the'
              ' clip - skipped' % miss)
    return got


def run(tag, every, fjson, size):
    import cv2
    import feat4                                # only cv2 / numpy

    t0 = time.time()
    frames = choose(dfree.load(tag), every, fjson)
    assert frames, 'no frames chosen'
    ppl = load_ppl(tag)
    has_ppl = ppl is not None
    print('%s: %d frames chosen, person boxes %s'
          % (tag, len(frames), 'present' if has_ppl else 'ABSENT (has_ppl=0)'),
          flush=True)

    N = sum(len(fr['s']) for fr in frames)
    F = np.empty(N, np.int32)
    J = np.empty(N, np.int16)
    X = np.empty(N, np.float32)
    Y = np.empty(N, np.float32)
    S = np.empty(N, np.float32)
    C = np.empty((N, size, size, 3), np.uint8)
    K = np.empty((N, len(CTX_NAMES)), np.float32)

    cap = cv2.VideoCapture(feat4.VIDEO)
    assert cap.isOpened(), 'cannot open video'
    pos = frames[0]['abs']
    cap.set(cv2.CAP_PROP_POS_FRAMES, pos)       # the one seek
    k = 0
    done = 0
    for fr in frames:
        ab = fr['abs']
        while pos < ab:
            if not cap.grab():
                break
            pos += 1
        ok, img = cap.read()
        if not ok or pos != ab:
            print('  read failed at %d' % ab)
            break
        pos += 1

        x, y, s = fr['x'], fr['y'], fr['s']
        n = len(s)
        sl = slice(k, k + n)
        F[sl] = ab
        J[sl] = np.arange(n)
        X[sl], Y[sl], S[sl] = x, y, s
        for j in range(n):
            cut(img, x[j], y[j], size, C[k + j])
        K[sl] = context(x, y, s, ppl.get(ab) if has_ppl else None, has_ppl)
        k += n
        done += 1
        if done % 100 == 0:
            el = time.time() - t0
            print('  %d/%d frames  %d crops  %.1f s  %.1f fps'
                  % (done, len(frames), k, el, done / el), flush=True)
    cap.release()

    out = os.path.join(S2, '%scrops.npz' % tag)
    np.savez_compressed(out, f=F[:k], j=J[:k], x=X[:k], y=Y[:k], s=S[:k],
                        crop=C[:k], ctx=K[:k], ctx_names=np.array(CTX_NAMES),
                        size=size, tag=tag)
    print('%s  %d frames  %d crops  %.1f s  -> %s'
          % (tag, done, k, time.time() - t0, os.path.basename(out)),
          flush=True)


def selftest():
    """Synthetic arrays only - no video, no npz from disk."""
    boxes = np.array([[100, 100, 150, 300],      # tall player
                      [110, 150, 140, 250],      # smaller box inside it
                      [500, 500, 520, 540]], np.float64)
    x = np.array([125.0, 105.0, 125.0, 530.0, 1900.0])
    y = np.array([200.0, 110.0, 310.0, 520.0, 1080.0])
    s = np.array([0.1, 0.9, 0.5, 0.5, 0.3])
    c = context(x, y, s, boxes, True)
    col = dict((nm, c[:, i]) for i, nm in enumerate(CTX_NAMES))
    assert (col['n_people'] == 3).all()
    # 0: in both player boxes -> smallest (h 100), centred, half way down
    assert col['inside'][0] == 1 and col['box_h'][0] == 100
    assert abs(col['u'][0]) < 1e-6 and abs(col['v'][0] - 0.5) < 1e-6
    # 1: only in the big box, near its head
    assert col['inside'][1] == 1 and col['box_h'][1] == 200
    assert abs(col['v'][1] - 0.05) < 1e-6 and col['dist_px'][1] == 0
    # 2: 10 px under the big box's feet; the small box is 60 px away
    assert col['inside'][2] == 0 and col['box_h'][2] == 200
    assert abs(col['dist_px'][2] - 10) < 1e-4
    assert abs(col['dist_rel'][2] - 0.05) < 1e-6
    assert abs(col['v'][2] - 1.05) < 1e-6
    # 3: 10 px right of the disc-sized box, level with its middle
    assert col['box_h'][3] == 40 and abs(col['dist_px'][3] - 10) < 1e-4
    assert abs(col['u'][3] - 1.0) < 1e-6
    # 4: far corner, dist_rel clipped
    assert col['dist_rel'][4] == DIST_REL_MAX
    assert list(col['rank']) == [4, 0, 1, 2, 3]
    assert abs(col['y_rel'][4] - 1.0) < 1e-6
    assert np.isfinite(c).all()

    none = context(x, y, s, None, False)
    assert (none[:, 0] == 0).all() and np.isfinite(none).all()
    assert (none[:, 5] == DIST_NONE).all()
    empty = context(x, y, s, np.zeros((0, 4)), True)
    assert (empty[:, 0] == 1).all() and (empty[:, 1] == 0).all()

    img = (np.arange(H * W * 3) % 251).astype(np.uint8).reshape(H, W, 3)
    d = np.empty((48, 48, 3), np.uint8)
    cut(img, 500.0, 400.0, 48, d)
    assert (d == img[376:424, 476:524]).all()
    cut(img, 3.0, 2.0, 48, d)                   # top-left corner
    assert (d[:22] == 0).all() and (d[:, :21] == 0).all()
    assert (d[22:, 21:] == img[:26, :27]).all()
    cut(img, 1919.4, 1079.6, 48, d)             # bottom-right corner
    assert (d[:24, :24] == img[1056:1080, 1895:1919]).all()
    assert (d[:, 25:] == 0).all() and (d[24:] == 0).all()
    cut(img, -500.0, -500.0, 48, d)
    assert (d == 0).all()
    print('selftest ok')


def main():
    a = sys.argv[1:]
    if a and a[0] == 'selftest':
        selftest()
        return
    if not a or a[0].startswith('-'):
        print(__doc__)
        sys.exit(1)
    tag, every, fjson, size = a[0], 10, None, 48
    i = 1
    while i < len(a):
        if a[i] == '--every':
            every = int(a[i + 1])
        elif a[i] == '--frames':
            fjson = a[i + 1]
        elif a[i] == '--size':
            size = int(a[i + 1])
        else:
            sys.exit('unknown argument %s' % a[i])
        i += 2
    run(tag, every, fjson, size)


if __name__ == '__main__':
    main()
