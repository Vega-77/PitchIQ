"""Stats per position, the position named by place: where a player stands in the team's shape.

Every half second each outfielder on the map gets one of nine roles: the back, middle or front
line, crossed with the left, centre or right. The role comes from where the player stands
against their own team, not against the pitch or a fitted formation:

* the team is turned to attack right (own goal at x = 0, its left wing at y = 0, as
  `map_stats._attack_right`), and needs `MIN_TEAM` outfielders on the map that instant;
* each player's depth and width are measured from the team's middle (the median) in units of
  the team's own spread (10th to 90th percentile), so a back four stays a back four whether
  the team is camped in its own box or pressing high;
* those two numbers are averaged over `WINDOW_S` seconds of the player's own track, so a
  full back who overlaps for two seconds is still a full back, and one who has moved to the
  wing for a minute is a winger for that minute;
* the line is back below -`LINE_CUT`, front above +`LINE_CUT`, middle between; the side is
  left below -`SIDE_CUT`, right above +`SIDE_CUT`, centre between.

Why by place and not by the formation the formation video fits (session step 3.6): per
five-minute stretch the fitted formation flips between near-ties (4-1-4-1, 4-2-3-1, 4-3-3,
3-4-3), even three or four at the back, and the same footage mapped twice gives different
calls. A "left back" named from it would change with a near-tie, not with the game. The
coach chose this instead.

Per role and team the stats are the same ones the report gives per player, counted while
someone stood in that role: minutes, touches, passes, carries, defensive actions, distance
and a heat map, plus, for the coach's side, who filled it each minute (`HOLD_MIN_S`). Who
is the report's clusters (the identity the coach maps to players), so the page can name them.

Keepers, referees and grey (unknown-team) dots get no role. Instants in a window whose team
split failed (`map_stats.split_checks`) get none either: the teams there are wrong.
Distance counts only seen dots, cleaned as the stats clean them (`metrics.clean_track`), so
the nine roles plus "no role" add up to the report's distance for the same players.

    from pitchiq import positions
    placed = positions.place(map_table)
    block = positions.half_block(map_table, placed, half_report, ours=0)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .map_stats import OUTFIELD, TEAMS, MapTable, bad_split_frames, defends_left
from .metrics import MAX_PLAUSIBLE_SPEED_MS, PositionSeries, clean_track

LINES = ('back', 'middle', 'front')
SIDES = ('left', 'centre', 'right')
ROLES = tuple(f'{line}_{side}' for line in LINES for side in SIDES)
NO_ROLE = -1

STEP_S = 0.5  # how often a role is given: the map's own shape step (map_stats.shape)
WINDOW_S = 6.0  # a role is where the player stood over this long, centred
MIN_TEAM = 7  # outfielders on the map before the team has a shape to place anyone in
LINE_CUT = 0.25  # in team depths: back line at about -0.5, front at +0.5 (a 4-4-2)
SIDE_CUT = 0.25  # in team widths: full backs at about +-0.45, centre backs +-0.15
MIN_DEPTH_M = 12.0  # a team bunched tighter than this is still measured against 12 m
MIN_WIDTH_M = 18.0
ROLE_GRID = (12, 8)  # heat map cells per role: about 9 x 7 m on a full pitch
TEAMS_PER_TRACK = 3  # the stats' track id is map trk * 3 + map team (map_table)
# Who stood in a role and when, for our side only (the coach names only their own players):
# per minute of each half, every cluster that held the role HOLD_MIN_S or more of it, with
# how long. Clusters are the report's identity fragments (a few minutes each), so this
# reads as people once the coach has matched clusters to players; the page adds a
# player's clusters up then. Flat parallel lists, as Firestore refuses nested arrays.
HOLD_MIN_S = 5.0
ACTIONS = ('interception', 'tackle', 'recovery', 'duel')


@dataclass
class Placed:
    """Every outfielder's role every `step` frames: one row per player per instant."""

    step: int
    f: np.ndarray  # frame
    sid: np.ndarray  # the stats' track id (trk * 3 + map team)
    team: np.ndarray  # map team, 0 or 1
    role: np.ndarray  # index into ROLES
    x: np.ndarray  # metres, turned so the team attacks right
    y: np.ndarray
    seen: np.ndarray
    depth: np.ndarray  # the averaged depth and width in team units that set the role
    width: np.ndarray
    shaped: dict[int, np.ndarray]  # per map team: the frames it had a shape

    def __len__(self) -> int:
        return len(self.f)

    def lookup(self):
        """A `RoleLookup` over these rows."""
        return RoleLookup(self)


