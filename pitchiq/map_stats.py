"""Team statistics read off the top-down map: shape, then territory and heatmaps.

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

Territory, from the players, not the ball (`territory.py` splits possession by
the ball, which is not trustworthy yet):

* play        the centre of every outfielder on the map, both teams and greys:
              where the game was, as a share of time in each third
* height      each team's outfield centre, metres from its own goal
* heatmap     each team's outfielder time on a 6 x 5 grid of zones, turned so it
              attacks to the right, with the seen (not guessed) part of each zone

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


# ---------------------------------------------------------------- territory

GRID = (6, 5)  # zones.zone_grid's default: 6 columns along the pitch (2 a third), 5 across
PLAY_MIN = 6  # outfielders on the map before an instant has a centre of play
THIRD_NAMES = ('defensive', 'middle', 'attacking')  # zones.DEFENSIVE_THIRD etc.


def _sampled_rows(t: MapTable, step: int) -> tuple[np.ndarray, np.ndarray]:
    frames = np.unique(t.f)
    frames = frames[(frames - frames[0]) % step == 0]
    keep = np.isin(t.f, frames) & (t.role == OUTFIELD) & (t.alpha.astype(np.float32) > 0)
    return frames, keep


def _attack_right(t: MapTable, x, y, defends_left: bool):
    """Turn the pitch so the team attacks to the right: own goal at x = 0, its
    left wing at y = 0 (a half turn, not a mirror, so wings keep their side)."""
    return (x, y) if defends_left else (t.length_m - x, t.width_m - y)


@dataclass
class TerritorySeries:
    """Where play sat, every sampled frame (NaN where it cannot be said)."""

    frames: np.ndarray
    play: np.ndarray  # centre of every outfielder, team or grey: metres from team 0's goal
    height: dict[int, np.ndarray]  # each team's outfield centre, metres from its own goal


def territory(
    t: MapTable,
    step: int = 15,
    left: dict[int, bool] | None = None,
    drop_bad_split: bool = True,
    split_of: MapTable | None = None,
) -> TerritorySeries:
    """The centre of play and each team's height, every `step` frames.

    The centre of play counts every outfielder, greys included, so it needs no
    team split and is kept in windows `split_checks` drops; a team's height is
    NaN there, like its shape."""
    left = left if left is not None else defends_left(t)
    frames, keep = _sampled_rows(t, step)
    fi = np.searchsorted(frames, t.f)
    F = len(frames)
    x0 = np.where(left[0], t.x, t.length_m - t.x).astype(np.float64)
    m = keep & np.isin(t.team, (0, 1, 2))
    n = np.bincount(fi[m], minlength=F)
    s = np.bincount(fi[m], x0[m], minlength=F)
    play = np.where(n >= PLAY_MIN, s / np.maximum(n, 1), np.nan)
    bad = np.zeros(F, bool)
    if drop_bad_split:
        bad = bad_split_frames(split_of if split_of is not None else t, frames)
    height = {}
    for team in TEAMS:
        r = keep & (t.team == team)
        xt = x0 if left[team] == left[0] else t.length_m - x0
        n = np.bincount(fi[r], minlength=F)
        s = np.bincount(fi[r], xt[r], minlength=F)
        height[team] = np.where((n >= MIN_SHAPE) & ~bad, s / np.maximum(n, 1), np.nan)
    return TerritorySeries(frames, play, height)


def third_shares(fwd: np.ndarray, length_m: float) -> dict[str, float]:
    """Share of the finite values in each third, measured forward from the own
    goal; a value on a boundary goes to the further-forward third (zones.third)."""
    v = fwd[np.isfinite(fwd)]
    if not len(v):
        return {k: float('nan') for k in THIRD_NAMES}
    k = np.clip((v >= length_m / 3).astype(int) + (v >= 2 * length_m / 3), 0, 2)
    c = np.bincount(k, minlength=3) / len(v)
    return dict(zip(THIRD_NAMES, map(float, c)))


@dataclass
class Heatmap:
    """One team's outfielder time per zone, turned so it attacks to the right.

    share sums to 1 (each sampled dot is one equal slice of time); seen is the
    part of each zone's time that was a seen player rather than a guess (NaN for
    an empty zone): a cold zone the camera never showed is a guess, not a fact."""

    share: np.ndarray  # (cols, rows): column 0 at the own goal, row 0 the left wing
    seen: np.ndarray
    samples: int

    def thirds(self) -> dict[str, float]:
        c = self.share.reshape(3, -1).sum(1)  # 6 columns, 2 a third
        return dict(zip(THIRD_NAMES, map(float, c)))


def heatmap(
    t: MapTable,
    team: int,
    step: int = 15,
    left: dict[int, bool] | None = None,
    grid: tuple[int, int] = GRID,
    drop_bad_split: bool = True,
    split_of: MapTable | None = None,
) -> Heatmap:
    """`team`'s outfielders on a `grid` of zones over the sampled frames. Dots past
    a line land in the edge zone (zones.zone_grid clamps the same way)."""
    left = left if left is not None else defends_left(t)
    frames, keep = _sampled_rows(t, step)
    r = keep & (t.team == team)
    if drop_bad_split:
        bad = bad_split_frames(split_of if split_of is not None else t, frames)
        r &= ~np.isin(t.f, frames[bad])
    x, y = _attack_right(t, t.x[r].astype(np.float64), t.y[r].astype(np.float64), left[team])
    cols, rows = grid
    ci = np.clip((x / t.length_m * cols).astype(int), 0, cols - 1)
    ri = np.clip((y / t.width_m * rows).astype(int), 0, rows - 1)
    cell = ci * rows + ri
    n = np.bincount(cell, minlength=cols * rows).astype(np.float64)
    ns = np.bincount(cell, t.seen[r].astype(np.float64), minlength=cols * rows)
    share = (n / max(1.0, n.sum())).reshape(cols, rows)
    seen = np.where(n > 0, ns / np.maximum(n, 1), np.nan).reshape(cols, rows)
    return Heatmap(share, seen, int(n.sum()))


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


# ------------------------------------------------------------ for the report

REPORT_GRID = (21, 14)  # 5 m cells on 105 x 68; the thirds fall on columns 7 and 14


def _num_or_none(v, places: int) -> float | None:
    """A plain rounded float, or None for NaN: absent is null in the report, never 0."""
    v = float(v)
    return round(v, places) if np.isfinite(v) else None


def _finite_mean(v: np.ndarray) -> float:
    v = v[np.isfinite(v)]
    return float(v.mean()) if len(v) else float('nan')


def _shares_or_none(d: dict[str, float]) -> dict[str, float | None] | None:
    if not all(np.isfinite(v) for v in d.values()):
        return None
    return {k: round(float(v), 3) for k, v in d.items()}


def lighter_team(path) -> int:
    """Which map team wore the lighter shirts: the median kit L over the half's
    windows, after each window's team swap (as `experiments/heat_sheet.kits`)."""
    z = np.load(path)
    kit = np.where(z['swap'][:, None, None], z['kit_lab'][:, ::-1], z['kit_lab'])
    light = [float(np.nanmedian(kit[:, team, 0])) for team in TEAMS]
    return int(np.argmax(light))


