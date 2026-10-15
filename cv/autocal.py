"""Calibrate a panning camera from the match itself.

The Hudl camera stands in one place for the whole match and only turns and
zooms. So the whole calibration is one camera position, shared by every frame,
plus a pan, tilt, roll and focal length per frame. Two things pin those down:

* **The paint.** Wherever enough yellow lines are in view (halfway line,
  centre circle, touchlines, boxes) the camera is fitted so that the pitch
  model lands on them: a chamfer fit, model -> paint and paint -> model.

* **The picture moving.** Between two nearby frames of a camera that only
  turns, every pixel -- grass, bleachers, trees -- moves by one homography,
  K2 R2 R1^T K1^-1. SIFT measures it well, so a frame with little paint in
  view inherits its pose from its neighbours.

Lengths are in centre-circle radii (R), the one length the laws fix, as in the
venue map (`balltrack/homog/vpitch.py`); outputs are StatsBomb 120 x 80.
u runs along the pitch (0 .. L), v across it from the near touchline (0 .. W),
z up. The camera stands behind the near touchline, so its v is negative.
"""

from __future__ import annotations

from dataclasses import dataclass
import os

import cv2
import numpy as np

# penalty box, goal area and penalty arc in R (circle radius 10 yd)
BOX_D, BOX_HW = 1.8, 2.2
GOAL_AREA_D, GOAL_AREA_HW = 0.6, 1.0
SPOT_D = 1.2

# ---- the pitch ------------------------------------------------------------------


@dataclass(frozen=True)
class PitchModel:
    L: float = 12.31        # length in R (the scrimmage venue's fit)
    W: float = 7.74         # width in R

    def lines(self, step: float = 0.05) -> list[np.ndarray]:
        """The painted lines as polylines of ground points (n, 2), every `step` R."""
        L, W, c = self.L, self.W, self.W / 2

        def seg(a, b):
            a, b = np.asarray(a, float), np.asarray(b, float)
            n = max(2, int(np.ceil(np.hypot(*(b - a)) / step)) + 1)
            return a + np.linspace(0, 1, n)[:, None] * (b - a)

        def arc(cx, cy, r, a0, a1):
            n = max(2, int(np.ceil(r * abs(a1 - a0) / step)) + 1)
            a = np.linspace(a0, a1, n)
            return np.c_[cx + r * np.cos(a), cy + r * np.sin(a)]

        out = [seg((0, 0), (L, 0)), seg((0, W), (L, W)),         # touchlines
               seg((0, 0), (0, W)), seg((L, 0), (L, W)),         # goal lines
               seg((L / 2, 0), (L / 2, W)),                      # halfway
               arc(L / 2, c, 1.0, 0, 2 * np.pi)]                 # centre circle
        for g, s in ((0.0, 1.0), (L, -1.0)):
            for d, hw in ((BOX_D, BOX_HW), (GOAL_AREA_D, GOAL_AREA_HW)):
                out += [seg((g, c - hw), (g + s * d, c - hw)), seg((g + s * d, c - hw), (g + s * d, c + hw)),
                        seg((g + s * d, c + hw), (g, c + hw))]
            # the arc outside the box: |u - spot| > 0.6 of a radius of 1
            a = np.arccos((BOX_D - SPOT_D) / 1.0)
            out.append(arc(g + s * SPOT_D, c, 1.0, -a, a) if s > 0 else arc(g + s * SPOT_D, c, 1.0, np.pi - a, np.pi + a))
        return out

    def to_sb(self) -> np.ndarray:
        """Pitch (R units) -> StatsBomb 120 x 80."""
        return np.diag([120.0 / self.L, 80.0 / self.W, 1.0])


# ---- the paint ------------------------------------------------------------------


LINES = os.environ.get('PIQ_LINES', 'gold')   # the soccer lines' paint: 'gold' (the home field) or 'white'


def playing_surface(image: np.ndarray, size=(240, 135), diff: float = 4.0, grow: int = 1) -> np.ndarray:
    """The ground the camera sees, as a (h, w) bool mask at `size`: colour grown
    from the bottom of the frame, a small step at a time so haze and vignetting
    do not stop it, then every column filled from its highest reach down (lines
    and players punch holes in it) and `grow` cells more above. Stops at the
    fence and the trees, so a fence's top rail, a white line above the grass,
    is not paint."""
    w, h = size
    lab = cv2.cvtColor(cv2.medianBlur(cv2.resize(image, size, interpolation=cv2.INTER_AREA), 5), cv2.COLOR_BGR2LAB)
    mask = np.zeros((h + 2, w + 2), np.uint8)
    for x in range(w // 12, w - w // 12, w // 12):
        cv2.floodFill(lab, mask, (x, h - 5), 0, (diff,) * 3, (diff,) * 3, 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8))
    m = mask[1:-1, 1:-1] > 0
    top = np.where(m.any(0), m.argmax(0), h)
    return np.arange(h)[:, None] >= (top - grow)[None, :]


def paint_mask(image: np.ndarray, scale: float = 0.5, gold: float = 0.45, logo: int = 41,
               colour: str = None) -> np.ndarray:
    """Thin gold paint at `scale` of the frame's size. Both venues paint the
    soccer lines gold; the orange and red of other sports' lines and the
    running track are refused (`gold`: least green rise per unit of red rise).
    `colour` 'white' (default: LINES) takes even rises in all three channels
    instead and refuses the blue of other sports' lines.

    A line is a thin ridge brighter than the grass beside it (a white top-hat
    on brightness, which drops sunlit turf) whose colour, once the grass is
    taken away, leans red-yellow rather than green, blue or white. A far line
    is a pixel wide and half grass, so its own hue is no help; the difference
    from the grass is. Thick blobs (club logos, a goal's shadow) are removed:
    a painted line is never wider than a few pixels at this size.
    """
    im = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    f = im.astype(np.float32)
    v = f.max(2)
    top = cv2.morphologyEx(v, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    bg = cv2.medianBlur(im, 15).astype(np.float32)
    d = f - bg
    b, g, r = d[..., 0], d[..., 1], d[..., 2]
    if (colour or LINES) == 'white':
        warm = (np.minimum(np.minimum(r, g), b) > 12) & (b < r + 15) & (b < g + 15)   # white, not blue
        logo = 0
    else:
        warm = (r > 12) & (r > b + 8) & (g > gold * r)  # gold, not the red track or orange lacrosse
    # Lines have look-alikes off the pitch (a white fence rail, a bleacher edge, warm
    # blobs in the stands and sky at dusk), and with no far touchline painted (East
    # Brunswick) or a rail along it (North Brunswick) they pull that line off the grass.
    warm &= cv2.resize(playing_surface(image).astype(np.uint8), warm.shape[::-1], interpolation=cv2.INTER_NEAREST) > 0
    m = ((top > 12) & warm).astype(np.uint8) * 255
    if (colour or LINES) == 'white':
        # Sunlit white lines come out as broken one-pixel streaks at this size: a 2 x 2
        # opening took most of North Brunswick's circle and halfway line. Keep any pixel
        # with a neighbour along some direction; only lone specks go.
        m = np.bitwise_or.reduce([cv2.morphologyEx(m, cv2.MORPH_OPEN, np.array(k, np.uint8))
                                  for k in ([[1, 1]], [[1], [1]], [[1, 0], [0, 1]], [[0, 1], [1, 0]])])
    else:
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    blob = cv2.erode(m, np.ones((9, 9), np.uint8))
    blob = cv2.dilate(blob, np.ones((11, 11), np.uint8))
    if logo:
        # a painted club logo: a solid saturated-yellow fill whose stripes and edges
        # look like lines. Lines are never solid this wide, so drop all paint near one.
        hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
        fill = cv2.inRange(hsv, (12, 90, 90), (28, 255, 255))
        fill = cv2.morphologyEx(fill, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))   # cut lines off the logo
        n, lab, st, _ = cv2.connectedComponentsWithStats(fill, connectivity=8)
        keep = np.zeros(n, bool)
        for i in range(1, n):
            if st[i, cv2.CC_STAT_AREA] < 120:
                continue
            ys, xs = np.nonzero(lab[st[i, 1]:st[i, 1] + st[i, 3], st[i, 0]:st[i, 0] + st[i, 2]] == i)
            (_, _), (w, h), _ = cv2.minAreaRect(np.c_[xs, ys].astype(np.float32))
            keep[i] = max(w, h) < 8 * max(min(w, h), 1)      # compact, not a line
        blob |= cv2.dilate(keep[lab].astype(np.uint8) * 255, np.ones((logo, logo), np.uint8))
    return m & ~blob


