# `pitchiq/possession_sequences.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Possession sequences, and what a team did with them.

The roadmap has asked for phase-of-play since the catalog was written and left
its shape open, noting that `territory` and `turnovers_by_third` are the closest
things that exist and neither is it. That is the right reading, and the reason
is worth stating because it decides the whole design:

    territory says **where the ball was**. Phase-of-play says **what the team
    was trying to do there, and whether it worked**.

Those are different questions and only the second is actionable. A clearance
hoofed out of your own box and a patient move worked out from the goalkeeper are
the same square metre of pitch and the same second of possession; territory
cannot tell them apart and a coach cares about almost nothing else.

So the unit here is the **possession**, not the frame and not the event. A
possession has a place it began, a furthest point it reached, and a way it
ended, and those three make a funnel: of the times this team got the ball, how
often did they carry it into midfield, into the final third, into a shot. That
is a sentence a coach can do something about — "you win it in your own half
forty times and nine of those reach the final third" — in a way that "you had
33% of the ball in your defensive third" is not.

Everything here needs a calibration and an attacking end, because a third cannot
be named without both. Absent, the answer is None rather than an empty funnel: a
run that could not measure phases did not measure a team with no phases.
