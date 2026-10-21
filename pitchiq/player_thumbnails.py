"""A recognisable picture of each tracked figure, cut while the pixels exist.

The review tool asks a coach to map tracked figures to players, and that
question is visual. So `consider` scores each sighting (`crop_score`) inside the
decode loop, where the image still exists, and keeps the best crop per track;
`cut` and `encode` make it, `attach_thumbs` puts them on the report and
`fit_budget` keeps the total small.

Three rules, each about not showing something worse than nothing: never upscale
(a tiny picture is the truth about that figure); nothing clipped by the frame
edge; nothing wider than it is tall (two merged players, or someone on the
ground). A track never cleanly seen keeps its colour swatch.

Design notes, measurements and history: `docs/design/player_thumbnails.md`.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

import numpy as np

# The tallest a stored crop may be. Not a target — a ceiling, and one that is
# rarely reached: on the tight framing this pipeline needs, a player at 1080p is
# somewhere around 60-120px tall, so most thumbnails are stored at native size.
MAX_THUMB_H = 128

# Below this there is no face, no number and no hair — nothing a person could
# match to a name. A crop this small is a smudge, and a smudge in the picker
# invites a guess.
MIN_THUMB_H = 24

# Width over height. A standing person seen from the side of a pitch is roughly
# 0.35; 0.85 leaves room for a wide stride or an outstretched arm and still
# rejects the two-players-in-one-box case that would otherwise sail through as
# the largest box of the match.
MAX_ASPECT = 0.85

# How close to the frame edge counts as touching it. One pixel of slack, because
# a detector box routinely lands exactly on the boundary.
EDGE_MARGIN_PX = 2.0

# Encoding quality. These are 40x100px crops of grass and a shirt; past about 70
# the bytes go up and nothing a human can see does.
JPEG_QUALITY = 72

# What every thumbnail on one match may cost, in bytes of base64. A Firestore
# document stops at 1,048,576 bytes and the identity document also carries a
# heatmap per cluster, so this leaves well over half the budget alone. Clusters
# are served biggest-first, because the figure a coach will actually try to name
# is the one that was on screen for forty minutes.
THUMB_BUDGET_BYTES = 400_000


@dataclass
class Thumb:
    """One track's picture, and what it was worth taking."""

    track_id: int
    frame_index: int
    height_px: float
    data_uri: str

    def __len__(self) -> int:
        return len(self.data_uri)


def crop_score(
    box: tuple[float, float, float, float],
    frame_w: int,
    frame_h: int,
) -> float | None:
    """How good a portrait this box would make, or None if it would make none.

    The score is simply the box's height in pixels, which is the only honest
    proxy available: more pixels of a person is more of a person. Detector
    confidence is deliberately not in it — a confident box around a distant
    smudge is still a smudge, and the two rank differently.
    """
    x1, y1, x2, y2 = (float(v) for v in box)
    width, height = x2 - x1, y2 - y1

    if height < MIN_THUMB_H or width <= 0:
        return None
    if width / height > MAX_ASPECT:
        return None
    if (
        x1 <= EDGE_MARGIN_PX
        or y1 <= EDGE_MARGIN_PX
        or x2 >= frame_w - EDGE_MARGIN_PX
        or y2 >= frame_h - EDGE_MARGIN_PX
    ):
        return None
    return height


def cut(
    frame: np.ndarray,
    box: tuple[float, float, float, float],
    max_height: int = MAX_THUMB_H,
) -> np.ndarray | None:
    """The pixels inside `box`, shrunk to `max_height` but never stretched to it."""
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    x1, y1 = max(0, x1), max(0, y1)
    x2 = min(frame.shape[1], x2)
    y2 = min(frame.shape[0], y2)
    if x2 - x1 < 1 or y2 - y1 < 1:
        return None

    patch = frame[y1:y2, x1:x2]
    height = patch.shape[0]
    if height <= max_height:
        return patch

    import cv2

    scale = max_height / height
    return cv2.resize(
        patch,
        (max(1, int(round(patch.shape[1] * scale))), max_height),
        # AREA is the right filter for shrinking specifically: it averages the
        # pixels being discarded instead of sampling one of them, which is what
        # keeps a thin shirt stripe from disappearing between two rows.
        interpolation=cv2.INTER_AREA,
    )


def encode(patch: np.ndarray) -> str | None:
    """A BGR patch as a `data:` URI, or None if it could not be encoded.

    Returned ready to drop into an `<img src>`. The alternative — raw bytes plus
    a content type, assembled at the far end — would mean the browser, the
    fixture and the publish path each having their own opinion about the prefix.
    """
    import cv2

    ok, buffer = cv2.imencode(
        '.jpg',
        patch,
        [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
    )
    if not ok:
        return None
    encoded = base64.b64encode(buffer.tobytes()).decode('ascii')
    return f'data:image/jpeg;base64,{encoded}'


def consider(
    held: Thumb | None,
    frame: np.ndarray,
    track_id: int,
    frame_index: int,
    box: tuple[float, float, float, float],
) -> Thumb | None:
    """The better of `held` and this sighting, or `held` unchanged.

    The score is computed before anything is encoded, because encoding is the
    expensive half and almost every sighting loses. Over a match a track's best
    improves a handful of times and then stops.
    """
    score = crop_score(box, frame.shape[1], frame.shape[0])
    if score is None or (held is not None and score <= held.height_px):
        return held

    patch = cut(frame, box)
    if patch is None:
        return held
    data_uri = encode(patch)
    if data_uri is None:
        return held

    return Thumb(
        track_id=track_id,
        frame_index=frame_index,
        height_px=score,
        data_uri=data_uri,
    )


def attach_thumbs(clusters, thumbs: dict[int, Thumb]) -> None:
    """Give each cluster the best picture among the tracks it was built from.

    Best rather than earliest or longest: a cluster is several fragments of one
    person, and the only thing that makes one fragment's crop better than
    another's is how much of that person it caught.
    """
    for cluster in clusters:
        best: Thumb | None = None
        for track_id in cluster.track_ids:
            candidate = thumbs.get(track_id)
            if candidate is None:
                continue
            if best is None or candidate.height_px > best.height_px:
                best = candidate
        cluster.thumb = best.data_uri if best else None
        cluster.thumb_height_px = round(best.height_px, 1) if best else None


def fit_budget(
    clusters,
    budget_bytes: int = THUMB_BUDGET_BYTES,
) -> tuple[int, int]:
    """Drop thumbnails, smallest cluster first, until the rest fit the budget.

    Returns `(kept, dropped)`. A document that is one byte over the limit does
    not publish at all — not the thumbnails, not the heatmaps, not the clusters
    — so this trades the pictures nobody was going to use for the run reaching
    the coach at all. Order is by sightings, because the figure that was on
    screen for forty minutes is the one somebody will try to name.
    """
    ordered = sorted(clusters, key=lambda c: c.sightings, reverse=True)

    total = 0
    kept = dropped = 0
    for cluster in ordered:
        thumb = getattr(cluster, 'thumb', None)
        if not thumb:
            continue
        if total + len(thumb) > budget_bytes:
            cluster.thumb = None
            cluster.thumb_height_px = None
            dropped += 1
            continue
        total += len(thumb)
        kept += 1
    return kept, dropped
