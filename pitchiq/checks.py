"""Automatic checks: signs that a calibration or a top-down map is wrong, with no labels.

Each check returns plain numbers, so a run can be judged per game and per
stretch and the numbers compared run to run. None of them proves the map is
right; each one catches a kind of wrong that has happened.

Calibration
  * `feet_on_pitch`: the share of people's feet that the calibration puts on
    the pitch. People stand on it; a far touchline posed in mid-pitch throws
    the far half's players off it (East Brunswick eb02: 0.16 against 0.85-0.94
    on NB and WW). It does not catch a pitch drawn too big (eb01: 1.00 with the
    far line on the running track); `cal_overlay` by eye still does.
  * `far_near`: how tall the calibration makes far people against near ones,
    and the median height. Validated on EB frames where the answer is known:
    right calibrations (ebh1, ebh2, ww01, nbh1) give 0.94-1.02 and 1.72-1.79 m;
    eb01 (pitch too big) 0.67 and 1.44 m, eb02 (far line mid-pitch) 2.20 and
    2.82 m. Through EB's first half every 5-minute window stays 0.96-1.05;
    through WW's 0.91-1.14, with the height sliding 1.79 to 1.64 m (slight drift?).
    `cal_flags` turns it into a verdict.
  * `paint_on_lines`: the share of painted-line pixels within 1.5 m of the
    model's lines. It FAILED validation: eb01 scored 0.745 against ebh1's
    0.688 (a wrong pose still fits the paint it was fitted to), and through a
    half it swings 0.32-0.77 with where the camera points. Kept for looking
    at, never as a flag.

Tried and dropped
  * Kit mix inside one identity (the linker fixes an identity's team, so a
    "team flip" shows as its detections voting both kits). On the fm01/fr00
    labels it flags clean identities as often as merged ones (fm01, mix > 0.2:
    1 of 10 merges, 9 of 30 clean), so it is not a check.

Map physics (on a `topdown.TopDown`, positions in metres)
  * `speeds`: steps faster than a person runs, and one-frame jumps.
  * `crowding`: frames with more than eleven of a team on the pitch.
  * `still_outside`: players who stand outside the lines.
"""

from __future__ import annotations

import numpy as np

RUN_MS = 10.0  # faster than any player sprints, m/s
JUMP_M = 1.0  # one frame at 30 fps: 30 m/s
MARGIN_SB = 3.0  # StatsBomb units of slack around the lines for feet
OUT_M = 2.0  # this far beyond a line is outside it
STILL_MS = 0.5  # slower than this is standing
PAINT_TOL_M = 1.5  # paint this close to a model line is on it
FAR_NEAR_OK = (0.85, 1.15)  # far/near person height of a right calibration (validated 0.94-1.02)
HEIGHT_OK = (1.55, 1.95)  # median person height, m (validated 1.72-1.79)
MIN_PEOPLE = 30  # fewer and far_near is noise


def feet_on_pitch(Hsb: np.ndarray, feet_px: list, margin: float = MARGIN_SB) -> dict:
    """Hsb: (n, 3, 3) px -> StatsBomb 120 x 80 homographies; feet_px: n arrays of
    (k, 2) foot pixels (a box's bottom centre). Feet that map behind the camera
    count as off the pitch."""
    on = n = 0
    for H, p in zip(Hsb, feet_px):
        p = np.asarray(p, float).reshape(-1, 2)
        if not len(p):
            continue
        q = np.c_[p, np.ones(len(p))] @ np.asarray(H, float).T
        front = np.sign(q[:, 2]) == np.sign(np.median(q[:, 2]))  # H's overall sign is arbitrary
        xy = q[:, :2] / q[:, 2:3]
        inside = (
            front
            & (xy[:, 0] > -margin)
            & (xy[:, 0] < 120 + margin)
            & (xy[:, 1] > -margin)
            & (xy[:, 1] < 80 + margin)
        )
        on += int(inside.sum())
        n += len(p)
    return dict(feet=n, on_pitch=on / n if n else float('nan'))


def _to_metres(H: np.ndarray, px: np.ndarray, length: float, width: float):
    """Pixels through a px -> StatsBomb homography to metres on an L x W pitch, and
    which of them land in front of the camera (H's overall sign is arbitrary, so the
    majority side is taken as the front)."""
    q = np.c_[px, np.ones(len(px))] @ np.asarray(H, float).T
    front = np.sign(q[:, 2]) == np.sign(np.median(q[:, 2]))
    with np.errstate(divide='ignore', invalid='ignore'):
        xy = q[:, :2] / q[:, 2:3]
    return xy * [length / 120.0, width / 80.0], front


_DIST = {}