# ---- the camera -----------------------------------------------------------------


def rotation(pan, tilt, roll):
    """World -> camera rotation(s). pan 0 looks across the pitch (+v), positive
    pan turns towards +u; tilt is down from level; roll turns the picture.
    Broadcasts over arrays of angles: returns (..., 3, 3)."""
    pan, tilt, roll = np.broadcast_arrays(*(np.asarray(a, float) for a in (pan, tilt, roll)))
    cp, sp, ct, st = np.cos(pan), np.sin(pan), np.cos(tilt), np.sin(tilt)
    d = np.stack([sp * ct, cp * ct, -st], -1)                  # forward
    r = np.stack([cp, -sp, np.zeros_like(cp)], -1)             # right, level
    dn = np.cross(d, r)                                        # down
    cr, sr = np.cos(roll)[..., None], np.sin(roll)[..., None]
    r, dn = cr * r + sr * dn, -sr * r + cr * dn
    return np.stack([r, dn, d], -2)


def ground_to_image(C, pan, tilt, roll, f, size=(1920, 1080)):
    """Homography(s) from ground (u, v, 1) to pixels, for a camera at C = (u, v, h)."""
    R = rotation(pan, tilt, roll)
    f = np.asarray(f, float)
    K = np.zeros(f.shape + (3, 3)); K[..., 0, 0] = f; K[..., 1, 1] = f
    K[..., 0, 2] = size[0] / 2; K[..., 1, 2] = size[1] / 2; K[..., 2, 2] = 1
    M = np.array([[1.0, 0, -C[0]], [0, 1.0, -C[1]], [0, 0, -C[2]]])
    return K @ R @ M


class Paint:
    """One frame's paint, ready to score camera guesses against."""

    def __init__(self, image: np.ndarray, scale: float = 0.5):
        self.scale = scale
        self.mask = paint_mask(image, scale)
        self.h, self.w = self.mask.shape
        self.dt = cv2.distanceTransform(255 - self.mask, cv2.DIST_L2, 3)
        self.amount = int((self.mask > 0).sum())


class Model:
    """The pitch's painted lines as points, with each point's neighbour for spacing."""

    def __init__(self, pitch: PitchModel, step: float = 0.1):
        lines = pitch.lines(step)
        self.pitch = pitch
        self.pts = np.concatenate(lines)
        nxt = []
        i = 0
        for ln in lines:
            n = len(ln)
            nxt += list(range(i + 1, i + n)) + [-1]
            i += n
        self.next = np.array(nxt)
        self.has_next = self.next >= 0
        self.h = np.c_[self.pts, np.ones(len(self.pts))]


def score(G: np.ndarray, model: Model, paint: Paint, sigma: float = 2.0, miss: float = 0.3):
    """How well guesses G (..., 3, 3) put the model on the paint.

    Model line length (in paint-image pixels) that lands on paint, less `miss`
    times the length in view that lands on bare grass. Lines out of view, or
    behind the camera, count for nothing either way.
    """
    s = paint.scale
    q = np.einsum('...ij,nj->...ni', G, model.h)
    z = q[..., 2]
    xy = q[..., :2] / np.where(np.abs(z) < 1e-9, 1e-9, z)[..., None] * s
    nb = xy[..., np.where(model.has_next, model.next, 0), :]
    seg = np.linalg.norm(nb - xy, axis=-1) * model.has_next
    ok = (z > 0) & (xy[..., 0] >= 0) & (xy[..., 0] < paint.w - 1) & (xy[..., 1] >= 0) & (xy[..., 1] < paint.h - 1)
    ok &= seg < 40                                               # a jump: line leaving through the horizon
    xi = np.clip(xy[..., 0], 0, paint.w - 1).astype(int); yi = np.clip(xy[..., 1], 0, paint.h - 1).astype(int)
    d = paint.dt[yi, xi]
    hit = np.exp(-0.5 * (d / sigma) ** 2)
    return ((hit - miss * (1 - hit)) * seg * ok).sum(-1)


# ---- the picture moving ---------------------------------------------------------

CENTRE = np.array([960.0, 540.0])


