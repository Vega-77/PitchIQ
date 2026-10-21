"""Player tracking on the lab window: extract, baseline, track, score.

The player lab (`cv/balltrack/plab/`) has forty hand-labelled keyframes over
twenty seconds of fr00. This runs every tracker over the same frames and
scores them on those labels the same way, so a change is judged on the people
the user named rather than on how the boxes look.

    python -m cv.experiments.player_lab extract      # GPU, ~1 min: detections + colours
    python -m cv.experiments.player_lab features     # GPU: detector features per box
    python -m cv.experiments.player_lab redesc       # CPU: colours again, same boxes
    python -m cv.experiments.player_lab baseline     # GPU: the shipped pipeline's chain
    python -m cv.experiments.player_lab track        # CPU: variants + label-free checks
    python -m cv.experiments.player_lab score --labels DIR

`--labels` is a folder of keyframe docs as the lab stores them (`kf/k00.json`
...), fetched from the artifact's database. Scoring reruns the CPU tracking
from the saved detections, so it is one command once labels exist.

The dev window is fr00 136170..136769. A second window is for testing only:
nothing in this file reads labels except `score`, and nothing is tuned on
`score` output from the test window.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from cv import player_eval
from cv.linking import LinkSettings, Linker
from cv.pitchtrack import (
    Detections,
    PitchProjector,
    PitchTracker,
    TrackerSettings,
    body_descriptor,
    drop_clipped,
    drop_contained,
    grass_hue,
)

PLAB = Path(__file__).resolve().parents[1] / 'balltrack' / 'plab'
VIDEO = os.environ.get(
    'PIQ_VIDEO', str(Path.home() / 'Downloads' / '8-26-26 Scrimmage v Gov Livingston - Tactical.mp4'))
LO, N = 136170, 600


def _paths(tag: str) -> dict[str, Path]:
    out = PLAB / 'out'
    return {
        'dets': out / f'{tag}_dets.npz',
        'feat': out / f'{tag}_feat.npz',
        'baseline': out / f'{tag}_baseline.json',
        'sheets': PLAB / 'sheets.json' if tag == 'fr00' else out / f'{tag}_sheets.json',
    }


# ---- extract ----------------------------------------------------------------------


CHUNK = int(os.environ.get('PLAB_CHUNK', 9000))   # 5 minutes: a crash loses at most one


def _chunks(lo: int, hi: int) -> list[tuple[int, int]]:
    """lo..hi (inclusive) cut on absolute multiples of CHUNK, so a rerun over a
    longer or shorter window reuses the same finished pieces."""
    out, a = [], lo
    while a <= hi:
        b = min(hi, (a // CHUNK + 1) * CHUNK - 1)
        out.append((a, b))
        a = b + 1
    return out


def _part(tag: str, kind: str, a: int, b: int) -> Path:
    return PLAB / 'out' / 'parts' / f'{tag}_{kind}_{a}_{b}.npz'


def _frames(cap, a: int, b: int):
    """(f, frame) for f in a..b from cap, already seeked to a; a short video raises."""
    for f in range(a, b + 1):
        ok, frame = cap.read()
        if not ok:
            raise SystemExit(f'video ended at {f}')
        yield f, frame


def _ahead(it, depth: int = 16):
    """The items of `it`, in order, produced on a thread up to `depth` ahead:
    OpenCV lets go of the GIL while it decodes and resizes, so the GPU and the
    colour work run meanwhile. An exception in `it` is raised here."""
    import queue
    import threading

    q, stop = queue.Queue(depth), threading.Event()

    def put(x):
        while not stop.is_set():
            try:
                q.put(x, timeout=0.1)
                return True
            except queue.Full:
                pass
        return False

    def run():
        try:
            for x in it:
                if not put(x):
                    return
            put(None)
        except BaseException as e:   # noqa: BLE001 - handed to the reader
            put(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        while (x := q.get()) is not None:
            if isinstance(x, BaseException):
                raise x
            yield x
    finally:
        stop.set()
        t.join()


def _colours(idx, batch, boxes):
    """kit and desc rows of one predicted batch: [(f, xyxy, conf, kit, desc)]."""
    from cv.teams import kit_sample

    out = []
    for fi, img, (xy, cf) in zip(idx, batch, boxes):
        grass = grass_hue(img)
        for xyxy, c in zip(xy, cf):
            k = kit_sample(img, xyxy)
            out.append((fi, xyxy, float(c), np.full(4, np.nan) if k is None else np.asarray(k, dtype=np.float64),
                        body_descriptor(img, xyxy, grass)))
    return out


def _save_atomic(path: Path, **arrays) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.part.npz')
    np.savez_compressed(tmp, **arrays)
    os.replace(tmp, path)


class Extractor:
    """The detector and its feature hook, run one 5-minute piece at a time.

    `extract` and `features` drive it over a window; cv/balltrack/half.py feeds
    it pieces as the ball pass finishes them. Each piece is written atomically
    to plab/out/parts and a rerun skips the finished ones.
    """

    def __init__(self, imgsz: int = 1280, conf: float = 0.05):
        import cv2

        self.imgsz, self.conf = imgsz, conf
        self.cap = cv2.VideoCapture(VIDEO)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.model = self.net = None

    def _seek(self, a: int) -> None:
        import cv2

        self.cap.set(cv2.CAP_PROP_POS_FRAMES, a)
        landed = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
        if landed != a:
            raise SystemExit(f'seek landed on {landed}, not {a}')

    def dets_part(self, tag: str, a: int, b: int) -> Path:
        """Detections and colours for frames a..b."""
        path = _part(tag, 'dets', a, b)
        if path.exists():
            with np.load(path) as z:
                if int(z['imgsz']) == self.imgsz and float(z['min_conf']) == self.conf and str(z['video']) == VIDEO:
                    return path
        from concurrent.futures import ThreadPoolExecutor

        from cv.detector import CLASS_PERSON, DEFAULT_WEIGHTS

        if self.model is None:
            from ultralytics import YOLO
            self.model = YOLO(str(DEFAULT_WEIGHTS))
        self._seek(a)
        rows = {'frame': [], 'box': [], 'conf': [], 'kit': [], 'desc': []}

        def take(fut):
            for fi, xyxy, c, k, d in fut.result():
                for key, v in zip(rows, (fi, xyxy, c, k, d)):
                    rows[key].append(v)

        # one colour thread, batches in order, while the detector runs the next
        pending = []
        with ThreadPoolExecutor(1) as colour:
            batch, idx = [], []
            for f, frame in _ahead(_frames(self.cap, a, b)):
                batch.append(frame)
                idx.append(f)
                if len(batch) == 8 or f == b:
                    results = self.model.predict(batch, classes=[CLASS_PERSON], conf=self.conf, imgsz=self.imgsz,
                                                 verbose=False)
                    boxes = [(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy()) for r in results]
                    pending.append(colour.submit(_colours, idx, batch, boxes))
                    if len(pending) > 2:
                        take(pending.pop(0))
                    batch, idx = [], []
            for fut in pending:
                take(fut)
        _save_atomic(path, frame=np.array(rows['frame'], dtype=np.int64), box=np.array(rows['box']).reshape(-1, 4),
                     conf=np.array(rows['conf']), kit=np.array(rows['kit']).reshape(-1, 4),
                     desc=np.array(rows['desc']), imgsz=self.imgsz, min_conf=self.conf, video=VIDEO)
        return path

    def _features_for(self, det: Detections, a: int, b: int) -> np.ndarray:
        """Detector features of det's rows (all inside frames a..b), in row order."""
        import cv2
        import torch
        from torchvision.ops import roi_align

        if self.net is None:
            from ultralytics import YOLO

            from cv.detector import DEFAULT_WEIGHTS
            # only the backbone up to the feature layer: nothing after it changes its output
            self.net = YOLO(str(DEFAULT_WEIGHTS)).model.eval().cuda().model[:FEATURE_LAYER + 1]
            assert all(m.f == -1 for m in self.net)       # a plain chain, each layer reads the last
        net = self.net
        rows_by = det.rows_by_frame()
        out = np.zeros((len(det), 128 * 8), np.float16)    # layer 6 has 128 channels, pooled 4 x 2

        def run(batch):
            x = torch.from_numpy(np.stack([im[:, :, ::-1] for _, (im, _) in batch]))
            x = x.permute(0, 3, 1, 2)
            shape = batch[0][1][1]
            sx, sy = 1280 / shape[1], 736 / shape[0]
            rows = np.concatenate([rows_by[f] for f, _ in batch])
            bx = det.box[rows]
            which = np.concatenate([np.full(len(rows_by[f]), q) for q, (f, _) in enumerate(batch)])
            boxes = torch.tensor(np.c_[which, bx[:, 0] * sx, bx[:, 1] * sy, bx[:, 2] * sx, bx[:, 3] * sy],
                                 dtype=torch.float32).cuda()
            with torch.no_grad():
                fm = net(x.cuda().float() / 255).float()
                r = roi_align(fm, boxes, output_size=(4, 2), spatial_scale=fm.shape[-1] / 1280, aligned=True)
                out[rows] = torch.nn.functional.normalize(r.flatten(1), dim=1).half().cpu().numpy()

        if len(det):
            self._seek(a)
            batch = []
            # the reader thread decodes and shrinks; the GPU never waits on it
            used = (f in rows_by and len(rows_by[f]) for f in range(a, b + 1))
            frames = ((f, (cv2.resize(img, (1280, 736)), img.shape) if use else None)
                      for use, (f, img) in zip(used, _frames(self.cap, a, b)))
            for f, x in _ahead(frames):
                if x is not None:
                    batch.append((f, x))
                if len(batch) == FEATURE_BATCH or (f == b and batch):
                    run(batch)
                    batch = []
        return out

    def feat_part(self, tag: str, a: int, b: int, rows: np.ndarray, det: Detections) -> Path:
        """Features of the deduplicated detections in frames a..b; `rows` are
        their row numbers in the whole window's `load_detections`."""
        path = _part(tag, 'feat', a, b)
        if path.exists():
            with np.load(path) as z:
                if np.array_equal(z['rows'], rows):
                    return path
        _save_atomic(path, rows=rows, feat=self._features_for(det, a, b))
        return path

    def feat_part_from_dets(self, tag: str, a: int, b: int, first_row: int) -> tuple[Path, int]:
        """feat_part for a piece whose dets_part is done, numbering its rows from
        first_row: the pieces before it in the window hold the rows before."""
        with np.load(_part(tag, 'dets', a, b)) as z:
            det = drop_contained(drop_clipped(Detections(
                frame=z['frame'], box=z['box'], conf=z['conf'], team=np.full(len(z['frame']), -1),
                desc=z['desc']), self.h))
        rows = first_row + np.arange(len(det))
        return self.feat_part(tag, a, b, rows, det), len(det)


