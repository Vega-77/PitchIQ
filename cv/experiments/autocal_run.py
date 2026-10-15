"""Calibrate the panning camera over one stretch of a match, no clicks.

    PIQ_VIDEO=<video> python -m cv.experiments.autocal_run <tag> <lo> <hi>
    PIQ_VIDEO=<video> python -m cv.experiments.autocal_run nb01 7200 9000 --lines white
    PIQ_VIDEO=<video> python -m cv.experiments.autocal_run nbh2 120000 121800 --camera nb01

Five stages, each cached in `<work>/<tag>/` and skipped when its file exists
(`--redo STAGE` reruns that stage and everything after it):

    gather   SIFT links, paint points and paint masks             -> gather.pkl
             every 15 frames (AUTOCAL_WORKERS processes)
    prep     focal, chained rotations, the paint panorama        -> prep.pkl, pano.jpg
    cal      the whole pose from the panorama (`calibrate_pano`)  -> cal.pkl
             or, with --camera, the rotation alone from another
             stretch's camera and pitch (`calibrate_rotation`)
    height   camera height from the players' far/near size       -> full.pkl
             (skipped with --camera: the height came with it)
    write    the tracker's files: every sample -> StatsBomb       -> <homog>/<tag>_reg.npz, <tag>_venue.npz

The work folder defaults to `PitchIQ-data/autocal` beside the repo, never the
temp folder: Windows clears that, and these runs take long enough to lose.
The paint colour is gold (the home field) unless `--lines white` or
`PIQ_LINES=white`; it is saved with the stretch so a resumed run cannot mix.
"""

from __future__ import annotations

import argparse
import os
import pickle
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from cv import autocal as A

REPO = Path(__file__).resolve().parents[2]
WORK = REPO.parent / 'PitchIQ-data' / 'autocal'
HOMOG = REPO / 'cv' / 'balltrack' / 'homog'
DETS = REPO / 'cv' / 'balltrack' / 'plab' / 'out'
STAGES = ('gather', 'prep', 'cal', 'height', 'write')
STEP = 15                      # frames between samples
M_PER_R = 9.15                 # metres per centre-circle radius
INLIER_M = 1.5                 # paint this close to a model line counts as fitted
PERSON_CONF = 0.5              # the height cue's people: confident, whole boxes only
MASK_SHAPE = (540, 960)        # paint_mask's size for a 1920 x 1080 frame
# processes reading and matching frames (gather) and pricing focal lengths (prep)
WORKERS = int(os.environ.get('AUTOCAL_WORKERS', min(12, os.cpu_count() or 1)))


def _video():
    import cv2
    path = os.environ.get('PIQ_VIDEO')
    if not path:
        raise SystemExit('set PIQ_VIDEO: unset, every stage would silently read the scrimmage')
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f'cannot open {path}')
    return cap


def _frame(cap, ab):
    import cv2
    cap.set(cv2.CAP_PROP_POS_FRAMES, ab)
    ok, im = cap.read()
    if not ok:
        raise SystemExit(f'cannot read frame {ab}')
    return im


def _load(path: Path):
    with open(path, 'rb') as fh:
        return pickle.load(fh)


def _save(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + '.part')
    with open(tmp, 'wb') as fh:
        pickle.dump(obj, fh)
    os.replace(tmp, path)      # a crash mid-write leaves no half file to be mistaken for a finished stage


# ---- the stages ------------------------------------------------------------------


