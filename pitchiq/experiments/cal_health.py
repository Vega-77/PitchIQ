"""Calibration health through a registration: paint on the lines, far/near
person height and feet on the pitch, per stretch of the match.

    PIQ_VIDEO=<video> python -m pitchiq.experiments.cal_health <tag | reg.npz> [--every 60] [--window 5]

<tag> is a registration in pitchiq/balltrack/pitch_registration (`<tag>_reg.npz`, with
`<tag>_venue.npz` for the pitch size when there is one; a path to a reg file
looks for its venue file beside it). One frame every --every seconds is read;
each --window minutes gets a row. Set PIQ_LINES=white for white-lined fields,
as for autocal. See `pitchiq.checks` for what each number means.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from pitchiq.checks import cal_flags, far_near, feet_on_pitch, paint_on_lines
from pitchiq.experiments.autocal_run import M_PER_R
from pitchiq.experiments.player_lab import PLAB, VIDEO

HOMOG = PLAB.parent / 'pitch_registration'


def _files(tag: str):
    p = Path(tag)
    if p.suffix == '.npz':
        return p, p.with_name(p.name.replace('_reg.npz', '_venue.npz'))
    return HOMOG / f'{tag}_reg.npz', HOMOG / f'{tag}_venue.npz'


def pitch_size(venue: Path, default=(105.0, 68.0)):
    if not venue.exists():
        return default
    v = np.load(venue)
    return M_PER_R * float(v['L']), M_PER_R * float(v['W'])


def sample(tag: str, every_s: float = 60.0, fps: float = 30.0, video: str = VIDEO):
    """Rows (frame, H, paint pixels, people boxes) for one frame every every_s seconds."""
    import cv2
    from ultralytics import YOLO

    from pitchiq.autocal import paint_mask
    from pitchiq.detector import CLASS_PERSON, DEFAULT_WEIGHTS

    reg, _ = _files(tag)
    z = np.load(reg)
    idx = np.flatnonzero(z['ok'])
    step = max(
        1,
        int(
            round(every_s * fps / max(1, np.median(np.diff(z['abs'][idx])) if len(idx) > 1 else 1))
        ),
    )
    model = YOLO(str(DEFAULT_WEIGHTS))
    cap = cv2.VideoCapture(video)
    rows = []
    for i in idx[::step]:
        f = int(z['abs'][i])
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, img = cap.read()
        if not ok:
            continue
        pm = paint_mask(img)
        ys, xs = np.nonzero(pm)
        s = img.shape[1] / pm.shape[1]
        b = (
            model.predict(img, classes=[CLASS_PERSON], conf=0.4, imgsz=1280, verbose=False)[0]
            .boxes.xyxy.cpu()
            .numpy()
        )
        b = b[
            (b[:, 3] - b[:, 1] < 0.25 * img.shape[0]) & (b[:, 1] > 2) & (b[:, 3] < img.shape[0] - 2)
        ]  # whole people, not the camera's neighbours
        rows.append((f, z['Hsb'][i], np.c_[xs, ys] * s + s / 2 - 0.5, b))
    return rows


def report(rows, length: float, width: float, window_min: float = 5.0, fps: float = 30.0):
    """One dict per window of window_min minutes, and one for all rows."""

    def one(rs):
        H = np.array([r[1] for r in rs])
        feet = [np.c_[(r[3][:, 0] + r[3][:, 2]) / 2, r[3][:, 3]] for r in rs]
        return dict(
            frames=len(rs),
            first=int(rs[0][0]),
            last=int(rs[-1][0]),
            **paint_on_lines(H, [r[2] for r in rs], length, width),
            **far_near(H, [r[3] for r in rs], length, width),
            **feet_on_pitch(H, feet),
        )

    if not rows:
        return [], {}
    f0 = rows[0][0]
    win = int(window_min * 60 * fps)
    groups = {}
    for r in rows:
        groups.setdefault((r[0] - f0) // win, []).append(r)
    return [one(g) for _, g in sorted(groups.items())], one(rows)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='python -m pitchiq.experiments.cal_health', description=__doc__.split('\n\n')[0]
    )
    p.add_argument(
        'tag',
        help='registration tag in pitchiq/balltrack/pitch_registration, or a path to a _reg.npz',
    )
    p.add_argument('--every', type=float, default=60.0, help='seconds between sampled frames')
    p.add_argument('--window', type=float, default=5.0, help='minutes per row')
    p.add_argument(
        '--size',
        type=float,
        nargs=2,
        metavar=('L', 'W'),
        help='pitch metres (default: the venue file, else 105 x 68)',
    )
    p.add_argument('--json', type=Path, help='write the rows here too')
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    L, W = a.size or pitch_size(_files(a.tag)[1])
    rows = sample(a.tag, a.every)
    wins, total = report(rows, L, W, a.window)
    print('%s  pitch %.1f x %.1f m  %d frames' % (a.tag, L, W, len(rows)))
    print(
        '%8s %8s %6s  %8s %8s  %6s  %9s  %s'
        % ('from', 'to', 'n', 'far/near', 'height', 'feet', 'on_lines', 'flags')
    )
    for r in wins + [dict(total, first='all', last='')]:
        print(
            '%8s %8s %6d  %8.3f %8.2f  %6.2f  %9.3f  %s'
            % (
                r['first'],
                r['last'],
                r['frames'],
                r['ratio'],
                r['median_m'],
                r['on_pitch'],
                r['on_lines'],
                ', '.join(cal_flags(r)) or '-',
            )
        )
    if a.json:
        a.json.write_text(
            json.dumps(dict(tag=a.tag, L=L, W=W, windows=wins, total=total), indent=1)
        )
    return 0


if __name__ == '__main__':
    sys.exit(main())
