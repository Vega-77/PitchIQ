"""Following players on the pitch rather than on the screen.

BoT-SORT tracks boxes in pixels, and on this footage that is the wrong space to
do it in. The Hudl camera pans to follow play, so a player standing still moves
across the image; a step towards the far touchline is a few pixels while the
same step on the near side is fifty; and two players who cross at different
depths overlap on screen while being metres apart on the grass. The tracker
answers by cutting tracks: 165 ids for about 22 people in twenty seconds.

Every frame of the match already has its own homography to the pitch (the ball
tracker's venue registration, `cv/panned.py`), so this tracker works in metres:

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
  edge, and the offline linker (`cv/linking.py`) decides who comes back in.
  A track that is in view and unmatched is a real miss, coasted for half a
  second before it ends.

* **Two thresholds, ByteTrack-style.** Confident detections are matched first
  and can start tracks; weak ones (often a half-occluded player) may only
  continue a track that is already there.

* **Ambiguity ends tracks instead of guessing.** When two same-coloured
  players are close and swapping their detections would cost nearly the same,
  both tracks are cut around that stretch once the run is over. A wrong
  identity is worse than a missing one (the bar the user set for the ball,
  and the one `cv/identity.py` argues for players): two fragments still sum
  correctly, a swap silently gives one player's minutes to another. The linker then
  joins across the cut with both sides' full trajectories as evidence, and
  leaves it cut if that is still unclear.

Output is tracklets — runs of detections that are one person with high
confidence. Pixel boxes stay pixel boxes; metres are only the working space.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import cv2
import numpy as np

from .assignment import min_cost_matching

# ---- the camera ----------------------------------------------------------------


class PitchProjector:
    """Pixel <-> metre mapping for every frame of a window.

    `homographies` maps an absolute frame number to that frame's 3x3 pixel ->
    metres homography (for a `PannedCamera`, `calibration.H @ to_reference[f]`).
    """

    def __init__(self, homographies: dict[int, np.ndarray], width: int, height: int) -> None:
        self.H = {int(f): np.asarray(h, dtype=np.float64) for f, h in homographies.items()}
        self.Hinv = {f: np.linalg.inv(h) for f, h in self.H.items()}
        self.width = int(width)
        self.height = int(height)
        # Dense copies over the frame range, for questions asked of many frames
        # at once (is this whole path in view?). Missing frames are NaN.
        self.f0 = min(self.H) if self.H else 0
        n = (max(self.H) - self.f0 + 1) if self.H else 0
        self._Hinv = np.full((n, 3, 3), np.nan)
        self._sign = np.zeros(n)
        for f, h in self.Hinv.items():
            self._Hinv[f - self.f0] = h
            # A point behind the camera projects with the opposite sign of w.
            # The bottom-centre pixel is grass in front of the camera, and
            # H then Hinv returns it scaled by 1/w of the forward map, so that
            # sign is "in front".
            q = self.H[f] @ np.array([self.width / 2, self.height - 1, 1.0])
            self._sign[f - self.f0] = np.sign(q[2])

    @classmethod
    def from_panned(cls, camera, width: int, height: int) -> 'PitchProjector':
        cal = camera.calibration.H
        return cls({f: cal @ m for f, m in camera.to_reference.items()}, width, height)

    def has(self, frame: int) -> bool:
        return int(frame) in self.H

    @staticmethod
    def _apply(h: np.ndarray, pts) -> tuple[np.ndarray, np.ndarray]:
        p = np.atleast_2d(np.asarray(pts, dtype=np.float64))
        q = p @ h[:, :2].T + h[:, 2]
        return q[:, :2] / q[:, 2:3], q[:, 2]

    def to_m(self, frame: int, pts) -> np.ndarray:
        return self._apply(self.H[int(frame)], pts)[0]

    def to_px(self, frame: int, pts) -> np.ndarray:
        return self._apply(self.Hinv[int(frame)], pts)[0]

    def jacobian(self, frame: int, u: float, v: float) -> np.ndarray:
        """d(metres)/d(pixels) at one image point, 2x2."""
        h = self.H[int(frame)]
        q = h @ np.array([u, v, 1.0])
        w = q[2]
        x, y = q[0] / w, q[1] / w
        return (
            np.array(
                [
                    [h[0, 0] - x * h[2, 0], h[0, 1] - x * h[2, 1]],
                    [h[1, 0] - y * h[2, 0], h[1, 1] - y * h[2, 1]],
                ]
            )
            / w
        )

    def in_view(self, frame: int, pts_m, margin_px: float = 0.0) -> np.ndarray:
        """Which pitch points this frame's picture shows (with `margin_px` to spare)."""
        pts = np.atleast_2d(np.asarray(pts_m, dtype=np.float64))
        return self.in_view_many(np.full(len(pts), int(frame)), pts, margin_px)

    def in_view_many(self, frames, pts_m, margin_px: float = 0.0) -> np.ndarray:
        """`in_view` with each point at its own frame; unregistered frames show nothing."""
        frames = np.asarray(frames, dtype=np.int64)
        pts = np.atleast_2d(np.asarray(pts_m, dtype=np.float64))
        out = np.zeros(len(frames), dtype=bool)
        idx = frames - self.f0
        ok = (idx >= 0) & (idx < len(self._sign))
        ok[ok] &= self._sign[idx[ok]] != 0
        if not ok.any():
            return out
        Hi = self._Hinv[idx[ok]]
        q = np.einsum('nij,nj->ni', Hi, np.c_[pts[ok], np.ones(ok.sum())])
        u, v = q[:, 0] / q[:, 2], q[:, 1] / q[:, 2]
        out[ok] = (
            (np.sign(q[:, 2]) == self._sign[idx[ok]])
            & (u >= margin_px)
            & (u <= self.width - margin_px)
            & (v >= margin_px)
            & (v <= self.height - margin_px)
        )
        return out


