"""Player pictures for a report counted from the table: a short read of chosen frames.

    from pitchiq.table_pictures import attach
    said = attach(report, half, video)      # after pipeline.analyse_table(half)

The table path (`pipeline.analyse_table`) reads no video, so its figures came without the
pictures the coach's who's-who shows. This picks, for each figure the report could keep a
picture of, three moments where the map saw it biggest and alone, decodes only those frames
(a few hundred seeks instead of the half), and keeps the sharpest of the tallest.

A seen map dot is a detector box's feet placed on the pitch (`experiments/wholemap.py`), so
the half's camera puts it back on its box: on wb1 a dot lands 2.6 px from its box's feet at
the median and within 0.15 box heights 93% of the time (MATCH). The box, not the dot, is
what gets cut, by the same rules the video path used (`player_thumbnails`): the tallest box
wins, nothing touching the frame's edge, nothing wider than a standing person.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .player_thumbnails import Thumb, attach_thumbs, crop_score, cut, encode, fit_budget

# Figures asked for, most screen time first. The budget (`fit_budget`, 400 KB) keeps fewer:
# a 128 px picture is 4-6 KB once encoded, so asking for more only decodes frames to drop.
WANT = 160
EVERY = 15  # frames between the dots looked at: half a second
MATCH = 0.15  # a dot is on a box when it lands within this many box heights of its feet
ALONE = 0.10  # a box with more than this share of it under another box shows two people
OTHER_CONF = 0.1  # boxes less sure than this are not counted as someone in the way
TRIES = 3  # pictures decoded per figure, the sharpest kept (`best_of`)
GAP = 60  # frames between a figure's tries: two seconds, different strides
TALL_ENOUGH = 0.8  # a try this tall against the tallest can win on sharpness
SHARP_H = 64  # px: the height sharpness is measured at
SEEK_AFTER = 90  # frames: nearer than this, decoding forward is cheaper than a seek


@dataclass(frozen=True)
class Pick:
    """One figure's chosen picture: its stats track, the frame and the box to cut."""

    track_id: int
    frame: int
    box: tuple[float, float, float, float]
    score: float


def dets_path(tag: str) -> Path:
    from .experiments.player_lab import _paths

    return _paths(tag)['dets']


