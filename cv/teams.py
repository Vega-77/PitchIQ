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
* **Pick the axis within that space by measuring it, not by assuming it.**
  Chroma tells a red kit from a blue one and says nothing at all about a dark
  kit against a white one. Which of those a fixture is cannot be known in
  advance, so both are tried and the one that actually separates wins.
* **Decide per track, not per frame.** A single frame can be blurred, occluded
  or half in shadow; a track offers dozens of samples and a majority vote over
  them is dramatically steadier than any one of them.

Goalkeepers and referees wear neither shirt, so they are deliberately left
unassigned rather than forced into whichever team they resemble least.
"""

from __future__ import annotations

import warnings
from collections import Counter
from dataclasses import dataclass, field

import cv2
import numpy as np

# Fraction of the detection box treated as torso. Starts below the head and
# stops above the shorts.
TORSO_TOP = 0.22
TORSO_BOTTOM = 0.55
TORSO_INSET = 0.22          # trim the sides, which are mostly background

# How wide a strip of turf to read either side of a detection for the grass
# reference, as a fraction of the box width. Wide enough to clear the player's
# own outline and the blur around it, narrow enough to still be grass rather
# than whoever is standing next to them.
SURROUND_WIDTH = 0.75

# How far from its own cluster centre a sample may sit before it is called
# unknown, as a fraction of the gap between the two centres. Relative rather
# than absolute because chroma distances vary hugely between a red-versus-yellow
# fixture and two shades of blue.
OUTLIER_RATIO = 0.55

# The narrowest a cluster is allowed to be counted as, in Lab units. Every
# number here comes from 8-bit pixels through a median, so a cluster measured
# tighter than one unit is not tighter -- it is at the quantisation floor, and
# dividing by its measured width would report a ratio the evidence cannot
# support. Reaching this floor at all takes samples that agree exactly, which
# happens in synthetic frames and not on grass.
MIN_CLUSTER_WIDTH = 1.0

# How much better than chroma another axis has to score before it takes the
# split. Not a tie-break: the two axes are not equally trustworthy. Chroma is
# what survives a cloud crossing the sun, and grass-relative lightness only
# approximately survives it -- the grass reference assumes shadow dims shirt
# and turf by the same factor, which is close to true and not true. So chroma
# holds the split unless the evidence against it is plain.
#
# The two measured cases sit either side of this. Red against yellow, where
# chroma works perfectly well, scored 1.25x on lightness -- a real margin, and
# not a reason to give up the safer axis. The dark kit against the white one,
# where chroma is blind, scored 2.3x. Neither is near 1.5.
AXIS_MARGIN = 1.5

# What fraction of tracks must actually carry an axis before it may be used.
# Not every track has every reading: a detection at the very edge of frame has
# no turf beside it to read, so it has no grass-relative lightness. Those
# tracks end up UNKNOWN on that axis, which is what UNKNOWN is for.
#
# This was one track for all, and it silently cost the axis its first run on
# real footage: across 477 fragmented tracks at least one had no reading, so
# "all finite" was never true and the axis was never offered. What the rule is
# actually protecting is the fit -- centres drawn from a fifth of the pitch are
# not the pitch -- and that wants a fraction, not unanimity.
MIN_AXIS_COVERAGE = 0.8

# The smallest share of tracks the lesser cluster may hold and still be called
# a team. Separability alone does not ask this, and on real footage that let it
# be gamed: chroma scored 2.19 by setting 81 tracks against 608, a tight clump
# at a 159 b 144 -- the pitch is multi-use turf with orange and yellow lines,
# and that clump is line paint and skin caught in torso boxes, not eleven
# players. Grass-relative lightness scored 1.87 splitting 281 against 408 with
# its centres 100 apart, which is a dark kit and a white one. The lower number
# was the right answer, so the number was not asking enough.
#
# Two teams are eleven and eleven. Fragmentation, substitutes and a keeper in a
# third colour move that around, and one team being nearer the camera moves it
# further, so the bar is set low enough to survive all of it and high enough
# that no clump of outliers clears it.
MIN_CLUSTER_SHARE = 0.25

TEAM_A = "team_a"
TEAM_B = "team_b"
UNKNOWN = "unknown"


@dataclass
class TeamAssignment:
    """Which team each track belongs to, and how sure we are."""

    by_track: dict[int, str] = field(default_factory=dict)
    confidence: dict[int, float] = field(default_factory=dict)
    centres: dict[str, np.ndarray] = field(default_factory=dict)

    # Which feature the split was actually made on, and how well it separated:
    # the gap between the two centres over their combined width, so it is a
    # ratio and means the same thing whichever axis won. Carried because a
    # possession figure derived from a score near zero is two arbitrary halves
    # of one cloud, and a reader deserves to be able to see that.
    axis: str = 'chroma'
    score: float = 0.0
    # The raw gap along that axis, kept because `separation` cannot recompute
    # it from `centres` once the axis is no longer necessarily chroma.
    gap: float | None = None

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


def _nanmedian(rows: np.ndarray) -> np.ndarray:
    """Column medians ignoring NaN, and quiet about a column that is all NaN.

    numpy warns on an all-NaN slice and returns NaN, which is the answer this
    wants — a track that never got a grass reading has no value on that axis,
    and `_candidate_axes` reads the NaN and declines the axis. The warning
    would be the only thing here that was not deliberate.
    """
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        return np.nanmedian(rows, axis=0)


def _nanmean(rows: np.ndarray) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        return np.nanmean(rows, axis=0)


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


def surround_lightness(frame: np.ndarray, xyxy) -> float | None:
    """Median lightness of the turf flanking a detection, at torso height.

    Left and right of the box rather than above or below, and the choice is not
    arbitrary: above a player is as often the far stand, the tree line or
    somebody else entirely, and below is the player's own shadow — the one
    patch of ground guaranteed to misrepresent the light falling on the shirt.
    """
    x1, y1, x2, y2 = (float(v) for v in xyxy)
    height = y2 - y1
    width = x2 - x1
    if height < 6 or width < 3:
        return None

    h, w = frame.shape[:2]
    top = max(0, int(round(y1 + height * TORSO_TOP)))
    bottom = min(h, int(round(y1 + height * TORSO_BOTTOM)))
    if bottom - top < 2:
        return None

    band = max(1, int(round(width * SURROUND_WIDTH)))
    inner_left = min(w, max(0, int(round(x1))))
    inner_right = min(w, max(0, int(round(x2))))

    strips = [
        frame[top:bottom, max(0, inner_left - band):inner_left],
        frame[top:bottom, inner_right:min(w, inner_right + band)],
    ]
    turf = [s.reshape(-1, 3) for s in strips if s.size]
    if not turf:
        return None                      # a box spanning the whole frame width

    stacked = np.vstack(turf).reshape(-1, 1, 3)
    lab = cv2.cvtColor(stacked, cv2.COLOR_BGR2Lab)
    return float(np.median(lab.reshape(-1, 3)[:, 0]))


def kit_sample(frame: np.ndarray, xyxy) -> np.ndarray | None:
    """One detection's colour evidence: Lab, plus lightness against the grass.

    The fourth number is what separates a dark kit from a white one without
    reintroducing the failure that made this module throw raw lightness away.
    A shadow darkens the shirt and the grass beside it together, so the
    *difference* between them survives what the absolute value does not.

    NaN in that slot when there was no readable turf either side. Callers pass
    the array on unchanged; `assign_teams` treats the axis as unavailable
    rather than guessing at it.
    """
    colour = shirt_colour(frame, xyxy)
    if colour is None:
        return None

    grass = surround_lightness(frame, xyxy)
    relative = float(colour[0] - grass) if grass is not None else float('nan')
    return np.array([colour[0], colour[1], colour[2], relative], dtype=np.float64)


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


def _separability(feature: np.ndarray, centres: np.ndarray, labels: np.ndarray) -> float:
    """How convincingly a split separates: centre gap over cluster width.

    A gap on its own says nothing — a and b were reported 34 apart on footage
    where the split was cutting a single grey cloud down the middle, which is a
    large number and a meaningless one. Dividing by how wide the two clusters
    themselves are asks the question that matters, and being a ratio of
    distances in one space it is scale-free, so two candidate axes can be
    compared directly.

    One caveat, stated because it is a real thumb on the scale: a
    one-dimensional axis has no room to spread sideways and so scores a little
    higher than a two-dimensional one would on the same evidence. It is worth
    less than the margins actually seen between candidates, but it is not zero.

    Clusters that are tighter than `MIN_CLUSTER_WIDTH` are counted as that wide.
    Without the floor a perfect split divides by zero and was scored zero --
    the worst score available -- which is exactly backwards.
    """
    gap = float(np.linalg.norm(centres[0] - centres[1]))
    spread = 0.0
    for k in (0, 1):
        members = feature[labels == k]
        if len(members) == 0:
            return 0.0                   # one cluster took everything
        spread += float(np.mean(np.linalg.norm(members - centres[k], axis=1)))
    return gap / max(spread, MIN_CLUSTER_WIDTH)


def _balance(labels: np.ndarray) -> float:
    """The lesser cluster's share of the tracks, 0 to 0.5."""
    if len(labels) == 0:
        return 0.0
    smaller = min(int((labels == 0).sum()), int((labels == 1).sum()))
    return smaller / len(labels)


