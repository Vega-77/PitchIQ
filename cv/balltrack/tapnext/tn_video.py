"""Side-by-side-able phone videos: what we have vs TAPNext++, identical layout.

  camera    ring on the ball + the last 1 s of its path (carried with the camera pan)
  close-up  4x around the method's ball, so you can see whether it is on the ball
  map       last 2 s of the path on the pitch

    python tn_video.py tag lo hi ours            -> cmp_ours_<lo>.mp4
    python tn_video.py tag lo hi tn <tn npz>     -> cmp_tn_<lo>.mp4
"""
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE); HG = os.path.join(S2, 'homog')
sys.path.insert(0, S2); sys.path.insert(0, HG)
import render_vid as RV
import vovl
import vflight as V

TAG, LO, HI, WHO = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
WD = 720; CW, CH = 720, 405; TOP = 44
ZR, ZS = 64, 260                      # close-up: +-64 px of the 1920 frame shown at 260 px (4x)
MS = 3.4                              # map px per SB unit
RY = TOP + CH + 10                    # second row
MX0 = WD - 6 - int(123 * MS); MY0 = RY + 4
HT = RY + max(ZS, int(80 * MS) + 8) + 34
HT += HT % 2
SC = CW / 1920.
GREEN, FILLC = RV.GREEN, RV.ORANGE
CYAN, ORANGE, WHITE, GREY, MAG = (230, 200, 60), (40, 140, 255), (255, 255, 255), (150, 150, 150), (230, 80, 230)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def mm(x, y):
    return (int(MX0 + np.clip(x, -4, 124) * MS), int(MY0 + (80 - np.clip(y, -4, 84)) * MS))


def text(im, s, org, sc, col, th=1):
    cv2.putText(im, s, org, FONT, sc, (0, 0, 0), th + 2, cv2.LINE_AA)
    cv2.putText(im, s, org, FONT, sc, col, th, cv2.LINE_AA)


def ring(im, c, r, col, dashed=False):
    c = (int(c[0]), int(c[1]))
    if dashed:
        for a in range(0, 360, 40):
            cv2.ellipse(im, c, (r, r), 0, a, a + 22, col, 2, cv2.LINE_AA)
        return
    cv2.circle(im, c, r, (0, 0, 0), 4, cv2.LINE_AA); cv2.circle(im, c, r, col, 2, cv2.LINE_AA)


def per_frame():
    """Per frame of [LO, HI): image position (1920 px), colour, kind, SB spot for the map."""
    tr = V.Track(TAG)
    A0 = int(tr.abs[0])
    Hs = np.load(os.path.join(HG, '%s_smooth.npz' % TAG))['H']
    n = HI - LO
    img = np.full((n, 2), np.nan); sb = np.full((n, 2), np.nan); kind = [''] * n; gnd = np.full((n, 2), np.nan)
    hgt = np.full(n, np.nan)
    if WHO == 'ours':
        B = np.load(os.path.join(HG, '%s_ball3dd.npz' % TAG))
        O = np.load(os.path.join(HERE, 'tn_%s_ours.npz' % TAG))
        for k in range(n):
            i = LO - A0 + k
            air = str(B['state'][i]) == 'air' and np.isfinite(B['x'][i])
            if air:
                q = np.linalg.solve(tr.A, [B['x'][i], B['y'][i], 1.0]); uv = q[:2] / q[2]
                P = np.array([[uv[0], uv[1], B['z'][i]], [uv[0], uv[1], 0.0]])
                b_, g_ = tr.project(np.full(2, i), P)
                img[k], gnd[k], kind[k], hgt[k] = b_, g_, 'air', B['z'][i] / V.R_CH
            elif O['src'][i]:
                img[k] = O['px'][i], O['py'][i]; kind[k] = 'track' if O['src'][i] == 1 else 'fill'
            if np.isfinite(B['x'][i]):
                sb[k] = B['x'][i], B['y'][i]
    else:
        T = np.load(sys.argv[5])
        xy, vis = T['xy'], T['vis']
        for k in range(n):
            if np.isfinite(xy[k, 0]):
                img[k] = xy[k]; kind[k] = 'tn' if vis[k] else 'tnhid'
                q = Hs[LO - A0 + k].dot([xy[k, 0], xy[k, 1], 1.0])
                sb[k] = q[:2] / q[2]
    return A0, Hs, img, sb, kind, gnd, hgt


