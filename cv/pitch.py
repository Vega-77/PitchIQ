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

from dataclasses import dataclass, fields

# What the Laws of the Game specify regardless of overall pitch size — and
# therefore only the *defaults*. Paint on a real field is measured by whoever
# had the tape that morning, and a school pitch shared with another sport is
# routinely marked short, narrow, or off-centre. These are what `Pitch` starts
# from, not what it insists on: every one of them is a field you can override.
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

    **The markings are fields, not constants.** Assuming Laws-standard paint on
    a pitch that does not have it is not a small error and it is not a random
    one: every landmark of the mismarked family is displaced the same way, so
    the homography tilts to split the difference and *every* position on the
    pitch pays for it — including the ones nobody clicked. Worse, the picker
    reports the resulting residual as the coach's clicking, and no amount of
    re-clicking can move it. Measuring the box and putting the real number here
    is the fix; `calibrate/pitch-model.js::measureMarkings` measures it from the
    clicks themselves.

    One set of markings serves both ends. The two boxes on a real field are
    marked by the same person with the same tape on the same morning, so they
    are usually wrong together, and with the eight or ten clicks a coach will
    actually place there is not enough evidence to fit them apart — splitting
    them would produce two confident-looking numbers where the data supports
    one. `measureMarkings` still checks the ends against each other and says so
    when they disagree, which is the honest half of that: reporting the
    asymmetry without pretending to have measured it twice.
    """

    length_m: float = 105.0
    width_m: float = 68.0

    goal_width_m: float = GOAL_WIDTH_M
    goal_area_length_m: float = GOAL_AREA_LENGTH_M
    goal_area_width_m: float = GOAL_AREA_WIDTH_M
    penalty_area_length_m: float = PENALTY_AREA_LENGTH_M
    penalty_area_width_m: float = PENALTY_AREA_WIDTH_M
    penalty_spot_m: float = PENALTY_SPOT_M
    centre_circle_radius_m: float = CENTRE_CIRCLE_RADIUS_M

    @classmethod
    def from_mapping(cls, data) -> "Pitch":
        """A pitch from a JSON-ish mapping, ignoring keys it does not know.

        Every field is optional and falls back to the Laws, which is what makes
        this safe to point at a file written before the field existed. Reading
        by field name rather than by a hand-kept list is deliberate: a marking
        added to this class would otherwise be silently dropped by every reader
        that forgot to grow, and the failure would be a homography quietly
        wrong rather than an error anybody sees.
        """
        known = {f.name for f in fields(cls)}
        return cls(**{
            name: float(value) for name, value in (data or {}).items()
            if name in known and value is not None
        })

    @property
    def markings_are_standard(self) -> bool:
        """True when every marking is the Laws value this class defaults to.

        Used to decide whether a report needs to mention the pitch at all. A
        regulation pitch is the boring case and should read like one.
        """
        return (
            self.goal_width_m == GOAL_WIDTH_M
            and self.goal_area_length_m == GOAL_AREA_LENGTH_M
            and self.goal_area_width_m == GOAL_AREA_WIDTH_M
            and self.penalty_area_length_m == PENALTY_AREA_LENGTH_M
            and self.penalty_area_width_m == PENALTY_AREA_WIDTH_M
            and self.penalty_spot_m == PENALTY_SPOT_M
            and self.centre_circle_radius_m == CENTRE_CIRCLE_RADIUS_M
        )

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
        half = self.goal_width_m / 2
        return ((x, cy - half), (x, cy + half))

    # ---------------------------------------------------------------- landmarks

    def landmarks(self) -> dict[str, tuple[float, float]]:
        """Named points a human can identify unambiguously in a frame.

        Keys are stable — calibration files store them — so rename with care.
        """
        L, W = self.length_m, self.width_m
        cy = W / 2
        pa_half = self.penalty_area_width_m / 2
        ga_half = self.goal_area_width_m / 2
        pa_len = self.penalty_area_length_m
        ga_len = self.goal_area_length_m
        spot = self.penalty_spot_m
        circle = self.centre_circle_radius_m
        goal_half = self.goal_width_m / 2

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
            "centre_circle_bottom": (L / 2, cy - circle),
            "centre_circle_top": (L / 2, cy + circle),

            # Left penalty area
            "pen_left_bottom_goalline": (0.0, cy - pa_half),
            "pen_left_top_goalline": (0.0, cy + pa_half),
            "pen_left_bottom_corner": (pa_len, cy - pa_half),
            "pen_left_top_corner": (pa_len, cy + pa_half),
            "pen_spot_left": (spot, cy),

            # Right penalty area
            "pen_right_bottom_goalline": (L, cy - pa_half),
            "pen_right_top_goalline": (L, cy + pa_half),
            "pen_right_bottom_corner": (L - pa_len, cy - pa_half),
            "pen_right_top_corner": (L - pa_len, cy + pa_half),
            "pen_spot_right": (L - spot, cy),

            # Goal areas (six-yard boxes)
            "goalarea_left_bottom_corner": (ga_len, cy - ga_half),
            "goalarea_left_top_corner": (ga_len, cy + ga_half),
            "goalarea_right_bottom_corner": (L - ga_len, cy - ga_half),
            "goalarea_right_top_corner": (L - ga_len, cy + ga_half),

            # Goalposts
            "goalpost_left_bottom": (0.0, cy - goal_half),
            "goalpost_left_top": (0.0, cy + goal_half),
            "goalpost_right_bottom": (L, cy - goal_half),
            "goalpost_right_top": (L, cy + goal_half),
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
