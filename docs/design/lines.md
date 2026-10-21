# `pitchiq/lines.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

The painted lines: finding them, and using them to check a calibration.

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

---

## Moved from comments

Moved verbatim from comments beside the code in the deep clean (C-2).

### Penalty arcs

The penalty arcs. Easy to leave out -- no landmark sits on one and no
calibration is fitted from one -- and leaving them out is not neutral:
they are metres of real paint that the detector finds and the model then
fails to explain, so every frame containing one reports coverage lower
than the calibration deserves. An unmodelled line is indistinguishable
from a wrong calibration.
Clamped at both ends, not just the top. On Laws markings the ratio is
+0.60 and the lower clamp is unreachable, but a measured pitch can put
the spot far enough behind the box line to drive it past -1, and acos
raises rather than degrading.