def foot_points(boxes: np.ndarray) -> np.ndarray:
    b = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
    return np.c_[(b[:, 0] + b[:, 2]) / 2, b[:, 3]]


# A detector's box bottom lands within a few pixels of the feet; sideways, the
# centre of a running player's box wanders with their arms and stride. As
# fractions of box size, plus a pixel floor for tiny far-side boxes.
FOOT_SIGMA_W = 0.15
FOOT_SIGMA_H = 0.05
FOOT_SIGMA_FLOOR_PX = 1.5


def foot_covariances(projector: PitchProjector, frame: int, boxes: np.ndarray) -> np.ndarray:
    """Each detection's foot uncertainty on the grass, 2x2 metres^2."""
    b = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
    out = np.empty((len(b), 2, 2))
    for i, (x1, y1, x2, y2) in enumerate(b):
        su = FOOT_SIGMA_W * (x2 - x1) + FOOT_SIGMA_FLOOR_PX
        sv = FOOT_SIGMA_H * (y2 - y1) + FOOT_SIGMA_FLOOR_PX
        J = projector.jacobian(frame, (x1 + x2) / 2, y2)
        out[i] = J @ np.diag([su * su, sv * sv]) @ J.T
    return out


# ---- appearance -----------------------------------------------------------------

# Where the shirt and the shorts are, as fractions of a standing player's box.
TORSO_ROWS = (0.18, 0.48)
SHORTS_ROWS = (0.50, 0.68)
BODY_COLS = (0.28, 0.72)
MIN_PIXELS = 6

# Grass is "green-ish" in OpenCV hue units (0..180). That band is wide enough to
# swallow a neon yellow-green referee shirt whole: on the dev window the shirt
# sits at hue 36-38 and the grass at 44-46, and in Lab the two are only ~8
# apart. So the mask is narrowed to each frame's own grass hue when one is
# measured, and the wide band is only the fallback. Close to the camera the
# same shirt lands right on the grass hue (47 vs 47), and what tells them apart
# is saturation: grass at most ~100, the shirt ~195. So the mask also stops at
# a little above the frame's own grass saturation.
GRASS_HUE_BAND = (30, 90)
GRASS_MIN_SAT = 50
GRASS_MIN_VAL = 40
GRASS_HUE_MIN_TOL = 4
GRASS_SAMPLE_STEP = 8
GRASS_SAT_PCT = 95  # robust to a shirt filling a few % of the frame
GRASS_SAT_MARGIN = 30


def _green(hsv: np.ndarray) -> np.ndarray:
    return (
        (hsv[:, 0] >= GRASS_HUE_BAND[0])
        & (hsv[:, 0] <= GRASS_HUE_BAND[1])
        & (hsv[:, 1] >= GRASS_MIN_SAT)
        & (hsv[:, 2] >= GRASS_MIN_VAL)
    )


