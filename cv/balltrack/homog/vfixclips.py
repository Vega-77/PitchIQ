"""Fix Lab data: the default model (ball3dd) on a new window, for the user to mark what is wrong.

  fixlab/clips/<abs>.webm  15 s pieces at 10 fps: the clean camera (768x432) and a
                           256 px close-up around the model's ball, no overlay baked in
  fixlab/manifest.json     clip list
  fixlab/model.json        per shown frame: what the model says (drawn live by the page)
  fixlab/frames.json       per shown frame: pixel -> StatsBomb homography (map footprint, clicks)

    python vfixclips.py tag [procs]
"""
import json
import os
import sys
import time
from multiprocessing import Pool

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
sys.path.insert(0, S2); sys.path.insert(0, HERE)
CLIP, STEP = 450, 3
MW, MH = 768, 432; SC = MW / 1920.
ZR, INSET = 72, 256
W, H = MW + INSET, MH
TAG = sys.argv[1] if len(sys.argv) > 1 else 'vid3'
OUT = os.path.join(HERE, os.environ.get('LAB', 'fixlab'))
VER = os.environ.get('VER', 'd')


def r1(v):
    return None if v is None or not np.isfinite(v) else round(float(v), 1)


def model_frames():
    """Per frame: state, tracker ring, model ball in the image, ground point, SB spot, height,
    and the close-up centre. Same drawing rules as vphonevideo."""
    import render_vid as RV
    import vflight as V
    tr = V.Track(TAG)
    A0 = int(tr.abs[0]); n = len(tr.abs)
    B = np.load(os.path.join(HERE, '%s_ball3d%s.npz' % (TAG, VER)))
    st, bx, by, bz = B['state'], B['x'], B['y'], B['z']
    Hs = np.load(os.path.join(HERE, '%s_smooth.npz' % TAG))['H']
    picks = RV.picks_for(TAG)
    rows, centre, last = {}, {}, None
    for i in range(n):
        ab = A0 + i
        rec = picks.get(ab, {})
        # what the default path outputs: air flights, else the smoother's spot (it bridges
        # short gaps and blanks fast stretches, so chain2's 'ground'/'none' is not the output)
        s = 2 if str(st[i]) == 'air' else (1 if np.isfinite(bx[i]) else 0)
        t = (rec['x'], rec['y']) if rec.get('src') else None
        tsrc = (1 if rec.get('src') == 'track' else 2) if t else 0
        b = g = None
        if s == 2 and np.isfinite(bx[i]):
            q = np.linalg.solve(tr.A, [bx[i], by[i], 1.0]); uv = q[:2] / q[2]
            P = np.array([[uv[0], uv[1], bz[i]], [uv[0], uv[1], 0.0]])
            b, g = tr.project(np.full(2, i), P)
        elif s == 1 and np.isfinite(bx[i]):
            q = np.linalg.inv(Hs[i]).dot([bx[i], by[i], 1.0])
            if q[2] > 0:
                g = q[:2] / q[2]
        c = b if b is not None else (t if t is not None else g)
        if c is not None and np.all(np.isfinite(c)):
            last = (float(c[0]), float(c[1]))
        centre[ab] = last
        if i % STEP:
            continue
        rows[ab] = [ab, s, r1(t and t[0]), r1(t and t[1]), tsrc,
                    r1(b[0] if b is not None else None), r1(b[1] if b is not None else None),
                    r1(g[0] if g is not None else None), r1(g[1] if g is not None else None),
                    r1(bx[i]), r1(by[i]), (round(float(bz[i]) / V.R_CH, 2) if s == 2 and np.isfinite(bz[i]) else None),
                    None, None]
    return A0, n, rows, centre, Hs


def work(args):
    lo, hi, centre = args
    import feat4
    cap = cv2.VideoCapture(feat4.VIDEO); cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
    p = os.path.join(OUT, 'clips', '%d.webm' % lo)
    vw = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*'VP80'), 30 // STEP, (W, H))
    used = {}
    for f in range(lo, hi):
        ok, fr = cap.read()
        if not ok:
            break
        if (f - lo) % STEP:
            continue
        out = np.zeros((H, W, 3), np.uint8); out[:] = (28, 22, 18)
        out[:, :MW] = cv2.resize(fr, (MW, MH), interpolation=cv2.INTER_AREA)
        c = centre.get(f)
        if c is not None:
            cx, cy = int(np.clip(c[0], ZR, 1920 - ZR)), int(np.clip(c[1], ZR, 1080 - ZR))
            out[:INSET, MW:] = cv2.resize(fr[cy - ZR:cy + ZR, cx - ZR:cx + ZR], (INSET, INSET), interpolation=cv2.INTER_CUBIC)
            used[f] = (cx, cy)
        vw.write(out)
    vw.release()
    return lo, os.path.getsize(p), used


def main():
    procs = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    os.makedirs(os.path.join(OUT, 'clips'), exist_ok=True)
    t0 = time.time()
    A0, n, rows, centre, Hs = model_frames()
    print('model frames %d  %.0f s' % (len(rows), time.time() - t0), flush=True)
    hi = A0 + n - (n % CLIP if n % CLIP < 30 else 0)   # no stub clip for the window's last frame
    jobs = [(lo, min(lo + CLIP, hi), {f: centre[f] for f in range(lo, min(lo + CLIP, hi))}) for lo in range(A0, hi, CLIP)]
    with Pool(procs) as pool:
        res = sorted(pool.map(work, jobs))
    for lo, b, used in res:
        for f, (cx, cy) in used.items():
            if f in rows:
                rows[f][12], rows[f][13] = cx, cy
    man = dict(fps=30, step=STEP, clip=CLIP, tag=TAG, ver=VER, zr=ZR, inset=INSET,
               clips=[dict(id=str(lo), start=lo, end=min(lo + CLIP, hi) - 1, file='clips/%d.webm' % lo, bytes=b) for lo, b, _ in res])
    json.dump(man, open(os.path.join(OUT, 'manifest.json'), 'w'), indent=1)
    cols = 'abs state tx ty tsrc bx by gx gy X Y h cx cy'.split()
    json.dump(dict(cols=cols, rows=[rows[k] for k in sorted(rows)]), open(os.path.join(OUT, 'model.json'), 'w'), separators=(',', ':'))
    fr = {}
    for f in sorted(rows):
        h = Hs[f - A0] / abs(Hs[f - A0][2, 2])
        if h.dot([960, 900, 1])[2] < 0:   # keep w > 0 for points in view (the page rejects w <= 0)
            h = -h
        fr[str(f)] = [float('%.7g' % v) for v in h.ravel()]
    json.dump(dict(step=STEP, H=fr), open(os.path.join(OUT, 'frames.json'), 'w'), separators=(',', ':'))
    tot = sum(b for _, b, _ in res)
    print('%d clips  %.1f MiB  max %.1f MiB  %.0f s' % (len(res), tot / 2 ** 20, max(b for _, b, _ in res) / 2 ** 20, time.time() - t0))
    for nm in ('manifest', 'model', 'frames'):
        print(nm, os.path.getsize(os.path.join(OUT, nm + '.json')))


if __name__ == '__main__':
    main()
