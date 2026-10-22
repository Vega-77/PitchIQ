# Architecture

How a match video becomes the pages a coach and a player read. Terms are in
[glossary.md](glossary.md). The JSON and Firestore contract is in
[report-schema.md](report-schema.md). The measurements and history behind each
module are in [design/](design/).

## Data flow

```
match video ----------------------------+------------------------------+
   |                                    |                              |
   |  fixed camera                      |  panning camera              |
   |  calibration.py (clicked pins)     |  balltrack/  (ball, venue map, registration)
   |                                    |  autocal.py / pins -> panning_camera.py
   v                                    v
frame_table.py   one decode: detector.py boxes + BoT-SORT tracks, ball candidates,
   |             kit colours, thumbnails
   v
pipeline.analyse_match
   teams.py -> participants.py -> phases.py (tag log) -> ball.py -> possession.py
   -> identity.py (clusters) -> touches.py -> events.py -> xg_bridge.py
   -> metrics / territory / zones / keeper / possession_sequences
   -> checks: camera.py, coverage.py, ball_visibility.py, reconcile.py, run_timing.py
   v
MatchReport.to_json()  ->  report JSON (report_export.py)
   v
publish_to_firestore.py  ->  Firestore cvStats/* and playerReports cv* fields
   v
shared/db.js -> shared/report*.js -> coach/, player/, halftime/
```

The tag log enters at `phases.py` (when the ball was dead) and `reconcile.py`
(where the two records disagree). It comes from `live-tagging/` through
Firestore.

A second, separate path makes the top-down map for panning footage:
`pitchtrack.py` (players tracked in metres) -> `linking.py` (tracklets joined into
people) -> `topdown.py` (everyone, every frame). `balltrack/match_video.py` renders
it with the ball. It does not feed the report yet.

## Which path a module belongs to

| path | modules |
|---|---|
| fixed camera only | `calibration.py` (as the source of the homography), `camera.py` (the camera was bumped after calibrating), `distortion.py` |
| panning camera only | `autocal.py`, `panning_camera.py`, `pitchtrack.py`, `linking.py`, `topdown.py`, `balltrack/` |
| both | everything else. A panning match reaches `pipeline.py` as a `PannedCamera`, whose boxes and ball are already in one still reference view |

`lines.py` (finding the paint) serves both: it checks a fixed calibration and
feeds autocal and the venue map.

## `pitchiq/` modules

**Entry points.** `pipeline.py` runs everything and returns a `MatchReport`.
`__main__.py` dispatches `python -m pitchiq <command>` to `cli/`. `__init__.py`
exports `analyse_match` and the camera classes. `extras.py` turns a missing
optional dependency (the `[detect]` or `[balltrack]` extra, the tapnet clone)
into a message naming the install command.

**Video and detection.** `video_io.py` samples frames at a fixed rate.
`detector.py` runs YOLOv8 for the person and ball classes only. `frame_table.py`
makes the one pass over the video: decode, batched detection, BoT-SORT tracks,
ball candidates, kit-colour samples and thumbnails, held per frame.
`tracking.py` is the older stand-alone tracker, kept for `track_report` and the
player lab. `detection_diagnosis.py` says whether missed detections are the
camera's fault or the run's.

**Calibration and the pitch.** `pitch.py` holds the dimensions, the landmarks
and the metre and StatsBomb frames. `calibration.py` fits the fixed camera's
homography from clicked landmarks. `distortion.py` measures lens distortion
from the painted lines. `lines.py` finds the paint and scores a calibration
against it. `camera.py` notices the camera moved after it was calibrated.
`autocal.py` calibrates a panning camera from the match itself.
`panning_camera.py` turns a panning match into a still camera for the
pipeline. `checks.py` flags calibrations and top-down maps that look wrong,
without labels. `coverage.py` reports how much of the pitch the camera could
see.

**People.** `teams.py` splits the two teams by shirt colour. `participants.py`
decides who is playing, officiating or watching. `assignment.py` is the
min-cost matching (with "match nothing" priced) that the trackers use.
`pitchtrack.py` tracks players in metres through a pan. `linking.py` joins
tracklets into identities under a match's rules. `identity.py` clusters tracks
for a coach to confirm and name. `keeper.py` finds the goalkeepers and their
stats. `player_thumbnails.py` crops a recognisable picture of each figure.
`topdown.py` builds the top-down map. `player_eval.py` scores player tracking
against the player lab's labels.

**Ball and play.** `ball.py` builds the ball's trajectory from candidates (the
fixed-camera ball; a panning match brings the tracker's). `phases.py` works out
when the ball was in play from the tag log, and relates the two clocks.
`ball_visibility.py` sorts the stretches where the ball was unseen.
`possession.py` says who has the ball, in pixels, with no calibration.
`touches.py` finds touches, which everything below is built on. `events.py`
derives passes, shots, tackles and the rest (Opta taxonomy).
`possession_sequences.py` groups possession into sequences and phases of play.
`zones.py` names where on the pitch something happened, and `territory.py`
where each team had the ball.

**Numbers for the coach.** `metrics.py` turns positions into distance, speed,
sprints, shape and pressing. `xg_model.py` is the xG model's contract (file,
version, features, geometry). `xg_bridge.py` turns a detected shot into its 11
features and scores it. `reconcile.py` lists where the tag log and the pipeline
disagree. `run_timing.py` says where a run's seconds went and whether it would
keep up with a live half.

**Out of Python.** `report_export.py` rolls a report up into the report JSON
(and owns `SCHEMA_VERSION`). `publish_to_firestore.py` writes it to Firestore
under a strict path allowlist. `_util.py` holds small shared helpers.

**`cli/`.** The match-day commands: `grab_frame`, `calibrate`, `event_report`,
`match_report`, `compare_reports`, `publish_report`.

**`experiments/`.** Research tools and labs, run one by one: `autocal_run`,
`cal_health`, `cal_overlay`, `halves`, `player_lab`, `possession_report`,
`speed_report`, `spike_detect`, `spotlab`, `track_report`, and the ball
clicker in `clicker/`.

**`balltrack/`.** The ball tracker for panning footage (Hybrid 90) and the
venue map and registration in `pitch_registration/`. Its README covers the
chain, the accuracy and what it needs.

## The browser

Plain ES modules, no build step. `shared/` holds the Firestore access
(`db.js`), the report derivations (`report*.js`, re-exported by `report.js`),
charts and the CSS. Each page folder (`coach/`, `player/`, `halftime/`,
`live-tagging/`, `calibrate/`) has its own HTML and scripts. `firestore.rules`
is the only server-side logic. The Python publisher bypasses it, so the
publisher enforces its own allowlist.
