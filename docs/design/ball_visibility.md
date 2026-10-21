# `pitchiq/ball_visibility.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

What the pipeline could not see, and how much of that was its own fault.

`no_ball_s` has been in the quality block since possession was first measured,
and it is one number covering two things that could hardly be less alike:

    a throw-in, where the ball spent eleven seconds in a teenager's hands
    behind the touchline and no camera on earth was going to find it;

    twenty seconds in the middle of live football where the detector simply
    lost it.

The first is not a failure and nothing downstream is worse for it — those
frames are dead-ball time, already excluded from possession by `pitchiq/phases.py`.
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
