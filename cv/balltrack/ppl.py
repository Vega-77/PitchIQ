"""Person boxes for every frame of a clip - context for the crop classifier.

The pipeline's own detector (cv/detector.py: YOLOv8n, COCO person class) run
over the same frames drun.py scored, so each ball candidate can be placed
relative to the nearest player: on a head, at the feet, or clear of anyone.
This is the first stage of player tracking and nothing more - boxes, no ids.

    python ppl.py <tag> [<tag> ...]     # lo/hi from tag.npz -> tagppl.npz

When the player lab has already detected people over the window
(plab/out/<tag>_dets.npz, the same YOLOv8n at 1280 down to conf 0.05), its
boxes at conf >= 0.25 are used instead of a second pass: on fm01 they match
this pass's to under 1 px (99.9%) and move one ball pick of 1801 by 3 px.

Long windows run in drun's 5-minute chunks: each writes tagppl_k<chunk>.npz as
it finishes, a rerun skips those, and the last step joins them.
"""
import os
import sys
import time

import cv2
import numpy as np

S2 = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(S2))
sys.path.insert(0, REPO)
sys.path.insert(0, S2)

import feat4                                   # noqa: E402  (VIDEO path)
from drun import chunk_edges                   # noqa: E402
from cv.detector import PersonBallDetector     # noqa: E402

IMGSZ = 1280
CONF = 0.25
BATCH = 16


def from_dets(tag, lo, hi):
    """tagppl.npz from the player lab's detections, if they cover lo..hi."""
    p = os.path.join(S2, 'plab', 'out', '%s_dets.npz' % tag)
    if not os.path.exists(p):
        return False
    with np.load(p) as d:
        if int(d['imgsz']) != IMGSZ or float(d['min_conf']) > CONF:
            return False
        fr = d['frame']
        if fr.min() > lo or fr.max() < hi or int(d['lo']) > lo or int(d['lo']) + int(d['n']) <= hi:
            return False
        m = (d['conf'] >= CONF) & (fr >= lo) & (fr <= hi)
        np.savez_compressed(os.path.join(S2, '%sppl.npz' % tag), f=fr[m].astype(np.int32),
                            box=d['box'][m].astype(np.float32), conf=d['conf'][m].astype(np.float32),
                            lo=lo, hi=hi, imgsz=IMGSZ, minconf=CONF)
    print('%s  people from %s' % (tag, os.path.basename(p)), flush=True)
    return True


def run(det, tag):
    z = np.load(os.path.join(S2, '%s.npz' % tag), allow_pickle=True)
    lo, hi = int(z['lo']), int(z['hi'])
    if from_dets(tag, lo, hi):
        return
    t0 = time.time()
    names = []
    for c, (clo, chi) in enumerate(chunk_edges(lo, hi)):
        name = os.path.join(S2, '%sppl_k%03d_%d.npz' % (tag, c, clo))
        names.append(name)
        if not os.path.exists(name):
            run_chunk(det, clo, chi, name)
    F, B, C = [], [], []
    for name in names:
        with np.load(name) as p:       # closed, so Windows lets the part go below
            F.append(p['f'])
            B.append(p['box'])
            C.append(p['conf'])
    F, B, C = np.concatenate(F), np.concatenate(B).reshape(-1, 4), np.concatenate(C)
    np.savez_compressed(os.path.join(S2, '%sppl.npz' % tag),
                        f=F, box=B, conf=C, lo=lo, hi=hi,
                        imgsz=IMGSZ, minconf=CONF)
    for name in names:
        os.remove(name)
    el = time.time() - t0
    n = hi - lo + 1
    print('%s  %d frames  %.1f s  %.1f fps  %.1f people/frame'
          % (tag, n, el, n / el, len(F) / n), flush=True)


def run_chunk(det, lo, hi, name):
    cap = cv2.VideoCapture(feat4.VIDEO)
    assert cap.isOpened(), 'cannot open video'
    cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
    F, B, C = [], [], []
    imgs, ids = [], []

    def go():
        for ab, boxes in zip(ids, det.detect_batch_raw(imgs)):
            keep = boxes.cls == 0
            for b, c in zip(boxes.xyxy[keep], boxes.conf[keep]):
                F.append(ab)
                B.append(b)
                C.append(c)
        imgs.clear()
        ids.clear()

    for ab in range(lo, hi + 1):
        ok, img = cap.read()
        if not ok:
            print('  read failed at %d' % ab)
            break
        imgs.append(img)
        ids.append(ab)
        if len(imgs) == BATCH:
            go()
    if imgs:
        go()
    cap.release()
    np.savez_compressed(name + '.part.npz', f=np.array(F, np.int32),
                        box=np.array(B, np.float32).reshape(-1, 4),
                        conf=np.array(C, np.float32))
    os.replace(name + '.part.npz', name)
    print('  frames %d..%d done' % (lo, hi), flush=True)


def main():
    det = None
    for tag in sys.argv[1:]:
        if os.path.exists(os.path.join(S2, '%sppl.npz' % tag)):
            print('%s done already' % tag)
            continue
        if det is None:
            det = PersonBallDetector(conf=CONF, imgsz=IMGSZ, device=0)
        run(det, tag)


if __name__ == '__main__':
    main()