class RoleLookup:
    """The role a stats track held at a frame: the nearest instant within one step."""

    def __init__(self, placed: Placed):
        self.step = placed.step
        order = np.lexsort((placed.f, placed.sid))
        self.sid = placed.sid[order]
        self.f = placed.f[order]
        self.role = placed.role[order]
        self.team = placed.team[order]
        self.starts = {}
        if len(self.sid):
            cut = np.flatnonzero(np.diff(self.sid)) + 1
            lo = np.r_[0, cut]
            hi = np.r_[cut, len(self.sid)]
            self.starts = {int(self.sid[a]): (int(a), int(b)) for a, b in zip(lo, hi)}

    def at(self, sid: int, frames) -> np.ndarray:
        """Roles of `sid` at `frames` (NO_ROLE where it had none within a step)."""
        frames = np.asarray(frames, np.int64)
        out = np.full(frames.shape, NO_ROLE, np.int64)
        span = self.starts.get(int(sid))
        if span is None or not frames.size:
            return out
        a, b = span
        f = self.f[a:b]
        i = np.clip(np.searchsorted(f, frames), 1, len(f) - 1) if len(f) > 1 else np.zeros(
            frames.shape, np.int64
        )
        if len(f) > 1:
            left = i - 1
            near = np.where(np.abs(f[left] - frames) <= np.abs(f[i] - frames), left, i)
        else:
            near = i
        ok = np.abs(f[near] - frames) <= self.step
        out[ok] = self.role[a:b][near[ok]]
        return out

    def team_of(self, sid: int) -> int | None:
        span = self.starts.get(int(sid))
        return None if span is None else int(self.team[span[0]])


def _rolling_mean(values: np.ndarray, run_id: np.ndarray, half: int) -> np.ndarray:
    """Centred mean over +-`half` rows, never across a run boundary (rows sorted by run)."""
    n = len(values)
    if not n:
        return values.copy()
    idx = np.arange(n)
    cut = np.flatnonzero(np.diff(run_id)) + 1
    first = np.repeat(np.r_[0, cut], np.diff(np.r_[0, cut, n]))
    last = np.repeat(np.r_[cut, n] - 1, np.diff(np.r_[0, cut, n]))
    lo = np.maximum(idx - half, first)
    hi = np.minimum(idx + half, last)
    c = np.r_[0.0, np.cumsum(values)]
    return (c[hi + 1] - c[lo]) / (hi - lo + 1)