def extract(tag: str, lo: int, n: int, imgsz: int, conf: float) -> None:
    """Detections and colours over lo..lo+n-1, in 5-minute pieces that a rerun skips."""
    ex = Extractor(imgsz, conf)
    t0 = time.time()
    parts = []
    for a, b in _chunks(lo, lo + n - 1):
        parts.append(ex.dets_part(tag, a, b))
        print(f'{a}..{b}', '%.0fs' % (time.time() - t0), flush=True)
    merged = {k: [] for k in ('frame', 'box', 'conf', 'kit', 'desc')}
    for path in parts:
        with np.load(path) as z:
            for k in merged:
                if len(z['frame']):
                    merged[k].append(z[k])
    out = _paths(tag)['dets']
    _save_atomic(out, **{k: np.concatenate(v) for k, v in merged.items()},
                 fps=ex.fps, w=ex.w, h=ex.h, lo=lo, n=n, imgsz=imgsz, min_conf=conf)
    for path in parts:
        os.remove(path)
    print('wrote', out, sum(len(v) for v in merged['frame']), 'detections', '%.0fs' % (time.time() - t0))


def redescribe(tag: str) -> None:
    """Recompute the body descriptors of saved detections, without the detector."""
    import cv2

    path = _paths(tag)['dets']
    z = dict(np.load(path))
    lo, n = int(z['lo']), int(z['n'])
    cap = cv2.VideoCapture(VIDEO)
    cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
    if int(cap.get(cv2.CAP_PROP_POS_FRAMES)) != lo:
        raise SystemExit(f'seek did not land on {lo}')
    desc = np.full_like(z['desc'], np.nan)
    for f in range(lo, lo + n):
        ok, img = cap.read()
        if not ok:
            break
        grass = grass_hue(img)
        for i in np.flatnonzero(z['frame'] == f):
            desc[i] = body_descriptor(img, z['box'][i], grass)
    changed = ~np.isclose(desc, z['desc'], equal_nan=True).all(axis=1)
    z['desc'] = desc
    np.savez_compressed(path, **z)
    print('rewrote', path, f'{int(changed.sum())} of {len(desc)} descriptors changed')