def grass_hue(frame: np.ndarray) -> tuple[float, float, float] | None:
    """The frame's grass: hue, how far from it still counts, and a saturation ceiling.

    The modal hue of the green pixels, with a tolerance of their 10-90% spread
    (never under GRASS_HUE_MIN_TOL); the ceiling is the GRASS_SAT_PCT
    percentile of those pixels' saturation plus GRASS_SAT_MARGIN. None when
    the frame shows no grass.
    """
    small = frame[::GRASS_SAMPLE_STEP, ::GRASS_SAMPLE_STEP]
    hsv = cv2.cvtColor(np.ascontiguousarray(small), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    h = hsv[_green(hsv), 0].astype(np.float64)
    if len(h) < 100:
        return None
    mode = float(np.argmax(np.bincount(h.astype(int), minlength=181)))
    p10, p90 = np.percentile(h, [10, 90])
    tol = float(max(GRASS_HUE_MIN_TOL, p90 - p10))
    green = hsv[_green(hsv)]
    sat = green[np.abs(h - mode) <= tol, 1].astype(np.float64)
    return mode, tol, float(np.percentile(sat, GRASS_SAT_PCT)) + GRASS_SAT_MARGIN


def body_descriptor(frame: np.ndarray, xyxy, grass=None) -> np.ndarray:
    """Mean Lab colour of shirt and shorts, grass removed: six numbers, NaN if unseen.

    Teams are told apart by `cv/teams.py`; this is the finer signal the linker
    uses on top — a keeper's or referee's kit, a player in long sleeves, dark
    shorts against light ones. Teammates mostly look alike in it, which is why
    the linker leans on motion and treats appearance as a tie-breaker.

    `grass` is `grass_hue(frame)`; pass it whenever possible (see GRASS_HUE_BAND).
    """
    h_img, w_img = frame.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in xyxy)
    bw, bh = x2 - x1, y2 - y1
    out = np.full(6, np.nan)
    if bw < 3 or bh < 8:
        return out
    c0 = int(np.clip(x1 + BODY_COLS[0] * bw, 0, w_img - 1))
    c1 = int(np.clip(x1 + BODY_COLS[1] * bw, c0 + 1, w_img))
    for k, (r0f, r1f) in enumerate((TORSO_ROWS, SHORTS_ROWS)):
        r0 = int(np.clip(y1 + r0f * bh, 0, h_img - 1))
        r1 = int(np.clip(y1 + r1f * bh, r0 + 1, h_img))
        patch = frame[r0:r1, c0:c1]
        if patch.size == 0:
            continue
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).reshape(-1, 3)
        is_grass = _green(hsv)
        if grass is not None:
            is_grass &= np.abs(hsv[:, 0].astype(np.float64) - grass[0]) <= grass[1]
            if len(grass) > 2:
                is_grass &= hsv[:, 1] <= grass[2]
        lab = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float64)
        keep = lab[~is_grass]
        if len(keep) >= MIN_PIXELS:
            out[3 * k : 3 * k + 3] = np.median(keep, axis=0)
    return out


def descriptor_scale(desc: np.ndarray) -> np.ndarray:
    """Per-channel spread across a window, so channels weigh alike."""
    d = np.asarray(desc, dtype=np.float64)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)  # all-NaN channels
        s = np.nanstd(d, axis=0) if len(d) else np.ones(6)
    return np.where(np.isfinite(s) & (s > 1e-6), s, 1.0)


def descriptor_distance(a: np.ndarray, b: np.ndarray, scale: np.ndarray) -> float:
    """Mean squared z-distance over channels both sides have; 0 if none."""
    ok = np.isfinite(a) & np.isfinite(b)
    if not ok.any():
        return 0.0
    z = (a[ok] - b[ok]) / scale[ok]
    return float(np.mean(z * z))


# ---- detections -------------------------------------------------------------------


