# `pitchiq/camera.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Noticing that the camera moved, after the homography was already fitted.

The calibration is one homography, fitted once, from one frame. Every metre in
this pipeline goes through it: distance covered, top speed, sprint counts, shot
positions, xG, heatmaps, territory, team shape. It is the single assumption the
most numbers rest on, and it rests on the camera not moving.

Cameras move. Somebody walks into the tripod at half-time, a parent nudges it
reaching past, the wind catches a light rig, someone decides the framing looked
better a bit left. `FOOTAGE_DAY.md` asks for a fixed camera and the roadmap
makes it a hard requirement, which is the right rule and not a mechanism: a rule
that is broken by accident, silently, needs something that notices.

    Why this is worse than being wrong.

A stale homography does not fail. It keeps returning coordinates, plausible
ones, and every figure downstream keeps its shape — a player still covers seven
kilometres, a shot still lands somewhere in the box. The numbers are simply
about a pitch that is no longer where the camera thinks it is, and nothing in
the report has ever said which half of a match that applies to.

    Doing it with what is already there.

No new model and no second pass over the pixels. Over a short interval the
**median** displacement of every tracked player is close to zero, because
twenty-two people move in twenty-two directions — one team pressing forward is
matched by the other dropping back, and the median is not the mean, so a single
sprinter cannot drag it. A whole frame shifting moves everybody by the same
vector at once, and the median goes with it.

So: sample the frames, take the median per-track displacement between one
sample and the next, and look for a step. Football produces a small, wandering
median; a bump produces one large value in a single step and then a small one
again from the new position.

A median on its own is not enough, and the tests found the case that proves it.
With exactly half the pitch sprinting one way and the other half still, the
median sits midway between the two groups — half the sprinters' speed, which
lands right on any threshold worth setting. The median describes the middle
player, and the middle player is not the frame.

What actually distinguishes a camera move is **agreement**: when the frame
shifts, *every* track moves by the same vector, not just enough of them to drag
a middle value. So a shift is only called when the median is large **and** most
tracks are individually within a short distance of it. In the half-and-half
sprint nobody is near the median — the still players are half a stride behind it
and the sprinting ones half a stride ahead — so agreement collapses and nothing
is reported. In a real bump agreement is near total.

Everything is measured in **player heights** rather than pixels, so the same
threshold holds for a camera at the halfway line and one up a floodlight, and
for 720p and 4K. `frame_table.FrameRecord.scale_px` is the same measure the touch
detector and the identity bridge already use.

    What this cannot do, stated because it matters.

It says the calibration went stale, and roughly when. It does **not** say by how
much — recovering that needs the pitch lines re-found in the new framing, which
is `[Stretch] Automatic pitch-line detection` and is not this.

It cannot tell a bump from a deliberate pan or a zoom, and does not try. Both
invalidate the homography exactly as completely, so the distinction would change
nothing about what to do.

It cannot see a *slow* drift — a tripod settling into soft ground over forty
minutes moves the median by a hair per sample, which is indistinguishable from
football and is deliberately not chased. A step is what this finds. A creep is
still invisible, and that is the honest limit of the method rather than a
setting to turn up: lower the threshold far enough and every counter-attack
becomes a camera bump.
