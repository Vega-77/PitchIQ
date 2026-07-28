"""The whole pipeline in one place: video in, match report out.

    STATUS — WRITTEN BUT NOT VERIFIED ON REAL FOOTAGE.

Every component below is individually tested, and the joins between them are
tested against synthetic data. What has never happened is a run end to end on a
real match, because that needs a calibration, and calibration needs footage from
a camera that holds still. See ROADMAP.md Phase 4.

So treat this as the shape of the answer rather than the answer. The parts most
likely to need work once real footage exists are flagged inline with UNVERIFIED.
Nothing here should be shown to a coach until it has produced numbers somebody
has sanity-checked against a match they actually watched.

    from cv.pipeline import analyse_match
    report = analyse_match('match.mp4', calibration_path='home.calib.json')
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .ball import BallTrajectory, build_trajectory, candidates_from_detections
from .calibration import Calibration
from .detector import PersonBallDetector
from .metrics import (
    MovementStats,
    PositionSeries,
    heatmap,
    movement_stats,
    smooth_positions,
    team_shape,
)
from .pitch import MatchOrientation, Pitch
from .possession import PossessionSummary, build_states, summarise
from .teams import TEAM_A, TEAM_B, UNKNOWN, assign_teams, separation, shirt_colour
from .tracking import PlayerTracker, Track, drop_short_tracks, split_by_label

# Below this the possession split is drawn from too little play to mean
# anything; see cv/experiments/possession_report.py for how this was measured.
MIN_CLEAR_HOLDER_SHARE = 0.25

# Kits closer than this in chroma cannot be told apart reliably.
MIN_KIT_SEPARATION = 25.0


@dataclass
class PlayerReport:
    """One tracked player. Identity is a track id, not a name.

    Mapping a track to a real player is a human job (ROADMAP Phase 11), and at
    the fragmentation currently measured a single player spans several tracks —
    so these are fragments of players until a reviewer merges them.
    """

    track_id: int
    team: str
    movement: MovementStats
    heatmap: np.ndarray | None = None
    minutes_tracked: float = 0.0


@dataclass
class MatchReport:
    source: str
    duration_s: float
    processing_s: float

    players: list[PlayerReport] = field(default_factory=list)
    possession: PossessionSummary | None = None
    shape: dict[str, float] = field(default_factory=dict)
    ball: BallTrajectory | None = None

    kit_separation: float = 0.0
    clear_holder_share: float = 0.0
    calibration_error_m: float | None = None

    warnings: list[str] = field(default_factory=list)

    @property
    def is_trustworthy(self) -> bool:
        return not self.warnings

    def summary(self) -> str:
        lines = [
            f'{self.source}  {self.duration_s:.0f}s',
            f'  players tracked   {len(self.players)}',
            f'  ball coverage     {self.ball.coverage(int(self.duration_s * 30)):.0%}'
            if self.ball else '  ball coverage     n/a',
        ]
        if self.possession:
            lines.append(f'  possession        {self.possession.summary_line()}')
        if self.shape:
            lines.append(
                f'  shape             {self.shape["width_m"]:.0f}m wide, '
                f'{self.shape["depth_m"]:.0f}m deep, '
                f'{self.shape["compactness_m"]:.0f}m compactness'
            )
        if self.calibration_error_m is not None:
            lines.append(f'  calibration       {self.calibration_error_m:.2f}m error')

        if self.warnings:
            lines.append('')
            lines.append('  NOT TRUSTWORTHY:')
            lines.extend(f'    - {w}' for w in self.warnings)

        return '\n'.join(lines)


def analyse_match(
    video_path: str | Path,
    calibration_path: str | Path | None = None,
    start_s: float = 0.0,
    end_s: float | None = None,
    conf: float = 0.25,
    ball_conf: float = 0.08,
    imgsz: int = 1280,
    device: str | int | None = 0,
    orientation: MatchOrientation | None = None,
) -> MatchReport:
    """Run the full pipeline over a video.

    Without a calibration this still returns possession and team split, which
    need only pixels. With one it adds everything expressed in metres.
    """
    video_path = Path(video_path)
    started = time.perf_counter()

    calibration = Calibration.load(calibration_path) if calibration_path else None
    pitch = calibration.pitch if calibration else Pitch()
    orientation = orientation or MatchOrientation()

    report = MatchReport(source=video_path.name, duration_s=0.0, processing_s=0.0)

    # ---- frames ----
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FileNotFoundError(f'Could not open video: {video_path}')

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_s * fps))
    total_frames = int(((end_s or 1e9) - start_s) * fps)

    frames = []
    while len(frames) < total_frames:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()

    if not frames:
        raise ValueError('no frames read from video')

    report.duration_s = len(frames) / fps
    frame_width = frames[0].shape[1]

    # ---- detect ----
    # One pass at the ball's lower threshold; players are filtered back up
    # afterwards, so the model runs once rather than twice.
    detector = PersonBallDetector(conf=ball_conf, imgsz=imgsz, device=device)

    detections_by_frame: dict[int, list] = {}
    timestamps: dict[int, float] = {}
    for i in range(0, len(frames), 16):
        for k, dets in enumerate(detector.detect_batch(frames[i:i + 16])):
            idx = i + k
            detections_by_frame[idx] = dets
            timestamps[idx] = start_s + idx / fps

    # ---- ball ----
    report.ball = build_trajectory(
        candidates_from_detections(detections_by_frame, timestamps), frame_width
    )
    ball_by_frame = {p.frame_index: p.xy for p in report.ball.points}

    # ---- teams ----
    boxes_by_frame: dict[int, list] = {}
    colour_samples: dict[int, list] = {}

    for idx, dets in detections_by_frame.items():
        people = [d for d in dets if d.label == 'person' and d.confidence >= conf]
        boxes = []
        for n, det in enumerate(people):
            pseudo_id = idx * 1000 + n
            boxes.append((pseudo_id, det.xyxy))
            colour = shirt_colour(frames[idx], det.xyxy)
            if colour is not None:
                colour_samples[pseudo_id] = [colour]
        boxes_by_frame[idx] = boxes

    assignment = assign_teams(colour_samples)
    report.kit_separation = separation(assignment)

    if report.kit_separation < MIN_KIT_SEPARATION:
        report.warnings.append(
            f'the two kits are only {report.kit_separation:.0f} apart in colour, '
            'too close to separate reliably'
        )

    # ---- possession ----
    states = build_states(
        sorted(detections_by_frame), ball_by_frame, boxes_by_frame,
        assignment.team_of, timestamps,
    )
    report.possession = summarise(states)

    clear = sum(1 for s in states if s.team in (TEAM_A, TEAM_B))
    report.clear_holder_share = clear / len(states) if states else 0.0

    if report.clear_holder_share < MIN_CLEAR_HOLDER_SHARE:
        report.warnings.append(
            f'a ball holder was identifiable in only '
            f'{report.clear_holder_share:.0%} of frames'
        )

    # ---- everything below needs metres ----
    if calibration is None:
        report.warnings.append(
            'no calibration supplied, so distance, speed and shape are unavailable'
        )
        report.processing_s = time.perf_counter() - started
        return report

    error = calibration.holdout_error() or calibration.error()
    report.calibration_error_m = error.mean_m
    if not error.is_usable:
        report.warnings.append(
            f'calibration error is {error.summary()}, too high to trust positions'
        )

    # UNVERIFIED: tracking is re-run here rather than reusing the detections
    # above, because the tracker needs to see consecutive frames itself. That
    # doubles the model cost. Worth collapsing into one pass once there is real
    # footage to measure the trade-off on.
    tracker = PlayerTracker(conf=conf, imgsz=imgsz, device=device)
    tracks = tracker.run(video_path, start_s=start_s, end_s=end_s, stride=1)
    people_tracks, _ = split_by_label(tracks)
    people_tracks = drop_short_tracks(people_tracks, min_frames=10)

    series_by_track: dict[int, PositionSeries] = {}
    for track_id, track in people_tracks.items():
        series = _project(track, calibration)
        if len(series) < 3:
            continue
        smoothed = smooth_positions(series, window=9)
        series_by_track[track_id] = smoothed

        stats = movement_stats(smoothed)
        report.players.append(PlayerReport(
            track_id=track_id,
            # UNVERIFIED: the pseudo-ids used for colour above are per
            # detection, not per track, so team cannot be read across from
            # them. Once tracking is reliable enough to be worth it, sample
            # colours per track instead and this becomes a real lookup.
            team=UNKNOWN,
            movement=stats,
            heatmap=heatmap(smoothed, pitch.length_m, pitch.width_m),
            minutes_tracked=stats.minutes_tracked,
        ))

    report.shape = team_shape(series_by_track)

    if len(report.players) > 40:
        report.warnings.append(
            f'{len(report.players)} tracks for a match with ~22 players — '
            'per-player figures are fragments, not totals'
        )

    report.processing_s = time.perf_counter() - started
    return report


def _project(track: Track, calibration: Calibration) -> PositionSeries:
    """Track boxes to pitch metres via the ground point of each box."""
    if not track.boxes:
        return PositionSeries(track.track_id, np.empty(0), np.empty((0, 2)))

    pixels = np.array([b.ground_point for b in track.boxes], dtype=np.float64)
    times = np.array([b.timestamp_s for b in track.boxes], dtype=np.float64)
    return PositionSeries(track.track_id, times, calibration.to_pitch_many(pixels))