def report_block(t: MapTable, ours: int) -> dict[str, dict]:
    """The map's team figures, shaped for `report_export`'s teams block.

    `ours` is the map team (0 or 1) that is the coach's side: which side is "us"
    is a human fact the map cannot know. Keyed 'team_a' (ours) and 'team_b'.

    Per team:
    * shape: `line_m` and `compactness_m` (half means). `width_m` and `depth_m`
      are null on purpose: they are max-minus-min, so one stray ring sets them
      (+4 m and +19 m when there is one every instant), and they lean 3-5 m on
      where the map guesses unseen players. See docs/design/map_stats.md.
    * field_position: where the team stood, from the players, not the ball.
      `height_m` its outfield centre from its own goal; `third_share` its
      outfielders' time in each third, its own direction; `play_third_share`
      where the centre of all players was, from this team's direction;
      `heatmap` its outfielders' time on REPORT_GRID, turned to attack right,
      with `seen` (not guessed) per cell; `seen_share` the seen part of all
      its counted time; `kept_share` the part of the half whose team split
      held (`split_checks`). Team numbers in the rest are null.
    * attacking_end: 'right' if it defended the low-x goal, else 'left'.
    """
    if ours not in TEAMS:
        raise ValueError(f'ours must be 0 or 1, not {ours!r}')
    left = defends_left(t)
    sh = shape(t, left=left)
    ts = territory(t, left=left)
    kept = ~bad_split_frames(t, ts.frames)
    out = {}
    for team in TEAMS:
        s = sh[team]
        h = heatmap(t, team, left=left, grid=REPORT_GRID)
        cols, rows = REPORT_GRID
        play = ts.play if team == 0 else t.length_m - ts.play
        counted = s.n[kept].sum()
        out['team_a' if team == ours else 'team_b'] = {
            'shape': {
                'width_m': None,
                'depth_m': None,
                'compactness_m': _num_or_none(s.mean('compact'), 1),
                'line_m': _num_or_none(s.mean('line'), 1),
            },
            'field_position': {
                'height_m': _num_or_none(_finite_mean(ts.height[team]), 1),
                'third_share': _shares_or_none(h.thirds()) if h.samples else None,
                'play_third_share': _shares_or_none(third_shares(play, t.length_m)),
                'heatmap': (
                    {
                        'cols': cols,
                        'rows': rows,
                        'values': [round(float(v), 4) for v in h.share.ravel()],
                        'seen': [_num_or_none(v, 2) for v in h.seen.ravel()],
                    }
                    if h.samples
                    else None
                ),
                'seen_share': (
                    round(float(s.n_seen[kept].sum() / counted), 3) if counted else None
                ),
                'kept_share': round(float(kept.mean()), 3),
            },
            'attacking_end': 'right' if left[team] else 'left',
        }
    return out


