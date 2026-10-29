"""Map lab media: questions about the top-down map, answered as truth where a tap can say it.

    PIQ_VIDEO=<video> python -m pitchiq.experiments.maplab half <tag> <out> --lo L --hi H
    python -m pitchiq.experiments.maplab strip <game> <out>
    python -m pitchiq.experiments.maplab merge <set dir> <half or strip dir> ...

`half` links --windows stretches of --win-s seconds of a passed half (players, Hybrid 90
ball, smoothed camera), builds pitchiq/topdown on each, and cuts --per questions of
each type, half from the random pool and half from the hard one:

  landing     an air ball from take-off to first bounce (or a fast ground ball):
              tap where it first touched the ground
  possession  3 s before to 2 s after a moment: which team has the ball then?
  feet        one person boxed in a still: tap between their feet, pick a team
  missing     a still with the map's people ringed: tap anyone on the pitch without one
  extra       the same: tap anyone ringed who is not playing
  same        crops from before and after a join the map made: the same person?
  keeper      a still with a goal area in view: tap the keeper
  motion      6 s of one player on camera, then the same 6 s of their dot alone
  trust       8 s of camera, then the same 8 s of the map: rate players and ball 1-5

`strip` writes one thumbnail every 30 s over a whole video, for marking long
stoppages, with the half edges from halves.json. `merge` puts halves and strips
into one set for the page (pitchiq/experiments/maplab_page/index.html).

The tap types store what happened, not a verdict on what was drawn, so a later
version of the map is scored again without asking again (maplab_score.py). Every
question keeps the map's own answer in `model`; scores use sample == "random".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

from pitchiq.experiments.spotlab import _crop_rect, _feet_ring, _iou, _Window, linked

TYPES = ('landing', 'possession', 'feet', 'missing', 'extra', 'same', 'keeper', 'motion', 'trust')
FPS, STEP = 10, 3  # clips play at 10 fps: every third frame
CAM = (960, 540)
PANEL = (640, 400)
STILL = (1280, 720)
CROP = (100, 200)
THUMB = (480, 270)
STRIP_S, STRIP_THUMB, STRIP_COLS = 30, (192, 108), 10
STRIP_BIG, BIG_COLS = (1280, 720), 3  # the frame shown large when a thumbnail is tapped
PACK_S, PACK_MB = 60.0, 12.0  # a published file may be at most 16 MB
YELLOW, GREEN, ORANGE, WHITE, BLACK = (
    (0, 230, 255),
    (80, 230, 80),
    (40, 140, 255),
    (255, 255, 255),
    (0, 0, 0),
)
DARK, LIGHT, KEEPER, REF, UNK = (
    (60, 60, 235),
    (235, 170, 50),
    (0, 220, 255),
    (200, 90, 255),
    (200, 200, 200),
)
MS = 4.4  # map px per StatsBomb unit in the map panel


def _plain(o):
    """json.dumps default: numpy scalars and arrays as plain numbers and lists."""
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o).__name__)


def _mv():
    """pitchiq/balltrack/match_video, imported late: it changes directory on import."""
    from pitchiq.balltrack import match_video

    return match_video


def _jpg(path: Path, img, q=80):
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, q])


class _Packs:
    """Clips of any length appended into webm packs of about PACK_S seconds or PACK_MB; the page
    seeks."""

    def __init__(self, out: Path):
        self.out, self.cur, self.n = out, {}, 0
        (out / 'packs').mkdir(parents=True, exist_ok=True)

    def clip(self, frames):
        import cv2

        size = (frames[0].shape[1], frames[0].shape[0])
        cur = self.cur.get(size)
        if (
            cur is None
            or cur[2] >= PACK_S * FPS
            or (self.out / cur[0]).stat().st_size > PACK_MB * 2**20
        ):
            if cur is not None:
                cur[1].release()
            name = 'packs/p%03d.webm' % self.n
            self.n += 1
            vw = cv2.VideoWriter(str(self.out / name), cv2.VideoWriter_fourcc(*'VP80'), FPS, size)
            assert vw.isOpened(), 'no VP80 writer'
            cur = self.cur[size] = [name, vw, 0]
        start = cur[2]
        for fr in frames:
            cur[1].write(fr)
        cur[2] += len(frames)
        return dict(pack=cur[0], t0=start / FPS, t1=cur[2] / FPS, w=size[0], h=size[1])

    def close(self):
        for cur in self.cur.values():
            cur[1].release()


class _Half:
    """One linked stretch with its map, ball and possession, and the question pools."""

    def __init__(self, tag, lo, hi, det, meta, proj, pitch, Hs, a0, rng, res):
        MV = _mv()
        self.W = _Window(tag, lo, hi, det, meta, proj, pitch, Hs, a0, rng, res=res)
        self.lo, self.hi, self.rng, self.res = lo, hi, rng, res
        self.td, (self.kx, self.ky) = MV.pitch_map(tag, res, Hs, a0, lo, hi)
        self.length, self.width = pitch.length_m, pitch.width_m
        _, _, self.bsb, _, _ = MV.ball(tag, lo, hi)
        self.bimg, self.bkind = self.W.bimg, self.W.bkind
        kits = self.W.kits
        la, lb = kits['team_a']['lab'][0], kits['team_b']['lab'][0]
        self.dark = 'team_a' if not np.isfinite(lb) or (np.isfinite(la) and la <= lb) else 'team_b'
        self.light = 'team_b' if self.dark == 'team_a' else 'team_a'
        self.col = {self.dark: DARK, self.light: LIGHT}
        self.kits = dict(kits, dark=self.dark, light=self.light)
        self.poss = self._possession()

    def H(self, f):
        return self.W.H(f)

    def fits(self, a, b):
        return a >= self.lo and b < self.hi

    def _possession(self):
        from pitchiq.possession import build_states, prepare_states

        fs = list(range(self.lo, self.hi))
        ball = {
            f: (float(self.bimg[f - self.lo, 0]), float(self.bimg[f - self.lo, 1]))
            for f in fs
            if self.bkind[f - self.lo]
        }
        team = {i.pid: i.team for i in self.res.identities}
        play = {i.pid for i in self.res.identities if i.role in ('player', 'keeper')}
        boxes = {f: [(i.pid, b) for i, b in self.res.at(f)] for f in fs}
        st = build_states(
            fs,
            ball,
            boxes,
            lambda p: team.get(p, 'unknown'),
            {f: f / 30.0 for f in fs},
            is_player=lambda p: p in play,
        )
        return [s.team for s in prepare_states(st)]

    def seen_dots(self, f):
        return [d for d in self.td.at(f) if d.seen and d.ident is not None]

    def dot_of(self, ident, f):
        for d in self.seen_dots(f):
            if d.ident.pid == ident.pid:
                return [round(float(d.x), 2), round(float(d.y), 2)]
        return None

    def team_word(self, t):
        return 'dark' if t == self.dark else 'light' if t == self.light else 'unknown'

    def sb(self, f, p):
        q = self.H(f) @ [p[0], p[1], 1.0]
        return q[:2] / q[2]

    def px(self, f, xy_sb):
        q = np.linalg.inv(self.H(f)) @ [xy_sb[0], xy_sb[1], 1.0]
        return (q[:2] / q[2]) if q[2] > 0 else None

    def in_frame(self, p, m=0):
        return p is not None and m <= p[0] < 1920 - m and m <= p[1] < 1080 - m

    # ---- pools: [(frame, payload)] ------------------------------------------------
    def pool(self, kind, hard):
        R, out = self.res, []
        if kind == 'landing':
            air = np.array([k == 'air' for k in self.bkind])
            if hard:  # fast ground balls: a flight the tracker may have missed
                gnd = np.array([k == 'ground' for k in self.bkind])
                for k in range(0, len(gnd) - 15, 5):
                    if gnd[k] and gnd[k + 15] and np.all(np.isfinite(self.bsb[[k, k + 15]])):
                        if (
                            np.hypot(*(self.bsb[k + 15] - self.bsb[k])) > 8
                            and not air[k : k + 45].any()
                        ):
                            f = self.lo + k
                            if self.fits(f - 9, f + 60):
                                out.append((f, dict(a=f - 9, b=f + 60, kick=f)))
            else:
                d = np.diff(np.r_[0, air.astype(np.int8), 0])
                for s, e in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
                    if e - s < 6:
                        continue
                    a, b = self.lo + s - 9, self.lo + min(e + 21, s + 120)
                    if self.fits(a, b):
                        land = self.bsb[e - 1] if np.isfinite(self.bsb[e - 1]).all() else None
                        out.append(
                            (
                                self.lo + s,
                                dict(
                                    a=a,
                                    b=b,
                                    kick=self.lo + s,
                                    end=self.lo + e - 1,
                                    land=None
                                    if land is None
                                    else [round(float(v), 2) for v in land],
                                ),
                            )
                        )
        elif kind == 'possession':
            P = self.poss
            for f in range(self.lo + 90, self.hi - 61, 15):
                k = f - self.lo
                near = set(P[max(0, k - 30) : k + 31])
                if hard == (len(near) > 1 or P[k] == 'unknown'):
                    out.append((f, dict(a=f - 90, b=f + 60, team=P[k])))
        elif kind == 'feet':
            for ident in R.identities:
                for k in np.flatnonzero(~ident.interp)[::10]:
                    f = int(ident.frames[k])
                    if not self.fits(f, f + 1):
                        continue
                    b = ident.boxes[k]
                    crowd = any(_iou(b, c) > 0.05 for i, c in R.at(f) if i is not ident)
                    if hard == (crowd or ident.role == 'off'):
                        out.append((f, ident))
        elif kind in ('missing', 'extra'):
            for f in range(self.lo, self.hi, 30):
                ringed = [(d.ident, d.ident.box_at(f)) for d in self.seen_dots(f)]
                ringed = [(i, b) for i, b in ringed if b is not None]
                if kind == 'missing':
                    det = self.W.det
                    rows = np.flatnonzero((det.frame == f) & (det.conf >= 0.4))
                    miss = [
                        det.box[r]
                        for r in rows
                        if not any(_iou(det.box[r], c) > 0.3 for _, c in ringed)
                    ]
                    cands = miss if hard else [b for _, b in ringed]
                else:
                    odd = []
                    for i, b in ringed:
                        x, y = self.sb(f, ((b[0] + b[2]) / 2, b[3]))
                        if (
                            i.role in ('referee', 'off')
                            or i.team == 'unknown'
                            or not (3 < x < 117 and 3 < y < 77)
                        ):
                            odd.append((i, b))
                    cands = [b for _, b in odd] if hard else [b for _, b in ringed]
                for b in cands:
                    out.append((f, dict(c=((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), ringed=ringed)))
        elif kind == 'same':
            out = [j for j in self._joins() if j[1]['hard'] == hard]
        elif kind == 'keeper':
            for f in range(self.lo, self.hi, 30):
                for spot in ((12, 40), (108, 40)):
                    if self.in_frame(self.px(f, spot), 40):
                        out.append((f, dict(spot=spot)))
        elif kind == 'motion':
            T = self.td.path.shape[1]
            n_slot = np.flatnonzero(self.td.slot_of >= 0)
            for n in n_slot:
                for t in range(0, T - 180, 60):
                    seen = self.td.owner[n, t : t + 180] >= 0
                    if seen.mean() < 0.6:
                        continue
                    p = self.td.path[n, t : t + 180]
                    acc = float(
                        np.percentile(np.linalg.norm(np.diff(p, 2, axis=0), axis=1), 95) * 900
                    )
                    out.append(
                        (self.lo + t, dict(n=int(n), a=self.lo + t, b=self.lo + t + 180, acc=acc))
                    )
            if out:
                cut = np.percentile([o[1]['acc'] for o in out], 90)
                out = [o for o in out if (o[1]['acc'] >= cut) == hard]
        elif kind == 'trust':
            if not hard:
                out = [(f, dict(a=f, b=f + 240)) for f in range(self.lo, self.hi - 240, 60)]
        return out

    def _joins(self):
        """Consecutive runs of one map slot seen as two stretches with a gap of a second or more."""
        td, out = self.td, []
        ident = {i.pid: i for i in self.res.identities}
        for s in sorted(set(td.slot_of[td.slot_of >= 0].tolist())):
            rows = np.flatnonzero(td.slot_of == s)
            own = td.owner[rows].max(axis=0)
            runs, cur = [], None
            for t, p in enumerate(own):
                if p >= 0 and cur is not None and cur[0] == p and t == cur[2] + 1:
                    cur[2] = t
                elif p >= 0:
                    cur = [int(p), t, t]
                    runs.append(cur)
            for r0, r1 in zip(runs, runs[1:]):
                gap = r1[1] - r0[2]
                if gap < 30 or r0[2] - r0[1] < 30 or r1[2] - r1[1] < 30:
                    continue
                a, b = ident.get(r0[0]), ident.get(r1[0])
                if a is None or b is None:
                    continue
                pa = td.path[rows, r0[2]].mean(axis=0)
                pb = td.path[rows, r1[1]].mean(axis=0)
                hard = gap > 150 or (a.pid != b.pid and np.hypot(*(pb - pa)) > 15)
                out.append(
                    (
                        self.lo + r1[1],
                        dict(
                            a=a,
                            b=b,
                            ra=(self.lo + r0[1], self.lo + r0[2]),
                            rb=(self.lo + r1[1], self.lo + r1[2]),
                            gap=int(gap),
                            hard=hard,
                        ),
                    )
                )
        return out

    # ---- the map panel ------------------------------------------------------------
    def mm(self, x, y):
        ox = (PANEL[0] - int(128 * MS)) // 2
        oy = (PANEL[1] - int(88 * MS)) // 2
        return (int(ox + (np.clip(x, -4, 124) + 4) * MS), int(oy + (84 - np.clip(y, -4, 84)) * MS))

    def map_base(self):
        import cv2

        MV = _mv()
        im = np.zeros((PANEL[1], PANEL[0], 3), np.uint8)
        im[:] = (24, 20, 16)
        cv2.rectangle(im, self.mm(-3, 83), self.mm(123, -3), (45, 105, 50), -1)
        for pl in MV.draw_pitch_overlay.polylines():
            cv2.polylines(
                im,
                [np.int32([self.mm(x, y) for x, y in pl])],
                False,
                (225, 235, 225),
                1,
                cv2.LINE_AA,
            )
        return im

    def dot_colour(self, group, team=None):
        t, role = group
        if role == 'referee':
            return REF
        if role == 'keeper':
            return KEEPER
        return self.col.get(team or t, UNK)

    def map_frame(self, f, base, only=None, ball=False):
        """The map at f; `only` = a track index to draw alone and ring in yellow."""
        import cv2

        MV = _mv()
        im = base.copy()
        t = f - self.lo
        td = self.td
        if only is not None:
            p = self.mm(td.path[only, t, 0] * self.kx, td.path[only, t, 1] * self.ky)
            c = self.dot_colour(td.group[only])
            if td.owner[only, t] >= 0:
                cv2.circle(im, p, 7, BLACK, -1, cv2.LINE_AA)
                cv2.circle(im, p, 5, c, -1, cv2.LINE_AA)
            elif td.alpha[only, t] > 0.02:
                MV.blend_dot(im, p, 5, c, float(td.alpha[only, t]), ring=True)
            cv2.circle(im, p, 12, YELLOW, 2, cv2.LINE_AA)
            return im
        for d in td.at(f):
            p = self.mm(d.x * self.kx, d.y * self.ky)
            if not d.seen:
                MV.blend_dot(im, p, 5, self.dot_colour(d.group), d.alpha, ring=True)
        for d in td.at(f):
            if d.seen:
                p = self.mm(d.x * self.kx, d.y * self.ky)
                cv2.circle(im, p, 7, BLACK, -1, cv2.LINE_AA)
                cv2.circle(im, p, 5, self.dot_colour(d.group, d.drawn_team), -1, cv2.LINE_AA)
        if ball and self.bkind[t] and np.isfinite(self.bsb[t]).all():
            p = self.mm(*self.bsb[t])
            c = ORANGE if self.bkind[t] == 'air' else WHITE
            cv2.circle(im, p, 5, BLACK, -1, cv2.LINE_AA)
            cv2.circle(im, p, 4, c, -1, cv2.LINE_AA)
        return im


def _fit(img, size):
    """Letterbox img into size."""
    import cv2

    W, H = size
    h, w = img.shape[:2]
    s = min(W / w, H / h)
    r = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    out = np.zeros((H, W, 3), np.uint8)
    y0, x0 = (H - r.shape[0]) // 2, (W - r.shape[1]) // 2
    out[y0 : y0 + r.shape[0], x0 : x0 + r.shape[1]] = r
    return out


def _crop(img, rect, size):
    import cv2

    x0, y0, w, h = rect
    return cv2.resize(
        img[int(y0) : int(y0 + h), int(x0) : int(x0 + w)], size, interpolation=cv2.INTER_AREA
    )


def _r(rect):
    return [round(float(v), 1) for v in rect]


def _linked_cached(tag, det, meta, spans, out: Path):
    """spotlab.linked, kept in out/link/ so a rebuild of the media skips the linker."""
    import pickle

    path = [out / 'link' / ('%s_%d_%d.pkl' % (tag, lo, hi)) for lo, hi in spans]
    todo = [s for s, p in zip(spans, path) if not p.exists()]
    fresh = dict(zip(todo, linked(tag, det, meta, todo))) if todo else {}
    for s, p in zip(spans, path):
        if s in fresh:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(pickle.dumps(fresh.pop(s)))
        yield pickle.loads(p.read_bytes())


def make_half(tag, out: Path, lo, hi, per=6, windows=3, win_s=180.0, seed=0, types=TYPES):
    import cv2

    MV = _mv()
    from pitchiq.experiments import player_lab

    rng = np.random.default_rng(seed)
    sm = np.load(Path(MV.HG) / f'{tag}_smooth2.npz')
    Hs, a0 = sm['H'], int(sm['abs'][0])
    det, meta = player_lab.load_detections(tag)
    proj, pitch = player_lab.projector(tag, meta['w'], meta['h'])
    lo, hi = (
        max(lo, a0, int(det.frame.min())),
        min(hi, int(sm['abs'][-1]) + 1, int(det.frame.max()) + 1),
    )
    win = int(win_s * meta['fps'])
    spans = [(int(s), int(s) + win) for s in np.linspace(lo, hi - win, windows)]
    out.mkdir(parents=True, exist_ok=True)
    packs = _Packs(out)
    cap = cv2.VideoCapture(player_lab.VIDEO)
    qs = []
    n_of = {}
    for w, ((wlo, whi), res) in enumerate(zip(spans, _linked_cached(tag, det, meta, spans, out))):
        print('window %d: %d..%d' % (w, wlo, whi), flush=True)
        S = _Half(tag, wlo, whi, det, meta, proj, pitch, Hs, a0, rng, res)
        base = S.map_base()
        want = []
        for kind in types:
            n = per // windows
            for hard in (False, True):
                if kind in ('keeper', 'trust') and hard:
                    continue
                k = n if kind in ('keeper', 'trust') else (n + 1) // 2 if not hard else n // 2
                pool = S.pool(kind, hard)
                if not pool:
                    continue
                for i in rng.choice(len(pool), min(k, len(pool)), replace=False):
                    f, pay = pool[int(i)]
                    sample = 'random' if not hard else ('ground' if kind == 'landing' else 'hard')
                    want.append(dict(kind=kind, f=int(f), pay=pay, sample=sample))
        jobs = {}  # frame -> [(q, fn)]
        for q in want:
            _plan(S, q, jobs)
        if not jobs:
            continue
        f, last = min(jobs), max(jobs)
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        while f <= last:
            if f not in jobs:
                cap.grab()
                f += 1
                continue
            ok, img = cap.read()
            if not ok:
                break
            for q, fn in jobs[f]:
                fn(img)
                if q['left'] == 0 and not q.get('done'):
                    q['done'] = True
                    qs.append(_finish(S, q, w, tag, out, packs, base, len(qs), n_of))
            f += 1
    packs.close()
    qs = [qs[i] for i in rng.permutation(len(qs))]
    (out / 'questions.json').write_text(
        json.dumps(dict(tag=tag, fps=FPS, step=STEP, questions=qs), default=_plain)
    )
    return qs


def _plan(S, q, jobs):
    """Register, per source frame, what each question needs from it."""
    import cv2

    kind, f, pay = q['kind'], q['f'], q['pay']
    q['bufs'] = {}

    def add(fr, key, fn):
        q['left'] = q.get('left', 0) + 1

        def run(img, key=key, fn=fn):
            q['bufs'].setdefault(key, []).append(fn(img))
            q['left'] -= 1

        jobs.setdefault(fr, []).append((q, run))

    if kind in ('landing', 'possession'):
        frames = list(range(pay['a'], pay['b'], STEP))
        q['frames'] = frames

        def cam(img, fr):
            im = img
            if kind == 'landing' and fr <= pay['kick'] + 6:  # the tracker's ring on take-off only
                k = fr - S.lo
                if S.bkind[k]:
                    im = img.copy()
                    p = S.bimg[k]
                    cv2.circle(im, (int(p[0]), int(p[1])), 16, BLACK, 5, cv2.LINE_AA)
                    cv2.circle(
                        im,
                        (int(p[0]), int(p[1])),
                        16,
                        ORANGE if S.bkind[k] == 'air' else GREEN,
                        3,
                        cv2.LINE_AA,
                    )
            return cv2.resize(im, CAM, interpolation=cv2.INTER_AREA)

        for fr in frames:
            add(fr, 'clip', lambda img, fr=fr: cam(img, fr))
    elif kind == 'feet':
        b = pay.box_at(f)
        h = b[3] - b[1]
        q['rect'] = _crop_rect((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, max(300, 5 * h))
        q['box'] = [round(float(v), 1) for v in b]

        def still(img):
            im = img.copy()
            cv2.rectangle(
                im,
                (int(b[0]) - 4, int(b[1]) - 4),
                (int(b[2]) + 4, int(b[3]) + 4),
                YELLOW,
                3,
                cv2.LINE_AA,
            )
            return _crop(im, q['rect'], (960, 540))

        add(f, 'still', still)
    elif kind in ('missing', 'extra'):
        cx, cy = pay['c']
        q['rect'] = _crop_rect(cx + S.rng.uniform(-200, 200), cy + S.rng.uniform(-100, 100), 540)

        def still(img):
            im = img.copy()
            for _, bb in pay['ringed']:
                _feet_ring(im, bb, WHITE, 3)
            return _crop(im, q['rect'], STILL)

        add(f, 'still', still)
    elif kind == 'keeper':
        q['rect'] = [0.0, 0.0, 1920.0, 1080.0]
        add(f, 'still', lambda img: cv2.resize(img, STILL, interpolation=cv2.INTER_AREA))
    elif kind == 'same':
        for side, ident, (a, b) in (('A', pay['a'], pay['ra']), ('B', pay['b'], pay['rb'])):
            fs = ident.frames[(ident.frames >= a) & (ident.frames <= b) & ~ident.interp]
            if side == 'A':
                fs = fs[fs >= b - 300]
            else:
                fs = fs[fs <= a + 300]
            pick = fs[np.linspace(0, len(fs) - 1, 6).astype(int)] if len(fs) else []
            q.setdefault('crops', {})[side] = [int(x) for x in pick]
            for fr in pick:
                bb = ident.box_at(int(fr))

                def crop(img, bb=bb):
                    w, h = bb[2] - bb[0], bb[3] - bb[1]
                    hh = max(h * 1.2, 2 * w * 1.2)
                    r = (
                        float(np.clip((bb[0] + bb[2]) / 2 - hh / 4, 0, 1920 - hh / 2)),
                        float(np.clip((bb[1] + bb[3]) / 2 - hh / 2, 0, 1080 - hh)),
                        hh / 2,
                        hh,
                    )
                    th = img.copy()
                    cv2.rectangle(
                        th,
                        (int(bb[0]) - 4, int(bb[1]) - 4),
                        (int(bb[2]) + 4, int(bb[3]) + 4),
                        YELLOW,
                        4,
                    )
                    return _crop(img, r, CROP), cv2.resize(th, THUMB, interpolation=cv2.INTER_AREA)

                add(int(fr), side, crop)
    elif kind in ('motion', 'trust'):
        frames = list(range(pay['a'], pay['b'], STEP))
        q['frames'] = frames
        rect = [0.0, 0.0, 1920.0, 1080.0]
        pid_of = {i.pid: i for i in S.res.identities}
        if kind == 'motion':
            n = pay['n']
            bb = []
            for fr in frames:
                p = S.td.owner[n, fr - S.lo]
                b = pid_of[p].box_at(fr) if p >= 0 and p in pid_of else None
                if b is not None:
                    bb.append(b)
            if bb:
                bb = np.array(bb)
                x0, y0, x1, y1 = bb[:, 0].min(), bb[:, 1].min(), bb[:, 2].max(), bb[:, 3].max()
                rect = list(
                    _crop_rect(
                        (x0 + x1) / 2,
                        (y0 + y1) / 2,
                        max(360, 1.4 * (y1 - y0), 1.4 * (x1 - x0) * 9 / 16),
                    )
                )
        q['rect'] = rect

        def cam(img, fr):
            im = img
            if kind == 'motion':
                p = S.td.owner[pay['n'], fr - S.lo]
                if p >= 0 and p in pid_of:
                    b = pid_of[p].box_at(fr)
                    if b is not None:
                        im = img.copy()
                        cv2.rectangle(
                            im,
                            (int(b[0]) - 4, int(b[1]) - 4),
                            (int(b[2]) + 4, int(b[3]) + 4),
                            YELLOW,
                            3,
                            cv2.LINE_AA,
                        )
            return _fit(
                _crop(im, rect, (int(rect[2]), int(rect[3]))) if kind == 'motion' else im, PANEL
            )

        for fr in frames:
            add(fr, 'clip', lambda img, fr=fr: cam(img, fr))


def _finish(S, q, w, tag, out: Path, packs, base, n, n_of):
    """Write the question's media and its record."""

    kind, f, pay = q['kind'], q['f'], q['pay']
    n_of[kind] = n_of.get(kind, 0) + 1
    qid = '%s_%s_%d_%d' % (tag, kind, f, n)
    rec = dict(
        id=qid,
        tag=tag,
        type=kind,
        frame=f,
        window=w,
        sample=q['sample'],
        kits={t: S.kits[t] for t in ('team_a', 'team_b')},
        dark=S.dark,
        light=S.light,
        pitch=[S.length, S.width],
        sb_per_m=[round(S.kx, 5), round(S.ky, 5)],
    )
    if kind in ('landing', 'possession'):
        rec['clip'] = packs.clip(q['bufs']['clip'])
        rec['rect'] = [0.0, 0.0, 1920.0, 1080.0]
        rec['frames'] = [q['frames'][0], STEP]
        if kind == 'landing':
            rec['model'] = dict(
                kind='air' if q['sample'] == 'random' else 'ground',
                end=pay.get('end'),
                land=pay.get('land'),
            )
            rec['moment'] = None
        else:
            rec['moment'] = (f - pay['a']) / STEP / FPS
            rec['model'] = dict(team=pay['team'], word=S.team_word(pay['team']))
    elif kind in ('feet', 'missing', 'extra', 'keeper'):
        name = 'img/%s.jpg' % qid
        _jpg(out / name, q['bufs']['still'][0])
        rec['img'] = name
        rec['rect'] = _r(q['rect'])
        if kind == 'feet':
            ident = pay
            rec['model'] = dict(
                pid=ident.pid,
                role=ident.role,
                team=S.res.team_at(ident, f),
                word=S.team_word(S.res.team_at(ident, f)),
                box=q['box'],
                dot_m=S.dot_of(ident, f),
            )
        elif kind in ('missing', 'extra'):
            rec['model'] = dict(
                ringed=[
                    dict(pid=i.pid, role=i.role, team=i.team, box=_r(b)) for i, b in pay['ringed']
                ]
            )
        else:
            keep = [(d.ident, d.ident.box_at(f)) for d in S.seen_dots(f) if d.group[1] == 'keeper']
            rec['model'] = dict(
                keepers=[dict(pid=i.pid, team=i.team, box=_r(b)) for i, b in keep if b is not None]
            )
    elif kind == 'same':
        sheet = np.zeros((CROP[1] * 2, CROP[0] * 6, 3), np.uint8)
        atlas = np.zeros((THUMB[1] * 3, THUMB[0] * 4, 3), np.uint8)
        for r, side in enumerate('AB'):
            for c, (cr, th) in enumerate(q['bufs'].get(side, [])):
                sheet[r * CROP[1] : (r + 1) * CROP[1], c * CROP[0] : (c + 1) * CROP[0]] = cr
                k = r * 6 + c
                atlas[
                    (k // 4) * THUMB[1] : (k // 4 + 1) * THUMB[1],
                    (k % 4) * THUMB[0] : (k % 4 + 1) * THUMB[0],
                ] = th
        rec['sheet'], rec['atlas'] = 'img/%s_s.jpg' % qid, 'img/%s_a.jpg' % qid
        _jpg(out / rec['sheet'], sheet, 88)
        _jpg(out / rec['atlas'], atlas, 72)
        rec['ncrops'] = [len(q['crops']['A']), len(q['crops']['B'])]
        a, b = pay['a'], pay['b']
        rec['model'] = dict(
            same=True,
            pa=a.pid,
            pb=b.pid,
            ra=list(pay['ra']),
            rb=list(pay['rb']),
            gap=pay['gap'],
            crops=q['crops'],
            boxes={s: [_r(i.box_at(fr)) for fr in q['crops'][s]] for s, i in (('A', a), ('B', b))},
        )
    elif kind in ('motion', 'trust'):
        cam = q['bufs']['clip']
        if kind == 'motion':
            maps = [S.map_frame(fr, base, only=pay['n']) for fr in q['frames']]
        else:
            maps = [S.map_frame(fr, base, ball=True) for fr in q['frames']]
        rec['clip'] = packs.clip(cam + maps)
        rec['split'] = len(cam) / FPS
        rec['model'] = dict(n=pay.get('n'), acc=pay.get('acc'))
    print('  %-10s %-6s %d' % (kind, q['sample'], f), flush=True)
    return rec


# ---- strips ----------------------------------------------------------------------------


def make_strip(game, out: Path):
    """A thumbnail every STRIP_S seconds over the game's video, with the half edges."""
    import cv2

    H = json.loads((Path(__file__).resolve().parents[1] / 'balltrack' / 'halves.json').read_text())[
        game
    ]
    video = str(Path(os.environ.get('PIQ_DOWNLOADS', 'C:/Users/alexv/Downloads')) / H['video'])
    cap = cv2.VideoCapture(video)
    n, fps = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS)
    step = int(STRIP_S * fps)
    fs = list(range(0, n - 1, step))
    per = STRIP_COLS * STRIP_COLS
    out.mkdir(parents=True, exist_ok=True)
    bper = BIG_COLS * BIG_COLS
    atlases, bigs = [], []
    sheet = big = None
    for k, f in enumerate(fs):
        if k % per == 0:
            sheet = np.zeros(
                (STRIP_THUMB[1] * STRIP_COLS, STRIP_THUMB[0] * STRIP_COLS, 3), np.uint8
            )
        if k % bper == 0:
            big = np.zeros((STRIP_BIG[1] * BIG_COLS, STRIP_BIG[0] * BIG_COLS, 3), np.uint8)
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, img = cap.read()
        if ok:
            for sh, size, cols, j in (
                (sheet, STRIP_THUMB, STRIP_COLS, k % per),
                (big, STRIP_BIG, BIG_COLS, k % bper),
            ):
                r, c = divmod(j, cols)
                sh[r * size[1] : (r + 1) * size[1], c * size[0] : (c + 1) * size[0]] = cv2.resize(
                    img, size, interpolation=cv2.INTER_AREA
                )
        if k % per == per - 1 or k == len(fs) - 1:
            name = 'strip/%s_%02d.jpg' % (game, len(atlases))
            _jpg(out / name, sheet, 70)
            atlases.append(name)
            print(name, flush=True)
        if k % bper == bper - 1 or k == len(fs) - 1:
            name = 'strip/%s_big_%02d.jpg' % (game, len(bigs))
            _jpg(out / name, big, 78)
            bigs.append(name)
    edges = {k: v[2:4] for k, v in H.items() if isinstance(v, list) and len(v) == 4}
    q = dict(
        id='%s_stops' % game,
        tag=game,
        type='stops',
        frame=0,
        sample='random',
        step=step,
        fps=fps,
        n=len(fs),
        cols=STRIP_COLS,
        per=per,
        atlases=atlases,
        bigs=bigs,
        bcols=BIG_COLS,
        bper=bper,
        frames=n,
        model=dict(halves=edges),
    )
    (out / 'questions.json').write_text(json.dumps(dict(tag=game, questions=[q])))
    return [q]


