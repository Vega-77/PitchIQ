# `pitchiq/detection_diagnosis.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Why the detector found nothing: the camera, or the way we ran it.

`spike_detect` has always been able to say *whether* players and the ball were
found. It has never been able to say **why not**, and the two reasons need
opposite responses on opposite timescales:

* **The framing was wrong.** The pixels were never recorded. Nothing downstream
  recovers them and no setting on this laptop helps. The fix is the camera, and
  it costs a week — the next match.
* **We threw the pixels away.** The frame was fine and the detector never saw it
  at full size, because ultralytics letterboxes every frame so its long edge
  becomes `imgsz`. A 3840-wide export at `imgsz=1280` is scaled to a third
  before the model looks at it. The fix is a flag, and it costs one re-run.

Those look identical in the output we had — `0 ball detections` either way — and
they are told apart by one number that was never printed: **how tall a player is
in pixels, measured at the scale the model actually sees.**

    native_px  x  imgsz / max(width, height)  =  what the model gets

So the rule this module exists to apply:

* the **native** height is the ceiling. If a player is 14 px tall in the file,
  14 px is all there will ever be, and the verdict is about the camera.
* the **inference** height is what we chose. If native is comfortable and
  inference is not, we did this to ourselves, and tiling or a larger `imgsz`
  gives it back.

    Where the thresholds come from, and how much to trust them.

YOLOv8's finest feature map has a stride of 8, so an object under about 8 px at
inference scale has no grid cell that can describe it — that is architecture,
not tuning. Around three cells is where a box becomes comfortable, which puts
the usable floor near 24 px and a marginal band below it.

The repo's two real measurements are consistent with that and are the only
empirical check available (`docs/history/ROADMAP-archive.md`, the detection spike):

| clip (both 720p screen recordings) | players | ball |
|---|---|---|
| tight, pitch fills the frame | 99% of frames | 43% |
| wide multi-sport panorama, players 4-8 px wide | **0 on-field** | **0 / 300** |

That measurement is `WIDE_CLIP_WIDTHS_PX`, and it is recorded in widths while
every threshold here is a height. 4-8 px wide is 12-24 px tall, and at
`imgsz=960` on a 1280-wide frame that is 9-18 px at inference — inside the band
below, and it detected nothing at all. `player_height_px` is that conversion,
and it is a function rather than a sentence because the numbers either side of
it have to move together: correct the aspect ratio and the evidence under
`PLAYER_FLOOR_PX` has to move with it, or the threshold is left resting on
arithmetic nobody can re-run.

Two data points do not make a calibration curve. These constants are named and
gathered here precisely so the first real clip can correct them, and
`PLAYER_FLOOR_PX` is the one to move.

    The ball is a much harder problem than the player, by a fixed ratio.

A ball is 0.22 m across and a player is about 1.75 m tall, so the ball is always
about an eighth of the player's height in the same frame, wherever the camera
is. That single ratio explains the table above: on the clip where players were
found in 99% of frames the ball was still only found in 43%, and on the clip
where players were 12-24 px the ball was under 2 px and was never found once.

It also means the ball sets the real requirement. Asking for a detectable ball
is asking for a player around `BALL_FLOOR_PX / BALL_TO_PLAYER` tall — a far
stricter demand than the player threshold, and the reason `FOOTAGE_DAY.md` says
the framing test is whether jersey numbers are legible rather than whether you
can see people.

Nothing here imports a model or opens a video. It is arithmetic over boxes, so
it is tested against synthetic detections and needs no footage — which is the
whole point of having it before the footage arrives.
