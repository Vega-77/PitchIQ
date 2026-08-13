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
    pressing_segments,
    turnovers_by_third,
)
from .identity import PlayerCluster, fragmentation
from .metrics import phantom_m_per_minute
from .pitch import Pitch
from .teams import TEAM_A, TEAM_B

# 2: `participants` added, `shape` became per team, `no_ball_s` became a real
#    measurement instead of an identity that was always zero.
# 3: `reconciliation` added, and `xg` started carrying a number — `attach_xg`
#    had no caller until now, so every xG field in a version 2 document is null
#    because nothing computed it, not because no shots were found.
# 4: `pressing_segments` added per team. Purely additive — a version 3 reader
#    sees a key it does not know and every other number is unchanged.
# 5: `period` and `period_source` added. Also additive, and worth a version of
#    its own because it is the first field that says how confident the report is
#    about which way the teams were kicking — every version 4 document and
#    earlier was drawn as whatever the `--period` flag happened to say.
# 6: `accelerations`, `top_acceleration_ms2` and `position_noise_m` per cluster.
#    Additive. The last of those is the first figure the pipeline has published
#    about the quality of its own tracking rather than about the football, and
#    it is what decides whether the other two are reported at all.
# 7: `smoothing_s` and `phantom_m_per_minute` in the quality block. Additive,
#    but the first version where a figure changed rather than appeared: the
#    smoothing window is now fitted to each track's measured wobble instead of
#    being a fixed nine frames, so every distance and speed in a version 7
#    report is lower than the same footage would have given under version 6 —
#    by up to 29 metres a minute on noisy tracks. See SMOOTH_BANDS in
#    cv/metrics.py for the fit. Baselines taken under 6 will diff.
# 8: `blind` in the quality block. Additive, and a split of a figure that was
#    already there rather than a new measurement: `no_ball_s` has always been
#    one number covering both "the ball was off the pitch during a throw-in"
#    and "we lost twenty seconds of live football", which are not remotely the
#    same problem. See cv/blind.py.
# 9: `source_fps` and `sample_fps` in the quality block. Additive, and the
#    first fields that say how the run was *performed* rather than what it
#    found — every figure in metres belongs to a particular sample rate, and
#    until now a report analysed at half rate looked identical to one that was
#    not. See tests/test_sampling.py for what that costs (almost nothing, which
#    is itself the reason it has to be stated rather than assumed).
# 10: `timing` at the top level, and `realtime_factor` in the quality block.
#     Additive, and the second thing a report says about how the run was
#     performed rather than what it found. `processing_s` was already here and
#     answers a different question: it is the bill, and this is the itemisation
#     plus whether the bill would have fitted inside a live half. See
#     cv/timing.py — in particular why `lag_s` is a floor and not an estimate.
# 11: `camera` in the quality block. Additive, and the first field that says
#     whether the homography every metre goes through was still describing the
#     right pitch by the end of the window. A version 10 report is not a report
#     where the camera held still — it is one where nobody looked.
# 12: `pitch_coverage` beside it. Additive. The other question about the same
#     transform: not whether it still holds, but how much of the pitch it ever
#     covered. Same reading as version 11 — an older report is not one where the
#     camera saw the whole pitch, it is one where nobody measured.
SCHEMA_VERSION = 12

# More tracks than this for a match with ~22 players means identity broke up and
# every per-track number is a fragment.
FRAGMENTATION_LIMIT = 40

