"""What the pipeline could not see, and how much of that was its own fault.

`no_ball_s` has been in the quality block since possession was first measured,
and it is one number covering two things that could hardly be less alike:

    a throw-in, where the ball spent eleven seconds in a teenager's hands
    behind the touchline and no camera on earth was going to find it;

    twenty seconds in the middle of live football where the detector simply
    lost it.

The first is not a failure and nothing downstream is worse for it — those
frames are dead-ball time, already excluded from possession by `cv/phases.py`.
The second is a hole in the match: possession, territory, the pressing trend
and every event derived from a touch are all silent about that stretch, and
none of them say so. Added together into one figure, the first hides the
second, and a run whose tagging was thorough looks *worse* than one where
nobody tagged anything.

    What the tag log can and cannot do about it.

The roadmap asks for a fallback to the live-tagged log across long occlusions.
It cannot be a fallback in the literal sense — the log has no ball positions in
it, and inventing some would be strictly worse than the honest gap that is
there now. What the log genuinely knows is *why nobody could see the ball*, and
that is enough to sort the gaps into the ones worth worrying about and the ones
that are simply what a stoppage looks like from a camera.

So each unseen stretch is cut against the dead-ball spans and then checked
against the log, and comes out as one of three things:

    dead         inside a tagged stoppage. Not a failure; there was no
                 football happening to miss.

    accounted    live by the phase table, but a tag sits inside it or just
                 outside. Play was being interrupted around here and the log
                 says by what.

    unexplained  live football, nothing tagged anywhere near. The pipeline
                 lost the ball and nobody can say what happened.

Only the third is a defect, and it is the number this module exists to
produce.

    Why `accounted` needs a window rather than strict containment.

Two reasons, both structural rather than fussy. `phases.TAG_SLOP_S` shrinks
every dead span by two seconds at each end on purpose, so a gap that really was
a stoppage keeps a live-looking sliver at both ends. And taggers routinely tap
the restart without tapping what caused it — a `corner` with no `out_of_bounds`
before it opens no span at all, while the ball spent the previous few seconds
out of play and out of sight. A window catches both, and the cost of it is only
that a gap next to a genuine stoppage is called explained when it might have
been two things at once.

    What this is not.

It is not a measure of how good the detector is; `ball_seen_share` is that, and
it counts every isolated missed frame equally. This counts stretches, because
one continuous forty-second blackout and forty scattered one-second gaps do the
same thing to that share and completely different things to a match report. The
longest unexplained stretch is carried for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .phases import VideoClock

# How far outside an unseen stretch a tagged event may sit and still account
# for it. Wide enough to cover `phases.TAG_SLOP_S` at both ends plus a thumb
# that arrives late, narrow enough that a tag half a minute away is not allowed
# to explain a blackout it has nothing to do with.
EXPLAIN_WINDOW_S = 5.0

# The shortest stretch worth naming to a human. Everything is counted in the
# totals regardless — the three figures have to add up to the whole, or they
# are not an account of it — but a list of one-second gaps is not a list
# anybody reads.
#
# It also happens to be `ball.MAX_INTERPOLATION_GAP_S`, and not by coincidence:
# a gap shorter than that was bridged by the interpolator and never appeared as
# unseen in the first place, so in a real run this floor removes nothing. It is
# here for hand-built states, and to say out loud where the boundary is.
MIN_NAMED_S = 1.0

# A stretch this long with nothing to explain it is worth a warning rather than
# a statistic. Half a minute is long enough for a goal.
LONG_BLIND_S = 30.0

DEAD, ACCOUNTED, UNEXPLAINED = 'dead', 'accounted', 'unexplained'

# No tagged log reached the run, so nothing sorted this stretch at all. A
# separate kind rather than calling it unexplained: "we looked and found no
# reason" and "nobody looked" are different claims, and only the first is about
# the match.
UNCHECKED = 'unchecked'

KINDS = (DEAD, ACCOUNTED, UNEXPLAINED, UNCHECKED)

# The kinds nothing accounts for, which is what a warning is drawn from.
UNRESOLVED = (UNEXPLAINED, UNCHECKED)


@dataclass(frozen=True)
class BlindSpell:
    """One stretch of video with no ball located, and what explains it."""

    start_s: float
    end_s: float
    kind: str
    # The tagged types that account for it, nearest first. Empty for every kind
    # but `accounted`, and never a reason on its own — a `sub` inside a gap
    # says the tagger was busy, not that the ball was somewhere findable.
    tags: tuple[str, ...] = ()

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    def to_json(self) -> dict:
        return {
            'start_s': round(float(self.start_s), 1),
            'end_s': round(float(self.end_s), 1),
            'duration_s': round(self.duration_s, 1),
            'kind': self.kind,
            'tags': list(self.tags),
        }


@dataclass
class Blindness:
    """Every unseen stretch in a run, sorted into what it was."""

    spells: list[BlindSpell] = field(default_factory=list)

    # False when no tagged log reached the run. Everything is then a single
    # unsorted pile, and saying "none of it was explained" would be a claim
    # about the match rather than about what was checked.
    checked: bool = False

    @property
    def total_s(self) -> float:
        return sum(s.duration_s for s in self.spells)

    def seconds(self, kind) -> float:
        """Unseen seconds of one kind, or of any kind in a tuple of them."""
        wanted = (kind,) if isinstance(kind, str) else tuple(kind)
        return sum(s.duration_s for s in self.spells if s.kind in wanted)

    def named(self, kind=UNRESOLVED, min_s: float = MIN_NAMED_S):
        """The stretches long enough to be worth listing, longest first."""
        wanted = (kind,) if isinstance(kind, str) else tuple(kind)
        return sorted(
            (s for s in self.spells if s.kind in wanted and s.duration_s >= min_s),
            key=lambda s: s.duration_s,
            reverse=True,
        )

    def longest(self, kind=UNRESOLVED) -> BlindSpell | None:
        """The worst single stretch — the one worth a coach's eye.

        A run can lose the same total either as one blackout or as a hundred
        flickers, and only the first takes a passage of football with it.
        """
        worst = self.named(kind, min_s=0.0)
        return worst[0] if worst else None

    def to_json(self) -> dict:
        """The split, with the three sorted figures withheld when nothing sorted.

        Absent is not zero, the same rule `dead_ball_s` and the reconciliation
        rates already keep. Without a log there is no dead time known and no tag
        to explain anything, and three zeroes beside a large total would read as
        "we checked, and all of it was fine". `total_s` is still published,
        because the stretches themselves were measured either way.
        """
        return {
            'total_s': round(self.total_s, 1),
            'checked': self.checked,
            'dead_s': _round(self.seconds(DEAD)) if self.checked else None,
            'accounted_s': _round(self.seconds(ACCOUNTED)) if self.checked else None,
            'unexplained_s': (
                _round(self.seconds(UNEXPLAINED)) if self.checked else None
            ),
            # The list is only ever the ones nothing accounted for. The others
            # are a number; these are a place to send someone looking.
            'worst': [s.to_json() for s in self.named()[:5]],
        }


def _round(value, places: int = 1):
    return None if value is None else round(float(value), places)


def _clock_of(entry) -> float | None:
    value = entry.get('matchClockS') if hasattr(entry, 'get') else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def tagged_moments(entries, clock: VideoClock | None = None):
    """Every tagged entry as `(video_s, type)`, in order.

    Converted once, here, for the same reason `reconcile.tagged_times` does it
    once: the log runs on the match clock, everything else on video time, and a
    five-second explanation window is not going to survive a half-time interval
    applied in the wrong place.
    """
    clock = clock or VideoClock()
    out = []
    for entry in entries or ():
        kind = entry.get('type') if hasattr(entry, 'get') else None
        clock_s = _clock_of(entry)
        if clock_s is None or not kind:
            continue
        out.append((clock.to_video(clock_s), str(kind)))
    return sorted(out)


def unseen_stretches(states) -> list[tuple[float, float]]:
    """Maximal runs of frames with no ball located, as intervals.

    A run of frames owns the intervals that begin at each of them, which is why
    it ends at the timestamp of the *next* state rather than of its own last
    one. `possession.summarise` reasons about `no_ball_s` the same way and for
    the same reason: there is one fewer interval than there are frames, and
    measuring frames instead of intervals puts the total a frame above the
    window it is supposed to fit inside.
    """
    out: list[tuple[float, float]] = []
    start: float | None = None

    for index, state in enumerate(states or ()):
        if not state.ball_seen:
            if start is None:
                start = float(state.timestamp_s)
            continue
        if start is not None:
            out.append((start, float(state.timestamp_s)))
            start = None

    if start is not None and states:
        # A run that reaches the end of the clip closes at the last timestamp,
        # so it measures the intervals inside it and claims no time after the
        # video stopped.
        out.append((start, float(states[-1].timestamp_s)))

    return [(a, b) for a, b in out if b > a]


def _cut_against_dead(start_s, end_s, spans):
    """Split one interval into `(piece, is_dead)` against the dead spans.

    Splitting rather than voting. A twenty-second gap that covers a
    twelve-second stoppage and eight seconds of live play is twelve seconds
    explained and eight not — calling the whole thing one or the other on a
    majority would either forgive a real hole or invent one.
    """
    pieces = []
    cursor = start_s
    for span in spans:
        if span.end_s <= cursor:
            continue
        if span.start_s >= end_s:
            break
        if span.start_s > cursor:
            pieces.append(((cursor, span.start_s), False))
        overlap_end = min(span.end_s, end_s)
        pieces.append(((max(span.start_s, cursor), overlap_end), True))
        cursor = overlap_end
        if cursor >= end_s:
            break
    if cursor < end_s:
        pieces.append(((cursor, end_s), False))
    return [(p, dead) for p, dead in pieces if p[1] > p[0]]


def _explaining(start_s, end_s, moments, window_s):
    """Tagged types near an interval, nearest to the middle first."""
    lo, hi = start_s - window_s, end_s + window_s
    middle = (start_s + end_s) / 2.0
    near = [(t, kind) for t, kind in moments if lo <= t <= hi]
    near.sort(key=lambda pair: abs(pair[0] - middle))
    return tuple(kind for _, kind in near)


def blindness(
    states,
    phases=None,
    tag_log=None,
    clock: VideoClock | None = None,
    explain_window_s: float = EXPLAIN_WINDOW_S,
) -> Blindness:
    """Sort a run's unseen stretches into dead, accounted for, and neither.

    `phases` is a `cv.phases.PhaseTable` already shifted onto video time, and
    `tag_log` the raw log the same table was built from. Both come from the
    tagged log, so one missing means neither is available and nothing can be
    sorted — which is reported as `checked=False` rather than as a run where
    everything turned out to be unexplained.
    """
    stretches = unseen_stretches(states)
    checked = phases is not None or bool(tag_log)

    if not checked:
        return Blindness(
            spells=[BlindSpell(a, b, UNCHECKED) for a, b in stretches],
            checked=False,
        )

    spans = list(phases.spans) if phases is not None else []
    moments = tagged_moments(tag_log, clock)

    spells: list[BlindSpell] = []
    for start_s, end_s in stretches:
        for (a, b), is_dead in _cut_against_dead(start_s, end_s, spans):
            if is_dead:
                spells.append(BlindSpell(a, b, DEAD))
                continue
            tags = _explaining(a, b, moments, explain_window_s)
            spells.append(
                BlindSpell(a, b, ACCOUNTED, tags) if tags
                else BlindSpell(a, b, UNEXPLAINED)
            )

    return Blindness(spells=spells, checked=True)


def blindness_warnings(report_blindness: Blindness | None) -> list[str]:
    """The one case worth interrupting a coach for.

    A long stretch of live football that nobody saw and nothing explains. Not
    one warning per stretch: on poor footage that would be the whole warning
    list, and a list that is all one thing is a list nobody reads. The worst one
    is named and the rest are counted.
    """
    if report_blindness is None or not report_blindness.spells:
        return []

    long_ones = report_blindness.named(min_s=LONG_BLIND_S)
    if not long_ones:
        return []

    worst = long_ones[0]
    rest = len(long_ones) - 1
    also = f', and {rest} more like it' if rest else ''
    why = (
        'with nothing tagged to explain it' if report_blindness.checked
        else 'and with no tagged log there is nothing that could explain it'
    )
    return [
        f'the ball was not located for {worst.duration_s:.0f}s from '
        f'{worst.start_s:.0f}s{also}, {why}. Nothing in that stretch reached '
        f'possession, territory or the event list'
    ]
