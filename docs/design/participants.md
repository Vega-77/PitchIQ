# `pitchiq/participants.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Who in the picture is actually playing.

The detector asks for COCO class 0 — person — and a football match is full of
people who are not in it. Substitutes warming up behind the goal, a coach
pacing his technical area, parents three deep along the touchline, and the
referee. Every one of them becomes a track, and every one of them then feeds
the possession scale, the nearest-player-to-the-ball test, the clustering that
becomes a player report, and the fragmentation warning.

`assign_teams` does not filter them out and was never meant to. It always
produces exactly two kit clusters from whatever it is handed, and anything that
matches neither closely comes back `UNKNOWN` — a bucket that holds officials,
spectators and both goalkeepers at once.

    The separation this module can make, and the one it cannot.

Somebody who has not moved a body length in twenty seconds is not playing
football. That is a strong, kit-independent, calibration-free statement, and it
catches the bench and the crowd, which are most of the problem by count.

Given a calibration there is a second and much better test: project the ground
point through the homography and ask whether it is on the pitch at all. That is
a measurement rather than a heuristic, and it is the one that catches
substitutes warming up behind the goal — people who move exactly like players,
because they are players, just not ones in the match.

Without a calibration that test is unavailable and this module says so rather
than approximating it. An earlier draft approximated it in pixels, as a band
around wherever the kit-classified players were standing, and it had to be
abandoned for a reason worth recording: a track with a known kit *contributes to
its own band*, so the test was trivially passed by exactly the tracks it could
have judged, and only ever bit on the kit-unknown ones — referees and
goalkeepers, the two it must never act on alone.

Separating a *referee* from a *goalkeeper* is that remaining problem. Both run,
both wear neither kit, and off a calibration there is no goalmouth to measure
either of them against. So officials are **flagged and kept**, never dropped:
carrying a referee inflates a few counts, whereas silently deleting a goalkeeper
removes a player from the match. `pitchiq/keeper.py` refuses to guess at keepers
without a calibration for the same reason, and this follows it.

    The thresholds are guesses.

Every constant below was chosen to be reasonable and has never been checked
against a real touchline. The tests prove the classifier does what this
docstring says; they say nothing about whether the numbers are right. Anything
excluded therefore travels with the `reason` that excluded it, all the way into
the report JSON, so a wrong call is visible rather than merely wrong.

    Why spread rather than distance covered.

The obvious measure of "did this person move" is the path length, summed frame
to frame. It does not work. A detection box jitters by a pixel or two every
frame even on someone standing perfectly still, and at thirty frames a second
that noise integrates into hundreds of metres an hour — a stationary spectator
scores like a midfielder. Both features here are robust to it instead:
`spread_ph` is a median deviation, which ignores jitter by construction, and
`travel_ph_per_min` samples a second apart, by which point real movement
dominates the noise.
