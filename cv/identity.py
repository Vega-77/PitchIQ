"""Turning fragments back into players.

The tracker gives about a hundred track ids for a fifteen-second window of a
match with twenty-two people in it. A player who walks behind an opponent, or
leaves frame as the camera pans, comes back as somebody new. So every per-track
number this pipeline produces — touches, passes, distance covered — is a
fragment of a player rather than a player, and adding them up under a name
requires knowing which fragments belong together.

Nothing here guesses a *name*. That is the coach's job, and the point of this
module is to make it a job worth doing: mapping a hundred tracks to a roster by
hand is not something anyone will finish, whereas confirming fifteen clusters
is a couple of minutes.

    Two signals, one of them a hard constraint.

**Time overlap is disqualifying.** If two tracks are visible in the same frame
they are two different people, whatever they look like. This is the only
certainty available, and it does most of the work: it is what stops a merge
run from collapsing a whole team into one blob.

**Everything else is evidence.** Shirt colour, how close the tracks were in
space where one ended and the other began, and how long the gap was. A player
who disappears behind someone reappears a fraction of a second later, a couple
of metres away, in the same shirt. A player who reappears twenty seconds later
at the other end of the pitch might be the same person, and we should not claim
so.

    What this cannot do.

It cannot recover a player who left and returned much later — those stay
separate clusters, and the coach will map two clusters to the same player. That
is the right failure: two clusters mapped to one player still sums correctly,
whereas a wrong merge silently attributes one player's work to another and
cannot be undone downstream.

It also has no idea which cluster is which player. There is no jersey-number
reading here; at the camera distances this footage is shot from, shirt numbers
are a few pixels tall and not legible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .frames import FrameTable
from .teams import UNKNOWN

# Tracks separated by more than this in time are not merged on proximity alone —
# a player can cross a lot of ground in two seconds.
MAX_BRIDGE_S = 2.0

# How far apart the end of one track and the start of the next may be, in player
# heights, to read as the same person continuing.
MAX_BRIDGE_PH = 6.0

# Lab chroma distance beyond which two tracks are wearing different shirts.
# Deliberately looser than teams.assign_teams uses: that separates two kits,
# this asks whether two sightings are the same player, and lighting changes
# across a pitch more than kits differ.
MAX_CHROMA_DISTANCE = 30.0

# Clusters holding fewer sightings than this are noise, not people.
MIN_CLUSTER_SIGHTINGS = 20


@dataclass
class PlayerCluster:
    """A group of tracks believed to be one person."""

    cluster_id: int
    track_ids: set[int] = field(default_factory=set)
    team: str = UNKNOWN
    first_seen_s: float = 0.0
    last_seen_s: float = 0.0
    sightings: int = 0
    colour: tuple[float, float, float] | None = None
    heatmap: list[list[float]] | None = None
    # A `data:` URI cut from the footage by cv/thumbs.py, so a coach can match a
    # figure to a teenager by looking at it rather than by reading a time span.
    # None whenever the tracker never saw this person cleanly and whole, which
    # is a real answer and not a failure — the kit swatch stays either way.
    thumb: str | None = None
    thumb_height_px: float | None = None

    @property
    def minutes_tracked(self) -> float:
        return max(0.0, self.last_seen_s - self.first_seen_s) / 60.0

    def to_json(self) -> dict:
        return {
            'cluster_id': self.cluster_id,
            'track_ids': sorted(self.track_ids),
            'team': self.team,
            'first_seen_s': round(self.first_seen_s, 2),
            'last_seen_s': round(self.last_seen_s, 2),
            'minutes_tracked': round(self.minutes_tracked, 2),
            'sightings': self.sightings,
            'colour': list(self.colour) if self.colour else None,
            'heatmap': self.heatmap,
            'thumb': self.thumb,
            # How tall the crop was in the footage. Carried so the picker can
            # say "this is all there was" rather than silently drawing a 30px
            # portrait at the same size as a 120px one.
            'thumb_height_px': self.thumb_height_px,
        }


@dataclass
class TrackSpan:
    """One track's footprint in time and space."""

    track_id: int
    first_s: float
    last_s: float
    first_xy: tuple[float, float]
    last_xy: tuple[float, float]
    scale_px: float
    sightings: int
    frames: set[int]
    colour: np.ndarray | None = None

    def overlaps(self, other: 'TrackSpan') -> bool:
        """Whether both tracks were visible in the same frame.

        Frame-exact rather than interval-based. Two tracks whose time ranges
        overlap but which never appear together can still be one player seen
        either side of an occlusion; two that share even one frame cannot be.
        """
        return not self.frames.isdisjoint(other.frames)


def track_spans(table: FrameTable, colours: dict[int, list] | None = None) -> dict[int, TrackSpan]:
    """Summarise every track down to what merging needs."""
    spans: dict[int, TrackSpan] = {}

    for record in table.records:
        scale = record.scale_px
        for box in record.player_boxes():
            point = box.ground_point
            span = spans.get(box.track_id)
            if span is None:
                spans[box.track_id] = TrackSpan(
                    track_id=box.track_id,
                    first_s=record.timestamp_s,
                    last_s=record.timestamp_s,
                    first_xy=point,
                    last_xy=point,
                    scale_px=scale,
                    sightings=1,
                    frames={record.frame_index},
                )
            else:
                span.last_s = record.timestamp_s
                span.last_xy = point
                span.sightings += 1
                span.frames.add(record.frame_index)
                if scale > 0:
                    span.scale_px = scale

    for track_id, span in spans.items():
        samples = (colours or {}).get(track_id)
        if samples:
            span.colour = np.median(np.array(samples), axis=0)

    return spans


