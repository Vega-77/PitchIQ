# `pitchiq/events.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Passes, shots, tackles — the Opta taxonomy, derived from touches.

Once `pitchiq/touches.py` has produced an ordered list of who touched the ball and
when, most of a match report is arithmetic over adjacent pairs:

    same player again                       a carry
    same team, different player             a completed pass
    different team, ball was in flight      an interception
    different team, ball was controlled     a tackle
    first touch after nobody had it         a recovery
    followed by the ball heading at goal    a shot

That is the whole idea. It also means every number here inherits every error in
the touch list, which is why `confidence` travels with each event rather than
being computed at the end.

    Two things this cannot see, both structural.

**Intent.** Opta defines a pass as a *deliberate* attempt to find a teammate.
Intent is not observable from bounding boxes; all we can see is that the ball
went from A to B. So a deflection that happens to reach a teammate counts as a
completed pass, and a good clearance downfield counts as a failed long one.
Those biases push in opposite directions but they do not cancel, and neither is
tunable — any pass-accuracy figure carries both.

**Height.** The ball's z-axis is unrecoverable from one fixed camera, which is
the same wall `pitchiq/xg_bridge.py` hits on `shot_height`. Aerial duels, headers
and the difference between a goalkeeper's punt and a drilled goal kick all live
behind it. Where that bites, this module reports 'unknown' rather than guessing.

    What needs a calibration, and what does not.

Passes, carries, tackles, interceptions, recoveries, ground duels and pressure
counts all work from pixels alone, because each is a question about *who*
touched the ball and in what order. Anything asking *where* on the pitch —
pass direction, progressive passes, final-third entries, crosses, clearances,
shots — needs metres, and returns None without them.

Pass direction deserves a specific note: there is no honest pixel-space
fallback. On a panning camera the image x-axis is not the pitch x-axis and the
relationship changes every frame, so "forward" is unanswerable rather than
merely imprecise.
