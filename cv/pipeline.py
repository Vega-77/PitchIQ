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

from .ball import BallTrajectory, build_trajectory
from .calibration import Calibration
from .events import EventLog, derive_events
from .frames import FrameTable, TrackedFramePass, attach_trajectory
from .frame_sampler import video_info
from .identity import PlayerCluster, merge_tracks
from .keeper import KeeperAssignment, identify_keepers, keeper_reports
from .touches import TouchSequence, segment_touches
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
from .teams import TEAM_A, TEAM_B, assign_teams, separation

# Tracks shorter than this are noise — a detection that flickered for a fraction
# of a second rather than a player who was there.
MIN_TRACK_FRAMES = 10

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

    `movement` is None without a calibration, because distance and speed are
    metres and there are none. Team and minutes survive, since both are readable
    from pixels alone.
    """

    track_id: int
    team: str
    movement: MovementStats | None = None
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

    touches: TouchSequence | None = None
    events: EventLog | None = None
    clusters: list[PlayerCluster] = field(default_factory=list)
    keepers: KeeperAssignment | None = None
    keeper_stats: list = field(default_factory=list)

    kit_separation: float = 0.0
    clear_holder_share: float = 0.0
    calibration_error_m: float | None = None

    # Whether anything here is expressed in metres. Distance, speed, shape and
    # heatmaps all need a homography; without one the tracks still exist and
    # still have teams, they just cannot be measured. Saying which of the two
    # situations produced a missing number is the whole point of this flag —
    # "0 metres covered" and "we could not measure metres" look identical
    # otherwise.
    movement_available: bool = False

    warnings: list[str] = field(default_factory=list)

    @property
    def _ball_frames(self) -> int:
        return max(1, int(self.duration_s * 30))

    @property
    def ball_seen_share(self) -> float:
        """Frames where the ball was actually detected.

        Distinct from `BallTrajectory.coverage`, which counts interpolated
        points too. Only this number says how much of the match was watched;
        the other says how much of it has a position attached, which is not the
        same claim.
        """
        if not self.ball:
            return 0.0
        return self.ball.observed_count / self._ball_frames

    @property
    def ball_filled_share(self) -> float:
        """Frames whose ball position was drawn in between two sightings."""
        if not self.ball:
            return 0.0
        return self.ball.interpolated_count / self._ball_frames

    @property
    def is_trustworthy(self) -> bool:
        return not self.warnings

    def to_json(self, window: dict | None = None, include_touches: bool = False) -> dict:
        """The whole report as JSON-safe data. See cv/report_json.py."""
        from .report_json import build_report_json

        return build_report_json(self, window=window, include_touches=include_touches)

    def summary(self) -> str:
        lines = [
            f'{self.source}  {self.duration_s:.0f}s',
            f'  players tracked   {len(self.players)}',
            # Seen and filled-in are reported separately. Interpolated points
            # are a straight line drawn between two sightings, and a single
            # "coverage 98%" that silently included them read as though the
            # ball had been watched all match.
            f'  ball              {self.ball_seen_share:.0%} seen, '
            f'{self.ball_filled_share:.0%} filled in'
            if self.ball else '  ball              n/a',
        ]
        if not self.movement_available:
            lines.append('  movement          not measured (needs a calibration)')
        if self.touches is not None:
            lines.append(f'  touches           {_touch_line(self.touches)}')
        if self.events is not None:
            lines.append(f'  events            {_event_line(self.events)}')
        if self.clusters:
            lines.append(
                f'  players merged    {len(self.clusters)} from '
                f'{sum(len(c.track_ids) for c in self.clusters)} tracks'
            )
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


def _touch_line(touches: TouchSequence) -> str:
    if not touches.touches:
        return '0 found'
    return (
        f'{len(touches.touches)}, confidence p50 '
        f'{touches.confidence_percentile(50):.2f} '
        f'p10 {touches.confidence_percentile(10):.2f}, '
        f'{len(touches.gaps)} unseen spans'
    )


def _event_line(events: EventLog) -> str:
    if not len(events):
        return '0 derived'
    from .events import CARRY, DUEL, INTERCEPTION, RECOVERY, TACKLE

    passes = events.passes()
    completed = sum(1 for p in passes if p.outcome == 'completed')
    return (
        f'{len(passes)} passes ({completed} completed), '
        f'{len(events.shots())} shots, '
        f'{len(events.by_type(TACKLE, INTERCEPTION, RECOVERY, DUEL))} defensive, '
        f'{len(events.by_type(CARRY))} carries'
    )


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
    tracker: str = 'botsort.yaml',
    stride: int = 1,
    period: str = 'first_half',
    side_of_team: dict[str, str] | None = None,
) -> MatchReport:
    """Run the full pipeline over a video.

    Without a calibration this still returns possession, the team split and the
    tracks themselves, which need only pixels. With one it adds everything
    expressed in metres.

    Speed and fragmentation, on the same 15s 720p window (450 frames, RTX
    4060). Three fresh processes each, one model per process, medians given
    with the observed spread:

        two passes   detect 8.0s + track 29.9s = 37.8s   (37.1-49.0)  146 tracks
        one pass                                17.1s    (17.0-18.4)  125 tracks

    So roughly 2.2x faster. Two things produce that. The duplicated decode and
    duplicated inference are gone; and the surviving inference is batched,
    where `model.track()` forces one frame at a time. That second point is also
    why the old path's spread is so much wider than the new one's — anything
    that perturbs per-call overhead gets multiplied by 450 there and by 29
    here.

    Fragmentation came down slightly rather than up, which is the ByteTrack
    bargain: detection now runs at the ball's lower threshold, and those extra
    weak boxes go to the tracker, which uses them to re-associate tracks it
    already has while still refusing to create tracks from them. The old
    arrangement detected at 0.25 for tracking and threw that away.

    125 is still far more than the 22 people on a pitch, so per-track figures
    remain fragments. `cv/identity.py` merges them — on this window, into 37
    clusters — and a human confirms the result. That made the problem smaller,
    not absent.

    `stride` is the remaining speed lever: process every Nth frame. It used to
    apply to tracking only; now that there is one pass it skips detection too,
    so it costs ball coverage as well as identity. Since ball coverage is what
    bounds every event statistic downstream, raise it only when a run is too
    slow to finish at all.

        botsort.yaml,   stride 1      100 tracks, longest 449/450
        bytetrack.yaml, stride 1      119 tracks, longest 418
        botsort.yaml,   stride 2       70 tracks, longest 225/225

    The defaults keep the slower, better option: fragmentation is already the
    weakest link, so halving the run time by fragmenting further would be
    optimising the wrong thing.

    (`track_imgsz` is gone. It set a separate image size for the tracking pass,
    and there is no longer a separate pass for it to size.)
    """
    video_path = Path(video_path)
    started = time.perf_counter()

    calibration = Calibration.load(calibration_path) if calibration_path else None
    pitch = calibration.pitch if calibration else Pitch()
    orientation = orientation or MatchOrientation()

    report = MatchReport(source=video_path.name, duration_s=0.0, processing_s=0.0)

    # ---- one streamed pass: detect, track, sample colours ----
    #
    # Nothing here keeps a frame beyond the batch it arrived in. An earlier
    # version read the whole window into a list first, which is 2.8 MB per 720p
    # frame — about 5 GB a minute, and roughly 227 GB for a 45-minute half. It
    # could analyse a clip and could never analyse a match.
    #
    # The constraint that shapes the loop inside TrackedFramePass: shirt colour
    # needs pixels, so it has to be sampled there rather than in a later pass
    # over the detections. What survives a batch is a few numbers per detection,
    # not the image.
    info = video_info(video_path)
    fps = info.fps or 30.0

    single_pass = TrackedFramePass(
        conf=conf, ball_conf=ball_conf, imgsz=imgsz, device=device, tracker=tracker,
    )
    table, colour_samples, ball_candidates = single_pass.run(
        video_path,
        fps=fps,
        frame_width=info.width,
        frame_height=info.height,
        batches=_stream_batches(video_path, fps, start_s, end_s),
        stride=stride,
    )

    if not table.records:
        raise ValueError('no frames read from video')

    table.calibration = calibration
    report.duration_s = len(table.records) * stride / fps

    # ---- ball ----
    report.ball = build_trajectory(ball_candidates, info.width)
    attach_trajectory(table, report.ball)

    # ---- teams ----
    #
    # Keyed on real track ids, which is the whole point of the single pass: a
    # team is a property of a player over time, and the previous arrangement
    # could only ever answer it per detection.
    assignment = assign_teams(colour_samples)
    table.team_by_track = assignment.by_track
    report.kit_separation = separation(assignment)

    if report.kit_separation < MIN_KIT_SEPARATION:
        report.warnings.append(
            f'the two kits are only {report.kit_separation:.0f} apart in colour, '
            'too close to separate reliably'
        )

    # ---- possession ----
    ball_by_frame = {
        r.frame_index: r.ball_xy for r in table.records if r.ball_xy is not None
    }
    states = build_states(
        [r.frame_index for r in table.records],
        ball_by_frame,
        {r.frame_index: r.boxes() for r in table.records},
        table.team_of,
        {r.frame_index: r.timestamp_s for r in table.records},
    )
    report.possession = summarise(states)

    clear = sum(1 for s in states if s.team in (TEAM_A, TEAM_B))
    report.clear_holder_share = clear / len(states) if states else 0.0

    if report.clear_holder_share < MIN_CLEAR_HOLDER_SHARE:
        report.warnings.append(
            f'a ball holder was identifiable in only '
            f'{report.clear_holder_share:.0%} of frames'
        )

    # ---- identity ----
    #
    # Fragments back into people, so per-player figures have somebody to belong
    # to. This does not name anyone — that stays a human job — but it turns a
    # hundred tracks into a list short enough for a coach to work through.
    report.clusters = merge_tracks(table, colour_samples)

    # ---- keepers ----
    defending_ends = _defending_ends(orientation, side_of_team, period)
    report.keepers = identify_keepers(table, defending_ends)

    # ---- touches, and everything built on them ----
    report.touches = segment_touches(table)
    report.events = derive_events(
        report.touches, table,
        pitch=pitch,
        orientation=orientation,
        period=period,
        side_of_team=side_of_team,
        keeper_tracks=report.keepers.all_tracks(),
    )
    if defending_ends and report.keepers.all_tracks():
        report.keeper_stats = keeper_reports(
            report.events, report.keepers, pitch, defending_ends
        )

    if report.touches is not None and not report.touches.touches:
        report.warnings.append(_no_touches_reason(report))

    # ---- players ----
    #
    # The tracks exist either way — they came out of the pass above. What a
    # calibration adds is the ability to measure them.
    sightings = _sightings_by_track(table)

    if calibration is None:
        report.warnings.append(
            'no calibration supplied, so distance, speed and shape are unavailable'
        )
        for track_id, (times, _) in sorted(sightings.items()):
            report.players.append(PlayerReport(
                track_id=track_id,
                team=table.team_of(track_id),
                minutes_tracked=(times[-1] - times[0]) / 60.0,
            ))
        _warn_on_fragmentation(report)
        report.processing_s = time.perf_counter() - started
        return report

    error = calibration.holdout_error() or calibration.error()
    report.calibration_error_m = error.mean_m
    report.movement_available = True
    if not error.is_usable:
        report.warnings.append(
            f'calibration error is {error.summary()}, too high to trust positions'
        )

    series_by_track: dict[int, PositionSeries] = {}
    for track_id, (times, ground) in sorted(sightings.items()):
        if len(times) < 3:
            continue
        series = PositionSeries(
            track_id=track_id,
            timestamps_s=times,
            positions_m=calibration.to_pitch_many(ground),
        )
        smoothed = smooth_positions(series, window=9)
        series_by_track[track_id] = smoothed

        stats = movement_stats(smoothed)
        report.players.append(PlayerReport(
            track_id=track_id,
            team=table.team_of(track_id),
            movement=stats,
            heatmap=heatmap(smoothed, pitch.length_m, pitch.width_m),
            minutes_tracked=stats.minutes_tracked,
        ))

    report.shape = team_shape(series_by_track)

    # A team cannot be wider than the pitch or deeper than it is long. If it
    # comes out that way the homography is wrong, or the camera moved after it
    # was fitted — either way the metres are fiction, and saying so beats
    # printing "126m deep" on a 105m pitch with a straight face.
    if report.shape:
        impossible = [
            f'{report.shape["width_m"]:.0f}m wide on a {pitch.width_m:.0f}m pitch'
            if report.shape['width_m'] > pitch.width_m * 1.1 else '',
            f'{report.shape["depth_m"]:.0f}m deep on a {pitch.length_m:.0f}m pitch'
            if report.shape['depth_m'] > pitch.length_m * 1.1 else '',
        ]
        impossible = [x for x in impossible if x]
        if impossible:
            report.warnings.append(
                'team shape is physically impossible (' + '; '.join(impossible)
                + ') — the calibration does not match this footage'
            )

    _warn_on_fragmentation(report)

    report.processing_s = time.perf_counter() - started
    return report


def _defending_ends(
    orientation: MatchOrientation,
    side_of_team: dict[str, str] | None,
    period: str,
) -> dict[str, str]:
    """Which goal each colour cluster is protecting this period.

    Empty unless somebody has said which cluster is which side. Vision can tell
    two kits apart; it has no way to know which one is the home team, and
    without that there is no way to know which goal anyone is defending.
    """
    if not side_of_team:
        return {}
    return {
        team: orientation.defending_end(side, period)
        for team, side in side_of_team.items()
        if side in ('us', 'them')
    }


def _no_touches_reason(report: MatchReport) -> str:
    """Say *why* nothing was found, rather than assuming.

    An earlier version of this warning asserted the ball had been detected in a
    minority of frames — which was sometimes flatly contradicted by the ball
    figure printed two lines above it. There are two quite different failures
    here and they need different fixes: not seeing the ball is a detector
    problem, and seeing something that is never near a player means those
    detections are not the ball at all.
    """
    tail = ' Every event count below is therefore zero.'

    if report.ball_seen_share < 0.5:
        return (
            f'no touches found — the ball was only detected in '
            f'{report.ball_seen_share:.0%} of frames, so there was little to '
            f'work from.{tail}'
        )
    if report.clear_holder_share < MIN_CLEAR_HOLDER_SHARE:
        return (
            f'no touches found — the ball was detected in '
            f'{report.ball_seen_share:.0%} of frames, but almost never near a '
            'player, which usually means those detections are something else.'
            + tail
        )
    return (
        'no touches found despite usable ball and player coverage — worth '
        'checking against the video before trusting the thresholds.' + tail
    )


def _warn_on_fragmentation(report: MatchReport) -> None:
    """Say so when there are far more tracks than there were players.

    A match has about 22 people on the pitch. Many more tracks than that means
    one player is being counted as several, and every per-track figure in the
    report is a fragment of a player rather than a player.
    """
    if len(report.players) > 40:
        report.warnings.append(
            f'{len(report.players)} tracks for a match with ~22 players — '
            'per-player figures are fragments, not totals'
        )


def _sightings_by_track(table: FrameTable) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Group the frame table into per-track (timestamps, ground points).

    Ground points rather than box centres: a homography maps the plane the
    players stand on, so projecting the middle of a body puts them several
    metres up-pitch of where they are.
    """
    times: dict[int, list[float]] = {}
    points: dict[int, list[tuple[float, float]]] = {}

    for record in table.records:
        for box in record.player_boxes():
            times.setdefault(box.track_id, []).append(record.timestamp_s)
            points.setdefault(box.track_id, []).append(box.ground_point)

    return {
        track_id: (
            np.array(stamps, dtype=np.float64),
            np.array(points[track_id], dtype=np.float64),
        )
        for track_id, stamps in times.items()
        if len(stamps) >= MIN_TRACK_FRAMES
    }


