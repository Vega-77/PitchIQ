"""Map camera pixels to pitch metres.

A fixed camera looking at a flat pitch is a plane-to-plane projection, so a
single 3x3 homography converts image points to pitch coordinates. Four
correspondences are the mathematical minimum; more are better because the
extras let us measure how wrong the result is instead of assuming it is right.

The measurement matters more than it might seem. A homography always produces
*an* answer, and a badly-conditioned one still returns confident-looking
coordinates — it just puts them in the wrong place. Every calibration here
carries its own error estimate for that reason.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .distortion import DistortionModel
from .pitch import Pitch


def _is_convex_quad(points) -> bool:
    """True when four points in the given order trace a simple convex polygon.

    Consecutive edge cross-products all share a sign for a convex traversal;
    a mixed sign means the outline crosses itself.
    """
    pts = [np.asarray(p, dtype=np.float64) for p in points]
    if len(pts) != 4:
        return False

    signs = []
    for i in range(4):
        a, b, c = pts[i], pts[(i + 1) % 4], pts[(i + 2) % 4]
        u, v = b - a, c - b
        cross = float(u[0] * v[1] - u[1] * v[0])  # 2D cross; np.cross is 3D-only now
        if abs(cross) < 1e-9:
            return False  # three points collinear — degenerate
        signs.append(cross > 0)

    return all(signs) or not any(signs)


@dataclass
class Correspondence:
    """One clicked point: where a named landmark sits in the image."""

    landmark: str
    pixel: tuple[float, float]

    def to_json(self) -> dict:
        return {"landmark": self.landmark, "pixel": list(self.pixel)}

    @staticmethod
    def from_json(data: dict) -> "Correspondence":
        return Correspondence(data["landmark"], tuple(data["pixel"]))


@dataclass
class CalibrationError:
    """How far off the fit is, in metres on the pitch."""

    mean_m: float
    max_m: float
    per_landmark: dict[str, float] = field(default_factory=dict)
    # None means "nobody computed one", and `tail_m` then falls back to the
    # max. Kept optional rather than required so a hand-built error — in a
    # test, or the empty one below — stays valid and stays strict.
    p90_m: float | None = None

    @classmethod
    def from_per_landmark(cls, per: dict[str, float]) -> "CalibrationError":
        values = sorted(per.values())
        return cls(
            mean_m=float(np.mean(values)),
            max_m=float(values[-1]),
            per_landmark=per,
            p90_m=float(np.percentile(values, 90)),
        )

    @property
    def tail_m(self) -> float:
        """The bad end of the residuals: the 90th percentile, or the max.

        The single worst point is a brittle thing to judge a calibration by.
        `Calibration.fit` runs RANSAC at five points or more, so in Python one
        bad click is usually rejected outright — but the browser picker
        solves normal equations and spreads it across every point, and a coach
        clicking twelve landmarks on a rain-soaked field will produce one that
        is worse than the rest. Condemning the whole fit for it is not a
        judgement about the calibration, it is a judgement about one click.

        Below roughly eight points the 90th percentile is the max or very near
        it, so this only becomes lenient once there are enough points for one
        of them to be an outlier — which is exactly when it should.
        """
        return self.max_m if self.p90_m is None else self.p90_m

    @property
    def is_usable(self) -> bool:
        """Rough bar for player-position work.

        Half a metre mean is comfortably inside a player's own body width, so
        speed and distance survive it. Beyond ~1.5m at the tail the far side of
        the pitch is unreliable and possession calls near the touchline start
        to flip.

        Mirrors `LineFit.is_usable` — half a metre typical, a metre and a
        half at the tail — including the choice of a percentile over the
        extreme, so that the two rulers really are the same one.
        """
        return self.mean_m <= 0.5 and self.tail_m <= 1.5

    # A second, looser band. Not every number this pipeline produces needs the
    # same accuracy, and grading everything against the strictest consumer
    # throws away calibrations that would have answered most of the questions
    # a coach actually asks. A metre is a third of the width of the centre
    # circle: it cannot tell you where in the six-yard box a shot was struck,
    # and it is nowhere near enough to call a ball in or out on the touchline,
    # but it will not move a player out of the third they were standing in and
    # it barely touches distance covered over ninety minutes.
    COARSE_MEAN_M = 1.0
    COARSE_TAIL_M = 3.0

    @property
    def good_for(self) -> tuple[str, ...]:
        """What this calibration can honestly be used for, best band first.

        Empty means nothing positional — not that the fit failed, but that
        no number derived from it would survive being checked.
        """
        if self.mean_m != self.mean_m:  # NaN: nothing was measured
            return ()
        if self.is_usable:
            return ("shot position", "distance and speed", "territory")
        if self.mean_m <= self.COARSE_MEAN_M and self.tail_m <= self.COARSE_TAIL_M:
            return ("distance and speed", "territory")
        return ()

    def summary(self) -> str:
        if self.is_usable:
            verdict = "usable"
        elif self.good_for:
            verdict = "coarse: " + ", ".join(self.good_for)
        else:
            verdict = "TOO HIGH"
        tail = "" if self.p90_m is None else f", 90th {self.p90_m:.2f}m"
        return f"mean {self.mean_m:.2f}m{tail}, max {self.max_m:.2f}m ({verdict})"


class Calibration:
    """A fitted pixel -> pitch-metre transform.

    A homography preserves straight lines, so on its own it can only describe a
    lens that does too. When `lens` is present the pixels are straightened
    before `H` sees them and bent again on the way back out, and that is the
    only place a wide-angle camera's curvature is allowed to live. See
    `cv.distortion` for how the coefficient is recovered from the painted lines
    in the calibration frame, and for when it refuses to answer.
    """

    def __init__(
        self,
        homography: np.ndarray,
        pitch: Pitch,
        correspondences: list[Correspondence] | None = None,
        image_size: tuple[int, int] | None = None,
        lens: DistortionModel | None = None,
    ) -> None:
        self.H = np.asarray(homography, dtype=np.float64)
        if self.H.shape != (3, 3):
            raise ValueError(f"homography must be 3x3, got {self.H.shape}")
        self.pitch = pitch
        self.correspondences = correspondences or []
        self.image_size = image_size
        self.lens = lens
        self._H_inv = np.linalg.inv(self.H)

    # ---------------------------------------------------------------- fitting

    @classmethod
    def fit(
        cls,
        correspondences: list[Correspondence],
        pitch: Pitch,
        image_size: tuple[int, int] | None = None,
        lens: DistortionModel | None = None,
    ) -> "Calibration":
        if len(correspondences) < 4:
            raise ValueError(
                f"need at least 4 correspondences, got {len(correspondences)}"
            )

        seen = [c.landmark for c in correspondences]
        duplicates = {n for n in seen if seen.count(n) > 1}
        if duplicates:
            raise ValueError(f"landmark clicked more than once: {sorted(duplicates)}")

        src = np.array([c.pixel for c in correspondences], dtype=np.float64)
        if lens is not None:
            # Straighten the clicks before fitting. They record where the paint
            # appeared, not where a rectilinear lens would have put it, and a
            # homography handed the raw pixels will absorb as much of the
            # curvature as it can into a transform that cannot represent it.
            # The stored lens would then be correcting a matrix that had
            # already half-corrected itself, and the two would fight.
            src = lens.undistort(src)
        dst = np.array(
            [pitch.landmark(c.landmark) for c in correspondences], dtype=np.float64
        )

        if len(correspondences) == 4:
            # RANSAC needs redundancy to reject anything; with the bare minimum
            # every point is load-bearing, so fit them exactly.
            H = cv2.getPerspectiveTransform(
                src.astype(np.float32), dst.astype(np.float32)
            ).astype(np.float64)
        else:
            H, _ = cv2.findHomography(src, dst, cv2.RANSAC, ransacReprojThreshold=1.0)
            if H is None:
                raise ValueError(
                    "homography fit failed — points may be collinear or mislabelled"
                )

        return cls(H, pitch, correspondences, image_size, lens)

    # ---------------------------------------------------------------- transform

    def to_pitch(self, x_px: float, y_px: float) -> tuple[float, float]:
        """Image pixel -> pitch metres."""
        out = self.to_pitch_many([(x_px, y_px)])
        return (float(out[0][0]), float(out[0][1]))

    def to_pitch_many(self, points) -> np.ndarray:
        """Vectorised pixel -> metres for an (N, 2) array."""
        if self.lens is not None:
            points = self.lens.undistort(points)
        return self._apply(self.H, points)

    def to_pixels(self, x_m: float, y_m: float) -> tuple[float, float]:
        """Pitch metres -> image pixel. Useful for drawing overlays."""
        out = self._apply(self._H_inv, [(x_m, y_m)])
        if self.lens is not None:
            out = self.lens.distort(out)
        return (float(out[0][0]), float(out[0][1]))

    @staticmethod
    def _apply(matrix: np.ndarray, points) -> np.ndarray:
        pts = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, matrix).reshape(-1, 2)

    def ground_point_to_pitch(self, xyxy) -> tuple[float, float]:
        """Project a detection box to where the player meets the grass.

        The homography only maps the ground plane, so the bottom-centre of the
        box is the sole point on a standing person it is valid for. Using the
        box centre instead puts everyone metres up-pitch of where they are.
        """
        x1, _, x2, y2 = xyxy
        return self.to_pitch((x1 + x2) / 2, y2)

    # ---------------------------------------------------------------- error

    def error(self) -> CalibrationError:
        """Reprojection error on the points used to fit — optimistic by nature."""
        if not self.correspondences:
            return CalibrationError(float("nan"), float("nan"))

        per: dict[str, float] = {}
        for c in self.correspondences:
            predicted = np.array(self.to_pitch(*c.pixel))
            truth = np.array(self.pitch.landmark(c.landmark))
            per[c.landmark] = float(np.linalg.norm(predicted - truth))

        return CalibrationError.from_per_landmark(per)

    def holdout_error(self) -> CalibrationError | None:
        """Leave-one-out error — what the fit does on points it did not see.

        Reprojection error on the fitting points flatters itself: with exactly
        four points it is zero by construction, which says nothing. Refitting
        without each point in turn and measuring the omitted one is the honest
        number, and it is what catches a mislabelled or sloppily clicked
        landmark. Needs at least five points to be possible at all.
        """
        if len(self.correspondences) < 5:
            return None

        per: dict[str, float] = {}
        for i, held_out in enumerate(self.correspondences):
            subset = self.correspondences[:i] + self.correspondences[i + 1:]
            try:
                trial = Calibration.fit(
                    subset, self.pitch, self.image_size, self.lens
                )
            except (ValueError, np.linalg.LinAlgError):
                continue
            predicted = np.array(trial.to_pitch(*held_out.pixel))
            truth = np.array(self.pitch.landmark(held_out.landmark))
            per[held_out.landmark] = float(np.linalg.norm(predicted - truth))

        if not per:
            return None

        return CalibrationError.from_per_landmark(per)

    def worst_landmarks(self, limit: int = 3) -> list[tuple[str, float]]:
        """Most likely mis-clicks, worst first.

        Deliberately ranks by each point's own reprojection residual rather than
        by leave-one-out. The two answer different questions: hold-out is the
        better detector of "something here is wrong", but it is a poor
        *locator*, because dropping a good point lets a bad one dominate the
        refit and the innocent point ends up looking worst. A point's own
        residual points at the actual culprit.
        """
        return sorted(self.error().per_landmark.items(), key=lambda kv: -kv[1])[:limit]

    def sanity_check(self) -> list[str]:
        """Cheap checks for a fit that is wrong in a way the error metrics miss.

        Reprojection error is zero by construction on a four-point fit, even
        when the four labels are scrambled — the fit is exact, it just maps the
        pitch onto the image inside out. These checks look at the *shape* of the
        result instead of its residuals.
        """
        problems: list[str] = []

        if np.linalg.det(self.H) == 0:
            problems.append("homography is singular")
            return problems

        # Walk the pitch boundary and project it into the image. Going around
        # the touchlines in order must trace a simple convex quad on screen; a
        # mislabelled corner turns it into a self-intersecting bowtie, which is
        # exactly what scrambled clicks produce.
        corners_m = [
            self.pitch.landmark("corner_bottom_left"),
            self.pitch.landmark("corner_bottom_right"),
            self.pitch.landmark("corner_top_right"),
            self.pitch.landmark("corner_top_left"),
        ]
        corners_px = [self.to_pixels(*c) for c in corners_m]
        if not _is_convex_quad(corners_px):
            problems.append(
                "the pitch outline projects to a self-intersecting shape — "
                "landmark labels are probably swapped"
            )

        # The halfway line has to fall between the two goal lines on screen,
        # not outside them.
        left_x = np.mean([corners_px[0][0], corners_px[3][0]])
        right_x = np.mean([corners_px[1][0], corners_px[2][0]])
        mid_x = np.mean([
            self.to_pixels(*self.pitch.landmark("halfway_bottom"))[0],
            self.to_pixels(*self.pitch.landmark("halfway_top"))[0],
        ])
        if not (min(left_x, right_x) < mid_x < max(left_x, right_x)):
            problems.append("the halfway line projects outside the goal lines")

        if self.image_size:
            w, h = self.image_size
            projected = [self.to_pitch(*p) for p in [(0, 0), (w, 0), (w, h), (0, h)]]
            if not any(self.pitch.contains(x, y, margin_m=40) for x, y in projected):
                problems.append(
                    "no part of the image maps anywhere near the pitch — "
                    "landmarks are probably mislabelled"
                )

        return problems

    # ---------------------------------------------------------------- io

    def to_json(self) -> dict:
        return {
            "homography": self.H.tolist(),
            # The whole pitch, markings included. Writing only the size here
            # would undo everything upstream of it: the picker measures the
            # paint, `from_picker_export` reads it, this file is what the
            # pipeline actually loads — and a homography fitted against a 15m
            # box would come back tomorrow believing in a 16.5m one.
            "pitch": asdict(self.pitch),
            "image_size": list(self.image_size) if self.image_size else None,
            "correspondences": [c.to_json() for c in self.correspondences],
            "lens": self.lens.to_json() if self.lens else None,
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Calibration":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        pitch = Pitch.from_mapping(data["pitch"])
        size = tuple(data["image_size"]) if data.get("image_size") else None
        return cls(
            np.array(data["homography"], dtype=np.float64),
            pitch,
            [Correspondence.from_json(c) for c in data.get("correspondences", [])],
            size,
            DistortionModel.from_json(data.get("lens")),
        )

    @classmethod
    def from_picker_export(
        cls,
        path: str | Path,
        pitch: Pitch | None = None,
        lens: DistortionModel | None = None,
    ) -> "Calibration":
        """Load the JSON produced by the browser landmark picker.

        The `pitch` block carries the markings as well as the size, and both
        halves matter for the same reason. A coach who measured a 15m box in
        the picker, watched the yellow outline drop onto the paint and saved
        the file has recorded a fact about their field; loading it back under
        Laws-standard markings would silently throw that fact away and refit
        the same clicks against a pitch that does not exist — producing a
        homography wrong everywhere, from a file that looked fine.

        Every marking is optional and falls back to the Laws, so files written
        before the picker could measure paint still load unchanged.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))

        if pitch is None:
            dims = data.get("pitch") or {}
            pitch = Pitch.from_mapping(dims)

        points = [
            Correspondence(p["landmark"], (float(p["x"]), float(p["y"])))
            for p in data["points"]
        ]
        size = data.get("image_size")
        return cls.fit(points, pitch, tuple(size) if size else None, lens)
