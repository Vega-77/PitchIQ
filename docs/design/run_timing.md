# `pitchiq/run_timing.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Where a run's seconds went, and whether it could have kept up.

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
