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
    turnovers_by_third,
)
from .identity import PlayerCluster, fragmentation
from .teams import TEAM_A, TEAM_B

# 2: `participants` added, `shape` became per team, `no_ball_s` became a real
#    measurement instead of an identity that was always zero.
# 3: `reconciliation` added, and `xg` started carrying a number — `attach_xg`
#    had no caller until now, so every xG field in a version 2 document is null
#    because nothing computed it, not because no shots were found.
SCHEMA_VERSION = 3

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
    # Where this team's possession happened, as shares of its own total, named
    # from its own attacking direction. None without a calibration, and None for
    # a team that never held the ball — three zeroes would say the ball was
    # spread evenly across a pitch it never touched.
    territory: dict | None = None
    # How the shape differed late in the window against early. None on a clip
    # too short to have two halves worth comparing — which is every run so far.
    shape_drift: dict | None = None
    # Which goal this team attacked in this period. Published because a heatmap
    # without it cannot be read: "they stayed high" and "they sat deep" are the
    # same picture flipped.
    attacking_end: str | None = None
    # Giveaways by third, from this team's own direction. The defensive-third
    # count is the one the catalog asks for by name: a ball lost in front of
    # your own goal is a different event from one lost in theirs, and a single
    # turnover total hides exactly that.
    turnovers_by_third: dict | None = None

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
            'territory': self.territory,
            'shape_drift': self.shape_drift,
            'turnovers_by_third': self.turnovers_by_third,
            'attacking_end': self.attacking_end,
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
    territory: dict | None = None,
    shape_drift: dict | None = None,
    attacking_end: str | None = None,
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
            stats.turnovers_by_third = turnovers_by_third(
                log, pitch, team, attacking_end,
            )

    stats.attacking_end = attacking_end
    stats.shape = shape or {}
    stats.territory = territory
    stats.shape_drift = shape_drift
    return stats


def _merge_heatmaps(pairs) -> list[list[float]] | None:
    """Several tracks' occupancy grids into one, weighted by time tracked.

    `pairs` is `[(grid, minutes)]`. Each grid is already normalised to sum to 1,
    which is why the weighting matters: added raw, a fragment lasting eight
    seconds would count as much as one lasting half an hour, and a player
    tracked cleanly through a half plus once more at the edge of frame would
    show a hotspot at the edge of frame.

    Returns plain lists of floats, not a numpy array — everything leaving this
    module has to survive `json.dumps`.
    """
    usable = [(g, m) for g, m in pairs if g is not None and np.size(g)]
    if not usable:
        return None

    total = np.zeros_like(np.asarray(usable[0][0], dtype=float))
    for grid, minutes in usable:
        array = np.asarray(grid, dtype=float)
        if array.shape != total.shape:
            continue
        # A fragment with no minutes recorded still happened, so it counts for
        # something rather than nothing — but only just.
        total += array * (minutes if minutes and minutes > 0 else 0.001)

    weight = float(total.sum())
    if weight <= 0:
        return None
    return [[round(float(v), 5) for v in row] for row in (total / weight)]


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

            # Where the cluster spent its time. The grid is computed per track
            # in the pipeline and was never carried across to the cluster, so
            # `TrackStats.heatmap` serialised as null on every run — declared,
            # published, and never once filled in.
            stats.heatmap = _merge_heatmaps([
                (players_by_track.get(t).heatmap, players_by_track[t].minutes_tracked)
                for t in cluster.track_ids
                if players_by_track.get(t) is not None
                and players_by_track[t].heatmap is not None
            ])

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

    split = getattr(report, 'territory', None)
    territory_json = split.to_json() if split else {}

    # Both were missing entirely until 2026-08-02, and `ppda` was therefore
    # None in every report this project has ever produced — not because the
    # footage could not support it, but because nothing handed the function a
    # pitch to measure the pressing zone on.
    pitch = getattr(report, 'pitch', None)
    ends = getattr(report, 'attacking_ends', None) or {}

    teams = {}
    for team in (TEAM_A, TEAM_B):
        other = TEAM_B if team == TEAM_A else TEAM_A
        teams[team] = team_stats(
            log, team,
            calibrated=calibrated,
            possession_pct=possession.share(team) if possession else None,
            shape=(report.shape or {}).get(team) or {},
            territory=territory_json.get(team),
            shape_drift=_drift_json(report, team),
            pitch=pitch,
            attacking_end=ends.get(team),
            opponent_attacking_end=ends.get(other),
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
        # Every verdict, including the ones that changed nothing. An exclusion
        # that cannot be audited is indistinguishable from a bug.
        'participants': (
            report.participants.to_json() if report.participants else []
        ),
        # None, not an empty comparison. Without a tagged log there was nothing
        # to check this run against, which is a different statement from the two
        # records having been checked and found to agree about nothing.
        'reconciliation': (
            report.reconciliation.to_json()
            if getattr(report, 'reconciliation', None) else None
        ),
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
        # How many figures in the picture were left out of every number above,
        # and how many are being carried despite matching neither kit. Both
        # belong next to the stats rather than buried: they are the size of the
        # correction, and of the correction that could not be made.
        'excluded_tracks': (
            len(report.participants.excluded) if report.participants else 0
        ),
        'flagged_officials': (
            len(report.participants.officials) if report.participants else 0
        ),
        'no_ball_s': _round(
            report.possession.no_ball_s if report.possession else None, 1
        ),
        # None rather than zero when no tagged log was supplied. Zero would say
        # the ball was never out of play for the whole window, which is a
        # claim about the match rather than about what we were told.
        'dead_ball_s': _round(
            report.possession.dead_ball_s if report.phases else None, 1
        ),
        'live_share': (
            _round(1.0 - report.phases.dead_s / report.duration_s, 3)
            if report.phases and report.duration_s else None
        ),
        'stoppages': len(report.phases.spans) if report.phases else None,
        # How often this run and the person with the tablet described the same
        # moment the same way. Not an accuracy — both can be wrong together —
        # but a drift across several matches means something changed.
        'goal_agreement': _agreement(report, 'goal'),
        'exit_agreement': _agreement(report, 'exit'),
    }


def _drift_json(report, team: str) -> dict | None:
    drift = (getattr(report, 'shape_drift', None) or {}).get(team)
    return drift.to_json() if drift else None


def _agreement(report, kind: str) -> float | None:
    reconciliation = getattr(report, 'reconciliation', None)
    if reconciliation is None:
        return None
    return _round(reconciliation.rate(kind), 3)
