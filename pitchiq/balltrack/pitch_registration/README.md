# balltrack/pitch_registration/

From image pixels to the pitch (StatsBomb 120x80), and the ball in 3D.

- Venue map: `venue_features.py` -> `venue_map.py` (`vmap.npz`) ->
  `top_down_map.py` (`vtop.npz`) -> `fit_pitch_to_map.py` (`vpitch.npz`).
- `register_frames.py <lo> <hi> <tag>` writes `<tag>_reg.npz`, one homography
  per frame; the registrations made so far are in git.
- 3D chain (`../evaluation/mk3d.py` runs it in order): `ball_on_pitch.py`,
  `image_track.py`, `flight_scan.py`, `roll_speed_cache.py`, then
  `default_ball_path.sh <tag>` (`flight_chain.py`, `smooth_ball.py`,
  `merge_air_and_ground.py`) -> `<tag>_ball3dd.npz`.
- `score_*.py` score 3D output on the Air and Flight Lab labels in `labeling/`.
- `pinned_ww01/h3d/ww01_ballsb.csv` is a saved ball-on-pitch table for ww01,
  added with autocal on 2026-10-07; nothing reads it.