def frame_features(image: np.ndarray, sift=None, scale: float = 0.5):
    """SIFT over the whole picture (grass, stands and trees all move together
    when the camera turns), in full-frame pixel coordinates."""
    sift = sift or cv2.SIFT_create(nfeatures=3000)
    g = cv2.cvtColor(cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    kp, d = sift.detectAndCompute(g, None)
    xy = np.float32([p.pt for p in kp]).reshape(-1, 2) / scale
    return xy, (np.zeros((0, 128), np.float32) if d is None else d)


def link(a, b, max_pairs: int = 80, rng=None):
    """Matched points between two frames' features that agree on one homography
    (players running are the outliers). Returns (xa, xb) or None."""
    return thin_link(link_inliers(a, b), max_pairs, rng)


def link_inliers(a, b):
    """`link` before the thinning: every inlier pair, (xa, xb) or None. It draws
    no random numbers, so pairs can be matched in any order or process."""
    (xa, da), (xb, db) = a, b
    if len(da) < 20 or len(db) < 20:
        return None
    m = cv2.BFMatcher().knnMatch(da, db, k=2)
    good = [p[0] for p in m if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < 30:
        return None
    pa = xa[[g.queryIdx for g in good]]; pb = xb[[g.trainIdx for g in good]]
    H, inl = cv2.findHomography(pa, pb, cv2.RANSAC, 3.0)
    if H is None or inl.sum() < 30:
        return None
    k = np.flatnonzero(inl.ravel())
    return pa[k], pb[k]


def thin_link(pairs, max_pairs: int = 80, rng=None):
    """At most max_pairs of `link_inliers`' pairs, drawn from rng."""
    if pairs is None:
        return None
    pa, pb = pairs
    k = np.arange(len(pa))
    rng = rng or np.random.default_rng(0)
    if len(k) > max_pairs:
        k = rng.choice(k, max_pairs, replace=False)
    return pa[k].astype(float), pb[k].astype(float)


def rodrigues(w):
    """Rotation matrices from rotation vectors (n, 3)."""
    w = np.atleast_2d(w)
    th = np.linalg.norm(w, axis=1)[:, None, None]
    k = w / np.maximum(th[:, 0], 1e-12)
    Kx = np.zeros((len(w), 3, 3))
    Kx[:, 0, 1], Kx[:, 0, 2], Kx[:, 1, 2] = -k[:, 2], k[:, 1], -k[:, 0]
    Kx -= Kx.transpose(0, 2, 1)
    return np.eye(3) + np.sin(th) * Kx + (1 - np.cos(th)) * Kx @ Kx


def rays(xy, f):
    return np.c_[(xy - CENTRE) / f, np.ones(len(xy))]


def to_px(v, f):
    return v[:, :2] / v[:, 2:3] * f + CENTRE


# ---- distance to the paint, on the ground -----------------------------------------


class Primitives:
    """The model's lines as segments and arcs, for exact nearest-point queries."""

    def __init__(self, pitch: PitchModel):
        L, W, c = pitch.L, pitch.W, pitch.W / 2
        segs = [((0, 0), (L, 0)), ((0, W), (L, W)), ((0, 0), (0, W)), ((L, 0), (L, W)), ((L / 2, 0), (L / 2, W))]
        for g, s in ((0.0, 1.0), (L, -1.0)):
            for d, hw in ((BOX_D, BOX_HW), (GOAL_AREA_D, GOAL_AREA_HW)):
                segs += [((g, c - hw), (g + s * d, c - hw)), ((g + s * d, c - hw), (g + s * d, c + hw)),
                         ((g + s * d, c + hw), (g, c + hw))]
        self.a = np.array([s[0] for s in segs], float)
        self.b = np.array([s[1] for s in segs], float)
        a = np.arccos((BOX_D - SPOT_D) / 1.0)
        # (centre u, centre v, radius, angle lo, angle hi)
        self.arcs = np.array([(L / 2, c, 1.0, -np.pi, np.pi),
                              (SPOT_D, c, 1.0, -a, a),
                              (L - SPOT_D, c, 1.0, np.pi - a, np.pi + a)])
        self.centre = np.array([L / 2, c])

    def nearest(self, p: np.ndarray, with_id: bool = False):
        """The nearest point on any line to each ground point p (n, 2); with
        `with_id` also which line (segments first, then arcs)."""
        ab = self.b - self.a
        t = np.clip(((p[:, None, :] - self.a) * ab).sum(-1) / (ab ** 2).sum(-1), 0, 1)
        q = self.a + t[..., None] * ab                                   # (n, segs, 2)
        cands = [q]
        for cu, cv, r, lo, hi in self.arcs:
            ang = np.arctan2(p[:, 1] - cv, p[:, 0] - cu)
            mid = (lo + hi) / 2
            ang = mid + (ang - mid + np.pi) % (2 * np.pi) - np.pi      # unwrap about the arc's middle
            ang = np.clip(ang, lo, hi)
            cands.append(np.stack([cu + r * np.cos(ang), cv + r * np.sin(ang)], -1)[:, None, :])
        q = np.concatenate(cands, 1)
        k = np.argmin(((q - p[:, None, :]) ** 2).sum(-1), 1)
        return (q[np.arange(len(p)), k], k) if with_id else q[np.arange(len(p)), k]

    def independent(self, ids: np.ndarray, xy: np.ndarray, each: int = 20, spread: float = 80.0) -> bool:
        """Do the lines in `ids` (one per paint pixel, at image points `xy`) fix a
        pose? Paint along a single line lets the camera slide along it; it takes two
        lines that cross in direction (or one of them curved), each with `each`
        pixels spread over `spread` px of the image: on eb1 91650 a referee's gold
        shirt made a second line out of 10 pixels."""
        n_seg = len(self.a)
        got = [i for i in np.unique(ids) if (ids == i).sum() >= each
               and np.linalg.norm(np.ptp(xy[ids == i], 0)) >= spread]
        d = [(self.b[i] - self.a[i]) / np.linalg.norm(self.b[i] - self.a[i]) for i in got if i < n_seg]
        curved = sum(i >= n_seg for i in got)
        if curved and len(got) >= 2:
            return True
        return any(abs(u[0] * v[1] - u[1] * v[0]) > 0.5 for j, u in enumerate(d) for v in d[j + 1:])


def paint_points(paint: Paint, n: int = 400, rng=None) -> np.ndarray:
    """Up to n paint pixels, in full-frame coordinates."""
    ys, xs = np.nonzero(paint.mask)
    if len(xs) > n:
        k = (rng or np.random.default_rng(0)).choice(len(xs), n, replace=False)
        xs, ys = xs[k], ys[k]
    return (np.c_[xs, ys] + 0.5) / paint.scale


# ---- the camera of a whole stretch -------------------------------------------------

from scipy.optimize import least_squares          # noqa: E402


@dataclass
class Rig:
    """One camera position and every sample's rotation (world -> camera) and focal length."""
    C: np.ndarray            # (3,) u, v, height in R
    R: np.ndarray            # (n, 3, 3)
    f: np.ndarray            # (n,)

    def ground_to_image(self, k: int) -> np.ndarray:
        K = np.array([[self.f[k], 0, CENTRE[0]], [0, self.f[k], CENTRE[1]], [0, 0, 1]])
        M = np.array([[1.0, 0, -self.C[0]], [0, 1.0, -self.C[1]], [0, 0, -self.C[2]]])
        return K @ self.R[k] @ M

    def to_sb(self, k: int, pitch: PitchModel) -> np.ndarray:
        """Frame k pixels -> StatsBomb."""
        H = pitch.to_sb() @ np.linalg.inv(self.ground_to_image(k))
        return H / H[2, 2]


def fit_sample(xy, R, f: float, C, pitch: PitchModel, x0=None, f_scale: float = 3.0, logo_r: float = 0.9,
               dep0: float = 2.0, tol: float = 4.0, step: float = 2.0, zoom: float = 0.1):
    """One sample's correction to its chained pose, from its own paint: a small
    rotation (vector, applied as rodrigues(x) @ R) and log focal change pulling
    the paint pixels xy (full frame) onto the nearest model line, measured in the
    image (Cauchy, so kits and benches give up). Returns (x, paint within `tol`
    px after, before at x0; 0 unless it fixes the pose, `Primitives.independent`). The fit stays within `step` degrees and `zoom` log
    focal of x0: left free, it slid onto whichever other line was nearest."""
    C = np.asarray(C, float)
    prim = Primitives(pitch)
    sin0 = np.sin(np.radians(dep0))
    x0 = np.zeros(4) if x0 is None else np.asarray(x0, float)

    def proj(x):
        Rk = rodrigues(x[None, :3])[0] @ R
        fk = f * np.exp(x[3])
        d = rays(xy, fk) @ Rk                           # world directions
        ok = -d[:, 2] > sin0
        t = np.where(ok, -C[2] / np.where(ok, d[:, 2], -1), 0)
        X = C[:2] + t[:, None] * d[:, :2]
        ok &= np.hypot(*(X - prim.centre).T) > logo_r
        ok &= (X[:, 0] > -1) & (X[:, 0] < pitch.L + 1) & (X[:, 1] > -1) & (X[:, 1] < pitch.W + 1)
        q, ids = prim.nearest(X, with_id=True)
        e = to_px((np.c_[q, np.zeros(len(q))] - C) @ Rk.T, fk) - xy
        e[~ok] = 0
        return e, ok, ids

    def res(x):
        return proj(x)[0].ravel()

    def good(x):
        # paint on the lines, counted only when it lies on two independent lines:
        # eb1 91200-91800 saw one near line, and fitting it tilted the far box off
        e, ok, ids = proj(x)
        on = ok & (np.hypot(*e.T) < tol)
        return int(on.sum()) if prim.independent(ids[on], xy[on]) else 0

    if step <= 0:
        return x0, good(x0), good(x0)
    xs = np.r_[0.002, 0.002, 0.002, 0.01]
    span = np.r_[[np.radians(step)] * 3, zoom]
    lo, hi = x0 - span, x0 + span
    x = least_squares(res, x0, loss='cauchy', f_scale=25.0, x_scale=xs, max_nfev=60, bounds=(lo, hi)).x
    x = least_squares(res, x, loss='cauchy', f_scale=f_scale, x_scale=xs, max_nfev=60, bounds=(lo, hi)).x
    return x, good(x), good(x0)


def anchor_chain(paint: dict, R, f, C, R0, pitch: PitchModel, min_good: int = 40, half: int = 10, log=None):
    """Tie a long stretch's chained rotations back to the pitch, sample by sample.

    The chain walks forward along SIFT links with nothing to stop it wandering:
    over North Brunswick's first half it drifted by up to ~10 degrees, so the
    stitched panorama smeared and the pitch landed off the paint. Walking the
    samples in order, each one's own paint is fitted from the running
    correction and a few degrees around it; the fit, the running correction or
    none at all (the chain itself), whichever puts the most paint on the lines,
    is kept when that is at least `min_good` pixels. The kept corrections are then
    median-smoothed over +-`half` samples and interpolated across the rest; last,
    each sample with paint enough to judge takes the smoothed correction, its own
    fit or none, whichever its paint prefers.
    Returns (R (n, 3, 3) world -> camera, f (n,), info)."""
    n = len(R)
    R = np.asarray(R) @ R0
    xs = np.full((n, 4), np.nan)
    cur, recent = np.zeros(4), []
    for k in range(n):
        xy = paint.get(k)
        if xy is None or len(xy) < min_good:
            continue
        x, g, g_cur = fit_sample(xy, R[k], f[k], C, pitch, x0=cur)
        g_chain = fit_sample(xy, R[k], f[k], C, pitch, step=0)[1] if np.any(cur) else g_cur
        x, g = max([(x, g), (cur, g_cur), (np.zeros(4), g_chain)], key=lambda c: c[1])
        if g >= min_good:
            xs[k] = x
            recent = (recent + [x])[-half:]
            cur = np.median(recent, 0)
    kept = np.flatnonzero(~np.isnan(xs[:, 0]))
    if not len(kept):
        return R, np.asarray(f, float), dict(kept=0, n=n)
    sm = np.array([np.median(xs[kept[(kept >= kept[i] - half) & (kept <= kept[i] + half)]], 0)
                   for i in range(len(kept))])
    corr = np.stack([np.interp(np.arange(n), kept, sm[:, j]) for j in range(4)], 1)
    # Smoothing carries a neighbour's correction across a jump in the chain onto
    # samples the chain had right (eb1 83000: 190 px on the lines -> 0). Where a
    # sample has paint enough to judge, it keeps whichever of the smoothed
    # correction, its own fit and none puts the most paint on the lines.
    for k in range(n):
        xy = paint.get(k)
        if xy is None or len(xy) < min_good:
            continue
        cands = [corr[k], np.zeros(4)] + ([] if np.isnan(xs[k, 0]) else [xs[k]])
        score = [fit_sample(xy, R[k], f[k], C, pitch, x0=c, step=0)[1] for c in cands]
        if max(score) >= min_good:
            corr[k] = cands[int(np.argmax(score))]
    R2 = rodrigues(corr[:, :3]) @ R
    f2 = np.asarray(f, float) * np.exp(corr[:, 3])
    ang = np.degrees(np.linalg.norm(corr[:, :3], axis=1))
    info = dict(kept=len(kept), n=n, ang_p50=float(np.median(ang)), ang_p90=float(np.percentile(ang, 90)),
                ang_max=float(ang.max()))
    if log:
        log('anchored %d/%d samples to their paint; correction p50 %.2f p90 %.2f max %.2f deg'
            % (len(kept), n, info['ang_p50'], info['ang_p90'], info['ang_max']))
    return R2, f2, info


def relative_rotation(xi, xj, fi: float):
    """Rotation and focal length taking frame i (focal fi) to frame j."""
    def res(p):
        R = rodrigues(p[:3])[0]
        return (to_px(rays(xi, fi) @ R.T, np.exp(p[3])) - xj).ravel()
    sol = least_squares(res, np.r_[0, 0, 0, np.log(fi)], loss='cauchy', f_scale=2.0)
    return rodrigues(sol.x[:3])[0], float(np.exp(sol.x[3]))


def chain_rotations(links, n: int, f0: float):
    """A starting guess: walk forward along the shortest links."""
    best = {}
    for i, j, xi, xj in links:
        if j not in best or i > best[j][0]:
            best[j] = (i, xi, xj)
    R = np.tile(np.eye(3), (n, 1, 1)); f = np.full(n, float(f0))
    for j in range(1, n):
        if j in best:
            i, xi, xj = best[j]
            Rr, f[j] = relative_rotation(xi, xj, f[i])
            R[j] = Rr @ R[i]
        else:
            R[j], f[j] = R[j - 1], f[j - 1]
    return R, f


def best_focal(links, n: int, lo: float = 600.0, hi: float = 9000.0, pool=None):
    """Sample 0's focal length: the one under which all the links agree best.

    With the wrong focal length the rotations chained along short links do not
    explain the longer ones (0.5 vs 2 s apart); the residual has one clear
    minimum (scrimmage: 2110 found vs 2053 from the accepted registration).

    Each cost is a whole chain (half a minute over a half), so `pool` (one
    started with `focal_pool_init(links, n)`) prices the grid at once and each
    golden-section pair side by side; the answer is the same either way."""
    if pool is None:
        costs = lambda lfs: [_focal_cost(links, n, lf) for lf in lfs]   # noqa: E731
    else:
        costs = lambda lfs: pool.map(_pooled_focal_cost, lfs, chunksize=1)   # noqa: E731
    grid = np.linspace(np.log(lo), np.log(hi), 15)
    c = costs(grid)
    k = int(np.argmin(c))
    a, b = grid[max(k - 1, 0)], grid[min(k + 1, len(grid) - 1)]
    for _ in range(12):                                   # golden section
        m1, m2 = b - 0.618 * (b - a), a + 0.618 * (b - a)
        c1, c2 = costs([m1, m2])
        if c1 < c2:
            b = m2
        else:
            a = m1
    return float(np.exp((a + b) / 2))


def _focal_cost(links, n: int, lf: float) -> float:
    R, f = chain_rotations(links, n, np.exp(lf))
    e = np.concatenate([np.hypot(*(to_px(rays(xi, f[i]) @ R[i] @ R[j].T, f[j]) - xj).T)
                        for i, j, xi, xj in links])
    return float(np.minimum(e, 10).mean())


_FOCAL = {}


def focal_pool_init(links, n: int) -> None:
    """A `best_focal` pool worker's copy of the links."""
    _FOCAL.update(links=links, n=n)


def _pooled_focal_cost(lf: float) -> float:
    return _focal_cost(_FOCAL['links'], _FOCAL['n'], lf)


# ---- the camera's whole view, stitched ----------------------------------------------


@dataclass
class Panorama:
    """Paint seen from a fixed camera, stitched over many samples into one image
    in sample 0's camera frame: x = F*azimuth + cx, y = F*elevation + cy.

    Paint that stays put through the stretch is the pitch; players walk off it."""
    paint: np.ndarray        # (h, w) float, fraction of the samples that saw paint here
    cover: np.ndarray        # (h, w) int, how many samples saw this direction
    F: float
    cx: float
    cy: float

    def to_px(self, v):
        """Camera-0 directions (..., 3) -> panorama pixels (..., 2)."""
        az = np.arctan2(v[..., 0], v[..., 2]); el = np.arctan2(v[..., 1], np.hypot(v[..., 0], v[..., 2]))
        return np.stack([self.F * az + self.cx, self.F * el + self.cy], -1)

    def directions(self):
        h, w = self.paint.shape
        az = (np.arange(w) - self.cx) / self.F; el = (np.arange(h) - self.cy) / self.F
        az, el = np.meshgrid(az, el)
        return np.stack([np.sin(az) * np.cos(el), np.sin(el), np.cos(az) * np.cos(el)], -1)


def build_panorama(masks: dict, R_rel, f, scale: float = 0.5, F: float = None) -> Panorama:
    """masks: {k: paint mask at `scale`}; R_rel: (n, 3, 3) rotations from sample 0's
    camera to sample k's; f: (n,) focal lengths (full-res px)."""
    ks = sorted(masks)
    F = F or float(np.median(f[ks])) * scale
    corners = np.array([[0, 0], [1920, 0], [0, 1080], [1920, 1080], [960, 0], [960, 1080], [0, 540], [1920, 540]], float)
    dirs = []
    for k in ks:
        d = np.c_[(corners - CENTRE) / f[k], np.ones(len(corners))] @ R_rel[k]      # R_k^T x, row-wise
        dirs.append(d)
    d = np.concatenate(dirs)
    az = np.arctan2(d[:, 0], d[:, 2]); el = np.arctan2(d[:, 1], np.hypot(d[:, 0], d[:, 2]))
    x0, x1, y0, y1 = az.min() * F - 4, az.max() * F + 4, el.min() * F - 4, el.max() * F + 4
    w, h = int(x1 - x0), int(y1 - y0)
    pano = Panorama(np.zeros((h, w), np.float32), np.zeros((h, w), np.int32), F, -x0, -y0)
    D = pano.directions()
    hits = np.zeros((h, w), np.float32)
    for k in ks:
        # only the panorama cells the frame can reach: the box round its outline
        # (a frame is a few hundred of the panorama's thousands of columns)
        y0k, y1k, x0k, x1k = _footprint(pano, R_rel[k], f[k], h, w)
        v = D[y0k:y1k, x0k:x1k].reshape(-1, 3) @ R_rel[k].T
        ok = v[:, 2] > 1e-3
        px = np.where(ok[:, None], v[:, :2] / np.where(ok, v[:, 2], 1)[:, None], 0) * f[k] + CENTRE
        px *= scale
        m = masks[k]
        xi = np.round(px[:, 0]).astype(int); yi = np.round(px[:, 1]).astype(int)
        ok &= (xi >= 0) & (xi < m.shape[1]) & (yi >= 0) & (yi < m.shape[0])
        ok2 = ok.reshape(y1k - y0k, x1k - x0k)
        pano.cover[y0k:y1k, x0k:x1k][ok2] += 1
        hits[y0k:y1k, x0k:x1k][ok2] += m[yi[ok], xi[ok]] > 0
    pano.paint[:] = hits / np.maximum(pano.cover, 1)
    return pano


_XS, _YS = np.arange(-4.0, 1925, 8), np.arange(-4.0, 1085, 8)
_OUTLINE = np.concatenate([np.c_[_XS, np.full(len(_XS), _YS[0])], np.c_[_XS, np.full(len(_XS), _YS[-1])],
                           np.c_[np.full(len(_YS), _XS[0]), _YS], np.c_[np.full(len(_YS), _XS[-1]), _YS]])


def _footprint(pano: Panorama, R, f: float, h: int, w: int, pad: int = 12):
    """Rows y0..y1 and columns x0..x1 of the panorama holding every cell a frame
    (rotation R from sample 0's camera, focal f) sees: its outline, a few pixels
    wide of the frame, sampled every 8 px and padded. The whole panorama when
    the frame would wrap round behind the camera."""
    p = pano.to_px(np.c_[(_OUTLINE - CENTRE) / f, np.ones(len(_OUTLINE))] @ R)
    if np.ptp(p[:, 0]) > pano.F * np.pi:
        return 0, h, 0, w
    x0, y0 = np.floor(p.min(0)).astype(int) - pad
    x1, y1 = np.ceil(p.max(0)).astype(int) + pad
    return max(y0, 0), min(max(y1, 0), h), max(x0, 0), min(max(x1, 0), w)


def explained_paint(pano: Panorama, C, R0, pitch: PitchModel, tol: float = 4.0, thresh: float = 0.25,
                    step: float = 0.02, dep0: float = 2.0):
    """How much of the stitched paint (in pixels, below 2*dep0 degrees under the
    horizon) lies within `tol` panorama pixels of the pitch's lines under this pose."""
    paint = pano.paint >= thresh
    D = pano.directions()
    Rw = D @ R0                                                     # world directions of panorama pixels
    below = -Rw[..., 2] > np.sin(np.radians(2 * dep0))
    canvas = np.zeros(paint.shape, np.uint8)
    for ln in pitch.lines(step):
        v = (np.c_[ln, np.zeros(len(ln))] - C) @ R0.T
        ok = v[:, 2] > 0
        xy = pano.to_px(v)
        for seg in np.split(np.arange(len(xy)), np.nonzero(~ok)[0]):
            seg = seg[ok[seg]]
            if len(seg) > 1:
                cv2.polylines(canvas, [np.round(xy[seg]).astype(np.int32)], False, 255, int(2 * tol))
    return int((paint & below & (canvas > 0)).sum()), int((paint & below).sum())


def refine_pano(pano: Panorama, C, R0, pitch: PitchModel, n: int = 4000, thresh: float = 0.25, f_scale: float = 3.0,
                dep0: float = 4.0, free_W: bool = True, free_L: bool = False, logo_r: float = 0.9, rng=None,
                fix_h: float = None, fix_C: bool = False):
    """Least-squares polish in the panorama: every stitched paint pixel well below
    the horizon is pulled to the nearest model line (Cauchy loss, so the other
    sports' lines and the club logo give up rather than drag). Free: the camera
    position, sample 0's rotation and, optionally, the pitch's width and length
    in circle radii. `fix_C` holds the camera where it is (rotation, and W if free,
    only). Returns (C, R0, pitch, least_squares result)."""
    rng = rng or np.random.default_rng(0)
    ys, xs = np.nonzero(pano.paint >= thresh)
    if len(xs) > n:
        k = rng.choice(len(xs), n, replace=False); xs, ys = xs[k], ys[k]
    xy = np.c_[xs, ys].astype(float)
    az = (xy[:, 0] - pano.cx) / pano.F; el = (xy[:, 1] - pano.cy) / pano.F
    D = np.c_[np.sin(az) * np.cos(el), np.sin(el), np.cos(az) * np.cos(el)]
    sin0 = np.sin(np.radians(dep0))

    def unpack(x):
        R = rodrigues(x[None, 3:6])[0] @ R0
        L = pitch.L * np.exp(x[7]) if free_L else pitch.L
        W = pitch.W * np.exp(x[6]) if free_W else pitch.W
        C_ = np.asarray(C, float) if fix_C else x[:3] if fix_h is None else np.r_[x[:2], fix_h]
        return C_, R, PitchModel(L=L, W=W)

    def res(x):
        C_, R, pm = unpack(x)
        prim = Primitives(pm)
        d = D @ R                                              # world directions
        ok = -d[:, 2] > sin0
        t = np.where(ok, -C_[2] / np.where(ok, d[:, 2], -1), 0)
        X = C_[:2] + t[:, None] * d[:, :2]
        ok &= np.hypot(*(X - prim.centre).T) > logo_r
        q = prim.nearest(X)
        v = (np.c_[q, np.zeros(len(q))] - C_) @ R.T
        e = pano.to_px(v) - xy
        e[~ok] = 0
        return e.ravel()

    x0 = np.zeros(8); x0[:3] = C
    sol = least_squares(res, x0, loss='cauchy', f_scale=f_scale,
                        x_scale=np.r_[0.05, 0.05, 0.02, 0.002, 0.002, 0.002, 0.01, 0.01], max_nfev=200)
    C_, R, pm = unpack(sol.x)
    return C_, R, pm, sol


def line_paint(paint: np.ndarray, min_len: float = 25.0, sigma: float = 4.0, coh: float = 0.6) -> np.ndarray:
    """The line-like part of a boolean paint mask: pixels whose neighbourhood has
    one clear direction (structure-tensor coherence >= `coh`), in pieces at least
    `min_len` px across. Foliage, shirts and crowd speckle go; lines, arcs and
    letter strokes stay."""
    f = cv2.GaussianBlur(paint.astype(np.float32), (0, 0), 1.0)
    gx = cv2.Sobel(f, cv2.CV_32F, 1, 0, ksize=3); gy = cv2.Sobel(f, cv2.CV_32F, 0, 1, ksize=3)
    xx, yy, xy = (cv2.GaussianBlur(a, (0, 0), sigma) for a in (gx * gx, gy * gy, gx * gy))
    c = np.sqrt((xx - yy) ** 2 + 4 * xy ** 2) / np.maximum(xx + yy, 1e-6)
    n, lab, st, _ = cv2.connectedComponentsWithStats((paint & (c >= coh)).astype(np.uint8), connectivity=8)
    ok = np.hypot(st[:, 2], st[:, 3]) >= min_len; ok[0] = False
    return ok[lab]


def clean_pano(pano: Panorama, thresh: float = 0.25, noisy: float = 2.0) -> Panorama:
    """`pano` with only line-like paint, when most of its paint is not (white-line
    fields under trees: 5x); left alone when the paint is mostly lines already
    (gold lines: 1.2-1.5x), as the filter also nicks real line ends."""
    m = pano.paint >= thresh
    keep = line_paint(m)
    if m.sum() <= noisy * max(keep.sum(), 1):
        return pano
    return Panorama(np.where(keep, pano.paint, 0).astype(np.float32), pano.cover, pano.F, pano.cx, pano.cy)


# ---- a starting pose with no clicks: the halfway line and the centre circle ---------------


def halfway_candidates(paint: np.ndarray, top: int = 6, max_tilt: float = 35.0):
    """Long straight paint runs within `max_tilt` degrees of vertical in a panorama
    mask, longest first, one per line, as (x0, y0, x1, y1) with y0 < y1. Seen from
    the side, the halfway line is the one that stands up."""
    segs = cv2.HoughLinesP(paint.astype(np.uint8) * 255, 1, np.pi / 360, 60, minLineLength=80, maxLineGap=25)
    found = []
    for x0, y0, x1, y1 in ([] if segs is None else segs.reshape(-1, 4).tolist()):
        if y0 > y1:
            x0, y0, x1, y1 = x1, y1, x0, y0
        if abs(np.degrees(np.arctan2(x1 - x0, y1 - y0))) < max_tilt:
            found.append((np.hypot(x1 - x0, y1 - y0), (x0, y0, x1, y1)))
    found.sort(key=lambda t: -t[0])
    lines = []
    for _, (x0, y0, x1, y1) in found:
        d = np.array([x1 - x0, y1 - y0], float) / np.hypot(x1 - x0, y1 - y0)
        off = lambda p: abs(d[0] * (p[1] - y0) - d[1] * (p[0] - x0))
        if any(off(q[:2]) < 12 and off(q[2:]) < 12 for q in lines):
            continue                                    # another piece of a line already kept
        lines.append((x0, y0, x1, y1))
        if len(lines) >= top:
            break
    return lines


def circles_on_line(paint: np.ndarray, line, n_pts: int = 160, tol: float = 4.0, top: int = 3,
                    ring: float = 10.0):
    """Ellipses centred on `line` whose outlines lie on paint (the line's own pixels
    excluded), best first, as [(score, (cx, cy, a, aspect, tilt))], no two alike.
    Score: the semi-major times the worst quarter's share on paint -- a long
    outline wins over a small one hugging the club logo's stripes, and a far
    touchline (one flat side only) does not pass. Players stand on the circle
    for much of a stretch, so a quarter is often a third bare: hence several.
    The halfway line runs on toward the camera past the circle, so the visible
    line must reach at least a circle's height below the centre (the near
    touchline's two arcs meeting at the line's foot look like a circle too).
    Each quarter's share counts only above the emptier of two rings `ring` px
    inside and outside: a painted line is thin, trees and crowds are paint all
    over (white-line fields light the tree line up almost solid)."""
    h, w = paint.shape
    x0, y0, x1, y1 = line
    d = np.array([x1 - x0, y1 - y0], float); n = np.linalg.norm(d); d /= n
    cen = np.array([x0, y0], float) + np.arange(-0.3 * n, 1.3 * n, 3.0)[:, None] * d
    cen = cen[(cen[:, 0] > 0) & (cen[:, 0] < w) & (cen[:, 1] > 0) & (cen[:, 1] < h)]
    below = y1 - cen[:, 1]                              # visible line below each centre, px
    own = np.zeros((h, w), np.uint8)
    cv2.line(own, (int(x0 - 0.5 * n * d[0]), int(y0 - 0.5 * n * d[1])),
             (int(x1 + 0.5 * n * d[0]), int(y1 + 0.5 * n * d[1])), 1, 9)
    dt = cv2.distanceTransform(((paint > 0) & (own == 0)).astype(np.uint8) ^ 1, cv2.DIST_L2, 3)
    th = np.linspace(0, 2 * np.pi, n_pts, endpoint=False)
    quarter = (np.cos(th) < 0) * 2 + (np.sin(th) < 0)
    found = []
    for a in np.arange(40, 700, 6.0):
        for asp in np.arange(0.06, 0.62, 0.02):
            for tilt in np.radians(np.arange(-12, 12.1, 3)):
                share = []
                for off in (0.0, -ring, ring):
                    ex, ey = (a + off) * np.cos(th), max(asp * a + off, 1.0) * np.sin(th)
                    X = np.round(cen[:, :1] + ex * np.cos(tilt) - ey * np.sin(tilt)).astype(int)
                    Y = np.round(cen[:, 1:] + ex * np.sin(tilt) + ey * np.cos(tilt)).astype(int)
                    inb = (X >= 0) & (X < w) & (Y >= 0) & (Y < h)
                    hit = np.zeros(X.shape, bool)
                    hit[inb] = dt[Y[inb], X[inb]] < tol
                    share.append(np.array([hit[:, quarter == q].mean(1) for q in range(4)]))
                lift = share[0] - np.minimum(share[1], share[2])          # on the line, not just in the noise
                sc = a * np.min(lift, 0) * (below > 2 * asp * a)
                k = int(sc.argmax())
                if sc[k] > 0:
                    found.append((float(sc[k]), (cen[k, 0], cen[k, 1], a, asp, tilt)))
    found.sort(key=lambda c: -c[0])
    keep = []
    for sc, e in found:
        if all(np.hypot(e[0] - f[0], e[1] - f[1]) > 0.2 * max(e[2], f[2]) or abs(np.log(e[2] / f[2])) > 0.25
               or abs(np.log(e[3] / f[3])) > 0.35 for _, f in keep):
            keep.append((sc, e))
            if len(keep) >= top:
                break
    return keep


def _kabsch(world, cam):
    U, _, Vt = np.linalg.svd(cam.T @ world)
    return U @ np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))]) @ Vt


