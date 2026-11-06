"""Team statistics read off the top-down map: shape first.

The older statistics (`metrics.team_shape`, `territory`) were written for the
earlier tracking path, one `PositionSeries` per track. The map (`linking`, then
`topdown`) is a different shape of data: every dot of every frame, each one
seen or guessed, in a team or grey. `experiments/wholemap.py` saves it for a
whole half as one table; this module reads that table.

Shape, per team, every `step` frames (default half a second):

* width       across the pitch, outermost outfielders
* depth       along the pitch, deepest to highest outfielder
* compact     mean distance of the outfielders from their centre
* line        the back line: mean distance of the deepest `metrics.line_players(n)`
              outfielders from their own goal line (`zones.defensive_line_m`)

Keepers, referees and grey (unknown-team) rings are left out of every team
number; greys stay on the map, they just are not counted for either side.

`trim` drops that many outermost dots each side before width and depth: one
wrong ring (a stray, a team flip) sets a max-minus-min on its own, so the
sensitivity check (`experiments/stats_noise.py`) compares trim 0 and 1.

Absent is NaN, never zero: an instant with fewer than `MIN_SHAPE` outfielders
has no shape, and one with fewer than `metrics.MIN_LINE_OUTFIELDERS` no line.

Design notes, measurements and history: `docs/design/map_stats.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .metrics import MIN_LINE_OUTFIELDERS, line_players

MIN_SHAPE = 3  # two players have a width but not a shape (metrics.shape_samples)
TEAMS = (0, 1)
OUTFIELD, KEEPER = 0, 1
SHAPE_KEYS = ('width', 'depth', 'compact', 'line')


@dataclass
class MapTable:
    """One half's map: a row per dot per frame (see experiments/wholemap.py)."""

    f: np.ndarray
    trk: np.ndarray
    x: np.ndarray
    y: np.ndarray
    seen: np.ndarray
    alpha: np.ndarray
    team: np.ndarray
    role: np.ndarray
    fps: float = 30.0
    length_m: float = 105.0
    width_m: float = 68.0
    side_x: np.ndarray | None = None  # (windows, 2) mean x of each team's seen players
    windows: np.ndarray | None = None  # (windows, 2) first and end frame of each linked window
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path) -> 'MapTable':
        z = np.load(path)
        cols = {k: z[k] for k in ('f', 'trk', 'x', 'y', 'seen', 'alpha', 'team', 'role')}
        return cls(
            **cols,
            fps=float(z['fps']),
            length_m=float(z['length_m']),
            width_m=float(z['width_m']),
            side_x=z['side_x'] if 'side_x' in z else None,
            windows=np.stack([z['win_lo'], z['win_hi']], 1) if 'win_lo' in z else None,
        )

    def _meta(self) -> dict:
        return dict(
            fps=self.fps,
            length_m=self.length_m,
            width_m=self.width_m,
            side_x=self.side_x,
            windows=self.windows,
        )

    def subset(self, m: np.ndarray) -> 'MapTable':
        """The rows where m holds (same meta)."""
        cols = {
            k: getattr(self, k)[m] for k in ('f', 'trk', 'x', 'y', 'seen', 'alpha', 'team', 'role')
        }
        return MapTable(**cols, **self._meta())

    def replace(self, **cols) -> 'MapTable':
        """A copy with some columns swapped for new arrays (same length)."""
        base = {
            k: getattr(self, k) for k in ('f', 'trk', 'x', 'y', 'seen', 'alpha', 'team', 'role')
        }
        base.update(cols)
        return MapTable(**base, **self._meta())


# A linked window whose team split went wrong (ww1 77400 and 93600, nb1 14550
# and 25350: brighter exposure, one "team" took 9.5 seen players a frame and
# the other 3-5, with 1.4-2.9 greys; good windows run about 8 and 8 with
# under 1 grey). Its team numbers are dropped, not reported.
# Checked against the keepers on all 135 dev windows: the 8 where a team's keeper
# stood at the wrong end all have ratio < 0.65 or greys >= 2.7; windows with
# 1-2.3 greys and agreeing keepers (co2's two dark kits, pi2's low sun) are kept.
SPLIT_RATIO = 0.65  # fewer seen per frame / more seen per frame, below this is wrong
SPLIT_GREY = 2.5  # seen grey outfielders per frame, at or above this is wrong


def split_checks(t: MapTable) -> list[dict]:
    """Per linked window: seen outfielders per frame of each team and grey, and ok."""
    if t.windows is None:
        return []
    out = []
    for lo, hi in t.windows:
        m = (t.f >= lo) & (t.f < hi) & (t.role == OUTFIELD) & t.seen
        nf = max(1, len(np.unique(t.f[(t.f >= lo) & (t.f < hi)])))
        s0, s1, g = (float((m & (t.team == k)).sum()) / nf for k in (0, 1, 2))
        ratio = min(s0, s1) / max(s0, s1, 1e-9)
        ok = ratio >= SPLIT_RATIO and g < SPLIT_GREY
        out.append(dict(lo=int(lo), hi=int(hi), seen=(s0, s1), grey=g, ratio=ratio, ok=ok))
    return out


def bad_split_frames(t: MapTable, frames: np.ndarray) -> np.ndarray:
    """True for the frames that sit in a window failing `split_checks`."""
    bad = np.zeros(len(frames), bool)
    for c in split_checks(t):
        if not c['ok']:
            bad |= (frames >= c['lo']) & (frames < c['hi'])
    return bad