FEATURE_LAYER = 6                       # YOLOv8n backbone, stride 16, 128 channels
FEATURE_BATCH = int(os.environ.get('PLAB_FEATURE_BATCH', 8))   # frames per forward pass


def features(tag: str) -> None:
    """Detector features per saved detection, for the linker's appearance cost.

    The detector's own backbone (layer 6) pooled over each box on a 4 x 2
    grid, unit length. Same person vs a teammate, end of one tracklet vs
    start of the next: AUC 0.75 / 0.74 on fm01 / fr00, against 0.64 / 0.66
    for the shirt-and-shorts colours. Rows follow `load_detections(tag)`.
    """
    z = np.load(_paths(tag)['dets'])
    # the same rows as load_detections, without its team pass
    det = drop_contained(drop_clipped(Detections(
        frame=z['frame'], box=z['box'], conf=z['conf'], team=np.full(len(z['frame']), -1), desc=z['desc']),
        int(z['h'])))
    ex = Extractor()
    out = np.zeros((len(det), 128 * 8), np.float16)
    t0 = time.time()
    parts = []
    for a, b in _chunks(int(det.frame.min()), int(det.frame.max())):
        rows = np.flatnonzero((det.frame >= a) & (det.frame <= b))
        sub = Detections(frame=det.frame[rows], box=det.box[rows], conf=det.conf[rows], team=det.team[rows],
                         desc=det.desc[rows])
        parts.append(ex.feat_part(tag, a, b, rows, sub))
        with np.load(parts[-1]) as p:
            out[rows] = p['feat']
        print(f'{a}..{b}', '%.0fs' % (time.time() - t0), flush=True)
    path = _paths(tag)['feat']
    _save_atomic(path, feat=out, layer=FEATURE_LAYER)
    for p in parts:
        os.remove(p)
    print('wrote', path, out.shape, '%.0fs' % (time.time() - t0))


