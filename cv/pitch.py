"""The pitch: dimensions, landmarks, and the two coordinate systems.

Two systems are needed and they are not interchangeable:

  * **Metres** — the real pitch. Distance covered, speed and sprint counts are
    only meaningful here, and high school pitches genuinely vary in size, so the
    dimensions are configurable rather than assumed.

  * **StatsBomb 120x80** — the space `PitchIQHelper/main.py` trained the xG model
    in, and what `xg-sandbox/xg-model.js` feeds it at inference time. Any shot feature
    handed to that model has to be expressed here or the numbers are silently
    wrong.

Pitch coordinates in metres use the origin at the bottom-left corner as seen in
a standard broadcast view, x along the touchline (0 -> length) and y along the
goal line (0 -> width). Both goals sit at y = width / 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Fixed by the Laws of the Game regardless of overall pitch size.
GOAL_WIDTH_M = 7.32
GOAL_AREA_LENGTH_M = 5.5      # six-yard box depth
GOAL_AREA_WIDTH_M = 18.32
PENALTY_AREA_LENGTH_M = 16.5
PENALTY_AREA_WIDTH_M = 40.32
PENALTY_SPOT_M = 11.0
CENTRE_CIRCLE_RADIUS_M = 9.15

# The space the xG model was trained in.
STATSBOMB_LENGTH = 120.0
STATSBOMB_WIDTH = 80.0


@dataclass(frozen=True)
class Pitch:
    """A specific pitch. Defaults are a typical full-size field.

    Measure the real thing if you can — the roadmap's distance-covered and
    speed figures inherit any error here directly, and a 105m assumption on a
    100m pitch overstates every distance by 5%.
    """

    length_m: float = 105.0
    width_m: float = 68.0

    # ---------------------------------------------------------------- goals

    @property
    def left_goal_centre(self) -> tuple[float, float]:
        return (0.0, self.width_m / 2)

    @property
    def right_goal_centre(self) -> tuple[float, float]:
        return (self.length_m, self.width_m / 2)

    def goal_centre(self, end: str) -> tuple[float, float]:
        if end == "left":
            return self.left_goal_centre
        if end == "right":
            return self.right_goal_centre
        raise ValueError(f"end must be 'left' or 'right', got {end!r}")

    def goal_posts(self, end: str) -> tuple[tuple[float, float], tuple[float, float]]:
        x, cy = self.goal_centre(end)
        return ((x, cy - GOAL_WIDTH_M / 2), (x, cy + GOAL_WIDTH_M / 2))

    # ---------------------------------------------------------------- landmarks

    def landmarks(self) -> dict[str, tuple[float, float]]:
        """Named points a human can identify unambiguously in a frame.

        Keys are stable — calibration files store them — so rename with care.
        """
        L, W = self.length_m, self.width_m
        cy = W / 2
        pa_half = PENALTY_AREA_WIDTH_M / 2
        ga_half = GOAL_AREA_WIDTH_M / 2

        return {
            # Corners
            "corner_bottom_left": (0.0, 0.0),
            "corner_top_left": (0.0, W),
            "corner_bottom_right": (L, 0.0),
            "corner_top_right": (L, W),

            # Halfway line
            "halfway_bottom": (L / 2, 0.0),
            "halfway_top": (L / 2, W),
            "centre_spot": (L / 2, cy),
            "centre_circle_bottom": (L / 2, cy - CENTRE_CIRCLE_RADIUS_M),
            "centre_circle_top": (L / 2, cy + CENTRE_CIRCLE_RADIUS_M),

            # Left penalty area
            "pen_left_bottom_goalline": (0.0, cy - pa_half),
            "pen_left_top_goalline": (0.0, cy + pa_half),
            "pen_left_bottom_corner": (PENALTY_AREA_LENGTH_M, cy - pa_half),
            "pen_left_top_corner": (PENALTY_AREA_LENGTH_M, cy + pa_half),
            "pen_spot_left": (PENALTY_SPOT_M, cy),

            # Right penalty area
            "pen_right_bottom_goalline": (L, cy - pa_half),
            "pen_right_top_goalline": (L, cy + pa_half),
            "pen_right_bottom_corner": (L - PENALTY_AREA_LENGTH_M, cy - pa_half),
            "pen_right_top_corner": (L - PENALTY_AREA_LENGTH_M, cy + pa_half),
            "pen_spot_right": (L - PENALTY_SPOT_M, cy),

            # Goal areas (six-yard boxes)
            "goalarea_left_bottom_corner": (GOAL_AREA_LENGTH_M, cy - ga_half),
            "goalarea_left_top_corner": (GOAL_AREA_LENGTH_M, cy + ga_half),
            "goalarea_right_bottom_corner": (L - GOAL_AREA_LENGTH_M, cy - ga_half),
            "goalarea_right_top_corner": (L - GOAL_AREA_LENGTH_M, cy + ga_half),

            # Goalposts
            "goalpost_left_bottom": (0.0, cy - GOAL_WIDTH_M / 2),
            "goalpost_left_top": (0.0, cy + GOAL_WIDTH_M / 2),
            "goalpost_right_bottom": (L, cy - GOAL_WIDTH_M / 2),
            "goalpost_right_top": (L, cy + GOAL_WIDTH_M / 2),
        }

    def landmark(self, name: str) -> tuple[float, float]:
        try:
            return self.landmarks()[name]
        except KeyError:
            raise KeyError(f"Unknown landmark {name!r}") from None

    # ---------------------------------------------------------------- StatsBomb

    def to_statsbomb(
        self, x_m: float, y_m: float, attacking_end: str = "right"
    ) -> tuple[float, float]:
        """Metres -> StatsBomb 120x80, with the attacked goal always at x=120.

        The xG model assumes the shooter is attacking the goal at x=120, so a
        team attacking the left end has to be mirrored. Getting this wrong does
        not raise — it silently produces plausible-looking xG for a shot
        measured from the wrong goal.
        """
        if attacking_end == "left":
            x_m = self.length_m - x_m
            y_m = self.width_m - y_m
        elif attacking_end != "right":
            raise ValueError(
                f"attacking_end must be 'left' or 'right', got {attacking_end!r}"
            )

        return (
            x_m / self.length_m * STATSBOMB_LENGTH,
            y_m / self.width_m * STATSBOMB_WIDTH,
        )

    def from_statsbomb(
        self, x_sb: float, y_sb: float, attacking_end: str = "right"
    ) -> tuple[float, float]:
        x_m = x_sb / STATSBOMB_LENGTH * self.length_m
        y_m = y_sb / STATSBOMB_WIDTH * self.width_m

        if attacking_end == "left":
            x_m = self.length_m - x_m
            y_m = self.width_m - y_m
        return (x_m, y_m)

    def contains(self, x_m: float, y_m: float, margin_m: float = 0.0) -> bool:
        return (
            -margin_m <= x_m <= self.length_m + margin_m
            and -margin_m <= y_m <= self.width_m + margin_m
        )


# ---------------------------------------------------------------------------
# Attacking direction
# ---------------------------------------------------------------------------

# Which period markers flip the ends. Teams swap at halftime, so the second half
# attacks the opposite goal from the first.
_SECOND_HALF_PERIODS = {"kickoff_2nd", "second_half"}


@dataclass
class MatchOrientation:
    """Which end each side attacks, per period.

    `home_attacks_first_half` is the one thing a human has to supply — it comes
    from the tagger noting which way the team kicked off, and there is no way to
    infer it from geometry alone.
    """

    home_attacks_first_half: str = "right"

    def attacking_end(self, side: str, period: str) -> str:
        if side not in ("us", "them"):
            raise ValueError(f"side must be 'us' or 'them', got {side!r}")

        end = self.home_attacks_first_half
        if side == "them":
            end = _flip(end)
        if period in _SECOND_HALF_PERIODS:
            end = _flip(end)
        return end

    def defending_end(self, side: str, period: str) -> str:
        return _flip(self.attacking_end(side, period))


def _flip(end: str) -> str:
    if end == "left":
        return "right"
    if end == "right":
        return "left"
    raise ValueError(f"end must be 'left' or 'right', got {end!r}")
