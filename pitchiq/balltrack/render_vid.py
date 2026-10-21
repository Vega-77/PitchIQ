"""What the ball tracker thinks, drawn on the match video.

The full chain as it stands: detector candidates (detect_candidates), classifier
log-odds on every candidate (cls_B), ball_tracker.track_ball on 0.75*p + 0.25*s0, then
gapfill.fill.  Each output frame shows

  green ring    the tracker's ball, classifier agrees (track_ball pick)
  orange ring   the ball motion filled in across a gap (gapfill pick)
  grey ring     no ball chosen; the classifier's best candidate, if its
                log-odds is above 0, shown thin so you can see what the
                tracker declined
  panel         match clock, state, classifier confidence of the pick
                (sigmoid of log-odds), share of frames with a ball so far,
                and the last 10 s of confidence as bars

    python -m pitchiq.balltrack.render_vid <tag> [out.mp4] [scale]     (scale 0.6667 -> 1280x720)
"""

import os
import sys
import time

import numpy as np

from . import ball_tracker
from . import gapfill as G
from . import wm_cls as C

S2 = os.path.dirname(os.path.abspath(__file__))

BASE = dict(off_score=-0.5, enter_cost=12.0, return_cost=6.0, skip_cost=4.0)
A = 0.75
FPS = 30
HIST = 300  # graph width in frames (10 s)
GREEN = (60, 220, 60)
ORANGE = (0, 165, 255)
GREY = (170, 170, 170)


def saved_picks(ver, tag):
    """The picks 2D version `ver` saved (alltests/p2d/<ver>/<tag>.npz), keyed by abs frame.

    A frame with a ball is {src: 'track' | 'fill', x, y, p: 0.0}; one without is {}.
    TAPNext bridge frames (src 3) count as 'fill' unless BRIDGE_TRACK=1.
    """
    bridge_track = os.environ.get('BRIDGE_TRACK') == '1'
    z = np.load(os.path.join(S2, 'alltests', 'p2d', ver, '%s.npz' % tag))
    by = {}
    for f, x, y, s in zip(z['abs'], z['x'], z['y'], z['src']):
        rec = {}
        if s > 0 and np.isfinite(x):
            src = 'track' if s == 1 or (s == 3 and bridge_track) else 'fill'
            rec = dict(src=src, x=float(x), y=float(y), p=0.0)
        by[int(f)] = rec
    return by


def picks_for(tag):
    """The tracker's ball per abs frame: {top: (x, y, p), src, x, y, p}, src 'track' | 'fill'.

    With PIQ_P2D=<ver> set (alltests/mk3d.py sets it), saved_picks(ver, tag) instead.
    """
    if os.environ.get('PIQ_P2D'):
        return saved_picks(os.environ['PIQ_P2D'], tag)
    F = ball_tracker.load_frames(tag)
    C.attach(F, tag)
    G.attach_pan(F, tag)
    for fr in F:
        fr['s'] = A * fr['p'] + (1 - A) * fr['s0']
    base = ball_tracker.track_ball(F, return_drift=12.0, return_gate=2.0, **BASE)
    out = G.fill(F, base)
    by = {}
    for fr, b, o in zip(F, base, out):
        top = int(np.argmax(fr['p']))
        rec = dict(top=(fr['x'][top], fr['y'][top], fr['p'][top]))
        if o is not None:
            rec['src'] = 'track' if b is not None else 'fill'
            rec['x'], rec['y'], rec['p'] = fr['x'][o], fr['y'][o], fr['p'][o]
        by[fr['abs']] = rec
    return by


def sig(p):
    return 1.0 / (1.0 + np.exp(-p))