@dataclass
class Detections:
    """Every person detection in a window, flat and indexed by row.

    `team` is a per-detection kit guess (0, 1, or -1 for unknown) used only as
    a soft penalty while tracking; the linker decides teams per fragment.
    """

    frame: np.ndarray  # (N,) absolute frame number
    box: np.ndarray  # (N, 4) x1 y1 x2 y2, pixels of that frame
    conf: np.ndarray  # (N,)
    team: np.ndarray  # (N,) int
    desc: np.ndarray  # (N, 6) body_descriptor
    kit: np.ndarray | None = None  # (N, k) teams.kit_sample, for the linker
    feat: np.ndarray | None = None  # (N, d) unit appearance vectors, for the linker

    def __post_init__(self) -> None:
        self.frame = np.asarray(self.frame, dtype=np.int64)
        self.box = np.asarray(self.box, dtype=np.float64).reshape(-1, 4)
        self.conf = np.asarray(self.conf, dtype=np.float64)
        self.team = np.asarray(self.team, dtype=np.int64)
        self.desc = np.asarray(self.desc, dtype=np.float64).reshape(len(self.frame), -1)
        order = np.argsort(self.frame, kind='stable')
        if not np.all(order == np.arange(len(order))):
            raise ValueError('detections must be sorted by frame')

    def __len__(self) -> int:
        return len(self.frame)

    def rows_by_frame(self) -> dict[int, np.ndarray]:
        out: dict[int, np.ndarray] = {}
        if not len(self.frame):
            return out
        cuts = np.flatnonzero(np.diff(self.frame)) + 1
        for chunk in np.split(np.arange(len(self.frame)), cuts):
            out[int(self.frame[chunk[0]])] = chunk
        return out


CONTAINED = 0.75  # share of the weaker box inside the stronger one
SAME_FEET_H = 0.15  # feet this close vertically, in box heights
SAME_FEET_W = 0.6  # and horizontally, in box widths


def drop_contained(det: Detections) -> Detections:
    """Remove a second, partial box on the same person.

    YOLO's non-maximum suppression compares boxes by IoU, and a legs-only or
    half-body box inside a full one has a low IoU with it, so both survive —
    on this footage, the commonest reason one player became two tracks. A
    box mostly inside a more confident one, with its feet in the same place,
    is that. A player half-hidden behind another stands further back, so
    their feet are higher in the picture, and they are kept.
    """
    keep = np.ones(len(det), dtype=bool)
    for rows in det.rows_by_frame().values():
        order = rows[np.argsort(-det.conf[rows], kind='stable')]
        b = det.box[order]
        area = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        for j in range(1, len(order)):
            for i in range(j):
                if not keep[order[i]]:
                    continue
                iw = min(b[i, 2], b[j, 2]) - max(b[i, 0], b[j, 0])
                ih = min(b[i, 3], b[j, 3]) - max(b[i, 1], b[j, 1])
                if iw <= 0 or ih <= 0:
                    continue
                if iw * ih < CONTAINED * min(area[i], area[j]):
                    continue
                h = max(b[i, 3] - b[i, 1], b[j, 3] - b[j, 1])
                w = max(b[i, 2] - b[i, 0], b[j, 2] - b[j, 0])
                if abs(b[i, 3] - b[j, 3]) > SAME_FEET_H * h:
                    continue
                if abs((b[i, 0] + b[i, 2]) - (b[j, 0] + b[j, 2])) / 2 > SAME_FEET_W * w:
                    continue
                keep[order[j]] = False
                break
    return Detections(
        frame=det.frame[keep],
        box=det.box[keep],
        conf=det.conf[keep],
        team=det.team[keep],
        desc=det.desc[keep],
        kit=None if det.kit is None else det.kit[keep],
        feat=None if det.feat is None else det.feat[keep],
    )


def drop_clipped(det: Detections, height: int, margin_px: float = 3.0) -> Detections:
    """Remove boxes cut off by the bottom of the picture.

    Everything here is placed by the feet, and a box that runs into the
    bottom edge has none: its bottom is the frame, not the grass, so the
    position it gives is somewhere it isn't. On this camera they are the
    people standing just below it, on the near touchline.
    """
    keep = det.box[:, 3] < height - margin_px
    return Detections(
        frame=det.frame[keep],
        box=det.box[keep],
        conf=det.conf[keep],
        team=det.team[keep],
        desc=det.desc[keep],
        kit=None if det.kit is None else det.kit[keep],
        feat=None if det.feat is None else det.feat[keep],
    )


# ---- the tracker ------------------------------------------------------------------


