"""Why the detector found nothing: the camera, or the way we ran it.

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
empirical check available (`ROADMAP.md`, the detection spike):

| clip (both 720p screen recordings) | players | ball |
|---|---|---|
| tight, pitch fills the frame | 99% of frames | 43% |
| wide multi-sport panorama, players 4-8 px wide | **0 on-field** | **0 / 300** |

Four to eight pixels wide is roughly 12-24 px tall, and at `imgsz=960` on a
1280-wide frame that is 9-18 px at inference — inside the band below, and it
detected nothing at all. Two data points do not make a calibration curve. These
constants are named and gathered here precisely so the first real clip can
correct them, and `PLAYER_FLOOR_PX` is the one to move.

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
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

# --------------------------------------------------------------------------
# The thresholds. Two data points and a stride; see the module docstring.
# --------------------------------------------------------------------------

#: Below this many pixels at inference scale, a player has no grid cell that can
#: describe it. Stride 8, and a box wants to span a few cells.
PLAYER_FLOOR_PX = 16.0

#: Above this, players detect reliably enough to build on.
PLAYER_GOOD_PX = 28.0

#: A ball is roughly this fraction of a player's height, in any frame, because
#: it is the ratio of two physical objects: 0.22 m across against about 1.75 m
#: standing. Independent of camera, lens and distance.
BALL_TO_PLAYER = 0.126

#: What the ball needs at inference scale to be found with any consistency. It
#: is the same stride argument as the player floor, and via BALL_TO_PLAYER it
#: implies a player around 63 px — much stricter than PLAYER_GOOD_PX, and the
#: reason ball coverage is the number that gates possession, touches and shots.
BALL_FLOOR_PX = 8.0

#: A player standing is about this many times taller than wide. Only used to
#: read the one historical measurement, which was recorded in widths.
PLAYER_ASPECT = 3.0

GOOD = 'good'
MARGINAL = 'marginal'
UNUSABLE = 'unusable'
UNKNOWN = 'unknown'

#: What is holding the run back. 'framing' cannot be fixed here; 'resolution'
#: can, and that is the entire reason this module exists.
LIMIT_NONE = 'none'
LIMIT_FRAMING = 'framing'
LIMIT_RESOLUTION = 'resolution'
LIMIT_UNKNOWN = 'unknown'


@dataclass(frozen=True)
class FramingVerdict:
    """What the footage can support, and which lever moves it.

    `player_px` is measured in the file. `inference_px` is the same players
    after the letterbox to `imgsz`, and is what the model was actually shown.
    Everything interesting is the gap between those two.
    """

    width: int
    height: int
    imgsz: int
    frames: int
    player_px: float | None
    people_per_frame: float
    ball_share: float
    status: str
    limit: str

    @property
    def scale(self) -> float:
        """What ultralytics multiplies the frame by before the model sees it."""
        longest = max(self.width, self.height)
        return self.imgsz / longest if longest else 0.0

    @property
    def inference_px(self) -> float | None:
        """Player height in pixels as the model receives it."""
        if self.player_px is None:
            return None
        return self.player_px * self.scale

    @property
    def predicted_ball_px(self) -> float | None:
        """How big the ball is at inference scale, from the player's height.

        Predicted rather than measured on purpose: on the footage where this
        matters most the ball is never detected, so there are no ball boxes to
        measure and the prediction is the only number available.
        """
        inference = self.inference_px
        return None if inference is None else inference * BALL_TO_PLAYER

    @property
    def ball_reachable(self) -> bool:
        """Whether the ball could clear its floor if we stopped downscaling.

        Asks the question at native scale, so it says what the camera made
        possible rather than what this run happened to do.
        """
        if self.player_px is None:
            return False
        return self.player_px * BALL_TO_PLAYER >= BALL_FLOOR_PX

    def tiles_needed(self, target_px: float = PLAYER_GOOD_PX) -> int | None:
        """Tiles along the long edge to put players at `target_px`.

        `None` when the footage cannot get there at any tiling, which is the
        answer that matters: tiling stops the downscale, it does not invent
        detail. Once a tile is smaller than `imgsz` the model is upsampling and
        every further tile costs compute for no new information.
        """
        if self.player_px is None or self.player_px <= 0:
            return None
        if self.player_px < target_px:
            return None  # not there in the file; no tiling reaches it
        longest = max(self.width, self.height)
        if not longest or not self.imgsz:
            return None
        needed = target_px * longest / (self.player_px * self.imgsz)
        return max(1, math.ceil(needed))

    def to_json(self) -> dict:
        return {
            'width': self.width,
            'height': self.height,
            'imgsz': self.imgsz,
            'frames': self.frames,
            'scale': round(self.scale, 4),
            'player_px': None if self.player_px is None else round(self.player_px, 1),
            'inference_px': (
                None if self.inference_px is None else round(self.inference_px, 1)
            ),
            'predicted_ball_px': (
                None if self.predicted_ball_px is None
                else round(self.predicted_ball_px, 2)
            ),
            'people_per_frame': round(self.people_per_frame, 1),
            'ball_share': round(self.ball_share, 3),
            'status': self.status,
            'limit': self.limit,
            'tiles_needed': self.tiles_needed(),
        }

    def lines(self) -> list[str]:
        """The verdict as printed text, for `spike_detect` and for a person."""
        if self.player_px is None:
            return [
                'framing: no players detected, so there is nothing to measure.',
                '  Either the framing is far too wide to see anybody, or the',
                '  clip has no football in it. Look at one frame before',
                '  changing any setting.',
            ]

        out = [
            'framing: players are %.0f px tall in the file, %.0f px at '
            'imgsz=%d (x%.2f).' % (
                self.player_px, self.inference_px, self.imgsz, self.scale
            ),
            '  ball works out at about %.1f px, and wants %.0f.' % (
                self.predicted_ball_px, BALL_FLOOR_PX
            ),
        ]

        if self.limit == LIMIT_FRAMING:
            out.append(
                '  VERDICT: the camera. %.0f px is all the file holds, so no '
                'setting here' % self.player_px
            )
            out.append(
                '  recovers it. Move the camera closer or zoom in, and check '
                'jersey'
            )
            out.append('  numbers are faintly legible before trusting the next one.')
        elif self.limit == LIMIT_RESOLUTION:
            tiles = self.tiles_needed()
            out.append(
                '  VERDICT: our settings, not the camera. The file holds '
                '%.0f px and the' % self.player_px
            )
            out.append('  model was shown %.0f.' % self.inference_px)
            if tiles and tiles > 1:
                out.append(
                    '  Re-run with --tiles %d to detect at native scale '
                    '(about %dx the work).' % (tiles, tiles)
                )
            else:
                out.append('  Re-run with a larger --imgsz.')
        elif self.status == GOOD:
            out.append('  VERDICT: usable. Players are comfortably resolved.')
            if not self.ball_reachable:
                out.append(
                    '  The ball is still under its floor even at native scale, '
                    'so expect'
                )
                out.append(
                    '  patchy ball coverage and lean on the team-level figures.'
                )
        else:
            out.append(
                '  VERDICT: marginal. Players are near the floor, so expect '
                'fragmented'
            )
            out.append('  tracks and treat per-player figures as indicative.')

        return out


def _heights(detections) -> list[float]:
    """Box heights for people, in the pixels of the frame they came from."""
    heights = []
    for det in detections:
        if getattr(det, 'label', None) != 'person':
            continue
        _, y1, _, y2 = det.xyxy
        height = float(y2) - float(y1)
        if height > 0:
            heights.append(height)
    return heights


def assess_framing(
    per_frame,
    width: int,
    height: int,
    imgsz: int,
) -> FramingVerdict:
    """Judge a clip from the boxes a detector already produced.

    `per_frame` is one iterable of detections per sampled frame — exactly what
    `PersonBallDetector.detect_batch` returns, so this costs nothing beyond the
    detection run that was happening anyway.

    The median height is used rather than the mean because a touchline camera
    sees a coach three metres away and a full-back sixty metres away in the same
    frame, and the near one is several times the far one. A mean of those two
    describes nobody on the pitch. The median at least describes somebody.
    """
    frames = 0
    ball_frames = 0
    people_total = 0
    heights: list[float] = []

    for detections in per_frame:
        frames += 1
        detections = list(detections)
        people = _heights(detections)
        people_total += len(people)
        heights.extend(people)
        if any(getattr(d, 'label', None) == 'ball' for d in detections):
            ball_frames += 1

    player_px = statistics.median(heights) if heights else None
    people_per_frame = people_total / frames if frames else 0.0
    ball_share = ball_frames / frames if frames else 0.0

    longest = max(width, height)
    scale = imgsz / longest if longest else 0.0
    inference_px = None if player_px is None else player_px * scale

    if player_px is None:
        status, limit = UNKNOWN, LIMIT_UNKNOWN
    elif inference_px >= PLAYER_GOOD_PX:
        status, limit = GOOD, LIMIT_NONE
    else:
        # The one branch this module exists for. Ask what the file holds before
        # blaming the camera: if native clears the bar the downscale is ours.
        status = MARGINAL if inference_px >= PLAYER_FLOOR_PX else UNUSABLE
        limit = (
            LIMIT_RESOLUTION if player_px >= PLAYER_GOOD_PX else LIMIT_FRAMING
        )

    return FramingVerdict(
        width=width,
        height=height,
        imgsz=imgsz,
        frames=frames,
        player_px=player_px,
        people_per_frame=people_per_frame,
        ball_share=ball_share,
        status=status,
        limit=limit,
    )
