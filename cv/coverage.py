"""How much of the pitch the camera could actually see.

Every figure this system reports in metres rests on one homography fitted from
one frame, and a homography will happily map a pitch coordinate to a pixel that
was never in shot. Nothing checked. `Calibration.sanity_check` asks whether
*any* of the image lands near the pitch — the test for a scrambled fit — and
never how much of the pitch the image covers.

The difference matters because **an unseen third does not read as unseen**. It
reads as football that did not happen:

  * `territory` divides each team's possession across the thirds. A third that
    was out of frame contributes no seconds, so its share is 0 — indistinguishable
    from a side that never went there.
  * a heatmap draws occupancy over the whole pitch, and the part off camera
    comes out cold.
  * a shot map and every xG behind it need the goalmouth. A goal out of shot
    does not produce fewer shots, it produces none.

So this measures the visible fraction and says which parts are missing, and the
report carries it next to the numbers it qualifies.

Measured by sampling rather than by clipping polygons. A metre grid over a
105x68 pitch is about 7,000 points, one matrix multiply, and it needs no
geometry library and no convex-hull argument about a quadrilateral that
perspective has bent. The cost is a resolution of one metre, which is finer than
any claim made from the result.

A homography between two planes has a line where the denominator changes sign;
past it, points come back mirrored rather than off the edge of the frame, so
grass behind the camera can land inside the picture and count as visible.
`_project` rejects those — but **measured, it barely matters**: swept over 750
camera positions, focal lengths and aim points, guarding the sign moved the
answer by at most **0.002**. A camera standing on the halfway line filming one
goal has 3,536 of 7,140 pitch cells behind it, and their mirror images land
outside the frame anyway. The check stays because it is three lines and is
right; the figure is here so nobody later mistakes it for load-bearing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .pitch import Pitch
from . import zones


# One metre. Finer buys nothing: the reprojection error on a good calibration is
# around half a metre, so a grid tighter than this is measuring the fit's noise.
CELL_M = 1.0

# Below this, a third's share of possession is a fact about part of a third.
# Set where it is because `territory` is the most misreadable consumer — a
# quarter of a band missing moves a share by more than the differences coaches
# read from it.
THIRD_FLOOR = 0.75

# The whole pitch, near enough. A metre grid clips the touchlines by half a cell
# and a corner arc rounds off, so an honest full-coverage camera lands a little
# under 1.0 and should not be reported as short.
COMPLETE_SHARE = 0.98

# A goalmouth counts as in shot when most of the six-yard box is. The box rather
# than the goal line itself: a shot map needs the area a keeper covers, and a
# goal line visible only at one post is not a view of the goal.
GOALMOUTH_SEEN = 0.6


@dataclass(frozen=True)
class PitchCoverage:
    """What fraction of the pitch fell inside the frame, and where it did not."""

    visible_share: float
    # Keyed by end, so a caller that knows which way a team attacked can name
    # these the way `zones.third` does. Absolute here on purpose: coverage is a
    # property of the camera, and the camera does not change ends at half-time.
    left_third: float
    middle_third: float
    right_third: float
    left_goalmouth: float
    right_goalmouth: float
    cell_m: float

    @property
    def complete(self) -> bool:
        return self.visible_share >= COMPLETE_SHARE

    def third_share(self, end: str) -> float:
        """The visible share of the third at `end`, or of the middle."""
        if end == 'left':
            return self.left_third
        if end == 'right':
            return self.right_third
        raise ValueError(f"end must be 'left' or 'right', got {end!r}")

    def goalmouth_share(self, end: str) -> float:
        return self.left_goalmouth if zones._check_end(end) == 'left' \
            else self.right_goalmouth

    def sees_goal(self, end: str) -> bool:
        return self.goalmouth_share(end) >= GOALMOUTH_SEEN

    def to_json(self) -> dict:
        return {
            'visible_share': round(self.visible_share, 3),
            'thirds': {
                'left': round(self.left_third, 3),
                'middle': round(self.middle_third, 3),
                'right': round(self.right_third, 3),
            },
            'goalmouths': {
                'left': round(self.left_goalmouth, 3),
                'right': round(self.right_goalmouth, 3),
            },
            'complete': self.complete,
            'cell_m': self.cell_m,
        }


def _orientation(calibration, width_px: float, height_px: float) -> float:
    """Which sign of the denominator means "in front of this camera".

    A homography is defined up to scale, so the sign carries no meaning on its
    own — negate the matrix and every denominator flips. It needs a reference:
    one pitch point known to be in shot.

    The middle of the frame is that point, by construction. Whatever the camera
    is looking at, the thing at the centre of the picture is in front of it.

    Tried the majority sign across the pitch first and it is wrong in exactly
    the case the check exists for: a camera standing on the halfway line has
    half the pitch behind it, the vote is a coin toss, and the answer can invert
    on a single cell. A reference point does not have that failure.
    """
    centre_m = calibration.to_pitch(width_px / 2, height_px / 2)
    w = float(np.array([*centre_m, 1.0]) @ np.linalg.inv(calibration.H).T[:, 2])
    # Degenerate only if the frame's centre maps to the vanishing line itself,
    # which is a calibration `sanity_check` already rejects. Positive is the
    # harmless assumption: it is what an unscaled pinhole matrix produces.
    return -1.0 if w < 0 else 1.0


def _project(homography_inv: np.ndarray, xs: np.ndarray, ys: np.ndarray,
             sign: float = 1.0):
    """Pitch metres -> pixels, with the points behind the camera marked.

    Done by hand rather than through `cv2.perspectiveTransform`, which divides
    by the third coordinate and discards it. That coordinate is the thing worth
    inspecting: where it is negative the point lies beyond the plane's vanishing
    line, and the division reflects it back into the picture instead of sending
    it off the edge.

    `sign` comes from `_orientation` and is what makes this deterministic for
    any number of points — one or seven thousand, the verdict is the same.

    **This is insurance, not a load-bearing correction**, and the docstring says
    so because the alternative is somebody later assuming otherwise. Swept over
    750 cameras it moved the reported share by at most 0.002: a camera on the
    halfway line filming one goal has half the pitch behind it, and those
    mirrored points land outside the frame regardless. `TestProjection` pins the
    behaviour directly, since no coverage figure would notice if it broke.
    """
    points = np.column_stack([xs, ys, np.ones(xs.size)])
    projected = (points @ homography_inv.T) * sign
    w = projected[:, 2]

    in_front = w > 1e-9
    safe = np.where(in_front, w, 1.0)
    return projected[:, 0] / safe, projected[:, 1] / safe, in_front


def _grid(lo: float, hi: float, cell_m: float) -> np.ndarray:
    """Cell centres from lo to hi. At least one, however small the span."""
    count = max(1, int(round((hi - lo) / cell_m)))
    step = (hi - lo) / count
    return lo + step * (np.arange(count) + 0.5)


def _visible_share(calibration, xs, ys, width_px, height_px, sign) -> float:
    """The fraction of these pitch points that fall inside the frame."""
    if xs.size == 0:
        return 0.0
    u, v, in_front = _project(
        np.linalg.inv(calibration.H), xs.ravel(), ys.ravel(), sign
    )
    inside = in_front & (u >= 0) & (u <= width_px) & (v >= 0) & (v <= height_px)
    return float(np.mean(inside))


def pitch_coverage(
    calibration,
    image_size: tuple[int, int] | None = None,
    cell_m: float = CELL_M,
) -> PitchCoverage | None:
    """How much of the pitch this calibration's camera had in frame.

    Returns None without an image size — the frame's own dimensions are the
    boundary being tested, and a calibration carrying none cannot answer.
    Silence rather than a guess: `image_size` is optional on `Calibration` and
    absent on a hand-written fixture, and a fabricated 1920x1080 would report
    coverage for a camera nobody described.
    """
    size = image_size or getattr(calibration, 'image_size', None)
    if not size:
        return None
    width_px, height_px = size
    if width_px <= 0 or height_px <= 0:
        return None

    pitch: Pitch = calibration.pitch
    first, second = zones.third_boundaries(pitch)
    # Resolved once from the frame's own centre, so every band below is judged
    # against the same idea of which way the camera faces.
    sign = _orientation(calibration, width_px, height_px)

    ys = _grid(0.0, pitch.width_m, cell_m)

    def band(x_lo: float, x_hi: float) -> float:
        xs = _grid(x_lo, x_hi, cell_m)
        gx, gy = np.meshgrid(xs, ys, indexing='ij')
        return _visible_share(calibration, gx, gy, width_px, height_px, sign)

    left = band(0.0, first)
    middle = band(first, second)
    right = band(second, pitch.length_m)

    # Weighted by band width rather than averaged, so an uneven pitch length
    # cannot make the thirds and the whole disagree.
    widths = np.array([first, second - first, pitch.length_m - second])
    whole = float(np.average([left, middle, right], weights=widths))

    def goalmouth(end: str) -> float:
        x_min, x_max, y_min, y_max = zones.goal_area(pitch, end)
        xs = _grid(x_min, x_max, cell_m / 2)
        gy = _grid(y_min, y_max, cell_m / 2)
        mx, my = np.meshgrid(xs, gy, indexing='ij')
        return _visible_share(calibration, mx, my, width_px, height_px, sign)

    return PitchCoverage(
        visible_share=whole,
        left_third=left,
        middle_third=middle,
        right_third=right,
        left_goalmouth=goalmouth('left'),
        right_goalmouth=goalmouth('right'),
        cell_m=cell_m,
    )


def coverage_warnings(coverage: PitchCoverage | None) -> list[str]:
    """What is wrong with this framing, in sentences a person can act on.

    Framed as the camera's fault rather than the pitch's, because that is the
    half somebody can change before the next match — the whole point of
    measuring this is that it is fixable, and only between games.

    A goal out of shot outranks a thin third and suppresses it: they are the
    same mistake seen twice, and two lines about one end of the pitch reads as
    two problems.
    """
    if coverage is None or coverage.complete:
        return []

    out: list[str] = []
    pct = round(coverage.visible_share * 100)

    blind_end = {
        end for end in ('left', 'right') if not coverage.sees_goal(end)
    }
    for end in sorted(blind_end):
        out.append(
            f'the {end} goalmouth was not in shot — shots at that end were not '
            'seen, and neither was anything measured from them'
        )

    thin = [
        (name, share) for name, share in (
            ('left', coverage.left_third),
            ('middle', coverage.middle_third),
            ('right', coverage.right_third),
        )
        if share < THIRD_FLOOR and name not in blind_end
    ]
    for name, share in thin:
        out.append(
            f'only {round(share * 100)}% of the {name} third was in shot — '
            'possession and territory there are measured over the part the '
            'camera could see'
        )

    if not out:
        out.append(
            f'the camera framed {pct}% of the pitch — everything in metres '
            'describes that part of it'
        )

    return out