def gather(lo: int, hi: int, log, workers: int = WORKERS) -> dict:
    """Every sample's SIFT links to the samples 1, 2 and 4 ahead, its paint
    points and its whole paint mask (packed, so prep and write never read the
    video again). `workers` processes read and match a block of samples each;
    the random draws (which paint points, which link pairs) are made here
    afterwards in sample order, so the result does not depend on `workers`."""
    frames = list(range(lo, hi + 1, STEP))
    n = len(frames)
    size = max(8, -(-n // (4 * workers)))
    blocks = [(frames, s, min(s + size, n), A.LINES) for s in range(0, n, size)]
    packed, pairs = [], []
    for got_masks, got_pairs in _pool_map(_gather_block, blocks, workers):
        packed.extend(got_masks)
        pairs.extend(got_pairs)
    masks = _Masks(packed)
    rng = np.random.default_rng(0)
    paint, amount = {}, []
    for k in range(n):
        m = masks[k]
        paint[k] = A.paint_points(SimpleNamespace(mask=m, scale=0.5), 400, rng)
        amount.append(int(m.sum()))
    links = []
    for i, j, got in pairs:
        got = A.thin_link(got, rng=rng)
        if got is not None:
            links.append((i, j) + got)
    log('%d samples, %d links' % (n, len(links)))
    return dict(frames=frames, links=links, paint=paint, amount=amount, masks=np.stack(packed))


def _gather_block(args):
    """Samples s..e-1: their packed paint masks, and their links' inlier pairs
    as (i, j, pairs or None) in the order gather draws from them."""
    import cv2
    frames, s, e, lines = args
    A.LINES = lines
    cap = _video()
    sift = cv2.SIFT_create(nfeatures=3000)
    feats, masks = {}, []
    for k in range(s, min(e + 4, len(frames))):          # a link reaches 4 samples ahead
        im = _frame(cap, frames[k])
        feats[k] = A.frame_features(im, sift)
        if k < e:
            m = A.paint_mask(im) > 0
            if m.shape != MASK_SHAPE:
                raise SystemExit(f'paint mask is {m.shape}, not {MASK_SHAPE}: not a 1920 x 1080 video')
            masks.append(np.packbits(m))
    pairs = []
    for i in range(s, e):
        for d in (1, 2, 4):
            j = i + d
            if j < len(frames):
                pairs.append((i, j, A.link_inliers(feats[i], feats[j])))
    return masks, pairs


class _Masks(Mapping):
    """Sample index -> paint mask (h, w) of 0/1, from gather's packed masks or,
    for a gather made before it kept them, read again from the video."""

    def __init__(self, packed=None, frames=None):
        self.packed, self.frames, self.cap = packed, frames, None

    def __getitem__(self, k):
        if self.packed is None:
            self.cap = self.cap or _video()
            return A.paint_mask(_frame(self.cap, self.frames[k]))
        return np.unpackbits(self.packed[k], count=MASK_SHAPE[0] * MASK_SHAPE[1]).reshape(MASK_SHAPE)

    def __iter__(self):
        return iter(range(len(self)))

    def __len__(self):
        return len(self.packed if self.packed is not None else self.frames)




def _pool_map(fn, items, workers, initializer=None, initargs=()):
    """fn over items, results in order, in `workers` processes (this one when 1)."""
    if workers <= 1:
        if initializer:
            initializer(*initargs)
        yield from map(fn, items)
        return
    import multiprocessing as mp
    with mp.get_context('spawn').Pool(workers, initializer=initializer, initargs=initargs) as pool:
        yield from pool.imap(fn, items)


def prep(D: dict, log, workers: int = WORKERS) -> tuple[dict, np.ndarray]:
    n = len(D['frames'])
    if workers > 1:
        import multiprocessing as mp
        with mp.get_context('spawn').Pool(min(workers, 15), initializer=A.focal_pool_init,
                                          initargs=(D['links'], n)) as pool:
            f0 = A.best_focal(D['links'], n, pool=pool)
    else:
        f0 = A.best_focal(D['links'], n)
    R, f = A.chain_rotations(D['links'], n, f0)
    P = A.build_panorama(_Masks(D.get('masks'), D['frames']), R, f)
    log('%d samples, %d links, f0 %.0f, f %.0f..%.0f, pano %dx%d'
        % (n, len(D['links']), f0, f.min(), f.max(), P.paint.shape[1], P.paint.shape[0]))
    img = np.zeros(P.paint.shape + (3,), np.uint8)
    img[..., 1] = (P.cover > 0) * 60
    img[P.paint >= 0.25] = (0, 220, 255)
    return dict(f0=f0, R=R, f=f, pano=P, frames=D['frames']), img


def calibrate(Q: dict, camera: tuple | None, log) -> tuple:
    if camera is None:
        got = A.calibrate_pano(Q['pano'], log=log)
    else:
        C, _R0, L, W = camera
        got = A.calibrate_rotation(Q['pano'], np.asarray(C, float), A.PitchModel(L=L, W=W), log=log)
    if got is None:
        raise SystemExit('no halfway line and centre circle found: pick a stretch where the camera sees midfield')
    C, R0, pm, info = got
    log('C %s  L %.2f W %.2f  explained %d/%d' % (np.round(C, 3), pm.L, pm.W, info['explained'], info['paint']))
    return C, R0, pm.L, pm.W


def people(tag: str, frames: list[int], log) -> dict:
    """Sample index -> whole, confident people boxes. Read from the player lab's
    detections when they cover the samples, else detected on just those frames
    with the same detector and settings."""
    path = DETS / f'{tag}_dets.npz'
    if path.exists():
        z = np.load(path)
        if set(frames) <= set(np.unique(z['frame']).tolist()):
            fr, box, conf = z['frame'], z['box'], z['conf']
            log('people from %s' % path.name)
            return _whole({k: box[(fr == ab) & (conf >= PERSON_CONF)] for k, ab in enumerate(frames)})
    from ultralytics import YOLO

    from cv.detector import CLASS_PERSON, DEFAULT_WEIGHTS
    model = YOLO(str(DEFAULT_WEIGHTS))
    cap = _video()
    out = {}
    for i in range(0, len(frames), 8):
        ks = list(range(i, min(i + 8, len(frames))))
        res = model.predict([_frame(cap, frames[k]) for k in ks], classes=[CLASS_PERSON], conf=PERSON_CONF,
                            imgsz=1280, verbose=False)
        for k, r in zip(ks, res):
            out[k] = r.boxes.xyxy.cpu().numpy()
    log('people detected on %d samples' % len(frames))
    return _whole(out)


def _whole(boxes: dict) -> dict:
    return {k: b[(b[:, 1] > 4) & (b[:, 3] < 1075)] for k, b in boxes.items()}


def height(tag: str, Q: dict, cal: tuple, log) -> tuple:
    C, R0, L, W = cal
    boxes = people(tag, Q['frames'], log)
    C, R0, pm, _rows = A.height_from_people(Q['pano'], Q['R'], Q['f'], np.asarray(C, float), R0,
                                            A.PitchModel(L=L, W=W), boxes, log=log)
    log('C %s  W %.2f' % (np.round(C, 3), pm.W))
    return C, R0, pm.L, pm.W


def write(tag: str, Q: dict, rig_pose: tuple, homog: Path, log, paint: dict | None = None,
          masks: np.ndarray | None = None) -> dict:
    """The tracker's registration (every sample's frame -> StatsBomb) and venue
    (camera heights -> StatsBomb, the reference view, the pitch), plus the fit:
    each sample's paint on the ground, distance to the nearest model line
    (`masks`: gather's packed paint masks; read from the video without them).
    With `paint` (gather's points per sample) the chained rotations are first
    tied back to the pitch sample by sample (`autocal.anchor_chain`)."""
    C, R0, L, W = rig_pose
    C = np.asarray(C, float)
    pm = A.PitchModel(L=L, W=W)
    prim = A.Primitives(pm)
    if paint is None:
        rig = A.Rig(C, np.array([R @ R0 for R in Q['R']]), Q['f'])
    else:
        R2, f2, _ = A.anchor_chain(paint, Q['R'], Q['f'], C, R0, pm, log=log)
        rig = A.Rig(C, R2, f2)
    n = len(Q['frames'])
    Hsb = np.array([rig.to_sb(k, pm) for k in range(n)])

    masks = _Masks(masks, Q['frames'])
    ninl = np.zeros(n, int)
    d_all = []
    for k in range(n):
        ys, xs = np.nonzero(masks[k])
        G = np.linalg.inv(rig.ground_to_image(k))
        g = np.c_[xs * 2.0 + 0.5, ys * 2.0 + 0.5, np.ones(len(xs))] @ G.T
        g = g[g[:, 2] > 0]
        g = g[:, :2] / g[:, 2:]
        g = g[(g[:, 0] > -2) & (g[:, 0] < L + 2) & (g[:, 1] > -2) & (g[:, 1] < W + 2)]
        if not len(g):
            continue
        d = np.hypot(*(g - prim.nearest(g)).T) * M_PER_R
        inl = d < INLIER_M
        ninl[k] = inl.sum()
        d_all.append(d[inl])
    d_all = np.concatenate(d_all) if d_all else np.zeros(1)
    err = dict(err_mean=d_all.mean(), err_max=d_all.max(), err_p90=np.percentile(d_all, 90))
    log('paint within %.1f m of a line: %d px over %d samples; mean %.2f m p90 %.2f m; samples < 30: %d'
        % (INLIER_M, len(d_all), n, err['err_mean'], err['err_p90'], (ninl < 30).sum()))

    h = C[2]
    foot = np.array([C[0] * 120 / L, C[1] * 80 / W])
    Ach = np.array([[0, h * 120 / L, foot[0]], [h * 80 / W, 0, foot[1]], [0, 0, 1.0]])   # the scrimmage map's axes
    homog.mkdir(parents=True, exist_ok=True)
    np.savez(homog / f'{tag}_reg.npz', abs=np.asarray(Q['frames']), Hsb=Hsb, ninl=ninl, ok=np.ones(n, bool))
    np.savez(homog / f'{tag}_venue.npz', A=Ach, ref=Hsb[0], L=L, W=W, C=C, **err)
    log('camera at u %.2f v %.2f h %.3f R (foot SB %.1f, %.1f); pitch %.1f x %.1f m -> %s'
        % (C[0], C[1], h, foot[0], foot[1], L * M_PER_R, W * M_PER_R, homog))
    return err


# ---- the run ------------------------------------------------------------------------


def run(tag: str, lo: int, hi: int, work: Path = WORK, homog: Path = HOMOG, lines: str | None = None,
        camera: str | None = None, redo: str | None = None) -> dict:
    d = work / tag
    d.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    logfile = open(d / 'log.txt', 'a', encoding='utf-8')

    def log(s):
        line = '%5.0f s  %s' % (time.time() - t0, s)
        print(line, flush=True)
        logfile.write(line + '\n')
        logfile.flush()

    # the stretch, the paint colour and the camera source are fixed once; a resumed
    # run with different ones would quietly mix two calibrations
    spec = dict(lo=lo, hi=hi, lines=lines or os.environ.get('PIQ_LINES', 'gold'), camera=camera,
                video=os.environ.get('PIQ_VIDEO'))
    old = _load(d / 'spec.pkl') if (d / 'spec.pkl').exists() else None
    first = STAGES.index(redo) if redo else len(STAGES)
    if old is not None and old != spec:
        changed = [k for k in spec if old.get(k) != spec[k]]
        if redo != 'gather':
            raise SystemExit(f'{d} was run with other {changed}; pass --redo gather or another tag')
    _save(d / 'spec.pkl', spec)
    A.LINES = spec['lines']
    log('%s %d..%d lines %s%s' % (tag, lo, hi, spec['lines'], ' camera from ' + camera if camera else ''))

    out = {'gather': d / 'gather.pkl', 'prep': d / 'prep.pkl', 'cal': d / 'cal.pkl', 'height': d / 'full.pkl'}

    rerun = []

    def fresh(stage):
        # a stage reruns when asked to, when its file is missing, or when an earlier one reran
        if rerun or STAGES.index(stage) >= first or not out[stage].exists():
            rerun.append(stage)
        return bool(rerun)

    if fresh('gather'):
        log('== gather')
        _save(out['gather'], gather(lo, hi, log))
    if fresh('prep'):
        log('== prep')
        Q, img = prep(_load(out['gather']), log)
        _save(out['prep'], Q)
        import cv2
        cv2.imwrite(str(d / 'pano.jpg'), img)
    Q = _load(out['prep'])
    if fresh('cal'):
        log('== cal')
        cam = None
        if camera:
            src = work / camera
            cam = _load(src / 'full.pkl') if (src / 'full.pkl').exists() else _load(src / 'cal.pkl')
        _save(out['cal'], calibrate(Q, cam, log))
    if camera:
        pose = _load(out['cal'])
    else:
        if fresh('height'):
            log('== height')
            _save(out['height'], height(tag, Q, _load(out['cal']), log))
        pose = _load(out['height'])
    log('== write')
    G = _load(out['gather'])
    err = write(tag, Q, pose, homog, log, G.get('paint'), G.get('masks'))
    log('done')
    logfile.close()
    return err


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='python -m cv.experiments.autocal_run', description=__doc__.split('\n\n')[0])
    p.add_argument('tag')
    p.add_argument('lo', type=int)
    p.add_argument('hi', type=int)
    p.add_argument('--lines', choices=['gold', 'white'], help='paint colour (default PIQ_LINES, else gold)')
    p.add_argument('--camera', help="another tag's camera and pitch: solve this stretch's rotation only")
    p.add_argument('--redo', choices=STAGES, help='rerun this stage and every later one')
    p.add_argument('--work', type=Path, default=WORK)
    p.add_argument('--homog', type=Path, default=HOMOG, help='where the _reg/_venue files go')
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.hi <= a.lo:
        raise SystemExit('hi must be after lo')
    run(a.tag, a.lo, a.hi, a.work, a.homog, a.lines, a.camera, a.redo)
    return 0


if __name__ == '__main__':
    sys.exit(main())
