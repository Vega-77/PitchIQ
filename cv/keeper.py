"""Finding the goalkeepers, and what can be said about them.

Colour cannot do this alone. `teams.assign_teams` clusters on shirt chroma and
drops whatever matches neither kit into UNKNOWN — which is exactly where both
goalkeepers land, and also both referees, and anyone in a bib on the touchline.
Colour tells you somebody is not wearing either kit; it does not tell you which
of the not-wearing-either-kit people is a goalkeeper.

Position can, and the signal is unusually clean. A keeper spends nearly all of
a match within about twenty-five metres of one goal and inside the width of the
penalty area. A covering centre-back drops in there occasionally. A referee
patrols a diagonal and is almost never in either zone. So:

    colour proposes, position confirms, a human overrides both.

Without a calibration there is no position, and this returns
`method='unavailable'` rather than guessing. That is a deliberate refusal: the
alternative on offer is the nearest-player-to-goal heuristic that
`xg_bridge.shot_context_from_tracking` already documents as occasionally
catastrophic, and a wrong keeper poisons save percentage, distribution and
every keeper feature in the xG model at once.

`by_team` holds a *set* of track ids per team, not one id. At the fragmentation
this pipeline currently measures, one keeper is many tracks, and the positional
test is what makes the whole set recoverable where colour alone would only find
pieces of it.

    What is not here, and why.

**Punches.** Nothing distinguishes a punch from a catch without the ball's
height. Omitted rather than reported as zero.

**Punt versus goal kick.** Both are the keeper hitting the ball a long way. The
separator used here is a *hold* — the keeper having the ball at their feet or
in their hands for a couple of seconds first — which works for an obvious punt
and fails on a quickly-taken one. `kind='unknown'` is a legitimate answer and
is used freely.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .events import CARRY, GOAL, SAVED, EventLog, Pass
from .frames import FrameTable
from .pitch import Pitch
from . import zones

# How near their own goal a keeper lives, in metres.
KEEPER_ZONE_M = 25.0

# Share of a track's sightings that must fall in that zone. A keeper scores
# above 0.8; a centre-back who drops in scores well under 0.3; a referee, who
# works a diagonal, scores near zero.
MIN_GOAL_SHARE = 0.75

# Too few sightings to judge anything.
MIN_TRACK_SIGHTINGS = 15

# A hold long enough to mark a dead-ball restart rather than open play.
HOLD_S = 1.5

# Distribution bands, in metres.
THROW_MAX_M = 25.0
PUNT_MIN_M = 40.0


@dataclass
class KeeperAssignment:
    by_team: dict[str, set[int]] = field(default_factory=dict)
    scores: dict[int, float] = field(default_factory=dict)
    method: str = 'unavailable'      # 'colour+position' | 'manual' | 'unavailable'

    def is_keeper(self, track_id: int) -> bool:
        return any(track_id in ids for ids in self.by_team.values())

    def team_of_keeper(self, track_id: int) -> str | None:
        for team, ids in self.by_team.items():
            if track_id in ids:
                return team
        return None

    def all_tracks(self) -> set[int]:
        out: set[int] = set()
        for ids in self.by_team.values():
            out |= ids
        return out


@dataclass
class KeeperDistribution:
    timestamp_s: float
    kind: str                        # 'throw' | 'kick' | 'punt' | 'unknown'
    hold_duration_s: float
    distance_m: float
    completed: bool
    end_third: str | None


@dataclass
class KeeperReport:
    team: str
    track_ids: list[int] = field(default_factory=list)
    shots_faced: int = 0
    shots_on_target_faced: int = 0
    saves: int = 0
    goals_conceded: int = 0
    claims: int = 0
    sweeper_actions: int = 0
    sweeper_max_distance_m: float = 0.0
    distributions: list[KeeperDistribution] = field(default_factory=list)
    # Whether anything was able to say which goal this keeper defends.
    # Every figure but the shot counts is measured against that goal, so
    # without it `_count_positional` and `_collect_distribution` never run and
    # the four fields they fill stay at their defaults. Publishing those
    # defaults would say a keeper claimed nothing and never left his line,
    # when in truth nobody looked; `to_json` sends None for them instead.
    end_known: bool = False

    @property
    def save_pct(self) -> float | None:
        faced = self.saves + self.goals_conceded
        return self.saves / faced if faced else None

    def _of_kind(self, kind: str) -> list[KeeperDistribution]:
        return [d for d in self.distributions if d.kind == kind]

    def accuracy(self, kind: str) -> float | None:
        held = self._of_kind(kind)
        if not held:
            return None
        return sum(1 for d in held if d.completed) / len(held)

    def mean_distance_m(self, kind: str) -> float | None:
        held = self._of_kind(kind)
        if not held:
            return None
        return sum(d.distance_m for d in held) / len(held)

    def to_json(self) -> dict:
        return {
            'team': self.team,
            'track_ids': sorted(self.track_ids),
            'shots_faced': self.shots_faced,
            'shots_on_target_faced': self.shots_on_target_faced,
            'saves': self.saves,
            'goals_conceded': self.goals_conceded,
            'save_pct': self.save_pct,
            # None, not zero, when nothing said which goal this keeper
            # defends — see `end_known`. And the furthest sweep is None
            # whenever there were no sweeper actions to take a maximum of,
            # because a max over an empty set is not 0.0 metres.
            'claims': self.claims if self.end_known else None,
            'sweeper_actions': self.sweeper_actions if self.end_known else None,
            'sweeper_max_distance_m': (
                round(self.sweeper_max_distance_m, 1) if self.sweeper_actions else None
            ),
            'distributions': len(self.distributions) if self.end_known else None,
            'kick_accuracy': self.accuracy('kick'),
            'punt_accuracy': self.accuracy('punt'),
            'throw_accuracy': self.accuracy('throw'),
            'mean_kick_distance_m': self.mean_distance_m('kick'),
            'mean_punt_distance_m': self.mean_distance_m('punt'),
        }


# ---------------------------------------------------------------- identify


def goal_share(positions_m, pitch: Pitch, end: str) -> float:
    """Fraction of a track's sightings spent guarding one goal."""
    points = list(positions_m)
    if not points:
        return 0.0

    inside = 0
    half_width = pitch.width_m / 2
    for x, y in points:
        near_goal = zones.distance_to_goal(pitch, x, y, end) <= KEEPER_ZONE_M
        # Also inside roughly the width of the box: a winger hugging the
        # touchline near the corner flag is close to the goal by straight-line
        # distance and is not remotely a goalkeeper.
        in_lane = abs(y - half_width) <= 25.0
        if near_goal and in_lane:
            inside += 1
    return inside / len(points)


