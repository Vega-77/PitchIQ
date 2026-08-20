"""The painted lines: finding them, and using them to check a calibration.

A calibration is fitted from points a human clicked, and then measured against
those same points. `Calibration.error` says so in its own docstring, and
`holdout_error` exists because of it. But leave-one-out is still measured on
the human's own clicks: if every point was placed two metres up-pitch in the
same direction, both numbers stay small and the calibration is still wrong.

The painted lines are evidence the human did not supply. They are in the frame
whether or not anybody clicked them, they are where the Laws of the Game say
they are, and there are thousands of pixels of them. Projecting the ones the
camera can see into metres and asking how far they land from where the pitch
model says they should be is the first check on a calibration that does not
grade the human's homework with the human's own answers.

Two things are built on that:

  * `fit_to_lines` -- the check. Reports the distance in metres and, just as
    importantly, how much of what it looked at it could match at all. A
    calibration that is badly wrong does not produce a large error; it produces
    a small error over almost nothing, because the few points that happen to
    land near a line are the only ones counted. Read the coverage first.

  * `refine` -- the payoff. Given a rough calibration, pull it onto the lines.
    A coach clicking landmarks on a phone at the side of a pitch is placing
    points to within maybe ten pixels; the lines are exact. Refinement is
    accepted only if it measurably improves the fit, so a frame with no usable
    lines in it leaves the human's calibration alone rather than degrading it.

What this module deliberately does not do is propose landmarks from nothing.
Detecting lines is the easy half; deciding that *this* line is the halfway line
and not the edge of the penalty area is the hard half, and getting it wrong
produces a confident, exact, entirely fictional calibration. With a rough
calibration in hand the correspondence question is already answered, which is
why refinement is tractable and cold-start proposal is not.

The limit worth knowing before trusting any number here: a painted line pins a
calibration only perpendicular to itself. A pitch is mostly two long parallel
touchlines, so an error that slides everything up-pitch is close to invisible
to this check, and only the goal lines, the halfway line and the box edges push
back on it. That is why `LineFit` reports a tail as well as a middle, and why
this measures a calibration rather than replacing the clicks that made it.

Everything below is pure geometry over OpenCV primitives, so the whole module
is testable against a synthetic camera with exact ground truth -- see
`tests/test_lines.py`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .pitch import (
    CENTRE_CIRCLE_RADIUS_M,
    GOAL_AREA_LENGTH_M,
    GOAL_AREA_WIDTH_M,
    PENALTY_AREA_LENGTH_M,
    PENALTY_AREA_WIDTH_M,
    PENALTY_SPOT_M,
    Pitch,
)

# How far a projected pixel may land from the nearest painted line and still be
# counted as that line. Wide on purpose: this is the capture radius of the
# search, not the accuracy bar. Anything tighter and a calibration that is a
# couple of metres out matches nothing and reports a flawless fit over four
# points.
MATCH_RADIUS_M = 6.0

# Spacing along a detected segment, in pixels. Fine enough that a short segment
# still contributes several points, coarse enough that one long touchline does
# not swamp the sample.
SAMPLE_STEP_PX = 8.0

# The centre circle is drawn as a polygon. 48 sides keeps the chord error under
# a centimetre on a 9.15m radius, which is far below anything measured here.
CIRCLE_SIDES = 48


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Segment:
    """A straight run, in whatever units the caller is working in.

    Used for both pixels (what the detector found) and metres (what the pitch
    model says is there). Keeping one type for both is what lets the error
    metric be a single projection followed by a single distance.
    """

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    @property
    def midpoint(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    @property
    def angle_deg(self) -> float:
        """Direction with no sense of which way round, so 0 and 180 are equal."""
        return math.degrees(math.atan2(self.y2 - self.y1, self.x2 - self.x1)) % 180.0

    def sample(self, step: float) -> list[tuple[float, float]]:
        """Evenly spaced points along the segment, endpoints included."""
        n = max(1, int(self.length / max(step, 1e-9)))
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        return [(self.x1 + dx * i / n, self.y1 + dy * i / n) for i in range(n + 1)]

    def distance_to(self, x: float, y: float) -> float:
        """Distance from a point to this segment, not to its infinite line.

        The distinction matters: the goal line and the halfway line are
        parallel and 52 metres apart, but their infinite lines are the same
        distance from a point standing between them only if you ignore where
        the paint actually stops.
        """
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        denom = dx * dx + dy * dy
        if denom < 1e-12:
            return math.hypot(x - self.x1, y - self.y1)
        t = ((x - self.x1) * dx + (y - self.y1) * dy) / denom
        t = min(1.0, max(0.0, t))
        return math.hypot(x - (self.x1 + t * dx), y - (self.y1 + t * dy))

    def closest_point(self, x: float, y: float) -> tuple[float, float]:
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        denom = dx * dx + dy * dy
        if denom < 1e-12:
            return (self.x1, self.y1)
        t = ((x - self.x1) * dx + (y - self.y1) * dy) / denom
        t = min(1.0, max(0.0, t))
        return (self.x1 + t * dx, self.y1 + t * dy)

    def to_json(self) -> dict:
        return {"x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}


# ---------------------------------------------------------------------------
# What is painted on a pitch
# ---------------------------------------------------------------------------

def pitch_line_segments(pitch: Pitch) -> list[Segment]:
    """Every painted line, in metres.

    Derived from `Pitch` and the Laws constants rather than hardcoded, so a
    measured pitch of unusual size gets its own model and the check stays
    honest on it.
    """
    length, width = pitch.length_m, pitch.width_m
    cy = width / 2
    pa_half = PENALTY_AREA_WIDTH_M / 2
    ga_half = GOAL_AREA_WIDTH_M / 2

    segments = [
        # Touchlines and goal lines.
        Segment(0.0, 0.0, length, 0.0),
        Segment(0.0, width, length, width),
        Segment(0.0, 0.0, 0.0, width),
        Segment(length, 0.0, length, width),
        # Halfway.
        Segment(length / 2, 0.0, length / 2, width),
    ]

    for goal_x, inward in ((0.0, 1.0), (length, -1.0)):
        pa_x = goal_x + inward * PENALTY_AREA_LENGTH_M
        ga_x = goal_x + inward * GOAL_AREA_LENGTH_M
        segments += [
            Segment(goal_x, cy - pa_half, pa_x, cy - pa_half),
            Segment(pa_x, cy - pa_half, pa_x, cy + pa_half),
            Segment(pa_x, cy + pa_half, goal_x, cy + pa_half),
            Segment(goal_x, cy - ga_half, ga_x, cy - ga_half),
            Segment(ga_x, cy - ga_half, ga_x, cy + ga_half),
            Segment(ga_x, cy + ga_half, goal_x, cy + ga_half),
        ]

    segments += _arc(length / 2, cy, CENTRE_CIRCLE_RADIUS_M, 0.0, 2 * math.pi)

    # The penalty arcs. Easy to leave out -- no landmark sits on one and no
    # calibration is fitted from one -- and leaving them out is not neutral:
    # they are metres of real paint that the detector finds and the model then
    # fails to explain, so every frame containing one reports coverage lower
    # than the calibration deserves. An unmodelled line is indistinguishable
    # from a wrong calibration.
    half_angle = math.acos(
        min(1.0, (PENALTY_AREA_LENGTH_M - PENALTY_SPOT_M) / CENTRE_CIRCLE_RADIUS_M)
    )
    for spot_x, facing in ((PENALTY_SPOT_M, 0.0),
                           (length - PENALTY_SPOT_M, math.pi)):
        segments += _arc(
            spot_x, cy, CENTRE_CIRCLE_RADIUS_M,
            facing - half_angle, facing + half_angle,
        )

    return segments


def _arc(
    cx: float, cy: float, radius: float, start: float, end: float
) -> list[Segment]:
    """A circle or part of one, as straight segments.

    The chord error of a 48-sided circle at 9.15m is under two centimetres,
    which is an order of magnitude below anything this module claims to
    measure, so the polygon is not an approximation worth apologising for.
    """
    sides = max(4, int(round(CIRCLE_SIDES * abs(end - start) / (2 * math.pi))))
    points = [
        (cx + radius * math.cos(start + (end - start) * i / sides),
         cy + radius * math.sin(start + (end - start) * i / sides))
        for i in range(sides + 1)
    ]
    return [Segment(a[0], a[1], b[0], b[1]) for a, b in zip(points, points[1:])]


def draw_pitch_lines(
    image: np.ndarray,
    calibration,
    colour: tuple[int, int, int] = (0, 0, 255),
    thickness: int = 2,
) -> np.ndarray:
    """Draw the pitch model onto a frame through a calibration, in place.

    The eye is a better judge of a homography than any single number: a
    calibration whose outline sits on the paint is right, and one that does not
    is wrong in a way you can see immediately and point at. Segments that
    project outside the frame are clipped by OpenCV rather than skipped, so a
    calibration that folds the pitch behind the camera still draws something
    visibly wrong instead of drawing nothing.
    """
    for seg in pitch_line_segments(calibration.pitch):
        p1 = calibration.to_pixels(seg.x1, seg.y1)
        p2 = calibration.to_pixels(seg.x2, seg.y2)
        if not all(math.isfinite(v) for v in (*p1, *p2)):
            continue
        if max(abs(v) for v in (*p1, *p2)) > 1e6:
            continue
        cv2.line(
            image,
            (int(round(p1[0])), int(round(p1[1]))),
            (int(round(p2[0])), int(round(p2[1]))),
            colour,
            thickness,
            cv2.LINE_AA,
        )
    return image


# ---------------------------------------------------------------------------
# Finding the lines in a frame
# ---------------------------------------------------------------------------

def field_mask(image: np.ndarray) -> np.ndarray:
    """Where the grass is, with the paint filled back in.

    Painted lines are not green, so a plain grass mask has a hole exactly where
    every line is. Closing it puts the field back together, which turns the
    result into "the region of the frame that is pitch" -- the single most
    useful thing for throwing away the crowd, the scoreboard, and the white
    shirts of everyone standing behind the touchline.
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([30, 40, 40]), np.array([95, 255, 255]))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
    closed = cv2.morphologyEx(green, cv2.MORPH_CLOSE, kernel)
    return cv2.dilate(closed, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))


