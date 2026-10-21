# `pitchiq/distortion.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Lens distortion, measured from the painted lines themselves.

A wide lens does not degrade a calibration, it disqualifies it. Measured on the
synthetic camera in `tests/test_distortion.py`: mild barrel puts the mean
reprojection error past the 0.5m bar `CalibrationError.is_usable` sets *with
every landmark clicked perfectly*. No amount of careful clicking recovers it,
because the error is not in the clicks.

This was closed once, for a good reason: correcting it needs a per-camera lens
calibration, that means an OpenCV chessboard, and nobody can wave a chessboard
at a camera that does not exist yet. What reopened it is `pitchiq.lines`. A painted
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
frame actually answered the question. Same rule as `refine` in `pitchiq.lines` --
a tool that silently replaces a coach's calibration with a worse one is worse
than a tool that does nothing.

Everything here is one parameter. Real lenses want two or three, plus a
principal point that is not quite the image centre, and fitting those from one
frame of a pitch would fit the noise beautifully. One coefficient is what a
single frame of paint can support.

---

## Moved from comments

Moved verbatim from comments beside the code in the deep clean (C-2).

### Turn caps

The tracer follows curvature, which is the point, and must not follow it
around a corner flag onto the goal line. Two caps, because one will not do.

Per step: a centroid taken from rasterised paint wobbles a few tenths of a
pixel, which over a six-pixel step is already about three degrees of apparent
turn. A cap tight enough to be interesting is therefore tighter than the
tracer's own noise, and simply stops every chain at random. Twenty degrees is
noise-proof and still nothing like a right angle.

Cumulative, from the seed: this is the one that does the work. A lens bends a
whole touchline by a handful of degrees end to end. Anything that has turned
thirty-five degrees since it started is not a straight line seen through a
lens -- it is the centre circle, or the tracer has stepped onto another line.

### Straight enough

Paint rasterises to a few pixels wide and a traced centreline wobbles inside
it. Below this the lines are already as straight as the tracer can measure,
and any `k1` that "improves" on it is fitting that wobble.

As a fraction of half the image diagonal, because the bow a given lens puts
in a line is a fraction of the frame rather than a number of pixels: the same
camera measured at 1080p bows its lines half again as far as at 720p, and a
constant here would quietly get stricter as the footage got better. Half the
diagonal is the same normalisation `DistortionModel.scale` uses.

8e-4 is 0.59px at 1280x720. Measured on the synthetic camera, the tracer's
own noise floor there is 0.31px -- that is what a perfectly rectilinear frame
scores, and it stays flat at 0.31-0.41px across the whole sweep -- so this
asks for a bow about twice the wobble before treating it as a lens.

### Clean start

Snap to the middle of the paint before walking. A seed's midpoint
sits wherever Hough put it, which can be a couple of pixels off the
centreline, and starting there throws the first step sideways by that
much -- over a six-pixel step, a three-pixel offset reads as a
twenty-seven-degree turn and the cap kills the trace before it takes
a second step.

The midpoint is also the likeliest place on the whole segment to be a
junction, since Hough merges a line across its crossings and the
crossings tend to sit in the middle of it, so a midpoint that cannot
be centred is normal rather than fatal: walk along the seed and start
from the first place the paint is legible on its own.