def repack(part: Path, max_mb=15.0):
    """Split a built half's packs over max_mb at clip boundaries (re-encoded) and point its
    questions at the new packs."""
    import cv2

    qf = part / 'questions.json'
    j = json.loads(qf.read_text())
    clips = [q['clip'] for q in j['questions'] if q.get('clip')]
    n = 1 + max(int(Path(c['pack']).stem[1:]) for c in clips)
    for name in sorted({c['pack'] for c in clips}):
        src = part / name
        mb = src.stat().st_size / 2**20
        if mb <= max_mb:
            continue
        mine = sorted((c for c in clips if c['pack'] == name), key=lambda c: c['t0'])
        total = mine[-1]['t1']
        groups, cur = [], []
        for c in mine:
            if cur and (c['t1'] - cur[0]['t0']) / total * mb > PACK_MB:
                groups.append(cur)
                cur = []
            cur.append(c)
        groups.append(cur)
        cap = cv2.VideoCapture(str(src))
        f = 0
        for g in groups:
            new = 'packs/p%03d.webm' % n
            n += 1
            vw = cv2.VideoWriter(
                str(part / new), cv2.VideoWriter_fourcc(*'VP80'), FPS, (g[0]['w'], g[0]['h'])
            )
            assert vw.isOpened(), 'no VP80 writer'
            base = round(g[0]['t0'] * FPS)
            while f < round(g[-1]['t1'] * FPS):
                ok, im = cap.read()
                assert ok, '%s ends at frame %d' % (name, f)
                if f >= base:
                    vw.write(im)
                f += 1
            vw.release()
            for c in g:
                c['pack'] = new
                c['t0'], c['t1'] = (round(c[k] - base / FPS, 3) for k in ('t0', 't1'))
            print(
                '%s -> %s: %d clips, %.1f MB'
                % (name, new, len(g), (part / new).stat().st_size / 2**20)
            )
        cap.release()
        src.unlink()
    qf.write_text(json.dumps(j, default=_plain))


