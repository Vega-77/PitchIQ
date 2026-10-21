"""croplab sheets with enough context for a human to be sure.

The first sheets were the classifier's own 48 px crops: right for a network,
too little for a person to tell a ball from a sock.  Same items (same ids,
so labels already saved still apply), but each item is now a strip of three
CTX x CTX native-pixel panels around the candidate - frame f-DT, f, f+DT -
so the object's surroundings and its motion are both visible.  The middle
panel carries corner brackets around the candidate (drawn outside it, so no
pixel of the object is covered).  Panels are cut at the candidate's frame-f
position in all three frames; the camera pans little in 0.2 s.

    python lab_sheets2.py      # reads croplab/manifest.json (v1), rewrites it
                               # and croplab/sheets/tNNN.jpg
"""
import collections
import json
import os
import sys
import time

import numpy as np

S2 = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, S2)
import crops as CR                               # noqa: E402

OUT = os.path.join(S2, 'croplab')
CTX = 160
DT = 6
PER_SHEET = 40
COLS = 2                                         # strips per sheet row


def brackets(p, c, r=18, arm=7, col=(44, 158, 245)):
    """Corner brackets of half-size r around centre c on panel p (BGR)."""
    x0, y0, x1, y1 = c - r, c - r, c + r, c + r
    for (x, y, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                           (x0, y1, 1, -1), (x1, y1, -1, -1)):
        for k in range(arm):
            for w in (0, 1):
                xx, yy = x + dx * k, y + dy * w
                if 0 <= xx < CTX and 0 <= yy < CTX:
                    p[yy, xx] = col
                xx, yy = x + dx * w, y + dy * k
                if 0 <= xx < CTX and 0 <= yy < CTX:
                    p[yy, xx] = col


def main():
    man = json.load(open(os.path.join(OUT, 'manifest.json')))
    items = [it for sh in man['sheets'] for it in sh['items']]
    sheets = build(items, 't')
    json.dump(dict(tile=CTX, tw=3 * CTX + 8, th=CTX, dt=DT, sheets=sheets),
              open(os.path.join(OUT, 'manifest.json'), 'w'))


def build(items, prefix):
    """Strips for items ({id, tag, f, j, hint}) -> list of sheet dicts,
    images written as croplab/sheets/<prefix>NNN.jpg."""
    import cv2
    import feat4
    from PIL import Image

    t0 = time.time()
    pos = {}
    for tag in sorted({it['tag'] for it in items if 'x' not in it}):
        z = np.load(os.path.join(S2, '%scrops.npz' % tag))
        for f, j, x, y in zip(z['f'], z['j'], z['x'], z['y']):
            pos[(tag, int(f), int(j))] = (float(x), float(y))

    need = collections.defaultdict(set)          # abs frame -> item indices
    for i, it in enumerate(items):
        for d in (-DT, 0, DT):
            need[it['f'] + d].add(i)
    strips = np.zeros((len(items), CTX, 3 * CTX + 8, 3), np.uint8)
    strips[:] = 24
    cap = cv2.VideoCapture(feat4.VIDEO)
    frames = sorted(need)
    # read in runs: seek when the next wanted frame is far ahead
    cur = -10 ** 9
    for ab in frames:
        if ab - cur > 90 or ab < cur:
            cap.set(cv2.CAP_PROP_POS_FRAMES, ab)
            cur = ab
        while cur < ab:
            cap.grab()
            cur += 1
        ok, img = cap.read()
        assert ok, 'read failed at %d' % ab
        cur += 1
        for i in need[ab]:
            it = items[i]
            x, y = ((it['x'], it['y']) if 'x' in it
                    else pos[(it['tag'], it['f'], it['j'])])
            k = (ab - it['f']) // DT + 1
            p = np.empty((CTX, CTX, 3), np.uint8)
            CR.cut(img, x, y, CTX, p)
            if k == 1:
                brackets(p, CTX // 2)
            strips[i, :, k * (CTX + 4):k * (CTX + 4) + CTX] = p
    cap.release()

    for old in os.listdir(os.path.join(OUT, 'sheets')):
        pass                                     # old sheets left in place
    sw, sh_ = strips.shape[2], CTX
    sheets = []
    for q in range(0, len(items), PER_SHEET):
        chunk = list(range(q, min(q + PER_SHEET, len(items))))
        rows = (len(chunk) + COLS - 1) // COLS
        im = np.zeros((rows * sh_, COLS * sw, 3), np.uint8)
        for n, i in enumerate(chunk):
            r, c = divmod(n, COLS)
            im[r * sh_:(r + 1) * sh_, c * sw:(c + 1) * sw] = strips[i]
        name = 'sheets/%s%03d.jpg' % (prefix, len(sheets))
        Image.fromarray(im[:, :, ::-1]).save(os.path.join(OUT, name),
                                              quality=93)
        sheets.append(dict(file=name, cols=COLS,
                           items=[items[i] for i in chunk]))
    print('%d items, %d sheets, %d frames read, %.1f s'
          % (len(items), len(sheets), len(frames), time.time() - t0))
    return sheets


if __name__ == '__main__':
    main()
