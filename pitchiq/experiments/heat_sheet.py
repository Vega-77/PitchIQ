"""Picture of the team heatmaps: where each team's outfielders spent the half.

    python -m pitchiq.experiments.heat_sheet <tag> [<tag> ...] [--out sheet.jpg]

One row per half (from ../PitchIQ-data/stats/map/<tag>.npz), one pitch per
team, turned so the team attacks to the right. The pitch has its markings
(halfway line, centre circle, boxes, goals); the heat is the team's
outfielders' time on a 1 m grid, smoothed over a few metres. Hatched ground is
mostly guessed: the camera showed players there less than `SEEN_OK` of the
time, so the map placed them. Each team is named lighter or darker shirts,
with a swatch (see `kits`). For looking, not for scoring.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from pitchiq import map_stats as M
from pitchiq.experiments.wholemap import OUT as MAP
from pitchiq.pitch import Pitch

PX = 8  # pixels per metre
MARGIN = 24  # around the pitch, room for the goals
HEAD = 70  # title and thirds above the pitch
FOOT = 64  # sentences under it
BLUR_M = 3.0  # smoothing of the 1 m grid
SEEN_OK = 0.7  # below this seen share, the ground is hatched as mostly guessed
GRASS = (40, 95, 40)
LINE = (235, 235, 235)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _text(im, s, xy, scale=0.6, col=(20, 20, 20), thick=1):
    cv2.putText(im, s, xy, FONT, scale, col, thick, cv2.LINE_AA)


def kits(path) -> dict[int, tuple[tuple[int, int, int], str]]:
    """Each team's shirt swatch (BGR) and name ('lighter shirts' / 'darker shirts').

    From the median over windows of its players' kit Lab, after the window's team
    swap. That median is of whole player boxes, so shade, low sun and grass pull
    it dark (pi2's white team reads L 65 against 46); both swatches are brightened
    by one factor so the lighter reads light, and the names say only which is
    lighter, which the colour does support."""
    z = np.load(path)
    kit = np.where(z['swap'][:, None, None], z['kit_lab'][:, ::-1], z['kit_lab'])
    lab = {team: np.nanmedian(kit[:, team], axis=0) for team in M.TEAMS}
    gain = 220.0 / max(lab[0][0], lab[1][0], 1.0)
    out = {}
    for team in M.TEAMS:
        v = lab[team].copy()
        v[0] = min(255.0, v[0] * gain)
        px = np.clip(v, 0, 255).astype(np.uint8).reshape(1, 1, 3)
        bgr = tuple(int(c) for c in cv2.cvtColor(px, cv2.COLOR_LAB2BGR)[0, 0])
        light = lab[team][0] >= lab[1 - team][0]
        out[team] = (bgr, 'lighter shirts' if light else 'darker shirts')
    return out


def _markings(im, p: Pitch, o: tuple[int, int]):
    """White pitch lines, attacking right; o is the pixel of (x 0, y = width)."""

    def q(x, y):
        return int(o[0] + x * PX), int(o[1] + (p.width_m - y) * PX)

    L, W = p.length_m, p.width_m
    cv2.rectangle(im, q(0, 0), q(L, W), LINE, 2)
    cv2.line(im, q(L / 2, 0), q(L / 2, W), LINE, 2)
    cv2.circle(im, q(L / 2, W / 2), int(p.centre_circle_radius_m * PX), LINE, 2, cv2.LINE_AA)
    cv2.circle(im, q(L / 2, W / 2), 3, LINE, -1)
    for end in (0, 1):
        sx = (lambda x: x) if end == 0 else (lambda x: L - x)
        for ln, wd in (
            (p.penalty_area_length_m, p.penalty_area_width_m),
            (p.goal_area_length_m, p.goal_area_width_m),
        ):
            cv2.rectangle(im, q(sx(0), W / 2 - wd / 2), q(sx(ln), W / 2 + wd / 2), LINE, 2)
        cv2.circle(im, q(sx(p.penalty_spot_m), W / 2), 3, LINE, -1)
        g = p.goal_width_m / 2
        cv2.rectangle(im, q(sx(-2), W / 2 - g), q(sx(0), W / 2 + g), LINE, 2)


def _panel(t: M.MapTable, team: int, left: dict, height: float, kit, tag: str) -> np.ndarray:
    p = Pitch(length_m=t.length_m, width_m=t.width_m)
    L, W = t.length_m, t.width_m
    cols, rows = int(round(L)), int(round(W))
    h = M.heatmap(t, team, left=left, grid=(cols, rows))
    n = h.share * h.samples
    s = np.nan_to_num(h.seen) * n
    k = (0, 0)
    sig = BLUR_M
    nb = cv2.GaussianBlur(n.astype(np.float32), k, sig)
    sb = cv2.GaussianBlur(s.astype(np.float32), k, sig)
    heat = nb / max(float(nb.max()), 1e-9)
    seen = np.where(nb > 1e-6, sb / np.maximum(nb, 1e-9), 1.0)

    pw, ph = int(L * PX), int(W * PX)
    im = np.full((HEAD + ph + 2 * MARGIN + FOOT, pw + 2 * MARGIN, 3), 255, np.uint8)
    ox, oy = MARGIN, HEAD + MARGIN
    # heat: grid (cols along x, rows along y from the left wing) -> image (y down)
    hv = cv2.resize(heat.T[::-1], (pw, ph), interpolation=cv2.INTER_LINEAR)
    sv = cv2.resize(seen.T[::-1].astype(np.float32), (pw, ph), interpolation=cv2.INTER_LINEAR)
    cm = cv2.applyColorMap((255 * hv).astype(np.uint8), cv2.COLORMAP_INFERNO)
    a = np.clip(hv, 0, 1)[..., None] ** 0.6 * 0.9
    field = (np.array(GRASS, np.float32) * (1 - a) + cm.astype(np.float32) * a).astype(np.uint8)
    yy, xx = np.mgrid[0:ph, 0:pw]
    hatch = ((xx + yy) % 14 < 2) & (sv < SEEN_OK) & (hv > 0.08)
    field[hatch] = (200, 200, 200)
    im[oy : oy + ph, ox : ox + pw] = field
    _markings(im, p, (ox, oy))
    # thirds: dashed, with the team's time in each above the pitch
    th = M.heatmap(t, team, left=left).thirds()  # the 6 x 5 grid: thirds fall on its columns
    third = {
        'Own third': th['defensive'],
        'Middle third': th['middle'],
        'Final third': th['attacking'],
    }
    for kx in (1, 2):
        x = ox + int(kx * pw / 3)
        for y0 in range(oy, oy + ph, 12):
            cv2.line(im, (x, y0), (x, min(y0 + 6, oy + ph)), (200, 200, 200), 1)
    for i, (name, v) in enumerate(third.items()):
        _text(im, '%s %.0f%%' % (name, 100 * v), (ox + int(i * pw / 3) + 8, oy - 10), 0.6)
    # title with the shirt colour
    swatch, name = kit
    cv2.rectangle(im, (MARGIN, 12), (MARGIN + 26, 38), swatch, -1)
    cv2.rectangle(im, (MARGIN, 12), (MARGIN + 26, 38), (60, 60, 60), 1)
    _text(im, '%s, %s: attacking ->' % (tag, name), (MARGIN + 36, 32), 0.7, thick=2)
    y = oy + ph + MARGIN + 22
    _text(
        im,
        'Average outfield position: %.0f m from own goal (halfway line is %.0f m)'
        % (height, L / 2),
        (MARGIN, y),
        0.6,
    )
    _text(
        im,
        'Hatched: mostly guessed, the camera showed it under %.0f%% of the time' % (100 * SEEN_OK),
        (MARGIN, y + 26),
        0.55,
        (90, 90, 90),
    )
    return im


def sheet(tags: list[str]) -> np.ndarray:
    rows = []
    for tag in tags:
        path = MAP / f'{tag}.npz'
        t = M.MapTable.load(path)
        left = M.defends_left(t)
        ts = M.territory(t, left=left)
        k = kits(path)
        panels = [
            _panel(t, team, left, float(np.nanmean(ts.height[team])), k[team], tag)
            for team in M.TEAMS
        ]
        row = np.hstack(panels)
        play = M.third_shares(ts.play, t.length_m)  # team 0's view
        band = np.full((44, row.shape[1], 3), 255, np.uint8)
        _text(
            band,
            "%s, where the game was (centre of all players):   in the %s' final third %.0f%%"
            "   middle third %.0f%%   in the %s' final third %.0f%%"
            % (
                tag,
                k[0][1],
                100 * play['attacking'],
                100 * play['middle'],
                k[1][1],
                100 * play['defensive'],
            ),
            (MARGIN, 28),
            0.65,
            thick=2,
        )
        rows.append(np.vstack([band, row]))
    w = max(r.shape[1] for r in rows)  # pitches differ in size by venue
    pad = [np.pad(r, ((0, 20), (0, w - r.shape[1]), (0, 0)), constant_values=255) for r in rows]
    return np.vstack(pad)


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
