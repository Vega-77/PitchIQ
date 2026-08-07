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

    def on_video(self, clock: VideoClock) -> PhaseTable:
        """The same spans on video time.

        Converting the table once is less error-prone than converting every
        timestamp that meets it. `clock` rather than a bare offset because the
        offset alone stops being right at half-time — see VideoClock.
        """
        return PhaseTable(spans=[
            DeadSpan(
                start_s=clock.to_video(s.start_s),
                end_s=clock.to_video(s.end_s),
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


# ------------------------------------------------------------ which half it is
#
# `MatchOrientation.attacking_end` flips the whole pitch on the period, because
# teams change ends at the break. Everything positional rests on it: the shot
# maps, the heatmaps' reading, the pressing zone, the territory split, the
# turnovers by third, the passing network, and — through `attacking_end` —
# every xG figure.
#
# Until now that came from one string, defaulting to `first_half`. Process a
# second half and forget the flag and every one of those is mirrored, and the
# output looks exactly as plausible as a correct one. There is no downstream
# check that could catch it: a shot map at the wrong end is a shot map.
#
# The tagger has been tapping `kickoff_1st`, `kickoff_2nd`, `halftime` and
# `full_time` since Phase 3 and nothing has ever read them for this. They are a
# record of which half was being played and when, which is exactly the question,
# so the flag becomes a fallback rather than the source.

FIRST_HALF = 'first_half'
SECOND_HALF = 'second_half'

# The taps that begin a period. `halftime` and `full_time` (ENDS_PLAY) close one
# without opening another, which is what makes a window that runs past the break
# detectable rather than merely unattributed.
PERIOD_STARTS = {'kickoff_1st': FIRST_HALF, 'kickoff_2nd': SECOND_HALF}


@dataclass(frozen=True)
class PeriodSpan:
    period: str
    start_s: float
    # None means the log never closed it — no `halftime` tap, or the log simply
    # ends. Open-ended rather than guessed at: assuming a half is 45 minutes
    # would invent a boundary, and the boundary is the whole point here.
    end_s: float | None = None

    def covers(self, clock_s: float) -> bool:
        return clock_s >= self.start_s and (self.end_s is None or clock_s < self.end_s)

    def to_json(self) -> dict:
        return {
            'period': self.period,
            'start_s': round(float(self.start_s), 2),
            'end_s': None if self.end_s is None else round(float(self.end_s), 2),
        }


@dataclass
class PeriodTable:
    """Which half was being played, when. Empty when nobody tapped a kickoff."""

    spans: list[PeriodSpan] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.spans = sorted(self.spans, key=lambda s: s.start_s)

    def __bool__(self) -> bool:
        return bool(self.spans)

    def at(self, clock_s: float) -> str | None:
        """The period at an instant, or None where the log does not say.

        None before the first kickoff and inside the break, and both of those
        are real answers rather than gaps to be filled. A shot in the warm-up is
        not first-half football, and neither team is attacking anything at all
        while the sides are swapping over.
        """
        for span in self.spans:
            if span.covers(clock_s):
                return span.period
        return None

    def covering(self, start_s: float, end_s: float) -> list[str]:
        """Every period a window touches, in order, without repeats.

        More than one is the loud case: a window that runs through the break has
        no single answer to "which way were they attacking", so nothing that
        depends on it is right for the whole of it.
        """
        out: list[str] = []
        for span in self.spans:
            if span.start_s >= end_s:
                break
            if span.end_s is not None and span.end_s <= start_s:
                continue
            if span.period not in out:
                out.append(span.period)
        return out

    def dominant(self, start_s: float, end_s: float) -> str | None:
        """The period holding most of a window.

        Only meaningful when a window straddles the break, and then it is the
        least wrong single answer rather than a right one — five minutes of the
        first half in front of forty of the second should be read as a second
        half, and the caller still has to say out loud that part of it is
        mirrored. Ties go to whichever came first, which is arbitrary and
        cannot matter: at a tie both answers are equally wrong.
        """
        best, longest = None, 0.0
        for span in self.spans:
            finish = end_s if span.end_s is None else min(span.end_s, end_s)
            overlap = finish - max(span.start_s, start_s)
            if overlap > longest:
                best, longest = span.period, overlap
        return best

    def on_video(self, clock: VideoClock) -> PeriodTable:
        """The same spans on video time. Same reasoning as `PhaseTable`.

        Worth noticing what the two-anchor clock does here: the first half now
        ends where the whistle went rather than where the second half starts, so
        the interval becomes a gap between the spans instead of being absorbed
        into one of them. `PeriodTable.at` already answers None inside it.
        """
        return PeriodTable(spans=[
            PeriodSpan(
                period=s.period,
                start_s=clock.to_video(s.start_s),
                end_s=None if s.end_s is None else clock.to_video_end(s.end_s),
            )
            for s in self.spans
        ])

    def to_json(self) -> dict:
        return {'spans': [s.to_json() for s in self.spans]}


# ------------------------------------------------- the two clocks, and the break
#
# The tag log runs on a match clock that **stops at half-time**: the tablet
# freezes it at the break and restarts it from the same second (`advancePeriod`
# in live-tagging/tagging.js). The video runs on a file position, which freezes
# for nothing.
#
# So adding one offset to every tagged reading is exact for the first half and
# wrong for the whole of the second by however long the interval ran — ten to
# fifteen minutes, typically. Everything that meets a tagged timestamp inherits
# that: the dead spans land in the wrong place, every tagged goal misses its
# reconciliation window by two orders of magnitude more than the window, and
# `PeriodTable` — which decides which way each team is attacking, and through
# that mirrors every shot map, heatmap and xG figure — is shifted across the
# very boundary it exists to find.
#
# The clock the second half restarts on is in the log already: it is the start
# of the second-half span. The position of that moment in the video is not, and
# nothing can derive it, so it is supplied — the same way the kick-off offset
# always has been.


@dataclass(frozen=True)
class VideoClock:
    """Match-clock seconds to video seconds, piecewise across the break.

    With only `video_offset_s` this is the plain addition it has always been,
    and `knows_break` says so. Two numbers that imply a negative interval are
    refused rather than used: honouring them would make the map run backwards,
    and a non-monotonic clock breaks things that a merely shifted one does not.
    """

    video_offset_s: float = 0.0
    # Where the second half kicks off in the footage.
    second_half_video_s: float | None = None
    # What the tablet's clock read at that moment. Taken from the log, not
    # typed by anyone — see `VideoClock.from_periods`.
    second_half_clock_s: float | None = None

    @property
    def knows_break(self) -> bool:
        if self.second_half_video_s is None or self.second_half_clock_s is None:
            return False
        if self.second_half_clock_s <= 0:
            return False
        return self.break_s >= 0

    @property
    def break_s(self) -> float:
        """How long the footage spent on the interval. Meaningless unless
        `knows_break`; negative there is exactly what makes it meaningless."""
        if self.second_half_video_s is None or self.second_half_clock_s is None:
            return 0.0
        return (
            (self.second_half_video_s - self.video_offset_s)
            - self.second_half_clock_s
        )

    def to_video(self, clock_s: float) -> float:
        """Where in the footage the clock read this.

        The restart's own second belongs to the second half: `halftime` and
        `kickoff_2nd` share a reading, and the restart is the position worth
        having.
        """
        if self.knows_break and clock_s >= self.second_half_clock_s:
            return self.second_half_video_s + (clock_s - self.second_half_clock_s)
        return clock_s + self.video_offset_s

    def to_video_end(self, clock_s: float) -> float:
        """The same conversion, resolving the shared second the other way.

        The whistle and the restart are one reading on the clock and two
        positions in the footage. `to_video` sends that reading to the restart,
        which is what a tag placed at it means. The *end* of the first half
        means the other one: it is where play stopped, not where it began
        again. Without this distinction the first half's span swallows the
        interval, and a frame of somebody eating an orange is reported as
        first-half football — drawn at whichever end of the pitch that implies.
        """
        if self.knows_break and clock_s <= self.second_half_clock_s:
            return clock_s + self.video_offset_s
        return self.to_video(clock_s)

    def to_clock(self, video_s: float) -> float:
        """What the clock read at a position in the footage.

        Inside the interval this is the frozen reading — which is the truth: the
        clock really did show that second for the whole break.
        """
        if not self.knows_break:
            return video_s - self.video_offset_s
        if video_s >= self.second_half_video_s:
            return self.second_half_clock_s + (video_s - self.second_half_video_s)
        return min(video_s - self.video_offset_s, self.second_half_clock_s)

    @classmethod
    def from_periods(
        cls,
        periods: PeriodTable,
        video_offset_s: float = 0.0,
        second_half_video_s: float | None = None,
    ) -> VideoClock:
        """Build one from the log's own periods plus the one fact it lacks.

        Half of the answer is already in the tag log — the second half's span
        starts on the clock reading the break froze at — so only the video
        position has to come from outside. Passing that alone, with no second
        half in the log to pin it to, leaves the clock in its one-anchor state
        rather than anchoring it to a guess.
        """
        restart = next(
            (s.start_s for s in periods.spans if s.period == SECOND_HALF), None
        )
        return cls(
            video_offset_s=video_offset_s,
            second_half_video_s=second_half_video_s,
            second_half_clock_s=restart,
        )


def periods_from_log(entries) -> PeriodTable:
    """The halves, from the kickoff and end-of-period taps.

    A second kickoff of the same kind is ignored rather than treated as a
    restart — a tagger who taps `kickoff_2nd` twice has tapped twice, not
    played two second halves — and a kickoff while a period is open closes the
    one before it, which is what a missing `halftime` tap looks like.
    """
    usable = []
    for entry in entries or ():
        clock = _clock_of(entry)
        kind = entry.get('type') if hasattr(entry, 'get') else None
        if clock is None or not isinstance(kind, str):
            continue
        if kind in PERIOD_STARTS or kind in ENDS_PLAY:
            usable.append((clock, kind))
    usable.sort(key=lambda pair: pair[0])

    spans: list[PeriodSpan] = []
    open_period: str | None = None
    open_at = 0.0

    def close(at: float) -> None:
        nonlocal open_period
        if open_period is not None:
            spans.append(PeriodSpan(open_period, open_at, at))
            open_period = None

    for clock, kind in usable:
        if kind in ENDS_PLAY:
            close(clock)
            continue
        period = PERIOD_STARTS[kind]
        if period == open_period:
            continue
        close(clock)
        open_period, open_at = period, clock

    if open_period is not None:
        spans.append(PeriodSpan(open_period, open_at, None))

    return PeriodTable(spans=spans)


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