def merge(set_dir: Path, parts: list[Path]):
    """One set from built halves and strips: media copied under <part name>/, strips first."""
    import shutil

    set_dir.mkdir(parents=True, exist_ok=True)
    stops, rest = [], []
    for p in parts:
        j = json.loads((p / 'questions.json').read_text())
        pre = p.name + '/'
        for q in j['questions']:
            for k in ('img', 'sheet', 'atlas'):
                if q.get(k):
                    q[k] = pre + q[k]
            if q.get('clip'):
                q['clip']['pack'] = pre + q['clip']['pack']
            for k in ('atlases', 'bigs'):
                if q.get(k):
                    q[k] = [pre + a for a in q[k]]
            (stops if q['type'] == 'stops' else rest).append(q)
        dst = set_dir / p.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(p, dst, ignore=shutil.ignore_patterns('questions.json', 'link'))
    rng = np.random.default_rng(1)
    rest = [rest[i] for i in rng.permutation(len(rest))]
    (set_dir / 'questions.json').write_text(json.dumps(dict(questions=stops + rest)))
    n = {}
    for q in stops + rest:
        n[q['type']] = n.get(q['type'], 0) + 1
    size = sum(f.stat().st_size for f in set_dir.rglob('*') if f.is_file()) / 2**20
    print('set %s: %d questions %s, %.1f MB' % (set_dir.name, len(stops) + len(rest), n, size))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog='python -m pitchiq.experiments.maplab', description=__doc__.split('\n\n')[0]
    )
    sub = p.add_subparsers(dest='cmd', required=True)
    h = sub.add_parser('half')
    h.add_argument('tag')
    h.add_argument('out', type=Path)
    h.add_argument('--lo', type=int, required=True, help='first frame of play')
    h.add_argument('--hi', type=int, required=True, help='frame after play ends')
    h.add_argument('--per', type=int, default=6, help='questions per type')
    h.add_argument('--windows', type=int, default=3)
    h.add_argument('--win-s', type=float, default=180.0)
    h.add_argument('--seed', type=int, default=0)
    h.add_argument('--types', nargs='+', choices=TYPES, default=list(TYPES))
    s = sub.add_parser('strip')
    s.add_argument('game')
    s.add_argument('out', type=Path)
    m = sub.add_parser('merge')
    m.add_argument('set_dir', type=Path)
    m.add_argument('parts', type=Path, nargs='+')
    r = sub.add_parser('repack')
    r.add_argument('parts', type=Path, nargs='+')
    a = p.parse_args(argv)
    for k in ('out', 'set_dir'):  # match_video changes directory on import
        if getattr(a, k, None) is not None:
            setattr(a, k, getattr(a, k).resolve())
    if a.cmd in ('merge', 'repack'):
        a.parts = [x.resolve() for x in a.parts]
    if a.cmd == 'half':
        qs = make_half(a.tag, a.out, a.lo, a.hi, a.per, a.windows, a.win_s, a.seed, tuple(a.types))
        n = {}
        for q in qs:
            n[q['type'] + '/' + q['sample']] = n.get(q['type'] + '/' + q['sample'], 0) + 1
        print('questions:', len(qs), n)
    elif a.cmd == 'strip':
        make_strip(a.game, a.out)
    elif a.cmd == 'repack':
        for x in a.parts:
            repack(x)
    else:
        merge(a.set_dir, a.parts)
    return 0


if __name__ == '__main__':
    os.environ.setdefault('OMP_NUM_THREADS', '4')
    sys.exit(main())