# Default pitch, used only to mirror a shot map onto one attacking direction.
# The real dimensions come from the calibration; a few centimetres of
# difference moves a plotted dot by less than its own radius.
PITCH_LENGTH_M = Pitch().length_m
PITCH_WIDTH_M = Pitch().width_m


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
    # The same figure block by block across the window, so a press that lasted
    # twenty minutes is distinguishable from one that lasted the match. None
    # without a calibration, without a known direction, or on a window too short
    # to hold two blocks — a trend needs at least two points to be a trend.
    pressing_segments: list[dict] | None = None
    shape: dict[str, float] = field(default_factory=dict)
    # Where this team's possession happened, as shares of its own total, named
    # from its own attacking direction. None without a calibration, and None for
    # a team that never held the ball — three zeroes would say the ball was
    # spread evenly across a pitch it never touched.
    territory: dict | None = None
    # What this team did with its possessions: where they began, how far they
    # got, how they ended, and how the passing held up in each third. Counts
    # rather than shares, because a funnel drawn from four possessions is noise
    # and only the denominator can say so.
    #
    # Different from `territory` on purpose. That says where the ball was; this
    # says what the team was trying to do there and whether it worked.
    phase_of_play: dict | None = None
    # How the shape differed late in the window against early. None on a clip
    # too short to have two halves worth comparing — which is every run so far.
    shape_drift: dict | None = None
    # Which goal this team attacked in this period. Published because a heatmap
    # without it cannot be read: "they stayed high" and "they sat deep" are the
    # same picture flipped.
    attacking_end: str | None = None
    # Every shot as a point on a pitch, already mirrored so the team attacks
    # right. None without a calibration or a known direction.
    shot_map: list[dict] | None = None
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
            'pressing_segments': self.pressing_segments,
            'shape': {k: _round(v, 1) for k, v in self.shape.items()},
            'territory': self.territory,
            'phase_of_play': self.phase_of_play,
            'shape_drift': self.shape_drift,
            'turnovers_by_third': self.turnovers_by_third,
            'attacking_end': self.attacking_end,
            'shot_map': self.shot_map,
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
    # Hard accelerations. None means the question could not be answered — the
    # fragments were all shorter than one burst window, or the tracking wobbled
    # too much for the answer to be about the player. Not the same as a player
    # who never accelerated, and the browser has to keep them apart.
    accelerations: int | None = None
    top_acceleration_ms2: float | None = None
    # How far the raw position wobbled frame to frame, averaged over this
    # cluster's fragments. The first thing this pipeline has published about the
    # quality of its own tracking.
    position_noise_m: float | None = None

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

    # This cluster's shots as points, same convention as the team's.
    shot_map: list[dict] | None = None

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
            'accelerations': _num(self.accelerations),
            'top_acceleration_ms2': _round(self.top_acceleration_ms2, 2),
            'position_noise_m': _round(self.position_noise_m, 3),
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
            'shot_map': self.shot_map,
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
    phase_of_play: dict | None = None,
    shape_drift: dict | None = None,
    attacking_end: str | None = None,
    span_s: tuple[float, float] | None = None,
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
        stats.shot_map = shot_marks(shots, attacking_end)

        if pitch is not None:
            stats.ppda = ppda(log, pitch, team, opponent_attacking_end)
            stats.turnovers_by_third = turnovers_by_third(
                log, pitch, team, attacking_end,
            )
            if span_s is not None:
                stats.pressing_segments = pressing_segments(
                    log, pitch, team, opponent_attacking_end, *span_s,
                )

    stats.attacking_end = attacking_end
    stats.shape = shape or {}
    stats.territory = territory
    stats.phase_of_play = phase_of_play
    stats.shape_drift = shape_drift
    return stats


