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
from dataclasses import dataclass, field, replace

import numpy as np

from .teams import TEAM_A, TEAM_B, UNKNOWN

# A fourth label beside the two teams and `unknown`, used only when a tagged log
# says the ball was out of play. Not a team, and deliberately not `unknown`:
# "they were fighting for it" and "the referee had blown" are different things
# and had been indistinguishable in every possession figure to date.
DEAD = 'dead'

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
    # Whether the ball was located at all on this frame. Defaults to True so a
    # hand-built state in a test still means what it looks like it means.
    ball_seen: bool = True
    # Where the ball was, in metres, when there is a calibration to say. Carried
    # here rather than looked up later because the holder and the position are
    # the same fact about the same frame, and joining them back together by
    # timestamp afterwards is an opportunity to get it wrong. Used by
    # cv/territory.py; None without a calibration, which is most runs so far.
    ball_m: tuple[float, float] | None = None


@dataclass
class PossessionSummary:
    team_a_s: float = 0.0
    team_b_s: float = 0.0
    contested_s: float = 0.0
    # A slice of contested_s rather than a bucket beside it — see summarise().
    no_ball_s: float = 0.0
    # Time the tagged log says the ball was out of play. Zero when no log was
    # supplied, which means "nobody told us" and not "there were no stoppages".
    dead_ball_s: float = 0.0
    turnovers: int = 0
    spells: list[tuple[str, float, float]] = field(default_factory=list)

    @property
    def live_s(self) -> float:
        """Time with a clear holder. Contested, ball-missing and dead excluded."""
        return self.team_a_s + self.team_b_s

    def share(self, team: str) -> float:
        if self.live_s <= 0:
            return 0.0
        held = self.team_a_s if team == TEAM_A else self.team_b_s
        return held / self.live_s

    def summary_line(self, name_a: str = 'Team A', name_b: str = 'Team B') -> str:
        if self.live_s <= 0:
            return 'no possession established'
        dead = f', {self.dead_ball_s:.0f}s dead' if self.dead_ball_s else ''
        return (
            f'{name_a} {self.share(TEAM_A) * 100:.0f}% / '
            f'{name_b} {self.share(TEAM_B) * 100:.0f}%  '
            f'({self.live_s:.0f}s live, {self.contested_s:.0f}s contested'
            f'{dead}, {self.turnovers} turnovers)'
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
    is_player=None,
) -> FrameState | tuple:
    """Nearest player to the ball in one frame, if anyone is close enough.

    `boxes` is [(track_id, xyxy)]; `team_of` maps a track id to a team.

    `is_player` filters out anybody `cv/participants.py` decided is not playing,
    and it is applied *before* the scale is measured as well as before the
    search. Both matter. A referee standing next to the ball wins the
    nearest-player test outright; a row of substitutes on the touchline, small
    in a wide frame, drags the median player height down and quietly shrinks the
    possession radius for everyone.
    """
    if ball_xy is None or not boxes:
        return None, UNKNOWN, None

    if is_player is not None:
        boxes = [b for b in boxes if is_player(b[0])]
        if not boxes:
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


def build_states(
    frames, ball_by_frame, boxes_by_frame, team_of, timestamps, is_player=None,
    ball_m_by_frame=None,
) -> list[FrameState]:
    """Per-frame holder for a run of frames.

    `ball_m_by_frame` is the same ball positions projected onto the pitch, when
    there is a calibration to do it with. Optional, and absent on every run
    without one — see `FrameState.ball_m`.
    """
    ball_m_by_frame = ball_m_by_frame or {}
    states: list[FrameState] = []
    for frame_index in frames:
        holder, team, distance = frame_holder(
            ball_by_frame.get(frame_index),
            boxes_by_frame.get(frame_index, []),
            team_of,
            is_player=is_player,
        )
        states.append(FrameState(
            timestamp_s=timestamps.get(frame_index, 0.0),
            holder_track=holder,
            team=team,
            distance_px=distance,
            ball_seen=ball_by_frame.get(frame_index) is not None,
            ball_m=ball_m_by_frame.get(frame_index),
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
        # `replace` rather than a fresh constructor, so a field added to
        # FrameState later cannot be silently dropped here — which is exactly
        # what happened to `ball_m` the first time it was added.
        smoothed.append(replace(state, team=best))

    return smoothed


def prepare_states(
    states: list[FrameState],
    smooth_window: int = 15,
    phases=None,
) -> list[FrameState]:
    """Smooth the team labels, then mark dead-ball frames.

    Public because possession is no longer the only thing that reads these.
    `cv/territory.py` has to see the *same* labels the possession split was
    built from — territory derived from raw per-frame answers while possession
    used smoothed ones would produce two figures about the same half that
    quietly disagree.

    Order matters and is the same as it has always been: a dead ball is a fact
    from the tagged log rather than a noisy estimate, so running it through the
    mode filter would let a busy passage either side vote it away.
    """
    states = smooth_states(states, smooth_window)

    if phases is not None:
        states = [
            state if phases.is_live(state.timestamp_s)
            else replace(state, team=DEAD)
            for state in states
        ]
    return states


def summarise(
    states: list[FrameState],
    min_spell_s: float = MIN_POSSESSION_SPELL_S,
    smooth_window: int = 15,
    phases=None,
) -> PossessionSummary:
    """Aggregate per-frame holders into possession, smoothing out flicker.

    `phases` is a `cv.phases.PhaseTable` already shifted onto the same clock as
    the states. Without one, every stoppage is counted as possession by whoever
    was standing over the ball.
    """
    summary = PossessionSummary()
    if len(states) < 2:
        return summary

    # A caller that has already prepared its states — because it also wanted
    # them for territory — passes `smooth_window=0` and `phases=None`, and this
    # becomes a no-op rather than a second pass over the same data.
    states = prepare_states(states, smooth_window, phases)

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
        elif team == DEAD:
            summary.dead_ball_s += duration
        else:
            summary.contested_s += duration

    summary.spells = merged

    # A turnover is possession passing directly from one team to the other.
    # Contested stretches between them are not counted twice.
    held = [s for s in merged if s[0] in (TEAM_A, TEAM_B)]
    summary.turnovers = sum(
        1 for a, b in zip(held, held[1:]) if a[0] != b[0]
    )

    # How much of the window had no ball in it at all.
    #
    # This is a *diagnostic*, not a fourth bucket: the spells above already tile
    # the whole window, and a frame with no ball resolves to no holder and so is
    # already inside `contested_s`. It is reported separately because those two
    # are very different things to a coach — "both teams were fighting for it"
    # and "we could not see it" read identically in a possession split, and only
    # one of them is football.
    #
    # It used to be computed as span minus the other two, which the tiling makes
    # identically zero. That is the sort of number that looks fine forever.
    #
    # Taken as a share of the window rather than a count of frames times a frame
    # duration. The spells above measure the intervals *between* timestamps, of
    # which there is one fewer than there are frames, and a count-based figure
    # here would sit a frame above the window it is supposed to fit inside.
    span = states[-1].timestamp_s - states[0].timestamp_s
    unseen = sum(1 for s in states if not s.ball_seen)
    summary.no_ball_s = span * unseen / len(states)

    return summary


# Territory-by-thirds lives in cv/territory.py, and is still gated on the same
# thing it always was: without a calibration the frame is not a fixed part of
# the pitch, so on a panning camera "the left third" means somewhere different
# every second. What changed is that `FrameState` now carries `ball_m` when
# there is a homography to produce it, so the split can be taken from the same
# per-frame answers the possession figures came from rather than reconstructed
# alongside them.
