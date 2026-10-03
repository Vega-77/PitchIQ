"""Hybrid 90 + default flight chain on one window, same layout as tn_video.py.

  camera    ring on the ball + the last 1 s of its path (carried with the camera pan)
            green = our tracker, orange = our gap fill, magenta = TAPNext++ bridge,
            orange ring + drop line = in the air (3D chain)
  close-up  4x around the ball
  map       last 2 s of the path on the pitch

    python hyb_video.py tag lo hi [part]     -> hyb90_<tag>_<lo>.mp4   (hi exclusive)
"""
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE); HG = os.path.join(S2, 'h3d_hyb90')
sys.path.insert(0, S2); sys.path.insert(0, HG)
import render_vid as RV      # noqa: E402  (colours, clock)
import vovl                  # noqa: E402
import vflight as V          # noqa: E402

TAG, LO, HI = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
PART = sys.argv[4] if len(sys.argv) > 4 else ''
WD = 720; CW, CH = 720, 405; TOP = 44
ZR, ZS = 64, 260
MS = 3.4
RY = TOP + CH + 10
MX0 = WD - 6 - int(123 * MS); MY0 = RY + 4
HT = RY + max(ZS, int(80 * MS) + 8) + 34 + 24
HT += HT % 2
SC = CW / 1920.
GREEN, FILLC = RV.GREEN, RV.ORANGE
CYAN, ORANGE, WHITE, GREY, MAG = (230, 200, 60), (40, 140, 255), (255, 255, 255), (150, 150, 150), (230, 80, 230)
FONT = cv2.FONT_HERSHEY_SIMPLEX
COL = {'air': ORANGE, 'track': GREEN, 'fill': FILLC, 'bridge': MAG}


def mm(x, y):
    return (int(MX0 + np.clip(x, -4, 124) * MS), int(MY0 + (80 - np.clip(y, -4, 84)) * MS))


def text(im, s, org, sc, col, th=1):
    cv2.putText(im, s, org, FONT, sc, (0, 0, 0), th + 2, cv2.LINE_AA)
    cv2.putText(im, s, org, FONT, sc, col, th, cv2.LINE_AA)


def ring(im, c, r, col):
    c = (int(c[0]), int(c[1]))
    cv2.circle(im, c, r, (0, 0, 0), 4, cv2.LINE_AA); cv2.circle(im, c, r, col, 2, cv2.LINE_AA)


def per_frame():
    tr = V.Track(TAG)
    A0 = int(tr.abs[0])
    Hs = np.load(os.path.join(HG, '%s_smooth2.npz' % TAG))['H']
    B = np.load(os.path.join(HG, '%s_ball3dd.npz' % TAG))
    P = np.load(os.path.join(S2, 'alltests', 'p2d', 'hyb90', '%s.npz' % TAG))
    assert int(P['abs'][0]) == A0 and int(B['abs'][0]) == A0
    n = HI - LO
    img = np.full((n, 2), np.nan); sb = np.full((n, 2), np.nan); kind = [''] * n; gnd = np.full((n, 2), np.nan)
    hgt = np.full(n, np.nan)
    for k in range(n):
        i = LO - A0 + k
        if str(B['state'][i]) == 'air' and np.isfinite(B['x'][i]):
            q = np.linalg.solve(tr.A, [B['x'][i], B['y'][i], 1.0]); uv = q[:2] / q[2]
            Q = np.array([[uv[0], uv[1], B['z'][i]], [uv[0], uv[1], 0.0]])
            b_, g_ = tr.project(np.full(2, i), Q)
            img[k], gnd[k], kind[k], hgt[k] = b_, g_, 'air', B['z'][i] / V.R_CH
        elif P['src'][i] > 0 and np.isfinite(P['x'][i]):
            img[k] = P['x'][i], P['y'][i]; kind[k] = {1: 'track', 2: 'fill', 3: 'bridge'}[int(P['src'][i])]
        if np.isfinite(B['x'][i]):
            sb[k] = B['x'][i], B['y'][i]
    return A0, Hs, img, sb, kind, gnd, hgt