def place(
    t: MapTable,
    left: dict[int, bool] | None = None,
    step_s: float = STEP_S,
    window_s: float = WINDOW_S,
    drop_bad_split: bool = True,
) -> Placed:
    """Each outfielder's role every `step_s`, from the map's dots (seen and guessed)."""
    left = left if left is not None else defends_left(t)
    step = max(1, round(t.fps * step_s))
    frames = np.unique(t.f)
    frames = frames[(frames - frames[0]) % step == 0]
    keep = np.isin(t.f, frames) & (t.role == OUTFIELD) & np.isin(t.team, TEAMS)
    keep &= t.seen | (t.alpha.astype(np.float32) > 0)
    if drop_bad_split:
        bad = bad_split_frames(t, frames)
        keep &= ~np.isin(t.f, frames[bad])
    idx = np.flatnonzero(keep)
    team = t.team[idx].astype(np.int64)
    x = t.x[idx].astype(np.float64)
    y = t.y[idx].astype(np.float64)
    turn = ~np.vectorize(left.get)(team) if len(team) else np.zeros(0, bool)
    x = np.where(turn, t.length_m - x, x)
    y = np.where(turn, t.width_m - y, y)

    # The team's middle and spread at each instant.
    order = np.lexsort((t.f[idx], team))
    gkey = team[order] * (int(t.f.max()) + 1) + t.f[idx][order]
    cut = np.flatnonzero(np.diff(gkey)) + 1
    lo, hi = np.r_[0, cut], np.r_[cut, len(order)]
    depth = np.full(len(idx), np.nan)
    width = np.full(len(idx), np.nan)
    shaped = {k: [] for k in TEAMS}
    for a, b in zip(lo, hi):
        if b - a < MIN_TEAM:
            continue
        r = order[a:b]
        xd, yw = x[r], y[r]
        d10, d50, d90 = np.percentile(xd, (10, 50, 90))
        w10, w50, w90 = np.percentile(yw, (10, 50, 90))
        depth[r] = (xd - d50) / max(d90 - d10, MIN_DEPTH_M)
        width[r] = (yw - w50) / max(w90 - w10, MIN_WIDTH_M)
        shaped[int(team[r[0]])].append(int(t.f[idx][r[0]]))

    ok = np.isfinite(depth)
    idx, team, x, y, depth, width = idx[ok], team[ok], x[ok], y[ok], depth[ok], width[ok]
    f = t.f[idx].astype(np.int64)
    sid = t.trk[idx].astype(np.int64) * TEAMS_PER_TRACK + team

    # Each track's own few seconds: a run is one track on consecutive instants.
    order = np.lexsort((f, sid))
    f, sid, team, x, y = f[order], sid[order], team[order], x[order], y[order]
    depth, width, idx = depth[order], width[order], idx[order]
    new_run = np.r_[True, (np.diff(sid) != 0) | (np.diff(f) > step)]
    run_id = np.cumsum(new_run)
    half = max(0, int(round(window_s / step_s / 2)))
    depth_s = _rolling_mean(depth, run_id, half)
    width_s = _rolling_mean(width, run_id, half)
    line = np.where(depth_s < -LINE_CUT, 0, np.where(depth_s > LINE_CUT, 2, 1))
    side = np.where(width_s < -SIDE_CUT, 0, np.where(width_s > SIDE_CUT, 2, 1))
    return Placed(
        step=step,
        f=f,
        sid=sid,
        team=team,
        role=(line * 3 + side).astype(np.int64),
        x=x,
        y=y,
        seen=t.seen[idx].astype(bool),
        depth=depth_s,
        width=width_s,
        shaped={k: np.unique(np.asarray(v, np.int64)) for k, v in shaped.items()},
    )


# ------------------------------------------------------------ distance per role


