"""Player ID lab media: who is each tracked player, so whole-game identity can be measured.

    python -m pitchiq.experiments.idlab build <tag> <out> [--windows 1,7,13] [--per 20] [--min-s 15]

The whole-half map (experiments/wholemap.py) links each half in back-to-back 3-min windows,
and every window starts its identities afresh: nothing says that a player at minute 5 is the
same person at minute 40. This cuts one question per tracked player ("fragment": one linker
identity in one window) in the chosen windows of the map, and the user names the person,
by shirt number where it can be read. Two fragments with the same name are the same person,
so the answers are the truth for joining fragments across a window and across the half.

A still is often not enough at this camera's resolution (a number is readable only on a big
player with his back turned), so each question is a clip that follows the player for up to
CLIP_S seconds, the stretch where he is biggest on screen: a sharpened close-up beside a wider
view with him boxed, at CLIP_FPS so a number shows when he turns. The page plays it slowly
and steps frame by frame, and gives the video time to look the moment up in the original.

Fragments are the map's own: same windows, same linker run (it draws nothing at random), so
`trk` is the map's track id (pid + 10000 * window). Each question also keeps `samples`, the
fragment's box once a second, so a later linker is scored against the names without asking
again.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
MAPS = (REPO.parent / 'PitchIQ-data' / 'stats' / 'map').resolve()
CLIP_S, CLIP_FPS = 12.0, 15  # seconds of each fragment shown, frames a second
ZOOM, CTX = 420, 420  # panel sizes (square): close-up, wider view
ZOOM_K, ZOOM_MIN = 1.7, 100  # close-up side = ZOOM_K x box height, at least ZOOM_MIN px
CTX_K, CTX_MIN = 7.0, 420
THUMB = 160
SMOOTH = 9  # frames each side for the crop's centre and size: a steady camera, not a jumpy one
HELD_OUT = {'nb2', 'pi1', 'wh1', 'co1', 'wb2'}
YELLOW, BLACK, WHITE = (0, 230, 255), (0, 0, 0), (255, 255, 255)
CRF = 34  # VP9 quality: a number reads the same as in the raw frame at a tenth of VP8's size


def _plain(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o).__name__)


def _smooth(v, k=SMOOTH):
    """Moving mean, edges padded with the end values."""
    if len(v) < 3:
        return v.astype(float)
    p = np.pad(v.astype(float), k, mode='edge')
    c = np.cumsum(np.insert(p, 0, 0.0))
    return (c[2 * k + 1 :] - c[: -2 * k - 1]) / (2 * k + 1)


def _kit_l(ident, dw):
    """Median shirt lightness; rows index the window's own detections (dw), not the half's."""
    rows = ident.rows[ident.rows >= 0]
    return float(np.nanmedian(dw.desc[rows, 0])) if len(rows) else np.nan


class _Clip:
    """Raw frames piped to ffmpeg (imageio-ffmpeg's build) as VP9 webm."""

    def __init__(self, path, size):
        import subprocess

        import imageio_ffmpeg

        self.p = subprocess.Popen(
            [imageio_ffmpeg.get_ffmpeg_exe(), '-loglevel', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
             '-s', '%dx%d' % size, '-r', str(CLIP_FPS), '-i', '-', '-c:v', 'libvpx-vp9', '-crf', str(CRF),
             '-b:v', '0', '-row-mt', '1', '-deadline', 'good', '-cpu-used', '4', '-pix_fmt', 'yuv420p', '-an', str(path)],
            stdin=subprocess.PIPE,
        )

    def write(self, fr):
        self.p.stdin.write(np.ascontiguousarray(fr).tobytes())

    def release(self):
        self.p.stdin.close()
        assert self.p.wait() == 0, 'ffmpeg failed'


def pick(res, dw, fps, per, min_s):
    """The window's longest-seen players and keepers, teams taken in turn; kit shade by team."""
    pool = [
        i
        for i in res.identities
        if i.role in ('player', 'keeper')
        and i.team in ('team_a', 'team_b')
        and (~i.interp).sum() >= min_s * fps
    ]
    L = {}
    for t in ('team_a', 'team_b'):
        ls = [_kit_l(i, dw) for i in res.identities if i.team == t and i.role == 'player']
        L[t] = float(np.nanmedian(ls)) if ls else np.nan
    light = 'team_a' if L['team_a'] >= L['team_b'] else 'team_b'
    by = {t: sorted((i for i in pool if i.team == t), key=lambda i: -(~i.interp).sum()) for t in L}
    out = []
    while len(out) < per and (by['team_a'] or by['team_b']):
        for t in ('team_a', 'team_b'):
            if by[t] and len(out) < per:
                out.append(by[t].pop(0))
    kit = {t: ('light' if t == light else 'dark') for t in L}
    return out, kit


def plan(ident, fps):
    """The clip: CLIP_S seconds where the player is biggest on screen, every few frames, with a
    smoothed crop centre and size per shown frame."""
    fr, bx = ident.frames, ident.boxes.astype(float)
    h = np.where(ident.interp, 0.0, bx[:, 3] - bx[:, 1])
    n = min(len(fr), int(CLIP_S * fps))
    c = np.cumsum(np.insert(h, 0, 0.0))
    s = int(np.argmax(c[n:] - c[:-n])) if len(fr) > n else 0
    step = max(1, int(round(fps / CLIP_FPS)))
    idx = np.arange(s, s + n, step)
    cx = _smooth((bx[:, 0] + bx[:, 2]) / 2)
    cy = _smooth((bx[:, 1] + bx[:, 3]) / 2)
    hs = _smooth(bx[:, 3] - bx[:, 1])
    return [
        dict(f=int(fr[i]), box=bx[i], hidden=bool(ident.interp[i]), cx=cx[i], cy=cy[i], h=hs[i])
        for i in idx
    ]


def _square(cx, cy, side, W, H):
    side = float(min(side, H))
    x0 = float(np.clip(cx - side / 2, 0, W - side))
    y0 = float(np.clip(cy - side / 2, 0, H - side))
    return x0, y0, side


def render(img, p):
    """One clip frame: close-up (sharpened, marker over the head) | wider view (player boxed)."""
    import cv2

    H, W = img.shape[:2]
    b = p['box']
    x0, y0, s = _square(p['cx'], p['cy'] - 0.08 * p['h'], max(ZOOM_K * p['h'], ZOOM_MIN), W, H)
    z = cv2.resize(
        img[int(y0) : int(y0 + s), int(x0) : int(x0 + s)], (ZOOM, ZOOM), interpolation=cv2.INTER_LANCZOS4
    )
    blur = cv2.GaussianBlur(z, (0, 0), 1.4)
    z = cv2.addWeighted(z, 1.7, blur, -0.7, 0)
    k = ZOOM / s
    u, v = ((b[0] + b[2]) / 2 - x0) * k, (b[1] - y0) * k
    tri = np.array([[u - 9, v - 22], [u + 9, v - 22], [u, v - 8]], np.int32)
    if p['hidden']:
        cv2.polylines(z, [tri], True, YELLOW, 2, cv2.LINE_AA)
    else:
        cv2.fillPoly(z, [tri], YELLOW, cv2.LINE_AA)
        cv2.polylines(z, [tri], True, BLACK, 1, cv2.LINE_AA)
    X0, Y0, S = _square(p['cx'], p['cy'], max(CTX_K * p['h'], CTX_MIN), W, H)
    c = cv2.resize(img[int(Y0) : int(Y0 + S), int(X0) : int(X0 + S)], (CTX, CTX), interpolation=cv2.INTER_AREA)
    K = CTX / S
    r = [int((b[0] - X0) * K) - 3, int((b[1] - Y0) * K) - 3, int((b[2] - X0) * K) + 3, int((b[3] - Y0) * K) + 3]
    cv2.rectangle(c, (r[0], r[1]), (r[2], r[3]), BLACK, 3, cv2.LINE_AA)
    cv2.rectangle(c, (r[0], r[1]), (r[2], r[3]), YELLOW, 1 if p['hidden'] else 2, cv2.LINE_AA)
    out = np.hstack([z, np.full((ZOOM, 4, 3), 20, np.uint8), c])
    return out


def thumb(img, b):
    import cv2

    H, W = img.shape[:2]
    h = b[3] - b[1]
    x0, y0, s = _square((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, max(1.3 * h, 60), W, H)
    return cv2.resize(img[int(y0) : int(y0 + s), int(x0) : int(x0 + s)], (THUMB, THUMB), interpolation=cv2.INTER_LANCZOS4)


def build(tag, out: Path, windows=None, per=20, min_s=15.0, final=False):
    import cv2

    if tag in HELD_OUT and not final:
        raise SystemExit(f'{tag} is held out: scored once, at the end (--final)')
    from pitchiq.experiments.wholemap import video_of

    v = video_of(tag)
    if v:
        os.environ['PIQ_VIDEO'] = v  # before player_lab is imported: unset it reads the scrimmage
    sys.path.insert(0, str(REPO))
    from pitchiq.experiments import player_lab
    from pitchiq.experiments.spotlab import _subset, linked

    m = np.load(MAPS / f'{tag}.npz')
    wl, wh, fps = m['win_lo'], m['win_hi'], float(m['fps'])
    if windows is None:
        windows = sorted({int(round(x)) for x in np.linspace(1, len(wl) - 2, 3)})
    spans = [(int(wl[w]), int(wh[w])) for w in windows]
    det, meta = player_lab.load_detections(tag)
    (out / 'clips').mkdir(parents=True, exist_ok=True)
    (out / 'thumbs').mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(player_lab.VIDEO)
    qs = []
    for w, (lo, hi), res in zip(windows, spans, linked(tag, det, meta, spans)):
        frags, kit = pick(res, _subset(det, (det.frame >= lo) & (det.frame < hi)), fps, per, min_s)
        print('window %d %d..%d: %d identities, %d asked' % (w, lo, hi, len(res.identities), len(frags)), flush=True)
        jobs = {}  # frame -> [(question, shown frame)]
        live = []
        for ident in frags:
            P = plan(ident, fps)
            ok = ~ident.interp
            j = int(np.argmax(np.where(ok, ident.boxes[:, 3] - ident.boxes[:, 1], -1)))
            qid = '%s_w%02d_p%04d' % (tag, w, ident.pid)
            q = dict(
                id=qid, tag=tag, win=int(w), lo=lo, hi=hi, pid=int(ident.pid), trk=int(ident.pid) + 10000 * int(w),
                team=ident.team, kit=kit[ident.team], role=ident.role,
                first=int(ident.frames[0]), last=int(ident.frames[-1]),
                secs=round(len(ident.frames) / fps, 1), seen=round(float(ok.mean()), 2),
                h_px=round(float(np.median(ident.boxes[ok, 3] - ident.boxes[ok, 1])), 0),
                clip=dict(file='clips/%s.webm' % qid, fps=CLIP_FPS, n=len(P), frames=[p['f'] for p in P],
                          hidden=[int(p['hidden']) for p in P]),
                thumb='thumbs/%s.jpg' % qid,
                samples=[[int(ident.frames[i])] + [round(float(x), 1) for x in ident.boxes[i]]
                         for i in range(0, len(ident.frames), int(fps)) if ok[i]],
            )
            q['_P'], q['_left'], q['_thumb_f'], q['_thumb_b'] = P, len(P), int(ident.frames[j]), ident.boxes[j]
            for k, p in enumerate(P):
                jobs.setdefault(p['f'], []).append((q, k))
            jobs.setdefault(q['_thumb_f'], []).append((q, -1))
            live.append(q)
        if not jobs:
            continue
        writers = {}
        f, last = min(jobs), max(jobs)
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        while f <= last:
            if f not in jobs:
                cap.grab()
                f += 1
                continue
            ok_, img = cap.read()
            if not ok_:
                break
            for q, k in jobs[f]:
                if k < 0:
                    cv2.imwrite(str(out / q['thumb']), thumb(img, q['_thumb_b']), [cv2.IMWRITE_JPEG_QUALITY, 88])
                    continue
                fr = render(img, q['_P'][k])
                vw = writers.get(q['id'])
                if vw is None:
                    vw = writers[q['id']] = _Clip(out / q['clip']['file'], (fr.shape[1], fr.shape[0]))
                vw.write(fr)
                q['_left'] -= 1
                if q['_left'] == 0:
                    writers.pop(q['id']).release()
            f += 1
        for vw in writers.values():
            vw.release()
        for q in live:
            for k in [k for k in q if k.startswith('_')]:
                q.pop(k)
        live.sort(key=lambda q: (q['kit'], q['first']))
        qs += live
    video = Path(player_lab.VIDEO).name
    (out / 'questions.json').write_text(
        json.dumps(dict(tag=tag, video=video, fps=fps, questions=qs), default=_plain)
    )
    print('%d questions -> %s' % (len(qs), out), flush=True)
    return qs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('tag')
    b.add_argument('out', type=Path)
    b.add_argument('--windows', help='map window indices, comma separated (default: 3 spread out)')
    b.add_argument('--per', type=int, default=20)
    b.add_argument('--min-s', type=float, default=15.0)
    b.add_argument('--final', action='store_true')
    a = ap.parse_args(argv)
    w = [int(x) for x in a.windows.split(',')] if a.windows else None
    build(a.tag, a.out, w, a.per, a.min_s, a.final)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
