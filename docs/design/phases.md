# `pitchiq/phases.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

When the ball was actually in play.

Possession is measured as "time with a clear holder", and a player standing over
the ball waiting to take a throw-in is a very clear holder. So is a goalkeeper
holding it before a goal kick, and so is whoever is arguing with the referee.
Nothing in the pipeline has ever known the difference, which means every
possession figure it has produced includes the stoppages.

The information needed to fix that already exists and was never read. Somebody
sits on the touchline tapping `out_of_bounds`, `throw_in`, `corner`,
`goal_kick`, `free_kick`, `foul` and `offside` into the live tagger, each with a
match clock. That is a record of when the ball went dead and when it came back.
This module is the first thing under `pitchiq/` to read it.

    Opens and closes.

A stoppage event opens a dead span and the next restart closes it. A second
stoppage while already dead extends nothing — a foul and the card that follows
it are one interruption, not two.

Substitutions are deliberately ignored. A sub is made during a stoppage that was
already tagged by whatever caused it, so treating it as an opener would mostly
duplicate a span that exists; and because no restart is tagged afterwards, the
duplicate would run until it timed out and delete two minutes of football that
happened.

    Two failure modes, and which one to prefer.

Tags are placed when a person notices, not when the whistle blows. Every
boundary here therefore carries a second or two of that person's reaction time,
in whichever direction their attention happened to drift.

Given that, a span is always **shrunk** and never grown. Calling live football
dead deletes possession that really happened and nothing downstream can tell;
calling dead time live leaves the old, known behaviour. The first error is
invisible and the second is merely imprecise, so the slop is spent in the
direction of the second.

The same asymmetry sets `MAX_DEAD_S`. A restart that nobody tagged would
otherwise leave a span open until the half ended.

## Moved from the code

### `period taps`

The tagger has been tapping `kickoff_1st`, `kickoff_2nd`, `halftime` and
`full_time` since Phase 3 and nothing has ever read them for this. They are a
record of which half was being played and when, which is exactly the question,
so the flag becomes a fallback rather than the source.

---

## Moved from comments

Moved verbatim from comments beside the code in the deep clean (C-2).

### Which half it is

`MatchOrientation.attacking_end` flips the whole pitch on the period, because
teams change ends at the break. Everything positional rests on it: the shot
maps, the heatmaps' reading, the pressing zone, the territory split, the
turnovers by third, the passing network, and — through `attacking_end` —
every xG figure.

Until now that came from one string, defaulting to `first_half`. Process a
second half and forget the flag and every one of those is mirrored, and the
output looks exactly as plausible as a correct one. There is no downstream
check that could catch it: a shot map at the wrong end is a shot map.

The tagger has always tapped `kickoff_1st`, `kickoff_2nd`, `halftime` and
`full_time`, and nothing read them for this. They are a record of which half
was being played and when, which is exactly the question, so the flag becomes
a fallback rather than the source.

### The two clocks

The tag log runs on a match clock that **stops at half-time**: the tablet
freezes it at the break and restarts it from the same second (`advancePeriod`
in live-tagging/tagging.js). The video runs on a file position, which freezes
for nothing.

So adding one offset to every tagged reading is exact for the first half and
wrong for the whole of the second by however long the interval ran — ten to
fifteen minutes, typically. Everything that meets a tagged timestamp inherits
that: the dead spans land in the wrong place, every tagged goal misses its
reconciliation window by two orders of magnitude more than the window, and
`PeriodTable` — which decides which way each team is attacking, and through
that mirrors every shot map, heatmap and xG figure — is shifted across the
very boundary it exists to find.

The clock the second half restarts on is in the log already: it is the start
of the second-half span. The position of that moment in the video is not, and
nothing can derive it, so it is supplied — the same way the kick-off offset
always has been.
