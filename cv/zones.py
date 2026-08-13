"""Where on the pitch something happened.

Every event statistic beyond a raw count is really a question about geography.
A pass is progressive if it gained ground *towards a particular goal*. A cross
comes from a wide area *of the attacking third*. A clearance leaves the
defensive third. None of those mean anything until you can say which end of the
pitch a team is playing towards.

So `attacking_end` (or `end`, or `defending_end`) is a required argument
throughout this module, never defaulted. That is deliberate and slightly
annoying. The failure mode it prevents is the one `Pitch.to_statsbomb` already
warns about: getting the end wrong does not raise, it silently reports a
defensive clearance as a final-third entry and a shot from your own box as a
tap-in. A wrong number that looks right is worse than a crash.

Everything here is in metres and needs a calibration upstream. The functions
themselves are pure geometry over `Pitch`, so they are fully testable today —
which matters, because the footage that would let them run for real does not
exist yet.

Deliberately absent: offside. It needs the complete and accurate positions of
every defender at the exact moment of release. We have neither complete
positions (players leave frame) nor an accurate release moment (the ball is
detected in about 60% of frames). It stays human-tagged; see ROADMAP Phase 10.
"""

from __future__ import annotations

import math

from .pitch import (
    GOAL_AREA_LENGTH_M,
    GOAL_AREA_WIDTH_M,
    GOAL_WIDTH_M,
    PENALTY_AREA_LENGTH_M,
    PENALTY_AREA_WIDTH_M,
    Pitch,
)

DEFENSIVE_THIRD = 'defensive'
MIDDLE_THIRD = 'middle'
ATTACKING_THIRD = 'attacking'

# Progressive-pass thresholds, in metres gained towards the goal. These are the
# widely used Wyscout/Opta-style bands, and they are a *convention* rather than
# a measured fact — a coach comparing our number to a broadcast graphic will
# assume ours is broken if we invent our own. Named here so the choice is
# visible rather than buried in an expression.
PROGRESSIVE_OWN_HALF_M = 30.0
PROGRESSIVE_CROSSING_HALFWAY_M = 15.0
PROGRESSIVE_OPPONENT_HALF_M = 10.0

# A switch of play: a ball moved this far across the pitch.
SWITCH_LATERAL_M = 40.0

# How many defenders make a defensive line. Four matches the usual back four; a
# side playing three at the back will read a metre or two deeper than it is.
DEFENSIVE_LINE_PLAYERS = 4


def _check_end(end: str) -> str:
    if end not in ('left', 'right'):
        raise ValueError(f"end must be 'left' or 'right', got {end!r}")
    return end


def opposite(end: str) -> str:
    """The other end. A team defends the goal it is not attacking."""
    return 'left' if _check_end(end) == 'right' else 'right'


# ---------------------------------------------------------------- thirds


def third_boundaries(pitch: Pitch) -> tuple[float, float]:
    """The two x values dividing the pitch into thirds."""
    return (pitch.length_m / 3.0, pitch.length_m * 2.0 / 3.0)


def third(pitch: Pitch, x_m: float, attacking_end: str) -> str:
    """Which third of the pitch, named from the attacking team's point of view.

    A point exactly on a boundary resolves to the further-forward third, so the
    three bands partition the pitch rather than overlapping at their edges.
    """
    _check_end(attacking_end)
    first, second = third_boundaries(pitch)

    # Measure along the direction of attack, so the naming is the same whichever
    # goal the team is playing towards.
    forward = x_m if attacking_end == 'right' else pitch.length_m - x_m

    if forward >= second:
        return ATTACKING_THIRD
    if forward >= first:
        return MIDDLE_THIRD
    return DEFENSIVE_THIRD


def in_final_third(pitch: Pitch, x_m: float, attacking_end: str) -> bool:
    return third(pitch, x_m, attacking_end) == ATTACKING_THIRD


# ---------------------------------------------------------------- boxes


def _box(pitch: Pitch, end: str, length_m: float, width_m: float):
    """The rectangle of a goal-end box, as (x_min, x_max, y_min, y_max)."""
    _check_end(end)
    centre_y = pitch.width_m / 2
    half = width_m / 2
    if end == 'left':
        return (0.0, length_m, centre_y - half, centre_y + half)
    return (pitch.length_m - length_m, pitch.length_m, centre_y - half, centre_y + half)


def in_penalty_area(pitch: Pitch, x_m: float, y_m: float, end: str) -> bool:
    """Inside the 18-yard box at the given end. The lines are part of the box."""
    x0, x1, y0, y1 = _box(pitch, end, PENALTY_AREA_LENGTH_M, PENALTY_AREA_WIDTH_M)
    return x0 <= x_m <= x1 and y0 <= y_m <= y1