def main():
    import feat4
    A0, Hs, img, sb, kind, gnd, hgt = per_frame()
    lines = vovl.polylines()
    base = np.zeros((HT, WD, 3), np.uint8); base[:] = (24, 20, 16)
    p0, p1 = mm(-3, 83), mm(123, -3)
    cv2.rectangle(base, p0, p1, (45, 105, 50), -1)
    for pl in lines:
        cv2.polylines(base, [np.int32([mm(x, y) for x, y in pl])], False, (225, 235, 225), 1, cv2.LINE_AA)
    text(base, 'close-up (4x)', (12, RY + ZS + 24), 0.5, GREY)
    text(base, 'pitch, camera side down', (MX0, RY + int(80 * MS) + 30), 0.5, GREY)
    foot = np.float32([[0, 1079], [1919, 1079], [1919, 520], [0, 520]])
    OW = int(os.environ.get('OW', WD)); OH = int(round(HT * OW / WD / 2.)) * 2
    out_p = os.path.join(HERE, 'cmp_%s_%d.mp4' % (WHO, LO))
    vw = cv2.VideoWriter(out_p, cv2.CAP_MSMF, cv2.VideoWriter_fourcc(*'H264'), 30, (OW, OH))
    assert vw.isOpened(), 'no H.264 writer'
    title = 'WHAT WE HAVE' if WHO == 'ours' else 'TAPNext++'
    cap = cv2.VideoCapture(feat4.VIDEO); cap.set(cv2.CAP_PROP_POS_FRAMES, LO)
    last = None; t0 = time.time()
    for k in range(HI - LO):
        ok, fr = cap.read()
        if not ok:
            break
        i = LO - A0 + k
        im = base.copy()
        cam = cv2.resize(fr, (CW, CH), interpolation=cv2.INTER_AREA)
        # trail: last 1 s of image positions, carried into this frame through the ground plane
        Hc = np.linalg.inv(Hs[i])
        pts = []
        for j in range(max(0, k - 30), k + 1):
            if np.isfinite(img[j, 0]):
                q = Hs[LO - A0 + j].dot([img[j, 0], img[j, 1], 1.0]); q = Hc.dot(q)
                pts.append((j, q[:2] / q[2] * SC))
            else:
                pts.append((j, None))
        tcol = MAG if WHO == 'tn' else WHITE
        for (ja, a), (jb, b) in zip(pts[:-1], pts[1:]):
            if a is not None and b is not None and jb == ja + 1:
                cv2.line(cam, tuple(np.int32(a)), tuple(np.int32(b)), tcol, 2, cv2.LINE_AA)
        kd = kind[k]
        if np.isfinite(img[k, 0]):
            c = img[k] * SC
            if kd == 'air':
                g = gnd[k] * SC
                cv2.line(cam, tuple(np.int32(c)), tuple(np.int32(g)), ORANGE, 2, cv2.LINE_AA)
                cv2.circle(cam, tuple(np.int32(g)), 3, (0, 0, 0), -1, cv2.LINE_AA)
                ring(cam, c, 13, ORANGE)
            elif kd == 'tnhid':
                ring(cam, c, 12, GREY, dashed=True)
            else:
                ring(cam, c, 12, {'track': GREEN, 'fill': FILLC, 'tn': MAG}[kd])
            last = img[k].copy()
        im[TOP:TOP + CH] = cam
        # header
        text(im, title, (12, 31), 0.8, MAG if WHO == 'tn' else GREEN, 2)
        text(im, 'match %s' % RV.clock(LO + k)[:5], (WD - 150, 31), 0.65, WHITE, 2)
        msg = {'air': 'in the air, %.1f CR up' % hgt[k] if np.isfinite(hgt[k]) else 'in the air',
               'track': 'on the ground (tracked)', 'fill': 'on the ground (gap fill)',
               'tn': 'tracked', 'tnhid': 'TAPNext says hidden (its guess)', '': 'no ball'}[kd]
        text(im, msg, (235, 31), 0.55, GREY if kd in ('', 'tnhid') else WHITE)
        # close-up around the method's ball (last known if none)
        if last is not None:
            cx, cy = int(np.clip(last[0], ZR, 1920 - ZR)), int(np.clip(last[1], ZR, 1080 - ZR))
            z = cv2.resize(fr[cy - ZR:cy + ZR, cx - ZR:cx + ZR], (ZS, ZS), interpolation=cv2.INTER_CUBIC)
            if np.isfinite(img[k, 0]):
                zc = ((img[k] - [cx - ZR, cy - ZR]) * ZS / (2 * ZR))
                col = {'air': ORANGE, 'track': GREEN, 'fill': FILLC, 'tn': MAG, 'tnhid': GREY}[kd]
                ring(z, zc, 30, col, dashed=(kd == 'tnhid'))
            im[RY:RY + ZS, 10:10 + ZS] = z
        cv2.rectangle(im, (9, RY - 1), (11 + ZS, RY + ZS + 1), (90, 90, 90), 1)
        # map
        fp = Hs[i].dot(np.c_[foot, np.ones(4)].T).T; fp = fp[:, :2] / fp[:, 2:]
        cv2.polylines(im, [np.int32([mm(*p) for p in fp])], True, (0, 220, 255), 1, cv2.LINE_AA)
        for j in range(max(1, k - 60), k + 1):
            if np.isfinite(sb[j, 0]) and np.isfinite(sb[j - 1, 0]):
                col = CYAN if kind[j] == 'air' else tcol
                cv2.line(im, mm(*sb[j - 1]), mm(*sb[j]), col, 2, cv2.LINE_AA)
        if np.isfinite(sb[k, 0]):
            cv2.circle(im, mm(*sb[k]), 6, (0, 0, 0), -1, cv2.LINE_AA)
            cv2.circle(im, mm(*sb[k]), 4, CYAN if kd == 'air' else tcol, -1, cv2.LINE_AA)
        vw.write(im if OW == WD else cv2.resize(im, (OW, OH), interpolation=cv2.INTER_AREA))
    vw.release()
    print('%s  %dx%d  %.1f MiB  %.0f s' % (os.path.basename(out_p), OW, OH, os.path.getsize(out_p) / 2 ** 20, time.time() - t0))


if __name__ == '__main__':
    main()