def identify_keepers(
    table: FrameTable,
    defending_end_of_team: dict[str, str] | None = None,
    manual: dict[str, list[int]] | None = None,
    min_goal_share: float = MIN_GOAL_SHARE,
    min_sightings: int = MIN_TRACK_SIGHTINGS,
) -> KeeperAssignment:
    """Work out which tracks are goalkeepers.

    `defending_end_of_team` maps a colour cluster to the goal it is protecting
    this period — derivable from `MatchOrientation` plus the human fact of which
    cluster is which side. Without it a keeper can still be *found*, but not
    attributed to a team, so this returns nothing rather than half an answer.
    """
    if manual:
        return KeeperAssignment(
            by_team={team: set(ids) for team, ids in manual.items()},
            scores={},
            method='manual',
        )

    if table.calibration is None or not defending_end_of_team:
        # No metres means no "near the goal", and the alternative heuristic is
        # the one already documented as occasionally catastrophic.
        return KeeperAssignment(method='unavailable')

    pitch = table.calibration.pitch
    positions: dict[int, list[tuple[float, float]]] = {}
    for record in table.records:
        for box in record.player_boxes():
            point = table.to_pitch(box.ground_point)
            if point is not None:
                positions.setdefault(box.track_id, []).append(point)

    assignment = KeeperAssignment(method='colour+position')
    for team, end in defending_end_of_team.items():
        assignment.by_team.setdefault(team, set())

    for track_id, points in positions.items():
        if len(points) < min_sightings:
            continue
        for team, end in defending_end_of_team.items():
            share = goal_share(points, pitch, end)
            assignment.scores[track_id] = max(
                assignment.scores.get(track_id, 0.0), share
            )
            if share >= min_goal_share:
                assignment.by_team[team].add(track_id)

    return assignment