def goal_area(pitch: Pitch, end: str):
    """The six-yard box as (x_min, x_max, y_min, y_max).

    Public because a caller can want the region itself and not only a
    membership test — `cv/coverage.py` samples it to ask whether the camera had
    the goalmouth in frame at all.
    """
    return _box(pitch, end, GOAL_AREA_LENGTH_M, GOAL_AREA_WIDTH_M)


def in_goal_area(pitch: Pitch, x_m: float, y_m: float, end: str) -> bool:
    """Inside the six-yard box at the given end."""
    x0, x1, y0, y1 = goal_area(pitch, end)
    return x0 <= x_m <= x1 and y0 <= y_m <= y1


def in_wide_channel(pitch: Pitch, y_m: float) -> bool:
    """Outside the width of the penalty area — where crosses come from."""
    half = PENALTY_AREA_WIDTH_M / 2
    centre_y = pitch.width_m / 2
    return y_m < centre_y - half or y_m > centre_y + half


def channel(pitch: Pitch, y_m: float, channels: int = 5) -> int:
    """Which lateral channel, 0 at y=0. Clamped, so an off-pitch ball still lands."""
    if channels < 1:
        raise ValueError('channels must be at least 1')
    index = int(y_m / pitch.width_m * channels)
    return max(0, min(channels - 1, index))


def zone_grid(
    pitch: Pitch, x_m: float, y_m: float, cols: int = 6, rows: int = 5
) -> tuple[int, int]:
    """Grid cell for heatmap-style aggregation. Not direction-aware by design.

    Zones here are absolute positions on the pitch, not positions relative to
    an attack, because a grid is normally drawn once for a whole match and the
    ends swap at halftime. Mirror at display time if you want attack-relative.
    """
    col = max(0, min(cols - 1, int(x_m / pitch.length_m * cols)))
    row = max(0, min(rows - 1, int(y_m / pitch.width_m * rows)))
    return (col, row)


# ---------------------------------------------------------------- the goal


def distance_to_goal(pitch: Pitch, x_m: float, y_m: float, end: str) -> float:
    """Straight-line metres to the centre of the goal at `end`."""
    gx, gy = pitch.goal_centre(_check_end(end))
    return math.dist((x_m, y_m), (gx, gy))


def angle_to_goal(pitch: Pitch, x_m: float, y_m: float, end: str) -> float:
    """The angle the goal mouth subtends from a point, in radians.

    This is the "how much goal can they see" term, and it is what separates a
    tight angle from a central chance at the same distance. Zero on the goal
    line outside the posts, where the mouth is edge-on.
    """
    left, right = pitch.goal_posts(_check_end(end))
    a = (left[0] - x_m, left[1] - y_m)
    b = (right[0] - x_m, right[1] - y_m)

    mag = math.hypot(*a) * math.hypot(*b)
    if mag == 0:
        return 0.0

    cosine = (a[0] * b[0] + a[1] * b[1]) / mag
    return math.acos(max(-1.0, min(1.0, cosine)))


def crosses_goal_line(
    pitch: Pitch,
    start_m: tuple[float, float],
    end_m: tuple[float, float],
    end: str,
) -> tuple[bool, float | None]:
    """Does the segment reach the goal line at `end`, and at what y?

    Returns (crossed, y_at_line). A segment running parallel to the goal line
    never crosses it, and one that stops short does not either — both are the
    difference between a shot that went in and a shot that was cleared.
    """
    _check_end(end)
    line_x = 0.0 if end == 'left' else pitch.length_m

    x0, y0 = start_m
    x1, y1 = end_m
    if x1 == x0:
        return (False, None)

    t = (line_x - x0) / (x1 - x0)
    if not 0.0 <= t <= 1.0:
        return (False, None)

    return (True, y0 + t * (y1 - y0))


def enters_goal_mouth(
    pitch: Pitch,
    start_m: tuple[float, float],
    end_m: tuple[float, float],
    end: str,
) -> bool:
    """Whether the segment crosses the goal line between the posts.

    Height is not modelled, because a single fixed camera cannot recover it.
    A shot sailing over the bar and one hitting the top corner trace the same
    path on the ground plane, so this over-counts on-target shots. Said out
    loud here because the number it feeds — save percentage — is one people
    read closely.
    """
    crossed, y = crosses_goal_line(pitch, start_m, end_m, end)
    if not crossed or y is None:
        return False

    centre_y = pitch.width_m / 2
    half = GOAL_WIDTH_M / 2
    return centre_y - half <= y <= centre_y + half


