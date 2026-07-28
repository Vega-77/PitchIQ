"""Who has the ball, without needing a calibration.

Possession is the one substantial team statistic reachable from pixels alone:
it only asks which player is nearest the ball, never how far anything is in
metres. That makes it available today, while calibration is blocked on footage
from a camera that does not pan and zoom.

Two problems have to be solved for the answer to mean anything.

**Scale.** "Near the ball" is a distance, and in pixels that distance changes
every time the camera zooms. The radius here is therefore expressed in player
heights, measured from the detections in that same frame, so it tracks the zoom
automatically.

**Flicker.** Two opponents contesting the ball swap nearest-player many times a
second, and counting that raw would report a possession change several times
per second. Possession is held in runs, so a change is only accepted once a
team has held the ball for a minimum spell.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .teams import TEAM_A, TEAM_B, UNKNOWN

# How close a player must be to count as having the ball, in multiples of the
# median player height in that frame. Roughly a stride and a half.
POSSESSION_RADIUS_PLAYER_HEIGHTS = 1.6

# A team must hold the ball this long before the change is believed. Below this
# it is a deflection or a contested moment, not a turnover.
MIN_POSSESSION_SPELL_S = 0.6


@dataclass(frozen=True)
class FrameState:
    timestamp_s: float
    holder_track: int | None
    team: str
    distance_px: float | None


@dataclass
class PossessionSummary:
    team_a_s: float = 0.0
    team_b_s: float = 0.0
    contested_s: float = 0.0
    no_ball_s: float = 0.0
    turnovers: int = 0
    spells: list[tuple[str, float, float]] = field(default_factory=list)

    @property
    def live_s(self) -> float:
        """Time with a clear holder. Contested and ball-missing time excluded."""
        return self.team_a_s + self.team_b_s

    def share(self, team: str) -> float:
        if self.live_s <= 0:
            return 0.0
        held = self.team_a_s if team == TEAM_A else self.team_b_s
        return held / self.live_s

    def summary_line(self, name_a: str = 'Team A', name_b: str = 'Team B') -> str:
        if self.live_s <= 0:
            return 'no possession established'
        return (
            f'{name_a} {self.share(TEAM_A) * 100:.0f}% / '
            f'{name_b} {self.share(TEAM_B) * 100:.0f}%  '
            f'({self.live_s:.0f}s live, {self.contested_s:.0f}s contested, '
            f'{self.turnovers} turnovers)'
        )


def median_player_height(boxes) -> float:
    """Typical player height in this frame — the pixel-space scale reference."""
    heights = [float(b[3]) - float(b[1]) for _, b in boxes]
    heights = [h for h in heights if h > 1]
    return float(np.median(heights)) if heights else 0.0


def frame_holder(
    ball_xy: tuple[float, float] | None,
    boxes,
    team_of,
    radius_player_heights: float = POSSESSION_RADIUS_PLAYER_HEIGHTS,
) -> FrameState | tuple:
    """Nearest player to the ball in one frame, if anyone is close enough.

    `boxes` is [(track_id, xyxy)]; `team_of` maps a track id to a team.
    """
    if ball_xy is None or not boxes:
        return None, UNKNOWN, None

    scale = median_player_height(boxes)
    if scale <= 0:
        return None, UNKNOWN, None

    radius = scale * radius_player_heights

    best_id, best_distance = None, float('inf')
    for track_id, (x1, _, x2, y2) in boxes:
        # Ground point: the ball is on the floor, so compare against the feet
        # rather than the middle of the body.
        ground = ((float(x1) + float(x2)) / 2, float(y2))
        distance = math.dist(ball_xy, ground)
        if distance < best_distance:
            best_distance, best_id = distance, track_id

    if best_id is None or best_distance > radius:
        return None, UNKNOWN, best_distance if best_id is not None else None

    return best_id, team_of(best_id), best_distance


def build_states(frames, ball_by_frame, boxes_by_frame, team_of, timestamps) -> list[FrameState]:
    """Per-frame holder for a run of frames."""
    states: list[FrameState] = []
    for frame_index in frames:
        holder, team, distance = frame_holder(
            ball_by_frame.get(frame_index),
            boxes_by_frame.get(frame_index, []),
            team_of,
        )
        states.append(FrameState(
            timestamp_s=timestamps.get(frame_index, 0.0),
            holder_track=holder,
            team=team,
            distance_px=distance,
        ))
    return states


def smooth_states(states: list[FrameState], window: int = 15) -> list[FrameState]:
    """Mode-filter the per-frame team labels.

    Per-frame answers are noisy from two directions at once: the nearest player
    genuinely alternates when two opponents contest the ball, and shirt-colour
    reading is imperfect on any single frame. Taking the most common label in a
    window around each frame removes both.

    Doing this *before* grouping into spells matters. Filtering short spells
    afterwards instead leaves gaps that then read as contested, which turns a
    normal passage of play into "nobody had the ball for most of the minute".
    """
    if len(states) < window or window < 3:
        return states

    half = window // 2
    labels = [s.team for s in states]
    smoothed: list[FrameState] = []

    for i, state in enumerate(states):
        lo = max(0, i - half)
        hi = min(len(labels), i + half + 1)
        counts: dict[str, int] = {}
        for label in labels[lo:hi]:
            counts[label] = counts.get(label, 0) + 1

        # Ties resolve toward a real team rather than unknown: a window split
        # evenly between "A has it" and "nobody does" is a passage of play, not
        # a stoppage.
        best = max(counts.items(), key=lambda kv: (kv[1], kv[0] != UNKNOWN))[0]
        smoothed.append(FrameState(
            timestamp_s=state.timestamp_s,
            holder_track=state.holder_track,
            team=best,
            distance_px=state.distance_px,
        ))

    return smoothed


def summarise(
    states: list[FrameState],
    min_spell_s: float = MIN_POSSESSION_SPELL_S,
    smooth_window: int = 15,
) -> PossessionSummary:
    """Aggregate per-frame holders into possession, smoothing out flicker."""
    summary = PossessionSummary()
    if len(states) < 2:
        return summary

    states = smooth_states(states, smooth_window)

    # Group consecutive frames sharing a team into candidate spells.
    raw: list[tuple[str, float, float]] = []
    current_team = states[0].team
    spell_start = states[0].timestamp_s

    for previous, state in zip(states, states[1:]):
        if state.team != current_team:
            raw.append((current_team, spell_start, previous.timestamp_s))
            current_team = state.team
            spell_start = state.timestamp_s
    raw.append((current_team, spell_start, states[-1].timestamp_s))

    # Drop spells too short to be real, then merge neighbours that end up
    # adjacent — a two-frame blip inside a long spell should vanish rather than
    # split that spell in two.
    # Absorb any remaining sub-threshold spell into whatever came before it,
    # rather than dropping it. Dropping leaves a hole that later reads as
    # contested — which is how a normal passage of play turns into "nobody had
    # the ball".
    kept: list[tuple[str, float, float]] = []
    for team, start, end in raw:
        too_short = (end - start) < min_spell_s
        if too_short and kept:
            previous = kept[-1]
            kept[-1] = (previous[0], previous[1], end)
            continue
        if kept and kept[-1][0] == team:
            kept[-1] = (team, kept[-1][1], end)
        else:
            kept.append((team, start, end))

    merged: list[tuple[str, float, float]] = []
    for spell in kept:
        if merged and merged[-1][0] == spell[0]:
            merged[-1] = (spell[0], merged[-1][1], spell[2])
        else:
            merged.append(spell)

    for team, start, end in merged:
        duration = max(0.0, end - start)
        if team == TEAM_A:
            summary.team_a_s += duration
        elif team == TEAM_B:
            summary.team_b_s += duration
        else:
            summary.contested_s += duration

    summary.spells = merged

    # A turnover is possession passing directly from one team to the other.
    # Contested stretches between them are not counted twice.
    held = [s for s in merged if s[0] in (TEAM_A, TEAM_B)]
    summary.turnovers = sum(
        1 for a, b in zip(held, held[1:]) if a[0] != b[0]
    )

    total_span = states[-1].timestamp_s - states[0].timestamp_s
    summary.no_ball_s = max(
        0.0, total_span - summary.live_s - summary.contested_s
    )

    return summary


# Territory-by-thirds is deliberately absent. Without a calibration the frame
# is not a fixed part of the pitch, so on a panning camera "the left third"
# means somewhere different every second. It becomes meaningful once Phase 4
# supplies a homography to divide the actual field with.