def shot_marks(shots, attacking_end: str | None) -> list[dict] | None:
    """Shots as points on a pitch, always attacking to the right.

    A shot map is only readable if every shot on it faces the same way, so the
    flip happens here rather than in three renderers. `x_m` and `y_m` are
    therefore not raw pitch coordinates: they are the position expressed as if
    this team were attacking the right-hand goal, whichever end they actually
    attacked. Naming them anything shorter would invite somebody to plot a
    second-half shot at the wrong end and never notice.

    Kept beside the counts rather than in the event list. `events_payload` drops
    positions for good reasons that still hold for passes and carries — they are
    null without a calibration and nothing plots them — but a half has a dozen
    shots, and those are the ones somebody wants to see on a pitch.

    None without an attacking end, since the flip cannot be decided. An empty
    list means a calibrated run in which nobody shot, which is a real answer.
    """
    if attacking_end is None:
        return None

    marks = []
    for shot in shots:
        if shot.start_m is None:
            continue
        x, y = float(shot.start_m[0]), float(shot.start_m[1])
        if attacking_end == 'left':
            # Mirror through the centre, both axes, so left and right stay
            # consistent with each other and a shot from the right wing does
            # not migrate to the left one.
            x, y = PITCH_LENGTH_M - x, PITCH_WIDTH_M - y

        marks.append({
            # The join key. Without it a browser holding a coach's per-shot
            # corrections has only a rounded timestamp to match a mark against,
            # and two shots in the same second would silently swap.
            'event_id': shot.event_id,
            'video_s': _round(shot.timestamp_s, 2),
            'x_m': _round(x, 1),
            'y_m': _round(y, 1),
            'xg': _round(shot.xg, 3),
            # The same shot scored as a header. Not an alternative estimate of
            # the same quantity — it is the answer to a different question the
            # camera cannot settle, and it only replaces `xg` once a human says
            # which one this was.
            'xg_header': _round(shot.xg_header, 3),
            'outcome': shot.outcome,
            'on_target': bool(shot.on_target),
            'track_id': shot.track_id,
        })
    return marks


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
    attacking_ends: dict[str, str | None] | None = None,
) -> list[TrackStats]:
    """Per-cluster rollups, merging movement in from the per-track reports."""
    out: list[TrackStats] = []
    players_by_track = players_by_track or {}
    attacking_ends = attacking_ends or {}

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
            stats.shot_map = shot_marks(shots, attacking_ends.get(cluster.team))

            # Movement is per track; a cluster covers the ground all its
            # fragments did.
            distance = 0.0
            top = 0.0
            sprints = 0
            sprint_distance = 0.0
            seen = False

            # Bursts and noise are gathered separately, because a fragment can
            # contribute distance while having nothing to say about either: one
            # shorter than a burst window answers None, and summing None into a
            # total would turn "we could not tell" into "none happened".
            bursts = 0
            burst_seen = False
            hardest = None
            noises = []
            minutes = []

            for track_id in cluster.track_ids:
                player = players_by_track.get(track_id)
                if player is None or player.movement is None:
                    continue
                seen = True
                distance += player.movement.distance_m
                top = max(top, player.movement.top_speed_kmh)
                sprints += player.movement.sprint_count
                sprint_distance += player.movement.sprint_distance_m

                if player.movement.accelerations is not None:
                    burst_seen = True
                    bursts += player.movement.accelerations
                    if player.movement.top_acceleration_ms2 is not None:
                        hardest = max(
                            hardest if hardest is not None else float('-inf'),
                            player.movement.top_acceleration_ms2,
                        )
                if player.movement.position_noise_m is not None:
                    noises.append(player.movement.position_noise_m)
                    minutes.append(max(player.movement.minutes_tracked, 1e-9))

            if seen:
                stats.distance_m = distance
                stats.top_speed_kmh = top
                stats.sprint_count = sprints
                stats.sprint_distance_m = sprint_distance
            if burst_seen:
                stats.accelerations = bursts
                stats.top_acceleration_ms2 = hardest
            if noises:
                # Weighted by how long each fragment lasted: a two-second
                # fragment's noise estimate is built from a handful of samples
                # and should not outvote a two-minute one's.
                stats.position_noise_m = sum(
                    n * m for n, m in zip(noises, minutes)
                ) / sum(minutes)

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

    # The span the pressing blocks tile. `end_s` is optional on the command
    # line — nobody passes `--end` when they mean "to the end of the clip" — so
    # it falls back to however much footage was actually processed rather than
    # to however long the file is. The blocks have to describe the events that
    # exist, not the minutes that were skipped.
    span_start = float((window or {}).get('start_s') or 0.0)
    span_end = (window or {}).get('end_s')
    span = (
        span_start,
        float(span_end) if span_end is not None
        else span_start + float(report.duration_s or 0.0),
    )

    teams = {}
    for team in (TEAM_A, TEAM_B):
        other = TEAM_B if team == TEAM_A else TEAM_A
        teams[team] = team_stats(
            log, team,
            calibrated=calibrated,
            possession_pct=possession.share(team) if possession else None,
            shape=(report.shape or {}).get(team) or {},
            territory=territory_json.get(team),
            phase_of_play=_phase_json(report, team),
            shape_drift=_drift_json(report, team),
            pitch=pitch,
            attacking_end=ends.get(team),
            opponent_attacking_end=ends.get(other),
            span_s=span,
        ).to_json()

    tracks = track_stats(
        report.clusters, log, players_by_track, calibrated=calibrated,
        attacking_ends=ends,
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
        # Where those seconds went, and whether they would have fitted inside a
        # live half. None when the run was not instrumented — an old report, or
        # one assembled by hand — which is a different thing from a run that
        # took no time.
        'timing': (
            report.timings.to_json(report.duration_s) if report.timings else None
        ),
        'calibrated': calibrated,
        # Which half, and whether anything but a default said so. Both travel
        # because the period flips every pitch-relative figure in this file and
        # nothing else in it records the decision.
        'period': getattr(report, 'period', None),
        'period_source': getattr(report, 'period_source', None),
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
        # Seconds of work per second of football. Below 1 the run could have
        # been fed frames as they arrived and still have finished at the
        # whistle; at or above it the half-time report is late, and `timing`
        # above says by how much. Deliberately *not* a warning: a slow run is
        # not an inaccurate one, and `trustworthy` is `not warnings`, so
        # treating this as a defect would mark a perfectly good batch report
        # unreliable for taking its time.
        'realtime_factor': (
            _round(report.timings.realtime_factor(report.duration_s), 3)
            if report.timings else None
        ),
        # Whether the camera held still, and when it did not. Every figure in
        # metres is fitted from one frame, so this is the precondition for all
        # of them — carried whole rather than as a boolean, because "moved at
        # 34:12" is actionable and "moved" is not.
        'camera': report.camera.to_json() if report.camera else None,
        # How much of the pitch was ever in frame. The companion to `camera`:
        # that says whether the homography still held, this says how much of the
        # pitch it covered at all. Null without a calibration, and null when the
        # calibration carries no frame size — the frame's dimensions are the
        # boundary being measured against, so without them there is no answer
        # rather than a full-coverage one.
        'pitch_coverage': report.coverage.to_json() if report.coverage else None,
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
        # What that total was made of. A throw-in and a lost twenty seconds of
        # live football both land in `no_ball_s`, and only one of them is a
        # hole in the report — added together, the well-tagged half looks worse
        # than the untagged one. `checked` is False when no log reached the run,
        # and the three figures inside are then null rather than zero.
        'blind': report.blindness.to_json() if report.blindness else None,
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
        # How far the tracked position wobbles frame to frame, in metres. The
        # median across tracks, not the mean: one fragment that caught a
        # reflection should not set the figure for the run.
        #
        # This belongs with the quality block rather than with the football,
        # because it is the number every figure in metres rests on and the one
        # nothing has ever reported. It is also what decides whether bursts are
        # counted at all — see MAX_ACCEL_NOISE_M in cv/metrics.py.
        # What the footage runs at and what this run looked at. Both, because
        # neither means anything alone: 15 a second is half a camcorder and a
        # quarter of a phone. None rather than zero when nothing recorded it —
        # every report written before schema 9, which were all full rate but
        # cannot prove it.
        'source_fps': _round(report.source_fps or None, 2),
        'sample_fps': _round(report.sample_fps or None, 2),
        'position_noise_m': _round(_median_noise(report), 3),
        # How long the tracks were smoothed over, and what that wobble still
        # costs at that window. Both published because the second is no longer
        # derivable from the first: the window is chosen per track from the
        # measured noise, so the phantom rate stopped being proportional to the
        # noise the moment it stopped being a constant window. The browser held
        # exactly that constant, and this is what retires it.
        'smoothing_s': _round(_typical_smoothing(report), 2),
        'phantom_m_per_minute': _round(
            phantom_m_per_minute(
                _median_noise(report), _typical_smoothing(report) or 0.0
            ), 1,
        ),
    }


