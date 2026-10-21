"""One pass over the video, holding everything a frame knows at once.

`TrackedFramePass` decodes the video once, detects in batches, and drives the
tracker itself (`build_tracker`), so each `FrameRecord` in the resulting
`FrameTable` holds the ball, the players with persistent track ids
(`PlayerBox`), and shirt colours sampled against those ids. The ball is kept out
of the tracker, which in ultralytics would filter it out of the results.
`attach_trajectory` puts the built ball trajectory back on the frames.

Detection runs at the ball's low threshold and those boxes reach the tracker
too; ByteTrack only creates tracks above `new_track_thresh`, so low boxes can
rescue a track but not invent one. Limit: tracks are still fragments of players
(several per person) until `pitchiq/identity.py` merges them.

Design notes, measurements and history: `docs/design/frame_table.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .ball import BallCandidate
from .calibration import Calibration
from .detector import CLASS_BALL, CLASS_PERSON, DEFAULT_WEIGHTS, PersonBallDetector
from .possession import median_player_height
from .teams import UNKNOWN, kit_sample
from .player_thumbnails import Thumb, consider

# Columns of FrameRecord.players.
COL_TRACK_ID, COL_X1, COL_Y1, COL_X2, COL_Y2, COL_CONF = range(6)
PLAYER_COLUMNS = 6

# How often to sample a shirt colour, in frames. Colour is stable over a track,
# so sampling every frame buys nothing but time; the clustering downstream takes
# a median over whatever it is given.
COLOUR_EVERY = 5

# Colour samples kept per track. A cap matters because one long track would
# otherwise dominate the k-means against fifty short ones.
MAX_COLOUR_SAMPLES = 40


@dataclass(frozen=True)
class PlayerBox:
    """One tracked player in one frame, unpacked from the array."""

    track_id: int
    xyxy: tuple[float, float, float, float]
    confidence: float

    @property
    def ground_point(self) -> tuple[float, float]:
        """Where the player meets the pitch — the only point a homography maps."""
        x1, _, x2, y2 = self.xyxy
        return ((x1 + x2) / 2, y2)

    @property
    def height_px(self) -> float:
        return self.xyxy[3] - self.xyxy[1]


@dataclass
class FrameRecord:
    """One frame: everything downstream needs, nothing it does not.

    Players are held as an (N, 6) float32 array rather than a list of
    dataclasses. Twenty-two players at 30fps across a 45-minute half is 1.8
    million rows — 43 MB as float32, and roughly ten times that as Python
    objects. The pipeline already learned once that per-frame retention has to
    be costed rather than assumed (see the streaming rewrite of the decode
    loop); this is the same lesson applied to the output instead of the input.
    """

    frame_index: int  # absolute frame number in the source video
    timestamp_s: float
    players: np.ndarray  # (N, 6): track_id, x1, y1, x2, y2, conf
    ball_xy: tuple[float, float] | None = None
    ball_observed: bool = False
    ball_confidence: float = 0.0

    def __len__(self) -> int:
        return int(self.players.shape[0])

    def boxes(self) -> list[tuple[int, tuple[float, float, float, float]]]:
        """[(track_id, xyxy)] — the shape possession.frame_holder already takes."""
        return [
            (
                int(row[COL_TRACK_ID]),
                (
                    float(row[COL_X1]),
                    float(row[COL_Y1]),
                    float(row[COL_X2]),
                    float(row[COL_Y2]),
                ),
            )
            for row in self.players
        ]

    def player_boxes(self) -> list[PlayerBox]:
        return [
            PlayerBox(
                track_id=int(row[COL_TRACK_ID]),
                xyxy=(
                    float(row[COL_X1]),
                    float(row[COL_Y1]),
                    float(row[COL_X2]),
                    float(row[COL_Y2]),
                ),
                confidence=float(row[COL_CONF]),
            )
            for row in self.players
        ]

    def track_ids(self) -> list[int]:
        return [int(v) for v in self.players[:, COL_TRACK_ID]]

    @property
    def scale_px(self) -> float:
        """Median player height — the pixel-space scale reference for this frame.

        Delegates to possession.median_player_height rather than recomputing, so
        the two can never drift into disagreeing about what "a player height"
        means on the same frame.
        """
        return median_player_height(self.boxes())


@dataclass
class FrameTable:
    """A window of frames, with the identities and teams that span them."""

    fps: float
    frame_width: int
    frame_height: int
    records: list[FrameRecord] = field(default_factory=list)
    team_by_track: dict[int, str] = field(default_factory=dict)
    # The best picture of each track seen so far, chosen online by pitchiq/player_thumbnails.py.
    # Track-keyed rather than frame-keyed, which is why it sits here beside
    # `team_by_track` instead of being a fourth thing `run` hands back: it is a
    # fact about an identity that spans the window, not about a frame.
    thumb_by_track: dict[int, Thumb] = field(default_factory=dict)
    calibration: Calibration | None = None

    # Built on first use. Records are usually contiguous, but a strided run
    # leaves holes, and event derivation looks frames up constantly enough that
    # a scan would make the whole chain quadratic.
    _index: dict[int, int] | None = field(default=None, repr=False, compare=False)

    def __len__(self) -> int:
        return len(self.records)

    @property
    def duration_s(self) -> float:
        if len(self.records) < 2:
            return 0.0
        return self.records[-1].timestamp_s - self.records[0].timestamp_s

    def team_of(self, track_id: int) -> str:
        return self.team_by_track.get(int(track_id), UNKNOWN)

    def at(self, frame_index: int) -> FrameRecord | None:
        """The record for an absolute frame number, or None if it was not read."""
        if self._index is None:
            self._index = {r.frame_index: i for i, r in enumerate(self.records)}
        position = self._index.get(frame_index)
        return self.records[position] if position is not None else None

    def window(self, start_s: float, end_s: float) -> list[FrameRecord]:
        return [r for r in self.records if start_s <= r.timestamp_s <= end_s]

    def track_ids(self) -> set[int]:
        ids: set[int] = set()
        for record in self.records:
            ids.update(record.track_ids())
        return ids

    def to_pitch(self, xy_px: tuple[float, float]) -> tuple[float, float] | None:
        """Pixels to metres, or None when there is no calibration.

        None rather than a guess: a caller that gets a number back has no way to
        tell an estimate from a measurement, and every metre-based statistic
        downstream would inherit the fiction silently.
        """
        if self.calibration is None:
            return None
        return self.calibration.to_pitch(xy_px[0], xy_px[1])

    def ball_coverage(self) -> float:
        """Share of frames with a ball position, observed or interpolated."""
        if not self.records:
            return 0.0
        return sum(1 for r in self.records if r.ball_xy is not None) / len(self.records)

    def ball_observed_share(self) -> float:
        """Share of frames where the ball was actually detected, not filled in."""
        if not self.records:
            return 0.0
        return sum(1 for r in self.records if r.ball_observed) / len(self.records)


def _empty_players() -> np.ndarray:
    return np.empty((0, PLAYER_COLUMNS), dtype=np.float32)


def build_tracker(tracker: str = 'botsort.yaml', device: str | int | None = None):
    """Construct a standalone ultralytics tracker.

    Mirrors what `trackers.track.on_predict_start` does, because that is the
    only description of how these classes expect to be built. Driving the
    tracker ourselves rather than through `model.track()` is what keeps the ball
    — see the module docstring.
    """
    from ultralytics.trackers.track import TRACKER_MAP
    from ultralytics.utils import YAML, IterableSimpleNamespace
    from ultralytics.utils.checks import check_yaml

    cfg = IterableSimpleNamespace(**YAML.load(check_yaml(tracker)))
    cfg.device = device
    if cfg.tracker_type not in TRACKER_MAP:
        raise ValueError(
            f'unsupported tracker {cfg.tracker_type!r}; expected one of {sorted(TRACKER_MAP)}'
        )
    return TRACKER_MAP[cfg.tracker_type](args=cfg)


class TrackedFramePass:
    """One decode, one batched inference, a tracker we drive ourselves."""

    def __init__(
        self,
        weights: str | Path = DEFAULT_WEIGHTS,
        conf: float = 0.25,
        ball_conf: float = 0.08,
        imgsz: int = 1280,
        device: str | int | None = None,
        tracker: str = 'botsort.yaml',
        colour_every: int = COLOUR_EVERY,
        max_colour_samples: int = MAX_COLOUR_SAMPLES,
        detector=None,
        tracker_factory=None,
    ) -> None:
        # Detection runs at the ball's threshold and players are filtered back
        # up afterwards, so the model sees each frame once.
        #
        # `detector` and `tracker_factory` exist so the assembly below can be
        # tested without a GPU: everything this class does with a frame is
        # bookkeeping, and bookkeeping is exactly the part worth pinning.
        self.detector = detector or PersonBallDetector(
            weights=weights, conf=ball_conf, imgsz=imgsz, device=device
        )
        self._tracker_factory = tracker_factory or build_tracker
        self.conf = conf
        self.ball_conf = ball_conf
        self.tracker_name = tracker
        self.device = device
        self.colour_every = max(1, colour_every)
        self.max_colour_samples = max_colour_samples

    def run(
        self,
        video_path: str | Path,
        fps: float,
        frame_width: int,
        frame_height: int,
        batches,
        stride: int = 1,
    ) -> tuple[FrameTable, dict[int, list[np.ndarray]], dict[int, list[BallCandidate]]]:
        """Consume a stream of (absolute_first_index, frames) batches.

        Takes the batch generator rather than opening the video itself so that
        the decode loop stays in one place — `pipeline._stream_batches` is where
        the seek-lands-on-a-keyframe problem is already solved.

        Frames must arrive in order and without gaps. BoT-SORT compensates for
        camera motion by comparing each frame to the one before it, so replaying
        a batch out of order does not merely reorder the output, it corrupts the
        motion estimate for everything after it.

        `stride` skips frames before inference, not after — skipping afterwards
        would save nothing, since inference is the cost. Frames are selected
        against the first frame actually decoded rather than against zero, so a
        seek that lands early does not shift which frames get processed.
        """
        stride = max(1, stride)
        tracker = self._tracker_factory(self.tracker_name, self.device)
        table = FrameTable(fps=fps, frame_width=frame_width, frame_height=frame_height)
        colour_samples: dict[int, list[np.ndarray]] = {}
        ball_candidates: dict[int, list[BallCandidate]] = {}
        origin: int | None = None

        for first_index, frames in batches:
            if origin is None:
                origin = first_index

            if stride > 1:
                wanted = [i for i in range(len(frames)) if (first_index + i - origin) % stride == 0]
                if not wanted:
                    continue
                indices = [first_index + i for i in wanted]
                frames = [frames[i] for i in wanted]
            else:
                indices = [first_index + i for i in range(len(frames))]

            raw = self.detector.detect_batch_raw(frames)

            for offset, boxes in enumerate(raw):
                absolute = indices[offset]
                timestamp = absolute / fps
                frame = frames[offset]

                classes = boxes.cls.astype(int)
                people = boxes[classes == CLASS_PERSON]
                balls = boxes[classes == CLASS_BALL]

                # Only people reach the tracker. A ball detection at a player's
                # feet has almost no overlap with the player's box, so this is
                # unlikely to matter — but "unlikely" is not a reason to hand
                # the tracker an object it has no way to model.
                tracks = tracker.update(people, frame)
                players = self._as_player_array(tracks)

                self._sample_colours(frame, players, absolute, colour_samples)
                self._sample_thumbs(frame, players, absolute, table.thumb_by_track)

                candidates = self._ball_candidates(balls, absolute, timestamp)
                if candidates:
                    ball_candidates[absolute] = candidates

                table.records.append(
                    FrameRecord(
                        frame_index=absolute,
                        timestamp_s=timestamp,
                        players=players,
                    )
                )

        return table, colour_samples, ball_candidates

    def _as_player_array(self, tracks: np.ndarray) -> np.ndarray:
        """Tracker output to the (N, 6) record layout.

        The tracker returns [x1, y1, x2, y2, track_id, score, cls, idx] per row
        (see STrack.result). Players above the *player* confidence threshold are
        kept: the low-confidence detections were worth handing to the tracker to
        rescue tracks with, but they are not worth reporting as sightings.
        """
        if tracks is None or len(tracks) == 0:
            return _empty_players()

        tracks = np.asarray(tracks, dtype=np.float32)
        keep = tracks[:, 5] >= self.conf
        tracks = tracks[keep]
        if not len(tracks):
            return _empty_players()

        players = np.empty((len(tracks), PLAYER_COLUMNS), dtype=np.float32)
        players[:, COL_TRACK_ID] = tracks[:, 4]
        players[:, COL_X1 : COL_Y2 + 1] = tracks[:, 0:4]
        players[:, COL_CONF] = tracks[:, 5]
        return players

    def _sample_colours(
        self,
        frame: np.ndarray,
        players: np.ndarray,
        absolute: int,
        samples: dict[int, list[np.ndarray]],
    ) -> None:
        """Read shirt colours while the pixels are still in scope.

        This has to happen inside the decode loop for the same reason the old
        pipeline sampled colours there: what survives a batch is a few numbers
        per detection, never the image.
        """
        if absolute % self.colour_every:
            return

        for row in players:
            track_id = int(row[COL_TRACK_ID])
            held = samples.setdefault(track_id, [])
            if len(held) >= self.max_colour_samples:
                continue
            colour = kit_sample(
                frame,
                (
                    float(row[COL_X1]),
                    float(row[COL_Y1]),
                    float(row[COL_X2]),
                    float(row[COL_Y2]),
                ),
            )
            if colour is not None:
                held.append(colour)

    def _sample_thumbs(
        self,
        frame: np.ndarray,
        players: np.ndarray,
        absolute: int,
        thumbs: dict[int, Thumb],
    ) -> None:
        """Keep the best picture of each player, for the same reason as colours.

        Shares `colour_every` rather than getting a cadence of its own. Both are
        asking the same thing of the same pixels — take something small off this
        detection while it still exists — and a second interval would be a
        second thing to tune with no evidence for either setting.
        """
        if absolute % self.colour_every:
            return

        for row in players:
            track_id = int(row[COL_TRACK_ID])
            thumbs[track_id] = consider(
                thumbs.get(track_id),
                frame,
                track_id,
                absolute,
                (
                    float(row[COL_X1]),
                    float(row[COL_Y1]),
                    float(row[COL_X2]),
                    float(row[COL_Y2]),
                ),
            )
            # `consider` returns None only when there was nothing held and this
            # sighting was unusable, and a None entry would look like a track
            # that was considered and rejected forever.
            if thumbs[track_id] is None:
                del thumbs[track_id]

    def _ball_candidates(self, balls, absolute: int, timestamp: float) -> list[BallCandidate]:
        out: list[BallCandidate] = []
        for xyxy, score in zip(balls.xyxy, balls.conf):
            if float(score) < self.ball_conf:
                continue
            x1, y1, x2, y2 = (float(v) for v in xyxy)
            out.append(
                BallCandidate(
                    frame_index=absolute,
                    timestamp_s=timestamp,
                    xy=((x1 + x2) / 2, (y1 + y2) / 2),
                    confidence=float(score),
                )
            )
        return out


def attach_trajectory(table: FrameTable, trajectory) -> None:
    """Write a solved ball trajectory back onto the frame records.

    The trajectory is solved over the whole window at once — the DP in pitchiq/ball.py
    needs to see every candidate before it can choose a path — so the ball
    arrives after the records already exist.
    """
    by_frame = {p.frame_index: p for p in trajectory.points}
    for record in table.records:
        point = by_frame.get(record.frame_index)
        if point is None:
            continue
        record.ball_xy = point.xy
        record.ball_observed = point.observed
        record.ball_confidence = point.confidence
