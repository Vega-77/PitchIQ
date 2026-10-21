"""A panning camera, turned into the still one the rest of the pipeline assumes.

Everything metre-based in `pitchiq/pipeline.py` was written for one homography per
match, fitted once on a tripod camera. The footage this team actually has is a
Hudl camera that follows the play, and `FOOTAGE_DAY.md` says plainly what that
does to a single homography: breaks it.

The ball tracker in `pitchiq/balltrack` already solved the harder half of this. It
registers every frame to a venue map (`homog/vreg.py`, SIFT against map
keyframes every half second) and fits that map to the pitch once
(`homog/vpitch.py`). So each frame has its own frame -> StatsBomb homography.

What this module does with that is small on purpose. The Hudl camera pans and
tilts about a fixed point, so frame -> frame is a homography for *every* point
in the image, not only the grass. That means every box and ball position can be
moved into one reference view — the venue map's own reference keyframe — and
after that the match looks exactly as if it had been filmed by one camera that
never moved. The pipeline then runs unchanged: one fixed `Calibration` from the
reference view to metres, boxes that keep their shape (a pan rotates them; it
does not stretch them the way a top-down warp would), and pixel-space logic
like possession's player-height scale still means what it meant.

    The ball comes from the tracker, not the pipeline's own detector.

The pipeline's `build_trajectory` is the 43%-coverage path the tracker
replaced. Hybrid 90's answer is used instead: the 3D chain's ground position
(StatsBomb x, y), carried back into the reference view. For a ball in the air
that is the point under it, which is the right thing for possession to compare
with a player's feet and the wrong thing for nothing downstream. Frames the
detector picked are `observed`; TAPNext bridges and the chain's own fills are
not.

    Metres.

The venue map measures the pitch in centre-circle radii, because a radius is
the one length the laws fix (9.15 m) — so the size is not assumed:

    length 12.31 R x 9.15 = 112.7 m,   width 7.74 R x 9.15 = 70.8 m

    Error.

The map's fit to the pitch is the calibration error this reports: 40
landmarks, mean 0.28 m, 90th percentile 0.84 m, max 2.07 m (the far goalpost).
That is the fixed part. Per-frame registration error comes on top, and on the
one minute where it was checked against the 3D chain, ground balls projected
through each frame's homography agreed with it to a median 0.36 and a 90th
percentile 1.46 StatsBomb units (about 0.3 m and 1.3 m).

    from pitchiq.panned import PannedCamera
    camera = PannedCamera.from_tracker('fr00')
    report = analyse_match(video, panned=camera, start_s=..., end_s=...)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .ball import BallPoint, BallTrajectory
from .balltrack import ballgate
from .calibration import Calibration, CalibrationError
from .frame_table import COL_X1, COL_X2, COL_Y1, COL_Y2, FrameTable
from .pitch import STATSBOMB_LENGTH, STATSBOMB_WIDTH, Pitch

TRACKER = Path(__file__).resolve().parent / 'balltrack'

# The one length the laws of the game fix, and the unit the venue map measures
# the pitch in. See `homog/vpitch.py`.
CENTRE_CIRCLE_RADIUS_M = 9.15

# The venue map's fit to the pitch, measured by `homog/vpitch.py` on 40
# landmarks and converted from radii to metres.
MAP_FIT_ERROR = CalibrationError(mean_m=0.28, max_m=2.07, p90_m=0.84)

# Hybrid 90's per-frame source codes (`alltests/hybrid.py`): 1 a tracked
# detector pick, 2 a detector pick filling a gap, 3 a TAPNext bridge.
OBSERVED_SOURCES = (1, 2)


class PannedCalibration(Calibration):
    """Reference view -> metres, with the venue map's error as its error.

    A plain `Calibration` measures itself from clicked correspondences, and
    there are none here: the fit was made once, on the venue map, by
    `homog/vpitch.py`. Reporting NaN would read as "never checked", which is
    not true, and a fabricated set of clicks would report a fit to points that
    were never clicked. So the measured number is carried as it is.
    """

    def __init__(self, homography, pitch: Pitch, error: CalibrationError) -> None:
        super().__init__(homography, pitch)
        self._error = error

    def error(self) -> CalibrationError:
        return self._error

    def holdout_error(self) -> CalibrationError | None:
        return None


@dataclass
class PannedCamera:
    """Per-frame registration onto one reference view, plus the ball in it.

    `to_reference` maps an absolute frame number to that frame's pixel ->
    reference-view homography. `ball_sb` holds the tracker's ball on the
    ground plane, in StatsBomb units, keyed the same way, as (x, y, observed).
    """

    to_reference: dict[int, np.ndarray]
    calibration: PannedCalibration
    ball_sb: dict[int, tuple[float, float, bool]]

    @classmethod
    def from_arrays(
        cls,
        frames: np.ndarray,
        frame_to_sb: np.ndarray,
        reference_to_sb: np.ndarray,
        pitch: Pitch,
        ball_x: np.ndarray | None = None,
        ball_y: np.ndarray | None = None,
        ball_observed: np.ndarray | None = None,
        error: CalibrationError = MAP_FIT_ERROR,
    ) -> 'PannedCamera':
        """Build from the tracker's arrays, all indexed like `frames`.

        A frame whose homography is missing (NaN) is left out rather than
        guessed at: `stabilise` then empties it, and says how many it did.
        """
        sb_to_reference = np.linalg.inv(reference_to_sb)
        to_reference = {}
        for frame, H in zip(np.asarray(frames, dtype=int), frame_to_sb):
            if np.all(np.isfinite(H)):
                M = sb_to_reference @ H
                to_reference[int(frame)] = M / M[2, 2]

        sb_to_m = np.diag(
            [
                pitch.length_m / STATSBOMB_LENGTH,
                pitch.width_m / STATSBOMB_WIDTH,
                1.0,
            ]
        )
        calibration = PannedCalibration(sb_to_m @ reference_to_sb, pitch, error)

        ball_sb = {}
        if ball_x is not None:
            for frame, x, y, seen in zip(frames, ball_x, ball_y, ball_observed):
                if np.isfinite(x) and np.isfinite(y):
                    ball_sb[int(frame)] = (float(x), float(y), bool(seen))
        return cls(to_reference, calibration, ball_sb)

    @classmethod
    def from_tracker(cls, tag: str, version: str = 'hyb90') -> 'PannedCamera':
        """Load what `pitchiq/balltrack/hybrid.sh <lo> <hi> <tag>` left behind.

        Those files are generated, not in git. The chain that makes them:
        the camera's registration (`python -m pitchiq.experiments.autocal_run`,
        which writes `homog/<tag>_reg.npz` and `<tag>_venue.npz`; hybrid.sh
        falls back to `homog/vreg.py` on the scrimmage's venue map), then
        `hybrid.sh` for a window or `half.py` for a half, with PIQ_VIDEO set
        to the match. See `pitchiq/balltrack/README.md`.
        """
        homog = TRACKER / 'homog'
        paths = (
            TRACKER / f'h3d_{version}' / f'{tag}_smooth2.npz',
            TRACKER / f'h3d_{version}' / f'{tag}_ball3dd.npz',
            TRACKER / 'alltests' / 'p2d' / version / f'{tag}.npz',
        )
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            raise FileNotFoundError(
                f'{tag}: the ball tracker has not been run for this tag; missing '
                f'{", ".join(missing)}. Make them with `sh hybrid.sh <lo> <hi> {tag}` '
                f'in pitchiq/balltrack (a window) or `python half.py {tag} <lo> <hi>` '
                '(a half), with PIQ_VIDEO set to the match; pitchiq/balltrack/README.md '
                'has the steps before that.'
            )
        smooth, ball, picks = (np.load(p) for p in paths)
        frames = smooth['abs']
        if not (np.array_equal(ball['abs'], frames) and np.array_equal(picks['abs'], frames)):
            raise ValueError(f'{tag}: tracker outputs cover different frames')

        own = homog / f'{tag}_venue.npz'
        error = MAP_FIT_ERROR
        if own.exists():
            # A camera calibrated in-match by `pitchiq/autocal.py`: its own reference
            # view (the first registered sample) and its own fit to the paint.
            venue = np.load(own)
            reference_to_sb = venue['ref']
            error = CalibrationError(
                mean_m=float(venue['err_mean']),
                max_m=float(venue['err_max']),
                p90_m=float(venue['err_p90']),
            )
        else:
            venue = np.load(homog / 'vpitch.npz')
            top = np.load(homog / 'vtop.npz')
            # The venue map's reference keyframe -> StatsBomb; `homog/vovl.py`'s
            # frame_to_sb for the keyframe whose own map homography is identity.
            reference_to_sb = venue['P'] @ top['T'] @ top['B'] @ np.linalg.inv(top['K'])
        pitch = Pitch(
            length_m=round(float(venue['L']) * CENTRE_CIRCLE_RADIUS_M, 1),
            width_m=round(float(venue['W']) * CENTRE_CIRCLE_RADIUS_M, 1),
        )
        observed = np.isin(picks['src'], OBSERVED_SOURCES)
        # No ball where the classifier is lost for minutes (pitchiq/balltrack/ballgate.py).
        lost = ballgate.lost(tag, int(frames[0]), (picks['src'] > 0) & np.isfinite(picks['x']))
        return cls.from_arrays(
            frames,
            smooth['H'],
            reference_to_sb,
            pitch,
            np.where(lost, np.nan, ball['x']),
            np.where(lost, np.nan, ball['y']),
            observed,
            error,
        )

    @property
    def pitch(self) -> Pitch:
        return self.calibration.pitch

    def stabilise(self, table: FrameTable) -> int:
        """Move every box in the table into the reference view, in place.

        Returns how many frames had players but no registration; their players
        are dropped, because a box left in its own frame's pixels would be
        measured through a homography that belongs to a different view.
        """
        dropped = 0
        for record in table.records:
            H = self.to_reference.get(record.frame_index)
            if H is None:
                if len(record):
                    dropped += 1
                record.players = record.players[:0]
                continue
            if not len(record):
                continue
            rows = record.players.astype(np.float64)
            x1, y1, x2, y2 = (rows[:, c] for c in (COL_X1, COL_Y1, COL_X2, COL_Y2))
            corners = np.stack(
                [
                    np.c_[x1, y1],
                    np.c_[x2, y1],
                    np.c_[x1, y2],
                    np.c_[x2, y2],
                ],
                axis=1,
            ).reshape(-1, 1, 2)
            moved = cv2.perspectiveTransform(corners, H).reshape(-1, 4, 2)
            # Bottom-centre has to land where the feet land, since that is the
            # point every metre in the report is measured from; a pan barely
            # turns a box, so its enclosing rectangle keeps that true.
            rows[:, COL_X1] = moved[:, :, 0].min(axis=1)
            rows[:, COL_X2] = moved[:, :, 0].max(axis=1)
            rows[:, COL_Y1] = moved[:, :, 1].min(axis=1)
            rows[:, COL_Y2] = moved[:, :, 1].max(axis=1)
            record.players = rows.astype(record.players.dtype)
        return dropped

    def trajectory(self, table: FrameTable) -> BallTrajectory:
        """The tracker's ball for the frames in this table, in the reference view."""
        wanted = [r for r in table.records if r.frame_index in self.ball_sb]
        if not wanted:
            return BallTrajectory()
        sb = np.array([self.ball_sb[r.frame_index][:2] for r in wanted])
        sb_to_m = np.diag(
            [
                self.pitch.length_m / STATSBOMB_LENGTH,
                self.pitch.width_m / STATSBOMB_WIDTH,
            ]
        )
        metres = sb @ sb_to_m
        reference = Calibration._apply(np.linalg.inv(self.calibration.H), metres)
        return BallTrajectory(
            points=[
                BallPoint(
                    frame_index=r.frame_index,
                    timestamp_s=r.timestamp_s,
                    xy=(float(x), float(y)),
                    observed=self.ball_sb[r.frame_index][2],
                    confidence=1.0 if self.ball_sb[r.frame_index][2] else 0.0,
                )
                for r, (x, y) in zip(wanted, reference)
            ]
        )
