# `pitchiq/touches.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

When somebody touched the ball. Everything else is built on this.

A pass is two touches by the same team. An interception is two touches by
different teams with the ball in flight between them. A tackle is the same but
with the ball under control first. A shot is a touch followed by the ball
heading at a goal. Get touches right and the Opta taxonomy is mostly geometry;
get them wrong and every count downstream is wrong in the same direction.

    A touch is the conjunction of two signals, and neither works alone.

*Proximity alone* fires constantly. A player running alongside the ball sits
inside any sensible radius for a second at a time; a defender standing near a
rolling ball produces a clean minimum having done nothing. High recall,
useless precision.

*Motion change alone* fires on bounces, on the post, on the turf, and on the
seams where interpolated ball positions meet real ones — all with nobody near.

So a touch is: the ball changed what it was doing, **and** a specific player
was close enough to have been the cause.

    Scale. Nothing here is measured in pixels.

Radii are in player heights and speeds in player heights per second, extending
the convention `pitchiq/possession.py` established, because the camera zooms and a
pixel is worth a different distance every second. This module goes one step
further and uses a *local* scale — the players nearest the ball — rather than
the frame median. On a wide shot a near-touchline player is three times the
pixel height of a far one, so a frame median is far too generous at the top of
the picture and far too mean at the bottom.

    The ceiling, stated plainly.

Every threshold below is a guess. Not an estimate — a guess, chosen to be
reasonable and never yet checked against a human watching the same video. The
synthetic tests prove the algorithm does what this docstring says; they say
nothing about whether what it says is what a football touch looks like.

On the footage available today it will find almost nothing, and that is the
correct outcome rather than a bug: the ball is detected in about 60% of frames,
and on the one clip where it is detected at all, the nearest player to a
"ball" sits a median of six player heights away — those detections are mostly
false positives. This becomes meaningful at higher ball recall from a camera
that holds still.

**No touch count should be shown to anyone before it has been checked against
scrubbed video.** `pitchiq/experiments/touch_report.py` exists to make that cheap.
