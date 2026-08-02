"""When the ball was actually in play.

Possession is measured as "time with a clear holder", and a player standing over
the ball waiting to take a throw-in is a very clear holder. So is a goalkeeper
holding it before a goal kick, and so is whoever is arguing with the referee.
Nothing in the pipeline has ever known the difference, which means every
possession figure it has produced includes the stoppages.

The information needed to fix that already exists and was never read. Somebody
sits on the touchline tapping `out_of_bounds`, `throw_in`, `corner`,
`goal_kick`, `free_kick`, `foul` and `offside` into the live tagger, each with a
match clock. That is a record of when the ball went dead and when it came back.
This module is the first thing under `cv/` to read it.

    Opens and closes.

A stoppage event opens a dead span and the next restart closes it. A second
stoppage while already dead extends nothing — a foul and the card that follows
it are one interruption, not two.

Substitutions are deliberately ignored. A sub is made during a stoppage that was
already tagged by whatever caused it, so treating it as an opener would mostly
duplicate a span that exists; and because no restart is tagged afterwards, the
duplicate would run until it timed out and delete two minutes of football that
happened.

    Two failure modes, and which one to prefer.

Tags are placed when a person notices, not when the whistle blows. Every
boundary here therefore carries a second or two of that person's reaction time,
in whichever direction their attention happened to drift.

Given that, a span is always **shrunk** and never grown. Calling live football
dead deletes possession that really happened and nothing downstream can tell;
calling dead time live leaves the old, known behaviour. The first error is
invisible and the second is merely imprecise, so the slop is spent in the
direction of the second.

The same asymmetry sets `MAX_DEAD_S`. A restart that nobody tagged would
otherwise leave a span open until the half ended.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

# Play stops.
OPENS_DEAD = frozenset({'out_of_bounds', 'foul', 'offside', 'card', 'goal'})

# Play restarts.
CLOSES_DEAD = frozenset({
    'corner', 'throw_in', 'goal_kick', 'free_kick', 'kickoff_1st', 'kickoff_2nd',
})

# Play is over for the period; anything still open closes here.
ENDS_PLAY = frozenset({'halftime', 'full_time'})

# The longest a stoppage may run before it is assumed the restart went untagged.
MAX_DEAD_S = 120.0

# Openers whose restart is never tagged at all, and so need a tighter cap.
# Nothing marks the kickoff that follows a goal — `kickoff_1st` and
# `kickoff_2nd` are period markers, not restarts — so without this every goal
# would delete the two minutes after it.
DEAD_CAP_S = {'goal': 60.0}

# How much of each end of a span to give back, in seconds. See the docstring:
# this is spent on being wrong in the recoverable direction.
TAG_SLOP_S = 2.0


@dataclass(frozen=True)
class DeadSpan:
    start_s: float
    end_s: float
    opened_by: str
    # None means nothing closed it and it hit its cap — worth being able to
    # count, because a run full of them means the tagging had gaps rather than
    # that the match did.
    closed_by: str | None = None

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    @property
    def timed_out(self) -> bool:
        return self.closed_by is None

    def to_json(self) -> dict:
        return {
            'start_s': round(float(self.start_s), 2),
            'end_s': round(float(self.end_s), 2),
            'opened_by': self.opened_by,
            'closed_by': self.closed_by,
        }


@dataclass
class PhaseTable:
    """Every stretch where the ball was out of play, in match-clock seconds."""

    spans: list[DeadSpan] = field(default_factory=list)

    # Parallel start/end arrays, so the per-frame `is_live` is a binary search
    # rather than a scan. Possession asks this question once per frame.
    _starts: list[float] = field(default_factory=list, repr=False, compare=False)
    _ends: list[float] = field(default_factory=list, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.spans = sorted(self.spans, key=lambda s: s.start_s)
        self._starts = [s.start_s for s in self.spans]
        self._ends = [s.end_s for s in self.spans]

    @property
    def dead_s(self) -> float:
        return sum(s.duration_s for s in self.spans)

    @property
    def timed_out(self) -> int:
        return sum(1 for s in self.spans if s.timed_out)

    def is_live(self, clock_s: float) -> bool:
        i = bisect_right(self._starts, clock_s) - 1
        if i < 0:
            return True
        return not (self._starts[i] <= clock_s < self._ends[i])

    def dead_between(self, start_s: float, end_s: float) -> float:
        """How much of a window was dead."""
        total = 0.0
        for span in self.spans:
            if span.end_s <= start_s:
                continue
            if span.start_s >= end_s:
                break
            total += min(span.end_s, end_s) - max(span.start_s, start_s)
        return max(0.0, total)

    def live_share(self, start_s: float, end_s: float) -> float:
        window = end_s - start_s
        if window <= 0:
            return 0.0
        return max(0.0, 1.0 - self.dead_between(start_s, end_s) / window)

    def shifted(self, offset_s: float) -> PhaseTable:
        """The same spans on another clock.

        The log runs on the match clock and the pipeline on video time, related
        by the offset already stored on the match. Converting the table once is
        less error-prone than converting every timestamp that meets it.
        """
        return PhaseTable(spans=[
            DeadSpan(
                start_s=s.start_s + offset_s, end_s=s.end_s + offset_s,
                opened_by=s.opened_by, closed_by=s.closed_by,
            )
            for s in self.spans
        ])

    def to_json(self) -> dict:
        return {
            'dead_s': round(self.dead_s, 1),
            'spans': [s.to_json() for s in self.spans],
            'timed_out': self.timed_out,
        }


def _clock_of(entry) -> float | None:
    value = entry.get('matchClockS') if hasattr(entry, 'get') else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def phases_from_log(
    entries,
    *,
    max_dead_s: float = MAX_DEAD_S,
    slop_s: float = TAG_SLOP_S,
) -> PhaseTable:
    """Turn a tagged match log into the stretches where the ball was dead.

    `entries` is the log as `assets/db.js::listLog` returns it — dicts with a
    `type` and a `matchClockS`. Anything without both is skipped rather than
    guessed at.
    """
    usable = []
    for entry in entries or ():
        clock = _clock_of(entry)
        kind = entry.get('type') if hasattr(entry, 'get') else None
        if clock is None or not isinstance(kind, str):
            continue
        usable.append((clock, kind))

    # Stable within a timestamp, so a foul and its card on the same second
    # resolve in the order they were tapped.
    usable.sort(key=lambda pair: pair[0])

    spans: list[DeadSpan] = []
    open_at: float | None = None
    open_by = ''

    def close(at: float, by: str | None) -> None:
        nonlocal open_at, open_by
        cap = DEAD_CAP_S.get(open_by, max_dead_s)
        limit = open_at + cap
        if at > limit:
            at, by = limit, None          # nothing closed it in time

        # Shrunk, never grown. A span shorter than the slop it would give back
        # is dropped entirely: it is inside the tagger's reaction time, so
        # there is nothing here we can honestly claim to know.
        start, end = open_at + slop_s, at - slop_s
        if end > start:
            spans.append(DeadSpan(start, end, open_by, by))
        open_at, open_by = None, ''

    def expire_if_due(now: float) -> None:
        """Time out an open span that the clock has already run past.

        Without this, a stoppage whose restart went untagged swallows every
        later event: the next opener sees a span already open and declines to
        start a second one, so the rest of the half collapses into the first
        interruption.
        """
        if open_at is None:
            return
        if now > open_at + DEAD_CAP_S.get(open_by, max_dead_s):
            close(float('inf'), None)

    for clock, kind in usable:
        expire_if_due(clock)

        if kind in ENDS_PLAY:
            if open_at is not None:
                close(clock, kind)
        elif kind in OPENS_DEAD:
            # Already dead: a foul and the card that follows it are one
            # interruption. Extending or restarting would double-count it.
            if open_at is None:
                open_at, open_by = clock, kind
        elif kind in CLOSES_DEAD:
            # A restart with no stoppage in front of it. Nothing to close, and
            # inventing a span backwards from it would be a guess about when
            # the ball went out.
            if open_at is not None:
                close(clock, kind)

    if open_at is not None:
        close(float('inf'), None)

    return PhaseTable(spans=spans)
