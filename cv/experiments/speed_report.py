"""Can this machine keep up with a live half, and at what sample rate?

There is one deadline in this project and it is the half-time whistle. A report
handed over ten minutes into the second half describes a match the coach is
already losing differently. Everything else here can be slow.

`track_report` has measured a speed for a while, but only for the tracking pass
and expressed the flattering way round — "12x real time" on a fifteen-second
clip. This runs the *whole* pipeline, at several sample rates, and reports the
number that actually decides the question: seconds of work per second of
football, and how late the report would be at each rate.

    python -m cv.experiments.speed_report "match.mp4" --seconds 60
    python -m cv.experiments.speed_report "match.mp4" --rates 6 12 30 --budget 0.7
    python -m cv.experiments.speed_report "match.mp4" --calibration home.calib.json

    What the answer is a statement about.

This machine, this footage, this model, today. It is not a property of the
pipeline and should not be written into the roadmap as one — a laptop on battery
and the same laptop plugged in are different answers. Run it on the machine that
will actually be at the side of the field.

    Measure over a minute, not a few seconds.

Loading the model and the first inference are one-time costs. `cv/timing.py`
keeps the load out of the rate, but the warm-up hiding inside the first
detection pass cannot be separated out — measured on a six-second clip it was
most of the run, and the same pipeline once warm was four times faster. A short
measurement therefore reads slow, which is the safe direction to be wrong but
not a useful one. Sixty seconds is the shortest window worth quoting.

The lag is still a floor: `identity.merge_tracks` is quadratic in the tracks and
the tracks grow with the footage, so a run containing a meaningful one says *at
least* this late. See cv/timing.py.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cv.pipeline import analyse_match
from cv.timing import FIXED, HALF_S, SUPERLINEAR, fastest_rate_that_fits

# The rates worth asking about. 30 is a phone export run at full rate, 15 a
# camcorder at full rate, and 6 is `metrics.min_sample_hz()` — the floor below
# which the smoothing window has fewer than three samples and the burst count
# starts reporting wobble rather than football.
DEFAULT_RATES = (6.0, 12.0, 30.0)

# A budget of 1.0 means finishing exactly at the whistle with nothing in hand,
# which is not something to plan a match day around. Two thirds leaves room for
# a slow disk, a dropped frame batch and someone's laptop deciding to update.
DEFAULT_BUDGET = 0.67

# A throwaway run before the measured ones. Long enough to land the weights and
# build the inference graph, short enough not to be the bulk of the wait.
WARMUP_S = 3.0


def measure(video: Path, rate: float, seconds: float, calibration: Path | None):
    """One run, and what it says about a whole half."""
    report = analyse_match(
        video,
        calibration_path=calibration,
        start_s=0.0,
        end_s=seconds,
        sample_fps=rate,
    )
    timings = report.timings
    return {
        'asked': rate,
        # The rate that actually ran. A 30fps source asked for 12 gets 15, and
        # quoting the request would describe a run nobody did.
        'ran': report.sample_fps,
        'footage_s': report.duration_s,
        'total_s': timings.total_s if timings else report.processing_s,
        'factor': timings.realtime_factor(report.duration_s) if timings else None,
        'timings': timings,
    }


def print_run(row: dict, half_s: float) -> None:
    factor = row['factor']
    timings = row['timings']
    lag = timings.lag_s(row['footage_s'], half_s) if timings else None
    fixed = timings.fixed_s if timings else 0.0

    if factor is None:
        verdict = 'no footage to measure against'
    elif factor < 1.0:
        verdict = f"keeps up, {1.0 - factor:.0%} in hand"
    else:
        floor = 'at least ' if timings and timings.optimistic() else ''
        verdict = f"{floor}{lag / 60.0:.0f} min late at the break"

    print(
        f"  {row['ran']:>5.1f} Hz  {row['total_s']:>7.1f}s  {fixed:>5.1f}s"
        f"  {factor if factor is not None else float('nan'):>7.2f}x  {verdict}"
    )


def print_stages(timings, label: str) -> None:
    print(f"\n  where the seconds went at {label}")
    print("  " + "-" * 58)
    total = timings.total_s or 1.0
    marks = {
        FIXED: '   (paid once, at any length)',
        SUPERLINEAR: '   (grows faster than the footage)',
    }
    for stage in timings.ranked():
        mark = marks.get(stage.scaling, '')
        print(f"  {stage.name:<32} {stage.seconds:>7.1f}s {stage.seconds / total:>6.0%}{mark}")
    # Always printed, including at zero. A breakdown that omits its own gap
    # invites optimising the largest named stage while a third of the run sits
    # somewhere nobody wrapped.
    gap = timings.unaccounted_s
    print(f"  {'everything else':<32} {gap:>7.1f}s {gap / total:>6.0%}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='speed_report',
        description='Whether this machine could produce a half-time report in time.',
    )
    parser.add_argument('video', type=Path)
    parser.add_argument('--calibration', type=Path, default=None)
    parser.add_argument(
        '--seconds', type=float, default=60.0,
        help='how much footage to measure over. Longer is a better projection, '
             'because the quadratic stages have more to be quadratic about.',
    )
    parser.add_argument('--rates', type=float, nargs='+', default=list(DEFAULT_RATES))
    parser.add_argument(
        '--budget', type=float, default=DEFAULT_BUDGET,
        help='the real-time factor to stay under. 1.0 finishes exactly at the '
             'whistle with nothing in hand.',
    )
    parser.add_argument(
        '--half-minutes', type=float, default=HALF_S / 60.0,
        help='the half this is projected onto. 45 for senior football.',
    )
    args = parser.parse_args(argv)

    half_s = args.half_minutes * 60.0

    # ASCII only from here down. This is run from a Windows terminal, where the
    # console codepage turns an em-dash into a replacement character — the first
    # run of this printed "clip70.mp4 <?> 60s of footage per run".
    print(f"\n{args.video.name} - {args.seconds:.0f}s of footage per run")

    # One throwaway pass before anything is measured, so every row below is
    # comparable. Without it whichever rate goes first pays the model load and
    # the first-inference warm-up and reads slow — the run that produced this
    # comment had 6 Hz at 0.42x and 15 Hz at 0.36x, which is backwards, and the
    # entire difference was warm-up. With it: 0.14x, 0.37x, 0.71x, in order.
    print("  warming up...")
    measure(args.video, min(args.rates), WARMUP_S, args.calibration)

    # `setup` is shown beside the total rather than folded into it, because it
    # is the number that decides whether a slow-looking run is actually slow.
    print(f"\n  {'rate':>8}  {'took':>7}  {'setup':>6}  {'factor':>7}  verdict")
    print("  " + "-" * 70)

    rows = []
    for rate in sorted(args.rates):
        row = measure(args.video, rate, args.seconds, args.calibration)
        rows.append(row)
        print_run(row, half_s)

    # Keyed on the rate that ran, not the one asked for: recommending 12 Hz when
    # the source can only give 15 would be advice nobody can follow.
    fits = fastest_rate_that_fits(
        {row['ran']: row['factor'] for row in rows}, budget=args.budget,
    )
    print()
    if fits is None:
        # Not "run at the lowest rate". Every rate measured was too slow, and
        # rounding that into a recommendation is how a coach ends up waiting.
        print(f"  Nothing measured fits a {args.budget:.2f} budget — a live "
              f"half-time report is not possible on this machine as configured.")
    else:
        print(f"  Fastest rate inside a {args.budget:.2f} budget: {fits:.1f} Hz")

    slowest = max(rows, key=lambda r: r['total_s'])
    if slowest['timings']:
        print_stages(slowest['timings'], f"{slowest['ran']:.1f} Hz")

    print("\n  This machine, this footage, this model, today."
          "\n  Run it on the one that will be at the side of the field.\n")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