def distance_by_role(t: MapTable, placed: Placed, lookup: RoleLookup | None = None) -> dict:
    """Metres each stats track covered in each role, from its seen dots.

    Cleaned as the stats clean a track (`metrics.clean_track`): spikes dropped, broken at
    gaps and jumps, a runnable gap crossed in a straight line. A step goes to the role held
    where it starts. Returns {sid: array of len(ROLES) + 1}, the last slot "no role"."""
    lookup = lookup or placed.lookup()
    out = {}
    m = t.seen & np.isin(t.team, TEAMS)
    sids_all = t.trk.astype(np.int64) * TEAMS_PER_TRACK + t.team.astype(np.int64)
    wanted = np.isin(sids_all, np.unique(placed.sid)) & m
    idx = np.flatnonzero(wanted)
    order = idx[np.lexsort((t.f[idx], sids_all[idx]))]
    s = sids_all[order]
    cut = np.flatnonzero(np.diff(s)) + 1
    for a, b in zip(np.r_[0, cut], np.r_[cut, len(order)]):
        r = order[a:b]
        sid = int(s[a])
        tot = np.zeros(len(ROLES) + 1)
        if len(r) >= 2:
            series = PositionSeries(
                sid,
                t.f[r].astype(np.float64) / t.fps,
                np.stack([t.x[r], t.y[r]], 1).astype(np.float64),
            )
            clean = clean_track(series)
            prev = None
            for piece in clean.pieces:
                fr = np.round(piece.timestamps_s * t.fps).astype(np.int64)
                if prev is not None:
                    gap_t = piece.timestamps_s[0] - prev[0]
                    gap_m = float(np.linalg.norm(piece.positions_m[0] - prev[1]))
                    if gap_m <= MAX_PLAUSIBLE_SPEED_MS * gap_t:
                        tot[lookup.at(sid, [prev[2]])[0]] += gap_m
                if len(piece) >= 2:
                    steps = np.linalg.norm(np.diff(piece.positions_m, axis=0), axis=1)
                    roles = lookup.at(sid, fr[:-1])
                    np.add.at(tot, roles, steps)  # NO_ROLE (-1) lands in the last slot
                prev = (piece.timestamps_s[-1], piece.positions_m[-1], int(fr[-1]))
        out[sid] = tot
    return out


# ------------------------------------------------------------------ the counts


def _cluster_of(report: dict) -> dict[int, int]:
    return {
        int(tid): int(c['cluster_id'])
        for c in report.get('clusters') or []
        for tid in c.get('track_ids') or []
    }


def _role_heatmap(xs, ys, seen, length_m, width_m, grid=ROLE_GRID) -> dict | None:
    if not len(xs):
        return None
    cols, rows = grid
    ci = np.clip((xs / length_m * cols).astype(int), 0, cols - 1)
    ri = np.clip((ys / width_m * rows).astype(int), 0, rows - 1)
    cell = ci * rows + ri
    n = np.bincount(cell, minlength=cols * rows).astype(np.float64)
    ns = np.bincount(cell, seen.astype(np.float64), minlength=cols * rows)
    share = n / n.sum()
    return {
        'cols': cols,
        'rows': rows,
        'values': [round(float(v), 4) for v in share],
        'seen': [round(float(v), 2) if c else None for v, c in zip(ns / np.maximum(n, 1), n)],
    }