def _chroma_distance(a, b) -> float:
    """Lab distance ignoring lightness, matching teams.assign_teams.

    Lightness is dropped for the same reason it is dropped there: the sunlit
    half of a pitch and the shaded half turn one kit into two, and chroma
    survives that where brightness does not.
    """
    if a is None or b is None:
        return 0.0                    # no evidence either way, so no objection
    return float(math.dist(a[1:3], b[1:3]))


def _can_merge(a: TrackSpan, b: TrackSpan) -> bool:
    """Whether two tracks could be one player, in the order a-then-b."""
    if a.overlaps(b):
        return False                  # seen together: definitively two people

    gap_s = b.first_s - a.last_s
    if gap_s < 0 or gap_s > MAX_BRIDGE_S:
        return False

    scale = a.scale_px or b.scale_px
    if scale <= 0:
        return False
    if math.dist(a.last_xy, b.first_xy) / scale > MAX_BRIDGE_PH:
        return False

    return _chroma_distance(a.colour, b.colour) <= MAX_CHROMA_DISTANCE


def merge_tracks(
    table: FrameTable,
    colours: dict[int, list] | None = None,
    min_sightings: int = MIN_CLUSTER_SIGHTINGS,
    is_player=None,
) -> list[PlayerCluster]:
    """Group tracks into clusters that are probably one player each.

    Greedy agglomeration in time order, which suits the shape of the problem:
    fragments arrive in sequence, and the question is always "does this
    continue something?" rather than "which of these hundred pairs is most
    similar?". A greedy pass is also easy to explain to the person confirming
    the result, which matters more here than optimality.

    `is_player` drops anyone `cv/participants.py` decided is not playing, before
    they can become a cluster. A cluster is a question put to a coach — "who is
    this?" — and the referee is not one of the answers.
    """
    spans = track_spans(table, colours)
    if is_player is not None:
        spans = {t: s for t, s in spans.items() if is_player(t)}
    if not spans:
        return []

    ordered = sorted(spans.values(), key=lambda s: (s.first_s, s.track_id))

    # Union-find over track ids, so a chain of three fragments ends up as one
    # cluster rather than two overlapping pairs.
    parent = {span.track_id: span.track_id for span in ordered}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    members: dict[int, list[TrackSpan]] = {s.track_id: [s] for s in ordered}
    # The union of every frame a cluster's members appear in, kept as one set so
    # the overlap test stays a single set operation. Checking member against
    # member instead is quadratic in cluster size on top of the quadratic pass
    # below, which cost about eight seconds on a fifteen-second clip.
    seen_frames: dict[int, set[int]] = {s.track_id: set(s.frames) for s in ordered}

    for index, span in enumerate(ordered):
        for other in ordered[index + 1:]:
            if other.first_s - span.last_s > MAX_BRIDGE_S:
                break                 # ordered by start, so nothing later is closer
            root_a, root_b = find(span.track_id), find(other.track_id)
            if root_a == root_b:
                continue
            # Compare whole clusters, not just the two tracks: a merge is only
            # safe if no member of either cluster was ever on screen with a
            # member of the other.
            if not seen_frames[root_a].isdisjoint(seen_frames[root_b]):
                continue
            if not _can_merge(span, other):
                continue
            parent[root_b] = root_a
            members[root_a].extend(members.pop(root_b))
            seen_frames[root_a] |= seen_frames.pop(root_b)

    clusters: list[PlayerCluster] = []
    for cluster_id, (root, group) in enumerate(sorted(members.items())):
        sightings = sum(s.sightings for s in group)
        if sightings < min_sightings:
            continue

        colours_seen = [s.colour for s in group if s.colour is not None]
        teams = [table.team_of(s.track_id) for s in group]
        known = [t for t in teams if t != UNKNOWN]

        clusters.append(PlayerCluster(
            cluster_id=cluster_id,
            track_ids={s.track_id for s in group},
            # Majority vote: a fragment mislabelled by one bad colour sample
            # should not decide the whole player's team.
            team=max(set(known), key=known.count) if known else UNKNOWN,
            first_seen_s=min(s.first_s for s in group),
            last_seen_s=max(s.last_s for s in group),
            sightings=sightings,
            colour=tuple(np.median(np.array(colours_seen), axis=0)) if colours_seen else None,
        ))

    clusters.sort(key=lambda c: c.sightings, reverse=True)
    for index, cluster in enumerate(clusters):
        cluster.cluster_id = index
    return clusters


def cluster_of_track(clusters: list[PlayerCluster]) -> dict[int, int]:
    """Reverse index: track id -> cluster id."""
    return {
        track_id: cluster.cluster_id
        for cluster in clusters
        for track_id in cluster.track_ids
    }


def fragmentation(clusters: list[PlayerCluster]) -> float:
    """Average tracks per cluster — how badly identity was breaking up.

    Reported rather than hidden, because it is the number that says how much to
    trust anything per-player. A one means tracking held; a ten means each
    "player" below is a tenth of one.
    """
    if not clusters:
        return 0.0
    return sum(len(c.track_ids) for c in clusters) / len(clusters)
