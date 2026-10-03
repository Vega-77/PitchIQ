"""Flight-arc check video: one segment per detected flight (1 s either side),
with the fitted arc, the model ball and its shadow, a pitch map (flat ground
reading vs 3D), and the Air Lab label next to the detector's call.

    python vflvideo.py [tag]  -> flights_false.webm (flights no label backs),
                                 flights_true.webm  (the rest)
"""
import os
import sys
import time

import cv2
import numpy as np

import vflight as V
import vdrag as D

tag = sys.argv[1] if len(sys.argv) > 1 else 'vid1'
HERE = V.HERE
STEP = 3
PAD = 30
MW, MH = 768, 432; SC = MW / 1920.
ZR, INSET = 72, 256
W, H = MW + INSET, MH
MAPX, MAPY, MS = MW + 38, INSET + 34, 1.5          # pitch map: 120x80 SB at 1.5 px/unit

CYAN, ORANGE, GREEN, GREY, WHITE, RED = (224, 195, 79), (44, 158, 245), (0, 255, 0), (150, 150, 150), (255, 255, 255), (95, 100, 224)
LABC = {'air': CYAN, 'ground': (115, 191, 111), 'hidden': (133, 119, 107), 'none': GREY}


def main():
    import vfeats
    tr = V.Track(tag)
    A0 = int(tr.abs[0])
    T = np.load(os.path.join(HERE, 'airtruth.npz'))
    truth = dict(zip(T['abs'].tolist(), T['truth'].tolist()))
    B = np.load(os.path.join(HERE, '%s_ball3d.npz' % tag))
    state = B['state']
    # flights with full parameters, from the tuned scan
    Z = np.load(os.path.join(HERE, '%s_scan2.npz' % tag))
    S, C = Z['S'], {c: i for i, c in enumerate(Z['cols'].tolist())}
    S = S[np.isfinite(S[:, C['d_rms']])]
    ch = np.load(os.path.join(HERE, '%s_tuned.npz' % tag), allow_pickle=True)['chosen']
    fl = []
    for r in ch:
        th = S[r, [C[c] for c in 'u0 v0 vu vv tau Tf k'.split()]]
        a = int(S[r, C['a']])
        i0, i1 = int(np.floor(a + th[4] * V.FPS)), int(np.ceil(a + (th[4] + th[5]) * V.FPS))
        backed = any(truth.get(A0 + i) == 'air' for i in range(i0, i1 + 1))
        fl.append(dict(th=th, a=a, i0=i0, i1=i1, err=S[r, C['d_trim']], apex=S[r, C['d_apex']], backed=backed))
    fl.sort(key=lambda f: f['i0'])
    for k, f in enumerate(fl):
        f['no'] = k + 1
    cap = cv2.VideoCapture(vfeats.VIDEO)
    for name, sel in (('false', [f for f in fl if not f['backed']]), ('true', [f for f in fl if f['backed']])):
        p = os.path.join(HERE, 'flights_%s.webm' % name)
        vw = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*'VP80'), 30 // STEP, (W, H))
        t0 = time.time(); nfr = 0
        for f in sel:
            nfr += segment(cap, vw, tr, f, len(fl), truth, state, A0)
        vw.release()
        print('%s: %d flights, %d frames, %.1f MB, %.0f s' % (p, len(sel), nfr, os.path.getsize(p) / 1e6, time.time() - t0), flush=True)


