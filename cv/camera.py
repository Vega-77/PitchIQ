"""Noticing that the camera moved, after the homography was already fitted.

The calibration is one homography, fitted once, from one frame. Every metre in
this pipeline goes through it: distance covered, top speed, sprint counts, shot
positions, xG, heatmaps, territory, team shape. It is the single assumption the
most numbers rest on, and it rests on the camera not moving.

Cameras move. Somebody walks into the tripod at half-time, a parent nudges it
reaching past, the wind catches a light rig, someone decides the framing looked
better a bit left. `FOOTAGE_DAY.md` asks for a fixed camera and the roadmap
makes it a hard requirement, which is the right rule and not a mechanism: a rule
that is broken by accident, silently, needs something that notices.

    Why this is worse than being wrong.

A stale homography does not fail. It keeps returning coordinates, plausible
ones, and every figure downstream keeps its shape — a player still covers seven
kilometres, a shot still lands somewhere in the box. The numbers are simply
about a pitch that is no longer where the camera thinks it is, and nothing in
the report has ever said which half of a match that applies to.

    Doing it with what is already there.

No new model and no second pass over the pixels. Over a short interval the
**median** displacement of every tracked player is close to zero, because
twenty-two people move in twenty-two directions — one team pressing forward is
matched by the other dropping back, and the median is not the mean, so a single
sprinter cannot drag it. A whole frame shifting moves everybody by the same
vector at once, and the median goes with it.

So: sample the frames, take the median per-track displacement between one
sample and the next, and look for a step. Football produces a small, wandering
median; a bump produces one large value in a single step and then a small one
again from the new position.

A median on its own is not enough, and the tests found the case that proves it.
With exactly half the pitch sprinting one way and the other half still, the
median sits midway between the two groups — half the sprinters' speed, which
lands right on any threshold worth setting. The median describes the middle
player, and the middle player is not the frame.

What actually distinguishes a camera move is **agreement**: when the frame
shifts, *every* track moves by the same vector, not just enough of them to drag
a middle value. So a shift is only called when the median is large **and** most
tracks are individually within a short distance of it. In the half-and-half
sprint nobody is near the median — the still players are half a stride behind it
and the sprinting ones half a stride ahead — so agreement collapses and nothing
is reported. In a real bump agreement is near total.

Everything is measured in **player heights** rather than pixels, so the same
threshold holds for a camera at the halfway line and one up a floodlight, and
for 720p and 4K. `frames.FrameRecord.scale_px` is the same measure the touch
detector and the identity bridge already use.

    What this cannot do, stated because it matters.

It says the calibration went stale, and roughly when. It does **not** say by how
much — recovering that needs the pitch lines re-found in the new framing, which
is `[Stretch] Automatic pitch-line detection` and is not this.

It cannot tell a bump from a deliberate pan or a zoom, and does not try. Both
invalidate the homography exactly as completely, so the distinction would change
nothing about what to do.

It cannot see a *slow* drift — a tripod settling into soft ground over forty
minutes moves the median by a hair per sample, which is indistinguishable from
football and is deliberately not chased. A step is what this finds. A creep is
still invisible, and that is the honest limit of the method rather than a
setting to turn up: lower the threshold far enough and every counter-attack
becomes a camera bump.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .frames import FrameTable

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
        moves = np.array([
            (after[t][0] - before[t][0], after[t][1] - before[t][1]) for t in shared
        ], dtype=float)

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

        shifts.append(Shift(
            frame_index=record.frame_index,
            timestamp_s=record.timestamp_s,
            shift=magnitude,
            dx_heights=float(dx) / scale,
            dy_heights=float(dy) / scale,
            tracks=len(shared),
            agreement=agreement,
        ))

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