def load_detections(tag: str, dedupe: bool = True, with_features: bool = True):
    z = np.load(_paths(tag)['dets'])
    from cv.teams import TEAM_A, TEAM_B, assign_teams

    kit, conf = z['kit'], z['conf']    # each z[...] decompresses the whole array: read once, not per row
    # A kit guess per detection, used only as a soft hint while tracking;
    # teams proper are decided per tracklet by the linker.
    samples = {i: [kit[i]] for i in range(len(kit)) if np.isfinite(kit[i]).any() and conf[i] >= 0.25}
    a = assign_teams(samples)
    team = np.array([{TEAM_A: 0, TEAM_B: 1}.get(a.team_of(i), -1) for i in range(len(kit))])
    det = Detections(frame=z['frame'], box=z['box'], conf=z['conf'], team=team, desc=z['desc'], kit=kit)
    meta = {k: z[k].item() for k in ('fps', 'w', 'h', 'lo', 'n')}
    if dedupe:
        det = drop_contained(drop_clipped(det, meta['h']))
    path = _paths(tag)['feat']
    if with_features and dedupe and path.exists():
        feat = np.load(path)['feat']
        if len(feat) != len(det):
            raise SystemExit(f'{path} has {len(feat)} rows for {len(det)} detections: run `features` again')
        # kept float16, as saved: the linker averages them in float64 (linking._unit_mean), which
        # float16 converts to exactly, and a float32 copy of a whole half was 18 GB on NB
        det.feat = feat
    return det, meta


def projector(tag: str, w: int, h: int) -> tuple[PitchProjector, object]:
    from cv.panned import PannedCamera

    cam = PannedCamera.from_tracker(tag)
    return PitchProjector.from_panned(cam, w, h), cam.pitch


# ---- baseline: what the website pipeline does today -------------------------------


