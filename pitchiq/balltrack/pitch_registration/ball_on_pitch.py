"""Ball on the pitch: every frame's image->StatsBomb homography (half-second
registrations carried by the detect_candidates pan and blended), the tracker's ball
mapped through it, and a review video.

    python -m pitchiq.balltrack.pitch_registration.ball_on_pitch <tag> [video]  -> <tag>_ballsb.npz, <tag>_ballsb.csv,
                                        [<tag>_pitch.mp4]
"""

import os
import sys
import time
import cv2
import numpy as np

from .. import render_review_video
from . import draw_pitch_overlay

from . import WORK as HERE

S2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MARGIN = 3.0
Q = np.float32([[200, 500], [1720, 500], [1720, 1000], [200, 1000]])


def proj(H, p):
    """Apply homography H to the n x 2 points p."""
    q = np.c_[p, np.ones(len(p))].dot(H.T)
    return q[:, :2] / q[:, 2:]


def per_frame(tag):
    """{abs frame: image -> StatsBomb homography} for every frame <tag>pan.npz covers.

    Each ok half-second registration in <tag>_reg.npz (Hsb) is carried to the frame through
    the pan cum; between two registrations the frame blends both, through where they put the
    corners Q.  H is scaled to H[2, 2] = 1 with w > 0 for points in view.
    """
    r = np.load(os.path.join(HERE, '%s_reg.npz' % tag))
    p = np.load(os.path.join(S2, '%span.npz' % tag))
    lo, cum = int(p['lo']), p['cum']
    ab, Hs = r['abs'][r['ok']], r['Hsb'][r['ok']]
    out = {}
    for f in range(lo, int(p['hi']) + 1):
        j = np.searchsorted(ab, f)
        cf = cum[f - lo]
        A = [
            (i, Hs[i].dot(np.linalg.inv(cum[ab[i] - lo])).dot(cf))
            for i in (j - 1, j)
            if 0 <= i < len(ab)
        ]
        if len(A) == 1 or ab[A[1][0]] == f:
            H = A[-1][1]
        else:
            (ia, Ha), (ib, Hb) = A
            t = (f - ab[ia]) / float(ab[ib] - ab[ia])
            H = cv2.getPerspectiveTransform(Q, np.float32((1 - t) * proj(Ha, Q) + t * proj(Hb, Q)))
        H = H / H[2, 2]
        if H.dot([960, 900, 1])[2] < 0:  # keep w > 0 for points in view
            H = -H
        out[f] = H
    return out


def main():
    """Command line: <tag> [video] - the ball in StatsBomb coordinates, frame by frame.

    Writes <tag>_ballsb.npz (abs, x, y, has, tracked, H per frame, onpitch: within MARGIN of
    the lines) and <tag>_ballsb.csv; with `video`, also renders <tag>_pitch.mp4.
    """
    tag = sys.argv[1]
    video = len(sys.argv) > 2 and sys.argv[2] == 'video'
    Hf = per_frame(tag)
    by = render_review_video.picks_for(tag)
    rows = []
    for f in sorted(Hf):
        rec = by.get(f, {})
        if rec.get('src'):
            x, y = proj(Hf[f], np.array([[rec['x'], rec['y']]]))[0]
            rows.append((f, rec['src'] == 'track', x, y, 1))
        else:
            rows.append((f, False, np.nan, np.nan, 0))
    R = np.array(rows, float)
    # beyond the lines (+ MARGIN): out of play, or in the air - a raised ball
    # projects too far from the camera, near the horizon without bound
    with np.errstate(invalid='ignore'):
        onp = (
            (R[:, 4] > 0)
            & (R[:, 2] > -MARGIN)
            & (R[:, 2] < 120 + MARGIN)
            & (R[:, 3] > -MARGIN)
            & (R[:, 3] < 80 + MARGIN)
        )
    np.savez(
        os.path.join(HERE, '%s_ballsb.npz' % tag),
        abs=R[:, 0].astype(int),
        x=R[:, 2],
        y=R[:, 3],
        has=R[:, 4].astype(bool),
        tracked=R[:, 1].astype(bool),
        H=np.array([Hf[f] for f in sorted(Hf)]),
        onpitch=onp,
    )
    with open(os.path.join(HERE, '%s_ballsb.csv' % tag), 'w') as fh:
        fh.write('frame,match_time_s,ball_x_sb,ball_y_sb,source,on_pitch\n')
        for (f, tr, x, y, h), op in zip(rows, onp):
            if h and not op:
                x = y = None
            fh.write(
                '%d,%.2f,%s,%s,%s,%d\n'
                % (
                    f,
                    f / 30.0,
                    '' if x is None or not h else '%.1f' % x,
                    '' if y is None or not h else '%.1f' % y,
                    '' if not h else ('track' if tr else 'fill'),
                    op,
                )
            )
    h = R[:, 4] > 0
    print(
        'frames %d, ball %d, on pitch %d (%.1f%% of ball frames beyond the lines)'
        % (len(R), h.sum(), onp.sum(), 100 * (1 - onp.sum() / max(1, h.sum()))),
        flush=True,
    )
    if video:
        render(tag, Hf, by)


