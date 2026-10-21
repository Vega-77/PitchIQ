"""Where on the pitch each team had the ball.

`territory` splits each team's possession across its own defensive, middle and
attacking thirds (named from the holding team's direction, as `zones.third`
does), from per-frame possession states, a `Pitch` and each team's attacking
end. `pinned_back` flags a team that spent too much of its ball in its own third.

It is where the ball was **while a team held it**, not where play happened:
frames with no clear holder count for nobody. Every frame needs the ball in
metres, so it needs a calibration; interpolated ball positions are included.

Design notes, measurements and history: `docs/design/territory.md`.
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
        """Seconds `team` held the ball, all thirds."""
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
        """Each team's share by third, 0..1, or None for a team never in possession."""
        out = {}
        for team in (TEAM_A, TEAM_B):
            if self.total_s(team) <= 0:
                # Absent rather than three zeroes, for the usual reason.
                out[team] = None
                continue
            out[team] = {third: round(self.share(team, third), 3) for third in THIRDS} | {
                'seconds': round(self.total_s(team), 1)
            }
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


def pinned_back(split: TerritorySplit, team: str, threshold: float = 0.45) -> str | None:
    """A plain sentence when a team's possession was mostly in its own third.

    Returns None when there is nothing to say, which is the common case. The
    threshold is a guess like every other threshold in this package — it is set
    where it is because a third of the pitch is a third of the pitch, so 45% is
    already well above what an even spread would give.
    """
    share = split.share(team, zones.DEFENSIVE_THIRD)
    if share is None or share < threshold:
        return None
    return f'{round(share * 100)}% of that possession was in their own third'
