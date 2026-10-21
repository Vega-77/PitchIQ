# `pitchiq/metrics.py`

Moved verbatim from comments in the deep clean (C-2). The code keeps what each
value is; this keeps the reasoning and the measurements behind it.

### Bursts

Acceleration is the second derivative of a position, and each derivative
multiplies the noise. The jitter that fakes 6 m/s of speed between two frames
fakes 180 m/s² of acceleration between three, which is fifteen times what a
human can produce — so a frame-to-frame acceleration is not a noisy version of
the real thing, it is entirely noise with the real thing somewhere inside it.

So this never differentiates twice. A burst is measured as a **speed gain
across a span**: how much faster the player was one second later. That is one
derivative of a speed which was itself smoothed, and averaging over a whole
second is what makes it survivable — the jitter is zero-mean, so it cancels
across the span while a real acceleration does not.

The cost is stated rather than hidden: a burst shorter than the span reads
lower than it was, because it is averaged against the stillness on either
side. A 0.4s explosion out of the blocks is real football and this will
understate it. That is the direction to be wrong in — the other one invents
bursts nobody ran.

### Burst smoothing window

The speeds a burst is read off get their own, much longer smoothing window
than distance does, because they are answering a second-order question.

This was measured rather than picked. On synthetic 30fps tracks at the window
the pipeline actually smooths over — a player jogging at a constant 3 m/s, so
every burst reported is a false one, against a player running four genuine
3 m/s² bursts in the same minute — reading bursts straight off that smoothing
gives, per 60 seconds:

    noise σ    false bursts    real bursts (of 8)
     0.05 m         0.0              8.0
     0.10 m         2.4              9.6
     0.20 m        40.1             37.4

At 0.20 m there are more phantom accelerations in one minute than a real
player makes in a match, and no threshold fixes it: the false and the real
counts rise together, because they are the same noise. The window is what is
wrong. Smoothing the burst pass over a full second instead:

    noise σ    false bursts    real bursts (of 8)
     0.05 m         0.0              8.0
     0.10 m         0.0              8.0
     0.20 m         0.0              8.0
     0.30 m         0.0              7.5
     0.40 m           — withheld, see below

Exact to 0.2 m, fraying at 0.3 m, and past that the count is a count of the
jitter — which is what MAX_ACCEL_NOISE_M is for.

### How long to smooth for

The window distance is read off was 9 frames, written as a bare number in the
pipeline, and it was never fitted to anything. Two errors decide it, and they
pull in opposite directions:

  phantom  — a still player accumulates distance from jitter alone. Measured
             at 60·fps·σ·√π / W metres a minute, exactly: the smoothed noise
             has neighbouring samples correlated (W-1)/W, so the step between
             them has size σ√2/W per axis, and a 2D magnitude averages σ√π/W.
             Verified to three figures at every window and noise level below.
             **Falls as 1/W.**

  corners  — a moving average cuts the inside off a turn. On a constant arc
             the loss is exact: the average of an arc of half-angle θ = vT/2R
             sits at radius R·sin(θ)/θ, so the path shortens by that factor.
             Since a person cannot exceed about 4.5 m/s² sideways, R is at
             least v²/a, and the metres lost to a turn of angle φ work out at
             φ·a·T²/24 — **independent of speed**. A fast turn is necessarily
             a wide one, which is the whole reason this is survivable: a 90°
             turn costs 0.03m at 0.3s and 0.30m at 1.0s.

The real cost is not the smooth arc but the hard cut — decelerate, plant,
accelerate back — which is a cusp rather than a curve, and which a long
average smears right through. Metres of real path lost to one 180° stop-turn,
by how long the player is stopped:

    window      0.2s stop   0.4s stop   0.8s stop
     0.30s        0.36        0.19        0.09
     0.50s        0.82        0.52        0.26
     1.03s        2.12        1.71        1.10
     1.50s        3.27        2.84        2.11

