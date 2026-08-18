"""Passes, shots, tackles — the Opta taxonomy, derived from touches.

Once `cv/touches.py` has produced an ordered list of who touched the ball and
when, most of a match report is arithmetic over adjacent pairs:

    same player again                       a carry
    same team, different player             a completed pass
    different team, ball was in flight      an interception
    different team, ball was controlled     a tackle
    first touch after nobody had it         a recovery
    followed by the ball heading at goal    a shot

That is the whole idea. It also means every number here inherits every error in
the touch list, which is why `confidence` travels with each event rather than
being computed at the end.

    Two things this cannot see, both structural.

**Intent.** Opta defines a pass as a *deliberate* attempt to find a teammate.
Intent is not observable from bounding boxes; all we can see is that the ball
went from A to B. So a deflection that happens to reach a teammate counts as a
completed pass, and a good clearance downfield counts as a failed long one.
Those biases push in opposite directions but they do not cancel, and neither is
tunable — any pass-accuracy figure carries both.

**Height.** The ball's z-axis is unrecoverable from one fixed camera, which is
the same wall `cv/xg_bridge.py` hits on `shot_height`. Aerial duels, headers
and the difference between a goalkeeper's punt and a drilled goal kick all live
behind it. Where that bites, this module reports 'unknown' rather than guessing.

    What needs a calibration, and what does not.

Passes, carries, tackles, interceptions, recoveries, ground duels and pressure
counts all work from pixels alone, because each is a question about *who*
touched the ball and in what order. Anything asking *where* on the pitch —
pass direction, progressive passes, final-third entries, crosses, clearances,
shots — needs metres, and returns None without them.

Pass direction deserves a specific note: there is no honest pixel-space
fallback. On a panning camera the image x-axis is not the pitch x-axis and the
relationship changes every frame, so "forward" is unanswerable rather than
merely imprecise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .frames import FrameTable
from .pitch import MatchOrientation, Pitch
from .teams import TEAM_A, TEAM_B, UNKNOWN
from .touches import Touch, TouchSequence
from . import zones

PASS = 'pass'
CARRY = 'carry'
SHOT = 'shot'
TACKLE = 'tackle'
INTERCEPTION = 'interception'
RECOVERY = 'recovery'
CLEARANCE = 'clearance'
DUEL = 'duel'

COMPLETED = 'completed'
INCOMPLETE = 'incomplete'
UNKNOWN_OUTCOME = 'unknown'

GOAL = 'goal'
SAVED = 'saved'
BLOCKED = 'blocked'
OFF_TARGET = 'off_target'

# Below this the ball was being carried rather than played — the discriminator
# between a tackle (took it off someone in control) and an interception (read a
# pass in flight). In player heights per second, so it survives the zoom.
CONTROLLED_SPEED_PH_S = 4.0

# A ball nobody touched for this long has gone loose; whoever takes it next has
# made a recovery rather than received a pass.
LOOSE_BALL_S = 1.5

# Two opposing players touching this close together in time and space were
# contesting the ball, not exchanging it.
DUEL_WINDOW_S = 0.5
DUEL_DISTANCE_PH = 1.5

# An opponent within this many player heights of the player on the ball counts
# as pressure.
PRESSURE_PH = 2.5

# Pass length bands, in metres.
SHORT_PASS_M = 15.0
LONG_PASS_M = 30.0

# A pass is 'forward' or 'backward' if it deviates from square by more than
# this; between the two it is sideways.
DIRECTION_DEADZONE_DEG = 30.0

# How long after a touch the ball's path is followed to decide whether it was a
# shot.
SHOT_LOOKAHEAD_S = 2.5

# A shot has to actually be struck. In player heights per second.
SHOT_SPEED_PH_S = 6.0

# A defender touching the ball this close to the shooter blocked it rather than
# saving or clearing it.
BLOCK_DISTANCE_M = 5.0

# PPDA is conventionally measured outside the pressing team's own 40%.
PPDA_ZONE_FRACTION = 0.6

# How long a slice of the match one pressing figure covers.
#
# Fifteen minutes, because the denominator decides this and nothing else. A team
# makes somewhere around fifty defensive actions in the pressing zone across a
# full match, so a quarter-hour block holds roughly ten — few enough that the
# ratio is rough, many enough that it is a ratio. Five-minute blocks would give
# a prettier chart of two or three actions apiece, which is a picture of noise.
PRESSING_SEGMENT_S = 15 * 60.0

# Below this many defensive actions a segment carries its counts and no ratio.
#
# A floor for legibility, not a significance test. Treating the action count as
# Poisson, five of them puts roughly ±45% around the ratio, so even a segment
# that clears this bar is a rough number — it is just no longer a number built
# from two events. Segments below it are still reported, still drawn, and still
# said out loud in the note, because "we barely challenged them for twenty
# minutes" is the finding, not a gap in the data.
MIN_PRESSING_ACTIONS = 5


def _other(team: str) -> str:
    if team == TEAM_A:
        return TEAM_B
    if team == TEAM_B:
        return TEAM_A
    return UNKNOWN


@dataclass(frozen=True)
class EventBase:
    event_id: str
    type: str
    timestamp_s: float
    frame_index: int
    team: str
    track_id: int | None
    start_xy_px: tuple[float, float]
    end_xy_px: tuple[float, float] | None = None
    start_m: tuple[float, float] | None = None
    end_m: tuple[float, float] | None = None
    confidence: float = 0.0
    tags: tuple[str, ...] = ()
    # Whether the tagged log says the ball was in play at this moment.
    #
    # True by default, and that default means "nobody told us" rather than "we
    # checked". Dead-ball events are kept rather than dropped: a throw-in is a
    # real pass and a coach counts it. What this flag exists for is the handful
    # of measures that are only defined on open play — PPDA foremost — and for
    # a reviewer who wants to see restarts separately.
    in_play: bool = True

    def to_json(self) -> dict:
        return {
            'event_id': self.event_id,
            'type': self.type,
            'timestamp_s': round(self.timestamp_s, 3),
            'frame_index': self.frame_index,
            'team': self.team,
            'track_id': self.track_id,
            'start_m': list(self.start_m) if self.start_m else None,
            'end_m': list(self.end_m) if self.end_m else None,
            'confidence': round(self.confidence, 3),
            'tags': list(self.tags),
            'in_play': self.in_play,
        }


@dataclass(frozen=True)
class Pass(EventBase):
    receiver_track_id: int | None = None
    outcome: str = UNKNOWN_OUTCOME
    length_m: float | None = None
    length_ph: float = 0.0
    length_bucket: str | None = None
    direction: str | None = None
    crossed_gap: bool = False

    def to_json(self) -> dict:
        return super().to_json() | {
            'receiver_track_id': self.receiver_track_id,
            'outcome': self.outcome,
            'length_m': self.length_m,
            'length_bucket': self.length_bucket,
            'direction': self.direction,
            'crossed_gap': self.crossed_gap,
        }


@dataclass(frozen=True)
class Carry(EventBase):
    touches: int = 0
    distance_m: float | None = None

    def to_json(self) -> dict:
        return super().to_json() | {
            'touches': self.touches, 'distance_m': self.distance_m,
        }


@dataclass(frozen=True)
class Shot(EventBase):
    outcome: str = UNKNOWN_OUTCOME
    on_target: bool = False
    distance_to_goal_m: float | None = None
    angle_to_goal_rad: float | None = None
    under_pressure: bool = False
    pressure_count: int = 0
    xg: float | None = None
    # The same shot scored as a header instead. A single fixed camera cannot see
    # the ball's height, so the pipeline cannot tell which this was — but the
    # question has exactly two answers, so both are computed here and a human
    # picks later. Measured against the real model, the difference is 1.3x to
    # 3.7x, which is far too large to leave as a footnote on `xg`.
    xg_header: float | None = None
    # Always False *from the pipeline*. Kept as a field so the assumption is
    # visible in the output rather than only in this comment; a coach's tag
    # lives in cvReview, beside their other judgements, and never overwrites a
    # measurement here.
    is_header: bool = False

    def to_json(self) -> dict:
        return super().to_json() | {
            'outcome': self.outcome,
            'on_target': self.on_target,
            'distance_to_goal_m': self.distance_to_goal_m,
            'angle_to_goal_rad': self.angle_to_goal_rad,
            'under_pressure': self.under_pressure,
            'pressure_count': self.pressure_count,
            'xg': self.xg,
            'xg_header': self.xg_header,
            'is_header': self.is_header,
        }


@dataclass(frozen=True)
class DefensiveAction(EventBase):
    """A tackle, interception, recovery, clearance or duel."""

    won: bool | None = None
    opponent_track_id: int | None = None

    def to_json(self) -> dict:
        return super().to_json() | {
            'won': self.won, 'opponent_track_id': self.opponent_track_id,
        }


@dataclass
class EventLog:
    events: list[EventBase] = field(default_factory=list)
    touches: TouchSequence | None = None

    def __len__(self) -> int:
        return len(self.events)

    def __iter__(self):
        return iter(self.events)

    def by_type(self, *types: str) -> list[EventBase]:
        wanted = set(types)
        return [e for e in self.events if e.type in wanted]

    def by_team(self, team: str) -> list[EventBase]:
        return [e for e in self.events if e.team == team]

    def by_track(self, track_id: int) -> list[EventBase]:
        return [e for e in self.events if e.track_id == track_id]

    def passes(self, team: str | None = None) -> list[Pass]:
        out = [e for e in self.events if isinstance(e, Pass)]
        return [e for e in out if team is None or e.team == team]

    def shots(self, team: str | None = None) -> list[Shot]:
        out = [e for e in self.events if isinstance(e, Shot)]
        return [e for e in out if team is None or e.team == team]

    def to_json(self, include_touches: bool = False) -> dict:
        data: dict = {'events': [e.to_json() for e in self.events]}
        if include_touches and self.touches:
            data['touches'] = [
                {
                    'timestamp_s': round(t.timestamp_s, 3),
                    'track_id': t.track_id,
                    'team': t.team,
                    'observed': t.observed,
                    'confidence': round(t.confidence, 3),
                }
                for t in self.touches
            ]
        return data


# ---------------------------------------------------------------- helpers


def _event_id(touch: Touch, kind: str) -> str:
    return f'{touch.frame_index}:{touch.track_id}:{kind}'


def _pixel_distance_ph(a: Touch, b: Touch) -> float:
    scale = (a.scale_px + b.scale_px) / 2
    if scale <= 0:
        return 0.0
    return math.dist(a.ball_xy, b.ball_xy) / scale


def _speed_between(a: Touch, b: Touch) -> float:
    dt = b.timestamp_s - a.timestamp_s
    if dt <= 0:
        return 0.0
    return _pixel_distance_ph(a, b) / dt


def _bucket(length_m: float | None) -> str | None:
    if length_m is None:
        return None
    if length_m < SHORT_PASS_M:
        return 'short'
    return 'medium' if length_m < LONG_PASS_M else 'long'


def _direction(
    pitch: Pitch, start_m, end_m, attacking_end: str | None
) -> str | None:
    """Forward, sideways or backward — or None, which is not the same as square.

    None means we could not tell, and there is no pixel-space approximation
    worth offering: on a panning camera the image axes are not the pitch axes.
    """
    if start_m is None or end_m is None or attacking_end is None:
        return None

    gained = zones.progressive_distance(pitch, start_m, end_m, attacking_end)
    lateral = abs(end_m[1] - start_m[1])
    angle = math.degrees(math.atan2(abs(gained), lateral)) if lateral or gained else 0.0

    if angle < DIRECTION_DEADZONE_DEG:
        return 'sideways'
    return 'forward' if gained > 0 else 'backward'


def _pressure(table: FrameTable, touch: Touch) -> int:
    """Opponents within a couple of strides of the player on the ball."""
    record = table.at(touch.frame_index)
    if record is None or touch.team == UNKNOWN:
        return 0

    boxes = record.player_boxes()
    holder = next((b for b in boxes if b.track_id == touch.track_id), None)
    if holder is None or touch.scale_px <= 0:
        return 0

    count = 0
    for box in boxes:
        if box.track_id == touch.track_id:
            continue
        if table.team_of(box.track_id) != _other(touch.team):
            continue
        if math.dist(holder.ground_point, box.ground_point) / touch.scale_px <= PRESSURE_PH:
            count += 1
    return count


def _tags(pitch: Pitch, start_m, end_m, attacking_end: str | None) -> tuple[str, ...]:
    """Everything geographic we can say about a ball played from A to B."""
    if start_m is None or end_m is None or attacking_end is None:
        return ()

    tags: list[str] = []
    if zones.is_progressive(pitch, start_m, end_m, attacking_end):
        tags.append('progressive')
    if zones.is_switch(start_m, end_m):
        tags.append('switch')
    if zones.is_cross(pitch, start_m, end_m, attacking_end):
        tags.append('cross')
    if zones.in_penalty_area(pitch, end_m[0], end_m[1], attacking_end):
        tags.append('into_box')
    if (
        zones.in_final_third(pitch, end_m[0], attacking_end)
        and not zones.in_final_third(pitch, start_m[0], attacking_end)
    ):
        tags.append('final_third_entry')
    return tuple(tags)


# ---------------------------------------------------------------- derivation


def attacking_end_for(
    orientation: MatchOrientation,
    side_of_team: dict[str, str] | None,
    period: str,
    team: str,
    *,
    calibrated: bool = True,
) -> str | None:
    """Which goal a colour cluster is attacking, or None if nothing can say.

    Three separate things have to be true before this has an answer: there is a
    calibration, so the two ends are distinguishable at all; somebody said which
    cluster is which side, which no amount of vision supplies; and the period is
    known, because the sides swap at half time.

    Module-level rather than a closure inside `derive_events` because the xG
    bridge needs the same answer for the same shot. Two places working out which
    way a team kicks is how a second-half sign error gets in, and it would show
    up as plausible xG for shots at the wrong goal rather than as a crash.
    """
    if not calibrated or not side_of_team:
        return None
    side = side_of_team.get(team)
    if side not in ('us', 'them'):
        return None
    return orientation.attacking_end(side, period)


def derive_events(
    touches: TouchSequence,
    table: FrameTable,
    pitch: Pitch | None = None,
    orientation: MatchOrientation | None = None,
    period: str = 'first_half',
    side_of_team: dict[str, str] | None = None,
    keeper_tracks: set[int] | None = None,
    phases=None,
) -> EventLog:
    """Turn a touch sequence into events.

    `side_of_team` maps a colour cluster to a side — `{'team_a': 'us'}`. It is
    the one fact no amount of vision supplies: clustering can tell two kits
    apart but has no idea which one is the home team, and without that there is
    no way to know which goal anyone is attacking. Absent it, everything
    positional is left as None rather than assumed.

    `phases` is a `cv.phases.PhaseTable` on the same clock as the table, and
    only sets each event's `in_play` flag. Nothing is discarded for being a
    restart — see `EventBase.in_play` for why.
    """
    pitch = pitch or (table.calibration.pitch if table.calibration else Pitch())
    orientation = orientation or MatchOrientation()
    keeper_tracks = keeper_tracks or set()
    log = EventLog(touches=touches)

    calibrated = table.calibration is not None

    def attacking_end(team: str) -> str | None:
        return attacking_end_for(
            orientation, side_of_team, period, team, calibrated=calibrated,
        )

    ordered = sorted(touches, key=lambda t: t.timestamp_s)

    for index, current in enumerate(ordered):
        following = ordered[index + 1] if index + 1 < len(ordered) else None

        shot = _as_shot(
            current, following, touches, table, pitch,
            attacking_end(current.team), keeper_tracks, ordered[index + 1:],
        )
        if shot is not None:
            log.events.append(shot)
            continue

        if following is None:
            continue

        log.events.extend(_between(
            current, following, touches, table, pitch,
            attacking_end(current.team), index, ordered,
        ))

    log.events.extend(_carries(ordered, table, pitch))
    log.events.sort(key=lambda e: e.timestamp_s)

    # Stamped in one pass at the end rather than threaded through eight
    # constructors, all of which would have to remember it.
    if phases is not None:
        log.events = [
            replace(event, in_play=phases.is_live(event.timestamp_s))
            for event in log.events
        ]

    return log


def _between(
    current: Touch,
    following: Touch,
    touches: TouchSequence,
    table: FrameTable,
    pitch: Pitch,
    attacking_end: str | None,
    index: int,
    ordered: list[Touch],
) -> list[EventBase]:
    """Classify one adjacent pair of touches."""
    if current.track_id == following.track_id:
        return []                                   # a carry; handled in bulk

    events: list[EventBase] = []
    crossed_gap = touches.gap_between(current, following)
    confidence = min(current.confidence, following.confidence)
    if crossed_gap:
        # Something may have touched the ball in between and we would not know,
        # so the link between these two touches is a guess.
        confidence *= 0.5

    same_team = (
        current.team == following.team
        and current.team != UNKNOWN
    )

    length_m = None
    if current.ball_m and following.ball_m:
        length_m = math.dist(current.ball_m, following.ball_m)

    if crossed_gap:
        outcome = UNKNOWN_OUTCOME
    elif same_team:
        outcome = COMPLETED
    elif current.team != UNKNOWN and following.team != UNKNOWN:
        outcome = INCOMPLETE
    else:
        outcome = UNKNOWN_OUTCOME

    events.append(Pass(
        event_id=_event_id(current, PASS),
        type=PASS,
        timestamp_s=current.timestamp_s,
        frame_index=current.frame_index,
        team=current.team,
        track_id=current.track_id,
        start_xy_px=current.ball_xy,
        end_xy_px=following.ball_xy,
        start_m=current.ball_m,
        end_m=following.ball_m,
        confidence=confidence,
        tags=_tags(pitch, current.ball_m, following.ball_m, attacking_end),
        receiver_track_id=following.track_id if outcome == COMPLETED else None,
        outcome=outcome,
        length_m=length_m,
        length_ph=_pixel_distance_ph(current, following),
        length_bucket=_bucket(length_m),
        direction=_direction(pitch, current.ball_m, following.ball_m, attacking_end),
        crossed_gap=crossed_gap,
    ))

    if outcome == INCOMPLETE:
        events.append(_defensive_action(
            current, following, touches, table, pitch, index, ordered, confidence,
        ))

    return events


def _defensive_action(
    current: Touch,
    following: Touch,
    touches: TouchSequence,
    table: FrameTable,
    pitch: Pitch,
    index: int,
    ordered: list[Touch],
    confidence: float,
) -> DefensiveAction:
    """Name the way possession changed hands.

    The discriminator is what the ball was doing immediately before, not who
    ended up with it:

      * the previous touch was by the same player and the ball was moving
        slowly between them — the opponent had it under control, so this is a
        **tackle**;
      * the ball was travelling fast between two different players — the
        opponent read a pass, so this is an **interception**;
      * nobody had touched it for a while — it was loose, so this is a
        **recovery**.

    All three are ratios of pixel distances to player heights, so none of them
    needs a calibration.
    """
    idle = following.timestamp_s - current.timestamp_s
    speed = _speed_between(current, following)

    previous = ordered[index - 1] if index > 0 else None
    was_controlled = (
        previous is not None
        and previous.track_id == current.track_id
        and _speed_between(previous, current) < CONTROLLED_SPEED_PH_S
    )

    if idle >= LOOSE_BALL_S:
        kind = RECOVERY
    elif was_controlled and speed < CONTROLLED_SPEED_PH_S:
        kind = TACKLE
    elif _is_duel(current, following, table):
        kind = DUEL
    else:
        kind = INTERCEPTION

    return DefensiveAction(
        event_id=_event_id(following, kind),
        type=kind,
        timestamp_s=following.timestamp_s,
        frame_index=following.frame_index,
        team=following.team,
        track_id=following.track_id,
        start_xy_px=following.ball_xy,
        start_m=following.ball_m,
        confidence=confidence,
        won=True,
        opponent_track_id=current.track_id,
    )


def _is_duel(current: Touch, following: Touch, table: FrameTable) -> bool:
    """Two opponents contesting the same ball rather than one taking it.

    Ground duels only. Whether a duel was aerial needs the ball's height, which
    one camera cannot recover — reporting these as "duels" without the
    qualifier would silently exclude every header from a stat people expect to
    include them.
    """
    if following.timestamp_s - current.timestamp_s > DUEL_WINDOW_S:
        return False
    return _pixel_distance_ph(current, following) <= DUEL_DISTANCE_PH


def _carries(ordered: list[Touch], table: FrameTable, pitch: Pitch) -> list[Carry]:
    """Runs of consecutive touches by one player."""
    carries: list[Carry] = []
    run: list[Touch] = []

    def flush() -> None:
        if len(run) < 2:
            return
        first, last = run[0], run[-1]
        distance = (
            math.dist(first.ball_m, last.ball_m)
            if first.ball_m and last.ball_m else None
        )
        carries.append(Carry(
            event_id=_event_id(first, CARRY),
            type=CARRY,
            timestamp_s=first.timestamp_s,
            frame_index=first.frame_index,
            team=first.team,
            track_id=first.track_id,
            start_xy_px=first.ball_xy,
            end_xy_px=last.ball_xy,
            start_m=first.ball_m,
            end_m=last.ball_m,
            confidence=min(t.confidence for t in run),
            touches=len(run),
            distance_m=distance,
        ))

    for touch in ordered:
        if run and touch.track_id != run[-1].track_id:
            flush()
            run = []
        run.append(touch)
    flush()

    return carries


# ---------------------------------------------------------------- shots


def _as_shot(
    touch: Touch,
    following: Touch | None,
    touches: TouchSequence,
    table: FrameTable,
    pitch: Pitch,
    attacking_end: str | None,
    keeper_tracks: set[int],
    later: list[Touch],
) -> Shot | None:
    """Whether this touch sent the ball at the goal, and what became of it.

    Needs a calibration, and not for precision — without knowing where the goal
    is there is no difference between a shot and a long pass. This is why shot
    detection is gated on fixed-camera footage rather than on effort.
    """
    if attacking_end is None or touch.ball_m is None:
        return None

    end_m = _ball_after(table, touch, SHOT_LOOKAHEAD_S)
    if end_m is None:
        return None

    if not zones.enters_goal_mouth(pitch, touch.ball_m, end_m, attacking_end):
        return None

    # How fast the ball left the foot, not how far it got before someone else
    # touched it. Those differ most for exactly the shots worth catching: a
    # blocked strike travels two metres, and measuring the gap to the blocker
    # would score it as a gentle roll.
    if touch.speed_after_ph_s < SHOT_SPEED_PH_S:
        return None                    # rolled towards goal, not struck at it

    pressure = _pressure(table, touch)
    outcome, on_target = _shot_outcome(
        touch, later, pitch, attacking_end, keeper_tracks,
    )

    return Shot(
        event_id=_event_id(touch, SHOT),
        type=SHOT,
        timestamp_s=touch.timestamp_s,
        frame_index=touch.frame_index,
        team=touch.team,
        track_id=touch.track_id,
        start_xy_px=touch.ball_xy,
        end_xy_px=None,
        start_m=touch.ball_m,
        end_m=end_m,
        confidence=touch.confidence,
        tags=('in_box',) if zones.in_penalty_area(
            pitch, touch.ball_m[0], touch.ball_m[1], attacking_end) else (),
        outcome=outcome,
        on_target=on_target,
        distance_to_goal_m=zones.distance_to_goal(
            pitch, touch.ball_m[0], touch.ball_m[1], attacking_end),
        angle_to_goal_rad=zones.angle_to_goal(
            pitch, touch.ball_m[0], touch.ball_m[1], attacking_end),
        under_pressure=pressure > 0,
        pressure_count=pressure,
    )


def _ball_after(table: FrameTable, touch: Touch, window_s: float):
    """Where the ball was last seen within `window_s` of a touch, in metres."""
    if table.calibration is None:
        return None

    last = None
    for record in table.records:
        if record.timestamp_s <= touch.timestamp_s:
            continue
        if record.timestamp_s > touch.timestamp_s + window_s:
            break
        if record.ball_xy is not None:
            last = table.to_pitch(record.ball_xy)
    return last


def _shot_outcome(
    touch: Touch,
    later: list[Touch],
    pitch: Pitch,
    attacking_end: str,
    keeper_tracks: set[int],
) -> tuple[str, bool]:
    """Goal, saved, blocked or off target.

    On-target here means the ball's ground path crossed the line between the
    posts. Height is not modelled, so a shot over the bar traces the same path
    as one in the top corner and counts as on target — which inflates save
    percentage. Stated on the way past because that is a number people read
    closely.
    """
    for other in later:
        if other.timestamp_s - touch.timestamp_s > SHOT_LOOKAHEAD_S:
            break
        if other.track_id == touch.track_id:
            continue

        if other.track_id in keeper_tracks:
            return (SAVED, True)

        if other.team != touch.team and other.ball_m and touch.ball_m:
            if math.dist(touch.ball_m, other.ball_m) <= BLOCK_DISTANCE_M:
                return (BLOCKED, True)
        # Somebody else got to it before it crossed the line.
        return (OFF_TARGET, True)

    return (GOAL, True)


# ---------------------------------------------------------------- rollups


def turnovers_by_third(
    log: EventLog,
    pitch: Pitch,
    team: str,
    attacking_end: str | None,
) -> dict[str, int] | None:
    """Where `team` gave the ball away, counted by third of the pitch.

    The catalog asks for "dangerous turnover locations — giveaways in your own
    defensive third", and possession could not answer it: `PossessionSummary`
    counts turnovers but a spell is `(team, start_s, end_s)` with no position in
    it at all. The event log has positions, so the count comes from here.

    A giveaway is a pass this team attempted and did not complete. Deliberately
    not "every time possession changed hands": a tackle by the opposition is
    also a turnover, and blaming the player who was tackled for a giveaway is a
    different claim from the one a coach reads here. Unknown-outcome passes are
    excluded too — we could not see whether it found a teammate, and counting
    that as a loss would blame the detector's blind spots on the defence.

    Located at where the pass *started*, which is where the player was when they
    made the decision. The end point is where the ball ended up, and a hopeful
    ball from the edge of your own box that lands in midfield was a bad decision
    taken in a dangerous place.

    Returns None without an attacking end, since a third cannot be named.
    """
    if attacking_end is None:
        return None

    counts = {
        zones.DEFENSIVE_THIRD: 0, zones.MIDDLE_THIRD: 0, zones.ATTACKING_THIRD: 0,
    }
    for event in log.passes(team):
        if event.outcome != INCOMPLETE or event.start_m is None:
            continue
        counts[zones.third(pitch, event.start_m[0], attacking_end)] += 1
    return counts


@dataclass(frozen=True)
class PressingCount:
    """The two numbers PPDA is made of, kept together.

    The ratio alone cannot be read at the bottom end. A spell with thirty passes
    allowed and no challenge at all is the strongest non-press there is, and it
    has no ratio — dividing by zero is undefined, so the one figure that would
    describe it best is the one figure that cannot exist. Carrying the counts
    means that spell still says something.
    """

    allowed: int
    actions: int

    @property
    def ppda(self) -> float | None:
        """None when nobody challenged. Not infinity, and certainly not zero."""
        if not self.actions:
            return None
        return self.allowed / self.actions


def count_pressing(
    log: EventLog,
    pitch: Pitch,
    pressing_team: str,
    attacking_end_of_opponent: str | None,
    since_s: float | None = None,
    until_s: float | None = None,
) -> PressingCount | None:
    """Passes allowed and defensive actions in the pressing zone, over a span.

    One definition of the zone, one definition of an action, used by both the
    whole-match figure and every segment of it. Two implementations of "outside
    their own 40%" is exactly how a chart ends up disagreeing with the number
    printed above it.

    `since_s` is inclusive and `until_s` exclusive, so consecutive segments
    tile the window without counting an event on a boundary twice.
    """
    if attacking_end_of_opponent is None:
        return None

    boundary = pitch.length_m * (1 - PPDA_ZONE_FRACTION)

    def in_zone(point) -> bool:
        if point is None:
            return False
        # Distance from the opponent's own goal line, along their attack.
        forward = (
            point[0] if attacking_end_of_opponent == 'right'
            else pitch.length_m - point[0]
        )
        return forward >= boundary

    def in_span(event) -> bool:
        if since_s is not None and event.timestamp_s < since_s:
            return False
        if until_s is not None and event.timestamp_s >= until_s:
            return False
        return True

    opponent = _other(pressing_team)
    allowed = sum(
        1 for p in log.passes(opponent)
        if p.in_play and in_span(p) and in_zone(p.start_m)
        and p.outcome != UNKNOWN_OUTCOME
    )
    actions = sum(
        1 for e in log.by_type(TACKLE, INTERCEPTION, RECOVERY, DUEL)
        if e.in_play and e.team == pressing_team and in_span(e)
        and in_zone(e.start_m)
    )
    return PressingCount(allowed=allowed, actions=actions)


def ppda(
    log: EventLog,
    pitch: Pitch,
    pressing_team: str,
    attacking_end_of_opponent: str | None,
) -> float | None:
    """Opponent passes allowed per defensive action, in the pressing zone.

    Lower means more aggressive pressing. Conventionally measured outside the
    pressing team's own 40% of the pitch.

        This will read systematically HIGHER than Opta's. Fouls are a defensive
        action in their definition and are undetectable from bounding boxes, so
        our denominator is short by every foul in the match. The bias has a
        known direction and an unknown size.

    Restarts are excluded from both sides of the ratio. Pressing is a thing a
    team does to open play, and counting the throw-in a defender takes
    unopposed as a "pass allowed" flatters whoever they were playing against.
    Without a tagged log nothing is marked as a restart and this is a no-op.
    """
    counted = count_pressing(log, pitch, pressing_team, attacking_end_of_opponent)
    return counted.ppda if counted else None


def _segment_bounds(
    start_s: float, end_s: float, segment_s: float,
) -> list[tuple[float, float]]:
    """Tile `[start_s, end_s)` in blocks, with no runt at the end.

    A block shorter than half a segment is folded into the one before it rather
    than drawn beside it. Three minutes of football next to fifteen is two bars
    the eye compares directly and should not — the short one is quiet because it
    is short, which looks identical to a team that stopped pressing.
    """
    span = end_s - start_s
    if span <= 0 or segment_s <= 0:
        return []

    count = max(1, int(span // segment_s))
    bounds = [
        (start_s + i * segment_s, start_s + (i + 1) * segment_s)
        for i in range(count)
    ]
    # Whatever is left over extends the last block. By construction the
    # remainder is under one full segment, so this never doubles a block.
    bounds[-1] = (bounds[-1][0], end_s)
    return bounds


def pressing_segments(
    log: EventLog,
    pitch: Pitch,
    pressing_team: str,
    attacking_end_of_opponent: str | None,
    start_s: float,
    end_s: float,
    segment_s: float = PRESSING_SEGMENT_S,
) -> list[dict] | None:
    """How the press held up across the window, block by block.

    A single PPDA for a whole match answers "did they press" and hides the
    question a coach actually asks, which is "for how long". A team that
    squeezed for twenty minutes and then dropped off has the same match figure
    as one that pressed evenly throughout, and those are different teams.

    Returns None, never an empty list or a single block, when the window is too
    short to hold two segments. One number redrawn as one bar is not a trend,
    and presenting it as one would invite reading a slope that was never
    measured.

    Timestamps are video seconds, matching every other event in the report. The
    match clock lives in the browser, where the video offset is, and converting
    here would bake in the one number in this app most likely to be wrong.
    """
    if attacking_end_of_opponent is None:
        return None

    bounds = _segment_bounds(start_s, end_s, segment_s)
    if len(bounds) < 2:
        return None

    out = []
    for since, until in bounds:
        counted = count_pressing(
            log, pitch, pressing_team, attacking_end_of_opponent, since, until,
        )
        ratio = counted.ppda
        out.append({
            'start_s': round(since, 1),
            'end_s': round(until, 1),
            'allowed': counted.allowed,
            'actions': counted.actions,
            # Withheld on a thin denominator, but the counts stay. A reader who
            # wants the rough number can divide; what they cannot do is mistake
            # it for one of the segments that earned its ratio.
            'ppda': (
                round(ratio, 2) if ratio is not None
                and counted.actions >= MIN_PRESSING_ACTIONS else None
            ),
        })
    return out


def attach_xg(log: EventLog, xg_by_event: dict[str, tuple[float, float]]) -> None:
    """Write xG onto shots, keyed by event id.

    Each value is `(foot, header)` — the same shot scored both ways, because
    nothing here can see which it was and the two differ by more than any other
    uncertainty in the number. Whoever watches the video decides; both readings
    travel so that decision costs no second pass over the footage.

    Kept separate from derivation so `cv/events.py` never has to import
    onnxruntime — the event layer is pure geometry and stays that way.
    """
    for index, event in enumerate(log.events):
        if isinstance(event, Shot) and event.event_id in xg_by_event:
            foot, header = xg_by_event[event.event_id]
            log.events[index] = replace(event, xg=foot, xg_header=header)
