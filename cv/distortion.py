"""Lens distortion, measured from the painted lines themselves.

A wide lens does not degrade a calibration, it disqualifies it. Measured on the
synthetic camera in `tests/test_distortion.py`: mild barrel puts the mean
reprojection error past the 0.5m bar `CalibrationError.is_usable` sets *with
every landmark clicked perfectly*. No amount of careful clicking recovers it,
because the error is not in the clicks.

This was closed once, for a good reason: correcting it needs a per-camera lens
calibration, that means an OpenCV chessboard, and nobody can wave a chessboard
at a camera that does not exist yet. What reopened it is `cv.lines`. A painted
touchline is straight, the Laws say so, and a straight line in the world is
straight in the image *only* if the lens is rectilinear. So the calibration
frame a coach already has to grab -- one frame, pitch lines visible -- is a
lens calibration target. It has been sitting in the workflow the whole time.

That is the plumb-line method, and it is worth being clear about what it buys
over the failed diagnostics of 2026-08-12. Those tried to separate a lens from
sloppy clicking using the *residuals of the homography fit*, and all three
statistics failed, because a homography absorbs a great deal of distortion and
what it cannot absorb looks like noise. This does not go through the homography
at all. Click jitter cannot bend a painted line; a lens can, and only a lens
does.

What it cannot do is see distortion the frame does not exhibit. A line through
the image centre stays straight under radial distortion no matter how strong it
is, so a frame whose only visible paint runs through the middle carries no
information about `k1` and this returns nothing rather than a confident zero.
`DistortionFit.confident` is that judgement, and `lens_for_frame` is the only
entry point that should be wired into anything: it returns `None` unless the
frame actually answered the question. Same rule as `refine` in `cv.lines` --
a tool that silently replaces a coach's calibration with a worse one is worse
than a tool that does nothing.

Everything here is one parameter. Real lenses want two or three, plus a
principal point that is not quite the image centre, and fitting those from one
frame of a pitch would fit the noise beautifully. One coefficient is what a
single frame of paint can support.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Search bound on the coefficient. Past about 0.5 the division model stops
# being invertible over the whole frame, and a fisheye that bad is not a camera
# anyone should be filming a match on.
MAX_K1 = 0.5

# A traced run of paint has to be this long to say anything. A short chain is
# straight under every `k1` worth considering -- the whole signal is how much a
# *long* line bows -- so a floor here is not tidying, it is the measurement.
MIN_CHAIN_LENGTH_PX = 120.0

# The floor for a Hough *seed*, which has to be far lower, and the reason is the
# one thing about this that is genuinely counter-intuitive. Hough finds straight
# chords. The harder a line is bent, the shorter the straight chords inside it,
# so the lines carrying the most information about the lens are the ones Hough
# reports as shortest. Measured on the synthetic camera at k1 = -0.2: a 235px
# seed traced out to 894px of real paint. Seeding at the chain floor would have
# thrown that line away and kept only the ones that had nothing to say.
SEED_LENGTH_PX = 60.0

# How far either side of the paint the tracer looks, and how far it steps.
TRACE_BAND_PX = 14.0
TRACE_STEP_PX = 6.0

# Widest run of lit pixels the tracer will accept as "one painted line seen
# side-on". Anything wider is a junction -- the slice is running *along* a
# crossing line rather than across this one -- and its centroid is not on any
# centreline. See `_centre_of_paint`.
MAX_PAINT_PX = 14.0

# The tracer follows curvature, which is the point, and must not follow it
# around a corner flag onto the goal line. Two caps, because one will not do.
#
# Per step: a centroid taken from rasterised paint wobbles a few tenths of a
# pixel, which over a six-pixel step is already about three degrees of apparent
# turn. A cap tight enough to be interesting is therefore tighter than the
# tracer's own noise, and simply stops every chain at random. Twenty degrees is
# noise-proof and still nothing like a right angle.
#
# Cumulative, from the seed: this is the one that does the work. A lens bends a
# whole touchline by a handful of degrees end to end. Anything that has turned
# thirty-five degrees since it started is not a straight line seen through a
# lens -- it is the centre circle, or the tracer has stepped onto another line.
MAX_TURN_DEG = 20.0
MAX_BEND_DEG = 35.0

# What the frame has to supply before a verdict is allowed at all.
MIN_CHAINS = 3
MIN_POINTS = 150

# Paint rasterises to a few pixels wide and a traced centreline wobbles inside
# it. Below this the lines are already as straight as the tracer can measure,
# and any `k1` that "improves" on it is fitting that wobble.
#
# As a fraction of half the image diagonal, because the bow a given lens puts
# in a line is a fraction of the frame rather than a number of pixels: the same
# camera measured at 1080p bows its lines half again as far as at 720p, and a
# constant here would quietly get stricter as the footage got better. Half the
# diagonal is the same normalisation `DistortionModel.scale` uses.
#
# 8e-4 is 0.59px at 1280x720. Measured on the synthetic camera, the tracer's
# own noise floor there is 0.31px -- that is what a perfectly rectilinear frame
# scores, and it stays flat at 0.31-0.41px across the whole sweep -- so this
# asks for a bow about twice the wobble before treating it as a lens.
STRAIGHT_ENOUGH_FRACTION = 8e-4

# How much straighter the correction has to make things. Not a p-value, a
# margin: at 1.0 every frame yields a coefficient, most of them noise.
GAIN_REQUIRED = 1.6

# Leave-one-chain-out agreement, the same idea as `Calibration.holdout_error`.
# A coefficient that swings when one line is dropped was that line's opinion.
SPREAD_FRACTION = 0.5


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DistortionModel:
    """One-parameter radial distortion, in the division form.

    Brown's polynomial is the usual choice and is the wrong one here. Brown
    maps ideal to observed, so *undistorting* a point -- which is the direction
    everything in this repo needs, pixels inward to metres -- requires
    iterating. The division model inverts in closed form both ways:

        undistort:  r_ideal = r_obs / (1 + k1 * r_obs^2)
        distort:    r_obs   = (1 - sqrt(1 - 4*k1*r_ideal^2)) / (2*k1*r_ideal)

    Radii are normalised by `scale`, half the image diagonal, so `k1` means the
    same thing at 720p and 4K and the numbers in the tests stay comparable. Sign
    follows the usual convention: **negative is barrel**, the direction every
    action camera errs in.

    `centre` is the image centre, not a fitted principal point. One frame of
    paint cannot support both, and getting the principal point wrong by a few
    pixels costs far less than fitting it to noise.
    """

    k1: float
    centre: tuple[float, float]
    scale: float

    @staticmethod
    def for_image(image_size, k1: float = 0.0) -> "DistortionModel":
        width, height = float(image_size[0]), float(image_size[1])
        return DistortionModel(
            k1=float(k1),
            centre=(width / 2.0, height / 2.0),
            scale=math.hypot(width, height) / 2.0,
        )

    @property
    def is_identity(self) -> bool:
        return abs(self.k1) < 1e-12

    def undistort(self, points) -> np.ndarray:
        """Observed pixels -> the pixels a rectilinear lens would have given."""
        pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        if self.is_identity:
            return pts.copy()

        offset = pts - np.array(self.centre, dtype=np.float64)
        r2 = np.sum((offset / self.scale) ** 2, axis=1)
        return np.array(self.centre) + offset / (1.0 + self.k1 * r2)[:, None]

    def distort(self, points) -> np.ndarray:
        """The inverse: ideal pixels -> where this lens actually puts them."""
        pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        if self.is_identity:
            return pts.copy()

        offset = pts - np.array(self.centre, dtype=np.float64)
        r_ideal = np.linalg.norm(offset, axis=1) / self.scale

        disc = 1.0 - 4.0 * self.k1 * r_ideal ** 2
        # Pincushion strong enough to fold the frame onto itself has no real
        # inverse out at the corners. Clamping rather than raising keeps a
        # drawing routine drawing something visibly wrong instead of crashing;
        # `MAX_K1` is what stops the search reaching here in the first place.
        disc = np.maximum(disc, 0.0)

        # The quadratic solves for the distorted *radius*; what scales the
        # offset vector is the ratio of the two radii, so this divides by
        # r_ideal twice. As r_ideal -> 0 the ratio tends to 1 (expand the root
        # as sqrt(1-x) ~ 1 - x/2) but the expression goes 0/0, hence the guard.
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = (1.0 - np.sqrt(disc)) / (2.0 * self.k1 * r_ideal ** 2)
        ratio = np.where(r_ideal < 1e-9, 1.0, ratio)

        return np.array(self.centre) + offset * ratio[:, None]

    def to_json(self) -> dict:
        return {"k1": self.k1, "centre": list(self.centre), "scale": self.scale}

    @staticmethod
    def from_json(data: dict | None) -> "DistortionModel | None":
        if not data:
            return None
        return DistortionModel(
            k1=float(data["k1"]),
            centre=(float(data["centre"][0]), float(data["centre"][1])),
            scale=float(data["scale"]),
        )


# ---------------------------------------------------------------------------
# Following the paint
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Chain:
    """A traced centreline of one run of paint. Straight in the world."""

    points: np.ndarray

    @property
    def span_px(self) -> float:
        """End to end, which is the length that matters -- not the arc."""
        return float(np.linalg.norm(self.points[-1] - self.points[0]))

    def residuals(self, model: DistortionModel | None = None) -> np.ndarray:
        pts = model.undistort(self.points) if model else self.points
        return _residuals(pts)

    def rms(self, model: DistortionModel | None = None) -> float:
        r = self.residuals(model)
        return float(np.sqrt(np.mean(r * r)))

    def bow_px(self, model: DistortionModel | None = None) -> float:
        """The sagitta: how far the middle of the line sags off its own chord.

        The one number here a person can check by eye against the frame.
        """
        return float(np.max(np.abs(self.residuals(model))))


def _residuals(points: np.ndarray) -> np.ndarray:
    """Perpendicular distance from each point to the best line through them.

    Total least squares, not a fit of y on x: these lines can be vertical, and
    a touchline that happens to run down the frame is not a special case worth
    having a bug in.
    """
    centred = points - points.mean(axis=0)
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    return centred @ vt[1]


def trace_chains(
    image: np.ndarray,
    *,
    min_length_px: float = MIN_CHAIN_LENGTH_PX,
    band_px: float = TRACE_BAND_PX,
    step_px: float = TRACE_STEP_PX,
    limit: int = 12,
) -> list[Chain]:
    """Walk each long run of paint and record where its centre goes.

    `cv.lines.detect_segments` cannot be used directly for this, and the reason
    is the whole difficulty: Hough finds *straight* runs by construction, so a
    bowed touchline comes back as several straight chords and the bow -- the
    only thing being measured -- is exactly what got thrown away. The Hough
    segments are still the right way to find where the paint is, so they are
    used as seeds and then abandoned; from the seed's midpoint this walks the
    mask itself, one small step at a time, letting the direction turn.

    Turning is what makes it work and what makes it dangerous. Capped at
    `MAX_TURN_DEG` per step, it follows a lens; uncapped it would round the
    corner flag onto the goal line and report the right angle as distortion.
    """
    from .lines import detect_segments, line_mask

    if image is None or image.size == 0:
        return []

    mask = line_mask(image)
    seeds = detect_segments(image, min_length_px=SEED_LENGTH_PX)

    chains: list[Chain] = []
    for seed in seeds:
        if len(chains) >= limit:
            break

        angle = math.radians(seed.angle_deg)
        direction = np.array([math.cos(angle), math.sin(angle)])

        # Snap to the middle of the paint before walking. A seed's midpoint
        # sits wherever Hough put it, which can be a couple of pixels off the
        # centreline, and starting there throws the first step sideways by that
        # much -- over a six-pixel step, a three-pixel offset reads as a
        # twenty-seven-degree turn and the cap kills the trace before it takes
        # a second step.
        #
        # The midpoint is also the likeliest place on the whole segment to be a
        # junction, since Hough merges a line across its crossings and the
        # crossings tend to sit in the middle of it, so a midpoint that cannot
        # be centred is normal rather than fatal: walk along the seed and start
        # from the first place the paint is legible on its own.
        start = _clean_start(mask, seed, direction, band_px)
        if start is None:
            continue
        if any(_near_any(start, c.points, band_px) for c in chains):
            continue  # another seed on paint already traced

        forward = _walk(mask, start, direction, band_px, step_px)
        backward = _walk(mask, start, -direction, band_px, step_px)

        points = np.array(backward[::-1] + [start] + forward)
        if len(points) < 8:
            continue
        chain = Chain(points)
        if chain.span_px < min_length_px:
            continue
        chains.append(chain)

    return chains


def _clean_start(mask, seed, direction, band_px):
    """A point on the seed where the paint can be centred. Midpoint first."""
    for fraction in (0.5, 0.35, 0.65, 0.2, 0.8, 0.1, 0.9):
        along = np.array([
            seed.x1 + (seed.x2 - seed.x1) * fraction,
            seed.y1 + (seed.y2 - seed.y1) * fraction,
        ])
        found = _centre_of_paint(mask, along, direction, band_px)
        if found is not None and found[1]:
            return found[0]
    return None


def _near_any(point: np.ndarray, points: np.ndarray, radius: float) -> bool:
    return bool(np.min(np.linalg.norm(points - point, axis=1)) <= radius)


def _walk(mask, start, direction, band_px, step_px) -> list[np.ndarray]:
    """One direction, until the paint runs out or the frame does."""
    height, width = mask.shape[:2]
    max_steps = int(math.hypot(width, height) / step_px) + 2

    point = np.asarray(start, dtype=np.float64)
    seed = np.asarray(direction, dtype=np.float64)
    seed = seed / np.linalg.norm(seed)
    heading = seed.copy()
    out: list[np.ndarray] = []

    for _ in range(max_steps):
        guess = point + heading * step_px
        found = _centre_of_paint(mask, guess, heading, band_px)
        if found is None:
            break
        centre, clean = found

        if not clean:
            # Crossing another line. Walk straight through on the current
            # heading rather than recentring: the crossing line's paint is in
            # the slice too, and letting it pull the centre sideways is how a
            # touchline turns into a goal line.
            point = guess
            if not (0 <= point[0] < width and 0 <= point[1] < height):
                break
            out.append(point)
            continue

        moved = centre - point
        distance = float(np.linalg.norm(moved))
        if distance < 1e-6:
            break
        step_heading = moved / distance

        if _angle_deg(step_heading, heading) > MAX_TURN_DEG:
            break  # a junction, not a lens
        # Smoothed rather than taken outright: the heading decides where to
        # look next, and letting one noisy centroid swing it starts a wander
        # that the next step then follows.
        blended = heading * 0.65 + step_heading * 0.35
        blended = blended / np.linalg.norm(blended)
        if _angle_deg(blended, seed) > MAX_BEND_DEG:
            break  # curved paint, or a line that was never one line

        point, heading = centre, blended
        if not (0 <= point[0] < width and 0 <= point[1] < height):
            break
        out.append(point)

    return out


def _angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(a, b))))))


def _centre_of_paint(mask, around, heading, band_px):
    """Centroid of the painted run crossing `around`, perpendicular to heading.

    Returns `(point, clean)`, or None when there is no paint in the slice at
    all. `clean` is False when the run reaches the edge of the band or is
    simply too wide to be one line seen side-on -- both mean the slice is
    running along a crossing line instead of across this one, and its centroid
    is not on anybody's centreline.

    That distinction is not fussiness. The longest Hough segment on this pitch
    is the near touchline, its midpoint sits exactly where the halfway line
    meets it, and a perpendicular slice there runs the full height of the
    halfway line. Snapping to that centroid moved the start seven pixels off
    the touchline, which the turn cap then read as a twenty-degree turn on the
    first step and killed the trace -- so the two longest lines in the frame
    were the only two that produced nothing.

    Only the run containing the closest lit pixel counts. A second line passing
    nearby is a different line, and averaging the two would invent a centreline
    between them that no paint is on.
    """
    height, width = mask.shape[:2]
    normal = np.array([-heading[1], heading[0]])

    offsets = np.arange(-band_px, band_px + 1.0, 1.0)
    samples = around[None, :] + normal[None, :] * offsets[:, None]

    cols = np.rint(samples[:, 0]).astype(int)
    rows = np.rint(samples[:, 1]).astype(int)
    inside = (cols >= 0) & (cols < width) & (rows >= 0) & (rows < height)
    lit = np.zeros(len(offsets), dtype=bool)
    lit[inside] = mask[rows[inside], cols[inside]] > 0
    if not lit.any():
        return None

    centre_index = int(np.argmin(np.abs(offsets)))
    nearest = int(np.argmin(np.where(lit, np.abs(np.arange(len(offsets)) - centre_index), 10 ** 6)))

    lo = nearest
    while lo > 0 and lit[lo - 1]:
        lo -= 1
    hi = nearest
    while hi + 1 < len(lit) and lit[hi + 1]:
        hi += 1

    clean = lo > 0 and hi < len(lit) - 1 and (hi - lo + 1) <= MAX_PAINT_PX
    return samples[lo:hi + 1].mean(axis=0), clean


# ---------------------------------------------------------------------------
# Fitting the coefficient
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DistortionFit:
    """What one frame of paint had to say about the lens."""

    k1: float
    residual_px: float
    baseline_px: float
    bow_px: float
    chains: int
    points: int
    spread: float
    image_size: tuple[int, int]

    @property
    def model(self) -> DistortionModel:
        return DistortionModel.for_image(self.image_size, self.k1)

    @property
    def straight_enough_px(self) -> float:
        """Below this much bow, the frame is straight as far as anyone can tell."""
        width, height = self.image_size
        return STRAIGHT_ENOUGH_FRACTION * math.hypot(width, height) / 2.0

    @property
    def gain(self) -> float:
        """How many times straighter the correction made the lines."""
        if not (self.residual_px > 0):
            return float("inf")
        return self.baseline_px / self.residual_px

    @property
    def confident(self) -> bool:
        """Did this frame answer the question, or merely get asked it?

        Four ways to say no, and the last two are the ones that matter.

          * **Not enough paint.** Fewer than `MIN_CHAINS` runs or
            `MIN_POINTS` traced centres is not a measurement.

          * **Already straight.** If the lines bow less than the tracer's own
            wobble there is nothing to correct, and a coefficient fitted here
            is fitting rasterisation.

          * **Not enough gain.** The correction has to make the lines
            substantially straighter than leaving them alone. A frame whose
            paint all runs near the image centre carries no information about
            `k1` -- radial distortion leaves such lines straight -- and it
            shows up here as a coefficient that barely helps.

          * **The lines disagree.** Refit with each chain dropped in turn,
            set aside the one drop that moved the answer most, and see whether
            the rest still agree. Same instinct as
            `Calibration.holdout_error`, and it is what catches the centre
            circle being traced as though it were a straight line: an arc
            wants a coefficient nothing else wants. See
            `_leave_one_out_spread` for why the most influential run is
            excused rather than counted against the frame.
        """
        if self.chains < MIN_CHAINS or self.points < MIN_POINTS:
            return False
        if self.baseline_px < self.straight_enough_px:
            return False
        if self.gain < GAIN_REQUIRED:
            return False
        return self.spread <= max(0.02, SPREAD_FRACTION * abs(self.k1))

    def summary(self) -> str:
        if self.chains == 0:
            return "no runs of paint long enough to measure"
        verdict = "confident" if self.confident else "not confident"
        return (
            f"k1 {self.k1:+.4f}, lines bow {self.bow_px:.1f}px and straighten "
            f"{self.baseline_px:.2f}px -> {self.residual_px:.2f}px "
            f"({self.gain:.1f}x) over {self.chains} runs, "
            f"+/-{self.spread:.4f} across them ({verdict})"
        )


def estimate(
    image: np.ndarray | None = None,
    *,
    chains: list[Chain] | None = None,
    image_size: tuple[int, int] | None = None,
    max_k1: float = MAX_K1,
) -> DistortionFit:
    """Fit one radial coefficient by straightening the traced paint.

    Pass a frame, or pass chains directly when the caller already has them.
    Never raises: a frame with nothing in it returns a fit reporting exactly
    that, which `confident` then declines.
    """
    if chains is None:
        if image is None:
            raise ValueError("estimate needs either an image or chains")
        chains = trace_chains(image)
    if image_size is None:
        if image is None:
            raise ValueError("estimate needs an image_size when given chains")
        image_size = (int(image.shape[1]), int(image.shape[0]))

    if not chains:
        return DistortionFit(0.0, float("nan"), float("nan"), float("nan"),
                             0, 0, float("inf"), image_size)

    kept = _drop_the_crooked(chains, image_size, max_k1)
    # Counted over `kept`, not over everything traced: a run that trimming threw
    # out contributed nothing to `k1`, and letting it help clear `MIN_POINTS`
    # would be counting evidence that was rejected for being bad.
    points = sum(len(c.points) for c in kept)
    k1 = _best_k1(kept, image_size, max_k1)
    model = DistortionModel.for_image(image_size, k1)

    spread = _leave_one_out_spread(kept, image_size, max_k1)

    return DistortionFit(
        k1=k1,
        residual_px=_pooled_rms(kept, model),
        baseline_px=_pooled_rms(kept, None),
        bow_px=max(c.bow_px() for c in kept),
        chains=len(kept),
        points=points,
        spread=spread,
        image_size=image_size,
    )


def lens_for_frame(image: np.ndarray, *, max_k1: float = MAX_K1):
    """The model this frame supports, or None. Returns (model, fit).

    The only function here worth wiring into anything. `None` is a real answer
    and by far the most common one: most frames of most pitches do not pin a
    lens coefficient, and applying an unpinned one moves every landmark for no
    reason.
    """
    fit = estimate(image, max_k1=max_k1)
    return (fit.model if fit.confident else None), fit


def _pooled_rms(chains: list[Chain], model: DistortionModel | None) -> float:
    total, count = 0.0, 0
    for chain in chains:
        r = chain.residuals(model)
        total += float(np.sum(r * r))
        count += len(r)
    return math.sqrt(total / count) if count else float("nan")


def _best_k1(chains: list[Chain], image_size, max_k1: float) -> float:
    """Grid then bisect. The objective is smooth and one-dimensional.

    Deliberately not an optimiser from a library: this has to give the same
    answer on every machine that runs the tests, and sixty evaluations of a
    few thousand points is not worth a dependency.
    """
    if not chains:
        return 0.0

    def cost(k1: float) -> float:
        return _pooled_rms(chains, DistortionModel.for_image(image_size, k1))

    grid = np.linspace(-max_k1, max_k1, 61)
    costs = [cost(k) for k in grid]
    best = int(np.argmin(costs))

    lo = grid[max(0, best - 1)]
    hi = grid[min(len(grid) - 1, best + 1)]
    for _ in range(40):
        a = lo + (hi - lo) / 3.0
        b = hi - (hi - lo) / 3.0
        if cost(a) < cost(b):
            hi = b
        else:
            lo = a
    return float((lo + hi) / 2.0)


def _drop_the_crooked(chains, image_size, max_k1) -> list[Chain]:
    """Throw out runs that no coefficient makes straight.

    The centre circle is the case this exists for. It is real paint, it is long,
    it traces beautifully, and it is *actually curved* -- there is no `k1` that
    straightens it, and including it drags the fit towards bending the whole
    frame to suit one arc. A run that is still bent at the best shared answer is
    not evidence about the lens.

    One round, and never below `MIN_CHAINS` survivors: trimming until the
    remainder agrees is how you get a confident answer out of noise.
    """
    if len(chains) <= MIN_CHAINS:
        return list(chains)

    model = DistortionModel.for_image(image_size, _best_k1(chains, image_size, max_k1))
    scores = [c.rms(model) for c in chains]
    ceiling = max(2.0, 2.5 * float(np.median(scores)))

    kept = [c for c, s in zip(chains, scores) if s <= ceiling]
    if len(kept) < MIN_CHAINS:
        order = np.argsort(scores)[:MIN_CHAINS]
        kept = [chains[i] for i in sorted(order)]
    return kept


def _leave_one_out_spread(chains, image_size, max_k1) -> float:
    """Range of the coefficient when each run is dropped in turn, less one.

    The trim is the whole design, and it took a measurement to find. The plain
    range answers the wrong question. On a frame at k1 = -0.05 the seven
    leave-one-out refits came out -0.051 six times and -0.080 once: the six say
    the lines agree completely, and the one says that dropping the near
    touchline -- the longest, most curved, most informative run in the frame --
    moves the answer. Of course it does. A line that carries the evidence is
    supposed to move the answer when it goes, and a statistic that treats that
    as disagreement rejects exactly the frames that had something to say.

    So the single most influential run is discarded before taking the range,
    which leaves the question this check was always meant to ask: with the
    informative line set aside, does everything *else* still tell one story?
    Two runs pulling in different directions survives the trim and is
    disagreement. One run pulling on its own does not, and is evidence.

    What this deliberately does not do is decide whether there was any signal
    at all -- that is `gain`'s job, and keeping the two separate is why a
    perfectly rectilinear frame is still refused. There the runs agree
    beautifully (trimmed spread 0.0000) and the correction buys nothing
    (1.0x), and it is the gain that turns it down.
    """
    if len(chains) < 3:
        return float("inf")
    answers = sorted(
        _best_k1(chains[:i] + chains[i + 1:], image_size, max_k1)
        for i in range(len(chains))
    )
    middle = answers[len(answers) // 2]
    furthest = max(range(len(answers)), key=lambda i: abs(answers[i] - middle))
    answers.pop(furthest)
    return float(max(answers) - min(answers))
