"""Players and ball in one table: a half's top-down map plus its v17 ball, both in metres.

    from pitchiq.map_table import HalfTable
    t = HalfTable.load('wb1')
    rows = t.players_at(30000)           # the map's dots on that frame, as columns
    x, y, src = t.ball_at(30000)         # metres; NaN and src 0 where there is no ball
    frames = t.frame_table()             # the same, as the stats' frames (pipeline.analyse_table)

Players: PitchIQ-data/stats/map/<tag>.npz (`experiments/wholemap.py`), every dot of every
frame: x along the pitch (0..length) and y across it (0..width) in metres, trk, team (0 / 1,
2 unknown), role (0 player, 1 keeper, 2 referee, 3 anyone else), seen (detected on that
frame, else the map's guess) and alpha (how sure the guess is), slot (formation slot).
`map_stats.MapTable` reads the same file for the map's own figures (shape, territory).

Ball: `balltrack/h3d_v17/<tag>_ball3dd.npz` (StatsBomb units, 120 x 80, on the same pitch
axes) with `evaluation/p2d/v17/<tag>.npz` for its source (src 1 a tracked detector pick, 2 a
pick filling a gap, 3 a TAPNext bridge, 0 no ball), scaled to metres by the map's pitch size.
A ring the 3D chain could not place, or calls 'unsure' (seen, but no flight and no ground path
vouches for it: `pitch_registration/merge_air_and_ground.py`, session step 3.3), has src > 0
and NaN metres; the stats treat it as no ball, as `PannedCamera.from_tracker` does. The v17
ball has no other gate (`ball17.lost` is empty for it: its rings carry their own).
Both are placed by the same camera (`balltrack/camera.py`; the v17 ball's own smoothed copy
of it differs by under a micrometre on one frame of wb1), so a ball at a player's feet in
the video is at that player's dot here.

The stats' frames (`frame_table`) are in pitch metres, not pixels. Every player is a box
PLAYER_HEIGHT_M tall standing on its dot, so the "player heights" the stats measure distances
in (possession, touches, pressure, the identity merge) are one fixed length: 1.2 player
heights is 2.16 m wherever the player stands. Before, a player height was the boxes' height
in pixels near the ball, which made the same 1.2 stand for about 2.2 m.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
MAPS = REPO.parent / 'PitchIQ-data' / 'stats' / 'map'
TRACK = REPO / 'pitchiq' / 'balltrack'
VER = 'v17'
PLAYER_COLS = ('f', 'trk', 'x', 'y', 'seen', 'alpha', 'team', 'role', 'slot')
OBSERVED = (1, 2)  # ball sources that are a detector pick, not a bridge
PLAYER_HEIGHT_M = 1.8  # every box's height in the stats' frames: their "player height"
BOX_HALF_WIDTH_M = 0.3
TEAMS_PER_TRACK = 3  # a frame track id is trk * 3 + team (0, 1, 2 grey)
# The map's roles as the stats' participants: players and keepers play, a referee is an
# official (counted as playing, as `participants.ParticipantReport.is_player` always has, with
# no team), and anyone else is off the field.
MAP_ROLES = {0: 'player', 1: 'player', 2: 'official', 3: 'offfield'}


@dataclass
class HalfTable:
    tag: str
    fps: float
    length_m: float
    width_m: float
    players: dict[str, np.ndarray]  # PLAYER_COLS, rows in frame order
    ball_f0: int  # the ball arrays' first frame
    ball_x: np.ndarray  # metres along, NaN where there is no ball
    ball_y: np.ndarray  # metres across
    ball_src: np.ndarray  # int8, see the module docstring
    kit_lab: np.ndarray | None = None  # (2, 3) each team's kit, OpenCV Lab (the windows' median)

    @classmethod
    def load(cls, tag: str, map_path: Path | None = None, track: Path = TRACK) -> 'HalfTable':
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
            kit = np.median(m['kit_lab'], axis=0) if 'kit_lab' in m.files else None
        if np.any(np.diff(players['f']) < 0):
            raise ValueError(f'{mp}: rows are not in frame order')
        with np.load(b3) as z3, np.load(b2) as z2:
            if not np.array_equal(z3['abs'], z2['abs']):
                raise ValueError(f'{tag}: the v17 ball 2D and 3D cover different frames')
            f = z3['abs']
            if not np.array_equal(f, np.arange(f[0], f[0] + len(f))):
                raise ValueError(f'{tag}: the v17 ball skips frames')
            src = z2['src'].astype(np.int8)
            has = (src > 0) & (z3['state'] != 'unsure')
            x = np.where(has, z3['x'] * L / 120.0, np.nan)
            y = np.where(has, z3['y'] * W / 80.0, np.nan)
        return cls(tag, fps, L, W, players, int(f[0]), x, y, src, kit)

    def first(self, seconds: float) -> 'HalfTable':
        """The table cut to its first `seconds` of map frames (a short test window)."""
        f = self.players['f']
        end = np.searchsorted(f, f[0] + round(seconds * self.fps), 'left')
        return replace(self, players={k: v[:end] for k, v in self.players.items()})

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

    # ---- the stats' view of the table (pipeline.analyse_table) ----------------------------------

    def track_ids(self) -> np.ndarray:
        """Per dot, the stats' track id: the map's track and team together. A map track that
        goes grey for a while is a grey track for that stretch, credited to neither team, as
        the map's own figures do (`map_stats`)."""
        return self.players['trk'].astype(np.int64) * TEAMS_PER_TRACK + self.players['team']

    def frame_table(self, seen_only: bool = True, calibration=None):
        """The table as the stats' frames (`frame_table.FrameTable`), one record per frame of
        the map, in pitch metres with the ball attached.

        `seen_only` keeps only the detected dots, as the stats' own tracker had: a guess sits
        where the map thinks someone is, and 30% of wb1's guesses fall inside the picture,
        where the detector should have seen them. `calibration` defaults to `calibration()`.
        """
        from .frame_table import FrameRecord, FrameTable, attach_trajectory

        p = self.players
        keep = p['seen'].astype(bool) if seen_only else np.ones(len(p['f']), bool)
        f, x, y = p['f'][keep], p['x'][keep], p['y'][keep]
        conf = np.where(p['seen'][keep], 1.0, p['alpha'][keep])
        rows = np.stack(
            [
                self.track_ids()[keep],
                x - BOX_HALF_WIDTH_M,
                y - PLAYER_HEIGHT_M,
                x + BOX_HALF_WIDTH_M,
                y,
                conf,
            ],
            axis=1,
        ).astype(np.float32)
        frames = np.arange(self.frames.start, self.frames.stop)
        cuts = np.searchsorted(f, frames, 'left'), np.searchsorted(f, frames, 'right')
        table = FrameTable(fps=self.fps, frame_width=0, frame_height=0)
        table.records = [
            FrameRecord(frame_index=int(fr), timestamp_s=fr / self.fps, players=rows[a:b])
            for fr, a, b in zip(frames, *cuts)
        ]
        table.calibration = calibration if calibration is not None else self.calibration()
        attach_trajectory(table, self.trajectory(table))
        return table

    def calibration(self):
        """Pitch metres -> pitch metres: the frames are already on the pitch. Its error is the
        camera's fit to the paint (`balltrack/camera.py`), the only error left in a dot."""
        from .balltrack.camera import Camera
        from .calibration import CalibrationError
        from .panning_camera import PannedCalibration
        from .pitch import Pitch

        err = Camera(self.tag).error
        return PannedCalibration(
            np.eye(3), Pitch(length_m=self.length_m, width_m=self.width_m), CalibrationError(**err)
        )

    def trajectory(self, table=None):
        """The ball as a `ball.BallTrajectory` in metres: a point wherever it is placed,
        observed where it is a detector pick. Only the table's frames when one is given."""
        from .ball import BallPoint, BallTrajectory

        seen = self.ball_seen()
        i = np.flatnonzero(np.isfinite(self.ball_x))
        if table is not None:
            have = np.array([r.frame_index for r in table.records]) - self.ball_f0
            i = np.intersect1d(i, have)
        return BallTrajectory(
            points=[
                BallPoint(
                    frame_index=int(self.ball_f0 + k),
                    timestamp_s=(self.ball_f0 + k) / self.fps,
                    xy=(float(self.ball_x[k]), float(self.ball_y[k])),
                    observed=bool(seen[k]),
                    confidence=1.0 if seen[k] else 0.0,
                )
                for k in i
            ]
        )

    def teams(self, table, ours_kit: str | None = None):
        """(team_by_track, colour samples, kit separation) for the frames' tracks.

        The map's team 0 / 1 become TEAM_A / TEAM_B, swapped so that TEAM_A is ours when
        `ours_kit` ('lighter' or 'darker', by kit lightness) says which; grey is UNKNOWN.
        Each team track's colour sample is its team's kit, so the identity merge joins
        fragments within a team and never across (the two kits' chroma distance is the
        separation: wb1's are 45 apart, the merge allows 30).
        """
        from .teams import TEAM_A, TEAM_B, UNKNOWN

        if ours_kit not in (None, 'lighter', 'darker'):
            raise ValueError(f"ours_kit must be 'lighter' or 'darker', got {ours_kit!r}")
        lab = self.kit_lab
        ours = 0
        if ours_kit is not None:
            if lab is None:
                raise ValueError(f'{self.tag}: the map has no kit colours to tell ours by')
            lighter = int(np.argmax(lab[:, 0]))
            ours = lighter if ours_kit == 'lighter' else 1 - lighter
        name = {ours: TEAM_A, 1 - ours: TEAM_B, 2: UNKNOWN}
        by_track, colours = {}, {}
        for tid in table.track_ids():
            team = tid % TEAMS_PER_TRACK
            by_track[tid] = name[team]
            if team < 2 and lab is not None:
                colours[tid] = [np.asarray(lab[team], np.float64)]
        separation = float(np.hypot(*(lab[0, 1:] - lab[1, 1:]))) if lab is not None else 0.0
        return by_track, colours, separation

    def participants(self, table):
        """A `participants.ParticipantReport` from the map's roles (MAP_ROLES), not guessed
        from how a track moves: the map already decided who plays (keepers and referees
        first, past-the-line rules). Movement fields are 0: nothing measured them."""
        from .participants import ParticipantReport, ParticipantVerdict

        tids = self.track_ids()
        role = self.players['role']
        role_of = dict(zip(tids.tolist(), role.tolist()))
        counts: dict[int, int] = {}
        for r in table.records:
            for tid in r.players[:, 0].astype(np.int64).tolist():
                counts[tid] = counts.get(tid, 0) + 1
        report = ParticipantReport()
        for tid, n in counts.items():
            mr = int(role_of[tid])
            report.by_track[tid] = ParticipantVerdict(
                track_id=tid,
                role=MAP_ROLES[mr],
                reason=f'the map ({("player", "keeper", "referee", "other")[mr]})',
                sightings=n,
                screen_time_s=n / self.fps,
                spread_ph=0.0,
                travel_ph_per_min=0.0,
                edge_share=0.0,
                off_pitch_share=None,
                kit_known=tid % TEAMS_PER_TRACK < 2,
            )
        return report