def line_mask(image: np.ndarray) -> np.ndarray:
    """Pale, unsaturated pixels that are on the field."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, np.array([0, 0, 150]), np.array([180, 70, 255]))
    on_field = cv2.bitwise_and(white, field_mask(image))
    return cv2.morphologyEx(
        on_field, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    )


def detect_segments(
    image: np.ndarray,
    *,
    min_length_px: float = 40.0,
    max_gap_px: float = 12.0,
    votes: int = 50,
    merge: bool = True,
) -> list[Segment]:
    """Straight runs of paint, longest first.

    Hough rather than a learned detector on purpose. It has no weights to ship,
    no inference cost worth measuring, and its failure mode is finding nothing
    -- which the caller can see and act on -- rather than finding something
    plausible and wrong.

    A player's white sock or a shirt sleeve is pale and on the field, so it
    survives the mask. It does not survive `min_length_px`: nothing on a person
    is forty pixels of straight edge at the framing this pipeline needs. That
    is the whole reason the length floor is not tunable down to nothing.
    """
    if image is None or image.size == 0:
        return []

    edges = cv2.Canny(line_mask(image), 50, 150, apertureSize=3)
    found = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=math.pi / 360,
        threshold=int(votes),
        minLineLength=float(min_length_px),
        maxLineGap=float(max_gap_px),
    )
    if found is None:
        return []

    segments = [
        Segment(float(a), float(b), float(c), float(d))
        for a, b, c, d in found.reshape(-1, 4)
    ]
    if merge:
        segments = merge_collinear(segments)
    return sorted(segments, key=lambda s: -s.length)


def merge_collinear(
    segments: list[Segment],
    *,
    angle_tol_deg: float = 3.0,
    offset_tol_px: float = 6.0,
) -> list[Segment]:
    """Fold the fragments of one painted line back into one segment.

    Canny gives two edges per stripe and Hough breaks each into pieces wherever
    a player stands on it, so a single touchline can arrive as a dozen
    segments. Left alone that is not wrong -- every fragment still measures its
    own distance to the model -- but it silently weights whichever line
    happened to fragment most, and it makes the returned list unreadable to a
    human trying to see what the detector actually found.

    Only fragments that overlap along their shared direction are folded
    together, which is why `head.distance_to` measures to the segment and not
    to its infinite line. Two pieces of one touchline with a player-sized gap
    between them stay two segments on purpose: bridging the gap would sample
    the hidden stretch and grade a calibration against paint nobody saw, and
    would inflate the coverage figure that is supposed to say how much was
    actually seen. `BallPoint.observed` draws the same line -- a straight run
    between two sightings is not itself a sighting.
    """
    if not segments:
        return []

    groups: list[list[Segment]] = []
    for seg in sorted(segments, key=lambda s: -s.length):
        for group in groups:
            head = group[0]
            delta = abs(seg.angle_deg - head.angle_deg)
            if min(delta, 180.0 - delta) > angle_tol_deg:
                continue
            if head.distance_to(*seg.midpoint) > offset_tol_px:
                continue
            group.append(seg)
            break
        else:
            groups.append([seg])

    merged: list[Segment] = []
    for group in groups:
        merged.append(_average_line(group))
    return merged


def _average_line(group: list[Segment]) -> Segment:
    """One segment down the middle of a group of fragments.

    Adopting the longest fragment's own line would be simpler and is wrong in a
    way that quietly poisons every metre this module reports. Canny finds the
    two *edges* of a painted stripe, not its centre, so the longest fragment
    sits half a line-width off to one side -- and a calibration shifted onto
    that edge then scores better than the true one. Averaging the fragments,
    weighted by length, puts the result back on the paint.
    """
    points: list[tuple[float, float]] = []
    weights: list[float] = []
    for seg in group:
        for point in ((seg.x1, seg.y1), (seg.x2, seg.y2)):
            points.append(point)
            weights.append(max(seg.length, 1.0))

    pts = np.array(points, dtype=np.float64)
    wts = np.array(weights, dtype=np.float64)
    centre = (pts * wts[:, None]).sum(axis=0) / wts.sum()

    centred = (pts - centre) * np.sqrt(wts)[:, None]
    # The principal direction of the fragment cloud is the line's direction;
    # for a pair of parallel edges it is their shared direction exactly.
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    ux, uy = float(vt[0][0]), float(vt[0][1])

    ts = (pts - centre) @ np.array([ux, uy])
    lo, hi = float(ts.min()), float(ts.max())
    return Segment(
        centre[0] + ux * lo, centre[1] + uy * lo,
        centre[0] + ux * hi, centre[1] + uy * hi,
    )


# ---------------------------------------------------------------------------
# Scoring a calibration against them
# ---------------------------------------------------------------------------

@dataclass
class LineFit:
    """How well a calibration agrees with the paint the camera can see."""

    median_m: float
    p90_m: float
    sampled: int
    matched: int

    @property
    def coverage(self) -> float:
        """Share of sampled line pixels that landed near any painted line."""
        return self.matched / self.sampled if self.sampled else 0.0

    @property
    def is_usable(self) -> bool:
        """All three are required and none of them is sufficient.

        Coverage without accuracy is a calibration that finds the pitch and
        misplaces it. Accuracy without coverage is four lucky pixels: a
        homography shifted 25 metres up-pitch measures a median of 0.13m,
        three centimetres off the true calibration's own 0.10m and far inside
        any bar worth setting -- but over 42% of the points, because the only
        pixels left anywhere near a line are the ones that happen to be. Read the
        coverage first, always.

        The 90th percentile is here because the median alone cannot see a
        uniform shift along the touchlines. A line constrains a fit only
        perpendicular to itself, and a pitch is mostly two long parallel
        touchlines, so sliding the whole calibration three metres up-pitch
        leaves most sampled points exactly where they were: median 0.19m,
        which passes comfortably, against a 90th percentile of 3.01m, which
        does not.

        The two bars deliberately mirror `CalibrationError.is_usable` -- half a
        metre typical, a metre and a half at the tail -- so a calibration that
        passes one and fails the other is telling you something real rather
        than tripping over a different ruler.

        The coverage floor is low on purpose: a camera behind one goal
        genuinely cannot see the far penalty area, and demanding most of the
        pitch would fail an honest calibration of a real frame.
        """
        return self.coverage >= 0.5 and self.median_m <= 0.5 and self.p90_m <= 1.5

    def summary(self) -> str:
        if not self.sampled:
            return "no line pixels to check against"
        if not self.matched:
            return f"nothing matched, over {self.sampled} sampled points"
        return (
            f"median {self.median_m:.2f}m, 90th {self.p90_m:.2f}m "
            f"over {self.matched}/{self.sampled} sampled points "
            f"({self.coverage * 100:.0f}% matched)"
        )

    def to_json(self) -> dict:
        return {
            "median_m": None if math.isnan(self.median_m) else round(self.median_m, 4),
            "p90_m": None if math.isnan(self.p90_m) else round(self.p90_m, 4),
            "sampled": self.sampled,
            "matched": self.matched,
            "coverage": round(self.coverage, 4),
            "usable": self.is_usable,
        }


def _match_distances(calibration, segments, model, radius_m):
    """Project sampled pixels to metres and pair each with the nearest line.

    Returns the matched distances, the (pixel, target metre) pairs refinement
    needs, and how many points were sampled in total. The sampled count is
    returned rather than derived because the unmatched points are the whole
    story when a calibration is badly wrong, and they are gone by the time the
    caller sees the distances.
    """
    pixels: list[tuple[float, float]] = []
    for seg in segments:
        pixels.extend(seg.sample(SAMPLE_STEP_PX))
    if not pixels:
        return [], [], 0

    metres = calibration.to_pitch_many(pixels)

    distances: list[float] = []
    pairs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for pixel, (mx, my) in zip(pixels, metres):
        if not (math.isfinite(mx) and math.isfinite(my)):
            continue
        best, best_line = radius_m, None
        for line in model:
            gap = line.distance_to(mx, my)
            if gap < best:
                best, best_line = gap, line
        if best_line is None:
            continue
        distances.append(best)
        pairs.append((pixel, best_line.closest_point(mx, my)))

    return distances, pairs, len(pixels)


def fit_to_lines(
    calibration,
    segments: list[Segment],
    *,
    radius_m: float = MATCH_RADIUS_M,
) -> LineFit:
    """Measure a calibration against detected paint. Never raises.

    An empty result is a real answer and is reported as one: zero sampled
    points means the frame had no lines the detector could find, which is a
    fact about the frame and not a verdict on the calibration.
    """
    model = pitch_line_segments(calibration.pitch)
    distances, _, sampled = _match_distances(calibration, segments, model, radius_m)
    if not distances:
        return LineFit(float("nan"), float("nan"), sampled, 0)

    arr = np.array(distances)
    return LineFit(
        median_m=float(np.median(arr)),
        p90_m=float(np.percentile(arr, 90)),
        sampled=sampled,
        matched=len(distances),
    )


# ---------------------------------------------------------------------------
# Pulling a calibration onto them
# ---------------------------------------------------------------------------

def refine(
    calibration,
    segments: list[Segment],
    *,
    iterations: int = 6,
    radius_m: float = MATCH_RADIUS_M,
):
    """Nudge a calibration onto the paint. Returns (calibration, before, after).

    Iterated closest point, in the one form the problem allows: a detected
    pixel has no idea *where* along a line it belongs, so the target is the
    nearest point on the nearest line rather than a fixed landmark. That target
    slides freely as the fit improves, which is exactly right -- the constraint
    a painted line supplies is perpendicular to itself and nothing more.

    The returned calibration is the original unless the refined one measurably
    beats it. That guard is not defensive padding: ICP on a frame whose lines
    are mostly hidden will happily converge on a confident wrong answer, and a
    tool that silently replaces a coach's careful clicks with that is worse
    than one that does nothing.
    """
    from .calibration import Calibration

    before = fit_to_lines(calibration, segments, radius_m=radius_m)
    if not segments or before.matched < 8:
        return calibration, before, before

    model = pitch_line_segments(calibration.pitch)
    current = calibration
    for _ in range(max(1, iterations)):
        _, pairs, _ = _match_distances(current, segments, model, radius_m)
        if len(pairs) < 8:
            break
        src = np.array([p for p, _ in pairs], dtype=np.float64)
        dst = np.array([t for _, t in pairs], dtype=np.float64)
        matrix, _ = cv2.findHomography(src, dst, cv2.RANSAC, ransacReprojThreshold=0.5)
        if matrix is None:
            break
        candidate = Calibration(
            matrix, current.pitch, calibration.correspondences, calibration.image_size
        )
        if candidate.sanity_check():
            break
        current = candidate

    after = fit_to_lines(current, segments, radius_m=radius_m)
    if not _is_improvement(before, after):
        return calibration, before, before
    return current, before, after


def _is_improvement(before: LineFit, after: LineFit) -> bool:
    """Is the refined fit better in every way that matters, or only in one?

    Three conditions, and the last two are the ones that earn their keep.

      * **The middle has to come down.** This is the quantity ICP is
        minimising, so on its own it proves nothing except that the loop ran.

      * **The tail may not go up.** A line constrains a fit only perpendicular
        to itself, so a homography is free to slide along the touchlines,
        driving the median towards zero while sitting metres from the real
        pitch. Measured on the synthetic camera: one badly jittered click set
        refined to a median of 0.08m -- better than the true calibration
        scores -- with its worst landmark still seventeen metres out, and a
        90th percentile of 3.13m that never came down. The tail was the only
        line-based number that noticed.

      * **Coverage may not collapse.** One percent is allowed for sample
        points drifting in and out at the match radius; more than that and the
        fit improved by looking at less, which is the failure mode
        `LineFit.is_usable` is built around.

    When any of them fails, the human's calibration is returned untouched. A
    refusal costs a coach nothing. A silent bad refinement costs them every
    number in the match report, and gives them no way to find out.
    """
    if not math.isfinite(after.median_m):
        return False
    if math.isfinite(before.median_m) and after.median_m >= before.median_m:
        return False
    if math.isfinite(before.p90_m) and after.p90_m > before.p90_m:
        return False
    return after.coverage >= before.coverage - 0.01