def leaves_play(
    pitch: Pitch, start_m: tuple[float, float], end_m: tuple[float, float]
) -> str | None:
    """'touchline', 'byline', or None — which boundary the segment exits by.

    Only the endpoint is tested. A ball that leaves and returns within one
    segment is a ball we sampled too coarsely, not a ball that stayed in.
    """
    x, y = end_m
    if y < 0 or y > pitch.width_m:
        return 'touchline'
    if x < 0 or x > pitch.length_m:
        return 'byline'
    return None


# ---------------------------------------------------------------- progress


def progressive_distance(
    pitch: Pitch,
    start_m: tuple[float, float],
    end_m: tuple[float, float],
    attacking_end: str,
) -> float:
    """Metres of ground gained towards the goal being attacked.

    Negative for a backward pass, which is the useful sign convention: summing
    it over a team gives net territorial gain rather than total distance.
    """
    _check_end(attacking_end)
    gained = end_m[0] - start_m[0]
    return gained if attacking_end == 'right' else -gained


def is_progressive(
    pitch: Pitch,
    start_m: tuple[float, float],
    end_m: tuple[float, float],
    attacking_end: str,
) -> bool:
    """Whether a pass counts as progressive under the usual banded thresholds.

    The bands exist because 20 metres out of your own penalty area is a routine
    ball, while 20 metres from the edge of their box is a chance. See the
    PROGRESSIVE_* constants for the caveat about these being a convention.
    """
    _check_end(attacking_end)
    gained = progressive_distance(pitch, start_m, end_m, attacking_end)
    if gained <= 0:
        return False

    halfway = pitch.length_m / 2
    # Distance from own goal line, measured along the direction of attack.
    def forward(x: float) -> float:
        return x if attacking_end == 'right' else pitch.length_m - x

    start_forward = forward(start_m[0])
    end_forward = forward(end_m[0])

    if end_forward <= halfway:
        return gained >= PROGRESSIVE_OWN_HALF_M
    if start_forward < halfway:
        return gained >= PROGRESSIVE_CROSSING_HALFWAY_M
    return gained >= PROGRESSIVE_OPPONENT_HALF_M


def is_switch(start_m: tuple[float, float], end_m: tuple[float, float]) -> bool:
    """A ball moved a long way across the pitch. Direction-agnostic by nature."""
    return abs(end_m[1] - start_m[1]) >= SWITCH_LATERAL_M


def is_cross(
    pitch: Pitch,
    start_m: tuple[float, float],
    end_m: tuple[float, float],
    attacking_end: str,
) -> bool:
    """From a wide area of the attacking third, into the penalty area.

    Not "any ball into the box": a square pass from the penalty spot to the
    six-yard line is not a cross, and requiring a wide origin is what excludes
    it. Whether the ball was in the air — the thing a human uses to judge this
    instantly — is not recoverable, so some low balls across the face will
    count that a human would call a pass.
    """
    _check_end(attacking_end)
    return (
        in_final_third(pitch, start_m[0], attacking_end)
        and in_wide_channel(pitch, start_m[1])
        and in_penalty_area(pitch, end_m[0], end_m[1], attacking_end)
    )


# ---------------------------------------------------------------- shape


def defensive_line_m(
    positions_m,
    defending_end: str,
    pitch: Pitch,
    players: int = DEFENSIVE_LINE_PLAYERS,
) -> float | None:
    """How high a team's back line is, as metres from its own goal line.

    Takes the mean of the `players` deepest outfielders. Deepest rather than
    all, because a lone striker pressing high would otherwise drag the "line"
    up the pitch; and a mean over several rather than the single deepest,
    because one recovering full-back should not define the shape.

    Exclude the keeper before calling — a keeper on the line pulls the mean
    down by several metres and does not move with the defence.
    """
    _check_end(defending_end)
    points = list(positions_m)
    if not points:
        return None

    def depth(point) -> float:
        """Distance from the goal being defended."""
        x = point[0]
        return x if defending_end == 'left' else pitch.length_m - x

    deepest = sorted(depth(p) for p in points)[:max(1, players)]
    return sum(deepest) / len(deepest)


def in_shot_cone(
    pitch: Pitch, point_m: tuple[float, float], shooter_m: tuple[float, float], end: str
) -> bool:
    """Whether a player stands between the shooter and the goal mouth.

    The triangle shooter-post-post, tested by sign of cross product. This is
    the metres-space twin of xg_bridge.in_shot_cone, which works in StatsBomb
    coordinates because that is what the model wants; keeping both means events
    can count defenders before anything has been converted.
    """
    left, right = pitch.goal_posts(_check_end(end))

    def side(a, b, p) -> float:
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

    d1 = side(shooter_m, left, point_m)
    d2 = side(left, right, point_m)
    d3 = side(right, shooter_m, point_m)

    has_negative = d1 < 0 or d2 < 0 or d3 < 0
    has_positive = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_negative and has_positive)
