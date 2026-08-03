"""Where the tagged log and the pipeline disagree.

Two people watched the same match: somebody on the touchline tapping a tablet,
and this pipeline. Neither is right. The tagger looks away, taps late, and gives
up on the boring stretches; the pipeline loses the ball for seconds at a time
and has never seen a real match. What is worth having is not a verdict on which
one to believe — it is a list of the moments where they say different things,
because those are the moments worth a human's twenty seconds.

    What can honestly be compared.

Almost nothing, and pretending otherwise would manufacture agreement. The tagged
vocabulary is about *why play stopped* — corners, throw-ins, fouls, cards. The
derived vocabulary is about *what a player did* — passes, carries, tackles. They
intersect on exactly one word:

    goal

which is also the one worth getting right above all others. A coach who sees a
goal that did not happen, or misses one that did, stops trusting everything else
on the page and is correct to.

The second comparison is not a shared word but a shared event. The tagger records
`out_of_bounds`, `throw_in`, `corner`, `goal_kick`; the pipeline can see the ball
cross a line, via `zones.leaves_play`. That is the independent cross-check the
roadmap asks for in Phase 9 — until now the tagged log was the pipeline's only
source of truth about stoppages, so a stoppage nobody tagged was invisible and a
mistaken tap was unquestionable.

    What this is not.

It is not ground truth, and no rate here is an accuracy. Both sides can be wrong
about the same moment in the same direction, and this would call that agreement.
The number is only useful as a trend across matches: a run of games where the two
records drift apart means something changed — the camera, the tagger, the
detector — and it is worth finding out which before the numbers are believed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import zones

# A tagger taps a goal after the ball crosses the line, usually after watching
# the celebration start. Fifteen seconds is generous on purpose: matching a real
# pair late is a much smaller error than reporting a disagreement that is really
# just a slow thumb.
GOAL_WINDOW_S = 15.0

# Restarts are tapped faster — the tagger is waiting for the ball to come back.
STOPPAGE_WINDOW_S = 10.0

# One ball rolling out is one exit. Without this, a ball dribbling along beyond
# the touchline for two seconds becomes fifty of them.
MIN_EXIT_GAP_S = 5.0

# Tagged types that mean the ball left the field of play, or came back.
EXIT_TYPES = frozenset({'out_of_bounds', 'corner', 'throw_in', 'goal_kick'})

GOAL = 'goal'

AGREED, CV_ONLY, TAG_ONLY = 'agreed', 'cv_only', 'tag_only'


@dataclass(frozen=True)
class BallExit:
    """A moment the ball's path crossed a boundary of the pitch."""

    timestamp_s: float
    boundary: str                     # 'touchline' | 'byline'


@dataclass(frozen=True)
class Disagreement:
    """One moment, and what each record said about it.

    `status` is `agreed`, `cv_only` (the pipeline saw something nobody tagged)
    or `tag_only` (somebody tagged something the pipeline missed). An agreed
    entry keeps both timestamps, because how far apart they were is itself
    worth seeing — a consistent five-second lag is a video offset problem, not a
    disagreement.
    """

    kind: str                         # 'goal' | 'exit'
    status: str
    cv_s: float | None = None
    tag_s: float | None = None
    tag_type: str | None = None
    detail: str | None = None         # the boundary, for exits

    @property
    def at_s(self) -> float:
        """When to send someone looking, whichever record has an opinion."""
        return self.cv_s if self.cv_s is not None else (self.tag_s or 0.0)

    def to_json(self) -> dict:
        return {
            'kind': self.kind,
            'status': self.status,
            'cv_s': None if self.cv_s is None else round(float(self.cv_s), 2),
            'tag_s': None if self.tag_s is None else round(float(self.tag_s), 2),
            'tag_type': self.tag_type,
            'detail': self.detail,
        }