def clock(ab):
    s = ab / FPS
    return '%d:%02d' % (s // 60, s % 60)


def main():
    import cv2
    from . import config

    tag = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(S2, tag + '.mp4')
    sc = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0 / 3.0
    z = np.load(os.path.join(S2, '%s.npz' % tag), allow_pickle=True)
    lo, hi = int(z['lo']), int(z['hi'])
    by = picks_for(tag)
    print('picks ready: %d frames with candidates' % len(by), flush=True)

    cap = cv2.VideoCapture(config.VIDEO)
    cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
    W, H = int(1920 * sc), int(1080 * sc)
    vw = cv2.VideoWriter(out, cv2.VideoWriter_fourcc(*'mp4v'), FPS, (W, H))
    assert vw.isOpened(), 'VideoWriter did not open'
    hist = []  # (src, conf) per frame
    n = dict(track=0, fill=0, none=0)
    t0 = time.time()
    font = cv2.FONT_HERSHEY_SIMPLEX
    for ab in range(lo, hi + 1):
        ok, img = cap.read()
        if not ok:
            break
        img = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
        rec = by.get(ab, {})
        src = rec.get('src')
        if src:
            n[src] += 1
            cx, cy = int(rec['x'] * sc), int(rec['y'] * sc)
            col = GREEN if src == 'track' else ORANGE
            cv2.circle(img, (cx, cy), 16, (0, 0, 0), 5, cv2.LINE_AA)
            cv2.circle(img, (cx, cy), 16, col, 3, cv2.LINE_AA)
            conf = sig(rec['p'])
            cv2.putText(
                img,
                '%d%%' % round(100 * conf),
                (cx + 20, cy - 12),
                font,
                0.55,
                (0, 0, 0),
                4,
                cv2.LINE_AA,
            )
            cv2.putText(
                img, '%d%%' % round(100 * conf), (cx + 20, cy - 12), font, 0.55, col, 1, cv2.LINE_AA
            )
            hist.append((src, conf))
        else:
            n['none'] += 1
            conf = None
            if 'top' in rec and rec['top'][2] > 0:
                tx, ty = int(rec['top'][0] * sc), int(rec['top'][1] * sc)
                cv2.circle(img, (tx, ty), 12, GREY, 1, cv2.LINE_AA)
            hist.append((None, 0.0))
        hist = hist[-HIST:]

        # panel
        px, py, pw, ph = 12, H - 178, HIST + 150, 166
        roi = img[py : py + ph, px : px + pw]
        roi[:] = (roi * 0.35).astype(np.uint8)
        state = {
            'track': ('BALL  (tracked)', GREEN),
            'fill': ('BALL  (motion fill)', ORANGE),
            None: ('no ball chosen', GREY),
        }[src]
        tot = sum(n.values())
        found = 100.0 * (n['track'] + n['fill']) / max(1, tot)
        lines = [
            ('match %s   frame %d' % (clock(ab), ab), (235, 235, 235)),
            (state[0] + ('   conf %d%%' % round(100 * conf) if conf is not None else ''), state[1]),
            ('ball found in %.0f%% of frames so far' % found, (235, 235, 235)),
        ]
        for i, (s, c) in enumerate(lines):
            cv2.putText(img, s, (px + 10, py + 22 + 22 * i), font, 0.55, c, 1, cv2.LINE_AA)
        gx, gy, gh = px + 10, py + ph - 12, 70
        cv2.line(img, (gx, gy), (gx + HIST, gy), (120, 120, 120), 1)
        cv2.line(img, (gx, gy - gh // 2), (gx + HIST, gy - gh // 2), (80, 80, 80), 1)
        for k, (s, c) in enumerate(hist):
            if s is None:
                continue
            x = gx + HIST - len(hist) + k
            cv2.line(
                img, (x, gy), (x, gy - max(1, int(gh * c))), GREEN if s == 'track' else ORANGE, 1
            )
        cv2.putText(
            img, '100%', (gx + HIST + 6, gy - gh + 4), font, 0.4, (200, 200, 200), 1, cv2.LINE_AA
        )
        cv2.putText(
            img,
            '50%',
            (gx + HIST + 6, gy - gh // 2 + 4),
            font,
            0.4,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            img, 'last 10 s', (gx + HIST + 6, gy), font, 0.4, (200, 200, 200), 1, cv2.LINE_AA
        )
        lroi = img[8:84, 8:318]
        lroi[:] = (lroi * 0.35).astype(np.uint8)
        for i, (s, c) in enumerate(
            [
                ('tracked: classifier agrees', GREEN),
                ('motion fill: across a gap', ORANGE),
                ('best candidate, not chosen', GREY),
            ]
        ):
            cv2.circle(img, (26, 26 + 22 * i), 7, c, 2, cv2.LINE_AA)
            cv2.putText(img, s, (42, 31 + 22 * i), font, 0.5, (235, 235, 235), 1, cv2.LINE_AA)
        vw.write(img)
        if (ab - lo) % 1500 == 0:
            print('%d/%d  %.0f s' % (ab - lo, hi - lo, time.time() - t0), flush=True)
    vw.release()
    tot = sum(n.values())
    print(
        'done %s  %d frames  tracked %.1f%%  fill %.1f%%  none %.1f%%  %.0f s'
        % (
            out,
            tot,
            100.0 * n['track'] / tot,
            100.0 * n['fill'] / tot,
            100.0 * n['none'] / tot,
            time.time() - t0,
        )
    )


if __name__ == '__main__':
    main()