@dataclass
class TrackerSettings:
    fps: float = 30.0
    high_conf: float = 0.35  # may start a track
    low_conf: float = 0.10  # may only continue one
    accel_sigma: float = 4.0  # m/s^2, white-noise acceleration
    gate: float = 13.8  # Mahalanobis^2, chi-square 2 dof at 99.9%
    low_gate: float = 9.2  # tighter for weak detections (99%)
    max_gate_m: float = 4.0  # never jump further than this in one frame
    team_penalty: float = 6.0  # kit disagrees with the track's
    appearance_weight: float = 0.8
    confirm_hits: int = 3  # hits within the first `confirm_window` frames
    confirm_window: int = 5
    max_miss_in_view: int = 15  # coast half a second while visible
    max_miss_out_of_view: int = 3  # left the picture: end almost at once
    ambiguity_margin: float = 3.0  # swap cost within this of the chosen -> cut
    ambiguity_radius_m: float = 2.5  # only between players this close
    cut_ambiguous: bool = True
    episode_join: int = 4  # ambiguous frames this close are one episode
    episode_min: int = 2  # ambiguous frames before an episode counts
    episode_pad: int = 2  # and the frames either side are suspect too
    episode_drop: int = 15  # episodes up to this long are removed, longer kept apart
    view_margin_px: float = 8.0


@dataclass
class _Track:
    tid: int
    x: np.ndarray  # [x, y, vx, vy]
    P: np.ndarray
    rows: list = field(default_factory=list)  # detection rows, in frame order
    hits: int = 0
    age: int = 0  # frames since birth
    miss: int = 0
    miss_out: int = 0
    confirmed: bool = False
    desc: np.ndarray | None = None
    team_votes: np.ndarray = field(default_factory=lambda: np.zeros(2))

    def team(self) -> int:
        t = self.team_votes
        if t.sum() < 2:
            return -1
        if t[0] >= 2 * t[1]:
            return 0
        if t[1] >= 2 * t[0]:
            return 1
        return -1


@dataclass
class Tracklet:
    tid: int
    rows: np.ndarray  # detection rows, in frame order
    end_reason: str  # 'left view', 'lost', 'cut', 'window end'
    start_reason: str  # 'new', 'cut'


