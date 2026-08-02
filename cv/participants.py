"""Who in the picture is actually playing.

The detector asks for COCO class 0 — person — and a football match is full of
people who are not in it. Substitutes warming up behind the goal, a coach
pacing his technical area, parents three deep along the touchline, and the
referee. Every one of them becomes a track, and every one of them then feeds
the possession scale, the nearest-player-to-the-ball test, the clustering that
becomes a player report, and the fragmentation warning.

`assign_teams` does not filter them out and was never meant to. It always
produces exactly two kit clusters from whatever it is handed, and anything that
matches neither closely comes back `UNKNOWN` — a bucket that holds officials,
spectators and both goalkeepers at once.

    The separation this module can make, and the one it cannot.

Somebody who has not moved a body length in twenty seconds is not playing
football. That is a strong, kit-independent, calibration-free statement, and it
catches the bench and the crowd, which are most of the problem by count.

Given a calibration there is a second and much better test: project the ground
point through the homography and ask whether it is on the pitch at all. That is
a measurement rather than a heuristic, and it is the one that catches
substitutes warming up behind the goal — people who move exactly like players,
because they are players, just not ones in the match.

Without a calibration that test is unavailable and this module says so rather
than approximating it. An earlier draft approximated it in pixels, as a band
around wherever the kit-classified players were standing, and it had to be
abandoned for a reason worth recording: a track with a known kit *contributes to
its own band*, so the test was trivially passed by exactly the tracks it could
have judged, and only ever bit on the kit-unknown ones — referees and
goalkeepers, the two it must never act on alone.

Separating a *referee* from a *goalkeeper* is that remaining problem. Both run,
both wear neither kit, and off a calibration there is no goalmouth to measure
either of them against. So officials are **flagged and kept**, never dropped:
carrying a referee inflates a few counts, whereas silently deleting a goalkeeper
removes a player from the match. `cv/keeper.py` refuses to guess at keepers
without a calibration for the same reason, and this follows it.

    The thresholds are guesses.

Every constant below was chosen to be reasonable and has never been checked
against a real touchline. The tests prove the classifier does what this
docstring says; they say nothing about whether the numbers are right. Anything
excluded therefore travels with the `reason` that excluded it, all the way into
the report JSON, so a wrong call is visible rather than merely wrong.

    Why spread rather than distance covered.

The obvious measure of "did this person move" is the path length, summed frame
to frame. It does not work. A detection box jitters by a pixel or two every
frame even on someone standing perfectly still, and at thirty frames a second
that noise integrates into hundreds of metres an hour — a stationary spectator
scores like a midfielder. Both features here are robust to it instead:
`spread_ph` is a median deviation, which ignores jitter by construction, and
`travel_ph_per_min` samples a second apart, by which point real movement
dominates the noise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .frames import COL_TRACK_ID, COL_X1, COL_X2, COL_Y1, COL_Y2, FrameTable
from .teams import UNKNOWN

ROLE_PLAYER = 'player'
ROLE_OFFFIELD = 'offfield'
ROLE_OFFICIAL = 'official'
ROLE_UNSURE = 'unsure'

# Below this much screen time there is not enough evidence to say anything, and
# the verdict is `unsure` rather than a coin flip. A player who appears for ten
# seconds at the edge of a clip must not be thrown away for being brief.
MIN_SCREEN_TIME_S = 20.0

# How far a track's ground point strays from its own median position, measured
# in that track's own player heights. Below this the person has not moved a
# body length in the whole time they were on screen. Even a goalkeeper watching
# play at the far end drifts further than this.
STATIC_SPREAD_PH = 1.5

# Movement sampled a second apart, in player heights per minute. Supporting
# evidence only — it is here so the reason string can say how still somebody was.
SAMPLE_S = 1.0

# Fraction of the frame treated as border. Reported as evidence and never acted
# on: with a camera framing the whole pitch the touchline sits close to the
# frame edge, so a wide player is at the edge for most of the match. Being at
# the edge of the picture says something about the camera, not the person.
EDGE_MARGIN = 0.04

# How far beyond the touchline or byline somebody has to be before they count as
# off the pitch, in metres. A throw-in is taken from the line itself and the
# taker steps back from it, so this has to clear a couple of metres of ordinary
# play before it means anything.
OFF_PITCH_MARGIN_M = 3.0

# Fraction of sightings off the pitch before a track is rejected for it. Not
# 1.0: a substitute who wanders on to collect a ball should still be rejected,
# and a real player's ground point lands off the pitch occasionally when the box
# is clipped by another body.
OFF_PITCH_SHARE_MIN = 0.8


@dataclass(frozen=True)
class ParticipantVerdict:
    """What one track is, and the evidence that says so."""

    track_id: int
    role: str
    reason: str
    sightings: int
    screen_time_s: float
    spread_ph: float
    travel_ph_per_min: float
    edge_share: float
    # None when there is no calibration, which is not the same as zero. Zero
    # would claim we looked and they were on the pitch every time.
    off_pitch_share: float | None
    kit_known: bool

    @property
    def excluded(self) -> bool:
        return self.role == ROLE_OFFFIELD

    def to_json(self) -> dict:
        return {
            'track_id': int(self.track_id),
            'role': self.role,
            'reason': self.reason,
            'sightings': int(self.sightings),
            'screen_time_s': round(float(self.screen_time_s), 2),
            'spread_ph': round(float(self.spread_ph), 3),
            'travel_ph_per_min': round(float(self.travel_ph_per_min), 2),
            'edge_share': round(float(self.edge_share), 3),
            'off_pitch_share': (
                None if self.off_pitch_share is None
                else round(float(self.off_pitch_share), 3)
            ),
            'kit_known': bool(self.kit_known),
        }


@dataclass
class ParticipantReport:
    by_track: dict[int, ParticipantVerdict] = field(default_factory=dict)

    @property
    def excluded(self) -> frozenset[int]:
        return frozenset(
            track_id for track_id, v in self.by_track.items() if v.excluded
        )

    @property
    def officials(self) -> frozenset[int]:
        return frozenset(
            track_id for track_id, v in self.by_track.items()
            if v.role == ROLE_OFFICIAL
        )

    def is_player(self, track_id: int) -> bool:
        """Everything not positively identified as off-field counts as playing.

        The default direction matters. An unknown track is kept, because the
        cost of carrying a stranger is a slightly wrong count and the cost of
        dropping a real player is a report that says they were never there.
        """
        verdict = self.by_track.get(int(track_id))
        return verdict is None or not verdict.excluded

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for verdict in self.by_track.values():
            out[verdict.role] = out.get(verdict.role, 0) + 1
        return out

    def to_json(self) -> list[dict]:
        return [v.to_json() for v in sorted(self.by_track.values(),
                                            key=lambda v: v.track_id)]


@dataclass
class _Accumulator:
    """Per-track running totals, so one pass over the frames is enough."""

    xs: list[float] = field(default_factory=list)
    ys: list[float] = field(default_factory=list)
    heights: list[float] = field(default_factory=list)
    times: list[float] = field(default_factory=list)
    edge_hits: int = 0
    off_pitch_hits: int = 0
    pitch_tests: int = 0


def _spread_ph(acc: _Accumulator) -> float:
    """Median distance from the track's own median position, in player heights.

    A median of distances rather than a mean, so one detector jump across the
    pitch cannot make a stationary person look busy.
    """
    scale = float(np.median(acc.heights))
    if scale <= 0:
        return 0.0

    cx, cy = float(np.median(acc.xs)), float(np.median(acc.ys))
    distances = [math.dist((x, y), (cx, cy)) for x, y in zip(acc.xs, acc.ys)]
    return float(np.median(distances)) / scale


def _travel_ph_per_min(acc: _Accumulator) -> float:
    """Ground covered, sampled a second apart, in player heights per minute."""
    scale = float(np.median(acc.heights))
    span = acc.times[-1] - acc.times[0]
    if scale <= 0 or span <= 0:
        return 0.0

    total = 0.0
    last = 0
    for i in range(1, len(acc.times)):
        if acc.times[i] - acc.times[last] < SAMPLE_S and i != len(acc.times) - 1:
            continue
        total += math.dist((acc.xs[i], acc.ys[i]), (acc.xs[last], acc.ys[last]))
        last = i

    return (total / scale) / (span / 60.0)


def _verdict(track_id, acc, kit_known) -> ParticipantVerdict:
    screen_time = acc.times[-1] - acc.times[0]
    spread = _spread_ph(acc)
    travel = _travel_ph_per_min(acc)
    edge_share = acc.edge_hits / len(acc.times)
    off_pitch = (
        acc.off_pitch_hits / acc.pitch_tests if acc.pitch_tests else None
    )

    def made(role, reason):
        return ParticipantVerdict(
            track_id=int(track_id), role=role, reason=reason,
            sightings=len(acc.times), screen_time_s=screen_time,
            spread_ph=spread, travel_ph_per_min=travel,
            edge_share=edge_share, off_pitch_share=off_pitch,
            kit_known=kit_known,
        )

    if screen_time < MIN_SCREEN_TIME_S:
        return made(
            ROLE_UNSURE,
            f'on screen for only {screen_time:.0f}s — too brief to judge',
        )

    if off_pitch is not None and off_pitch >= OFF_PITCH_SHARE_MIN:
        return made(
            ROLE_OFFFIELD,
            f'off the pitch for {off_pitch:.0%} of the time they were on screen',
        )

    if spread < STATIC_SPREAD_PH:
        # No screen time in the sentence. It is a field on this verdict
        # already, and every reader shows it alongside — so putting it here too
        # printed it twice, once in minutes and once as a raw "2460s" that no
        # coach should be asked to divide by sixty.
        return made(
            ROLE_OFFFIELD,
            f'never moved more than {spread:.1f} of a body length from one spot',
        )

    if not kit_known:
        return made(
            ROLE_OFFICIAL,
            'moves like a player but matches neither kit — a referee or a '
            'goalkeeper, and without a calibration those look identical',
        )

    return made(ROLE_PLAYER, 'moves like a player and wears a kit')


def classify_participants(table: FrameTable, team_of=None) -> ParticipantReport:
    """Decide what every track in the window is.

    `team_of` maps a track id to a team name; it defaults to the table's own
    assignment. Injectable so the classifier can be tested without building a
    TeamAssignment, the same seam `cv/frames.py` uses for its detector.
    """
    team_of = team_of or table.team_of
    width = float(table.frame_width or 0)
    height = float(table.frame_height or 0)
    x_edge, y_edge = width * EDGE_MARGIN, height * EDGE_MARGIN
    pitch = table.calibration.pitch if table.calibration else None

    accumulators: dict[int, _Accumulator] = {}

    for record in table.records:
        for row in record.players:
            track_id = int(row[COL_TRACK_ID])
            x = (float(row[COL_X1]) + float(row[COL_X2])) / 2
            y = float(row[COL_Y2])

            acc = accumulators.get(track_id)
            if acc is None:
                acc = accumulators[track_id] = _Accumulator()

            acc.xs.append(x)
            acc.ys.append(y)
            acc.heights.append(float(row[COL_Y2]) - float(row[COL_Y1]))
            acc.times.append(record.timestamp_s)

            if width and height and (
                x < x_edge or x > width - x_edge
                or y < y_edge or y > height - y_edge
            ):
                acc.edge_hits += 1

            if pitch is not None:
                on_pitch = table.to_pitch((x, y))
                if on_pitch is not None:
                    acc.pitch_tests += 1
                    if not pitch.contains(*on_pitch, margin_m=OFF_PITCH_MARGIN_M):
                        acc.off_pitch_hits += 1

    report = ParticipantReport()
    for track_id, acc in accumulators.items():
        report.by_track[track_id] = _verdict(
            track_id, acc, kit_known=team_of(track_id) != UNKNOWN,
        )
    return report
