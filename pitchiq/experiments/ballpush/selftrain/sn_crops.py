"""New-brief 2.1: ball and not-ball crops from the SoccerNet tracking 2023 train split, in BallNet's crop format.

    python -I sn_crops.py <train.zip> <out.npz> [frame step, default 2]

Reads the zip in place (nothing extracted, nothing from it is run).  Per sequence: gameinfo.ini names the ball
tracklet, gt/gt.txt is MOT rows (frame, id, x, y, w, h, ...).  For every <step>th frame with a ball box:
  ball    the frame rescaled so the ball is 6-11 px across (ours: about 8), a 40 x 40 crop on the ball (+-2 px)
  cleat   two crops at the feet of random player boxes, same scale
  head    one crop at the top of a random player box
  other   one crop at a random point of the frame
Negatives closer than 12 px (crop scale) to the ball are skipped.  -> crop uint8 N x 40 x 40 x 3 (BGR),
y (train_ball_classifier.CLASSES index), seq (sequence number).
"""
import re
import sys
import zipfile
from collections import defaultdict

import cv2
import numpy as np

CLASSES = ['ball', 'cleat', 'head', 'otherball', 'other']
SIDE = 40


def cut(img, cx, cy, s):
    """SIDE x SIDE crop centred on (cx, cy) of img rescaled by s (source px -> crop px), or None at the border."""
    half = SIDE / (2.0 * s)
    x0, y0, x1, y1 = int(round(cx - half)), int(round(cy - half)), int(round(cx + half)), int(round(cy + half))
    if x0 < 0 or y0 < 0 or x1 > img.shape[1] or y1 > img.shape[0] or x1 - x0 < 8:
        return None
    return cv2.resize(img[y0:y1, x0:x1], (SIDE, SIDE), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)


def main():
    zp, out = sys.argv[1], sys.argv[2]
    step = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    rng = np.random.default_rng(0)
    z = zipfile.ZipFile(zp)
    names = z.namelist()
    seqs = sorted({m.group(1) for n in names for m in [re.search(r'(SNMOT-\d+)/gt/gt.txt$', n)] if m})
    root = {s: next(n for n in names if n.endswith(s + '/gt/gt.txt'))[:-len('gt/gt.txt')] for s in seqs}
    C, Y, Q = [], [], []
    for si, s in enumerate(seqs):
        info = z.read(root[s] + 'gameinfo.ini').decode('utf8', 'replace')
        ball = {int(m.group(1)) for m in re.finditer(r'trackletID_(\d+)\s*=\s*ball', info)}
        rows = defaultdict(list)
        for line in z.read(root[s] + 'gt/gt.txt').decode().splitlines():
            p = line.split(',')
            if len(p) >= 6:
                rows[int(p[0])].append((int(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5])))
        nb = 0
        for f in sorted(rows)[::step]:
            B = [r for r in rows[f] if r[0] in ball]
            P = [r for r in rows[f] if r[0] not in ball]
            if len(B) != 1:
                continue
            _, bx, by, bw, bh = B[0]
            size = max(bw, bh)
            if size < 4:
                continue
            try:
                img = cv2.imdecode(np.frombuffer(z.read(root[s] + 'img1/%06d.jpg' % f), np.uint8), cv2.IMREAD_COLOR)
            except KeyError:
                continue
            if img is None:
                continue
            sc = rng.uniform(6, 11) / size
            cx, cy = bx + bw / 2, by + bh / 2
            c = cut(img, cx + rng.uniform(-2, 2) / sc, cy + rng.uniform(-2, 2) / sc, sc)
            if c is None:
                continue
            C.append(c), Y.append(0), Q.append(si)
            nb += 1
            pts = []
            for k in rng.permutation(len(P))[:2]:
                _, x, y, w, h = P[k]
                pts.append((1, x + w * rng.uniform(0.25, 0.75), y + h * rng.uniform(0.88, 1.0)))
            if P:
                _, x, y, w, h = P[rng.integers(len(P))]
                pts.append((2, x + w * rng.uniform(0.3, 0.7), y + h * rng.uniform(0.0, 0.15)))
            pts.append((4, rng.uniform(0, img.shape[1]), rng.uniform(0, img.shape[0])))
            for cls, x, y in pts:
                if np.hypot(x - cx, y - cy) * sc < 12:
                    continue
                c = cut(img, x, y, sc)
                if c is not None:
                    C.append(c), Y.append(cls), Q.append(si)
        print('%s  ball tracklets %s  frames %d  ball crops %d  total rows %d' % (s, sorted(ball), len(rows), nb, len(Y)), flush=True)
    Y = np.array(Y, np.int64)
    np.savez(out, crop=np.array(C, np.uint8), y=Y, seq=np.array(Q, np.int16), seqs=np.array(seqs))
    print('DONE rows %d  by class %s' % (len(Y), dict(zip(CLASSES, np.bincount(Y, minlength=5).tolist()))))


if __name__ == '__main__':
    main()
