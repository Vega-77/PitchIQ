"""Air Lab clips: 15 s WebM pieces of the window with the tracker ring, the
frame number and a zoomed inset around the ball.

    python vairclips.py lo hi [procs]  -> airlab/clips/<abs>.webm, airlab/manifest.json
"""
import json, os, sys, time
from multiprocessing import Pool
import cv2, numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
CLIP = 450; STEP = 3                 # 15 s per clip, every third frame (10 fps)
MW, MH = 768, 432; SC = MW / 1920.     # main view
ZR, INSET = 72, 256                    # close-up: +-72 full-res px -> 256 px
W, H = MW + INSET, MH
TAG = os.environ.get('TAG', 'vid1'); LAB = os.environ.get('LAB', 'airlab')


def work(lo):
    import vfeats
    im = np.load(os.path.join(HERE, TAG + '_img.npz')); ab = im['abs']; ix, iy = im['ix'], im['iy']
    pos = {int(a): (x, y) for a, x, y in zip(ab, ix, iy)}
    hi = lo + CLIP
    cap = cv2.VideoCapture(vfeats.VIDEO); cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
    p = os.path.join(HERE, LAB, 'clips', '%d.webm' % lo)
    vw = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*'VP80'), 30 // STEP, (W, H))
    last = None
    for f in range(lo, hi):
        ok, fr = cap.read()
        if not ok: break
        x, y = pos.get(f, (np.nan, np.nan))
        if np.isfinite(x): last = (x, y)
        if (f - lo) % STEP: continue
        out = np.zeros((H, W, 3), np.uint8); out[:] = (28, 22, 18)
        out[:, :MW] = cv2.resize(fr, (MW, MH), interpolation=cv2.INTER_AREA)
        if last is not None:
            cx, cy = int(np.clip(last[0], ZR, 1920 - ZR)), int(np.clip(last[1], ZR, 1080 - ZR))
            crop = cv2.resize(fr[cy - ZR:cy + ZR, cx - ZR:cx + ZR], (INSET, INSET), interpolation=cv2.INTER_CUBIC)
            if np.isfinite(x):
                c = (int((x - cx + ZR) * INSET / (2 * ZR)), int((y - cy + ZR) * INSET / (2 * ZR)))
                cv2.circle(crop, c, 22, (0, 255, 0), 1, cv2.LINE_AA)
            out[:INSET, MW:] = crop
        if np.isfinite(x):
            cv2.circle(out, (int(x * SC), int(y * SC)), 10, (0, 255, 0), 1, cv2.LINE_AA)
        cv2.putText(out, 'close-up', (MW + 8, INSET + 24), 0, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.putText(out, 'frame %d' % f, (MW + 8, H - 16), 0, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        vw.write(out)
    vw.release()
    return lo, os.path.getsize(p)


if __name__ == '__main__':
    lo, hi = int(sys.argv[1]), int(sys.argv[2])
    procs = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    os.makedirs(os.path.join(HERE, LAB, 'clips'), exist_ok=True)
    starts = list(range(lo, hi, CLIP))
    t0 = time.time()
    with Pool(procs) as p:
        res = p.map(work, starts, chunksize=1)
    man = dict(fps=30, step=STEP, clip=CLIP, clips=[dict(id=str(a), start=a, end=min(a + CLIP, hi) - 1, file='clips/%d.webm' % a, bytes=b) for a, b in res])
    json.dump(man, open(os.path.join(HERE, LAB, 'manifest.json'), 'w'), indent=1)
    print('%d clips, %.1f MB, %.0f s' % (len(res), sum(b for _, b in res) / 1e6, time.time() - t0))
