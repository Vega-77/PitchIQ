"""Noticing that the camera moved, after the homography was already fitted.

`detect_camera_shift` samples the frames every `SAMPLE_EVERY_S` and calls a
shift when the median per-track displacement between samples is large
(`SHIFT_PLAYER_HEIGHTS`) **and** most tracks agree with it
(`AGREE_WITHIN_HEIGHTS`, `MIN_AGREEMENT`). Distances are in player heights
(`FrameRecord.scale_px`), so the thresholds hold at any resolution or camera
height. `camera_warnings` turns the result into report warnings.

Limits: it says the calibration went stale and roughly when, not by how much;
it cannot tell a bump from a deliberate pan or zoom (both invalidate the
homography); and it cannot see a slow drift, only a step.

Design notes, measurements and history: `docs/design/camera.md`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .frame_table import FrameTable

# How far apart to sample, in seconds. Long enough that a real bump lands inside
# one interval rather than being spread across several and looking like drift;
# short enough that ordinary play cannot accumulate much median displacement.
SAMPLE_EVERY_S = 1.0

# A median shift bigger than this, in player heights, between two samples one
# second apart. A player at a hard sprint covers roughly one and a half player
# heights a second, so a single runner is well inside it — but the *median* of a
# whole pitch is an order of magnitude smaller than any one player, which is
# what leaves this much room above the football.
SHIFT_PLAYER_HEIGHTS = 0.75

# How close a track has to be to the median, in player heights, to count as
# having moved with it. Generous on purpose: players keep playing during the
# frame or two a bump takes, so their displacement is the camera's plus their
# own, and demanding an exact match would only ever fire on an empty pitch.
AGREE_WITHIN_HEIGHTS = 0.5

# And how many of them have to agree. Set above a half deliberately: the case
# this exists to reject is half the pitch breaking one way, and any threshold at
# or below 0.5 would call that a camera move.
MIN_AGREEMENT = 0.7

# Below this many tracks in both frames the median is not a median. Six is
# already generous: with four players a single mis-tracked box moves it.
MIN_TRACKS = 6


@dataclass(frozen=True)
class Shift:
    """One moment the whole picture moved."""

    frame_index: int
    timestamp_s: float
    # In player heights, so it means the same thing at any resolution or camera
    # distance. Signed components are kept because a horizontal nudge and a
    # vertical one are different accidents, and someone reading a log to work
    # out what happened at the field wants to know which.
    shift: float
    dx_heights: float
    dy_heights: float
    tracks: int
    # What share of those tracks moved with the median rather than around it.
    # Carried because it is the difference between "the picture moved" and "a
    # lot of players ran the same way", and a reader deciding whether to believe
    # this deserves the number it was decided on.
    agreement: float

    def to_json(self) -> dict:
        return {
            'frame_index': self.frame_index,
            'timestamp_s': round(self.timestamp_s, 2),
            'shift': round(self.shift, 2),
            'dx': round(self.dx_heights, 2),
            'dy': round(self.dy_heights, 2),
            'tracks': self.tracks,
            'agreement': round(self.agreement, 2),
        }


@dataclass
class CameraMotion:
    """What the camera did during the window, and what that costs."""

    shifts: list[Shift]
    # False when there were never enough tracks to say anything. Distinct from
    # "checked and found nothing", which is the whole point of carrying it: a
    # run that could not look is not a run that looked and was happy.
    checked: bool = True

    @property
    def moved(self) -> bool:
        return bool(self.shifts)

    @property
    def first_s(self) -> float | None:
        """When the metres stopped being trustworthy, or None if they did not."""
        return self.shifts[0].timestamp_s if self.shifts else None

    def to_json(self) -> dict:
        return {
            'checked': self.checked,
            'moved': self.moved,
            'first_s': round(self.first_s, 2) if self.first_s is not None else None,
            'shifts': [s.to_json() for s in self.shifts],
        }


def _ground_points(record) -> dict[int, tuple[float, float]]:
    """Where each tracked player meets the pitch, keyed by track.

    The ground point rather than the box centre, for the same reason the
    homography only ever maps that point: it is the one place on a person that
    is actually on the plane the calibration describes. A centre moves when
    somebody jumps.
    """
    return {box.track_id: box.ground_point for box in record.player_boxes()}


def detect_camera_shift(
    table: FrameTable,
    sample_every_s: float = SAMPLE_EVERY_S,
    threshold_heights: float = SHIFT_PLAYER_HEIGHTS,
    min_tracks: int = MIN_TRACKS,
    agree_within: float = AGREE_WITHIN_HEIGHTS,
    min_agreement: float = MIN_AGREEMENT,
) -> CameraMotion:
    """Moments where the whole picture moved rather than the football.

    Returns every step found, not just the first. A camera knocked once and
    straightened again is two shifts and one ruined stretch in between, which
    reads very differently from one knock that was never corrected.
    """
    records = table.records or []
    if len(records) < 2:
        return CameraMotion(shifts=[], checked=False)

    shifts: list[Shift] = []
    compared = 0
    previous = records[0]

    for record in records[1:]:
        gap = record.timestamp_s - previous.timestamp_s
        if gap < sample_every_s:
            continue

        before = _ground_points(previous)
        after = _ground_points(record)
        shared = sorted(set(before) & set(after))
        previous = record

        if len(shared) < min_tracks:
            continue

        # The scale of the *later* frame. If the camera zoomed, the two frames
        # disagree about what a player height is, and the answer that matters is
        # the one the following frames will be measured in.
        scale = record.scale_px
        if not scale or scale <= 0:
            continue

        compared += 1
        moves = np.array(
            [(after[t][0] - before[t][0], after[t][1] - before[t][1]) for t in shared], dtype=float
        )

        # Median per axis, not the median of the magnitudes. A crowd moving in
        # all directions cancels on each axis; taking magnitudes first would
        # make every direction agree that something moved, and twenty-two
        # players running about would read as a permanent camera shake.
        dx, dy = np.median(moves, axis=0)
        magnitude = float(np.hypot(dx, dy)) / scale
        if magnitude < threshold_heights:
            continue

        # Did the picture move, or did a lot of players run the same way? The
        # median cannot tell those apart — with half the pitch sprinting it
        # lands midway between the two groups and looks exactly like a bump.
        # Agreement can: a frame that shifted takes everybody with it.
        distances = np.hypot(moves[:, 0] - dx, moves[:, 1] - dy) / scale
        agreement = float(np.mean(distances <= agree_within))
        if agreement < min_agreement:
            continue

        shifts.append(
            Shift(
                frame_index=record.frame_index,
                timestamp_s=record.timestamp_s,
                shift=magnitude,
                dx_heights=float(dx) / scale,
                dy_heights=float(dy) / scale,
                tracks=len(shared),
                agreement=agreement,
            )
        )

    return CameraMotion(shifts=shifts, checked=compared > 0)


def camera_warnings(motion: CameraMotion | None, calibrated: bool) -> list[str]:
    """What to say about it, and only when it changes what a reader should do.

    Silent without a calibration. With no homography there are no metres to
    invalidate, and warning that a camera moved on a run that never claimed a
    distance would be noise on every uncalibrated clip.

    Silent, too, when it could not be checked. `trustworthy` is `not warnings`,
    and a fifteen-second clip with four people in it has not earned a verdict
    either way — `quality.camera.checked` carries that fact for anyone who wants
    it, without condemning the run.
    """
    if motion is None or not calibrated or not motion.moved:
        return []

    first = motion.first_s or 0.0
    count = len(motion.shifts)
    when = f'{int(first // 60)}:{int(first % 60):02d}'

    return [
        f'the camera moved at {when} into the footage'
        + (f' and {count - 1} more time{"s" if count > 2 else ""}' if count > 1 else '')
        + ' — the pitch calibration was fitted before that, so every figure in '
        'metres after it describes a pitch the camera is no longer pointed at'
    ]
