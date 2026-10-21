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