def _coverage(feature: np.ndarray) -> float:
    """Fraction of tracks that have a value on this axis at all."""
    if len(feature) == 0:
        return 0.0
    return float(np.mean(np.all(np.isfinite(feature), axis=1)))


def _candidate_axes(full: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """The feature spaces worth trying, chroma first so it wins any tie.

    Chroma leads because it is right for most fixtures and is what every
    threshold in this file was set against.

    Raw lightness is deliberately **not** a candidate. It would win outright on
    a dark-versus-white fixture — and it would also win on a half-shadowed
    pitch where both teams wear the same colour, splitting the frame into sunny
    and shady. Those two cases look identical from in here, which is exactly
    why this module dropped L to begin with. The grass-relative column is the
    half of that signal a shadow cannot fake.
    """
    axes = [('chroma', full[:, 1:3])]

    if full.shape[1] > 3:
        relative = full[:, 3:4]
        if _coverage(relative) >= MIN_AXIS_COVERAGE:
            axes.append(('grass-relative lightness', relative))

    return axes


def assign_teams(
    samples_by_track: dict[int, list[np.ndarray]],
    outlier_ratio: float = OUTLIER_RATIO,
) -> TeamAssignment:
    """Split tracks into two teams from their shirt-colour samples.

    Accepts three-number Lab samples or the four-number ones `kit_sample`
    produces, and clusters on whichever candidate axis measurably separates
    this fixture best — see `_candidate_axes` for what is offered and, more
    importantly, for what is refused.

    Raw lightness stays out of it. Half a pitch is usually in shadow, and
    lightness varies far more between a sunlit and a shaded player in the same
    shirt than it does between two kits, so clustering on L splits the frame
    into "sunny" and "shady". Chroma is immune to that and was the whole answer
    here for a long time — until footage of a dark kit against a white one,
    where the two shirts differ by 119 in lightness and 15 in chroma, and the a
    channel duly split the pitch at 128, which is to say it cut neutral grey in
    half and called the halves teams. Possession came back 0% to 100%. Chroma
    was not wrong; it was being asked a question it has no information about.

    Each track is labelled by where its own median colour falls, which is what
    makes the result robust to any single blurred or occluded frame.
    """
    result = TeamAssignment()

    # nanmedian, because a detection at the frame edge can be missing its
    # grass reference while its Lab is perfectly good.
    per_track = {
        track_id: _nanmedian(np.vstack(colours))
        for track_id, colours in samples_by_track.items()
        if colours
    }
    if len(per_track) < 2:
        return result

    track_ids = list(per_track)
    full = np.vstack([per_track[t] for t in track_ids])

    # Chroma first, and it stays unless something clears AXIS_MARGIN. Each axis
    # is fitted on the tracks that have a value for it; the rest are labelled
    # UNKNOWN below rather than clustered on a number they do not have.
    best = None
    for name, candidate in _candidate_axes(full):
        known = np.all(np.isfinite(candidate), axis=1)
        if known.sum() < 2:
            continue
        centres, labels = _kmeans_two(candidate[known])
        score = _separability(candidate[known], centres, labels)
        even = _balance(labels) >= MIN_CLUSTER_SHARE
        if best is None:
            best = (score, even, name, candidate, known, centres, labels)
            continue
        if even != best[1]:
            # A partition of the pitch beats a clump set aside from it however
            # tight the clump scores, and losing the balance is not something a
            # good score can buy back.
            if even:
                best = (score, even, name, candidate, known, centres, labels)
            continue
        if score > best[0] * AXIS_MARGIN:
            best = (score, even, name, candidate, known, centres, labels)

    if best is None:
        return result

    score, _even, axis, feature, known, centres, labels = best
    result.axis = axis
    result.score = score

    # Keep Lab centres for display, built from the members of each cluster so
    # the reported colour is a real shirt rather than a projection onto
    # whichever axis happened to win.
    result.centres = {}
    seen = full[known]
    for name, k in ((TEAM_A, 0), (TEAM_B, 1)):
        members = seen[labels == k][:, :3]
        result.centres[name] = (
            _nanmean(members) if len(members)
            else np.full(3, np.nan, dtype=np.float64)
        )

    gap = float(np.linalg.norm(centres[0] - centres[1]))
    result.gap = gap
    cutoff = gap * outlier_ratio

    names = [TEAM_A, TEAM_B]
    label_of = dict(zip(np.flatnonzero(known), labels))
    for index, track_id in enumerate(track_ids):
        if index not in label_of:
            # No reading on the axis that won, so nothing to place it by.
            result.by_track[track_id] = UNKNOWN
            result.confidence[track_id] = 0.0
            continue

        label = int(label_of[index])
        colour = feature[index]
        distances = np.linalg.norm(centres - colour, axis=1)
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


def separation(assignment: TeamAssignment) -> float:
    """Distance between the two kit colours, along the axis they were split on.

    A small number means the kits look alike to the camera, and every team
    statistic derived from them deserves suspicion. Better to surface that than
    to discover it later through nonsensical possession figures.

    Read `assignment.score` alongside it. This is a raw gap, and a raw gap can
    be large and mean nothing — that is precisely how a chroma split of one
    grey cloud reported 34 while getting possession exactly backwards. The
    score is the number that knows the difference.

    Falls back to the chroma distance between the display centres for an
    assignment built by hand rather than fitted, which is how the tests
    construct one.
    """
    if assignment.gap is not None:
        return float(assignment.gap)
    if TEAM_A not in assignment.centres or TEAM_B not in assignment.centres:
        return 0.0
    return float(np.linalg.norm(
        assignment.centres[TEAM_A][1:3] - assignment.centres[TEAM_B][1:3]
    ))