def _typical_smoothing(report) -> float | None:
    """The window most of this run's tracks were smoothed over.

    The mode, not a mean: the windows come from three bands, and averaging them
    would publish a window nothing was actually smoothed at.

    None when nothing was smoothed — an uncalibrated run has no positions in
    metres to smooth, and naming a window for it would describe work that never
    happened.
    """
    chosen = list(getattr(report, 'smoothing_s', {}).values())
    if not chosen:
        return None
    return max(set(chosen), key=chosen.count)


def _median_noise(report) -> float | None:
    measured = [
        p.movement.position_noise_m for p in report.players
        if p.movement is not None and p.movement.position_noise_m is not None
    ]
    if not measured:
        return None
    measured.sort()
    mid = len(measured) // 2
    if len(measured) % 2:
        return measured[mid]
    return (measured[mid - 1] + measured[mid]) / 2.0


def _phase_json(report, team: str) -> dict | None:
    """One team's possession funnel, or None if this run could not build one.

    `getattr` rather than a direct read because `team_stats` is called by tests
    and experiments with reports assembled by hand, and a missing attribute
    there should mean "not measured" rather than raise.
    """
    funnel = (getattr(report, 'phase_of_play', None) or {}).get(team)
    return funnel.to_json() if funnel else None


def _drift_json(report, team: str) -> dict | None:
    drift = (getattr(report, 'shape_drift', None) or {}).get(team)
    return drift.to_json() if drift else None


def _agreement(report, kind: str) -> float | None:
    reconciliation = getattr(report, 'reconciliation', None)
    if reconciliation is None:
        return None
    return _round(reconciliation.rate(kind), 3)
