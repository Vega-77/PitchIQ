"""Phone video: the camera with the tracker on top, the pitch map under it.
H.264 through Windows Media Foundation (no extra libraries), portrait 720 wide.

  camera  green ring = tracker (orange = gap fill), orange dot = ball in the air
          with a line down to the grass under it, magenta = pitch lines
  map     white trail = last 2 s, green dot = on the ground, cyan = in the air
          (with its height), yellow box = what the camera sees

    python vphonevideo.py [ver] [fps]  -> phone_<ver>.mp4   (ver sx: vid1_ball3dsx)
"""
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
sys.path.insert(0, S2); sys.path.insert(0, HERE)
import render_vid as RV
import vovl
import vflight as V

ver = sys.argv[1] if len(sys.argv) > 1 else 'd'
FPS = int(sys.argv[2]) if len(sys.argv) > 2 else 30
tag = 'vid1'
WD = 720
CW, CH = 720, 405                  # camera panel
TOP = 44                           # header
MS = 5.6                           # map px per SB unit
MX0 = int((WD - 120 * MS) / 2); MY0 = TOP + CH + 30
HT = MY0 + int(80 * MS) + 52
HT += HT % 2
SC = CW / 1920.
GREEN, FILL = RV.GREEN, RV.ORANGE
CYAN, ORANGE, WHITE, GREY = (230, 200, 60), (40, 140, 255), (255, 255, 255), (170, 170, 170)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def mm(x, y):
    return (int(MX0 + np.clip(x, -4, 124) * MS), int(MY0 + (80 - np.clip(y, -4, 84)) * MS))


def text(im, s, org, sc, col, th=1):
    cv2.putText(im, s, org, FONT, sc, (0, 0, 0), th + 2, cv2.LINE_AA)
    cv2.putText(im, s, org, FONT, sc, col, th, cv2.LINE_AA)