def segment(cap, vw, tr, f, nfl, truth, state, A0):
    th = f['th']
    lo, hi = max(0, f['i0'] - PAD), min(len(tr.abs) - 1, f['i1'] + PAD)
    ts = th[4] + np.linspace(0, th[5], 48)
    arc3 = D.drag(th, ts)
    shadow3 = arc3.copy(); shadow3[:, 2] = 0
    arc_sb = tr.to_sb(arc3[:, :2])
    cap.set(cv2.CAP_PROP_POS_FRAMES, A0 + lo)
    last = None; n = 0
    for i in range(lo, hi + 1):
        ok, fr = cap.read()
        if not ok:
            break
        if tr.has[i]:
            last = tr.px[i]
        if (i - lo) % STEP:
            continue
        out = np.zeros((H, W, 3), np.uint8); out[:] = (28, 22, 18)
        img = fr.copy()
        idx = np.full(len(ts), i)
        a2 = tr.project(idx, arc3); s2 = tr.project(idx, shadow3)
        cv2.polylines(img, [np.round(s2).astype(np.int32)], False, GREY, 2, cv2.LINE_AA)
        cv2.polylines(img, [np.round(a2).astype(np.int32)], False, CYAN, 3, cv2.LINE_AA)
        s = np.clip((i - f['a']) / V.FPS, th[4], th[4] + th[5])
        inflight = f['i0'] <= i <= f['i1']
        P = D.drag(th, np.array([s]))
        if inflight:
            b = tr.project(np.array([i]), P)[0]
            P0 = P.copy(); P0[:, 2] = 0
            g = tr.project(np.array([i]), P0)[0]
            cv2.line(img, tuple(np.round(b).astype(int)), tuple(np.round(g).astype(int)), ORANGE, 2, cv2.LINE_AA)
            cv2.circle(img, tuple(np.round(g).astype(int)), 6, ORANGE, 2, cv2.LINE_AA)
            cv2.circle(img, tuple(np.round(b).astype(int)), 9, ORANGE, -1, cv2.LINE_AA)
        if tr.has[i]:
            cv2.circle(img, tuple(np.round(tr.px[i]).astype(int)), 26, GREEN, 2, cv2.LINE_AA)
        out[:, :MW] = cv2.resize(img, (MW, MH), interpolation=cv2.INTER_AREA)
        # close-up on the tracker pick (clean frame)
        if last is not None:
            cx, cy = int(np.clip(last[0], ZR, 1920 - ZR)), int(np.clip(last[1], ZR, 1080 - ZR))
            out[:INSET, MW:] = cv2.resize(img[cy - ZR:cy + ZR, cx - ZR:cx + ZR], (INSET, INSET), interpolation=cv2.INTER_CUBIC)
        # pitch map
        x0, y0 = MAPX, MAPY
        cv2.rectangle(out, (x0, y0), (x0 + int(120 * MS), y0 + int(80 * MS)), (60, 110, 60), -1)
        cv2.rectangle(out, (x0, y0), (x0 + int(120 * MS), y0 + int(80 * MS)), WHITE, 1)
        cv2.line(out, (x0 + int(60 * MS), y0), (x0 + int(60 * MS), y0 + int(80 * MS)), WHITE, 1)
        m = lambda q: (int(x0 + np.clip(q[0], -4, 124) * MS), int(y0 + (80 - np.clip(q[1], -4, 84)) * MS))
        cv2.polylines(out, [np.array([m(q) for q in arc_sb], np.int32)], False, CYAN, 1, cv2.LINE_AA)
        if tr.has[i]:
            gsb = tr.to_sb(tr.ground(i)[None])[0]
            cv2.circle(out, m(gsb), 4, GREY, -1, cv2.LINE_AA)          # flat (z=0) reading
        if inflight:
            cv2.circle(out, m(tr.to_sb(P[:, :2])[0]), 4, ORANGE, -1, cv2.LINE_AA)
        cv2.putText(out, 'grey: flat reading  orange: 3D', (x0, y0 + int(80 * MS) + 16), 0, 0.4, (200, 200, 200), 1, cv2.LINE_AA)
        # text
        ab = A0 + i
        lab = truth.get(ab, 'none')
        z = P[0, 2] / V.R_CH if inflight else 0.0
        head = 'flight %d/%d   apex %.2f circle radii   fit %.1f px' % (f['no'], nfl, f['apex'] / V.R_CH, f['err'])
        cv2.rectangle(out, (0, 0), (MW, 26), (20, 16, 12), -1)
        cv2.putText(out, head, (8, 18), 0, 0.52, WHITE, 1, cv2.LINE_AA)
        if not f['backed']:
            cv2.putText(out, 'NO LABELLED FLIGHT', (MW - 200, 18), 0, 0.52, RED, 2, cv2.LINE_AA)
        cv2.rectangle(out, (0, MH - 30), (MW, MH), (20, 16, 12), -1)
        cv2.putText(out, 'label: %s' % ('not visible' if lab == 'hidden' else lab), (8, MH - 10), 0, 0.55, LABC.get(lab, GREY), 2, cv2.LINE_AA)
        det = 'air  h %.2f' % z if inflight else str(state[i])
        cv2.putText(out, 'detector: %s' % det, (230, MH - 10), 0, 0.55, CYAN if inflight else WHITE, 1, cv2.LINE_AA)
        cv2.putText(out, 'frame %d' % ab, (MW - 130, MH - 10), 0, 0.55, WHITE, 1, cv2.LINE_AA)
        vw.write(out); n += 1
    return n


if __name__ == '__main__':
    main()