def calibrate_pano(pano: Panorama, L: float = 12.31, W0: float = 7.5, thresh: float = 0.25, circles: int = 6,
                   log=None):
    """The whole pose from the stitched paint, no clicks: find the halfway line and
    the centre circle; the circle's size gives the camera's distance and its
    squash (minor / major = sine of the depression) its height; aim along the
    line; refine from a few starts; keep the pitch-sized answer that explains the
    most paint. The best few circles are each tried. Returns (C, R0, pitch, info)
    or None."""
    pano = clean_pano(pano, thresh)
    paint = pano.paint >= thresh
    found = sorted(((sc, el, ln) for ln in halfway_candidates(paint) for sc, el in circles_on_line(paint, ln)),
                   key=lambda c: -c[0])[:circles]
    best = None
    for score, el, line in found:
        if log:
            log('circle at (%.0f, %.0f) a %.0f px aspect %.3f, score %.1f' % (el[:4] + (score,)))
        got = _pose_from_circle(pano, el, line, L, W0, log)
        if got and (best is None or got[3]['explained'] > best[3]['explained']):
            best = got
    return best


def _pose_from_circle(pano, el, line, L, W0, log):
    cx, cy, a, asp, _ = el

    def pdir(x, y):
        az, up = (x - pano.cx) / pano.F, (y - pano.cy) / pano.F
        return np.array([np.sin(az) * np.cos(up), np.sin(up), np.cos(az) * np.cos(up)])
    dist = pano.F / a                                  # radius 1 R spans a px
    dep = np.arcsin(np.clip(asp, 0.02, 0.95))
    h, back = dist * np.sin(dep), dist * np.cos(dep)
    x0, y0, x1, y1 = line
    a_c = pdir(cx, cy)
    b_c = pdir(*(np.array([cx, cy]) + 80 * np.array([x1 - x0, y1 - y0]) / np.hypot(x1 - x0, y1 - y0)))
    pm = PitchModel(L=L, W=W0)
    best = None
    n_c = np.cross(b_c, a_c)                           # image x (right) <-> world u, camera behind the near touchline
    for du in (0.0, -1.5, 1.5):
        C = np.array([L / 2 + du, W0 / 2 - np.sqrt(max(back ** 2 - du ** 2, 0.25)), h])
        aim = np.array([L / 2, W0 / 2, 0]) - C
        R0 = _kabsch(np.array([aim / np.linalg.norm(aim), [1, 0, 0]]), np.array([a_c, n_c / np.linalg.norm(n_c)]))
        R0 = best_roll(pano, C, R0, pm)
        C2, R2, pm2, _ = refine_pano(pano, C, R0, pm, f_scale=25.0)
        C2, R2, pm2, sol = refine_pano(pano, C2, R2, pm2, f_scale=3.0)
        e, tot = explained_paint(pano, C2, R2, pm2)
        plausible = 5.0 <= pm2.W <= 10.0 and 1.3 <= pm2.L / pm2.W <= 1.9 and C2[1] < 0 < C2[2]
        if log:
            log('start %s -> C %s W %.2f cost %.0f explained %d/%d%s'
                % (np.round(C, 2), np.round(C2, 3), pm2.W, sol.cost, e, tot, '' if plausible else ' (refused)'))
        if plausible and (best is None or e > best[3]['explained']):
            best = (C2, R2, pm2, dict(explained=e, paint=tot, cost=float(sol.cost), circle=(cx, cy, a, asp),
                                      line=line))
    return best


