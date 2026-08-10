"""Where a run's seconds went, and whether it could have kept up.

The pipeline has always reported `processing_s`. One number, and it answers the
wrong question. "This took eleven minutes" cannot tell anyone whether a
half-time report would have existed, and it cannot tell anyone what to change to
make one exist — a smaller model, a lower sample rate and a shorter window are
three different fixes and the total is the same shape under all of them.

    The question this exists to answer.

A half-time report has to be ready at half-time. That is the only deadline in
this project, and it is a hard one: a report handed over ten minutes into the
second half is a report about a match the coach is already losing differently.

The number that decides it is the **real-time factor** — seconds of processing
per second of footage. Below 1 the run keeps up with the football and could in
principle be fed frames as they arrive. At or above 1 it falls behind, and the
useful form of that is not a ratio but a length of time: *how late the report
would be.* `lag_s` is that, and it is the figure worth putting in front of a
person.

    Two honesties this module is built around.

**The remainder is a line, not a rounding error.** Stages are timed
individually and never add up to the whole, because setup, imports, teardown
and everything nobody thought to wrap fall outside them. A breakdown that
silently omits its own gap invites the reader to optimise the largest named
stage while a third of the run sits somewhere else. `unaccounted_s` is reported
alongside the stages it is missing from.

**A clip does not scale onto a half by multiplication.** Measuring this
against a real run made that unmissable. Six seconds of synthetic footage, three
runs back to back on one laptop:

    cold   8.86s total   1.86s loading the detector   6.99s detecting
    warm   2.62s total   0.04s loading                2.57s detecting
    warm   2.02s total   0.04s loading                1.96s detecting

The naive ratio on the cold run is 1.48x — "cannot keep up, abandon the idea".
The same pipeline on the same clip once warm is 0.33x, comfortably live. A 4.5x
spread, and it decides the verdict, and none of it is about the football.

Loading is paid once whether the clip is six seconds or a whole half, so it does
not belong in a per-second rate at all. The first detection pass is expensive
for the same reason — weights land lazily and the first inference builds the
graph — which is why a projection from a *short* clip runs pessimistic, and why
`speed_report` measures over a minute rather than a few seconds.

So every stage declares how its cost grows, and the projection treats the three
kinds differently:

  - `LINEAR` — decoding, inference, possession, movement. Scales with footage.
  - `FIXED` — loading the model, reading a calibration. Paid once, at any length.
  - `SUPERLINEAR` — `identity.merge_tracks`, a quadratic pass over the tracks,
    and the tracks grow with the footage too. Scaled linearly like the rest,
    which is known to *understate* it, so any projection containing a
    meaningful one says **at least** this late rather than this late.

The unaccounted remainder is projected as linear. It is the pessimistic
assumption, and on a deadline the pessimistic assumption is the right default:
being wrong in the optimistic direction about the half-time whistle is the one
way to be wrong that costs a match.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field

# A half, for projecting a clip's cost onto a real one. Senior football; a
# shorter age-group half scales down proportionally, which is why the callers
# take it as an argument rather than reading this.
HALF_S = 45 * 60.0

# Below this share of the total, a non-linear stage is not worth qualifying the
# whole projection over. A quadratic stage taking half a percent of the run does
# not change whether the report arrives.
NONLINEAR_NOTICE = 0.02


# How a stage's cost grows with the length of the footage.
LINEAR = 'linear'                # decode, inference, possession, movement
FIXED = 'fixed'                  # loading a model, reading a calibration
SUPERLINEAR = 'superlinear'      # merge_tracks: quadratic in tracks


@dataclass
class Stage:
    """One named piece of the work, and how its cost grows with the footage."""

    name: str
    seconds: float = 0.0
    scaling: str = LINEAR

    @property
    def linear(self) -> bool:
        return self.scaling == LINEAR

    def to_json(self) -> dict:
        return {
            'name': self.name,
            'seconds': round(self.seconds, 3),
            'scaling': self.scaling,
        }


@dataclass
class Timings:
    """A stopwatch per stage, plus what the whole run cost.

    Stages accumulate by name, so a stage entered twice is one line rather than
    two — the reader wants to know what detection cost, not how many times the
    loop was re-entered.
    """

    stages: list[Stage] = field(default_factory=list)
    total_s: float = 0.0

    def add(self, name: str, seconds: float, *, scaling: str = LINEAR) -> None:
        for stage in self.stages:
            if stage.name == name:
                stage.seconds += seconds
                # The worst thing inside the name wins. A name covering both a
                # fixed load and a quadratic pass grows quadratically.
                stage.scaling = _worst(stage.scaling, scaling)
                return
        self.stages.append(Stage(name=name, seconds=seconds, scaling=scaling))

    @contextmanager
    def stage(self, name: str, *, scaling: str = LINEAR):
        """Time a block. Records the elapsed time even if the block raises.

        Deliberately: a run that fell over after eight minutes of detection
        spent eight minutes on detection, and that is exactly the run somebody
        wants the numbers from.
        """
        started = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, time.perf_counter() - started, scaling=scaling)

    @property
    def accounted_s(self) -> float:
        return sum(stage.seconds for stage in self.stages)

    @property
    def unaccounted_s(self) -> float:
        """The gap between the stages and the whole. Never negative.

        A negative would mean the stages overlapped or the total was never set,
        and reporting a negative remainder as if it were time saved is worse
        than reporting nothing.
        """
        return max(0.0, self.total_s - self.accounted_s)

    def ranked(self) -> list[Stage]:
        """Stages, most expensive first — the order anyone reads them in."""
        return sorted(self.stages, key=lambda s: s.seconds, reverse=True)

    @property
    def fixed_s(self) -> float:
        """The part of the run that is paid once, whatever the footage's length."""
        return sum(s.seconds for s in self.stages if s.scaling == FIXED)

    @property
    def scaling_s(self) -> float:
        """The part that grows with the footage, remainder included.

        The unaccounted gap is counted here rather than as fixed. Nobody
        measured it, and on a deadline the unmeasured thing is assumed to be the
        expensive kind.
        """
        return max(0.0, self.total_s - self.fixed_s)

    def realtime_factor(self, footage_s: float | None) -> float | None:
        """Seconds of work per second of football, over the part that scales.

        Deliberately not `total_s / footage_s`. On a short clip the model load
        dwarfs the football and the naive ratio measures the startup, not the
        pipeline — the first real run came out at 3.5x when the work itself was
        1.4x and the other 2.1 was loading YOLO once.

        `None` rather than 0 when there is no footage to divide by. A run over
        an empty window did not achieve a factor of zero; it has no factor.
        """
        if not footage_s or footage_s <= 0 or self.total_s <= 0:
            return None
        return self.scaling_s / footage_s

    def keeps_up(self, footage_s: float | None) -> bool | None:
        """Whether frames could be fed in as fast as they arrive.

        Asked of the scaling part only, because the fixed part is paid before
        kick-off. A pipeline that loads for thirty seconds and then runs at 0.8x
        keeps up with a half; it just has to be started half a minute early.
        """
        factor = self.realtime_factor(footage_s)
        return None if factor is None else factor < 1.0

    def project_s(self, footage_s: float | None, half_s: float = HALF_S) -> float | None:
        """What this run would cost over a whole half.

        Fixed once, plus the scaling part stretched to the new length. This is
        the arithmetic the whole module exists for, and it is the piece that
        multiplying the total would have got wrong by an order of magnitude.
        """
        factor = self.realtime_factor(footage_s)
        if factor is None:
            return None
        return self.fixed_s + factor * half_s

    def lag_s(self, footage_s: float | None, half_s: float = HALF_S) -> float | None:
        """How far behind a live half this run would finish.

        Zero when it keeps up — a real answer, unlike the factor, because a run
        that keeps up is exactly zero minutes late. The fixed cost does not
        count towards being late: loading the model happens before kick-off, not
        during the half. Anything above zero is a floor: see `optimistic`.
        """
        factor = self.realtime_factor(footage_s)
        if factor is None:
            return None
        return max(0.0, (factor - 1.0) * half_s)

    def optimistic(self) -> bool:
        """Whether a projection from this run understates a full half.

        True when a stage known to grow faster than the footage took enough of
        the run to matter. The lag is then a lower bound. Fixed stages do not
        count: they make a projection *pessimistic*, and that is already handled
        by keeping them out of the factor.
        """
        if self.total_s <= 0:
            return False
        return any(
            stage.scaling == SUPERLINEAR
            and stage.seconds / self.total_s >= NONLINEAR_NOTICE
            for stage in self.stages
        )

    def to_json(self, footage_s: float | None = None, half_s: float = HALF_S) -> dict:
        return {
            'total_s': round(self.total_s, 3),
            'stages': [stage.to_json() for stage in self.ranked()],
            # Named rather than left to be inferred from the difference. The
            # gap is where the reader's optimisation would have been wasted.
            'unaccounted_s': round(self.unaccounted_s, 3),
            # Split out because they project differently and the difference is
            # the whole point. `fixed_s` is paid before kick-off; `scaling_s` is
            # what has to fit inside the half.
            'fixed_s': round(self.fixed_s, 3),
            'scaling_s': round(self.scaling_s, 3),
            'footage_s': round(footage_s, 1) if footage_s else None,
            'realtime_factor': _round(self.realtime_factor(footage_s), 3),
            'keeps_up': self.keeps_up(footage_s),
            'lag_s': _round(self.lag_s(footage_s, half_s), 1),
            'projected_half_s': _round(self.project_s(footage_s, half_s), 1),
            'half_s': round(half_s, 1),
            # True means `lag_s` is a floor. A consumer that prints it as a
            # flat number is printing the best case.
            'optimistic': self.optimistic(),
        }


def _round(value, digits):
    return None if value is None else round(value, digits)


# Worst first: a name covering several kinds of work grows like the worst of
# them, because that is the one that decides whether the deadline is met.
_SEVERITY = {SUPERLINEAR: 2, LINEAR: 1, FIXED: 0}


def _worst(a: str, b: str) -> str:
    return a if _SEVERITY.get(a, 1) >= _SEVERITY.get(b, 1) else b


def fastest_rate_that_fits(measured: dict[float, float], budget: float = 1.0):
    """The highest sample rate whose real-time factor stays inside the budget.

    `measured` is `{sample_fps: realtime_factor}`. Returns the rate, or `None`
    when even the slowest one measured is too slow — which is a different
    finding from "run at the lowest rate" and must not be rounded into it.

    The budget defaults to 1.0, meaning "exactly keeps up", which in practice is
    not a budget anyone should ship on: a run at 0.98 has two percent of a half
    in hand for every hiccup in the whole day. Callers are expected to ask for
    something below 1.
    """
    fits = [rate for rate, factor in measured.items() if factor is not None and factor <= budget]
    return max(fits) if fits else None