# Frames per decode batch. Sized so the model gets enough work to keep the GPU
# busy while the memory held at any moment stays a fixed cost — 16 720p frames
# is about 45 MB, whatever the length of the match.
BATCH_FRAMES = 16


def _stream_batches(
    video_path: Path,
    fps: float,
    start_s: float,
    end_s: float | None,
):
    """Yield (absolute_frame_index, frames), letting each batch go out of scope.

    Indices are absolute frame numbers in the source video, not offsets into the
    window, because seeking does not land where you ask. cv2's
    CAP_PROP_POS_FRAMES seek goes to the nearest keyframe — asking for frame 30
    of a compressed clip can land on 26 — so a caller that assumed the first
    frame sat exactly at start_s would date every event a fraction of a second
    wrong. That matters here: this output has to line up with a hand-tagged
    match log. Reporting the true frame number lets the caller compute honest
    timestamps.

    Reads sequentially rather than seeking per frame: consecutive reads are what
    a decoder is fast at, and every frame in the window is wanted anyway.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FileNotFoundError(f'Could not open video: {video_path}')

    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_s * fps))
        # Where the seek actually landed, which is not necessarily where it was
        # sent. Everything downstream is dated from this.
        index = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        last_wanted = int((end_s * fps)) if end_s is not None else None

        batch: list[np.ndarray] = []
        batch_start = index
        while last_wanted is None or index < last_wanted:
            ok, frame = cap.read()
            if not ok:
                break
            batch.append(frame)
            index += 1
            if len(batch) == BATCH_FRAMES:
                yield batch_start, batch
                batch = []
                batch_start = index
        if batch:
            yield batch_start, batch
    finally:
        cap.release()
