"""Players and ball in one table: a half's top-down map plus its v17 ball, both in metres.

    from pitchiq.map_table import MapTable
    t = MapTable.load('wb1')
    rows = t.players_at(30000)           # the map's dots on that frame, as columns
    x, y, src = t.ball_at(30000)         # metres; NaN and src 0 where there is no ball

Players: PitchIQ-data/stats/map/<tag>.npz (`experiments/wholemap.py`), every dot of every
frame: x along the pitch (0..length) and y across it (0..width) in metres, trk, team (0 / 1,
2 unknown), role (0 player, 1 keeper, 2 referee, 3 anyone else), seen (detected on that
frame, else the map's guess) and alpha (how sure the guess is), slot (formation slot).

Ball: `balltrack/h3d_v17/<tag>_ball3dd.npz` (StatsBomb units, 120 x 80, on the same pitch
axes) with `evaluation/p2d/v17/<tag>.npz` for its source (src 1 a tracked detector pick, 2 a
pick filling a gap, 3 a TAPNext bridge, 0 no ball), scaled to metres by the map's pitch size.
A ring the 3D chain could not place has src > 0 and NaN metres (15% of wb1's rings, two thirds
of them off the pitch: out of play or in the air); the stats treat it as no ball, as
`PannedCamera.from_tracker` always has.
Both are placed by the same camera (`balltrack/camera.py`; the v17 ball's own smoothed copy
of it differs by under a micrometre on one frame of wb1), so a ball at a player's feet in
the video is at that player's dot here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
MAPS = REPO.parent / 'PitchIQ-data' / 'stats' / 'map'
TRACK = REPO / 'pitchiq' / 'balltrack'
VER = 'v17'
PLAYER_COLS = ('f', 'trk', 'x', 'y', 'seen', 'alpha', 'team', 'role', 'slot')
OBSERVED = (1, 2)  # ball sources that are a detector pick, not a bridge


@dataclass
class MapTable:
    tag: str
    fps: float
    length_m: float
    width_m: float
    players: dict[str, np.ndarray]  # PLAYER_COLS, rows in frame order
    ball_f0: int  # the ball arrays' first frame
    ball_x: np.ndarray  # metres along, NaN where there is no ball
    ball_y: np.ndarray  # metres across
    ball_src: np.ndarray  # int8, see the module docstring

    @classmethod
    def load(cls, tag: str, map_path: Path | None = None, track: Path = TRACK) -> 'MapTable':
        """The half's map and ball; FileNotFoundError names what makes a missing file."""
        mp = Path(map_path or MAPS / f'{tag}.npz')
        b3 = track / f'h3d_{VER}' / f'{tag}_ball3dd.npz'
        b2 = track / 'evaluation' / 'p2d' / VER / f'{tag}.npz'
        for p, how in (
            (mp, f'python -m pitchiq.experiments.wholemap {tag}'),
            (b3, f'python -m pitchiq.balltrack.ball17 <game> {tag}'),
            (b2, f'python -m pitchiq.balltrack.ball17 <game> {tag}'),
        ):
            if not p.exists():
                raise FileNotFoundError(f'{tag}: {p} is missing; `{how}` makes it')
        with np.load(mp) as m:
            players = {k: m[k] for k in PLAYER_COLS}
            fps, L, W = float(m['fps']), float(m['length_m']), float(m['width_m'])
        if np.any(np.diff(players['f']) < 0):
            raise ValueError(f'{mp}: rows are not in frame order')
        with np.load(b3) as z3, np.load(b2) as z2:
            if not np.array_equal(z3['abs'], z2['abs']):
                raise ValueError(f'{tag}: the v17 ball 2D and 3D cover different frames')
            f = z3['abs']
            if not np.array_equal(f, np.arange(f[0], f[0] + len(f))):
                raise ValueError(f'{tag}: the v17 ball skips frames')
            src = z2['src'].astype(np.int8)
            has = src > 0
            x = np.where(has, z3['x'] * L / 120.0, np.nan)
            y = np.where(has, z3['y'] * W / 80.0, np.nan)
        return cls(tag, fps, L, W, players, int(f[0]), x, y, src)

    @property
    def frames(self) -> range:
        """The frames the map covers."""
        return range(int(self.players['f'][0]), int(self.players['f'][-1]) + 1)

    def players_at(self, frame: int) -> dict[str, np.ndarray]:
        """The map's dots on one frame."""
        f = self.players['f']
        a, b = np.searchsorted(f, frame, 'left'), np.searchsorted(f, frame, 'right')
        return {k: v[a:b] for k, v in self.players.items()}

    def ball_at(self, frame: int) -> tuple[float, float, int]:
        """(x, y, src) in metres on one frame; (nan, nan, 0) where there is no ball."""
        i = int(frame) - self.ball_f0
        if not 0 <= i < len(self.ball_src):
            return np.nan, np.nan, 0
        return float(self.ball_x[i]), float(self.ball_y[i]), int(self.ball_src[i])

    def ball_seen(self) -> np.ndarray:
        """Per ball frame: the ball is a placed detector pick (not a bridge, not missing)."""
        return np.isin(self.ball_src, OBSERVED) & np.isfinite(self.ball_x)
