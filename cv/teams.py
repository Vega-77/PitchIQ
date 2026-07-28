"""Tell the two teams apart by shirt colour.

Works entirely in pixel space, so unlike anything in `metrics.py` this does not
need a calibration — which matters, because on the footage available the camera
pans and zooms and no fixed homography exists.

Three decisions carry most of the accuracy:

* **Sample the torso, not the box.** A detection box is mostly grass at the
  edges, hair at the top and socks at the bottom. The middle band is shirt.
* **Cluster in a colour space that separates hue from brightness.** Shadowed
  red and sunlit red are far apart in RGB and close in Lab, and half the pitch
  is usually in shadow.
* **Decide per track, not per frame.** A single frame can be blurred, occluded
  or half in shadow; a track offers dozens of samples and a majority vote over
  them is dramatically steadier than any one of them.

Goalkeepers and referees wear neither shirt, so they are deliberately left
unassigned rather than forced into whichever team they resemble least.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import cv2
import numpy as np

# Fraction of the detection box treated as torso. Starts below the head and
# stops above the shorts.
TORSO_TOP = 0.22
TORSO_BOTTOM = 0.55
TORSO_INSET = 0.22          # trim the sides, which are mostly background

# How far from its own cluster centre a sample may sit before it is called
# unknown, as a fraction of the gap between the two centres. Relative rather
# than absolute because chroma distances vary hugely between a red-versus-yellow
# fixture and two shades of blue.
OUTLIER_RATIO = 0.55

TEAM_A = "team_a"
TEAM_B = "team_b"
UNKNOWN = "unknown"


@dataclass
class TeamAssignment:
    """Which team each track belongs to, and how sure we are."""

    by_track: dict[int, str] = field(default_factory=dict)
    confidence: dict[int, float] = field(default_factory=dict)
    centres: dict[str, np.ndarray] = field(default_factory=dict)

    def team_of(self, track_id: int) -> str:
        return self.by_track.get(track_id, UNKNOWN)

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(self.by_track.values()))

    def swap(self) -> None:
        """Exchange the two labels.

        Clustering has no idea which side is 'us'; that is a human fact. This
        exists so a caller can correct the mapping without refitting.
        """
        flip = {TEAM_A: TEAM_B, TEAM_B: TEAM_A}
        self.by_track = {k: flip.get(v, v) for k, v in self.by_track.items()}
        if TEAM_A in self.centres and TEAM_B in self.centres:
            self.centres[TEAM_A], self.centres[TEAM_B] = (
                self.centres[TEAM_B], self.centres[TEAM_A]
            )


def torso_patch(frame: np.ndarray, xyxy) -> np.ndarray | None:
    """Crop the shirt region out of a detection box."""
    x1, y1, x2, y2 = (float(v) for v in xyxy)
    height = y2 - y1
    width = x2 - x1
    if height < 6 or width < 3:
        return None                      # too small to carry colour information

    top = int(round(y1 + height * TORSO_TOP))
    bottom = int(round(y1 + height * TORSO_BOTTOM))
    left = int(round(x1 + width * TORSO_INSET))
    right = int(round(x2 - width * TORSO_INSET))

    h, w = frame.shape[:2]
    top, bottom = max(0, top), min(h, bottom)
    left, right = max(0, left), min(w, right)
    if bottom - top < 2 or right - left < 2:
        return None

    patch = frame[top:bottom, left:right]
    return patch if patch.size else None


def shirt_colour(frame: np.ndarray, xyxy) -> np.ndarray | None:
    """Representative shirt colour for one detection, in Lab.

    Uses the median rather than the mean: a mean is dragged around by the few
    grass or skin pixels that survive the crop, whereas the median ignores them.
    """
    patch = torso_patch(frame, xyxy)
    if patch is None:
        return None

    lab = cv2.cvtColor(patch, cv2.COLOR_BGR2Lab)
    return np.median(lab.reshape(-1, 3), axis=0).astype(np.float64)


def _kmeans_two(samples: np.ndarray, iterations: int = 25) -> tuple[np.ndarray, np.ndarray]:
    """Two-cluster k-means, seeded by the most separated pair of samples.

    Random seeding occasionally converges to a split along brightness (sunlit
    versus shadowed) rather than along kit colour. Starting from the two most
    distant samples makes that far less likely.
    """
    if len(samples) < 2:
        raise ValueError("need at least two samples")

    # Farthest-first seeding.
    first = samples[0]
    distances = np.linalg.norm(samples - first, axis=1)
    a = samples[int(np.argmax(distances))]
    distances = np.linalg.norm(samples - a, axis=1)
    b = samples[int(np.argmax(distances))]

    centres = np.vstack([a, b]).astype(np.float64)

    for _ in range(iterations):
        d = np.linalg.norm(samples[:, None, :] - centres[None, :, :], axis=2)
        labels = np.argmin(d, axis=1)

        moved = False
        for k in (0, 1):
            members = samples[labels == k]
            if len(members) == 0:
                continue
            new_centre = members.mean(axis=0)
            if not np.allclose(new_centre, centres[k]):
                centres[k] = new_centre
                moved = True
        if not moved:
            break

    d = np.linalg.norm(samples[:, None, :] - centres[None, :, :], axis=2)
    return centres, np.argmin(d, axis=1)


def assign_teams(
    samples_by_track: dict[int, list[np.ndarray]],
    outlier_ratio: float = OUTLIER_RATIO,
) -> TeamAssignment:
    """Split tracks into two teams from their shirt-colour samples.

    Clusters on the **chroma channels only** — Lab's a and b — and discards
    lightness. This is the single most important detail here. Half a pitch is
    usually in shadow, and lightness varies far more between a sunlit and a
    shaded player in the same shirt than it does between the two kits, so
    including L makes k-means split the frame into "sunny" and "shady" rather
    than into two teams. That failure is not subtle when it happens: both
    cluster centres come back grey.

    Each track is labelled by where its own median colour falls, which is what
    makes the result robust to any single blurred or occluded frame.
    """
    result = TeamAssignment()

    per_track = {
        track_id: np.median(np.vstack(colours), axis=0)
        for track_id, colours in samples_by_track.items()
        if colours
    }
    if len(per_track) < 2:
        return result

    track_ids = list(per_track)
    full = np.vstack([per_track[t] for t in track_ids])
    chroma = full[:, 1:3]                    # drop L, keep a and b

    centres_ab, labels = _kmeans_two(chroma)

    # Keep full-Lab centres for display, built from the members of each cluster
    # so the reported colour is a real shirt rather than a chroma-only stub.
    result.centres = {}
    for name, k in ((TEAM_A, 0), (TEAM_B, 1)):
        members = full[labels == k]
        result.centres[name] = (
            members.mean(axis=0) if len(members) else np.array([0.0, *centres_ab[k]])
        )

    gap = float(np.linalg.norm(centres_ab[0] - centres_ab[1]))
    cutoff = gap * outlier_ratio

    names = [TEAM_A, TEAM_B]
    for track_id, colour, label in zip(track_ids, chroma, labels):
        distances = np.linalg.norm(centres_ab - colour, axis=1)
        nearest = float(distances[label])
        other = float(distances[1 - label])

        if gap > 0 and nearest > cutoff:
            # Matches neither kit closely — most likely a keeper or an official.
            result.by_track[track_id] = UNKNOWN
            result.confidence[track_id] = 0.0
            continue

        result.by_track[track_id] = names[label]
        total = nearest + other
        result.confidence[track_id] = float((other - nearest) / total) if total else 0.0

    return result


def collect_samples(frames_with_boxes) -> dict[int, list[np.ndarray]]:
    """Gather shirt colours per track.

    `frames_with_boxes` yields (frame, [(track_id, xyxy), ...]).
    """
    samples: dict[int, list[np.ndarray]] = {}
    for frame, boxes in frames_with_boxes:
        for track_id, xyxy in boxes:
            colour = shirt_colour(frame, xyxy)
            if colour is not None:
                samples.setdefault(track_id, []).append(colour)
    return samples


def separation(assignment: TeamAssignment) -> float:
    """Chroma distance between the two kit colours.

    Measured on a and b only, matching how the split was made — a lightness
    difference between kits does not help tell them apart under mixed sun and
    shade. A small number means the kits look alike to the camera, and every
    team statistic derived from them deserves suspicion. Better to surface that
    than to discover it later through nonsensical possession figures.
    """
    if TEAM_A not in assignment.centres or TEAM_B not in assignment.centres:
        return 0.0
    return float(np.linalg.norm(
        assignment.centres[TEAM_A][1:3] - assignment.centres[TEAM_B][1:3]
    ))
