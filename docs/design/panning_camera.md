# `pitchiq/panning_camera.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

A panning camera, turned into the still one the rest of the pipeline assumes.

Everything metre-based in `pitchiq/pipeline.py` was written for one homography per
match, fitted once on a tripod camera. The footage this team actually has is a
Hudl camera that follows the play, and `FOOTAGE_DAY.md` says plainly what that
does to a single homography: breaks it.

The ball tracker in `pitchiq/balltrack` already solved the harder half of this. It
registers every frame to a venue map (`homog/register_frames.py`, SIFT against map
keyframes every half second) and fits that map to the pitch once
(`homog/fit_pitch_to_map.py`). So each frame has its own frame -> StatsBomb homography.

What this module does with that is small on purpose. The Hudl camera pans and
tilts about a fixed point, so frame -> frame is a homography for *every* point
in the image, not only the grass. That means every box and ball position can be
moved into one reference view — the venue map's own reference keyframe — and
after that the match looks exactly as if it had been filmed by one camera that
never moved. The pipeline then runs unchanged: one fixed `Calibration` from the
reference view to metres, boxes that keep their shape (a pan rotates them; it
does not stretch them the way a top-down warp would), and pixel-space logic
like possession's player-height scale still means what it meant.

    The ball comes from the tracker, not the pipeline's own detector.

The pipeline's `build_trajectory` is the 43%-coverage path the tracker
replaced. Hybrid 90's answer is used instead: the 3D chain's ground position
(StatsBomb x, y), carried back into the reference view. For a ball in the air
that is the point under it, which is the right thing for possession to compare
with a player's feet and the wrong thing for nothing downstream. Frames the
detector picked are `observed`; TAPNext bridges and the chain's own fills are
not.

    Metres.

The venue map measures the pitch in centre-circle radii, because a radius is
the one length the laws fix (9.15 m) — so the size is not assumed:

    length 12.31 R x 9.15 = 112.7 m,   width 7.74 R x 9.15 = 70.8 m

    Error.

The map's fit to the pitch is the calibration error this reports: 40
landmarks, mean 0.28 m, 90th percentile 0.84 m, max 2.07 m (the far goalpost).
That is the fixed part. Per-frame registration error comes on top, and on the
one minute where it was checked against the 3D chain, ground balls projected
through each frame's homography agreed with it to a median 0.36 and a 90th
percentile 1.46 StatsBomb units (about 0.3 m and 1.3 m).

    from pitchiq.panning_camera import PannedCamera
    camera = PannedCamera.from_tracker('fr00')
    report = analyse_match(video, panned=camera, start_s=..., end_s=...)
