# `pitchiq/keeper.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Finding the goalkeepers, and what can be said about them.

Colour cannot do this alone. `teams.assign_teams` clusters on shirt chroma and
drops whatever matches neither kit into UNKNOWN — which is exactly where both
goalkeepers land, and also both referees, and anyone in a bib on the touchline.
Colour tells you somebody is not wearing either kit; it does not tell you which
of the not-wearing-either-kit people is a goalkeeper.

Position can, and the signal is unusually clean. A keeper spends nearly all of
a match within about twenty-five metres of one goal and inside the width of the
penalty area. A covering centre-back drops in there occasionally. A referee
patrols a diagonal and is almost never in either zone. So:

    colour proposes, position confirms, a human overrides both.

Without a calibration there is no position, and this returns
`method='unavailable'` rather than guessing. That is a deliberate refusal: the
alternative on offer is the nearest-player-to-goal heuristic that
`xg_bridge.shot_context_from_tracking` already documents as occasionally
catastrophic, and a wrong keeper poisons save percentage, distribution and
every keeper feature in the xG model at once.

`by_team` holds a *set* of track ids per team, not one id. At the fragmentation
this pipeline currently measures, one keeper is many tracks, and the positional
test is what makes the whole set recoverable where colour alone would only find
pieces of it.

    What is not here, and why.

**Punches.** Nothing distinguishes a punch from a catch without the ball's
height. Omitted rather than reported as zero.

**Punt versus goal kick.** Both are the keeper hitting the ball a long way. The
separator used here is a *hold* — the keeper having the ball at their feet or
in their hands for a couple of seconds first — which works for an obvious punt
and fails on a quickly-taken one. `kind='unknown'` is a legitimate answer and
is used freely.
