"""The match viewer's data: a game's map, 3D ball, possession and moments, packed for the site.

    python -m pitchiq viewer_export wbr --ours lighter --report wbr_report.json --out view/

Session step 3.9. The coach's match viewer plays the video with the top-down map beside it,
the stats so far and the moments on a timeline. This module packs what it draws:

- Samples, `HZ` a second, of each half. Each sample has every dot on the map (x and y in
  decimetres, the stats' track id, team, role, and how sure the map is: 15 = seen on that
  frame, 0-14 the alpha of a guess), the 3D ball (decimetres, height, state) and who had the
  ball (`save_holds`, the stats' own per-frame answer).
- The chunks are `CHUNK_S` of samples each, a few hundred KB, under Firestore's 1 MB a
  document. Arrays are little-endian and base64 encoded, so the page reads them as typed
  arrays.
- The index has the halves, the kits, possession in `BUCKET_S` buckets (for possession so
  far), and every moment with its "why". The why is the player it was given to and their
  distance from the ball, plus the nearest other player (MAF FC's explorer idea).

The map's frame numbers are the video's (`halves.json`), so a sample's time in the video is
its frame over fps. Nothing has to be fitted to keep the map and the video in step.

Which map team is ours follows the stats (`map_table.HalfTable.teams`). The export checks it
against the moments: every moment's team must match its track's map team. A mismatch means
the export and the stats disagree on which team is ours.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HZ = 5  # samples a second (6 frames at 30 fps): the page moves the dots between them
CHUNK_S = 300  # seconds of samples in one chunk document
BUCKET_S = 10  # possession buckets, for possession so far
DM = 10.0  # positions in decimetres
NO_XY = -32768  # int16: no position
NO_ID = 0xFFFFFFFF  # uint32: no track
TEAMS_PER_TRACK = 3  # the stats' track id is map trk * 3 + map team (map_table)
BALL_STATES = ('none', 'ground', 'air', 'unsure')
HOLD_CODES = {'team_a': 0, 'team_b': 1, 'unknown': 2, 'dead': 3}  # 255: no state
NO_HOLD = 255
SEEN = 15  # the sureness code of a dot seen on that frame
PLAYING_ROLES = (0, 1)  # player, keeper: the ones the why's "nearest player" looks at
MAX_MISMATCH = 0.02  # share of moments whose team disagrees with their track's


def save_holds(states, fps: float, path: Path) -> None:
    """Who had the ball on each frame, from the stats' prepared states.
    `pipeline.MatchReport.frame_states`, which `cached_report half` saves beside its report."""
    frame = np.array([round(s.timestamp_s * fps) for s in states], np.int64)
    team = np.array([HOLD_CODES.get(s.team, HOLD_CODES['unknown']) for s in states], np.uint8)
    holder = np.array([-1 if s.holder_track is None else s.holder_track for s in states], np.int64)
    np.savez_compressed(path, frame=frame, team=team, holder=holder, fps=fps)


def ours_team(kit_lab: np.ndarray, ours_kit: str) -> int:
    """The map team that is team_a: the lighter or darker kit by median L over the windows,
    as `map_table.HalfTable.teams` decides it for the stats."""
    if ours_kit in ('0', '1'):
        return int(ours_kit)
    lighter = int(np.argmax(np.median(kit_lab, axis=0)[:, 0]))
    return lighter if ours_kit == 'lighter' else 1 - lighter


def kit_hex(lab) -> str:
    """An OpenCV Lab (0-255) kit colour as #rrggbb."""
    import cv2

    px = np.clip(np.round(np.asarray(lab, np.float64)), 0, 255).astype(np.uint8).reshape(1, 1, 3)
    r, g, b = cv2.cvtColor(px, cv2.COLOR_Lab2RGB)[0, 0]
    return f'#{r:02x}{g:02x}{b:02x}'


def b64(a: np.ndarray, dtype) -> str:
    return base64.b64encode(
        np.ascontiguousarray(a, dtype=np.dtype(dtype).newbyteorder('<')).tobytes()
    ).decode('ascii')


def _dm(v: np.ndarray) -> np.ndarray:
    out = np.round(np.asarray(v, np.float64) * DM)
    return np.where(np.isfinite(out), np.clip(out, -32767, 32767), NO_XY).astype(np.int16)


