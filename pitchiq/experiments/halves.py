"""Where the halves are in a match video, from the video alone.

    PIQ_VIDEO=<video> python -m pitchiq.experiments.halves <tag> [--step 5]

Samples one frame every `step` seconds and records, per sample: how many people
the detector sees, how far apart they stand (x spread, as a share of the frame
width), and how far the camera moved since the previous sample (ORB + RANSAC
shift of the frame centre, px). In play, the auto-following camera pans and
twenty-odd players spread over the pitch; at half-time the pitch empties and
the camera stops. Warm-ups have the players but in clumps, with little panning.

Writes plab/out/<tag>_halves.npz (t, count, spread, pan) and
plab/out/<tag>_halves.png (the three signals over the video, with the guess).

The guess (`fit`): each sample's activity is the least of three shares over the
minute around it: samples with at least 3/4 of the game's busy head count, samples
spread over SPREAD of the frame, and samples where the camera panned PAN px. A half
starts only where activity rises through LEVEL after a quiet minute (a kickoff
follows the lineup and the huddles) and ends only where it falls into one. Of
those edges, the two halves of HALF_MIN minutes with a break of BREAK_MIN that hold
the most activity above THETA are the game. Warm-ups, ceremonies and overtime fall
outside: on the 7 games with known halves (2026-11-21) every edge was within 2.6
min, and 77% within a minute (the old to-the-minute runs were 8-14 min off on 3).
`problems` says when a fit looks unlike a game.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

from pitchiq.experiments.player_lab import PLAB, VIDEO

SPREAD, PAN = 0.45, 25.0
BUSY, WIN_S = 0.75, 60.0  # a busy sample has BUSY of the game's 75th-percentile head count
THETA, LEVEL, QUIET_S = 0.3, 0.4, 60.0
HALF_MIN, BREAK_MIN = (40.0, 52.0), (4.0, 25.0)
MIN_PLAY, MAX_BREAK = (
    0.4,
    0.35,
)  # mean activity a fitted half must reach (night games: 0.5), a break stay under (seen: 0.16)


def sample(tag: str, step: float) -> Path:
    import cv2

    from pitchiq.detector import CLASS_PERSON, DEFAULT_WEIGHTS
    from ultralytics import YOLO

    out = PLAB / 'out' / f'{tag}_halves.npz'
    cap = cv2.VideoCapture(VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    model = YOLO(str(DEFAULT_WEIGHTS))
    orb = cv2.ORB_create(1500)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    T, C, S, P = [], [], [], []
    prev = None
    t0 = time.time()
    for f in range(0, n, int(round(step * fps))):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, img = cap.read()
        if not ok:
            break
        r = model.predict(img, classes=[CLASS_PERSON], conf=0.25, imgsz=1280, verbose=False)[0]
        b = r.boxes.xyxy.cpu().numpy()
        h = b[:, 3] - b[:, 1] if len(b) else np.zeros(0)
        b = b[h < 0.25 * img.shape[0]]  # the people on the pitch, not the camera's neighbours
        cx = (b[:, 0] + b[:, 2]) / 2 / img.shape[1]
        T.append(f / fps)
        C.append(len(b))
        S.append(float(np.percentile(cx, 90) - np.percentile(cx, 10)) if len(b) >= 4 else 0.0)
        g = cv2.cvtColor(cv2.resize(img, (960, 540)), cv2.COLOR_BGR2GRAY)
        kd = orb.detectAndCompute(g, None)
        pan = np.nan
        if prev is not None and prev[1] is not None and kd[1] is not None:
            m = bf.match(prev[1], kd[1])
            if len(m) >= 12:
                a = np.float32([prev[0][x.queryIdx].pt for x in m])
                c = np.float32([kd[0][x.trainIdx].pt for x in m])
                H, inl = cv2.estimateAffinePartial2D(
                    a, c, method=cv2.RANSAC, ransacReprojThreshold=4.0
                )
                if H is not None and inl.sum() >= 10:
                    p = H.dot([480.0, 270.0, 1.0])
                    pan = 2.0 * float(np.hypot(p[0] - 480.0, p[1] - 270.0))  # in full-size px
        P.append(pan)
        prev = kd
        if len(T) % 100 == 0:
            print(f'{T[-1] / 60:5.1f} min  {len(T)} samples  {time.time() - t0:.0f} s', flush=True)
    np.savez(
        out,
        t=np.array(T),
        count=np.array(C),
        spread=np.array(S),
        pan=np.array(P),
        step=step,
        video=VIDEO,
    )
    return out


def activity(z) -> tuple[np.ndarray, np.ndarray]:
    """Per sample: time (s) and activity (0..1), the least of the three busy shares."""
    t, c = z['t'], z['count'].astype(float)
    dt = float(np.median(np.diff(t)))
    w = max(1, int(round(WIN_S / dt)))
    k = np.ones(w) / w

    def share(x):
        return np.convolve(x.astype(float), k, mode='same')

    busy = share(c >= BUSY * np.percentile(c, 75))
    spread = share(z['spread'] >= SPREAD)
    pan = share(np.nan_to_num(z['pan']) > PAN)
    return t, np.minimum(np.minimum(busy, spread), pan)


def _edges(r: np.ndarray, quiet: int) -> tuple[list[int], list[int]]:
    """Sample indices where activity rises through LEVEL after `quiet` samples below
    it (starts), and where it falls through it into `quiet` below (ends)."""
    low = r < LEVEL
    n = len(r)
    starts = [
        i for i in range(1, n) if low[i - 1] and not low[i] and low[max(0, i - quiet) : i].all()
    ]
    ends = [i for i in range(1, n) if not low[i - 1] and low[i] and low[i : i + quiet].all()]
    return [0, *starts], [*ends, n]


def fit(z) -> tuple[list[tuple[float, float]], dict]:
    """The two halves as (start_s, end_s) on the video clock, and how sure: the mean
    activity in each half and in the break. ([], {}) when no pair of edges fits."""
    t, r = activity(z)
    n = len(r)
    dt = float(np.median(np.diff(t)))
    m = 60.0 / dt  # samples a minute
    S, E = _edges(r, int(round(QUIET_S / dt)))
    cs = np.concatenate([[0.0], np.cumsum(r - THETA)])
    halves = [(s, e) for s in S for e in E if HALF_MIN[0] * m <= e - s <= HALF_MIN[1] * m]
    best, pick = -np.inf, None
    for s1, e1 in halves:
        for s2, e2 in halves:
            if BREAK_MIN[0] * m <= s2 - e1 <= BREAK_MIN[1] * m:
                v = cs[e1] - cs[s1] + cs[e2] - cs[s2]
                if v > best:
                    best, pick = v, (s1, e1, s2, e2)
    if pick is None:
        return [], {}
    s1, e1, s2, e2 = pick

    def at(i):
        return float(t[i]) if i < n else float(t[-1] + dt)

    info = dict(
        play=(float(r[s1:e1].mean()), float(r[s2:e2].mean())),
        brk=float(r[e1:s2].mean()),
    )
    return [(at(s1), at(e1)), (at(s2), at(e2))], info


def problems(halves, info) -> list[str]:
    """Why a fit does not look like a game: [] when it does."""
    if len(halves) < 2:
        return [
            f'no two halves of {HALF_MIN[0]:.0f}-{HALF_MIN[1]:.0f} min with a '
            f'{BREAK_MIN[0]:.0f}-{BREAK_MIN[1]:.0f} min break between them'
        ]
    out = [
        f'half {k} is quiet for a half (activity {p:.2f}, under {MIN_PLAY})'
        for k, p in enumerate(info['play'], 1)
        if p < MIN_PLAY
    ]
    if info['brk'] > MAX_BREAK:
        out.append(f'the break is busy for a break (activity {info["brk"]:.2f}, over {MAX_BREAK})')
    return out


def plot(tag: str, z, halves) -> Path:
    import matplotlib

    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    t = z['t'] / 60
    busy = BUSY * np.percentile(z['count'], 75)
    fig, ax = plt.subplots(4, 1, figsize=(14, 9), sharex=True)
    for a, k, thr in zip(ax, ('count', 'spread', 'pan'), (busy, SPREAD, PAN)):
        a.plot(t, z[k], lw=0.5)
        a.axhline(thr, color='r', lw=0.8)
        a.set_ylabel(k)
    _, act = activity(z)
    ax[3].plot(t, act, lw=0.8)
    ax[3].axhline(LEVEL, color='r', lw=0.8)
    ax[3].set_ylabel('activity')
    for a in ax:
        for s, e in halves:
            a.axvspan(s / 60, e / 60, color='g', alpha=0.15)
    ax[2].set_yscale('symlog', linthresh=10)
    ax[3].set_xlabel('video minute')
    ax[0].set_title(
        f'{tag}: halves fitted ' + ', '.join(f'{s / 60:.1f}-{e / 60:.1f} min' for s, e in halves)
    )
    out = PLAB / 'out' / f'{tag}_halves.png'
    fig.tight_layout()
    fig.savefig(out, dpi=90)
    plt.close(fig)
    return out


def strip(tag: str, m0: float, m1: float, every: float) -> Path:
    """Thumbnails from video minute m0 to m1, one every `every` seconds, captioned
    with the video clock: for placing a kickoff or a final whistle by eye."""
    import cv2

    cap = cv2.VideoCapture(VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS)
    tiles = []
    for s in np.arange(60 * m0, 60 * m1 + 1e-6, every):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(s * fps)))
        ok, img = cap.read()
        if not ok:
            break
        im = cv2.resize(img, (384, 216))
        cv2.putText(
            im,
            f'{int(s // 60)}:{int(s % 60):02d}',
            (6, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 255),
            2,
        )
        tiles.append(im)
    cols = 6
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    sheet = np.vstack([np.hstack(tiles[i : i + cols]) for i in range(0, len(tiles), cols)])
    out = PLAB / 'out' / f'{tag}_strip_{m0:g}_{m1:g}.jpg'
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog='python -m pitchiq.experiments.halves', description=__doc__.split('\n\n')[0]
    )
    ap.add_argument('tag')
    ap.add_argument('--step', type=float, default=5.0, help='seconds between samples')
    ap.add_argument(
        '--strip',
        type=float,
        nargs=2,
        metavar=('MIN0', 'MIN1'),
        help='write a thumbnail sheet of these video minutes instead',
    )
    ap.add_argument('--every', type=float, default=10.0, help='seconds between strip thumbnails')
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.strip:
        print('strip:', strip(a.tag, a.strip[0], a.strip[1], a.every))
        return 0
    path = PLAB / 'out' / f'{a.tag}_halves.npz'
    if not path.exists():
        sample(a.tag, a.step)
    z = np.load(path)
    if str(z['video']) != VIDEO:
        raise SystemExit(f'{path} was sampled from {z["video"]}, not {VIDEO}')
    halves, info = fit(z)
    for k, (s, e) in enumerate(halves, 1):
        print(
            f'H{k}: {int(s // 60)}:{int(s % 60):02d} - {int(e // 60)}:{int(e % 60):02d}'
            f'  (frames {int(s * 30)}-{int(e * 30)} at 30 fps)'
        )
    for p in problems(halves, info):
        print('PROBLEM:', p)
    print('plot:', plot(a.tag, z, halves))
    return 0


if __name__ == '__main__':
    os.environ.setdefault('OMP_NUM_THREADS', '4')
    sys.exit(main())