def aim_from_circle(pano: Panorama, el, line, C, pitch: PitchModel):
    """Sample 0's rotation from a known camera position: the circle's centre is the
    pitch's centre, the halfway line runs across the pitch."""
    cx, cy = el[:2]

    def pdir(x, y):
        az, up = (x - pano.cx) / pano.F, (y - pano.cy) / pano.F
        return np.array([np.sin(az) * np.cos(up), np.sin(up), np.cos(az) * np.cos(up)])
    x0, y0, x1, y1 = line
    a_c = pdir(cx, cy)
    b_c = pdir(*(np.array([cx, cy]) + 80 * np.array([x1 - x0, y1 - y0]) / np.hypot(x1 - x0, y1 - y0)))
    n_c = np.cross(b_c, a_c)
    aim = np.array([pitch.L / 2, pitch.W / 2, 0]) - C
    return _kabsch(np.array([aim / np.linalg.norm(aim), [1, 0, 0]]), np.array([a_c, n_c / np.linalg.norm(n_c)]))


def best_roll(pano: Panorama, C, R0, pitch: PitchModel, span: float = 24.0, step: float = 2.0):
    """Turn about the line of sight to the centre spot: the halfway line's direction
    near the circle is a few degrees out, and a start that far off settles in the
    wrong place. Keeps the turn that explains the most paint."""
    aim = np.array([pitch.L / 2, pitch.W / 2, 0]) - C
    aim /= np.linalg.norm(aim)
    turns = [R0 @ rodrigues((np.radians(r) * aim)[None])[0] for r in np.arange(-span, span + step / 2, step)]
    return max(turns, key=lambda R: explained_paint(pano, C, R, pitch)[0])