Two metres a turn sounds ruinous until it is put beside the phantom it buys
off: at σ=0.20 going from a 0.3s window to a 1.0s one saves 50 metres a minute
of invented distance and costs about 2.1 metres for each hard cut. A player
would have to stop and reverse 23 times a minute — one every two and a half
seconds, for the whole match — before that trade starts to lose.

So it was fitted rather than argued. Twenty minutes of synthetic ground truth
— speeds drawn from the match-play gears, direction changes at a stated rate,
every turn held to 4.5 m/s² — measured through each window, as metres per
minute of error against a real 181 m/min:

    window    σ=0.05   σ=0.10   σ=0.15   σ=0.20   σ=0.30
     0.17s     +2.9    +11.3    +24.4    +41.3    +81.4
     0.30s     +0.7     +3.5     +7.8    +13.6    +29.4      <- was here
     0.50s     -0.2     +0.8     +2.5     +4.7    +10.8
     0.70s     -0.7     -0.2     +0.7     +1.9     +5.2
     1.03s     -1.7     -1.4     -1.0     -0.5     +1.1
     1.50s     -3.2     -3.1     -2.9     -2.6     -1.8

There is no single best window in that table, which is the finding: the right
one moves with the noise, and since `position_noise_m` measures the noise per
track, the window can follow it. The bands below are the argmin of each
column. They hold the error inside ±2 m/min from σ=0.05 to σ=0.30, where the
fixed 0.3s window drifts to +29 m/min — 1.8km over a 60-minute track, on a
figure a coach reads as kilometres run.

Fitted at 8 direction changes a minute, and then checked against that
assumption, because a rule that only works at the rate it was fitted at is
not a rule. Worst error under the rule, in m/min:

    turns/min   σ=0.05   σ=0.10   σ=0.15   σ=0.20   σ=0.30
         3       +0.1     +0.3     +1.1     +0.4     +1.9
         8       -0.2     -0.2     +0.7     -0.5     +1.1
        15       -0.6     -1.1     -0.2     -2.2     -0.6
        25       -0.9     -1.6     -0.8     -3.3     -1.7

Nothing here says a longer window is more *accurate*, only that the distance
it totals is closer. The path itself is smoother than the player's, and the
corner it cuts is real. That trade is right for a distance total and it is
the reason heatmaps and shape are unaffected either way — a 1s window moves a
position by the corner cut, which is under half a metre.

Boundaries in metres of measured wobble, and the window in seconds for each.
Open-ended at the top: past MAX_ACCEL_NOISE_M the bursts are already refused,
and distance survives noise that bursts do not because it is one derivative
rather than two.

### Samples per window

The fewest samples a smoothing window may contain and still be an average.

Two is a midpoint and one is the sample itself, so below three the window
stops smoothing and the wobble it was hiding comes back. Measured rather than
argued: at three samples every movement figure is where it was at thirty, and
at two the burst count jumps by four a minute on a track with 0.15m of wobble
— noise being counted as acceleration, which is the exact failure the window
exists to prevent. See tests/test_sampling.py.

### Back-line share

The share of tracked outfielders that counts as the back line, and the fewest
an instant needs before a line height is worth reporting at all.

Both are measured rather than chosen. `defensive_line_m` defaults to the mean
of the deepest four, which is right when all ten outfielders are tracked and
badly wrong when they are not: drop one defender and a midfielder twenty-five
metres up-pitch takes his place among the deepest four, so the figure comes
out too HIGH. Exhaustively over every subset of a synthetic 4-4-2 with its
line at 20m, the deepest-four mean overstates it by +20.0m at four tracked,
+10.0m at six, and +2.5m at nine. That is a systematic bias, always upward,
and it moves with tracking density — which varies across a match, so it is
not something a constant offset could absorb.

Taking the same *share* instead of the same *count* removes most of it.
round(0.35 * k) holds the error under 1.5m from six tracked upward across a
4-4-2, a 4-3-3 and a deep 5-3-2, and at a fully tracked ten it comes to four
players exactly — so good tracking still gets the conventional definition and
only sparse tracking degrades away from it. Below six no share helps, which is
what MIN_LINE_OUTFIELDERS is for: those instants report nothing rather than
something inflated.
