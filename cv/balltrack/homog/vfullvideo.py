"""Full-window review video: detector, tracker, flights and the field map.

  image  thin grey ring  detector's top candidate (its confidence)
         green / orange  tracker pick (tracked / gap fill)
         cyan arc        fitted flight (air segments), yellow = inferred ends
         orange dot      the 3D ball, with a line down to its shadow on the grass
  map    white trail     last 2 s;  green dot = on the ground, cyan = in the air
         grey x          where the flat reading would have put an air ball

    python vfullvideo.py [ver]  -> full_<ver>.mp4   (ver 3k: vid1_chains3k / vid1_ball3d3k)
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
import vflchain as Ch
import vflvideo2 as W

ver = sys.argv[1] if len(sys.argv) > 1 else 'd'
tag = 'vid1'
SC = 2 / 3.; WD, HT = 1280, 720
MS, MX0, MY0 = 3.0, WD - 12 - 360, 12
CYAN, YEL, ORANGE = W.CYAN, W.YEL, W.ORANGE
GREEN, FILL, GREY, WHITE = RV.GREEN, RV.ORANGE, (170, 170, 170), (255, 255, 255)
LABC = {'air': CYAN, 'ground': (115, 191, 111), 'hidden': (133, 119, 107), 'touch': (80, 200, 255)}


def mm(p):
    return (int(MX0 + np.clip(p[0], -6, 126) * MS), int(MY0 + (80 - np.clip(p[1], -6, 86)) * MS))


def main():
    import feat4
    tr = V.Track(tag)
    A0 = int(tr.abs[0]); n = len(tr.abs)
    B = np.load(os.path.join(HERE, '%s_ball3d%s.npz' % (tag, ver)))
    st, bx, by_, bz = B['state'], B['x'], B['y'], B['z']
    Z = np.load(os.path.join(HERE, '%s_ballsb.npz' % tag))
    flat = np.c_[Z['x'], Z['y']]
    T = np.load(os.path.join(HERE, 'airtruth.npz'))
    truth = dict(zip(T['abs'].tolist(), T['truth'].tolist()))
    picks = RV.picks_for(tag)
    # chains: air segments as 3D polylines, inferred ends, the span they cover
    chains = []
    no = 0
    for c in np.load(os.path.join(HERE, '%s_chains%s.npy' % (tag, ver)), allow_pickle=True):
        if not any(c['seg_air']):
            continue
        nodes, ts, ks = Ch.unpack(c['x'], c['m'])
        segs = []
        for j in range(c['m']):
            no += 1
            if c['seg_air'][j]:
                tt = np.linspace(ts[j], ts[j + 1], 24)
                segs.append((no, Ch.chain_eval(tr, c['x'], c['m'], tt)))
        pre, post = W.extension(c, 'take-off'), W.extension(c, 'landing')
        ends = [e[0] for e, ok in ((pre, c['seg_air'][0]), (post, c['seg_air'][-1])) if e and ok]
        lo = min([ts[0]] + ([pre[1]] if pre else [])); hi = max([ts[-1]] + ([post[2]] if post else []))
        chains.append(dict(lo=lo - 15, hi=hi + 15, segs=segs, ends=ends, ts=ts, m=c['m']))
    print('chains shown %d, flights numbered %d' % (len(chains), no), flush=True)

    lines = vovl.polylines()
    base = np.zeros((241, 361, 3), np.uint8); base[:] = (40, 110, 40)
    for pl in lines:
        cv2.polylines(base, [np.int32(pl * MS)], False, (230, 230, 230), 1, cv2.LINE_AA)
    foot = np.float32([[0, 1079], [1919, 1079], [1919, 520], [0, 520]])
    Hs = Z['H']
    cap = cv2.VideoCapture(feat4.VIDEO); cap.set(cv2.CAP_PROP_POS_FRAMES, A0)
    out_p = os.path.join(HERE, 'full_%s.mp4' % ver)
    vw = cv2.VideoWriter(out_p, cv2.VideoWriter_fourcc(*'mp4v'), 30, (WD, HT))
    trail = []; t0 = time.time(); font = cv2.FONT_HERSHEY_SIMPLEX
    S = np.diag([SC, SC, 1.0])
    for i in range(int(os.environ.get('NFR', n))):
        ok, im = cap.read()
        if not ok:
            break
        ab = A0 + i
        im = cv2.resize(im, (WD, HT), interpolation=cv2.INTER_AREA)
        Hi = S.dot(np.linalg.inv(Hs[i]))
        for pl in lines:
            q = np.c_[pl, np.ones(len(pl))].dot(Hi.T); g = q[:, 2] > 0; q = q[:, :2] / q[:, 2:]
            for a, b, ga, gb in zip(q[:-1], q[1:], g[:-1], g[1:]):
                if ga and gb and abs(a).max() < 4000 and abs(b).max() < 4000:
                    cv2.line(im, tuple(np.int32(a)), tuple(np.int32(b)), (160, 60, 160), 1, cv2.LINE_AA)
        pj = lambda P: np.round(tr.project(np.full(len(P), i), P) * SC).astype(np.int32)
        # flights on screen now
        live = [c for c in chains if c['lo'] <= i <= c['hi']]
        for c in live:
            for _, P in c['segs']:
                cv2.polylines(im, [pj(P)], False, CYAN, 2, cv2.LINE_AA)
            for P in c['ends']:
                cv2.polylines(im, [pj(P)], False, YEL, 2, cv2.LINE_AA)
        # detector and tracker
        rec = picks.get(ab, {})
        if 'top' in rec:
            x, y, p = rec['top']
            c_ = (int(x * SC), int(y * SC))
            cv2.circle(im, c_, 20, GREY, 1, cv2.LINE_AA)
            cv2.putText(im, '%.2f' % RV.sig(p), (c_[0] + 22, c_[1] - 12), font, 0.4, GREY, 1, cv2.LINE_AA)
        if rec.get('src'):
            c_ = (int(rec['x'] * SC), int(rec['y'] * SC))
            col = GREEN if rec['src'] == 'track' else FILL
            cv2.circle(im, c_, 14, (0, 0, 0), 4, cv2.LINE_AA); cv2.circle(im, c_, 14, col, 2, cv2.LINE_AA)
        air = st[i] == 'air' and np.isfinite(bx[i])
        if air:
            # rebuild the CH position from the stored SB position and height
            uv = sb_to_ch(tr, bx[i], by_[i])
            P = np.r_[uv, bz[i]]
            b = pj(P[None])[0]; g = pj(np.r_[uv, 0][None])[0]
            cv2.line(im, tuple(b), tuple(g), ORANGE, 2, cv2.LINE_AA)
            cv2.circle(im, tuple(g), 4, (0, 0, 0), -1, cv2.LINE_AA)
            cv2.circle(im, tuple(b), 8, ORANGE, -1, cv2.LINE_AA)
        # map
        pos = (bx[i], by_[i]) if np.isfinite(bx[i]) else None
        trail = (trail + [pos])[-60:]
        roi = im[MY0 - 6:MY0 + 241 + 44, MX0 - 6:MX0 + 367]; roi[:] = (roi * 0.3).astype(np.uint8)
        im[MY0:MY0 + 241, MX0:MX0 + 361] = base
        fp = Hs[i].dot(np.c_[foot, np.ones(4)].T).T; fp = fp[:, :2] / fp[:, 2:]
        cv2.polylines(im, [np.int32([mm(p) for p in fp])], True, (0, 220, 255), 1, cv2.LINE_AA)
        for c in live:
            for _, P_ in c['segs']:
                cv2.polylines(im, [np.int32([mm(q) for q in tr.to_sb(P_[:, :2])])], False, CYAN, 1, cv2.LINE_AA)
            for P_ in c['ends']:
                cv2.polylines(im, [np.int32([mm(q) for q in tr.to_sb(P_[:, :2])])], False, YEL, 1, cv2.LINE_AA)
        pts = [mm(q) for q in trail if q is not None]
        for a, b in zip(pts[:-1], pts[1:]):
            cv2.line(im, a, b, WHITE, 1, cv2.LINE_AA)
        if air and np.isfinite(flat[i, 0]):
            f_ = mm(flat[i]); cv2.drawMarker(im, f_, GREY, cv2.MARKER_TILTED_CROSS, 8, 1, cv2.LINE_AA)
        if pos is not None:
            col = CYAN if air else GREEN
            cv2.circle(im, mm(pos), 5, (0, 0, 0), -1, cv2.LINE_AA); cv2.circle(im, mm(pos), 4, col, -1, cv2.LINE_AA)
            s = ('IN THE AIR  h %.2f CR' % (bz[i] / V.R_CH)) if air else 'on the ground'
            s2 = 'x %.1f  y %.1f  (of 120 x 80)' % pos
        else:
            s = 'in the air (inferred, unseen)' if st[i] == 'air' else 'no ball'
            s2 = ''
        cv2.putText(im, s, (MX0, MY0 + 258), font, 0.5, CYAN if st[i] == 'air' else (235, 235, 235), 1, cv2.LINE_AA)
        cv2.putText(im, s2, (MX0, MY0 + 278), font, 0.45, (235, 235, 235), 1, cv2.LINE_AA)
        # header / footer
        cv2.rectangle(im, (0, 0), (MX0 - 12, 64), (20, 16, 12), -1)
        cv2.putText(im, 'match %s   frame %d' % (RV.clock(ab), ab), (12, 30), font, 0.7, WHITE, 1, cv2.LINE_AA)
        lab = truth.get(ab, '')
        cv2.rectangle(im, (0, HT - 34), (560, HT), (20, 16, 12), -1)
        cv2.putText(im, 'your label: %s' % ({'hidden': 'not visible', '': '-'}.get(lab, lab)), (10, HT - 11), font, 0.55,
                    LABC.get(lab, GREY), 2, cv2.LINE_AA)
        fl = [no_ for c in live for no_, P_ in c['segs']]
        if st[i] == 'air' and fl:
            cv2.putText(im, 'flight %s' % ','.join(map(str, fl)), (300, HT - 11), font, 0.55, CYAN, 1, cv2.LINE_AA)
        cv2.putText(im, 'grey ring detector  green tracker  orange fill  cyan flight  yellow inferred',
                    (12, 56), font, 0.45, (230, 230, 230), 1, cv2.LINE_AA)
        vw.write(im)
        if i % 1500 == 0:
            print('%d/%d  %.0f s' % (i, n, time.time() - t0), flush=True)
    vw.release()
    print('%s  %.0f MB  %.0f s' % (out_p, os.path.getsize(out_p) / 1e6, time.time() - t0))


def sb_to_ch(tr, x, y):
    """Inverse of tr.to_sb for one point."""
    q = np.linalg.solve(tr.A, [x, y, 1.0])
    return q[:2] / q[2]


if __name__ == '__main__':
    main()