def calibrate_rotation(pano: Panorama, C, pitch: PitchModel, thresh: float = 0.25, circles: int = 6, log=None):
    """The camera and the pitch already known (another stretch of the same match):
    only where it points. Each of the best few circles gives a start; the rotation
    alone is refined; the one explaining the most paint wins. Returns (C, R0,
    pitch, info) or None."""
    C = np.asarray(C, float)
    pano = clean_pano(pano, thresh)
    paint = pano.paint >= thresh
    found = sorted(((sc, el, ln) for ln in halfway_candidates(paint) for sc, el in circles_on_line(paint, ln)),
                   key=lambda c: -c[0])[:circles]
    best = None
    for score, el, line in found:
        R0 = best_roll(pano, C, aim_from_circle(pano, el, line, C, pitch), pitch)
        _, R2, _, _ = refine_pano(pano, C, R0, pitch, f_scale=25.0, fix_C=True, free_W=False)
        _, R2, _, sol = refine_pano(pano, C, R2, pitch, f_scale=3.0, fix_C=True, free_W=False)
        e, tot = explained_paint(pano, C, R2, pitch)
        if log:
            log('circle at (%.0f, %.0f) a %.0f px aspect %.3f, score %.1f -> cost %.0f explained %d/%d'
                % (el[:4] + (score, sol.cost, e, tot)))
        if best is None or e > best[3]['explained']:
            best = (C, R2, pitch, dict(explained=e, paint=tot, cost=float(sol.cost), circle=tuple(el[:4]), line=line))
    return best


