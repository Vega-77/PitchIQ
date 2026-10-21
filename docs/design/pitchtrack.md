# `pitchiq/pitchtrack.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Following players on the pitch rather than on the screen.

BoT-SORT tracks boxes in pixels, and on this footage that is the wrong space to
do it in. The Hudl camera pans to follow play, so a player standing still moves
across the image; a step towards the far touchline is a few pixels while the
same step on the near side is fifty; and two players who cross at different
depths overlap on screen while being metres apart on the grass. The tracker
answers by cutting tracks: 165 ids for about 22 people in twenty seconds.

Every frame of the match already has its own homography to the pitch (the ball
tracker's venue registration, `pitchiq/panning_camera.py`), so this tracker works in metres:

* **A constant-velocity Kalman filter per player, in metres.** A pan moves no
  one, because the camera's motion is already inside each frame's homography.

* **Measurement noise from the homography itself.** A foot is found to a few
  pixels; what a few pixels mean in metres depends on where in the picture it
  is. The Jacobian of the frame's homography at the foot carries the pixel
  uncertainty onto the grass, so a far-side player's position is honestly long
  and thin along the camera ray and a near-side one is tight. Gating uses that
  shape (Mahalanobis distance), not a fixed radius.

* **The camera's footprint.** A track whose predicted position has left the
  picture is not "missed" — nobody could have seen it. It ends quietly at the
  edge, and the offline linker (`pitchiq/linking.py`) decides who comes back in.
  A track that is in view and unmatched is a real miss, coasted for half a
  second before it ends.

* **Two thresholds, ByteTrack-style.** Confident detections are matched first
  and can start tracks; weak ones (often a half-occluded player) may only
  continue a track that is already there.

* **Ambiguity ends tracks instead of guessing.** When two same-coloured
  players are close and swapping their detections would cost nearly the same,
  both tracks are cut around that stretch once the run is over. A wrong
  identity is worse than a missing one (the bar the user set for the ball,
  and the one `pitchiq/identity.py` argues for players): two fragments still sum
  correctly, a swap silently gives one player's minutes to another. The linker then
  joins across the cut with both sides' full trajectories as evidence, and
  leaves it cut if that is still unclear.

Output is tracklets — runs of detections that are one person with high
confidence. Pixel boxes stay pixel boxes; metres are only the working space.
