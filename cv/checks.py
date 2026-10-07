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

Map physics (on a `topdown.TopDown`, positions in metres)
  * `speeds`: steps faster than a person runs, and one-frame jumps.
  * `crowding`: frames with more than eleven of a team on the pitch.
  * `still_outside`: players who stand outside the lines.
"""
from __future__ import annotations

import numpy as np

RUN_MS = 10.0          # faster than any player sprints, m/s
JUMP_M = 1.0           # one frame at 30 fps: 30 m/s
MARGIN_SB = 3.0        # StatsBomb units of slack around the lines for feet
OUT_M = 2.0            # this far beyond a line is outside it
STILL_MS = 0.5         # slower than this is standing


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
        front = np.sign(q[:, 2]) == np.sign(np.median(q[:, 2]))     # H's overall sign is arbitrary
        xy = q[:, :2] / q[:, 2:3]
        inside = front & (xy[:, 0] > -margin) & (xy[:, 0] < 120 + margin) & (xy[:, 1] > -margin) & (xy[:, 1] < 80 + margin)
        on += int(inside.sum()); n += len(p)
    return dict(feet=n, on_pitch=on / n if n else float('nan'))


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
    return dict(steps=int(both.sum()), fast=float((v > RUN_MS).mean()) if len(v) else 0.0,
                jumps=int((step[both] > JUMP_M).sum()), worst_ms=float(v.max()) if len(v) else 0.0)


def _on_pitch(xy: np.ndarray, length: float, width: float, out: float = OUT_M) -> np.ndarray:
    return (xy[..., 0] > -out) & (xy[..., 0] < length + out) & (xy[..., 1] > -out) & (xy[..., 1] < width + out)


def crowding(td, length: float, width: float, roles=('player', 'keeper'), min_alpha: float = 0.02) -> dict:
    """Frames where a team has more than eleven drawn on the pitch, and the most at once.
    min_alpha: the faintest guess counted (inf: seen boxes only)."""
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


def still_outside(td, length: float, width: float, fps: float = 30.0, min_s: float = 2.0,
                  roles=('player', 'keeper')) -> dict:
    """Players seen standing outside the lines for min_s or more at a stretch:
    usually a sub or a coach read as a player, or a calibration off at the edge."""
    seen = td.owner >= 0
    out = seen & ~_on_pitch(td.path, length, width)
    sp = np.r_['1', np.zeros((len(td.path), 1)), np.linalg.norm(np.diff(td.path, axis=1), axis=2) * fps]
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
    return dict(speeds=speeds(td, fps), crowding=crowding(td, length, width),
                crowding_seen=crowding(td, length, width, min_alpha=np.inf),
                still_outside=still_outside(td, length, width, fps))