# ---- camera height from the players: the paint hardly constrains it ---------------------

FAR_NEAR = 0.988   # far-quartile / near-quartile median player height at the true camera height (fm01 0.564, fr00 0.578)


def far_near_ratio(R_chain, f, C, R0, pitch: PitchModel, boxes: dict):
    """Median standing height of detected people in the farthest quarter of the
    pitch over that of the nearest quarter, and the overall median in R. Guess the
    camera too low and far people come out too tall; too high, too short. `boxes`: sample index ->
    (n, 4) full-resolution x0 y0 x1 y1, whole people only (not cut by the frame)."""
    out = []
    for k, b in boxes.items():
        if not len(b):
            continue
        Rk = R_chain[k] @ R0
        cx = (b[:, 0] + b[:, 2]) / 2
        df = rays(np.c_[cx, b[:, 3]], f[k]) @ Rk
        dh = rays(np.c_[cx, b[:, 1]], f[k]) @ Rk
        ok = df[:, 2] < 0
        G = C[:2] + (-C[2] / np.where(ok, df[:, 2], -1))[:, None] * df[:, :2]
        ok &= (G[:, 0] > 0) & (G[:, 0] < pitch.L) & (G[:, 1] > 0) & (G[:, 1] < pitch.W)
        D = np.hypot(*(G - C[:2]).T)
        out.append(np.c_[C[2] + D / np.hypot(dh[:, 0], dh[:, 1]) * dh[:, 2], D][ok])
    o = np.concatenate(out)
    q1, q3 = np.percentile(o[:, 1], [25, 75])
    return np.median(o[o[:, 1] >= q3, 0]) / np.median(o[o[:, 1] <= q1, 0]), float(np.median(o[:, 0]))


