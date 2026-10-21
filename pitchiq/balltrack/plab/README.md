# balltrack/plab/

The player lab: hand-labelled keyframes for scoring player tracking.
`sheets.json` is the fr00 lab (40 keyframes, every 15th frame of
136170-136769), built by `python -m pitchiq.balltrack.plab.plab_make`.
`python -m pitchiq.experiments.player_lab score --labels DIR` scores trackers
on the lab's downloaded answers. `out/` holds per-tag outputs (players,
overlays); only the fm01 and vid1 sheets and the fr00 baseline are in git.