def render(tag, Hf, by):
    """Write <tag>_pitch.mp4 (1280 x 720): the pitch lines drawn over each frame, a minimap
    with the ball and its trail, and the clock.
    """
    from .. import config

    sc = 2 / 3.0
    Wd, Ht = 1280, 720
    S = np.diag([sc, sc, 1])
    lines = draw_pitch_overlay.polylines()
    ms, mx0, my0 = 3.0, Wd - 12 - 360, 12  # minimap 360x240

    def mm(p):
        return (int(mx0 + p[0] * ms), int(my0 + (80 - p[1]) * ms))  # camera touchline at the bottom

    base = np.zeros((240 + 1, 360 + 1, 3), np.uint8)
    base[:] = (40, 110, 40)
    for pl in lines:
        cv2.polylines(base, [np.int32(pl * ms)], False, (230, 230, 230), 1, cv2.LINE_AA)
    lo, hi = min(Hf), max(Hf)
    cap = cv2.VideoCapture(config.VIDEO)
    cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
    vw = cv2.VideoWriter(
        os.path.join(HERE, '%s_pitch.mp4' % tag), cv2.VideoWriter_fourcc(*'mp4v'), 30, (Wd, Ht)
    )
    trail = []
    t0 = time.time()
    font = cv2.FONT_HERSHEY_SIMPLEX
    foot = np.float32([[0, 1079], [1919, 1079], [1919, 520], [0, 520]])
    for f in range(lo, hi + 1):
        ok, im = cap.read()
        if not ok:
            break
        im = cv2.resize(im, (Wd, Ht), interpolation=cv2.INTER_AREA)
        Hi = S.dot(np.linalg.inv(Hf[f]))
        for pl in lines:
            q = np.c_[pl, np.ones(len(pl))].dot(Hi.T)
            g = q[:, 2] > 0
            q = q[:, :2] / q[:, 2:]
            for a, b, ga, gb in zip(q[:-1], q[1:], g[:-1], g[1:]):
                if ga and gb and abs(a).max() < 4000 and abs(b).max() < 4000:
                    cv2.line(
                        im, tuple(np.int32(a)), tuple(np.int32(b)), (255, 0, 255), 1, cv2.LINE_AA
                    )
        rec = by.get(f, {})
        ball = None
        if rec.get('src'):
            ball = proj(Hf[f], np.array([[rec['x'], rec['y']]]))[0]
            col = render_review_video.GREEN if rec['src'] == 'track' else render_review_video.ORANGE
            c = (int(rec['x'] * sc), int(rec['y'] * sc))
            cv2.circle(im, c, 14, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.circle(im, c, 14, col, 2, cv2.LINE_AA)
        out = ball is not None and not (
            -MARGIN < ball[0] < 120 + MARGIN and -MARGIN < ball[1] < 80 + MARGIN
        )
        if out:
            ball = None
        trail = (trail + [ball])[-60:]
        roi = im[my0 - 6 : my0 + 241 + 30, mx0 - 6 : mx0 + 367]
        roi[:] = (roi * 0.3).astype(np.uint8)
        im[my0 : my0 + 241, mx0 : mx0 + 361] = base
        fp = proj(Hf[f], foot)
        cv2.polylines(im, [np.int32([mm(p) for p in fp])], True, (0, 220, 255), 1, cv2.LINE_AA)
        pts = [mm(b) for b in trail if b is not None]
        for a, b in zip(pts[:-1], pts[1:]):
            cv2.line(im, a, b, (255, 255, 255), 1, cv2.LINE_AA)
        if ball is not None:
            cv2.circle(im, mm(ball), 5, (0, 0, 0), -1, cv2.LINE_AA)
            cv2.circle(im, mm(ball), 4, col, -1, cv2.LINE_AA)
            s = 'ball  x %.1f  y %.1f  (of 120 x 80)' % (ball[0], ball[1])
        elif out:
            s = 'ball beyond the lines (out, or in the air)'
        else:
            s = 'no ball chosen'
        cv2.putText(im, s, (mx0, my0 + 262), font, 0.5, (235, 235, 235), 1, cv2.LINE_AA)
        cv2.putText(
            im,
            'match %s' % render_review_video.clock(f),
            (12, 30),
            font,
            0.7,
            (0, 0, 0),
            4,
            cv2.LINE_AA,
        )
        cv2.putText(
            im,
            'match %s' % render_review_video.clock(f),
            (12, 30),
            font,
            0.7,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        vw.write(im)
        if (f - lo) % 1500 == 0:
            print('%d/%d  %.0f s' % (f - lo, hi - lo, time.time() - t0), flush=True)
    vw.release()
    print('video done %.0f s' % (time.time() - t0))


if __name__ == '__main__':
    main()
