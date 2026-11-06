"""Picture check for the team shape: the camera frame beside the map, with the shape drawn.

    python -m pitchiq.experiments.shape_sheet <tag> [--n 6] [--seed 0] [--out sheet.jpg]

For `n` random instants of the half (from ../PitchIQ-data/stats/map/<tag>.npz),
one row each: the video frame, and the map with each team's dots (filled =
seen, hollow = guessed, grey = unknown team, gold = keeper), its back line
(dashed, across the pitch), and its width and depth (the box around its
outfielders). Numbers per team under the map. For looking, not for scoring.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from pitchiq import map_stats as M
from pitchiq.experiments.wholemap import OUT as MAP, video_of

PX = 6  # map pixels per metre
PAD = 20
COL = {0: (60, 60, 235), 1: (235, 170, 50), 2: (170, 170, 170)}
GOLD = (0, 215, 255)


def _map_panel(t: M.MapTable, f: int, sh: dict, left: dict) -> np.ndarray:
    W, H = int(t.length_m * PX) + 2 * PAD, int(t.width_m * PX) + 2 * PAD + 44
    im = np.full((H, W, 3), (40, 110, 40), np.uint8)

    def p(x, y):
        return int(PAD + x * PX), int(PAD + (t.width_m - y) * PX)

    cv2.rectangle(im, p(0, 0), p(t.length_m, t.width_m), (230, 230, 230), 1)
    cv2.line(im, p(t.length_m / 2, 0), p(t.length_m / 2, t.width_m), (230, 230, 230), 1)
    m = t.f == f
    for x, y, seen, team, role, a in zip(
        t.x[m], t.y[m], t.seen[m], t.team[m], t.role[m], t.alpha[m]
    ):
        if role == 2 or a <= 0:
            continue
        c = GOLD if role == M.KEEPER else COL[int(team)]
        cv2.circle(im, p(x, y), 5, c, -1 if seen else 2, cv2.LINE_AA)
    for team in M.TEAMS:
        r = m & (t.team == team) & (t.role == M.OUTFIELD) & (t.alpha.astype(np.float32) > 0)
        if r.sum() < M.MIN_SHAPE:
            continue
        xs, ys = t.x[r], t.y[r]
        cv2.rectangle(im, p(xs.min(), ys.min()), p(xs.max(), ys.max()), COL[team], 1)
        ln = sh[team]['line']
        if np.isfinite(ln):
            lx = ln if left[team] else t.length_m - ln
            for y0 in np.arange(0, t.width_m, 3):
                cv2.line(im, p(lx, y0), p(lx, min(y0 + 1.5, t.width_m)), COL[team], 2)
    y = H - 30
    for team in M.TEAMS:
        s = sh[team]
        txt = 'team %d: n %d (%d seen)  width %.0f  depth %.0f  compact %.0f  line %s' % (
            team,
            s['n'],
            s['n_seen'],
            s['width'],
            s['depth'],
            s['compact'],
            '%.0f' % s['line'] if np.isfinite(s['line']) else '-',
        )
        cv2.putText(im, txt, (PAD, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, COL[team], 1, cv2.LINE_AA)
        y += 18
    return im


def sheet(tag: str, n: int = 6, seed: int = 0, at=None) -> np.ndarray:
    """`at`: these frames instead of `n` random ones."""
    t = M.MapTable.load(MAP / f'{tag}.npz')
    left = M.defends_left(t)
    rng = np.random.default_rng(seed)
    frames = np.unique(t.f)
    picks = (
        np.asarray(at)
        if at
        else np.sort(rng.choice(frames[(frames - frames[0]) % 15 == 0], n, replace=False))
    )
    cap = cv2.VideoCapture(video_of(tag.split('_')[0]))  # eb1_82200_98400: a stretch of eb1
    rows = []
    for f in picks:
        one = M.shape(t.subset(t.f == f), step=1, left=left)
        sh = {
            team: dict(
                n=int(s.n[0]),
                n_seen=int(s.n_seen[0]),
                width=s.width[0],
                depth=s.depth[0],
                compact=s.compact[0],
                line=s.line[0],
            )
            for team, s in one.items()
        }
        mp = _map_panel(t, int(f), sh, left)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(f))
        ok, fr = cap.read()
        h = mp.shape[0]
        fr = (
            cv2.resize(fr, (int(h * 16 / 9), h))
            if ok
            else np.zeros((h, int(h * 16 / 9), 3), np.uint8)
        )
        cv2.putText(
            fr,
            '%s frame %d (%.1f min in)' % (tag, f, (f - frames[0]) / t.fps / 60),
            (10, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        rows.append(np.hstack([fr, mp]))
    return np.vstack(rows)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog='python -m pitchiq.experiments.shape_sheet', description=__doc__.split('\n')[0]
    )
    ap.add_argument('tag')
    ap.add_argument('--n', type=int, default=6)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--at', type=int, nargs='+', help='these frames instead of --n random ones')
    ap.add_argument('--out')
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    out = a.out or str(MAP.parent / 'sheets' / f'shape_{a.tag}.jpg')
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(out, sheet(a.tag, a.n, a.seed, a.at), [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(out)


if __name__ == '__main__':
    main()