def main():
    import feat4
    A0, Hs, img, sb, kind, gnd, hgt = per_frame()
    base = np.zeros((HT, WD, 3), np.uint8); base[:] = (24, 20, 16)
    cv2.rectangle(base, mm(-3, 83), mm(123, -3), (45, 105, 50), -1)
    for pl in vovl.polylines():
        cv2.polylines(base, [np.int32([mm(x, y) for x, y in pl])], False, (225, 235, 225), 1, cv2.LINE_AA)
    text(base, 'close-up (4x)', (12, RY + ZS + 24), 0.5, GREY)
    text(base, 'pitch, camera side down', (MX0, RY + int(80 * MS) + 30), 0.5, GREY)
    lx = 12
    for s, c in (('ours', GREEN), ('our fill', FILLC), ('TAPNext bridge', MAG), ('air', ORANGE)):
        cv2.circle(base, (lx + 6, HT - 13), 5, c, -1, cv2.LINE_AA); text(base, s, (lx + 15, HT - 8), 0.42, GREY); lx += 30 + 9 * len(s)
    foot = np.float32([[0, 1079], [1919, 1079], [1919, 520], [0, 520]])
    OW = int(os.environ.get('OW', 576)); OH = int(round(HT * OW / WD / 2.)) * 2
    out_p = os.path.join(HERE, 'hyb90_%s_%d.mp4' % (TAG, LO))
    vw = cv2.VideoWriter(out_p, cv2.CAP_MSMF, cv2.VideoWriter_fourcc(*'H264'), 30, (OW, OH))
    assert vw.isOpened(), 'no H.264 writer'
    cap = cv2.VideoCapture(feat4.VIDEO); cap.set(cv2.CAP_PROP_POS_FRAMES, LO)
    last = None; t0 = time.time()
    for k in range(HI - LO):
        ok, fr = cap.read()
        if not ok:
            break
        i = LO - A0 + k
        im = base.copy()
        cam = cv2.resize(fr, (CW, CH), interpolation=cv2.INTER_AREA)
        Hc = np.linalg.inv(Hs[i])
        pts = []
        for j in range(max(0, k - 30), k + 1):
            if np.isfinite(img[j, 0]):
                q = Hs[LO - A0 + j].dot([img[j, 0], img[j, 1], 1.0]); q = Hc.dot(q)
                pts.append((j, q[:2] / q[2] * SC))
            else:
                pts.append((j, None))
        for (ja, a), (jb, b) in zip(pts[:-1], pts[1:]):
            if a is not None and b is not None and jb == ja + 1:
                cv2.line(cam, tuple(np.int32(a)), tuple(np.int32(b)), WHITE, 2, cv2.LINE_AA)
        kd = kind[k]
        if np.isfinite(img[k, 0]):
            c = img[k] * SC
            if kd == 'air':
                g = gnd[k] * SC
                cv2.line(cam, tuple(np.int32(c)), tuple(np.int32(g)), ORANGE, 2, cv2.LINE_AA)
                cv2.circle(cam, tuple(np.int32(g)), 3, (0, 0, 0), -1, cv2.LINE_AA)
                ring(cam, c, 13, ORANGE)
            else:
                ring(cam, c, 12, COL[kd])
            last = img[k].copy()
        im[TOP:TOP + CH] = cam
        text(im, 'HYBRID 90' + (' ' + PART if PART else ''), (12, 31), 0.8, GREEN, 2)
        text(im, 'match %s' % RV.clock(LO + k)[:5], (WD - 150, 31), 0.65, WHITE, 2)
        msg = {'air': 'in the air, %.1f CR up' % hgt[k] if np.isfinite(hgt[k]) else 'in the air',
               'track': 'on the ground (tracked)', 'fill': 'on the ground (gap fill)',
               'bridge': 'on the ground (TAPNext bridge)', '': 'no ball'}[kd]
        text(im, msg, (250, 31), 0.55, GREY if kd == '' else WHITE)
        if last is not None:
            cx, cy = int(np.clip(last[0], ZR, 1920 - ZR)), int(np.clip(last[1], ZR, 1080 - ZR))
            z = cv2.resize(fr[cy - ZR:cy + ZR, cx - ZR:cx + ZR], (ZS, ZS), interpolation=cv2.INTER_CUBIC)
            if np.isfinite(img[k, 0]):
                ring(z, (img[k] - [cx - ZR, cy - ZR]) * ZS / (2 * ZR), 30, COL[kd])
            im[RY:RY + ZS, 10:10 + ZS] = z
        cv2.rectangle(im, (9, RY - 1), (11 + ZS, RY + ZS + 1), (90, 90, 90), 1)
        fp = Hs[i].dot(np.c_[foot, np.ones(4)].T).T; fp = fp[:, :2] / fp[:, 2:]
        cv2.polylines(im, [np.int32([mm(*p) for p in fp])], True, (0, 220, 255), 1, cv2.LINE_AA)
        for j in range(max(1, k - 60), k + 1):
            if np.isfinite(sb[j, 0]) and np.isfinite(sb[j - 1, 0]):
                cv2.line(im, mm(*sb[j - 1]), mm(*sb[j]), CYAN if kind[j] == 'air' else WHITE, 2, cv2.LINE_AA)
        if np.isfinite(sb[k, 0]):
            cv2.circle(im, mm(*sb[k]), 6, (0, 0, 0), -1, cv2.LINE_AA)
            cv2.circle(im, mm(*sb[k]), 4, CYAN if kd == 'air' else WHITE, -1, cv2.LINE_AA)
        vw.write(cv2.resize(im, (OW, OH), interpolation=cv2.INTER_AREA))
    vw.release()
    print('%s  %dx%d  %.1f MiB  %.0f s' % (os.path.basename(out_p), OW, OH, os.path.getsize(out_p) / 2 ** 20, time.time() - t0))


if __name__ == '__main__':
    main()