def main():
    import feat4
    tr = V.Track(tag)
    A0 = int(tr.abs[0]); n = len(tr.abs)
    B = np.load(os.path.join(HERE, '%s_ball3d%s.npz' % (tag, ver)))
    st, bx, by_, bz = B['state'], B['x'], B['y'], B['z']
    Hs = np.load(os.path.join(HERE, '%s_smooth.npz' % tag))['H']
    picks = RV.picks_for(tag)
    lines = vovl.polylines()
    # map background
    base = np.zeros((HT - MY0 + 20, WD, 3), np.uint8); base[:] = (24, 20, 16)
    p0, p1 = mm(-3, 83), mm(123, -3)
    cv2.rectangle(base, (p0[0], p0[1] - MY0 + 20), (p1[0], p1[1] - MY0 + 20), (45, 105, 50), -1)
    for pl in lines:
        q = np.array([mm(x, y) for x, y in pl]); q[:, 1] -= MY0 - 20
        cv2.polylines(base, [np.int32(q)], False, (225, 235, 225), 1, cv2.LINE_AA)
    foot = np.float32([[0, 1079], [1919, 1079], [1919, 520], [0, 520]])
    step = max(1, 30 // FPS)
    # MSMF H.264 spends a fixed ~0.12 B per pixel-frame, so the phone size is set
    # by the output width (OW), the frame rate and the part length (PART s)
    OW = int(os.environ.get('OW', WD)); OH = int(round(HT * OW / WD / 2.)) * 2
    part_n = int(float(os.environ.get('PART', 1e9)) * 30)
    vw = None; outs = []
    cap = cv2.VideoCapture(feat4.VIDEO); cap.set(cv2.CAP_PROP_POS_FRAMES, A0)
    trail = []; t0 = time.time()
    S = np.diag([SC, SC, 1.0])
    for i in range(int(os.environ.get('NFR', n))):
        ok, fr = cap.read()
        if not ok:
            break
        ab = A0 + i
        pos = (bx[i], by_[i]) if np.isfinite(bx[i]) else None
        trail = (trail + [(pos, st[i] == 'air')])[-60:]
        if i % step:
            continue
        if vw is None or i % part_n == 0:
            if vw is not None:
                vw.release()
            out_p = os.path.join(HERE, 'phone_%s%s.mp4' % (ver, '' if part_n > n else '_p%d' % (i // part_n + 1)))
            vw = cv2.VideoWriter(out_p, cv2.CAP_MSMF, cv2.VideoWriter_fourcc(*'H264'), 30 // step, (OW, OH))
            assert vw.isOpened(), 'no H.264 writer'
            outs.append(out_p)
        im = np.zeros((HT, WD, 3), np.uint8); im[:] = (24, 20, 16)
        cam = cv2.resize(fr, (CW, CH), interpolation=cv2.INTER_AREA)
        Hi = S.dot(np.linalg.inv(Hs[i]))
        for pl in lines:
            q = np.c_[pl, np.ones(len(pl))].dot(Hi.T); g = q[:, 2] > 0; q = q[:, :2] / q[:, 2:]
            for a, b, ga, gb in zip(q[:-1], q[1:], g[:-1], g[1:]):
                if ga and gb and abs(a).max() < 3000 and abs(b).max() < 3000:
                    cv2.line(cam, tuple(np.int32(a)), tuple(np.int32(b)), (170, 70, 170), 1, cv2.LINE_AA)
        rec = picks.get(ab, {})
        air = st[i] == 'air' and np.isfinite(bx[i])
        if rec.get('src') and not air:
            c_ = (int(rec['x'] * SC), int(rec['y'] * SC))
            col = GREEN if rec['src'] == 'track' else FILL
            cv2.circle(cam, c_, 12, (0, 0, 0), 4, cv2.LINE_AA); cv2.circle(cam, c_, 12, col, 2, cv2.LINE_AA)
        if air:
            q = np.linalg.solve(tr.A, [bx[i], by_[i], 1.0]); uv = q[:2] / q[2]
            P = np.array([[uv[0], uv[1], bz[i]], [uv[0], uv[1], 0.0]])
            b_, g_ = np.round(tr.project(np.full(2, i), P) * SC).astype(int)
            cv2.line(cam, tuple(b_), tuple(g_), ORANGE, 2, cv2.LINE_AA)
            cv2.circle(cam, tuple(g_), 3, (0, 0, 0), -1, cv2.LINE_AA)
            cv2.circle(cam, tuple(b_), 13, (0, 0, 0), 4, cv2.LINE_AA); cv2.circle(cam, tuple(b_), 13, ORANGE, 2, cv2.LINE_AA)
        im[TOP:TOP + CH] = cam
        # header
        text(im, 'match %s' % RV.clock(ab)[:5], (12, 30), 0.75, WHITE, 2)
        if st[i] == 'air':
            cv2.rectangle(im, (WD - 190, 8), (WD - 10, 38), CYAN, -1)
            cv2.putText(im, 'IN THE AIR', (WD - 172, 31), FONT, 0.7, (20, 20, 20), 2, cv2.LINE_AA)
        elif rec.get('src'):
            cv2.putText(im, 'on the ground', (WD - 190, 31), FONT, 0.6, (140, 220, 140), 1, cv2.LINE_AA)
        else:
            cv2.putText(im, 'no ball', (WD - 190, 31), FONT, 0.6, GREY, 1, cv2.LINE_AA)
        # map
        im[MY0 - 20:] = base[:HT - MY0 + 20]
        fp = Hs[i].dot(np.c_[foot, np.ones(4)].T).T; fp = fp[:, :2] / fp[:, 2:]
        cv2.polylines(im, [np.int32([mm(*p) for p in fp])], True, (0, 220, 255), 1, cv2.LINE_AA)
        for (pa, aa), (pb, ab_) in zip(trail[:-1], trail[1:]):
            if pa is not None and pb is not None:
                cv2.line(im, mm(*pa), mm(*pb), CYAN if ab_ else WHITE, 2 if ab_ else 1, cv2.LINE_AA)
        if pos is not None:
            col = CYAN if air else (90, 200, 90)
            cv2.circle(im, mm(*pos), 8, (0, 0, 0), -1, cv2.LINE_AA); cv2.circle(im, mm(*pos), 6, col, -1, cv2.LINE_AA)
        if air:
            s = 'ball in the air, %.1f CR up' % (bz[i] / V.R_CH)
        elif pos is not None:
            s = 'ball at x %.0f  y %.0f  (of 120 x 80)' % pos
        else:
            s = ''
        text(im, s, (MX0, HT - 18), 0.6, WHITE)
        text(im, 'camera side', (WD // 2 - 52, MY0 + int(80 * MS) + 30), 0.45, GREY)
        vw.write(im if OW == WD else cv2.resize(im, (OW, OH), interpolation=cv2.INTER_AREA))
        if i % 1800 == 0:
            print('%d/%d  %.0f s' % (i, n, time.time() - t0), flush=True)
    vw.release()
    for out_p in outs:
        print('%s  %dx%d  %.1f MiB' % (os.path.basename(out_p), OW, OH, os.path.getsize(out_p) / 2 ** 20))
    print('%.0f s' % (time.time() - t0))


if __name__ == '__main__':
    main()
