# `pitchiq/territory.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Where on the pitch each team had the ball.

Possession says *how much*. It says nothing about where, and the two come apart
in the way that matters most to a coach: a side pinned in its own half can hold
60% of the ball and be losing badly, and every possession figure this pipeline
has produced would have called that a good half.

`pitchiq/possession.py` has carried a note since it was written saying territory was
deliberately left out pending a homography. The homography arrived; this is the
part that was waiting for it.

    Named from each team's own direction.

A third is `defensive`, `middle` or `attacking` **relative to the team holding
the ball**, not a fixed end of the pitch. So "Team A spent 40% of its possession
in its attacking third" and "Team B spent 40% of its possession in its
attacking third" describe opposite ends of the same field, and both are the
sentence a coach wants. `zones.third` already works this way and this is only
applying it per frame.

    What it is not.

It is not territory in the sense of where play happened — it is where the ball
was **while a team held it**. Frames with no clear holder are excluded from both
teams, so a scramble in the box belongs to nobody. That is the conservative
reading: the alternative is attributing contested time to whoever happened to be
marginally nearer, which is the thing possession smoothing already exists to
avoid.

Every frame here needs a ball position in metres, so this needs a calibration
and it needs the ball to have been seen. Frames where it was interpolated are
included — a straight line between two sightings is a poor answer to "did it
cross the line" and a perfectly good one to "which third was it in".
