# coach/

The coach's dashboard: season, roster, staff, matches, and each match's report
(overview, play, shots, the video, mapping figures to players, review of the
pipeline's events), plus publishing reports to the players.

`coach.js` is the entry point and loads the rest. The modules form a stack in
which each imports only those below it, as listed at the top of `coach.js`.
`tests/test_browser_graph.py` holds that order.

Run it with the rest of the site (see `shared/README.md`, "Local development").
It needs a coach account on the Firebase project, or the emulators.