def defends_left(t: MapTable) -> dict[int, bool]:
    """Which team defends the x = 0 goal this half.

    By its keeper when both keepers were seen a while and stand at opposite ends
    (eb1 probe: keepers at x 4 and 110, while the seen outfielders' means were
    only 5-10 m apart, both teams crowding the end where the ball was). Else the
    team whose seen players stand lower: from the per-window means when the table
    has them (a majority over windows), else from all seen outfielders."""
    gk = [t.x[(t.role == KEEPER) & (t.team == team) & t.seen] for team in TEAMS]
    if min(len(g) for g in gk) >= 10 * t.fps:
        a, b = (float(np.median(g)) for g in gk)
        mid = t.length_m / 2
        if (a - mid) * (b - mid) < 0:
            return {0: a < b, 1: b < a}
    if t.side_x is not None and np.isfinite(t.side_x).all(axis=1).any():
        d = t.side_x[:, 0] - t.side_x[:, 1]
        a_low = float(np.nanmedian(np.sign(d))) < 0
    else:
        m = t.seen & (t.role == OUTFIELD)
        a_low = np.mean(t.x[m & (t.team == 0)]) < np.mean(t.x[m & (t.team == 1)])
    return {0: bool(a_low), 1: not a_low}


@dataclass
class ShapeSeries:
    """One team's shape, one value per sampled frame (NaN where it has none)."""

    frames: np.ndarray
    n: np.ndarray  # outfielders counted
    n_seen: np.ndarray  # ...of them seen (a box) rather than guessed
    width: np.ndarray
    depth: np.ndarray
    compact: np.ndarray
    line: np.ndarray

    def mean(self, key: str, m: np.ndarray | None = None) -> float:
        v = getattr(self, key) if m is None else getattr(self, key)[m]
        v = v[np.isfinite(v)]
        return float(np.mean(v)) if len(v) else float('nan')


def _spread(v: np.ndarray, trim: int) -> float:
    if len(v) - 2 * trim < 2:
        return float('nan')
    s = np.sort(v)
    return float(s[-1 - trim] - s[trim])


def shape(
    t: MapTable,
    step: int = 15,
    seen_only: bool = False,
    min_alpha: float = 0.0,
    trim: int = 0,
    left: dict[int, bool] | None = None,
    drop_bad_split: bool = True,
    split_of: MapTable | None = None,
) -> dict[int, ShapeSeries]:
    """Per team, the shape every `step` frames.

    seen_only: count only dots with a box this frame; else guesses too (the map's
    unseen players, moving with their team), down to `min_alpha` strength. The
    camera shows part of the pitch, so seen-only shape is the shape of what was
    in view: narrower and shorter than the team.

    drop_bad_split: frames in a window whose team split failed `split_checks`
    get NaN for every number (n and n_seen still counted). The split is judged
    on `split_of` when given (the unperturbed table, in the wrong-input check),
    else on `t`."""
    left = left if left is not None else defends_left(t)
    frames = np.unique(t.f)
    frames = frames[(frames - frames[0]) % step == 0]
    bad = np.zeros(len(frames), bool)
    if drop_bad_split:
        bad = bad_split_frames(split_of if split_of is not None else t, frames)
    keep = np.isin(t.f, frames) & (t.role == OUTFIELD) & np.isin(t.team, TEAMS)
    keep &= t.alpha.astype(np.float32) > min_alpha  # 0: every drawn dot
    if seen_only:
        keep &= t.seen
    idx = np.flatnonzero(keep)
    order = idx[np.lexsort((t.f[idx], t.team[idx]))]
    out = {}
    for team in TEAMS:
        rows = order[t.team[order] == team]
        fr = t.f[rows]
        starts = np.searchsorted(fr, frames, 'left')
        ends = np.searchsorted(fr, frames, 'right')
        F = len(frames)
        n = (ends - starts).astype(np.int16)
        n_seen = np.zeros(F, np.int16)
        width, depth, compact, line = (np.full(F, np.nan) for _ in range(4))
        for i in range(F):
            a, b = starts[i], ends[i]
            if b - a == 0:
                continue
            r = rows[a:b]
            n_seen[i] = int(t.seen[r].sum())
            if b - a < MIN_SHAPE or bad[i]:
                continue
            x, y = t.x[r].astype(np.float64), t.y[r].astype(np.float64)
            width[i] = _spread(y, trim)
            depth[i] = _spread(x, trim)
            compact[i] = float(np.mean(np.hypot(x - x.mean(), y - y.mean())))
            if b - a >= MIN_LINE_OUTFIELDERS:
                d = np.sort(x if left[team] else t.length_m - x)
                line[i] = float(d[: line_players(b - a)].mean())
        out[team] = ShapeSeries(frames, n, n_seen, width, depth, compact, line)
    return out


def summary(series: dict[int, ShapeSeries], fps: float, every_s: float = 300.0) -> dict:
    """Half means and per-`every_s` block means per team, metres, rounded for a report."""
    out = {}
    for team, s in series.items():
        blocks = ((s.frames - s.frames[0]) / fps // every_s).astype(int)
        out[team] = {
            'half': {k: round(s.mean(k), 1) for k in SHAPE_KEYS},
            'blocks': [
                {k: round(s.mean(k, blocks == b), 1) for k in SHAPE_KEYS}
                for b in range(blocks.max() + 1)
            ],
            'n_mean': round(float(s.n.mean()), 1),
            'seen_share': round(float(s.n_seen.sum() / max(1, s.n.sum())), 2),
            'cover': {k: round(float(np.isfinite(getattr(s, k)).mean()), 2) for k in SHAPE_KEYS},
        }
    return out
