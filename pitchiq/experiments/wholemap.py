"""The top-down map for a whole half, end to end, saved as one table of dots.

The Map Lab only ever mapped 3 x 3-min windows a half. Statistics need the
whole half, so this links the half's detections in back-to-back windows (as the
lab does, `player_lab.run_variant('A+B+C')`), builds `topdown` on each, and
keeps every dot of every frame:

    python -m pitchiq.experiments.wholemap <tag> [--procs 3] [--win-s 180] [--lo F --hi F]

Writes ../PitchIQ-data/stats/map/<tag>.npz (or <tag>_<lo>_<hi>.npz with --lo/--hi):

    f, trk, x, y, seen, alpha, team, role, slot   one row per dot per frame
    win_lo, win_hi                                 the windows
    kit_lab (W, 2, 3), side_x (W, 2), n_seen (W, 2) per window and raw team (a, b)
    swap (W,)                                      the window's team_a is the half's team_b

Positions are metres on the pitch (x along 0..length, y across 0..width).
team: 0 / 1 = the half's two teams (after `swap`), 2 = unknown (grey).
role: 0 player, 1 keeper, 2 referee, 3 anyone else. trk is unique in the half.

Each window is linked alone, so team_a / team_b can trade names between
windows: `swap` lines them up by shirt colour, and the side each team stands
on is kept (side_x) so a disagreement shows up rather than passing silently.

Held out halves (nb2, pi1, wh1, co1, wb2) are refused without --final: they
are scored once, at the end.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
OUT = (REPO.parent / 'PitchIQ-data' / 'stats' / 'map').resolve()
HALVES = REPO / 'pitchiq' / 'balltrack' / 'halves.json'
HELD_OUT = {'nb2', 'pi1', 'wh1', 'co1', 'wb2'}
TEAM_CODE = {'team_a': 0, 'team_b': 1}
ROLE_CODE = {'player': 0, 'keeper': 1, 'referee': 2}


def video_of(tag: str) -> str | None:
    """The video for a half tag such as 'eb1' (game 'eb', half 1); None for the scrimmage."""
    H = json.loads(HALVES.read_text())
    g = H.get(tag[:-1])
    if g is None or tag[:-1] == 'scrim':
        return None
    if g.get('path'):  # process_match's games outside Downloads
        return g['path']
    return str(Path(os.environ.get('PIQ_DOWNLOADS', 'C:/Users/alexv/Downloads')) / g['video'])


_W = {}


def _init(tag):
    v = video_of(tag)
    if v:
        os.environ['PIQ_VIDEO'] = v  # unset, player_lab silently reads the scrimmage
    sys.path.insert(0, str(REPO))
    from pitchiq.balltrack import match_video as MV  # noqa: F401  (chdirs to its work folder)
    from pitchiq.experiments import player_lab

    det, meta = player_lab.load_detections(tag)
    proj, pitch = player_lab.projector(tag, meta['w'], meta['h'])
    sm = np.load(Path(MV.HG) / f'{tag}_smooth2.npz')
    _W.update(tag=tag, det=det, meta=meta, proj=proj, pitch=pitch, Hs=sm['H'], a0=int(sm['abs'][0]))


def _one(span):
    """Link and map one window; return its dots as columns, plus kit and side per raw team."""
    from pitchiq.balltrack import match_video as MV
    from pitchiq.experiments import player_lab
    from pitchiq.experiments.spotlab import _subset

    lo, hi = span
    t0 = time.time()
    det, meta = _W['det'], _W['meta']
    dw = _subset(det, (det.frame >= lo) & (det.frame < hi))
    res = player_lab.run_variant(
        'A+B+C', dw, dict(meta, lo=lo, n=hi - lo), _W['proj'], _W['pitch']
    )[1]
    t1 = time.time()
    td, _ = MV.pitch_map(_W['tag'], res, _W['Hs'], _W['a0'], lo, hi)
    cols = {k: [] for k in ('f', 'trk', 'x', 'y', 'seen', 'alpha', 'team', 'role', 'slot')}
    side = {0: [], 1: []}
    for f in range(lo, hi):
        for d in td.at(f):
            team = TEAM_CODE.get(d.drawn_team, 2)
            role = ROLE_CODE.get(d.group[1], 3)
            cols['f'].append(f)
            cols['trk'].append(d.track)
            cols['x'].append(d.x)
            cols['y'].append(d.y)
            cols['seen'].append(d.seen)
            cols['alpha'].append(d.alpha)
            cols['team'].append(team)
            cols['role'].append(role)
            cols['slot'].append(d.slot)
            if d.seen and team < 2 and role == 0:
                side[team].append(d.x)
    kit = np.full((2, 3), np.nan)
    for t, name in enumerate(('team_a', 'team_b')):
        rows = [
            i.rows[i.rows >= 0] for i in res.identities if i.team == name and i.role == 'player'
        ]
        rows = np.concatenate(rows).astype(int) if rows else np.zeros(0, int)
        if len(rows):
            kit[t] = np.nanmedian(dw.desc[rows, :3], axis=0)
    side_x = np.array([np.mean(side[t]) if side[t] else np.nan for t in (0, 1)])
    n_seen = np.array([len(side[t]) for t in (0, 1)])
    out = dict(
        f=np.asarray(cols['f'], np.int32),
        trk=np.asarray(cols['trk'], np.int32),
        x=np.asarray(cols['x'], np.float32),
        y=np.asarray(cols['y'], np.float32),
        seen=np.asarray(cols['seen'], bool),
        alpha=np.asarray(cols['alpha'], np.float16),
        team=np.asarray(cols['team'], np.int8),
        role=np.asarray(cols['role'], np.int8),
        slot=np.asarray(cols['slot'], np.int16),
    )
    return lo, hi, out, kit, side_x, n_seen, (t1 - t0, time.time() - t1)


def align(kit: np.ndarray, side_x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per window: swap (its team_a is the half's team_b), by shirt colour against the
    windows' running consensus; and whether the side each team stands on agrees.

    kit (W, 2, 3) Lab per raw team; side_x (W, 2) mean x of seen players.
    Returns swap (W,) bool and side_ok (W,) bool (True where a side is unknown)."""
    W = len(kit)
    swap = np.zeros(W, bool)
    ref = None
    for w in range(W):
        k = kit[w]
        if not np.isfinite(k).all():
            continue
        if ref is None:
            ref = [[k[0]], [k[1]]]
            continue
        ra, rb = np.median(ref[0], axis=0), np.median(ref[1], axis=0)
        keep = np.linalg.norm(k[0] - ra) + np.linalg.norm(k[1] - rb)
        flip = np.linalg.norm(k[0] - rb) + np.linalg.norm(k[1] - ra)
        swap[w] = flip < keep
        a, b = (k[1], k[0]) if swap[w] else (k[0], k[1])
        ref[0].append(a)
        ref[1].append(b)
    sx = np.where(swap[:, None], side_x[:, ::-1], side_x)
    d = sx[:, 0] - sx[:, 1]
    ok = np.isfinite(d)
    ref_sign = np.sign(np.nanmedian(d)) if ok.any() else 0.0
    side_ok = ~ok | (np.sign(d) == ref_sign)
    return swap, side_ok