def _line_distance(length: float, width: float, res: float = 0.1, pad: float = 15.0):
    """Distance in metres to the nearest painted line, on a res-metre grid over the
    pitch and pad metres around it."""
    key = (round(length, 2), round(width, 2), res, pad)
    if key not in _DIST:
        import cv2

        from pitchiq.experiments.cal_overlay import _lines

        g = np.full(
            (int((width + 2 * pad) / res) + 1, int((length + 2 * pad) / res) + 1), 255, np.uint8
        )
        for line in _lines(length, width):
            m = line * [length / 120.0, width / 80.0]
            cv2.polylines(g, [np.round((m + pad) / res).astype(np.int32)], False, 0, 1)
        _DIST[key] = (cv2.distanceTransform(g, cv2.DIST_L2, 5) * res, pad, res)
    return _DIST[key]


def paint_on_lines(
    Hsb: np.ndarray, paint_px: list, length: float, width: float, tol: float = PAINT_TOL_M
) -> dict:
    """The share of painted-line pixels the calibration puts within tol metres of a
    pitch line. Hsb: (n, 3, 3) px -> StatsBomb; paint_px: n arrays of (k, 2)
    full-resolution pixels (autocal.paint_mask's, scaled up). Paint that maps
    behind the camera or far off the pitch counts as off the lines. Other sports'
    lines and the penalty arcs, which the model does not draw, hold it below 1 on
    every venue; what matters is how it moves over a half and between calibrations."""
    d, pad, res = _line_distance(length, width)
    near = n = 0
    for H, p in zip(Hsb, paint_px):
        p = np.asarray(p, float).reshape(-1, 2)
        if not len(p):
            continue
        m, front = _to_metres(H, p, length, width)
        ij = np.round((m + pad) / res)
        ok = (
            front
            & np.isfinite(ij).all(1)
            & (ij[:, 0] >= 0)
            & (ij[:, 0] < d.shape[1])
            & (ij[:, 1] >= 0)
            & (ij[:, 1] < d.shape[0])
        )
        ij = ij[ok].astype(int)
        near += int((d[ij[:, 1], ij[:, 0]] <= tol).sum())
        n += len(p)
    return dict(paint=n, on_lines=near / n if n else float('nan'))


def person_heights(H: np.ndarray, boxes: np.ndarray, length: float, width: float) -> np.ndarray:
    """How tall the calibration makes each person, in metres: the ground distance
    across the feet spanned by a level segment as long as the box is tall. A
    pinhole camera shrinks a standing person and the ground beside them alike, so
    a right calibration gives about a person's height (less a little for the
    camera's tilt) near and far. NaN for feet that map behind the camera."""
    b = np.asarray(boxes, float).reshape(-1, 4)
    u, v, h = (b[:, 0] + b[:, 2]) / 2, b[:, 3], b[:, 3] - b[:, 1]
    m, front = _to_metres(H, np.r_[np.c_[u - h / 2, v], np.c_[u + h / 2, v]], length, width)
    k = len(b)
    est = np.linalg.norm(m[:k] - m[k:], axis=1)
    est[~(front[:k] & front[k:])] = np.nan
    return est


def far_near(Hsb: np.ndarray, boxes: list, length: float, width: float) -> dict:
    """Median person height (`person_heights`) in the farthest quarter of the
    frame's people (feet highest in the image) over that in the nearest quarter.
    About 1 when the calibration's depth is right; a pitch drawn too deep makes
    far people short (below 1), too shallow makes them tall. boxes: n arrays of
    (k, 4) x0 y0 x1 y1, whole people on the pitch only."""
    est, rows = [], []
    for H, b in zip(Hsb, boxes):
        b = np.asarray(b, float).reshape(-1, 4)
        if len(b):
            est.append(person_heights(H, b, length, width))
            rows.append(b[:, 3])
    if not est:
        return dict(people=0, ratio=float('nan'), median_m=float('nan'))
    e, r = np.concatenate(est), np.concatenate(rows)
    ok = np.isfinite(e) & (e > 0.3) & (e < 6.0)
    e, r = e[ok], r[ok]
    if len(e) < 8:
        return dict(
            people=int(len(e)),
            ratio=float('nan'),
            median_m=float(np.median(e)) if len(e) else float('nan'),
        )
    q1, q3 = np.percentile(r, [25, 75])
    return dict(
        people=int(len(e)),
        ratio=float(np.median(e[r <= q1]) / np.median(e[r >= q3])),
        median_m=float(np.median(e)),
    )


def _known(td, min_alpha: float = 0.02) -> np.ndarray:
    """(tracks, frames) where the map draws the track: seen, or guessed at least
    min_alpha strong (topdown draws guesses from 0.02; inf: seen only)."""
    seen = td.owner >= 0 if td.owner is not None else np.zeros(td.path.shape[:2], bool)
    alpha = td.alpha if td.alpha is not None else np.zeros(td.path.shape[:2])
    return seen | (alpha > min_alpha)


