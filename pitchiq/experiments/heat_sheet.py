"""Picture of the team heatmaps: each team of each half on its own pitch, attacking right.

    python -m pitchiq.experiments.heat_sheet <tag> [<tag> ...] [--out sheet.jpg]

One row per half (from ../PitchIQ-data/stats/map/<tag>.npz), two pitches per
row. Each of the 6 x 5 zones is shaded by the team's share of outfielder time
and labelled with it; the small figure under it is the seen part (the rest is
where the map guessed unseen players were). Under each pitch: the team's height
and its time in its defensive / middle / attacking third. For looking, not
for scoring.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from pitchiq import map_stats as M
from pitchiq.experiments.wholemap import OUT as MAP

PX = 6  # pixels per metre
PAD = 16
TEXT_H = 40


def _pitch(t: M.MapTable, h: M.Heatmap, height: float, title: str) -> np.ndarray:
    L, W = t.length_m, t.width_m
    w, hh = int(L * PX), int(W * PX)
    im = np.full((hh + 2 * PAD + TEXT_H + 20, w + 2 * PAD, 3), 255, np.uint8)
    cols, rows = h.share.shape
    top = h.share.max() or 1.0
    for c in range(cols):
        for r in range(rows):
            x0, x1 = PAD + int(c * w / cols), PAD + int((c + 1) * w / cols)
            # row 0 is the team's left wing: drawn at the bottom when it attacks right
            y1 = PAD + 20 + hh - int(r * hh / rows)
            y0 = PAD + 20 + hh - int((r + 1) * hh / rows)
            v = h.share[c, r] / top
            col = (int(255 * (1 - v)), int(255 * (1 - 0.6 * v)), 255)  # white -> red (BGR)
            cv2.rectangle(im, (x0, y0), (x1, y1), col, -1)
            cv2.putText(
                im,
                '%.0f%%' % (100 * h.share[c, r]),
                (x0 + 8, (y0 + y1) // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (30, 30, 30),
                1,
                cv2.LINE_AA,
            )
            s = h.seen[c, r]
            cv2.putText(
                im,
                'seen %s' % ('-' if np.isnan(s) else '%.0f' % (100 * s)),
                (x0 + 8, (y0 + y1) // 2 + 16),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.33,
                (90, 90, 90),
                1,
                cv2.LINE_AA,
            )
    g = (PAD, PAD + 20)
    cv2.rectangle(im, g, (PAD + w, PAD + 20 + hh), (60, 60, 60), 1)
    for k in (1, 2):
        x = PAD + int(k * w / 3)
        cv2.line(im, (x, PAD + 20), (x, PAD + 20 + hh), (60, 60, 60), 1)
    cv2.putText(
        im,
        title + '   attacking ->',
        (PAD, PAD + 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (0, 0, 0),
        1,
        cv2.LINE_AA,
    )
    th = h.thirds()
    txt = 'height %.0f m   thirds: own %.0f%%  middle %.0f%%  attacking %.0f%%' % (
        height,
        100 * th['defensive'],
        100 * th['middle'],
        100 * th['attacking'],
    )
    cv2.putText(
        im, txt, (PAD, PAD + 20 + hh + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA
    )
    return im


def sheet(tags: list[str]) -> np.ndarray:
    rows = []
    for tag in tags:
        t = M.MapTable.load(MAP / f'{tag}.npz')
        left = M.defends_left(t)
        ts = M.territory(t, left=left)
        play = M.third_shares(ts.play, t.length_m)
        panels = []
        for team in M.TEAMS:
            h = M.heatmap(t, team, left=left)
            title = '%s team %d' % (tag, team)
            if team == 0:
                title += '  (play in its att. third %.0f%%, own third %.0f%%)' % (
                    100 * play['attacking'],
                    100 * play['defensive'],
                )
            panels.append(_pitch(t, h, float(np.nanmean(ts.height[team])), title))
        rows.append(np.hstack(panels))
    w = max(r.shape[1] for r in rows)  # pitches differ in size by venue
    return np.vstack(
        [np.pad(r, ((0, 0), (0, w - r.shape[1]), (0, 0)), constant_values=255) for r in rows]
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog='python -m pitchiq.experiments.heat_sheet', description=__doc__.split('\n')[0]
    )
    ap.add_argument('tags', nargs='+')
    ap.add_argument('--out')
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    out = a.out or str(MAP.parent / 'sheets' / ('heat_%s.jpg' % '_'.join(a.tags)))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(out, sheet(a.tags), [cv2.IMWRITE_JPEG_QUALITY, 88])
    print(out)


if __name__ == '__main__':
    main()