def feet_px(cam, f: np.ndarray, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pitch metres on frames `f` -> the pixel the half's camera puts them at.

    The map's metres are the camera's StatsBomb units scaled to the pitch's size
    (`wholemap.pitch_map`), so this is that map run backwards."""
    D = np.diag([cam.length_m / 120, cam.width_m / 80, 1.0])
    M = np.einsum('ij,njk->nik', D, cam.H[np.asarray(f, np.int64) - int(cam.abs[0])])
    q = np.linalg.solve(M, np.stack([x, y, np.ones_like(x)], 1)[..., None])[..., 0]
    return q[:, 0] / q[:, 2], q[:, 1] / q[:, 2]


def covered(box: np.ndarray, others: np.ndarray) -> float:
    """The largest share of `box` that any one of `others` covers."""
    if not len(others):
        return 0.0
    ix = np.clip(np.minimum(box[2], others[:, 2]) - np.maximum(box[0], others[:, 0]), 0, None)
    iy = np.clip(np.minimum(box[3], others[:, 3]) - np.maximum(box[1], others[:, 1]), 0, None)
    area = max((box[2] - box[0]) * (box[3] - box[1]), 1e-6)
    return float((ix * iy).max() / area)


def choose(half, cam, dets, track_ids, every: int = EVERY) -> dict[int, list[Pick]]:
    """Per stats track id in `track_ids`, the boxes that would make its picture: those that
    make one at all (`crop_score`), with one of the track's seen dots on their feet and nobody
    over them, tallest first.

    `dets` is the half's detections (`plab/out/<tag>_dets.npz`: frame, box, conf, w, h),
    sorted by frame as the extract writes them. Only every `every`-th frame is looked at.
    """
    p = half.players
    tids = half.track_ids()
    keep = p['seen'].astype(bool) & (p['f'] % every == 0) & np.isin(tids, list(track_ids))
    f, tid = p['f'][keep].astype(np.int64), tids[keep]
    if not len(f):
        return {}
    u, v = feet_px(cam, f, p['x'][keep].astype(float), p['y'][keep].astype(float))
    frame, boxes, conf = dets['frame'], dets['box'], dets['conf']
    w, h = int(dets['w']), int(dets['h'])
    lo, hi = np.searchsorted(frame, f, 'left'), np.searchsorted(frame, f, 'right')
    found: dict[int, list[Pick]] = {}
    for i in range(len(f)):
        on = boxes[lo[i] : hi[i]]
        if not len(on):
            continue
        d = np.hypot((on[:, 0] + on[:, 2]) / 2 - u[i], on[:, 3] - v[i])
        j = int(d.argmin())
        b = on[j]
        if d[j] > MATCH * (b[3] - b[1]):
            continue
        score = crop_score(tuple(float(c) for c in b), w, h)
        if score is None:
            continue
        others = np.delete(on, j, 0)[np.delete(conf[lo[i] : hi[i]], j) >= OTHER_CONF]
        if covered(b, others) > ALONE:
            continue
        found.setdefault(int(tid[i]), []).append(
            Pick(int(tid[i]), int(f[i]), tuple(float(c) for c in b), score)
        )
    return {t: sorted(ks, key=lambda k: (-k.score, k.frame)) for t, ks in found.items()}


def shortlist(picks: list[Pick], n: int = TRIES, gap: int = GAP) -> list[Pick]:
    """The `n` tallest of `picks` (tallest first) at least `gap` frames apart: different
    moments, so one blurred stride does not blur them all."""
    out: list[Pick] = []
    for k in sorted(picks, key=lambda k: (-k.score, k.frame)):
        if all(abs(k.frame - o.frame) >= gap for o in out):
            out.append(k)
            if len(out) == n:
                break
    return out


def sharpness(patch: np.ndarray) -> float:
    """How sharp a picture is: the variance of its Laplacian at one fixed height, so a big
    picture and a small one are measured alike."""
    import cv2

    grey = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    scale = SHARP_H / grey.shape[0]
    grey = cv2.resize(grey, (max(1, round(grey.shape[1] * scale)), SHARP_H))
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def read(video: Path | str, picks: list[Pick]) -> dict[Pick, tuple[np.ndarray, float]]:
    """Decode each pick's frame once, in order: per pick, its cut picture and how sharp it is."""
    import cv2

    by_frame: dict[int, list[Pick]] = {}
    for k in picks:
        by_frame.setdefault(k.frame, []).append(k)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise FileNotFoundError(f'cannot open {video}')
    out: dict[Pick, tuple[np.ndarray, float]] = {}
    at = 0  # the frame the next read() returns
    try:
        for fr in sorted(by_frame):
            if not 0 <= fr - at < SEEK_AFTER:
                cap.set(cv2.CAP_PROP_POS_FRAMES, fr)
                at = fr
            while at < fr:
                cap.grab()
                at += 1
            ok, img = cap.read()
            at += 1
            if not ok:
                continue
            for k in by_frame[fr]:
                patch = cut(img, k.box)
                if patch is not None:
                    out[k] = (patch, sharpness(patch))
    finally:
        cap.release()
    return out


def best_of(tries: list[Pick], cuts: dict[Pick, tuple[np.ndarray, float]]) -> Pick | None:
    """The sharpest of the tries nearly as tall as the tallest (TALL_ENOUGH)."""
    have = [k for k in tries if k in cuts]
    if not have:
        return None
    top = max(k.score for k in have)
    return max((k for k in have if k.score >= TALL_ENOUGH * top), key=lambda k: cuts[k][1])


def attach(report, half, video: Path | str, cam=None, dets=None, want: int = WANT) -> dict:
    """Give the report's figures their pictures and fit them to the budget. Returns what it
    did, for the log: figures asked for, figures with a picture, frames read, kept, dropped."""
    from .balltrack.camera import Camera

    t0 = time.time()
    cam = cam if cam is not None else Camera(half.tag)
    dets = dets if dets is not None else np.load(dets_path(half.tag))
    clusters = sorted(report.clusters, key=lambda c: c.sightings, reverse=True)[:want]
    tids = set().union(*(c.track_ids for c in clusters)) if clusters else set()
    picks = choose(half, cam, dets, tids)
    tries = {id(c): shortlist([k for t in c.track_ids for k in picks.get(t, [])]) for c in clusters}
    t1 = time.time()
    cuts = read(video, [k for ks in tries.values() for k in ks])
    thumbs: dict[int, Thumb] = {}
    for c in clusters:
        k = best_of(tries[id(c)], cuts)
        data = encode(cuts[k][0]) if k is not None else None
        if data:
            thumbs[k.track_id] = Thumb(k.track_id, k.frame, k.score, data)
    attach_thumbs(report.clusters, thumbs)
    kept, dropped = fit_budget(report.clusters)
    return dict(
        asked=len(clusters),
        pictured=len(thumbs),
        frames=len({k.frame for k in cuts}),
        kept=kept,
        dropped=dropped,
        choose_s=round(t1 - t0, 1),
        read_s=round(time.time() - t1, 1),
    )
