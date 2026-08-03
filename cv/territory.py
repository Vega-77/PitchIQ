"""Where on the pitch each team had the ball.

Possession says *how much*. It says nothing about where, and the two come apart
in the way that matters most to a coach: a side pinned in its own half can hold
60% of the ball and be losing badly, and every possession figure this pipeline
has produced would have called that a good half.

`cv/possession.py` has carried a note since it was written saying territory was
deliberately left out pending a homography. The homography arrived; this is the
part that was waiting for it.

    Named from each team's own direction.

A third is `defensive`, `middle` or `attacking` **relative to the team holding
the ball**, not a fixed end of the pitch. So "Team A spent 40% of its possession
in its attacking third" and "Team B spent 40% of its possession in its
attacking third" describe opposite ends of the same field, and both are the
sentence a coach wants. `zones.third` already works this way and this is only
applying it per frame.

    What it is not.

It is not territory in the sense of where play happened — it is where the ball
was **while a team held it**. Frames with no clear holder are excluded from both
teams, so a scramble in the box belongs to nobody. That is the conservative
reading: the alternative is attributing contested time to whoever happened to be
marginally nearer, which is the thing possession smoothing already exists to
avoid.

Every frame here needs a ball position in metres, so this needs a calibration
and it needs the ball to have been seen. Frames where it was interpolated are
included — a straight line between two sightings is a poor answer to "did it
cross the line" and a perfectly good one to "which third was it in".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import zones
from .teams import TEAM_A, TEAM_B

THIRDS = (zones.DEFENSIVE_THIRD, zones.MIDDLE_THIRD, zones.ATTACKING_THIRD)


@dataclass
class TerritorySplit:
    """Seconds of possession per team, per third, from that team's own view."""

    seconds: dict[str, dict[str, float]] = field(default_factory=dict)

    def total_s(self, team: str) -> float:
        return sum((self.seconds.get(team) or {}).values())

    def share(self, team: str, third: str) -> float | None:
        """What fraction of this team's possession was in that third.

        None, not zero, when the team never had a measurable possession —
        a team that never held the ball did not hold 0% of it in each third,
        it has no split at all.
        """
        total = self.total_s(team)
        if total <= 0:
            return None
        return (self.seconds.get(team) or {}).get(third, 0.0) / total

    def to_json(self) -> dict:
        out = {}
        for team in (TEAM_A, TEAM_B):
            if self.total_s(team) <= 0:
                # Absent rather than three zeroes, for the usual reason.
                out[team] = None
                continue
            out[team] = {
                third: round(self.share(team, third), 3) for third in THIRDS
            } | {'seconds': round(self.total_s(team), 1)}
        return out


def territory(states, pitch, attacking_ends: dict[str, str]) -> TerritorySplit:
    """Split each team's possession time across the thirds it happened in.

    `states` are `FrameState`s carrying `ball_m`; `attacking_ends` maps a team to
    the end it is attacking this period, which is the same answer
    `events.attacking_end_for` gives and should come from there.

    Time is attributed to the interval *ending* at each frame, so a run of
    frames in one third accumulates the wall-clock time it actually spanned
    rather than a count of frames. Frame rate then cannot change the answer,
    which matters because `stride` is a documented speed lever.
    """
    split = TerritorySplit()
    if not states or len(states) < 2:
        return split

    for previous, state in zip(states, states[1:]):
        team = state.team
        end = attacking_ends.get(team)
        if end is None or state.ball_m is None:
            continue
        if team not in (TEAM_A, TEAM_B):
            continue

        elapsed = state.timestamp_s - previous.timestamp_s
        if elapsed <= 0:
            continue

        third = zones.third(pitch, state.ball_m[0], end)
        buckets = split.seconds.setdefault(team, {t: 0.0 for t in THIRDS})
        buckets[third] += elapsed

    return split


def pinned_back(split: TerritorySplit, team: str, threshold: float = 0.45):
    """A plain sentence when a team's possession was mostly in its own third.

    Returns None when there is nothing to say, which is the common case. The
    threshold is a guess like every other threshold in this package — it is set
    where it is because a third of the pitch is a third of the pitch, so 45% is
    already well above what an even spread would give.
    """
    share = split.share(team, zones.DEFENSIVE_THIRD)
    if share is None or share < threshold:
        return None
    return (
        f'{round(share * 100)}% of that possession was in their own third'
    )
