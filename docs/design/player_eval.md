# `pitchiq/player_eval.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Scoring player tracking against the player lab's hand labels.

The lab (`pitchiq/balltrack/plab/`) shows forty keyframes, half a second apart, and
the user names every person in each: A1..An and B1..Bn for the two kits, AGK
and BGK for keepers, R for a referee, X for someone not playing, '-' for a box
that is not one person, and A?, B? or ? for a player whose identity they
can't tell. Missing players are added by tapping their feet. A confirmed
keyframe is complete truth; an unconfirmed one counts only for the labels the
user actually set.

What is measured, and why these:

* **coverage** — of the players on the pitch, how many the tracker has a box
  on. Recall over every labelled player, named or not.
* **precision** — of the boxes the tracker calls a player, how many are one.
  A box on a coach (X) or the referee (R) counts against it; a box on an
  unlabelled or '-' box is ignored, because nobody said what is there.
* **wrong-person rate** — the share of a tracker identity's appearances that
  belong to someone other than the player it mostly is. This is the number
  the user cares about most: a wrong identity silently gives one player's
  minutes, distance and touches to another, and nobody can see it happen.
* **IDF1** — the standard identity score: the best one-to-one pairing of
  labelled players with tracker identities, and how many appearances agree.
* **fragments** — tracker identities per labelled player; ID switches along
  each player's keyframes.
* **team accuracy**, **referee**, **identities per team** against the truth's.
* **position error** — feet in metres, when a projector is given, which says
  whether a box is on the right player and not merely near them.

Predictions are plain: for each keyframe frame, a list of (identity, box,
team, role). Every tracker — the shipped BoT-SORT, the pipeline's merged
clusters, the pitch tracker — is scored the same way.