# ---------------------------------------------------------------- stats


def keeper_reports(
    log: EventLog,
    keepers: KeeperAssignment,
    pitch: Pitch,
    defending_end_of_team: dict[str, str],
) -> list[KeeperReport]:
    """Everything the event log can say about each goalkeeper."""
    reports: list[KeeperReport] = []

    for team, tracks in sorted(keepers.by_team.items()):
        if not tracks:
            continue
        end = defending_end_of_team.get(team)
        report = KeeperReport(team=team, track_ids=sorted(tracks))

        _count_shots(log, report, team, tracks)
        if end is not None:
            report.end_known = True
            _count_positional(log, report, pitch, tracks, end)
            _collect_distribution(log, report, pitch, tracks, team, end)

        reports.append(report)

    return reports


def _count_shots(log: EventLog, report: KeeperReport, team: str, tracks: set[int]) -> None:
    for shot in log.shots():
        if shot.team == team:
            continue                       # our own keeper does not face our shots
        report.shots_faced += 1
        if shot.on_target:
            report.shots_on_target_faced += 1
        if shot.outcome == SAVED:
            report.saves += 1
        elif shot.outcome == GOAL:
            report.goals_conceded += 1


def _count_positional(
    log: EventLog, report: KeeperReport, pitch: Pitch, tracks: set[int], end: str
) -> None:
    """Sweeper actions and claims, both of which are questions about where."""
    for event in log.events:
        if event.track_id not in tracks or event.start_m is None:
            continue

        x, y = event.start_m
        if not zones.in_penalty_area(pitch, x, y, end):
            report.sweeper_actions += 1
            report.sweeper_max_distance_m = max(
                report.sweeper_max_distance_m,
                zones.distance_to_goal(pitch, x, y, end),
            )

    # A claim: the keeper's first touch on a ball an opponent crossed into the
    # box. Approximated from the cross tag rather than from the ball's height,
    # which is what a human would actually use.
    for event in log.events:
        if not isinstance(event, Pass) or 'cross' not in event.tags:
            continue
        landed = [
            e for e in log.events
            if e.track_id in tracks
            and 0 <= e.timestamp_s - event.timestamp_s <= 2.0
        ]
        if landed:
            report.claims += 1


def _collect_distribution(
    log: EventLog,
    report: KeeperReport,
    pitch: Pitch,
    tracks: set[int],
    team: str,
    end: str,
) -> None:
    """Throws, kicks and punts, and whether they found a teammate."""
    holds = {
        e.timestamp_s: e for e in log.by_type(CARRY) if e.track_id in tracks
    }

    for event in log.events:
        if not isinstance(event, Pass) or event.track_id not in tracks:
            continue
        if event.start_m is None or event.end_m is None:
            continue

        distance = math.dist(event.start_m, event.end_m)
        hold = 0.0
        for start, carry in holds.items():
            if 0 <= event.timestamp_s - start <= 4.0:
                hold = max(hold, getattr(carry, 'touches', 0) / 10.0)

        report.distributions.append(KeeperDistribution(
            timestamp_s=event.timestamp_s,
            kind=_distribution_kind(distance, hold),
            hold_duration_s=hold,
            distance_m=distance,
            completed=event.outcome == 'completed',
            end_third=zones.third(pitch, event.end_m[0], zones.opposite(end)),
        ))


def _distribution_kind(distance_m: float, hold_s: float) -> str:
    """Throw, kick, punt — or an honest 'unknown'.

    A punt is a drop-kick from the hands, so it is preceded by a hold and
    travels a long way. A throw is short and quick. Everything in between is a
    kick, and a long ball with no detectable hold is genuinely ambiguous —
    height would separate it instantly and is exactly what is missing.
    """
    if distance_m <= THROW_MAX_M and hold_s < HOLD_S:
        return 'throw'
    if distance_m >= PUNT_MIN_M and hold_s >= HOLD_S:
        return 'punt'
    if distance_m >= PUNT_MIN_M:
        return 'unknown'         # long, but nothing says punt rather than kick
    return 'kick'
