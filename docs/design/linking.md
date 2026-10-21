# `pitchiq/linking.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Joining tracklets into people, offline, with the rules a match obeys.

`pitchiq/pitchtrack.py` hands over tracklets: runs that are one person with high
confidence, ended wherever that confidence ran out — the player walked out of
the picture, was hidden behind a teammate for a second, or crossed a teammate
too closely to say who was who. Joining them is easier offline than online,
because both sides of every gap are known: where the player was going when
the track ended *and* where the new track was coming from when it began.

The joins respect what is physically true of a football match:

* **Two tracklets seen in the same frame are two people.** A person is never
  in two places, so groups that overlap in time never merge.

* **A player runs at most ~9 m/s.** A gap that would need more is not one
  person, whatever the colours say. Far-side positions are uncertain by a few
  metres along the camera ray, and the allowance includes that.

* **Shirts don't change.** Team A never continues as team B. A tracklet whose
  kit was unreadable may join either, at a price. And a tracklet whose shirt
  does change, clearly and for more than a moment, followed two people: the
  tracker swapped somewhere it did not notice, and it is cut there.

* **The camera's footprint explains absence.** A player who leaves the
  picture can be anywhere when they come back; a player who vanished while
  the picture still showed their path, with nobody in front of them, is
  suspicious, and the join pays for every unexplained second.

* **A team has eleven players.** If more than eleven identities of one kit
  remain over a short window, joins are missing, and a second pass with a
  looser threshold looks for them — within that team only.

* **Keepers and referees are their own people.** The deepest person in
  front of each goal is that end's keeper, whatever `assign_teams` made of
  their shirt; anyone else in a kit that is neither team's is officiating; and
  anyone mostly outside the lines, or standing still on one, is not playing.

And, as everywhere in this tracker, a wrong join is worse than a missing one.
Each round is an optimal assignment (`pitchiq/assignment.py`), and every join it
picks must win clearly: if changing it — to another end, another start, or by
swapping with a neighbouring join — costs less than `ambiguity_margin` more,
it is refused, and the two fragments stay two.

## Moved from the code

### `LinkSettings.kit_cut_keep_rows`

joined or not like any other. 0 drops every gap. On the vid1 lab (set
vid1-gaps, 2026-10-12) the ring stayed on one person at 44 of 45 ends of
kept gaps, and the kept rows were 3% of vid1's on-pitch detections.
