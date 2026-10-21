# pitchiq/experiments/

Research tools and labs: nothing the report depends on. Each is run as a
module, `python -m pitchiq.experiments.<name> --help`. The calibration, player
and lab tools read the match video from `PIQ_VIDEO`; set it for every tag,
because unset it means the scrimmage.

- Panning-camera calibration: `autocal_run` (no clicks), `cal_health`,
  `cal_overlay`, `halves` (where the halves are in a video).
- Players: `player_lab` (score trackers on the player lab's labels in
  `balltrack/plab/`), `track_report`, `possession_report`.
- Feasibility: `spike_detect` (can we see players and ball in new footage?),
  `speed_report` (can this machine keep up with a half?).
- Labs: `spotlab` (Spot Check Lab media), `clicker/` (the Ball Clicker).
