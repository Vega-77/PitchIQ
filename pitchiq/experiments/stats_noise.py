"""How much a map statistic moves when the map is a bit wrong.

The method that showed possession was fragile (half its frames changed answer
when the ball moved 20 px), applied to the team shape from `map_stats`:
perturb the map table in ways it is actually wrong, recompute, compare.

    python -m pitchiq.experiments.stats_noise eb1 ww2 ... [--reps 3] [--out file.json]

Reads ../PitchIQ-data/stats/map/<tag>.npz (experiments/wholemap.py). Kinds of wrong:

    jitter1   every dot moved by N(0, 1 m) each, independently
    bias1     every track moved by one N(0, 1 m) offset for its whole life
    drop1     one outfielder per team missing at every instant
    flip1     one outfielder per instant drawn in the other team
    stray1    one extra ring per team per instant, anywhere on the pitch
    edge1     one extra ring per team per instant, just off a touchline (ball kid, coach)
    cal2      the pitch 2% too long and too wide (a calibration error)

Each is run with trim 0 and trim 1 (width and depth leave out the outermost
dot each side). Reported per half and team: the half mean, how far each kind
moves it, and how far it moves the 5-minute block means (the worst block), next
to the real spread of block means, so the noise reads against the signal.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from pitchiq import map_stats as M

MAP = Path(__file__).resolve().parents[3] / 'PitchIQ-data' / 'stats' / 'map'
KINDS = ('jitter1', 'bias1', 'drop1', 'flip1', 'stray1', 'edge1', 'cal2')
STEP = 15


def _sampled(t: M.MapTable, step: int) -> M.MapTable:
    f0 = int(t.f.min())
    return t.subset((t.f - f0) % step == 0)


def _one_per_group(t: M.MapTable, m: np.ndarray, rng) -> np.ndarray:
    """Index of one random row among rows m in each (frame, team) group."""
    idx = np.flatnonzero(m)
    key = rng.random(len(idx))
    o = idx[np.lexsort((key, t.team[idx], t.f[idx]))]
    g = t.f[o].astype(np.int64) * 4 + t.team[o]
    last = np.r_[g[1:] != g[:-1], True]
    return o[last]


def perturb(t: M.MapTable, kind: str, rng) -> M.MapTable:
    out = (t.role == M.OUTFIELD) & np.isin(t.team, M.TEAMS) & (t.alpha.astype(np.float32) > 0)
    if kind == 'jitter1':
        return t.replace(x=t.x + rng.normal(0, 1, len(t.x)), y=t.y + rng.normal(0, 1, len(t.y)))
    if kind == 'bias1':
        u, inv = np.unique(t.trk, return_inverse=True)
        ox, oy = rng.normal(0, 1, len(u)), rng.normal(0, 1, len(u))
        return t.replace(x=t.x + ox[inv], y=t.y + oy[inv])
    if kind == 'drop1':
        keep = np.ones(len(t.f), bool)
        keep[_one_per_group(t, out, rng)] = False
        return t.subset(keep)
    if kind == 'flip1':
        team = t.team.copy()
        idx = np.flatnonzero(out)
        key = rng.random(len(idx))
        o = idx[np.lexsort((key, t.f[idx]))]
        first = np.r_[True, t.f[o][1:] != t.f[o][:-1]]
        r = o[first]
        team[r] = 1 - team[r]
        return t.replace(team=team)
    if kind in ('stray1', 'edge1'):
        frames = np.unique(t.f)
        n = 2 * len(frames)
        if kind == 'stray1':
            y = rng.uniform(0, t.width_m, n)
        else:  # a ball kid or coach: 0-2 m outside either touchline
            y = np.where(
                rng.random(n) < 0.5, -rng.uniform(0, 2, n), t.width_m + rng.uniform(0, 2, n)
            )
        add = dict(
            f=np.repeat(frames, 2).astype(t.f.dtype),
            trk=np.full(n, -1, t.trk.dtype),
            x=rng.uniform(0, t.length_m, n).astype(t.x.dtype),
            y=y.astype(t.y.dtype),
            seen=np.ones(n, bool),
            alpha=np.ones(n, t.alpha.dtype),
            team=np.tile(np.array([0, 1], t.team.dtype), len(frames)),
            role=np.zeros(n, t.role.dtype),
        )
        cols = {k: np.concatenate([getattr(t, k), v]) for k, v in add.items()}
        return M.MapTable(**cols, **t._meta())
    if kind == 'cal2':
        cx, cy = t.length_m / 2, t.width_m / 2
        return t.replace(x=cx + (t.x - cx) * 1.02, y=cy + (t.y - cy) * 1.02)
    raise ValueError(kind)


def blocks_of(s: M.ShapeSeries, fps: float, every_s: float = 300.0) -> np.ndarray:
    return ((s.frames - s.frames[0]) / fps // every_s).astype(int)


def block_means(s: M.ShapeSeries, key: str, b: np.ndarray) -> np.ndarray:
    return np.array([s.mean(key, b == k) for k in range(b.max() + 1)])


def run_half(tag: str, reps: int = 3, seed: int = 0) -> dict:
    t = _sampled(M.MapTable.load(MAP / f'{tag}.npz'), STEP)
    left = M.defends_left(t)
    res = {}
    for trim in (0, 1):
        base = M.shape(t, step=STEP, trim=trim, left=left)
        seen = M.shape(t, step=STEP, trim=trim, left=left, seen_only=True)
        for team in M.TEAMS:
            s = base[team]
            b = blocks_of(s, t.fps)
            row = {}
            for key in M.SHAPE_KEYS:
                bm = block_means(s, key, b)
                row[key] = dict(
                    half=s.mean(key),
                    block_sd=float(np.nanstd(bm)),  # the real 5-min to 5-min movement
                    seen_only=seen[team].mean(key) - s.mean(key),
                    move={},
                    block_move={},
                )
            res.setdefault(f'trim{trim}', {})[team] = row
        for kind in KINDS:
            d_half = {team: {k: [] for k in M.SHAPE_KEYS} for team in M.TEAMS}
            d_block = {team: {k: [] for k in M.SHAPE_KEYS} for team in M.TEAMS}
            for r in range(reps):
                rng = np.random.default_rng([seed, r, KINDS.index(kind)])
                p = M.shape(perturb(t, kind, rng), step=STEP, trim=trim, left=left)
                for team in M.TEAMS:
                    b = blocks_of(base[team], t.fps)
                    for key in M.SHAPE_KEYS:
                        d_half[team][key].append(p[team].mean(key) - base[team].mean(key))
                        bp = block_means(p[team], key, b)
                        bb = block_means(base[team], key, b)
                        d_block[team][key].append(float(np.nanmax(np.abs(bp - bb))))
            for team in M.TEAMS:
                for key in M.SHAPE_KEYS:
                    row = res[f'trim{trim}'][team][key]
                    row['move'][kind] = float(np.mean(d_half[team][key]))
                    row['block_move'][kind] = float(np.mean(d_block[team][key]))
    res['n_mean'] = {team: float(base[team].n.mean()) for team in M.TEAMS}
    # How often the map is wrong in these ways: an instant with more than ten
    # outfielders has at least one extra ring, one with fewer is missing someone.
    # Lower bounds both: an extra and a missing in one instant cancel in the count.
    res['over'] = {team: float(np.mean(base[team].n > 10)) for team in M.TEAMS}
    res['under'] = {team: float(np.mean(np.clip(10 - base[team].n, 0, None))) for team in M.TEAMS}
    # The kinds above wrong every instant; scaled by those rates, the expected move
    # of the half mean: missing players (drop1 x mean missing) plus extras placed
    # anywhere (stray1) or just off a touchline (edge1), x their share of instants.
    for trim in ('trim0', 'trim1'):
        for team in M.TEAMS:
            for key in M.SHAPE_KEYS:
                x = res[trim][team][key]
                miss = x['move']['drop1'] * res['under'][team]
                x['expected'] = {
                    k: miss + x['move'][k] * res['over'][team] for k in ('stray1', 'edge1')
                }
    res['seen_share'] = {
        team: float(base[team].n_seen.sum() / max(1, base[team].n.sum())) for team in M.TEAMS
    }
    res['minutes'] = float((t.f.max() - t.f.min()) / t.fps / 60)
    return res


def table(all_res: dict, trim: str = 'trim1') -> str:
    lines = []
    for key in M.SHAPE_KEYS:
        lines.append(
            f'== {key} ({trim}) ==  half mean | 5-min sd | seen-only | expected (stray, edge) | '
            + ' '.join(KINDS)
            + ' (half / worst block)'
        )
        for tag, r in all_res.items():
            for team in M.TEAMS:
                x = r[trim][team][key]
                mv = ' '.join('%+.1f/%.1f' % (x['move'][k], x['block_move'][k]) for k in KINDS)
                ex = '%+.1f %+.1f' % (x['expected']['stray1'], x['expected']['edge1'])
                lines.append(
                    '%s t%d  %5.1f | %4.1f | %+5.1f | %s | %s'
                    % (tag, team, x['half'], x['block_sd'], x['seen_only'], ex, mv)
                )
    return '\n'.join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog='python -m pitchiq.experiments.stats_noise', description=__doc__.split('\n')[0]
    )
    ap.add_argument('tags', nargs='+')
    ap.add_argument('--reps', type=int, default=3)
    ap.add_argument('--out')
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    all_res = {}
    for tag in a.tags:
        all_res[tag] = run_half(tag, a.reps)
        print(tag, 'done', flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps(all_res, indent=1, default=float))
    for trim in ('trim0', 'trim1'):
        print(table(all_res, trim))


if __name__ == '__main__':
    main()
