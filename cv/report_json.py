"""Rolling a match report up, and getting it out of Python.

This is the boundary between the CV pipeline and everything that consumes it —
the browser, Firestore, a human reading a file. Three rules shape it, and each
one was earned somewhere else in this project:

**Absent is not zero.** Anything needing a calibration serialises as `null`
when there is none. `analyse_match` learned this the hard way: "players tracked
0" read as "we looked and found nobody" when the truth was "we never looked".
A zero is a measurement. A null is an admission.

**The quality block travels with the numbers, not underneath them.** Ball
coverage and touch confidence are what say whether any count here means
anything, so they sit alongside rather than in a footnote nobody scrolls to.

**Per-track is not per-player.** At the fragmentation this pipeline measures,
a "player" with twelve touches may be a tenth of a real player. The JSON says
so in a field rather than relying on anyone having read a docstring.

Everything emitted here is a plain int, float, str, bool or None. numpy scalars
are not JSON-serialisable and arrive very easily — every position in this
pipeline starts life in a numpy array — so the conversion is deliberate rather
than incidental, and there is a test that walks the whole structure looking for
survivors.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .events import (
    CARRY,
    COMPLETED,
    DUEL,
    GOAL,
    INTERCEPTION,
    RECOVERY,
    TACKLE,
    EventLog,
    ppda,
)
from .identity import PlayerCluster, fragmentation
from .teams import TEAM_A, TEAM_B

SCHEMA_VERSION = 1

# More tracks than this for a match with ~22 players means identity broke up and
# every per-track number is a fragment.
FRAGMENTATION_LIMIT = 40


def _num(value):
    """A JSON-safe number, or None.

    numpy scalars pass `isinstance(x, float)` in some versions and fail
    `json.dumps` in all of them, so everything numeric goes through here.
    """
    if value is None:
        return None
    if isinstance(value, (np.generic,)):
        value = value.item()
    if isinstance(value, float):
        return round(value, 4)
    return value


def _round(value, places=2):
    return None if value is None else round(float(value), places)


@dataclass
class TeamStats:
    """Everything the event log can say about one team."""

    team: str

    possession_pct: float | None = None
    touches: int = 0

    passes_attempted: int = 0
    passes_completed: int = 0
    passes_by_length: dict[str, int] = field(default_factory=dict)
    passes_by_direction: dict[str, int] = field(default_factory=dict)
    progressive_passes: int | None = None
    final_third_entries: int | None = None
    box_entries: int | None = None
    switches: int | None = None
    crosses: int | None = None

    carries: int = 0

    shots: int | None = None
    shots_on_target: int | None = None
    goals: int | None = None
    xg: float | None = None

    tackles: int = 0
    interceptions: int = 0
    recoveries: int = 0
    duels: int = 0

    ppda: float | None = None
    shape: dict[str, float] = field(default_factory=dict)

    @property
    def pass_accuracy(self) -> float | None:
        """None when nothing was attempted. Zero would claim they all failed."""
        if not self.passes_attempted:
            return None
        return self.passes_completed / self.passes_attempted

    def to_json(self) -> dict:
        return {
            'team': self.team,
            'possession_pct': _round(self.possession_pct, 3),
            'touches': _num(self.touches),
            'passes_attempted': _num(self.passes_attempted),
            'passes_completed': _num(self.passes_completed),
            'pass_accuracy': _round(self.pass_accuracy, 3),
            'passes_by_length': {k: _num(v) for k, v in self.passes_by_length.items()},
            'passes_by_direction': {
                k: _num(v) for k, v in self.passes_by_direction.items()
            },
            'progressive_passes': _num(self.progressive_passes),
            'final_third_entries': _num(self.final_third_entries),
            'box_entries': _num(self.box_entries),
            'switches': _num(self.switches),
            'crosses': _num(self.crosses),
            'carries': _num(self.carries),
            'shots': _num(self.shots),
            'shots_on_target': _num(self.shots_on_target),
            'goals': _num(self.goals),
            'xg': _round(self.xg, 3),
            'tackles': _num(self.tackles),
            'interceptions': _num(self.interceptions),
            'recoveries': _num(self.recoveries),
            'duels': _num(self.duels),
            'ppda': _round(self.ppda, 2),
            'shape': {k: _round(v, 1) for k, v in self.shape.items()},
        }


@dataclass
class TrackStats:
    """One cluster of tracks. A person only after a human has confirmed it."""

    cluster_id: int
    team: str
    track_ids: list[int] = field(default_factory=list)
    minutes_tracked: float = 0.0

    distance_m: float | None = None
    top_speed_kmh: float | None = None
    sprint_count: int | None = None
    sprint_distance_m: float | None = None

    touches: int = 0
    passes_attempted: int = 0
    passes_completed: int = 0
    carries: int = 0
    shots: int | None = None
    goals: int | None = None
    xg: float | None = None
    tackles: int = 0
    interceptions: int = 0
    recoveries: int = 0

    # When each touch happened, in seconds. Carried per cluster rather than
    # only as a count so the player portal can put a mark on the match video
    # at every one of them — the difference between "you had 41 touches" and
    # being able to watch them.
    touch_times_s: list[float] = field(default_factory=list)

    heatmap: list[list[float]] | None = None

    @property
    def pass_accuracy(self) -> float | None:
        if not self.passes_attempted:
            return None
        return self.passes_completed / self.passes_attempted

    def to_json(self) -> dict:
        return {
            'cluster_id': self.cluster_id,
            'team': self.team,
            'track_ids': sorted(self.track_ids),
            'minutes_tracked': _round(self.minutes_tracked, 2),
            'distance_m': _round(self.distance_m, 1),
            'top_speed_kmh': _round(self.top_speed_kmh, 1),
            # Through _num even though it is "obviously an int": it is summed
            # from MovementStats, whose fields come out of numpy arrays, and a
            # np.int64 here is not JSON-serialisable while looking identical.
            'sprint_count': _num(self.sprint_count),
            'sprint_distance_m': _round(self.sprint_distance_m, 1),
            'touches': _num(self.touches),
            'passes_attempted': _num(self.passes_attempted),
            'passes_completed': _num(self.passes_completed),
            'pass_accuracy': _round(self.pass_accuracy, 3),
            'carries': _num(self.carries),
            'shots': _num(self.shots),
            'goals': _num(self.goals),
            'xg': _round(self.xg, 3),
            'tackles': _num(self.tackles),
            'interceptions': _num(self.interceptions),
            'recoveries': _num(self.recoveries),
            'touch_times_s': [_round(t, 2) for t in self.touch_times_s],
            'heatmap': self.heatmap,
        }


# ---------------------------------------------------------------- rollups


def team_stats(
    log: EventLog,
    team: str,
    calibrated: bool,
    possession_pct: float | None = None,
    shape: dict | None = None,
    opponent_attacking_end: str | None = None,
    pitch=None,
) -> TeamStats:
    """Aggregate one team's events.

    `calibrated` decides whether the positional counts are numbers or nulls.
    Without metres they are not zero — nobody counted them.
    """
    stats = TeamStats(team=team, possession_pct=possession_pct)
    passes = log.passes(team)

    stats.passes_attempted = len(passes)
    stats.passes_completed = sum(1 for p in passes if p.outcome == COMPLETED)
    stats.touches = sum(1 for t in (log.touches or []) if t.team == team)
    stats.carries = sum(1 for e in log.by_type(CARRY) if e.team == team)

    for name, kind in (
        ('tackles', TACKLE), ('interceptions', INTERCEPTION),
        ('recoveries', RECOVERY), ('duels', DUEL),
    ):
        setattr(stats, name, sum(1 for e in log.by_type(kind) if e.team == team))

    for entry in passes:
        if entry.length_bucket:
            stats.passes_by_length[entry.length_bucket] = (
                stats.passes_by_length.get(entry.length_bucket, 0) + 1
            )
        if entry.direction:
            stats.passes_by_direction[entry.direction] = (
                stats.passes_by_direction.get(entry.direction, 0) + 1
            )

    if calibrated:
        def tagged(tag: str) -> int:
            return sum(1 for p in passes if tag in p.tags)

        stats.progressive_passes = tagged('progressive')
        stats.final_third_entries = tagged('final_third_entry')
        stats.box_entries = tagged('into_box')
        stats.switches = tagged('switch')
        stats.crosses = tagged('cross')

        shots = log.shots(team)
        stats.shots = len(shots)
        stats.shots_on_target = sum(1 for s in shots if s.on_target)
        stats.goals = sum(1 for s in shots if s.outcome == GOAL)
        scored = [s.xg for s in shots if s.xg is not None]
        stats.xg = sum(scored) if scored else None

        if pitch is not None:
            stats.ppda = ppda(log, pitch, team, opponent_attacking_end)

    stats.shape = shape or {}
    return stats


def track_stats(
    clusters: list[PlayerCluster],
    log: EventLog,
    players_by_track: dict | None = None,
    calibrated: bool = False,
) -> list[TrackStats]:
    """Per-cluster rollups, merging movement in from the per-track reports."""
    out: list[TrackStats] = []
    players_by_track = players_by_track or {}

    for cluster in clusters:
        stats = TrackStats(
            cluster_id=cluster.cluster_id,
            team=cluster.team,
            track_ids=sorted(cluster.track_ids),
            minutes_tracked=cluster.minutes_tracked,
        )

        for track_id in cluster.track_ids:
            for event in log.by_track(track_id):
                if event.type == 'pass':
                    stats.passes_attempted += 1
                    if event.outcome == COMPLETED:
                        stats.passes_completed += 1
                elif event.type == CARRY:
                    stats.carries += 1
                elif event.type == TACKLE:
                    stats.tackles += 1
                elif event.type == INTERCEPTION:
                    stats.interceptions += 1
                elif event.type == RECOVERY:
                    stats.recoveries += 1

            if log.touches:
                found = log.touches.by_track(track_id)
                stats.touches += len(found)
                stats.touch_times_s.extend(t.timestamp_s for t in found)

        # A cluster is several tracks, so its touches arrive out of order.
        stats.touch_times_s.sort()

        if calibrated:
            shots = [s for s in log.shots() if s.track_id in cluster.track_ids]
            stats.shots = len(shots)
            stats.goals = sum(1 for s in shots if s.outcome == GOAL)
            scored = [s.xg for s in shots if s.xg is not None]
            stats.xg = sum(scored) if scored else None

            # Movement is per track; a cluster covers the ground all its
            # fragments did.
            distance = 0.0
            top = 0.0
            sprints = 0
            sprint_distance = 0.0
            seen = False
            for track_id in cluster.track_ids:
                player = players_by_track.get(track_id)
                if player is None or player.movement is None:
                    continue
                seen = True
                distance += player.movement.distance_m
                top = max(top, player.movement.top_speed_kmh)
                sprints += player.movement.sprint_count
                sprint_distance += player.movement.sprint_distance_m

            if seen:
                stats.distance_m = distance
                stats.top_speed_kmh = top
                stats.sprint_count = sprints
                stats.sprint_distance_m = sprint_distance

        out.append(stats)

    return out


# ---------------------------------------------------------------- the file


def build_report_json(
    report,
    window: dict | None = None,
    include_touches: bool = False,
    include_events: bool = True,
) -> dict:
    """The whole report as one JSON-safe dict.

    `touches` is opt-in: at thirty frames a second it dwarfs everything else in
    the file by an order of magnitude, and almost nothing needs it except the
    threshold-tuning harness.
    """
    calibrated = report.movement_available
    log = report.events or EventLog()
    possession = report.possession

    players_by_track = {p.track_id: p for p in report.players}

    teams = {}
    for team in (TEAM_A, TEAM_B):
        teams[team] = team_stats(
            log, team,
            calibrated=calibrated,
            possession_pct=possession.share(team) if possession else None,
            shape=report.shape if team == TEAM_A else {},
        ).to_json()

    tracks = track_stats(
        report.clusters, log, players_by_track, calibrated=calibrated,
    )

    warnings = list(report.warnings)
    fragments = fragmentation(report.clusters)
    if len(report.players) > FRAGMENTATION_LIMIT:
        # Carried next to the per-track numbers rather than only in the summary,
        # because the summary is not what a frontend reads.
        warnings.append(
            f'{len(report.players)} tracks merged into {len(report.clusters)} '
            f'clusters ({fragments:.1f} tracks each) — per-player figures are '
            'fragments until a human confirms the mapping'
        )

    data = {
        'schema_version': SCHEMA_VERSION,
        'source': report.source,
        'window': window or {},
        'duration_s': _round(report.duration_s, 1),
        'processing_s': _round(report.processing_s, 1),
        'calibrated': calibrated,
        'calibration_error_m': _round(report.calibration_error_m, 3),
        'quality': _quality(report, log),
        'warnings': warnings,
        'trustworthy': not warnings,
        'teams': teams,
        'tracks': [t.to_json() for t in tracks],
        'keepers': [k.to_json() for k in report.keeper_stats],
        'clusters': [c.to_json() for c in report.clusters],
    }

    if include_events:
        data['events'] = [e.to_json() for e in log]
    if include_touches and report.touches:
        data['touches'] = log.to_json(include_touches=True).get('touches', [])

    return data


def _quality(report, log: EventLog) -> dict:
    """The figures that say whether anything above is worth reading."""
    touches = report.touches

    return {
        'kit_separation': _round(report.kit_separation, 1),
        'clear_holder_share': _round(report.clear_holder_share, 3),
        # Seen and filled-in kept apart. Interpolated points are a straight
        # line drawn between two sightings, so folding them into one
        # "coverage" figure would let a run that barely saw the ball grade as
        # well as one that watched it throughout.
        'ball_seen_share': _round(report.ball_seen_share, 3),
        'ball_filled_share': _round(report.ball_filled_share, 3),
        'tracks': len(report.players),
        'clusters': len(report.clusters),
        'tracks_per_cluster': _round(fragmentation(report.clusters), 1),
        'touches': len(touches.touches) if touches else 0,
        'touch_confidence_p50': (
            _round(touches.confidence_percentile(50), 3) if touches else None
        ),
        'touch_confidence_p10': (
            _round(touches.confidence_percentile(10), 3) if touches else None
        ),
        'unseen_spans': len(touches.gaps) if touches else 0,
        'keeper_method': report.keepers.method if report.keepers else 'unavailable',
    }