def height_from_people(pano: Panorama, R_chain, f, C, R0, pitch: PitchModel, boxes: dict, target: float = FAR_NEAR,
                       step: float = 0.03, n: int = 9, log=None):
    """Re-fit the pose at a ladder of fixed camera heights around the current one
    (each from the last good answer), and keep the height where the players' far /
    near size ratio crosses `target`, interpolated. Returns (C, R0, pitch, rows),
    the pose re-fitted at that height, or the input pose if the ratio never crosses."""
    pano = clean_pano(pano)
    C = np.asarray(C, float)
    rows = []
    h0 = round(C[2] / step) * step
    for direction in (-1, 1):
        Cw, Rw, Ww = C, R0, pitch.W
        for i in range(0 if direction < 0 else 1, n):
            h = h0 + direction * step * i
            if h <= 0.1:
                break
            C2, R2, pm, sol = refine_pano(pano, np.r_[Cw[:2], h], Rw, PitchModel(L=pitch.L, W=Ww), fix_h=h)
            ok = 5.0 <= pm.W <= 10.0 and C2[1] < 0
            ratio, med = far_near_ratio(R_chain, f, C2, R2, pm, boxes)
            if log:
                log('h %.2f  C %s W %.2f cost %.0f  far/near %.3f median %.2f R%s'
                    % (h, np.round(C2, 2), pm.W, sol.cost, ratio, med, '' if ok else '  (refused)'))
            if not ok:
                break
            Cw, Rw, Ww = C2, R2, pm.W
            rows.append((h, ratio, C2, R2, pm))
    rows.sort(key=lambda r: r[0])
    for a, b in zip(rows, rows[1:]):
        if (a[1] - target) * (b[1] - target) <= 0 and a[1] != b[1]:
            h = a[0] + (a[1] - target) / (a[1] - b[1]) * (b[0] - a[0])
            near = a if abs(h - a[0]) < abs(h - b[0]) else b
            C2, R2, pm, _ = refine_pano(pano, np.r_[near[2][:2], h], near[3], near[4], fix_h=h)
            return C2, R2, pm, [r[:2] for r in rows]
    return C, R0, pitch, [r[:2] for r in rows]
