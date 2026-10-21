# `pitchiq/reconcile.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Where the tagged log and the pipeline disagree.

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