def speeds(td, fps: float = 30.0) -> dict:
    """Steps between consecutive drawn frames of a track: the share faster than
    RUN_MS, the number of one-frame jumps over JUMP_M, and the worst speed."""
    xy, k = td.path, _known(td)
    step = np.linalg.norm(np.diff(xy, axis=1), axis=2)
    both = k[:, 1:] & k[:, :-1] & np.isfinite(step)
    v = step[both] * fps
    return dict(
        steps=int(both.sum()),
        fast=float((v > RUN_MS).mean()) if len(v) else 0.0,
        jumps=int((step[both] > JUMP_M).sum()),
        worst_ms=float(v.max()) if len(v) else 0.0,
    )


def _on_pitch(xy: np.ndarray, length: float, width: float, out: float = OUT_M) -> np.ndarray:
    return (
        (xy[..., 0] > -out)
        & (xy[..., 0] < length + out)
        & (xy[..., 1] > -out)
        & (xy[..., 1] < width + out)
    )


def crowding(
    td, length: float, width: float, roles=('player', 'keeper'), min_alpha: float = 0.02
) -> dict:
    """Frames where a team has more than eleven drawn on the pitch, and the most at once.
    min_alpha: the faintest guess counted (inf: seen boxes only)."""
    if td.dots:  # what the map draws, after its own team cap
        return _crowding_dots(td, length, width, roles, min_alpha)
    k = _known(td, min_alpha) & _on_pitch(td.path, length, width)
    most = {}
    teams = sorted({g[0] for g in td.group if g and g[1] in roles and g[0] is not None})
    T = td.path.shape[1]
    worst = np.zeros(T, int)
    for t in teams:
        rows = [i for i, g in enumerate(td.group) if g and g[0] == t and g[1] in roles]
        c = k[rows].sum(0) if rows else np.zeros(T, int)
        most[t] = int(c.max()) if T else 0
        worst = np.maximum(worst, c)
    over = int((worst > 11).sum())
    return dict(frames=T, over11=over, most=most)


def _crowding_dots(td, length, width, roles, min_alpha) -> dict:
    count: dict = {}
    for t, dots in enumerate(td.dots):
        for d in dots:
            if (
                d.group[1] in roles
                and (d.seen or d.alpha > min_alpha)
                and _on_pitch(np.array([d.x, d.y]), length, width)
                and d.drawn_team in ('team_a', 'team_b')
            ):
                count.setdefault(d.drawn_team, np.zeros(len(td.dots), int))[t] += 1
    worst = np.max(list(count.values()), axis=0) if count else np.zeros(len(td.dots), int)
    return dict(
        frames=len(td.dots),
        over11=int((worst > 11).sum()),
        most={k: int(v.max()) for k, v in count.items()},
    )


def still_outside(
    td,
    length: float,
    width: float,
    fps: float = 30.0,
    min_s: float = 2.0,
    roles=('player', 'keeper'),
) -> dict:
    """Players seen standing outside the lines for min_s or more at a stretch:
    usually a sub or a coach read as a player, or a calibration off at the edge."""
    seen = td.owner >= 0
    out = seen & ~_on_pitch(td.path, length, width)
    sp = np.r_[
        '1', np.zeros((len(td.path), 1)), np.linalg.norm(np.diff(td.path, axis=1), axis=2) * fps
    ]
    bad = out & (sp < STILL_MS)
    need = int(round(min_s * fps))
    tracks = []
    for i, g in enumerate(td.group):
        if not g or g[1] not in roles:
            continue
        run = best = 0
        for b in bad[i]:
            run = run + 1 if b else 0
            best = max(best, run)
        if best >= need:
            tracks.append(i)
    return dict(tracks=tracks, n=len(tracks))


def map_report(td, length: float, width: float, fps: float = 30.0) -> dict:
    return dict(
        speeds=speeds(td, fps),
        crowding=crowding(td, length, width),
        crowding_seen=crowding(td, length, width, min_alpha=np.inf),
        still_outside=still_outside(td, length, width, fps),
    )


def cal_flags(r: dict) -> list[str]:
    """Why a `far_near` result says the calibration is wrong; empty when it looks right
    or has too few people to say."""
    if r.get('people', 0) < MIN_PEOPLE:
        return []
    out = []
    if not FAR_NEAR_OK[0] <= r['ratio'] <= FAR_NEAR_OK[1]:
        out.append('far/near %.2f' % r['ratio'])
    if not HEIGHT_OK[0] <= r['median_m'] <= HEIGHT_OK[1]:
        out.append('height %.2f m' % r['median_m'])
    return out
