"""Person boxes for every frame of a clip - context for the crop classifier.

The pipeline's own detector (cv/detector.py: YOLOv8n, COCO person class) run
over the same frames drun.py scored, so each ball candidate can be placed
relative to the nearest player: on a head, at the feet, or clear of anyone.
This is the first stage of player tracking and nothing more - boxes, no ids.

    python ppl.py <tag> [<tag> ...]     # lo/hi from tag.npz -> tagppl.npz
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
from cv.detector import PersonBallDetector     # noqa: E402

IMGSZ = 1280
CONF = 0.25
BATCH = 16


def run(det, tag):
    z = np.load(os.path.join(S2, '%s.npz' % tag), allow_pickle=True)
    lo, hi = int(z['lo']), int(z['hi'])
    cap = cv2.VideoCapture(feat4.VIDEO)
    assert cap.isOpened(), 'cannot open video'
    cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
    F, B, C = [], [], []
    imgs, ids = [], []
    t0 = time.time()

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
    B = np.array(B, np.float32).reshape(-1, 4)
    np.savez_compressed(os.path.join(S2, '%sppl.npz' % tag),
                        f=np.array(F, np.int32), box=B,
                        conf=np.array(C, np.float32), lo=lo, hi=hi,
                        imgsz=IMGSZ, minconf=CONF)
    el = time.time() - t0
    n = hi - lo + 1
    print('%s  %d frames  %.1f s  %.1f fps  %.1f people/frame'
          % (tag, n, el, n / el, len(F) / n), flush=True)


def main():
    det = PersonBallDetector(conf=CONF, imgsz=IMGSZ, device=0)
    for tag in sys.argv[1:]:
        if os.path.exists(os.path.join(S2, '%sppl.npz' % tag)):
            print('%s done already' % tag)
            continue
        run(det, tag)


if __name__ == '__main__':
    main()