def _holders(
    frames: np.ndarray, clusters: np.ndarray, f0: int, fps: float, step_s: float, half: int
) -> tuple[dict, float]:
    """Who held a role each minute of a half, and the minutes nobody nameable did.

    `clusters` is each instant's cluster id (-1 for none or one the identity does not
    keep). Returns {'half', 'start_s', 'minute', 'cluster_id', 'seconds'}: the last three
    parallel, one entry per minute and cluster held HOLD_MIN_S or more, minute counted
    from `start_s` (the half's first frame, video seconds)."""
    minute = (np.asarray(frames, dtype=np.int64) - f0) // int(round(60 * fps))
    clusters = np.asarray(clusters, dtype=np.int64)
    ok = clusters >= 0
    width = int(clusters.max(initial=0)) + 1
    u, n = np.unique(minute[ok] * width + clusters[ok], return_counts=True)
    secs = n * step_s
    keep = secs >= HOLD_MIN_S
    others = float((~ok).sum() * step_s + secs[~keep].sum()) / 60.0
    return (
        {
            'half': half,
            'start_s': round(f0 / fps, 2),
            'minute': (u[keep] // width).astype(int).tolist(),
            'cluster_id': (u[keep] % width).astype(int).tolist(),
            'seconds': [round(float(v), 1) for v in secs[keep]],
        },
        others,
    )


def _empty_role() -> dict:
    return {
        'minutes': 0.0,
        'occupied_share': None,
        'players_mean': None,
        'touches': 0,
        'passes_attempted': 0,
        'passes_completed': 0,
        'carries': 0,
        'shots': 0,
        'goals': 0,
        'defensive_actions': 0,
        'distance_m': 0.0,
        'seen_share': None,
        'heatmap': None,
        'holders': None,
        'unnamed_minutes': None,
    }


def half_block(
    t: MapTable,
    placed: Placed,
    report: dict,
    ours: int,
    cluster_shift: int = 0,
    named: set[int] | None = None,
    half: int = 1,
) -> dict[str, dict]:
    """Per role, each team's stats for one half: {'team_a': {...}, 'team_b': {...}}.

    `report` is the half's own report (`cached_report half`): its events, clusters and touch
    times use the same track ids as this half's map. `ours` is the map team that is the
    coach's side. `cluster_shift` is added to every cluster id, as `cached_report.merge`
    does to the second half's, so the ids match the published identity. `named` is the
    shifted cluster ids the published identity keeps (`merge --min-minutes` drops short
    ones): only those are listed in `holders`, as only those can be matched to a player.
    The rest is `unnamed_minutes`. `half` is the period, 1 or 2, for `holders`."""
    if ours not in TEAMS:
        raise ValueError(f'ours must be 0 or 1, not {ours!r}')
    lookup = placed.lookup()
    step_s = placed.step / t.fps
    cluster = _cluster_of(report)
    side = {ours: 'team_a', 1 - ours: 'team_b'}
    out = {side[k]: {'roles': {r: _empty_role() for r in ROLES}} for k in TEAMS}
    f0 = int(t.f.min())
    cid_of = np.array([cluster.get(s, -1) for s in placed.sid.tolist()], dtype=np.int64)
    cid_of = np.where(cid_of >= 0, cid_of + cluster_shift, -1)
    if named is not None:
        cid_of = np.where(np.isin(cid_of, sorted(named)), cid_of, -1)

    for k in TEAMS:
        name = side[k]
        mine = placed.team == k
        n_shaped = len(placed.shaped[k])
        out[name]['shaped_minutes'] = round(n_shaped * step_s / 60.0, 2)
        out[name]['method'] = 'place'
        for ri, role in enumerate(ROLES):
            row = out[name]['roles'][role]
            m = mine & (placed.role == ri)
            row['minutes'] = round(float(m.sum()) * step_s / 60.0, 2)
            if n_shaped:
                frames = np.unique(placed.f[m])
                row['occupied_share'] = round(len(frames) / n_shaped, 3)
                row['players_mean'] = round(float(m.sum()) / n_shaped, 2)
            if m.any():
                row['seen_share'] = round(float(placed.seen[m].mean()), 3)
                row['heatmap'] = _role_heatmap(
                    placed.x[m], placed.y[m], placed.seen[m], t.length_m, t.width_m
                )
            if k == ours:  # the coach names only their own players
                row['holders'], unnamed = _holders(
                    placed.f[m], cid_of[m], f0, t.fps, step_s, half
                )
                row['unnamed_minutes'] = round(unnamed, 2)

    # Events: the role of the player who made it, when they made it.
    for e in report.get('events') or []:
        sid, fr = e.get('track_id'), e.get('frame_index')
        if sid is None or fr is None:
            continue
        k = lookup.team_of(sid)
        if k is None:
            continue
        ri = int(lookup.at(sid, [fr])[0])
        if ri == NO_ROLE:
            continue
        row = out[side[k]]['roles'][ROLES[ri]]
        kind = e.get('type')
        if kind == 'pass':
            row['passes_attempted'] += 1
            row['passes_completed'] += int(e.get('outcome') == 'completed')
        elif kind == 'carry':
            row['carries'] += 1
        elif kind == 'shot':
            row['shots'] += 1
            row['goals'] += int(e.get('outcome') == 'goal')
        elif kind in ACTIONS:
            row['defensive_actions'] += 1

    # Touches: each cluster's touch times, on whichever of its tracks was there.
    tracks_of: dict[int, list[int]] = {}
    for sid, cid in cluster.items():
        tracks_of.setdefault(cid, []).append(sid)
    for c in report.get('tracks') or []:
        times = c.get('touch_times_s') or []
        if not times:
            continue
        sids = tracks_of.get(int(c['cluster_id']), [])
        for ts in times:
            fr = int(round(ts * t.fps))
            for sid in sids:
                ri = int(lookup.at(sid, [fr])[0])
                if ri != NO_ROLE:
                    out[side[lookup.team_of(sid)]]['roles'][ROLES[ri]]['touches'] += 1
                    break

    # Distance, seen dots only.
    dist = distance_by_role(t, placed, lookup)
    for sid, tot in dist.items():
        k = lookup.team_of(sid)
        for ri, role in enumerate(ROLES):
            out[side[k]]['roles'][role]['distance_m'] += float(tot[ri])
    for name in out:
        for row in out[name]['roles'].values():
            row['distance_m'] = round(row['distance_m'], 1)
    return out


def _merge_heatmaps(maps: list, weights: list[float]) -> dict | None:
    pairs = [(h, w) for h, w in zip(maps, weights) if h is not None and w > 0]
    if not pairs:
        return None
    cols, rows = pairs[0][0]['cols'], pairs[0][0]['rows']
    total = sum(w for _, w in pairs)
    values, seen = [], []
    for i in range(cols * rows):
        values.append(round(sum(h['values'][i] * w for h, w in pairs) / total, 4))
        part = [(h['values'][i] * w, h['seen'][i]) for h, w in pairs if h['seen'][i] is not None]
        time = sum(x for x, _ in part)
        seen.append(round(sum(x * s for x, s in part) / time, 2) if time > 0 else None)
    return {'cols': cols, 'rows': rows, 'values': values, 'seen': seen}


COUNT_KEYS = (
    'touches',
    'passes_attempted',
    'passes_completed',
    'carries',
    'shots',
    'goals',
    'defensive_actions',
)


def match_block(blocks: list[dict]) -> dict[str, dict]:
    """One block for the match from each half's `half_block` (both name team_a the same side).

    Counts, minutes and distance are summed; shares are weighted by each half's shaped
    minutes, heat maps by the role's minutes; holders are each half's, in a list (the
    halves' cluster ids never collide once shifted, as in `cached_report.merge`).
    A role nobody stood in for a whole half still has its row there, with zeros."""
    if not blocks:
        raise ValueError('at least one half')
    out = {}
    for name in ('team_a', 'team_b'):
        sides = [b[name] for b in blocks]
        shaped = [s['shaped_minutes'] for s in sides]
        roles = {}
        for role in ROLES:
            rows = [s['roles'][role] for s in sides]
            row = _empty_role()
            for key in COUNT_KEYS:
                row[key] = sum(r[key] for r in rows)
            row['minutes'] = round(sum(r['minutes'] for r in rows), 2)
            row['distance_m'] = round(sum(r['distance_m'] for r in rows), 1)
            unnamed = [r['unnamed_minutes'] for r in rows if r['unnamed_minutes'] is not None]
            row['unnamed_minutes'] = round(sum(unnamed), 2) if unnamed else None
            for key in ('occupied_share', 'players_mean'):
                pairs = [(r[key], w) for r, w in zip(rows, shaped) if r[key] is not None and w]
                tw = sum(w for _, w in pairs)
                row[key] = round(sum(v * w for v, w in pairs) / tw, 3) if tw else None
            pairs = [(r['seen_share'], r['minutes']) for r in rows if r['seen_share'] is not None]
            tw = sum(w for _, w in pairs)
            row['seen_share'] = round(sum(v * w for v, w in pairs) / tw, 3) if tw else None
            row['heatmap'] = _merge_heatmaps(
                [r['heatmap'] for r in rows], [r['minutes'] for r in rows]
            )
            held = [r['holders'] for r in rows if r['holders'] is not None]
            row['holders'] = held or None
            roles[role] = row
        out[name] = {
            'roles': roles,
            'shaped_minutes': round(sum(shaped), 2),
            'method': 'place',
        }
    return out
