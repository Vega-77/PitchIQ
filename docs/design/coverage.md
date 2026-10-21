# `pitchiq/coverage.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

How much of the pitch the camera could actually see.

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