def _weighted(values: list, weights: list[float], places: int) -> float | None:
    """The weighted mean of the halves that have a figure; None when none has."""
    pairs = [(v, w) for v, w in zip(values, weights) if v is not None and w > 0]
    total = sum(w for _, w in pairs)
    if not total:
        return None
    return round(sum(v * w for v, w in pairs) / total, places)


def _weighted_shares(dicts: list, weights: list[float]) -> dict[str, float] | None:
    pairs = [(d, w) for d, w in zip(dicts, weights) if d is not None and w > 0]
    if not pairs:
        return None
    return {k: _weighted([d[k] for d, _ in pairs], [w for _, w in pairs], 3) for k in pairs[0][0]}


def _weighted_heatmap(maps: list, weights: list[float]) -> dict | None:
    """Cell shares averaged by each half's counted time; `seen` per cell by the
    time spent in that cell, so a half that never reached a cell has no say in it."""
    pairs = [(h, w) for h, w in zip(maps, weights) if h is not None and w > 0]
    if not pairs:
        return None
    cols, rows = pairs[0][0]['cols'], pairs[0][0]['rows']
    if any((h['cols'], h['rows']) != (cols, rows) for h, _ in pairs):
        raise ValueError('the halves have heatmaps on different grids')
    total = sum(w for _, w in pairs)
    values, seen = [], []
    for i in range(cols * rows):
        values.append(round(sum(h['values'][i] * w for h, w in pairs) / total, 4))
        part = [(h['values'][i] * w, h['seen'][i]) for h, w in pairs if h['seen'][i] is not None]
        time = sum(t for t, _ in part)
        seen.append(round(sum(t * s for t, s in part) / time, 2) if time > 0 else None)
    return {'cols': cols, 'rows': rows, 'values': values, 'seen': seen}


def match_block(blocks: list[dict], durations_s: list[float]) -> dict[str, dict]:
    """One teams block for a whole match from each half's `report_block`.

    The site keeps one report per match, so the halves are joined here. Every
    figure in `report_block` is already in the team's own direction (from its
    own goal, its own thirds, a heatmap turned to attack right), so a half-time
    change of ends needs no turning: each figure is the halves' mean, weighted
    by the time its team split held (`duration * kept_share`). A half with no
    figure has no say; no half with one gives null. `attacking_end` is null
    unless the halves agree, as the ends change at half time.
    Both blocks must name the same side `team_a`: `report_block`'s `ours`.
    """
    if not blocks or len(blocks) != len(durations_s):
        raise ValueError('one duration per half, and at least one half')
    out = {}
    for team in ('team_a', 'team_b'):
        sides = [b[team] for b in blocks]
        pos = [s['field_position'] for s in sides]
        w = [d * (p['kept_share'] or 0.0) for d, p in zip(durations_s, pos)]
        ends = {s['attacking_end'] for s in sides}
        out[team] = {
            'shape': {
                'width_m': None,
                'depth_m': None,
                'compactness_m': _weighted([s['shape']['compactness_m'] for s in sides], w, 1),
                'line_m': _weighted([s['shape']['line_m'] for s in sides], w, 1),
            },
            'field_position': {
                'height_m': _weighted([p['height_m'] for p in pos], w, 1),
                'third_share': _weighted_shares([p['third_share'] for p in pos], w),
                'play_third_share': _weighted_shares([p['play_third_share'] for p in pos], w),
                'heatmap': _weighted_heatmap([p['heatmap'] for p in pos], w),
                'seen_share': _weighted([p['seen_share'] for p in pos], w, 3),
                'kept_share': _weighted([p['kept_share'] for p in pos], list(durations_s), 3),
            },
            'attacking_end': ends.pop() if len(ends) == 1 else None,
        }
    return out
