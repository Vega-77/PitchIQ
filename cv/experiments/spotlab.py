"""Spot-check lab media: yes/no questions about what the pipeline drew.

    PIQ_VIDEO=<video> python -m cv.experiments.spotlab <tag> <out> [--per-type 100] [--windows 6]

<tag> is a pass that `cv/balltrack/half.py` (or hybrid.sh plus player_lab
extract) left behind: players, Hybrid 90 ball and the smoothed camera. The
linker runs on --windows stretches of --win-s seconds spread over it, and each
question shows one thing it drew there, as the real frames:

  role      a box on a person: player, referee, or someone else?
  team      a box on a player: which kit (both kits shown as swatches)?
  same      a ring that follows one identity for 2 s: the same person throughout?
  lines     the pitch lines drawn on the frame: on the paint?
  missing   everyone the map counts ringed in a crop: anyone without a ring?
  ballring  the ball ring: on the ball?
  ballmiss  half a frame around the nearest ringed ball, centred on a frame with
            no ring: is the ball ever in play and visible with no ring?

Every question has "can't tell". A share (--hard) of each type comes from the
cases the pipeline is least sure of (near a line, at a join, an unringed
detection, a ball in the air); scores must use only sample == "random".

--keep-gaps N links with `kit_cut_keep_rows=N` and adds a `same` question
(sample "gap") at each end of every stretch that setting gives back: rows the
shipped linker drops as a kit swap. Those answers decide whether to turn it on.

Writes <out>/questions.json, <out>/packs/p<k>.webm (10 two-second clips each,
8 fps; the page seeks to slot * 2 s) and <out>/ctx/a<k>.jpg (5 x 5 atlases of
the whole frame with the crop marked).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

TYPES = ('role', 'team', 'same', 'lines', 'missing', 'ballring', 'ballmiss')
OFFS = np.arange(-32, 32, 4)                      # 16 frames around the question frame
CLIP_FPS, CLIP_S = 8, 2.0
PER_PACK, ATLAS = 10, 5
SIZES = {'S': (480, 270), 'M': (768, 432)}
SIZE_OF = {'lines': 'M', 'ballmiss': 'M'}
YELLOW, GREEN, ORANGE, MAGENTA, WHITE, BLACK = (0, 230, 255), (80, 230, 80), (40, 140, 255), (255, 0, 255), (255, 255, 255), (0, 0, 0)


def _mv():
    """cv/balltrack/match_video (not a package: imported by path, as the tracker's own scripts do)."""
    here = str(Path(__file__).resolve().parents[1] / 'balltrack')
    if here not in sys.path:
        sys.path.insert(0, here)
    import match_video
    return match_video


def _subset(det, m):
    from cv.pitchtrack import Detections
    return Detections(frame=det.frame[m], box=det.box[m], conf=det.conf[m], team=det.team[m], desc=det.desc[m],
                      kit=None if det.kit is None else det.kit[m], feat=None if det.feat is None else det.feat[m])


def _sb(H, p):
    q = H @ [p[0], p[1], 1.0]
    return q[:2] / q[2]


def _near_line(xy, d=5.0):
    x, y = xy
    return min(abs(x), abs(120 - x), abs(y), abs(80 - y)) < d


def _crop_rect(cx, cy, ch, W=1920, H=1080):
    ch = float(np.clip(ch, 120, H)); cw = ch * 16 / 9
    x0 = float(np.clip(cx - cw / 2, 0, W - cw)); y0 = float(np.clip(cy - ch / 2, 0, H - ch))
    return x0, y0, cw, ch


LINK_PROCS = int(os.environ.get('SPOTLAB_PROCS', 3))   # windows linked side by side
_LW = {}


def _link_init(tag, meta):
    from cv.experiments import player_lab as L
    _LW['meta'] = meta
    _LW['proj'], _LW['pitch'] = L.projector(tag, meta['w'], meta['h'])


def _link_one(job):
    from cv.experiments import player_lab as L
    lo, hi, dw = job
    return L.run_variant('A+B+C', dw, dict(_LW['meta'], lo=lo, n=hi - lo), _LW['proj'], _LW['pitch'])[1]


def linked(tag, det, meta, spans, procs=LINK_PROCS):
    """run_variant('A+B+C') on each (lo, hi) span's detections, in span order,
    `procs` spans at a time in their own processes: the linker draws nothing at
    random and each span is linked alone, so the results are the serial ones."""
    import multiprocessing as mp
    jobs = [(lo, hi, _subset(det, (det.frame >= lo) & (det.frame < hi))) for lo, hi in spans]
    if procs <= 1:
        _link_init(tag, meta)
        yield from map(_link_one, jobs)
        return
    with mp.get_context('spawn').Pool(min(procs, len(jobs)), initializer=_link_init, initargs=(tag, meta)) as pool:
        yield from pool.imap(_link_one, jobs)


class _Window:
    """One stretch: linked players, ball and camera, and the question pools."""

    def __init__(self, tag, lo, hi, det, meta, proj, pitch, Hs, a0, rng, keep_gaps=0, res=None):
        MV = _mv()
        from cv.experiments import player_lab as L
        m = (det.frame >= lo) & (det.frame < hi)
        dw = _subset(det, m)
        mw = dict(meta, lo=lo, n=hi - lo)
        self.lo, self.hi, self.Hs, self.a0, self.det = lo, hi, Hs, a0, dw
        self.res = res if res is not None else L.run_variant('A+B+C', dw, mw, proj, pitch)[1]
        self.gap_ends = []
        if keep_gaps:
            held0 = np.zeros(len(dw), bool)
            for i in self.res.identities:
                held0[i.rows[i.rows >= 0].astype(int)] = True
            L.VARIANTS['spot keep gaps'] = ({}, {'kit_cut_keep_rows': keep_gaps})
            _, self.res = L.run_variant('spot keep gaps', dw, mw, proj, pitch)
            for ident in self.res.identities:
                if ident.role == 'off':
                    continue
                back = np.array([r >= 0 and not held0[r] for r in ident.rows])
                d = np.diff(np.r_[0, back.astype(np.int8), 0])
                for b, e in zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)):
                    for f in (int(ident.frames[b]), int(ident.frames[e - 1])):
                        if self.fits(f):
                            self.gap_ends.append((f, ident, True))
        _, self.bimg, _, self.bgnd, self.bkind = MV.ball(tag, lo, hi)
        shirt = {}
        for t in ('team_a', 'team_b'):
            rows = np.concatenate([i.rows[i.rows >= 0] for i in self.res.identities
                                   if i.team == t and i.role == 'player'] or [[]]).astype(int)
            shirt[t] = np.nanmedian(dw.desc[rows, :3], axis=0) if len(rows) else np.full(3, np.nan)
        self.kits = {t: dict(name=MV.kit_name(s) if np.isfinite(s).all() else t, lab=[float(v) for v in s], rgb=_rgb(s))
                     for t, s in shirt.items()}
        ka, kb = self.kits['team_a'], self.kits['team_b']
        ab = ka['lab'][0] >= kb['lab'][0]
        if np.isfinite(ka['lab'][0]) and np.isfinite(kb['lab'][0]) and (
                ka['name'] == kb['name'] or (ka['name'] == 'white') != ab and 'white' in (ka['name'], kb['name'])):
            # both kits read as one colour (glare: white looks grey), or 'white' is the darker swatch:
            # tell them apart by lightness
            ka['name'], kb['name'] = ('lighter', 'darker') if ab else ('darker', 'lighter')
        self.rng = rng

    def _joins(self, ident):
        """Frames where the identity picks up again: after a gap, or after a filled-in stretch."""
        f, gi = ident.frames, ident.interp
        k = np.flatnonzero((np.diff(f) > 1) | (gi[:-1] & ~gi[1:])) + 1
        return [int(x) for x in f[k]]

    def _mixed_kit(self, ident):
        """More than a fifth of the identity's detections lean to the other kit."""
        t = self.det.team[ident.rows[ident.rows >= 0]]
        t = t[t >= 0]
        return len(t) > 0 and min((t == 0).mean(), (t == 1).mean()) > 0.2

    def H(self, f):
        return self.Hs[f - self.a0]

    def fits(self, f):
        return f + OFFS[0] >= self.lo and f + OFFS[-1] < self.hi

    # ---- pools: (frame, payload, hard) -------------------------------------------
    def pool(self, kind):
        out = []
        R = self.res
        if kind in ('role', 'team'):
            for ident in R.identities:
                if kind == 'team' and ident.role != 'player':
                    continue
                for k in np.flatnonzero(~ident.interp)[::5]:
                    f = int(ident.frames[k])
                    if not self.fits(f):
                        continue
                    b = ident.boxes[k]
                    if kind == 'role':
                        hard = _near_line(_sb(self.H(f), ((b[0] + b[2]) / 2, b[3]))) or ident.role == 'off'
                    else:
                        hard = R.team_at(ident, f) != ident.team or self._mixed_kit(ident)
                    out.append((f, ident, bool(hard)))
        elif kind == 'same':
            for ident in R.identities:
                if ident.role == 'off':
                    continue
                joins = self._joins(ident)
                for f in ident.frames[::15]:
                    f = int(f)
                    if not self.fits(f):
                        continue
                    span = np.searchsorted(ident.frames, [f + OFFS[0], f + OFFS[-1] + 1])
                    if span[1] - span[0] < 0.8 * (OFFS[-1] - OFFS[0]):
                        continue
                    out.append((f, ident, any(abs(j - f) <= 8 for j in joins)))
        elif kind == 'lines':
            out = [(f, None, False) for f in range(self.lo, self.hi, 15) if self.fits(f)]
        elif kind == 'missing':
            for f in range(self.lo, self.hi, 15):
                if not self.fits(f):
                    continue
                Hi = np.linalg.inv(self.H(f))
                for _ in range(2):                      # random pitch points in view
                    q = Hi @ [self.rng.uniform(0, 120), self.rng.uniform(0, 80), 1.0]
                    if q[2] != 0 and 0 <= q[0] / q[2] < 1920 and 0 <= q[1] / q[2] < 1080:
                        out.append((f, (q[0] / q[2], q[1] / q[2]), False))
                rows = np.flatnonzero((self.det.frame == f) & (self.det.conf >= 0.4))
                ringed = [b for i, b in R.at(f) if i.role != 'off']
                for r in rows:
                    b = self.det.box[r]
                    if not any(_iou(b, c) > 0.3 for c in ringed):
                        out.append((f, ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), True))
        elif kind == 'ballring':
            for k in range(0, self.hi - self.lo, 3):
                f = self.lo + k
                if self.bkind[k] and self.fits(f):
                    out.append((f, None, self.bkind[k] == 'air'))
        elif kind == 'ballmiss':
            for k in range(0, self.hi - self.lo, 15):
                f = self.lo + k
                if not self.bkind[k] and self.fits(f):
                    out.append((f, None, False))
        return out

    # ---- drawing ------------------------------------------------------------------
    def crop_for(self, kind, f, payload):
        if kind in ('role', 'team'):
            b = payload.box_at(f)
            return _crop_rect((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, 4.5 * (b[3] - b[1]))
        if kind == 'same':
            i0, i1 = np.searchsorted(payload.frames, [f + OFFS[0], f + OFFS[-1] + 1])
            bb = payload.boxes[i0:i1]
            x0, y0, x1, y1 = bb[:, 0].min(), bb[:, 1].min(), bb[:, 2].max(), bb[:, 3].max()
            h = max(3.5 * np.median(bb[:, 3] - bb[:, 1]), 1.3 * (y1 - y0), 1.3 * (x1 - x0) * 9 / 16)
            return _crop_rect((x0 + x1) / 2, (y0 + y1) / 2, h)
        if kind == 'missing':
            return _crop_rect(payload[0], payload[1], 360)
        if kind == 'ballring':
            k = f - self.lo
            p = self.bimg[k]
            return _crop_rect(p[0], p[1], 270)
        if kind == 'ballmiss':
            p = self.near_ball(f)
            return _crop_rect(p[0], p[1], 540)
        return 0.0, 0.0, 1920.0, 1080.0

    def near_ball(self, f):
        """Where the ball was last (or next) ringed in this stretch; else the middle of the players."""
        seen = np.flatnonzero(np.array([bool(k) for k in self.bkind]))
        if len(seen):
            k = seen[np.argmin(np.abs(seen - (f - self.lo)))]
            return self.bimg[k][:2]
        b = np.array([b for _, b in self.res.at(f)] or [[960, 540, 960, 540]], float)
        return float(np.median((b[:, 0] + b[:, 2]) / 2)), float(np.median(b[:, 3]))

    def draw(self, kind, f0, payload, img, f, th):
        import cv2
        if kind in ('role', 'team'):
            b = payload.box_at(f)
            if b is not None:
                cv2.rectangle(img, (int(b[0]) - 3, int(b[1]) - 3), (int(b[2]) + 3, int(b[3]) + 3), YELLOW, th, cv2.LINE_AA)
        elif kind == 'same':
            b = payload.box_at(f)
            if b is not None:
                _feet_ring(img, b, YELLOW, th)
        elif kind == 'lines':
            _draw_lines(img, self.H(f), th)
        elif kind == 'missing':
            for ident, b in self.res.at(f):
                if ident.role != 'off':
                    _feet_ring(img, b, WHITE, th)
        elif kind in ('ballring', 'ballmiss'):      # the tracker's ring wherever it had one
            k = f - self.lo
            if self.bkind[k]:
                p = self.bimg[k]
                c = ORANGE if self.bkind[k] == 'air' else GREEN
                cv2.circle(img, (int(p[0]), int(p[1])), 14, BLACK, th + 2, cv2.LINE_AA)
                cv2.circle(img, (int(p[0]), int(p[1])), 14, c, th, cv2.LINE_AA)

    def model_answer(self, kind, f, payload):
        if kind == 'role':
            return {'player': 'player', 'keeper': 'player', 'referee': 'referee'}.get(payload.role, 'other')
        if kind == 'team':
            t = self.res.team_at(payload, f)
            return {'team_a': 'A', 'team_b': 'B'}.get(t, 'unsure')
        return {'same': 'yes', 'lines': 'yes', 'missing': 'no', 'ballring': 'yes', 'ballmiss': 'no'}[kind]


def _rgb(lab):
    """OpenCV 8-bit Lab to '#rrggbb' for the page's kit swatch."""
    import cv2
    if not np.isfinite(lab).all():
        return '#808080'
    b, g, r = cv2.cvtColor(np.uint8([[np.clip(np.round(lab), 0, 255)]]), cv2.COLOR_LAB2BGR)[0, 0]
    return '#%02x%02x%02x' % (r, g, b)


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
    return ix * iy / u if u > 0 else 0.0


def _feet_ring(img, b, col, th):
    import cv2
    u, v = (b[0] + b[2]) / 2, b[3]
    w = max(10, (b[2] - b[0]) * 0.6)
    cv2.ellipse(img, (int(u), int(v)), (int(w), int(w * 0.35)), 0, 0, 360, BLACK, th + 2, cv2.LINE_AA)
    cv2.ellipse(img, (int(u), int(v)), (int(w), int(w * 0.35)), 0, 0, 360, col, th, cv2.LINE_AA)


_LINES = None


def _draw_lines(img, H, th):
    import cv2

    from cv.experiments.cal_overlay import _densify, _lines
    global _LINES
    if _LINES is None:
        _LINES = [_densify(l) for l in _lines(*_PITCH)]
    Hi = np.linalg.inv(H)
    c = H @ [960.0, 540.0, 1.0]
    if (Hi @ (c / c[2]))[2] < 0:
        Hi = -Hi
    for d in _LINES:
        q = np.c_[d, np.ones(len(d))] @ Hi.T
        ok = q[:, 2] > 1e-9
        p = q[:, :2] / np.where(ok, q[:, 2], 1)[:, None]
        run = []
        for good, pt in zip(ok, p):
            if good and -2000 < pt[0] < 4000 and -2000 < pt[1] < 3000:
                run.append(pt)
            elif len(run) > 1:
                cv2.polylines(img, [np.int32(run)], False, MAGENTA, th, cv2.LINE_AA); run = []
            else:
                run = []
        if len(run) > 1:
            cv2.polylines(img, [np.int32(run)], False, MAGENTA, th, cv2.LINE_AA)


_PITCH = (105.0, 68.0)


class _Writer:
    """Clips into 10-slot webm packs per frame size, context thumbnails into atlases."""

    def __init__(self, out: Path):
        self.out = out
        (out / 'packs').mkdir(parents=True, exist_ok=True); (out / 'ctx').mkdir(exist_ok=True)
        self.packs, self.n_pack = {}, 0
        self.atlas, self.n_atlas, self.cells = None, 0, 0

    def clip(self, size, frames):
        import cv2
        cur = self.packs.get(size)
        if cur is None or cur[2] == PER_PACK:
            if cur is not None:
                cur[1].release()
            name = 'packs/p%03d.webm' % self.n_pack; self.n_pack += 1
            vw = cv2.VideoWriter(str(self.out / name), cv2.VideoWriter_fourcc(*'VP80'), CLIP_FPS, SIZES[size])
            assert vw.isOpened(), 'no VP80 writer'
            cur = self.packs[size] = [name, vw, 0]
        for fr in frames:
            cur[1].write(fr)
        slot = cur[2]; cur[2] += 1
        return cur[0], slot

    def context(self, thumb):
        if self.atlas is None:
            self.atlas = np.zeros((270 * ATLAS, 480 * ATLAS, 3), np.uint8); self.cells = 0
        r, c = divmod(self.cells, ATLAS)
        self.atlas[r * 270:(r + 1) * 270, c * 480:(c + 1) * 480] = thumb
        name = 'ctx/a%03d.jpg' % self.n_atlas; cell = self.cells; self.cells += 1
        if self.cells == ATLAS * ATLAS:
            self.flush_atlas()
        return name, cell

    def flush_atlas(self):
        import cv2
        if self.atlas is not None and self.cells:
            cv2.imwrite(str(self.out / ('ctx/a%03d.jpg' % self.n_atlas)), self.atlas, [cv2.IMWRITE_JPEG_QUALITY, 72])
            self.n_atlas += 1
        self.atlas = None

    def close(self):
        for cur in self.packs.values():
            cur[1].release()
        self.flush_atlas()


def make(tag: str, out: Path, per_type: int = 100, windows: int = 6, win_s: float = 180.0,
         hard: float = 0.3, seed: int = 0, types=TYPES, keep_gaps: int = 0,
         play: tuple[int | None, int | None] = (None, None)) -> list[dict]:
    import cv2

    MV = _mv()
    from cv.experiments import player_lab as L
    global _PITCH, _LINES
    rng = np.random.default_rng(seed)
    HG = Path(MV.HG)
    sm = np.load(HG / f'{tag}_smooth2.npz')
    Hs, frames = sm['H'], sm['abs']
    a0 = int(frames[0])
    det, meta = L.load_detections(tag)
    proj, pitch = L.projector(tag, meta['w'], meta['h'])
    _PITCH, _LINES = (pitch.length_m, pitch.width_m), None
    lo_all, hi_all = max(a0, int(det.frame.min())), min(int(frames[-1]) + 1, int(det.frame.max()) + 1)
    # a pass range can run past play (walk-on before kickoff, walk-off after the whistle)
    lo_all, hi_all = max(lo_all, play[0] or lo_all), min(hi_all, play[1] or hi_all)
    win = int(win_s * meta['fps'])
    edges = np.linspace(lo_all, hi_all - win, windows).astype(int)
    out.mkdir(parents=True, exist_ok=True)
    W = _Writer(out)
    qs = []
    cap = cv2.VideoCapture(L.VIDEO)
    spans = [(int(lo), int(lo) + win) for lo in edges]
    for w, ((lo, hi), res) in enumerate(zip(spans, linked(tag, det, meta, spans))):
        print('window %d: %d..%d' % (w, lo, hi), flush=True)
        win_ = _Window(tag, lo, hi, det, meta, proj, pitch, Hs, a0, rng, keep_gaps, res=res)
        want = []
        for kind in types:
            n = int(round(per_type / windows))
            pool = win_.pool(kind)
            if not pool:
                continue
            hard_ix = [i for i, p in enumerate(pool) if p[2]]
            n_hard = min(len(hard_ix), int(round(hard * n)))
            pick = list(rng.choice(hard_ix, n_hard, replace=False)) if n_hard else []
            rest = [i for i in range(len(pool)) if i not in set(pick)]
            pick_r = list(rng.choice(rest, min(len(rest), n - n_hard), replace=False))
            for i, sample in [(i, 'hard') for i in pick] + [(i, 'random') for i in pick_r]:
                f, payload, _ = pool[i]
                want.append(dict(kind=kind, f=int(f), payload=payload, sample=sample))
        for f, ident, _ in win_.gap_ends:
            want.append(dict(kind='same', f=f, payload=ident, sample='gap'))
        want.sort(key=lambda q: q['f'])
        need = {}
        for q in want:
            q['rect'] = win_.crop_for(q['kind'], q['f'], q['payload'])
            q['frames'] = []
            for o in OFFS:
                need.setdefault(q['f'] + int(o), []).append(q)
        f = min(need); cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        last = max(need)
        while f <= last:
            if f not in need:
                cap.grab(); f += 1; continue
            ok, img = cap.read()
            if not ok:
                break
            for q in need[f]:
                x0, y0, cw, ch = q['rect']
                th = max(2, int(round(2 * ch / 270)))
                im = img.copy()
                win_.draw(q['kind'], q['f'], q['payload'], im, f, th)
                size = SIZE_OF.get(q['kind'], 'S')
                crop = im[int(y0):int(y0 + ch), int(x0):int(x0 + cw)]
                q['frames'].append(cv2.resize(crop, SIZES[size], interpolation=cv2.INTER_AREA))
                if f == q['f']:
                    t = cv2.resize(im, (480, 270), interpolation=cv2.INTER_AREA)
                    s = 480 / 1920
                    cv2.rectangle(t, (int(x0 * s), int(y0 * s)), (int((x0 + cw) * s) - 1, int((y0 + ch) * s) - 1), YELLOW, 2)
                    q['thumb'] = t
                if len(q['frames']) == len(OFFS):
                    fr = q.pop('frames')
                    if q['kind'] == 'lines':            # a still: the camera hardly moves in 2 s, and stills cost nothing
                        fr = [fr[len(fr) // 2]] * len(fr)
                    pack, slot = W.clip(size, fr)
                    atlas, cell = W.context(q.pop('thumb'))
                    p = q.pop('payload')
                    qs.append(dict(id='%s_%d_%s_%d' % (tag, q['f'], q['kind'], len(qs)), type=q['kind'], frame=q['f'],
                                   window=w, sample=q['sample'], pack=pack, slot=slot, atlas=atlas, cell=cell,
                                   model=win_.model_answer(q['kind'], q['f'], p),
                                   pid=getattr(p, 'pid', None), kits=win_.kits if q['kind'] == 'team' else None,
                                   rect=[round(float(v), 1) for v in q['rect']]))   # x0, y0, w, h of the clip in the frame
            f += 1
    W.close()
    order = rng.permutation(len(qs))
    qs = [qs[i] for i in order]
    (out / 'questions.json').write_text(json.dumps(dict(tag=tag, clip_s=CLIP_S, fps=CLIP_FPS, questions=qs)))
    return qs


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='python -m cv.experiments.spotlab', description=__doc__.split('\n\n')[0])
    p.add_argument('tag')
    p.add_argument('out', type=Path)
    p.add_argument('--per-type', type=int, default=100, help='questions per type')
    p.add_argument('--windows', type=int, default=6, help='stretches the linker runs on')
    p.add_argument('--win-s', type=float, default=180.0, help='seconds per stretch')
    p.add_argument('--hard', type=float, default=0.3, help='share of each type taken from the unsure cases')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--types', nargs='+', choices=TYPES, default=list(TYPES))
    p.add_argument('--keep-gaps', type=int, default=0, help='link with kit_cut_keep_rows=N and ask about the stretches it keeps')
    p.add_argument('--lo', type=int, help='first frame of play (default: the pass range)')
    p.add_argument('--hi', type=int, help='frame after play ends (default: the pass range)')
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    qs = make(a.tag, a.out, a.per_type, a.windows, a.win_s, a.hard, a.seed, tuple(a.types), a.keep_gaps, (a.lo, a.hi))
    n = {t: sum(q['type'] == t for q in qs) for t in TYPES}
    print('questions:', len(qs), n)
    print('size: %.1f MB' % (sum(f.stat().st_size for f in a.out.rglob('*') if f.is_file()) / 2 ** 20))
    return 0


if __name__ == '__main__':
    os.environ.setdefault('OMP_NUM_THREADS', '4')
    sys.exit(main())
