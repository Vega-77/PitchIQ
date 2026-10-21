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

The guess: a minute is "play" when its median count >= COUNT, spread >= SPREAD
and pan >= PAN; the halves are the two longest runs of play minutes (gaps of up
to GAP minutes bridged), in video order.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

from pitchiq.experiments.player_lab import PLAB, VIDEO

COUNT, SPREAD, PAN, GAP = 14, 0.45, 25.0, 2


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


def guess(z) -> tuple[list[tuple[float, float]], np.ndarray, np.ndarray]:
    """The two longest play runs as (start_s, end_s), and the per-minute play flags."""
    t = z['t']
    mins = np.arange(int(t.max() // 60) + 1)
    play = np.zeros(len(mins), bool)
    for i in mins:
        m = (t >= 60 * i) & (t < 60 * (i + 1))
        if m.any():
            pan = z['pan'][m]
            play[i] = (
                np.median(z['count'][m]) >= COUNT
                and np.median(z['spread'][m]) >= SPREAD
                and np.nanmedian(pan) >= PAN
                if np.isfinite(pan).any()
                else False
            )
    fill = play.copy()  # bridge short stoppages
    on = np.flatnonzero(play)
    for a, b in zip(on[:-1], on[1:]):
        if 1 < b - a <= GAP + 1:
            fill[a:b] = True
    runs, i = [], 0
    while i < len(fill):
        if fill[i]:
            j = i
            while j + 1 < len(fill) and fill[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    top = sorted(sorted(runs, key=lambda r: r[1] - r[0])[-2:])
    return [(60.0 * a, 60.0 * (b + 1)) for a, b in top], mins, play


def plot(tag: str, z, halves) -> Path:
    import matplotlib

    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    t = z['t'] / 60
    fig, ax = plt.subplots(3, 1, figsize=(14, 7), sharex=True)
    for a, k, thr in zip(ax, ('count', 'spread', 'pan'), (COUNT, SPREAD, PAN)):
        a.plot(t, z[k], lw=0.5)
        a.axhline(thr, color='r', lw=0.8)
        a.set_ylabel(k)
        for s, e in halves:
            a.axvspan(s / 60, e / 60, color='g', alpha=0.15)
    ax[2].set_yscale('symlog', linthresh=10)
    ax[2].set_xlabel('video minute')
    ax[0].set_title(
        f'{tag}: halves guessed ' + ', '.join(f'{s / 60:.0f}-{e / 60:.0f} min' for s, e in halves)
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
    halves, _, _ = guess(z)
    for k, (s, e) in enumerate(halves, 1):
        print(
            f'H{k}: {s / 60:.0f}:00 - {e / 60:.0f}:00  (frames {int(s * 30)}-{int(e * 30)} at 30 fps)'
        )
    print('plot:', plot(a.tag, z, halves))
    return 0


if __name__ == '__main__':
    os.environ.setdefault('OMP_NUM_THREADS', '4')
    sys.exit(main())