@dataclass
class Reconciliation:
    entries: list[Disagreement] = field(default_factory=list)

    # False means ball exits were never attempted — no calibration, so there is
    # no pitch boundary to cross. Distinct from "attempted and found none".
    exits_checked: bool = False

    def of_kind(self, kind: str) -> list[Disagreement]:
        return [e for e in self.entries if e.kind == kind]

    def counts(self, kind: str) -> dict[str, int]:
        entries = self.of_kind(kind)
        return {
            status: sum(1 for e in entries if e.status == status)
            for status in (AGREED, CV_ONLY, TAG_ONLY)
        }

    def rate(self, kind: str) -> float | None:
        """Agreed over everything of that kind, or None if there was nothing.

        None rather than zero, following the rule the rest of this pipeline
        keeps: a match with no goals in it has no goal agreement rate. Zero
        would say the two records disagreed about every goal, which is a claim
        about the match rather than about how much there was to compare.
        """
        counts = self.counts(kind)
        total = sum(counts.values())
        if not total:
            return None
        return counts[AGREED] / total

    def disagreements(self, kind: str | None = None) -> list[Disagreement]:
        """The entries worth a human's time — everything except agreement."""
        return [
            e for e in self.entries
            if e.status != AGREED and (kind is None or e.kind == kind)
        ]

    def to_json(self) -> dict:
        return {
            'goal_agreement': _round(self.rate(GOAL)),
            'exit_agreement': _round(self.rate('exit')),
            'goals': self.counts(GOAL),
            'exits': self.counts('exit') if self.exits_checked else None,
            'exits_checked': self.exits_checked,
            # Agreement is a number; disagreement is a place to look. Only the
            # second needs to travel, and only for goals — a half has a hundred
            # throw-ins and nobody is scrubbing to each one.
            'disagreements': [e.to_json() for e in self.disagreements(GOAL)],
        }


def _round(value, places: int = 3):
    return None if value is None else round(float(value), places)


def _clock_of(entry) -> float | None:
    value = entry.get('matchClockS') if hasattr(entry, 'get') else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def tagged_times(entries, types, video_offset_s: float = 0.0) -> list[float]:
    """When the tagger recorded each of `types`, in video seconds.

    The log runs on the match clock and everything else here on video time. The
    conversion happens once, at the edge, exactly as `PhaseTable.shifted` does
    it — converting per comparison is how half of them end up on the wrong
    clock.
    """
    wanted = frozenset(types)
    out = []
    for entry in entries or ():
        kind = entry.get('type') if hasattr(entry, 'get') else None
        clock = _clock_of(entry)
        if clock is None or kind not in wanted:
            continue
        out.append(clock + video_offset_s)
    return sorted(out)


def pair_up(cv_times, tag_times, window_s: float):
    """Match two sorted lists of moments, nearest first.

    Greedy on the smallest gap, which for a handful of goals is both optimal
    enough and explainable — a coach can follow "these two were closest, so
    they are the same goal" in a way they cannot follow an assignment
    algorithm. Neither list is long enough for the difference to matter.

    Returns (pairs, cv_only, tag_only).
    """
    candidates = sorted(
        (
            (abs(c - t), i, j)
            for i, c in enumerate(cv_times)
            for j, t in enumerate(tag_times)
            if abs(c - t) <= window_s
        ),
    )

    used_cv: set[int] = set()
    used_tag: set[int] = set()
    pairs = []
    for _, i, j in candidates:
        if i in used_cv or j in used_tag:
            continue
        used_cv.add(i)
        used_tag.add(j)
        pairs.append((cv_times[i], tag_times[j]))

    return (
        sorted(pairs),
        [c for i, c in enumerate(cv_times) if i not in used_cv],
        [t for j, t in enumerate(tag_times) if j not in used_tag],
    )


def ball_exits(
    trajectory,
    calibration,
    min_gap_s: float = MIN_EXIT_GAP_S,
) -> list[BallExit]:
    """Every time the ball's path left the pitch, from the ball track alone.

    Only **observed** points are used. An interpolated point is a straight line
    drawn between two sightings (`cv/ball.py`), and a straight line through the
    corner flag is not evidence the ball went out — it is evidence we were not
    watching. Treating one as the other would invent stoppages in exactly the
    stretches where the pipeline saw least, which is the worst possible place
    to be confidently wrong.

    Consecutive observed points are compared, so this misses a ball that left
    and returned between two sightings. That is the honest failure: under-report
    rather than guess.
    """
    if trajectory is None or calibration is None:
        return []

    pitch = calibration.pitch
    seen = [p for p in trajectory.points if p.observed]

    exits: list[BallExit] = []
    inside = True

    for previous, current in zip(seen, seen[1:]):
        start_m = calibration.to_pitch(*previous.xy)
        end_m = calibration.to_pitch(*current.xy)
        boundary = zones.leaves_play(pitch, start_m, end_m)

        if boundary is None:
            inside = True
            continue

        # Already outside: the ball is still out, not going out again.
        if not inside:
            continue
        inside = False

        if exits and current.timestamp_s - exits[-1].timestamp_s < min_gap_s:
            continue
        exits.append(BallExit(float(current.timestamp_s), boundary))

    return exits


