"""A short clip of a half's table (`pitchiq/map_table.py`), to check by eye that the players
and the ball line up.

    python -m pitchiq.experiments.table_clip <tag> <lo> <hi> [--step 2] [--out clip.webm]

Top: the video, with every dot of the table drawn back onto it through the half's camera
(metres -> pixels), in the team's kit colour (the map's kit_lab), keepers yellow, referees
pink: a filled dot at a detected player's feet, a ring where the map guesses,
an orange ring at the ball (thin where it is a TAPNext bridge, not a detector pick; none
where the 3D chain could not place the ring, which the caption calls unplaced).
Bottom: the same frame from above. Where the table is right, the dots sit on the feet and
the ball ring on the ball. Writes a VP80 webm (plays in a browser); default
PitchIQ-data/stats/table_clips/<tag>_<lo>.webm.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import cv2
import numpy as np

from pitchiq.balltrack import camera
from pitchiq.experiments.wholemap import video_of
from pitchiq.map_table import MAPS, HalfTable

OUT = Path(__file__).resolve().parents[2].parent / 'PitchIQ-data' / 'stats' / 'table_clips'
W = 960  # panel width
CAM_H = 540
MAP_PAD = 24
# BGR: keeper yellow, referee pink, anyone else and an unknown team grey; teams in their kits
KEEPER, REF, OTHER, BALL = (0, 220, 255), (200, 90, 255), (150, 150, 150), (0, 140, 255)
TEAM = {0: (60, 60, 235), 1: (235, 170, 50), 2: OTHER}  # until kit_colours replaces 0 and 1


def kit_colours(tag: str) -> dict:
    """Team -> BGR from the map's kit colours (OpenCV Lab, per window; the median)."""
    with np.load(MAPS / f'{tag}.npz') as m:
        lab = np.median(m['kit_lab'], axis=0)  # (2 teams, 3)
    bgr = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8)[None], cv2.COLOR_LAB2BGR)[0]
    return {0: tuple(int(v) for v in bgr[0]), 1: tuple(int(v) for v in bgr[1]), 2: OTHER}


def colour(team: int, role: int):
    return KEEPER if role == 1 else REF if role == 2 else OTHER if role == 3 else TEAM[int(team)]


def to_px(Hm_inv: np.ndarray, x, y) -> np.ndarray:
    """Metres -> full-size video pixels."""
    p = np.c_[np.atleast_1d(x), np.atleast_1d(y), np.ones(np.size(x))] @ Hm_inv.T
    return p[:, :2] / p[:, 2:3]


def map_panel(t: HalfTable, rows, ball, scale: float, size) -> np.ndarray:
    """The pitch from above (camera side at the bottom), with the frame's dots and ball."""
    im = np.full((size[1], size[0], 3), (40, 95, 40), np.uint8)

    def mm(x, y):
        return (int(MAP_PAD + x * scale), int(size[1] - MAP_PAD - y * scale))

    L, Wd = t.length_m, t.width_m
    cv2.rectangle(im, mm(0, 0), mm(L, Wd), (230, 230, 230), 1, cv2.LINE_AA)
    cv2.line(im, mm(L / 2, 0), mm(L / 2, Wd), (230, 230, 230), 1, cv2.LINE_AA)
    cv2.circle(im, mm(L / 2, Wd / 2), int(9.15 * scale), (230, 230, 230), 1, cv2.LINE_AA)
    for x, y, team, role, seen in zip(
        rows['x'], rows['y'], rows['team'], rows['role'], rows['seen']
    ):
        c = colour(team, role)
        cv2.circle(im, mm(x, y), 5, c, -1 if seen else 2, cv2.LINE_AA)
    bx, by, src = ball
    if src and np.isfinite(bx):
        cv2.circle(im, mm(bx, by), 4, BALL, -1, cv2.LINE_AA)
        cv2.circle(im, mm(bx, by), 7, (0, 0, 0), 1, cv2.LINE_AA)
    return im


def render(tag: str, lo: int, hi: int, step: int = 2, out: Path | None = None) -> Path:
    t = HalfTable.load(tag)
    TEAM.update(kit_colours(tag))
    camera.ensure(tag)
    cam = camera.Camera(tag)
    D = np.diag([t.length_m / 120.0, t.width_m / 80.0, 1.0])
    scale = (W - 2 * MAP_PAD) / t.length_m
    map_size = (W, int(t.width_m * scale + 2 * MAP_PAD))
    out = Path(out or OUT / f'{tag}_{lo}.webm')
    out.parent.mkdir(parents=True, exist_ok=True)
    video = video_of(tag)
    if video:
        os.environ['PIQ_VIDEO'] = video
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
    vw = cv2.VideoWriter(
        str(out), cv2.VideoWriter_fourcc(*'VP80'), 30.0 / step, (W, CAM_H + map_size[1])
    )
    for f in range(lo, hi):
        ok, frame = cap.read()
        if not ok:
            raise SystemExit(f'{video} ended at frame {f}')
        if (f - lo) % step:
            continue
        sx = W / frame.shape[1]
        top = cv2.resize(frame, (W, CAM_H), interpolation=cv2.INTER_AREA)
        Hinv = np.linalg.inv(D @ cam.at(f))
        rows = t.players_at(f)
        if len(rows['x']):
            px = to_px(Hinv, rows['x'], rows['y']) * sx
            for (u, v), team, role, seen in zip(px, rows['team'], rows['role'], rows['seen']):
                if -50 < u < W + 50 and -50 < v < CAM_H + 50:
                    c = colour(team, role)
                    cv2.circle(
                        top, (int(u), int(v)), 4 if seen else 6, c, -1 if seen else 1, cv2.LINE_AA
                    )
        ball = t.ball_at(f)
        placed = bool(ball[2]) and np.isfinite(ball[0])
        if placed:
            ((u, v),) = to_px(Hinv, ball[0], ball[1]) * sx
            cv2.circle(top, (int(u), int(v)), 9, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.circle(top, (int(u), int(v)), 9, BALL, 1 if ball[2] == 3 else 2, cv2.LINE_AA)
        cv2.putText(
            top,
            '%s  frame %d  %d dots  ball %s'
            % (
                tag,
                f,
                len(rows['x']),
                {0: 'none', 1: 'pick', 2: 'gap pick', 3: 'bridge'}[ball[2]]
                + ('' if placed or not ball[2] else ' (unplaced)'),
            ),
            (10, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        vw.write(np.vstack([top, map_panel(t, rows, ball, scale, map_size)]))
    cap.release()
    vw.release()
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog='python -m pitchiq.experiments.table_clip', description=__doc__.split('\n')[0]
    )
    ap.add_argument('tag')
    ap.add_argument('lo', type=int)
    ap.add_argument('hi', type=int)
    ap.add_argument('--step', type=int, default=2, help='draw every step-th frame (2: 15 fps)')
    ap.add_argument('--out')
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    print(render(a.tag, a.lo, a.hi, a.step, a.out))


if __name__ == '__main__':
    main()