def baseline(tag: str, lo: int, n: int, imgsz: int) -> None:
    from cv.frames import TrackedFramePass
    from cv.identity import cluster_of_track, merge_tracks
    from cv.panned import PannedCamera
    from cv.participants import classify_participants
    from cv.pipeline import _stream_batches
    from cv.teams import assign_teams

    import cv2
    cap = cv2.VideoCapture(VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    t0 = time.time()
    single = TrackedFramePass(imgsz=imgsz)
    table, colours, _ = single.run(
        VIDEO, fps=fps, frame_width=w, frame_height=h,
        batches=_stream_batches(Path(VIDEO), fps, lo / fps, (lo + n) / fps), stride=1)
    px = {r.frame_index: r.players.copy() for r in table.records if lo <= r.frame_index < lo + n}
    cam = PannedCamera.from_tracker(tag)
    table.calibration = cam.calibration
    cam.stabilise(table)
    assignment = assign_teams(colours)
    table.team_by_track = assignment.by_track
    parts = classify_participants(table)
    clusters = merge_tracks(table, colours, is_player=parts.is_player)
    c_of = cluster_of_track(clusters)
    officials = parts.officials

    def role(tid):
        if tid in officials:
            return 'referee'
        return 'player' if parts.is_player(tid) else 'off'

    out = {'botsort': {}, 'clusters': {}}
    for f, arr in px.items():
        for name in out:
            out[name][f] = []
        for tid, x1, y1, x2, y2, _c in arr.tolist():
            tid = int(tid)
            rec = [x1, y1, x2, y2, assignment.team_of(tid), role(tid)]
            out['botsort'][f].append([tid] + rec)
            out['clusters'][f].append([('c%d' % c_of[tid]) if tid in c_of else 't%d' % tid] + rec)
    path = _paths(tag)['baseline']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'fps': fps, 'lo': lo, 'n': n, 'imgsz': imgsz, 'tracks': len(colours),
                                'clusters': len(clusters), 'preds': out}))
    print('wrote', path, len(colours), 'tracks ->', len(clusters), 'clusters', '%.0fs' % (time.time() - t0))


def baseline_predictors(tag: str) -> dict:
    path = _paths(tag)['baseline']
    if not path.exists():
        return {}
    preds = json.loads(path.read_text())['preds']
    out = {}
    for name, by_frame in preds.items():
        table = {int(f): [player_eval.Pred(r[0], np.array(r[1:5]), r[5], r[6]) for r in rows]
                 for f, rows in by_frame.items()}
        out['pipeline ' + name] = (lambda t: (lambda f: t.get(f, [])))(table)
    return out


# ---- the model --------------------------------------------------------------------

VARIANTS = {
    # name: (tracker settings, link settings); None = no linking at all
    'A tracklets': ({}, None),
    'A no view': ({'max_miss_out_of_view': 15}, None),
    'A+B+C': ({}, {}),
    'A+B+C no features': ({}, {'feature_weight': 0.0}),
    'A+B+C no cut': ({'cut_ambiguous': False}, {}),
    'A+B+C no view': ({'max_miss_out_of_view': 15}, {'unseen_penalty_per_s': 0.0}),
    'A+B+C no margin': ({}, {'ambiguity_margin': 0.0, 'feature_margin': 0.0}),
    'A+B+C no fill': ({}, {'fill_gap_s': 0.0}),
}


def run_variant(name: str, det, meta, proj, pitch):
    tset, lset = VARIANTS[name]
    tracker = PitchTracker(proj, TrackerSettings(fps=meta['fps'], **tset))
    tracklets = tracker.run(det)
    link = LinkSettings(fps=meta['fps'], **(lset or {}))
    if lset is None:
        link.stage_windows_s = ()
        link.cap_window_s = -1.0
        link.fill_gap_s = 0.0
    result = Linker(proj, pitch.length_m, pitch.width_m, link).link(tracklets, det)
    return tracklets, result


def predictor(result):
    cache = {}

    def predict(f):
        if f not in cache:
            cache[f] = [player_eval.Pred(i.pid, b, result.team_at(i, f), i.role) for i, b in result.at(f)]
        return cache[f]
    return predict


def self_check(name, tracklets, result, meta) -> dict:
    """Label-free sanity: does the output obey the match's own rules?"""
    lo, n = meta['lo'], meta['n']
    per_frame = {'team_a': [], 'team_b': []}
    boxes = []
    for f in range(lo, lo + n):
        here = result.at(f)
        boxes.append(len(here))
        for t in per_frame:
            per_frame[t].append(sum(1 for i, _ in here if i.team == t and i.role == 'player'))
    over = sum(1 for t in per_frame for c in per_frame[t] if c > 11)
    roles: dict = {}
    for i in result.identities:
        key = i.role if i.role != 'player' else i.team
        roles[key] = roles.get(key, 0) + 1
    long = sorted((len(i.frames) / meta['fps'] for i in result.identities), reverse=True)
    return {
        'name': name,
        'tracklets': len(tracklets),
        'ids': len(result.identities),
        'ids by role': ' '.join('%s:%d' % kv for kv in sorted(roles.items())),
        'joins': len(result.joins),
        'refused': len(result.refused),
        'boxes/frame': round(float(np.mean(boxes)), 1),
        'frames >11': over,
        'top22 median s': round(float(np.median(long[:22])), 1) if long else 0,
        'defends x=0': result.defends_left,
    }