@dataclass
class HalfInputs:
    tag: str
    map_path: Path  # stats/map/<tag>.npz
    ball_path: Path  # h3d_v17/<tag>_ball3dd.npz
    holds_path: Path | None  # stats/<g>/run/pipe_<tag>.holds.npz
    camera_height_m: float  # the ball's z is in camera heights


@dataclass
class Half:
    tag: str
    period: int
    fps: float
    f0: int
    f1: int
    step: int
    ours: int
    length_m: float
    width_m: float
    kits: dict[str, str]
    # Full-rate arrays for the moments' why.
    f: np.ndarray
    trk_id: np.ndarray
    x: np.ndarray
    y: np.ndarray
    role: np.ndarray
    team: np.ndarray  # 0 team_a, 1 team_b, 2 grey
    code: np.ndarray
    ball_f0: int
    ball_x: np.ndarray  # metres
    ball_y: np.ndarray
    ball_h: np.ndarray  # metres above the ground
    ball_state: np.ndarray  # index into BALL_STATES
    hold_frame: np.ndarray | None
    hold_team: np.ndarray | None
    hold_id: np.ndarray | None

    @property
    def samples(self) -> np.ndarray:
        return np.arange(self.f0, self.f1 + 1, self.step)

    def ball_at(self, frames: np.ndarray):
        i = np.asarray(frames) - self.ball_f0
        ok = (i >= 0) & (i < len(self.ball_x))
        j = np.where(ok, i, 0)
        nan = np.full(len(j), np.nan)
        x = np.where(ok, self.ball_x[j], nan)
        y = np.where(ok, self.ball_y[j], nan)
        h = np.where(ok, self.ball_h[j], nan)
        st = np.where(ok, self.ball_state[j], 0)
        return x, y, h, st

    def holds_at(self, frames: np.ndarray):
        n = len(frames)
        if self.hold_frame is None:
            return np.full(n, NO_HOLD, np.uint8), np.full(n, NO_ID, np.uint32)
        i = np.searchsorted(self.hold_frame, frames)
        i = np.clip(i, 0, len(self.hold_frame) - 1)
        ok = self.hold_frame[i] == frames
        team = np.where(ok, self.hold_team[i], NO_HOLD).astype(np.uint8)
        hid = np.where(ok & (self.hold_id[i] >= 0), self.hold_id[i], NO_ID).astype(np.uint32)
        return team, hid

    def dots_at(self, frame: int):
        a, b = np.searchsorted(self.f, frame, 'left'), np.searchsorted(self.f, frame, 'right')
        return slice(a, b)


def load_half(inp: HalfInputs, period: int, ours_kit: str) -> Half:
    with np.load(inp.map_path) as m:
        f = m['f'].astype(np.int64)
        trk = m['trk'].astype(np.int64)
        x, y = m['x'].astype(np.float64), m['y'].astype(np.float64)
        seen, alpha = m['seen'].astype(bool), m['alpha'].astype(np.float64)
        mteam, role = m['team'].astype(np.int64), m['role'].astype(np.int64)
        fps, L, W = float(m['fps']), float(m['length_m']), float(m['width_m'])
        kit_lab = np.asarray(m['kit_lab'], np.float64)
    if np.any(np.diff(f) < 0):
        raise ValueError(f'{inp.map_path}: rows are not in frame order')
    ours = ours_team(kit_lab, ours_kit)
    team = np.where(mteam == 2, 2, np.where(mteam == ours, 0, 1))
    sure = np.where(seen, SEEN, np.clip(np.round(np.nan_to_num(alpha) * 14), 0, 14)).astype(
        np.int64
    )
    code = (team | (role << 2) | (sure << 4)).astype(np.uint8)
    lab = np.median(kit_lab, axis=0)
    kits = {'team_a': kit_hex(lab[ours]), 'team_b': kit_hex(lab[1 - ours])}

    with np.load(inp.ball_path) as z:
        bf = z['abs'].astype(np.int64)
        if not np.array_equal(bf, np.arange(bf[0], bf[0] + len(bf))):
            raise ValueError(f'{inp.ball_path}: the ball skips frames')
        state = np.array(
            [BALL_STATES.index(s) if s in BALL_STATES else 0 for s in z['state']], np.uint8
        )
        bx = np.asarray(z['x'], np.float64) * L / 120.0
        by = np.asarray(z['y'], np.float64) * W / 80.0
        bh = np.asarray(z['z'], np.float64) * inp.camera_height_m
    none = state == 0
    bx[none] = by[none] = bh[none] = np.nan

    hf = ht = hid = None
    if inp.holds_path is not None and Path(inp.holds_path).exists():
        with np.load(inp.holds_path) as h:
            order = np.argsort(h['frame'], kind='stable')
            hf, ht, hid = h['frame'][order], h['team'][order], h['holder'][order]

    step = max(1, round(fps / HZ))
    return Half(
        inp.tag, period, fps, int(f[0]), int(f[-1]), step, ours, L, W, kits,
        f, trk * TEAMS_PER_TRACK + mteam, x, y, role, team, code,
        int(bf[0]), bx, by, bh, state, hf, ht, hid,
    )  # fmt: skip


