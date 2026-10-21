# `pitchiq/identity.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Turning fragments back into players.

The tracker gives about a hundred track ids for a fifteen-second window of a
match with twenty-two people in it. A player who walks behind an opponent, or
leaves frame as the camera pans, comes back as somebody new. So every per-track
number this pipeline produces — touches, passes, distance covered — is a
fragment of a player rather than a player, and adding them up under a name
requires knowing which fragments belong together.

Nothing here guesses a *name*. That is the coach's job, and the point of this
module is to make it a job worth doing: mapping a hundred tracks to a roster by
hand is not something anyone will finish, whereas confirming fifteen clusters
is a couple of minutes.

    Two signals, one of them a hard constraint.

**Time overlap is disqualifying.** If two tracks are visible in the same frame
they are two different people, whatever they look like. This is the only
certainty available, and it does most of the work: it is what stops a merge
run from collapsing a whole team into one blob.

**Everything else is evidence.** Shirt colour, how close the tracks were in
space where one ended and the other began, and how long the gap was. A player
who disappears behind someone reappears a fraction of a second later, a couple
of metres away, in the same shirt. A player who reappears twenty seconds later
at the other end of the pitch might be the same person, and we should not claim
so.

    What this cannot do.

It cannot recover a player who left and returned much later — those stay
separate clusters, and the coach will map two clusters to the same player. That
is the right failure: two clusters mapped to one player still sums correctly,
whereas a wrong merge silently attributes one player's work to another and
cannot be undone downstream.

It also has no idea which cluster is which player. There is no jersey-number
reading here; at the camera distances this footage is shot from, shirt numbers
are a few pixels tall and not legible.
