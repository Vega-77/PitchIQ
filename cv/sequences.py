"""Possession sequences, and what a team did with them.

The roadmap has asked for phase-of-play since the catalog was written and left
its shape open, noting that `territory` and `turnovers_by_third` are the closest
things that exist and neither is it. That is the right reading, and the reason
is worth stating because it decides the whole design:

    territory says **where the ball was**. Phase-of-play says **what the team
    was trying to do there, and whether it worked**.

Those are different questions and only the second is actionable. A clearance
hoofed out of your own box and a patient move worked out from the goalkeeper are
the same square metre of pitch and the same second of possession; territory
cannot tell them apart and a coach cares about almost nothing else.

So the unit here is the **possession**, not the frame and not the event. A
possession has a place it began, a furthest point it reached, and a way it
ended, and those three make a funnel: of the times this team got the ball, how
often did they carry it into midfield, into the final third, into a shot. That
is a sentence a coach can do something about — "you win it in your own half
forty times and nine of those reach the final third" — in a way that "you had
33% of the ball in your defensive third" is not.

Everything here needs a calibration and an attacking end, because a third cannot
be named without both. Absent, the answer is None rather than an empty funnel: a
run that could not measure phases did not measure a team with no phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import zones
from .events import EventLog, INCOMPLETE, COMPLETED
from .pitch import Pitch

# Two events by the same team this far apart are not one possession. A pass and
# the touch that receives it are typically under two seconds; five leaves room
# for a carry and a look-up without welding two separate spells into one, and it
# is short enough that a spell the tracker lost the middle of is split rather
# than papered over.
MAX_GAP_S = 5.0

# How a possession finished.
ENDED_SHOT = 'shot'
ENDED_LOST = 'lost'
ENDED_STOPPED = 'stopped'

# The thirds, forward order, so a funnel can be walked without re-deciding which
# way is upfield every time.
FORWARD = (zones.DEFENSIVE_THIRD, zones.MIDDLE_THIRD, zones.ATTACKING_THIRD)


@dataclass(frozen=True)
class Sequence:
    """One spell of possession, and the three things that make it a phase.

    `reached_third` is the furthest third the ball got to, not where it ended:
    a move that reaches the byline and is worked back to the edge of the box
    reached the final third, and scoring it by its last touch would say
    otherwise.
    """

    team: str
    start_s: float
    end_s: float
    start_third: str
    reached_third: str
    ended: str
    events: int = 0
    passes: int = 0
    passes_completed: int = 0

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


def _forward_index(third: str) -> int:
    return FORWARD.index(third)


def sequences(
    log: EventLog | None,
    pitch: Pitch | None,
    attacking_ends: dict[str, str | None] | None,
    max_gap_s: float = MAX_GAP_S,
) -> list[Sequence] | None:
    """Split the event log into possessions.

    A possession ends when any of three things happen, and all three are needed:

    - **the other team does something.** The obvious one.
    - **a gap opens.** Two touches by the same team half a minute apart are two
      spells with something unrecorded between them, and merging them would
      invent a move that never happened.
    - **the ball goes dead.** A tagged log knows this and nothing else does; a
      throw-in is the start of a new possession however cleanly it is taken.

    Events with no position are skipped rather than ending the possession — the
    homography failing on one frame is not the defence winning the ball.

    Returns None when the log, the pitch or every attacking end is missing, so
    that "could not be measured" stays distinguishable from "never happened".
    """
    if log is None or pitch is None or not attacking_ends:
        return None
    if not any(attacking_ends.get(team) for team in attacking_ends):
        return None

    out: list[Sequence] = []
    current: list = []

    def flush() -> None:
        if current:
            built = _build(current, pitch, attacking_ends)
            if built is not None:
                out.append(built)
        current.clear()

    previous = None
    for event in sorted(log.events, key=lambda e: e.timestamp_s):
        if event.start_m is None:
            continue
        if attacking_ends.get(event.team) is None:
            # Nothing about this team's events can be named in thirds. Ending
            # the run rather than skipping the event, because the next event
            # from a team we *can* read is a different possession.
            flush()
            previous = None
            continue

        if previous is not None and (
            event.team != previous.team
            or event.timestamp_s - previous.timestamp_s > max_gap_s
            # Live play, then a stoppage: whatever was happening has ended, and
            # the dead-ball event is the restart that begins the next one.
            or (previous.in_play and not event.in_play)
        ):
            flush()

        current.append(event)
        previous = event

    flush()
    return out


def _build(events: list, pitch: Pitch, attacking_ends: dict) -> Sequence | None:
    team = events[0].team
    end = attacking_ends.get(team)
    if end is None:
        return None

    thirds = [zones.third(pitch, e.start_m[0], end) for e in events]
    # The furthest forward it got, which is not where it finished. A move worked
    # to the byline and pulled back to the penalty spot reached the final third.
    reached = max(thirds, key=_forward_index)

    passes = [e for e in events if e.type == 'pass']
    completed = sum(1 for p in passes if p.outcome == COMPLETED)

    return Sequence(
        team=team,
        start_s=events[0].timestamp_s,
        end_s=events[-1].timestamp_s,
        start_third=thirds[0],
        reached_third=reached,
        ended=_ending(events),
        events=len(events),
        passes=len(passes),
        passes_completed=completed,
    )


def _ending(events: list) -> str:
    """How the possession finished.

    A shot beats everything: a move that ends with a shot achieved what it was
    for, whether or not the shot was any good. Otherwise a giveaway is a loss,
    and anything else — the ball going out, the clip ending, the tracker losing
    it — is `stopped`, which is deliberately not `lost`. Scoring an unrecorded
    ending as a turnover would blame the defence for the detector's blind spots,
    the same reason `turnovers_by_third` counts only incomplete passes.
    """
    last = events[-1]
    if any(e.type == 'shot' for e in events):
        return ENDED_SHOT
    if last.type == 'pass' and last.outcome == INCOMPLETE:
        return ENDED_LOST
    return ENDED_STOPPED


@dataclass
class PhaseOfPlay:
    """What one team's possessions did, as a funnel.

    Counts rather than shares, everywhere. A funnel drawn from four possessions
    is noise, and the only way the browser can say so is to be handed the
    denominator instead of a percentage that has already thrown it away — the
    same reason `PressingCount` carries its two numbers rather than the ratio.
    """

    total: int = 0
    # Of the possessions that began in our own third, how many got out of it.
    escaped_defence: int = 0
    # Where possessions began, by third.
    started: dict[str, int] = field(default_factory=dict)
    # How many got at least as far as each third. Cumulative and therefore
    # monotonic: everything that reached the final third also reached midfield.
    reached: dict[str, int] = field(default_factory=dict)
    # How they finished.
    ended: dict[str, int] = field(default_factory=dict)
    # Passing, split by the third the pass was played *from* — where the player
    # was when they made the decision, which is the convention
    # `turnovers_by_third` already uses.
    passes: dict[str, int] = field(default_factory=dict)
    passes_completed: dict[str, int] = field(default_factory=dict)

    def share_reaching(self, third: str) -> float | None:
        """Of everything this team started, how much got this far."""
        if not self.total:
            return None
        return self.reached.get(third, 0) / self.total

    def accuracy_in(self, third: str) -> float | None:
        attempted = self.passes.get(third, 0)
        if not attempted:
            return None
        return self.passes_completed.get(third, 0) / attempted

    def out_of_defence(self) -> tuple[int, int] | None:
        """Possessions that began in our own third, and how many escaped it.

        A separate denominator on purpose. The whole-funnel share of moves
        reaching midfield is flattered by every possession that started there,
        and "can we play out from the back" is a question about the ones that
        did not.
        """
        started = self.started.get(zones.DEFENSIVE_THIRD, 0)
        if not started:
            return None
        return (started, self.escaped_defence)

    def to_json(self) -> dict:
        return {
            'total': self.total,
            'started': dict(self.started),
            'reached': dict(self.reached),
            'ended': dict(self.ended),
            'passes': dict(self.passes),
            'passes_completed': dict(self.passes_completed),
            'escaped_defence': self.escaped_defence,
        }


def phase_of_play(
    spells: list[Sequence] | None,
    log: EventLog | None,
    pitch: Pitch | None,
    team: str,
    attacking_end: str | None,
) -> PhaseOfPlay | None:
    """Roll one team's possessions up into the funnel.

    Takes the log as well as the sequences because the two halves of this
    answer count different things: the funnel counts possessions, the passing
    counts passes, and one possession contributes a dozen of those from three
    different thirds. Built in one call rather than two so there is never a
    half-filled report for a caller to read.
    """
    if spells is None:
        return None

    mine = [s for s in spells if s.team == team]
    if not mine:
        return None

    report = PhaseOfPlay(total=len(mine))
    for third in FORWARD:
        report.started[third] = 0
        report.reached[third] = 0
        report.passes[third] = 0
        report.passes_completed[third] = 0
    for ending in (ENDED_SHOT, ENDED_LOST, ENDED_STOPPED):
        report.ended[ending] = 0

    for spell in mine:
        report.started[spell.start_third] += 1
        report.ended[spell.ended] += 1
        # Cumulative: reaching the final third means having reached midfield.
        limit = _forward_index(spell.reached_third)
        for third in FORWARD[:limit + 1]:
            report.reached[third] += 1
        if (
            spell.start_third == zones.DEFENSIVE_THIRD
            and _forward_index(spell.reached_third) > 0
        ):
            report.escaped_defence += 1

    if log is not None and pitch is not None and attacking_end is not None:
        for event in log.passes(team):
            if event.start_m is None:
                continue
            third = zones.third(pitch, event.start_m[0], attacking_end)
            report.passes[third] += 1
            if event.outcome == COMPLETED:
                report.passes_completed[third] += 1

    return report