def chunks(h: Half) -> list[dict]:
    """The half's samples as chunk documents of CHUNK_S seconds each."""
    fs = h.samples
    per = max(1, int(round(CHUNK_S * HZ)))
    keep = (h.f >= h.f0) & ((h.f - h.f0) % h.step == 0)
    sf, sx, sy, sid, sc = h.f[keep], h.x[keep], h.y[keep], h.trk_id[keep], h.code[keep]
    if sid.size and sid.max() >= NO_ID:
        raise ValueError(f'{h.tag}: a track id does not fit 32 bits')
    bx, by, bh, bs = h.ball_at(fs)
    ht, hid = h.holds_at(fs)
    out = []
    for k, s0 in enumerate(range(0, len(fs), per)):
        s1 = min(len(fs), s0 + per)
        lo, hi = fs[s0], fs[s1 - 1]
        a, b = np.searchsorted(sf, lo, 'left'), np.searchsorted(sf, hi, 'right')
        n = np.bincount((sf[a:b] - lo) // h.step, minlength=s1 - s0)
        if n.max(initial=0) > 255:
            raise ValueError(f'{h.tag}: over 255 dots on one frame')
        hgt = np.where(np.isfinite(bh[s0:s1]), np.clip(np.round(bh[s0:s1] * DM), 0, 255), 0)
        out.append({
            'id': f'{h.tag}_{k:02d}',
            'tag': h.tag,
            'k': k,
            'f0': int(lo),
            'samples': int(s1 - s0),
            'n': b64(n, np.uint8),
            'x': b64(_dm(sx[a:b]), np.int16),
            'y': b64(_dm(sy[a:b]), np.int16),
            'id32': b64(sid[a:b], np.uint32),
            'code': b64(sc[a:b], np.uint8),
            'bx': b64(_dm(bx[s0:s1]), np.int16),
            'by': b64(_dm(by[s0:s1]), np.int16),
            'bh': b64(hgt, np.uint8),
            'bs': b64(bs[s0:s1], np.uint8),
            'ht': b64(ht[s0:s1], np.uint8),
            'hid': b64(hid[s0:s1], np.uint32),
        })  # fmt: skip
    return out


def possession_buckets(h: Half) -> dict:
    """Frames each team had the ball in BUCKET_S buckets from the half's first frame."""
    if h.hold_frame is None:
        return {'f0': h.f0, 'a': [], 'b': []}
    size = int(round(BUCKET_S * h.fps))
    k = (h.hold_frame - h.f0) // size
    ok = k >= 0
    nb = int(k[ok].max()) + 1 if ok.any() else 0
    a = np.bincount(k[ok & (h.hold_team == 0)], minlength=nb)
    b = np.bincount(k[ok & (h.hold_team == 1)], minlength=nb)
    return {'f0': h.f0, 'a': a.tolist(), 'b': b.tolist()}


def why(h: Half, e: dict) -> dict:
    """Why a moment was given to its player: their distance from the ball then, and the
    nearest other player. The ball is the moment's own start point (where the stats saw the
    touch), else the 3D ball on that frame."""
    frame = int(e['frame_index'])
    bx, by, _, bs = (v[0] for v in h.ball_at(np.array([frame])))
    start = e.get('start_m')
    px, py = (start[0], start[1]) if start else (bx, by)
    out = {'ball': BALL_STATES[int(bs)]}
    if not (np.isfinite(px) and np.isfinite(py)):
        return out
    s = h.dots_at(frame)
    ids, xs, ys, roles, teams = h.trk_id[s], h.x[s], h.y[s], h.role[s], h.team[s]
    d = np.hypot(xs - px, ys - py)
    tid = e.get('track_id')
    mine = ids == tid
    if mine.any():
        out['d'] = round(float(d[mine].min()), 1)
    others = ~mine & np.isin(roles, PLAYING_ROLES)
    if others.any():
        j = int(np.flatnonzero(others)[np.argmin(d[others])])
        out.update(near=int(ids[j]), near_team='ab-'[int(teams[j])], near_d=round(float(d[j]), 1))
    return out


def moments(halves: list[Half], events: list[dict]) -> tuple[list[dict], int]:
    """The report's moments, each with its half, time and why; and how many had a team that
    disagrees with their track's map team."""
    out, bad = [], 0
    for e in sorted(events, key=lambda e: e['frame_index']):
        frame = int(e['frame_index'])
        h = next((h for h in halves if h.f0 <= frame <= h.f1), None)
        if h is None:
            continue
        tid = e.get('track_id')
        if tid is not None and e.get('team') in ('team_a', 'team_b'):
            mteam = tid % TEAMS_PER_TRACK
            mapped = 2 if mteam == 2 else (0 if mteam == h.ours else 1)
            bad += mapped != HOLD_CODES[e['team']]
        m = {
            'p': h.period,
            't': round(frame / h.fps, 2),
            'type': e['type'],
            'team': {'team_a': 'a', 'team_b': 'b'}.get(e.get('team'), '-'),
            'id': tid,
        }
        for k in ('start_m', 'end_m'):
            if e.get(k):
                m[k[0]] = [round(float(v), 1) for v in e[k]]
        for k, short in (('outcome', 'out'), ('length_m', 'len'), ('confidence', 'conf')):
            if e.get(k) is not None:
                m[short] = round(e[k], 2) if isinstance(e[k], float) else e[k]
        if e.get('tags'):
            m['tags'] = e['tags']
        if e.get('receiver_track_id') is not None:
            m['to'] = e['receiver_track_id']
        m['why'] = why(h, e)
        out.append(m)
    return out, bad


def build(
    game: str, inputs: list[HalfInputs], report: dict, ours_kit: str
) -> tuple[dict, list[dict]]:
    """(index, chunks) for a game: one HalfInputs per half, in order, and the merged report."""
    halves = [load_half(inp, i + 1, ours_kit) for i, inp in enumerate(inputs)]
    events, bad = moments(halves, report.get('events') or [])
    if events and bad > MAX_MISMATCH * len(events):
        raise ValueError(
            f'{game}: {bad} of {len(events)} moments have a team unlike their track; '
            f'--ours {ours_kit} disagrees with the stats'
        )
    all_chunks = [c for h in halves for c in chunks(h)]
    index = {
        'schema': 1,
        'game': game,
        'hz': HZ,
        'pitch': {'length_m': halves[0].length_m, 'width_m': halves[0].width_m},
        'kits': halves[0].kits,
        'halves': [
            {
                'tag': h.tag,
                'period': h.period,
                'fps': h.fps,
                'f0': h.f0,
                'f1': h.f1,
                'step': h.step,
                'chunks': [c['id'] for c in all_chunks if c['tag'] == h.tag],
                'holds': h.hold_frame is not None,
            }
            for h in halves
        ],
        'possession': {'bucket_s': BUCKET_S, 'halves': [possession_buckets(h) for h in halves]},
        'moments': events,
        'team_mismatch': bad,
    }
    return index, all_chunks


def write(out: Path, index: dict, all_chunks: list[dict]) -> list[Path]:
    """index.json and one <id>.json per chunk under `out`."""
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / 'index.json']
    paths[0].write_text(json.dumps(index, separators=(',', ':')), encoding='utf-8')
    for c in all_chunks:
        p = out / f'{c["id"]}.json'
        p.write_text(json.dumps(c, separators=(',', ':')), encoding='utf-8')
        paths.append(p)
    return paths