def track(tag: str) -> dict:
    det, meta = load_detections(tag)
    proj, pitch = projector(tag, meta['w'], meta['h'])
    missing = sum(1 for f in range(meta['lo'], meta['lo'] + meta['n']) if not proj.has(f))
    print(f'{len(det)} detections; {missing} of {meta["n"]} frames without pitch registration')
    rows, results = [], {}
    for name in VARIANTS:
        t0 = time.time()
        tracklets, result = run_variant(name, det, meta, proj, pitch)
        results[name] = result
        row = self_check(name, tracklets, result, meta)
        row['cpu s'] = round(time.time() - t0, 1)
        rows.append(row)
    print(player_eval.format_table(rows))
    return {'det': det, 'meta': meta, 'proj': proj, 'results': results}


def score(tag: str, labels: Path, partial: bool) -> None:
    sheets = json.loads(_paths(tag)['sheets'].read_text())
    docs = player_eval.load_docs(labels)
    truth = player_eval.build_truth(sheets, docs, include_partial=partial)
    print(f'{len(docs)} label docs: {len(truth.confirmed)} confirmed, {len(truth.partial)} partial '
          f'keyframes scored; {len(truth.named)} named people')
    if not truth.frames:
        raise SystemExit('nothing to score yet')
    run = track(tag)
    proj = run['proj']

    sheet_preds = {}
    for s in sheets['sheets']:
        sheet_preds[int(s['f'])] = [
            player_eval.Pred(b['tid'], np.array(b['xyxy']), b.get('team') or 'unknown', 'player')
            for b in s['boxes']]
    predictors = {'lab BoT-SORT ids': lambda f: sheet_preds.get(f, [])}
    predictors.update(baseline_predictors(tag))
    for name, result in run['results'].items():
        predictors[name] = predictor(result)

    rows = [player_eval.score(name, truth, p, proj).row() for name, p in predictors.items()]
    print()
    print(player_eval.format_table(rows))

    # Two people one behind the other: a lab click lands on either box, so
    # who is who there is the labeller's guess as much as the model's.
    named = [e for f in truth.frames for e in truth.entries[f] if player_eval._is_player_label(e.label)]
    crowded = sum(e.crowded for e in named)
    print(f'\nidentity on clear views only ({crowded} of {len(named)} player labels crowded, left out):')
    keys = ('name', 'IDF1', 'wrong_person', 'merged_ids', 'ids_per_player', 'switches')
    clear = [player_eval.score(name, truth, p, proj, clear_only=True).row() for name, p in predictors.items()]
    print(player_eval.format_table([{k: r[k] for k in keys} for r in clear]))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='python -m cv.experiments.player_lab',
                                description=__doc__.split('\n\n')[0])
    p.add_argument('cmd', choices=['extract', 'features', 'redesc', 'baseline', 'track', 'score'])
    p.add_argument('--tag', default='fr00')
    p.add_argument('--lo', type=int, default=LO)
    p.add_argument('--n', type=int, default=N)
    p.add_argument('--imgsz', type=int, default=1280)
    p.add_argument('--conf', type=float, default=0.05)
    p.add_argument('--labels', type=Path)
    p.add_argument('--confirmed-only', action='store_true')
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == 'extract':
        extract(args.tag, args.lo, args.n, args.imgsz, args.conf)
    elif args.cmd == 'features':
        features(args.tag)
    elif args.cmd == 'redesc':
        redescribe(args.tag)
    elif args.cmd == 'baseline':
        baseline(args.tag, args.lo, args.n, args.imgsz)
    elif args.cmd == 'track':
        track(args.tag)
    else:
        if args.labels is None:
            raise SystemExit('--labels DIR is required')
        score(args.tag, args.labels, partial=not args.confirmed_only)
    return 0


if __name__ == '__main__':
    sys.exit(main())