class PitchTracker:
    """Online tracking in metres; produces tracklets for `cv/linking.py`."""

    def __init__(self, projector: PitchProjector, settings: TrackerSettings | None = None) -> None:
        self.proj = projector
        self.s = settings or TrackerSettings()
        dt = 1.0 / self.s.fps
        self.F = np.eye(4)
        self.F[0, 2] = self.F[1, 3] = dt
        q = self.s.accel_sigma**2
        g = np.array([[dt**4 / 4, dt**3 / 2], [dt**3 / 2, dt**2]]) * q
        self.Q = np.zeros((4, 4))
        self.Q[np.ix_([0, 2], [0, 2])] = g
        self.Q[np.ix_([1, 3], [1, 3])] = g
        self.Hm = np.zeros((2, 4))
        self.Hm[0, 0] = self.Hm[1, 1] = 1.0

    # -- bookkeeping
    def _end(self, t: _Track, reason: str, out: list, starts: dict) -> None:
        if t.confirmed and t.rows:
            out.append(Tracklet(t.tid, np.array(t.rows), reason, starts.get(t.tid, 'new')))

    def run(self, det: Detections) -> list[Tracklet]:
        s = self.s
        by_frame = det.rows_by_frame()
        if not by_frame:
            return []
        scale = descriptor_scale(det.desc)
        frames = range(min(by_frame), max(by_frame) + 1)
        live: list[_Track] = []
        done: list[Tracklet] = []
        start_reason: dict[int, str] = {}
        ambiguous: dict[int, list] = {}
        next_id = 0

        for f in frames:
            rows = by_frame.get(f, np.zeros(0, dtype=int))
            if not self.proj.has(f):
                # no registration: nothing can be placed; tracks end here
                for t in live:
                    self._end(t, 'lost', done, start_reason)
                live = []
                continue

            # predict
            for t in live:
                t.x = self.F @ t.x
                t.P = self.F @ t.P @ self.F.T + self.Q
                t.age += 1

            z = self.proj.to_m(f, foot_points(det.box[rows])) if len(rows) else np.zeros((0, 2))
            R = foot_covariances(self.proj, f, det.box[rows]) if len(rows) else np.zeros((0, 2, 2))
            conf = det.conf[rows]
            high = np.flatnonzero(conf >= s.high_conf)
            low = np.flatnonzero((conf >= s.low_conf) & (conf < s.high_conf))

            def cost_matrix(tracks, cand, gate):
                c = np.full((len(tracks), len(cand)), np.inf)
                for a, t in enumerate(tracks):
                    hp = self.Hm @ t.x
                    PHt = t.P @ self.Hm.T
                    S0 = self.Hm @ PHt
                    tt = t.team()
                    for b, j in enumerate(cand):
                        d = z[j] - hp
                        if d @ d > s.max_gate_m**2:
                            continue
                        S = S0 + R[j]
                        m2 = float(d @ np.linalg.solve(S, d))
                        if m2 > gate:
                            continue
                        cost = m2
                        dt_ = det.team[rows[j]]
                        if tt >= 0 and dt_ >= 0 and dt_ != tt:
                            cost += s.team_penalty
                        if t.desc is not None:
                            cost += s.appearance_weight * min(
                                descriptor_distance(t.desc, det.desc[rows[j]], scale), 9.0
                            )
                        c[a, b] = cost
                return c

            # stage 1: confident detections against every live track
            c1 = cost_matrix(live, high, s.gate)
            m1 = min_cost_matching(c1, unmatched=s.gate / 2 + 1.0)
            matched_t = {a for a, _ in m1}
            matched_d = {int(high[b]) for _, b in m1}
            pairs = [(live[a], int(high[b]), c1[a, b]) for a, b in m1]

            # ambiguity: a nearly-as-good swap between two close tracks of one
            # kit. Noted, not acted on here: cutting on the spot restarts both
            # tracks with no velocity, which makes the next frame ambiguous
            # too, and one crossing becomes a dozen fragments.
            if s.cut_ambiguous and len(m1) > 1:
                for (a1, b1), (a2, b2) in _close_pairs(m1, live, s.ambiguity_radius_m):
                    ta, tb = live[a1], live[a2]
                    if ta.team() >= 0 and tb.team() >= 0 and ta.team() != tb.team():
                        continue
                    keep = c1[a1, b1] + c1[a2, b2]
                    swap = c1[a1, b2] + c1[a2, b1]
                    if np.isfinite(swap) and swap - keep < s.ambiguity_margin:
                        ambiguous.setdefault(ta.tid, []).append(f)
                        ambiguous.setdefault(tb.tid, []).append(f)

            # stage 2: weak detections may continue tracks that are still free
            rest = [a for a in range(len(live)) if a not in matched_t and live[a].confirmed]
            if len(rest) and len(low):
                c2 = cost_matrix([live[a] for a in rest], low, s.low_gate)
                for a, b in min_cost_matching(c2, unmatched=s.low_gate / 2 + 1.0):
                    matched_t.add(rest[a])
                    pairs.append((live[rest[a]], int(low[b]), c2[a, b]))

            # update matched tracks
            for t, j, _ in pairs:
                self._update(t, z[j], R[j], rows[j], det, scale)

            survivors: list[_Track] = []
            for a, t in enumerate(live):
                if a in matched_t:
                    t.miss = t.miss_out = 0
                    if not t.confirmed and t.hits >= s.confirm_hits:
                        t.confirmed = True
                    survivors.append(t)
                    continue
                # unmatched
                if not t.confirmed:
                    if t.age >= s.confirm_window:
                        continue  # never confirmed: noise
                    survivors.append(t)
                    continue
                visible = bool(self.proj.in_view(f, t.x[None, :2], s.view_margin_px)[0])
                if visible:
                    t.miss += 1
                    if t.miss > s.max_miss_in_view:
                        self._end(t, 'lost', done, start_reason)
                        continue
                else:
                    t.miss_out += 1
                    if t.miss_out > s.max_miss_out_of_view:
                        self._end(t, 'left view', done, start_reason)
                        continue
                survivors.append(t)
            live = survivors

            # births from unmatched confident detections
            for b in high:
                if int(b) in matched_d:
                    continue
                live.append(self._birth(next_id, z, R, rows, int(b), det, scale))
                next_id += 1

        for t in live:
            self._end(t, 'window end', done, start_reason)
        done = self._split_ambiguous(done, ambiguous, det)
        done.sort(key=lambda tr: (det.frame[tr.rows[0]], tr.tid))
        return done

    def _split_ambiguous(self, tracklets, ambiguous, det) -> list[Tracklet]:
        """Cut each tracklet around the stretches where it could have swapped.

        A short stretch (a crossing) is removed outright — its detections are
        exactly the ones that might belong to the other player — and the linker
        decides, from both sides' full trajectories, whether to join across it;
        a joined gap is then filled by interpolation. A long one (two players
        jogging side by side) is kept as a piece of its own, because dropping
        seconds of a player is too high a price for a doubt.
        """
        s = self.s
        out = []
        for tr in tracklets:
            amb = sorted(set(ambiguous.get(tr.tid, ())))
            if not amb:
                out.append(tr)
                continue
            episodes = [[amb[0], amb[0]]]
            for f in amb[1:]:
                if f - episodes[-1][1] <= s.episode_join:
                    episodes[-1][1] = f
                else:
                    episodes.append([f, f])
            # a single near-tie is a blip, not a crossing
            episodes = [
                e for e in episodes if sum(1 for f in amb if e[0] <= f <= e[1]) >= s.episode_min
            ]
            if not episodes:
                out.append(tr)
                continue
            frames = det.frame[tr.rows]
            cuts = np.zeros(len(frames), dtype=int)  # piece index per row
            keep = np.ones(len(frames), dtype=bool)
            piece = 0
            for e0, e1 in episodes:
                inside = (frames >= e0 - s.episode_pad) & (frames <= e1 + s.episode_pad)
                if (e1 - e0 + 1) <= s.episode_drop:
                    keep &= ~inside
                piece += 1
                cuts[frames >= e0 - s.episode_pad] = piece
                if (e1 - e0 + 1) > s.episode_drop:
                    piece += 1
                    cuts[frames > e1 + s.episode_pad] = piece
            for p in np.unique(cuts):
                rows = tr.rows[(cuts == p) & keep]
                if len(rows) < s.confirm_hits:
                    continue
                first, last = p == cuts.min(), p == cuts.max()
                out.append(
                    Tracklet(
                        tid=tr.tid * 1000 + int(p),
                        rows=rows,
                        end_reason=tr.end_reason if last else 'cut',
                        start_reason=tr.start_reason if first else 'cut',
                    )
                )
        return out

    def _birth(self, tid, z, R, rows, j, det, scale) -> _Track:
        x = np.r_[z[j], 0.0, 0.0]
        P = np.zeros((4, 4))
        P[:2, :2] = R[j] + np.eye(2) * 0.05
        P[2, 2] = P[3, 3] = 9.0  # unknown velocity: up to ~3 m/s
        t = _Track(tid=tid, x=x, P=P)
        t.rows.append(int(rows[j]))
        t.hits = 1
        self._absorb(t, int(rows[j]), det)
        return t

    def _update(self, t: _Track, zj, Rj, row, det, scale) -> None:
        y = zj - self.Hm @ t.x
        S = self.Hm @ t.P @ self.Hm.T + Rj
        K = t.P @ self.Hm.T @ np.linalg.inv(S)
        t.x = t.x + K @ y
        t.P = (np.eye(4) - K @ self.Hm) @ t.P
        t.rows.append(int(row))
        t.hits += 1
        self._absorb(t, int(row), det)

    @staticmethod
    def _absorb(t: _Track, row: int, det: Detections) -> None:
        d = det.desc[row]
        if t.desc is None:
            t.desc = d.copy()
        else:
            both = np.isfinite(t.desc) & np.isfinite(d)
            t.desc[both] = 0.9 * t.desc[both] + 0.1 * d[both]
            fill = ~np.isfinite(t.desc) & np.isfinite(d)
            t.desc[fill] = d[fill]
        k = int(det.team[row])
        if k in (0, 1):
            t.team_votes[k] += 1


def _close_pairs(matches, tracks, radius_m):
    """Pairs of matches whose tracks' predicted positions are within radius."""
    out = []
    for i in range(len(matches)):
        for k in range(i + 1, len(matches)):
            a, b = matches[i][0], matches[k][0]
            if np.hypot(*(tracks[a].x[:2] - tracks[b].x[:2])) <= radius_m:
                out.append((matches[i], matches[k]))
    return out