def run(
    tag: str, procs: int = 3, win_s: float = 180.0, lo: int | None = None, hi: int | None = None
):
    import multiprocessing as mp

    from pitchiq.experiments import player_lab

    v = video_of(tag)
    if v:
        os.environ['PIQ_VIDEO'] = v
    z = np.load(player_lab._paths(tag)['dets'])
    f0, f1 = int(z['frame'].min()), int(z['frame'].max()) + 1
    fps = float(z['fps'])
    lo, hi = max(lo or f0, f0), min(hi or f1, f1)
    win = int(round(win_s * fps))
    edges = list(range(lo, hi, win))
    spans = [(a, min(a + win, hi)) for a in edges]
    if len(spans) > 1 and spans[-1][1] - spans[-1][0] < win // 3:  # no stub window at the end
        spans[-2] = (spans[-2][0], spans[-1][1])
        spans.pop()
    print(tag, 'frames', lo, hi, len(spans), 'windows', flush=True)
    t0 = time.time()
    parts = []
    with mp.get_context('spawn').Pool(
        min(procs, len(spans)), initializer=_init, initargs=(tag,)
    ) as pool:
        for r in pool.imap(_one, spans):
            a, b, _, _, _, n, (tl, tm) = r
            print(
                '  %d..%d  link %.0f s  map %.0f s  seen %d/%d  (%.0f s in)'
                % (a, b, tl, tm, n[0], n[1], time.time() - t0),
                flush=True,
            )
            parts.append(r)
    kit = np.stack([p[3] for p in parts])
    side_x = np.stack([p[4] for p in parts])
    swap, side_ok = align(kit, side_x)
    cols = {}
    for w, p in enumerate(parts):
        d = dict(p[2])
        d['trk'] = d['trk'] + 10000 * w
        if swap[w]:
            t = d['team'].copy()
            d['team'] = np.where(t == 0, 1, np.where(t == 1, 0, t)).astype(np.int8)
        for k, v_ in d.items():
            cols.setdefault(k, []).append(v_)
    cols = {k: np.concatenate(v_) for k, v_ in cols.items()}
    OUT.mkdir(parents=True, exist_ok=True)
    name = tag if (lo, hi) == (f0, f1) else f'{tag}_{lo}_{hi}'
    path = OUT / f'{name}.npz'
    np.savez_compressed(
        path,
        **cols,
        win_lo=np.array([p[0] for p in parts]),
        win_hi=np.array([p[1] for p in parts]),
        kit_lab=kit,
        side_x=side_x,
        n_seen=np.stack([p[5] for p in parts]),
        swap=swap,
        side_ok=side_ok,
        fps=fps,
        length_m=float(_pitch_size(tag)[0]),
        width_m=float(_pitch_size(tag)[1]),
    )
    print(
        '%s: %d dots, %d windows, %d swapped, %d side disagreements, %.0f s -> %s'
        % (tag, len(cols['f']), len(parts), swap.sum(), (~side_ok).sum(), time.time() - t0, path),
        flush=True,
    )
    return path


def _pitch_size(tag):
    from pitchiq.panning_camera import PannedCamera

    p = PannedCamera.from_tracker(tag).pitch
    return p.length_m, p.width_m


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog='python -m pitchiq.experiments.wholemap', description=__doc__.split('\n')[0]
    )
    ap.add_argument('tag')
    ap.add_argument('--procs', type=int, default=3)
    ap.add_argument('--win-s', type=float, default=180.0)
    ap.add_argument('--lo', type=int)
    ap.add_argument('--hi', type=int)
    ap.add_argument(
        '--final', action='store_true', help='allow a held out half (score once, at the end)'
    )
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.tag in HELD_OUT and not a.final:
        raise SystemExit(f'{a.tag} is held out: pass --final only for the one final score')
    run(a.tag, a.procs, a.win_s, a.lo, a.hi)


if __name__ == '__main__':
    main()