def reconcile(
    log=None,
    tag_entries=None,
    trajectory=None,
    calibration=None,
    video_offset_s: float = 0.0,
    goal_window_s: float = GOAL_WINDOW_S,
    stoppage_window_s: float = STOPPAGE_WINDOW_S,
) -> Reconciliation:
    """Compare what the pipeline derived against what somebody tagged.

    `log` is an `EventLog`, `tag_entries` the log as `assets/db.js::listLog`
    returns it. Both halves degrade independently: no calibration means no ball
    exits and the goals are still compared; no tagged log at all means there is
    nothing to compare and the result is empty rather than wrong.
    """
    entries: list[Disagreement] = []

    # ---- goals ----
    cv_goals = sorted(
        e.timestamp_s for e in (log.events if log else [])
        if getattr(e, 'outcome', None) == GOAL
    )
    tag_goals = tagged_times(tag_entries, {GOAL}, video_offset_s)

    agreed, cv_only, tag_only = pair_up(cv_goals, tag_goals, goal_window_s)
    for cv_s, tag_s in agreed:
        entries.append(Disagreement(GOAL, AGREED, cv_s=cv_s, tag_s=tag_s,
                                    tag_type=GOAL))
    for cv_s in cv_only:
        entries.append(Disagreement(GOAL, CV_ONLY, cv_s=cv_s))
    for tag_s in tag_only:
        entries.append(Disagreement(GOAL, TAG_ONLY, tag_s=tag_s, tag_type=GOAL))

    # ---- the ball leaving play ----
    exits = ball_exits(trajectory, calibration)
    exits_checked = trajectory is not None and calibration is not None

    if exits_checked:
        tag_exits = tagged_times(tag_entries, EXIT_TYPES, video_offset_s)
        exit_times = [e.timestamp_s for e in exits]
        boundary_at = {e.timestamp_s: e.boundary for e in exits}

        agreed, cv_only, tag_only = pair_up(
            exit_times, tag_exits, stoppage_window_s,
        )
        for cv_s, tag_s in agreed:
            entries.append(Disagreement(
                'exit', AGREED, cv_s=cv_s, tag_s=tag_s,
                detail=boundary_at.get(cv_s),
            ))
        for cv_s in cv_only:
            entries.append(Disagreement(
                'exit', CV_ONLY, cv_s=cv_s, detail=boundary_at.get(cv_s),
            ))
        for tag_s in tag_only:
            entries.append(Disagreement('exit', TAG_ONLY, tag_s=tag_s))

    entries.sort(key=lambda e: e.at_s)
    return Reconciliation(entries=entries, exits_checked=exits_checked)


def warnings_for(reconciliation: Reconciliation, clock=None) -> list[str]:
    """The disagreements loud enough to sit at the top of a report.

    Goals only. A disputed throw-in is a rounding error in a possession figure;
    a disputed goal is the difference between a report a coach believes and one
    they close. `clock` formats a timestamp — passed in so this module does not
    have to own a house style for time.
    """
    fmt = clock or (lambda s: f'{int(s) // 60}:{int(s) % 60:02d}')
    notes = []

    for entry in reconciliation.disagreements(GOAL):
        if entry.status == TAG_ONLY:
            notes.append(
                f'a goal was tagged at {fmt(entry.tag_s)} that the video '
                'analysis did not find'
            )
        else:
            notes.append(
                f'the video analysis found a goal at {fmt(entry.cv_s)} that '
                'nobody tagged'
            )
    return notes
