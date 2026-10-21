# player/

A player's own season: their match reports, totals, and how the run of games
has gone. It is the only page a player sees.

Everything on it comes from the `playerReports` documents a coach publishes.
It reads no live match data, by design (see the collection-group note in
`firestore.rules`). Run it with the rest of the site (`shared/README.md`,
"Local development"). It needs a player account linked to a roster entry.
