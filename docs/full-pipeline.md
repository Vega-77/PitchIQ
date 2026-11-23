# The full pipeline: video to published stats

What `python -m pitchiq process_match` does: each step, the files it reads and writes,
and why. Traced from the code at `de4a918` (2026-11-21) and kept current since, with sizes
and times from the Woodbridge run (game `wb`, halves `wb1` and `wb2`, 18-19 Nov 2026).

**The order.** Halves, camera, both halves' passes, then the ball step with both maps
running beside it, then both halves' stats, then publish:

    halves -> camera (coach's pins) -> pass wb1, pass wb2 -> ball17 wb ---------> stats wb1, wb2 -> publish
                                                            map wb1, map wb2 -/

The first half's pass starts before the camera is done: it starts beside the panoramas,
detects while the coach pins, and only its last stage waits for the calibration (section 3a,
"Started while the coach pins").

The maps need the pass's players and camera but not the ball, and the ball step is mostly
the GPU while the map is the CPU, so the two share the machine (`step_ball_and_maps`).
Woodbridge's measured steps add up to about 8 h 40 min in this order, against 10 h 52 min
one after another with the stats read from the video. The coach page says 5 to 10 hours.
[architecture.md](architecture.md) covers the analysis modules inside the stats step.

## The command

    python -m pitchiq process_match <video> --game wb --team T --match M [--ours lighter|darker]

`--game` is 2-8 lowercase letters. The halves become `<game>1` and `<game>2`, and every
file below is named by one of these. In this doc, `<g>` is the game code, `<tag>` is a half,
and `<ptag>` is a 5-minute piece of a half (`wb1_q003`).

| Flag | Effect |
|---|---|
| `--ours` | Which shirts are the coach's. Read from the coach's request on the site when left out. |
| `--h1 12:58-55:15 --h2 66:05-111:00` | Gives the halves on the video clock instead of guessing them. |
| `--lines white` | Colour of the pitch paint (default gold, the home field). |
| `--attacks-first left\|right` | The end ours attack in the first half. The default reads it from the map. |
| `--tag-log <file>` | The coach page's tag log. Reruns stats and publish with it. |
| `--no-pins` | Calibrates without the coach's pins (autocal). Only for a field it can read alone. |
| `--no-wait` | Stops instead of waiting while pins are missing. |
| `--no-early` | Passes each half only after the calibration, not while the coach pins (as before 2026-11-23). |
| `--pins-from wb` | Carries an earlier game's pins instead of asking the coach: the same field, with the camera in the same place. Checked like the carry from one half to the other, and the run stops if they don't carry or don't fit. |
| `--trust-cal` | Carries on when the calibration check fails. |
| `--trust-halves` | Carries on when the halves' lengths or the break look wrong. |
| `--final` | Allows the map of a held-out half (nb2, pi1, wh1, co1, wb2), which is scored once. |
| `--no-publish` | Writes the report file but not to the site. |
| `--plan` | Says what is done and what would run, and runs nothing. |
| `--list-matches`, `--requests` | Print the site's team and match ids, or the matches coaches asked for, and stop. |

Environment it reads:
- `PITCHIQ_SA_KEY`: path to the Firebase key, outside the repo.
- `PIQ_DOWNLOADS`: where videos are expected (default `~/Downloads`).
- `PIQ_MAP_PROCS`: the most map processes (default 8; fewer when memory is short).
- `PIQ_BALL_GB`: the memory the maps leave free for the ball step running beside them
  (default 6 GB, an estimate until a run's `peak` lines measure it).
- `PIQ_SITE`: site URL for pin links.
- `PIQ_HALF_WORKERS`: ball detector processes in the pass (default 12).

It sets these for every step:
- `PIQ_VIDEO` (the video)
- `PIQ_LINES`
- `PIQ_GIANT=50000` (the pale-surface finder rule the v17 ball needs)
- `PYTHONPATH` (the repo)

**Before anything long** (about 10 s), it checks:
- The video is 1920x1080 at 30 fps and decodes to its last two seconds.
- Every fixed input below is on disk, `pipeline/ballfit` included (`pitchiq/cli/preflight.py`).
- No other pipeline process is running.
- The disk has 35 GB free (5 GB once both passes are done).
- While steps that need them remain, 12 GB of memory is free and the GPU has under 3 GB in use.
- An existing `halves.json` entry looks like a game: halves 35-55 min, a break of 4-25 min,
  inside the video (`--trust-halves` skips this).
- With `--pins-from`, the earlier game's first half has its pins, panorama and keyframes.
- The Firestore key works, and the team and match exist.

`python -m pitchiq preflight` runs the machine checks alone.

## Where files live

| Root | What is there |
|---|---|
| `PitchIQ/pitchiq/balltrack/` | Code, **and** the pass's and ball step's outputs, all gitignored (`*.npz`, `h3d_*/`, `half/`, `plab/out/`, `evaluation/p2d/`). About 8.4 GB a game. |
| `PitchIQ-data/` | Calibration work, the ball step's work folder, maps, stats and run logs (about 1.5 GB a game). Also `pipeline/ballfit/`, the ball fit's shared inputs (40.6 GB, never delete). |
| `~/Downloads/` | The match videos (4-6 GB each). |
| Firestore `teams/{t}/matches/{m}/` | The coach's request, progress, panorama, pins and the published report. |

## Resuming

Every step is skipped when its output exists, so after a crash or a fix you run the same
command again.

| Step | Counted as done when |
|---|---|
| halves | `balltrack/halves.json` has the game (with the same video name) |
| camera | `pitch_registration/<tag>_reg.npz` and `<tag>_venue.npz` exist; the check when `stats/<g>/run/cal_health_<tag>.json` exists |
| pass | `half/<tag>/log.txt` says `HALF DONE`, and `pitch_registration/<tag>_camera.npz` (or, for a half passed before 2026-11-21, `h3d_hyb90/<tag>_smooth2.npz`) and `plab/out/<tag>_dets.npz` exist. Inside a half, every stage of every piece leaves `half/<tag>/<ptag>.<stage>.done`. A pass started early and cut off resumes from those marks; each run first deletes the go files `stats/<g>/run/ship_<tag>` and `half/<tag>/early.done`, so only its own camera and passes let a shipped chain start. |
| ball | `ball17.version(tag) == 'v17'` (its `p2d/v17` and `h3d_v17` files exist) |
| map | `PitchIQ-data/stats/map/<tag>.npz` exists |
| stats | `pipe_<tag>.json` exists and `pipe_<tag>.key.json` matches this run's ours, attacking end and tag log, and says `source: table` and `pictures: true` (a report from the old video path, or from the table before it had pictures, is counted again) |
| publish | never skipped: the merge, map report and publish rerun every time |

Each step logs to `PitchIQ-data/stats/<g>/run/logs/<step>.log`, and the run as a whole logs
to `run.log` beside it. Each step's end line in `run.log` gives its peak memory, for the
step's whole process tree, sampled every 30 s: in use (working set) and committed (what the
paging file has to back).

**After a reboot.** While it runs, `process_match` keeps `stats/<g>/run/active.json`. That
file holds the command, the folder, the PIQ_* settings, the key's path and the PID. A run
that ends clears it, whether it finished or stopped. A run the machine killed leaves it.
Two minutes after logon, the Task Scheduler task `\PitchIQ\Resume run` runs
`pythonw -m pitchiq.cli.resume`. That relaunches the newest cut-off run with no window, its
output in `run/resume.log`, and notes it in `run.log`. After 3 resumes in a row it gives up
and leaves a note instead. A resumed run whose machine checks fail (memory, the GPU, another
run) waits up to 30 min for them to pass, rechecking every 2 min: the desktop may still be
starting, and nobody is there to run it again.
- `python -m pitchiq resume --list` shows the marks.
- `--install` and `--uninstall` add and remove the task.

While it runs, the run also asks Windows not to sleep. That is the request a video player
makes, not a power setting.

**When a step fails.** A failed step is tried once more before the run stops, since every
step resumes from what it finished. When the end of its log says it ran out of memory
(`MemoryError`, `Unable to allocate`, the paging file, CUDA out of memory), the second try
runs fewer processes: the map with half its `--procs`, the pass with half its detector
workers and one ball lane. The ball step's second try waits for the maps beside it to
finish. Failing twice stops the run with the end of the step's log.

**Who hears about it.** A run that stops, crashes, waits for the coach's pins (once a wait),
is resumed after a reboot, or finishes writes a line to `run/notify.log` (`pitchiq/cli/notify.py`).
Once `python -m pitchiq notify --setup` has been run, the same line goes to the phone of whoever
runs the PC as an ntfy push (https://ntfy.sh: a free app, no account). Setup makes a long random
topic, keeps it in `PitchIQ-data/notify.json` and sends a test; subscribe to that topic in the app.
Stops and crashes are high priority, the end is low. Nothing is sent before setup, and a push
that fails never stops a run. The coach's card shows the step that stopped and, where it can say
it in plain words (the video, the halves, the machine not ready, the calibration, a step failing
twice), why (`Stop(..., coach=...)` in process_match). An email to the coach is for later.

## Fixed inputs (the same for every game)

| File | Size | Read by | What it is for |
|---|---|---|---|
| The video (`PIQ_VIDEO`) | 4-6 GB | nearly every step | Decoded frames |
| `balltrack/halves.json` | 3 KB | every step | Each game's half frame ranges, video name and overlay mask. Step 1 adds to it. |
| `pitchiq/weights/yolov8n.pt` | 6 MB | halves, pass (players) | Person detector |
| `balltrack/rank7.model.json` (+ `.affine.json`), `still4.npz` | 0.5 MB | pass (`detect_candidates`) | Ball candidate ranker and its settings |
| `balltrack/cls_C.pt` | 237 KB | pass (`score_candidates`) | Crop classifier scoring each ball candidate |
| `balltrack/tapnext_bridge/tapnextpp_512.ckpt` | 2 GB | ball | TAPNext++, which tracks the ball through gaps ("bridges") |
| `pitch_registration/vpitch.npz`, `vtop.npz` | 2 KB | 3D chain, stats | Default pitch template (used when a half has no venue file of its own) |
| `pitchiq/xg_model8.onnx` | 4 MB | stats | xG for shots |
| `botsort.yaml` (ships with Ultralytics) | - | stats `--video` only | Player tracker settings (the old stats path) |
| `balltrack/crop_labels/crops.jsonl` | 171 KB | ball | Hand crop labels the ball fit trains on |
| `PitchIQ-data/pipeline/ballfit/` | 40.6 GB | ball | The fit's dev data. Its README lists every file: `cache/` 17 GB and `tcache/` 22 GB crop caches, `devpass/` 712 MB frozen dev pass pieces, `crops/` 533 MB, `labels/`, `oof_cls/`, `prop/`, `clicks/`, `ckLlabels.json`, `models/` (ResNet-18 weights). |
| `shared/run-steps.json` | 2 KB | progress | Expected minutes per step, for the coach's progress bar |

## 1. Halves (5-15 min)

`pitchiq.experiments.halves <g>` samples one frame every 5 s. For each sample it records
how many people YOLO sees, how far apart they stand, and how far the camera panned (ORB).
Activity is the least of three shares over the minute around each sample: busy head
count, spread out, panning. A half may start only where activity rises after a quiet
minute (kickoff follows the lineup and the huddles) and end only where it falls into one.
Of those edges, `fit` keeps the two halves of 40-52 min, with a 4-25 min break, that hold
the most activity. Warm-ups, a senior-night ceremony and overtime fall outside.

On the 7 games with known halves (21 Nov 2026), every edge was within 2.6 min and 77%
within a minute. The old to-the-minute runs were 8-14 min off on eb (warm-ups), wb and wh
(a lone team warming up across the pitch). When a fitted half is quiet (activity under
0.4), the break is busy (over 0.35), or no two halves fit, the run stops and asks for
`--h1/--h2`, naming the plot. `--h1/--h2` skips the finder.

- Reads: the video and YOLO.
- Writes:
  - `plab/out/<g>_halves.npz` + `.png`: the signals and the activity, with the halves drawn on.
  - the game's entry in `halves.json`, to the 5 s sample. Every later step takes its frame range from here.

## 2. Camera (15-25 min a half, then the coach's pins, then 10-15 min)

1. **Panorama** (`pitchiq.experiments.pincal prep <tag> <lo> <hi>`), for each half:
   - It runs autocal's gather and prep: SIFT links between sampled frames, and paint masks. These go to `PitchIQ-data/autocal/<tag>/` (`gather.pkl`, `prep.pkl`, `spec.pkl`; about 420 MB a half).
   - Its worker processes run one OpenBLAS thread each (`autocal_run.spawn_pool`). With this PC's 28 threads, each worker's numpy and scipy otherwise reserve about 1.5 GB of commit as they load, so 12 workers committed 24 GB; now the step commits about 7 GB, with the same files byte for byte (session step 3.11, wbr1 and wbr2).
   - A loop closure then solves every frame's aim and zoom together, since the camera never moves.
   - Finally a colour panorama is built. The files go to `PitchIQ-data/pincal/<tag>/` (`keys/`, `keys.pkl`, `match.pkl`, `solve.pkl`, `pano_rgb.jpg/json`; about 50 MB).
2. **To the coach**: `upload_pano` writes the panorama to Firestore `cvPano/h1` (plus chunk documents `h1_<i>`). The coach pins it on `calibrate/?team&match&half`. The run polls `cvPins/h1` every minute and saves what it finds as `pincal/<tag>/pins.json`.
3. **Transfer** (`pincal transfer <h1> <h2>`): writes `pincal/<h2>/transfer_from_<h1>.json`. If the tripod stayed put, the first half's pins carry over to the second, so the coach pins once. Otherwise the second half gets its own panorama upload.
4. **Pin check**: `fit_pins` checks every pin against the rest. A set that doesn't fit is renamed `pins_rejected.json`, the coach is told why (`mark_problem` on `cvPano`), and the run waits for new pins.

   The first half's pass starts with this step, beside the panoramas (section 3a, "Started while the coach pins"). It does not start when the run will stop for pins (`--no-wait`, or no `--team`/`--match` to poll and no pins yet), nor with `--no-early`.

   With `--pins-from <game>`, nothing goes to the coach. Step 3's check runs between `<game>1` and this game's first half (`transfer_from_<game>1.json`), and if the camera stood in the same place, `<game>1`'s pins are carried over (`pins.json` with `"from": "<game>1"`). The second half then follows the usual rule, taking `<game>2`'s pins only if the camera moved. A camera that stood elsewhere, or carried pins that don't fit, stop the run: leave out `--pins-from` and have the field pinned.
5. **Calibration** (`pincal cal <tag> --with <other> --install`): pins give camera position, aim and pitch size, and the paint polishes the aim. It writes `pincal/<tag>/cal/` and `pin_pose.json`, and installs:
   - `balltrack/pitch_registration/<tag>_reg.npz`: per frame, the image-to-pitch mapping (about 1 MB)
   - `balltrack/pitch_registration/<tag>_venue.npz`: the camera's reference view, its fit error and the pitch size

   **Every later step uses these two files to put pixels on the pitch.**
6. **Check** (`pitchiq.experiments.cal_health <tag>`): reads the video, reg and venue, and measures paint on the lines, far and near player height, and feet on the pitch. It writes `stats/<g>/run/cal_health_<tag>.json`. If `checks.cal_flags` finds a problem, the run stops unless `--trust-cal` is given.
7. **Against earlier games** (`pitchiq.experiments.venue_check check <h1> <h2> --out stats/<g>/run`, under a minute): the first half (and the second, if the camera stood elsewhere for it) is compared with every other game's keyframes on disk. A game filmed from the same tripod spot shows it: its keyframe pairs fit a pure turn of the camera (3 pairs within 5 px). The two calibrations of that spot must then agree. It writes `stats/<g>/run/venue_<tag>.json`, and the self-checks (section 6) flag calibrations more than 5 m apart. Nothing stops here.
   - The pitch paint alone matches between different fields (a plane maps to a plane), so a camera set up somewhere else at a field is not compared. Woodbridge's away field "matched" the home field's games on 6-21 keyframe pairs, but none came within 24 px of a turn.
   - Keyframes come from `pincal/<tag>/keys`. A half calibrated another way gets a 5-16 MB copy in `PitchIQ-data/venues/<tag>/` (`venue_check book <tag>`, from its autocal samples). On 2026-11-22 that was done for ww1, eb1, co2, pi2, wh2 and nb1: the home field's four games stood within 0.3 m of each other, while wh (the stadium) and nb share no spot with any other game.

With `--no-pins`, `pitchiq.experiments.autocal_run` replaces steps 1-5. It first calibrates one minute
(`autocal/<g>cam<k>/full.pkl`), then each half.

## 3a. Pass (`pitchiq.balltrack.half <tag> <lo> <hi>`, 50-110 min a half)

The half is cut into 5-minute pieces (9,000 frames, plus 300 frames of overlap each side;
Woodbridge had 9 and 10). The stages run in parallel lanes:
- the ball detector on the CPU (it sets the pace: a piece can start only once its chunks are detected)
- players on the GPU
- the classifier on the GPU
- two lanes of CPU ball jobs

Until 2026-11-21 each piece also got TAPNext bridges and the Hybrid 90 ball with its own 3D.
Nothing read them once v17 shipped, so they were dropped. On Woodbridge they finished 10 and 13
minutes after each half's last shipped piece, so a pass is about that much shorter (estimated
from the logs, not yet measured on a run).

| Stage | Reads | Writes |
|---|---|---|
| Ball candidates (`detect_candidates`, 12 processes) | video, ranker | `balltrack/<tag>.npz` (every candidate blob, about 28 MB), `<tag>pan.npz` (camera pan), `<tag>_timing.json` |
| Players (`player_lab`, GPU) | video, YOLO | `plab/out/parts/*`, merged into `plab/out/<tag>_dets.npz` (boxes, confidence, kit colour, colour descriptors; about 95 MB) and `plab/out/<tag>_feat.npz` (appearance features the map's linker uses; **about 3.8 GB**) |
| Build a piece | the above | `balltrack/<ptag>.npz`, `<ptag>pan.npz`, `<ptag>ppl.npz` |
| Classify (`score_candidates cls_C.pt`) | video crops | `balltrack/<ptag>cls.npz` (each candidate's ball score) |
| Shipped chain (first the reg cut to the piece's window and a copy of the venue, then `mkpicks_one`, `ball_on_pitch`, `image_track`, `flight_scan`, `roll_speed_cache`, `flight_chain`, `smooth_ball`, `merge_air_and_ground`) | picks, reg, venue | `pitch_registration/<ptag>_reg.npz`, `<ptag>_venue.npz`, `evaluation/p2d/cls_B/<ptag>.npz` (the older tracker's 2D picks), `pitch_registration/<ptag>_*` (scan, chains, smooth, `ball3dd` = ball in 3D) |
| Stitch | every piece | whole-half `p2d/cls_B/<tag>.npz`, `pitch_registration/<tag>_ball3dd.npz`, `pitch_registration/<tag>_camera.npz` (the per-frame camera `H`, from the shipped chain's pieces: `balltrack/camera.py`), `half/<tag>/seams.json`, `log.txt` |

Each seam is cut at the frame nearest the chunk edge where both pieces' shipped balls agree
for 5 frames each side (1 px in 2D, 0.5 StatsBomb units in 3D). The old rule also needed the
Hybrid 90 balls to agree. Re-cut on the shipped ball alone, 2 of the 126 seams of the 14
passed game halves move: 7 frames in all, with the same 2D picks and camera and a 3D ball
within the tolerance.

**Started while the coach pins** (session step 3.12, 2026-11-23). Only the shipped chain
reads the camera (reg and venue). So `process_match` starts the first half's pass with the
camera step, beside the panoramas, with `--after stats/<g>/run/ship_<tag>`. Every other stage
runs while the panoramas are made and the coach pins, and the shipped chain waits for that file:
- **Beside the panoramas.** A panorama commits about 7 GB (its workers run one OpenBLAS thread each, section 2) and a pass about 20 GB, against this PC's 51 GB commit limit. The panoramas still run one after the other, so the coach's comes first.
- **Priority.** The early pass runs below normal priority, so the panoramas, the upload and the calibration keep the processor. Its children inherit that priority.
- **The go file.** The camera step writes `ship_<h1>` once the camera passes its checks (`go`). That also lifts the pass and its children back to normal priority.
- **The second half.** When everything but the shipped chain is done, the pass writes `half/<tag>/early.done`. By then its detector, players and classifier have finished, and the players' networks are freed from the GPU. The second half's pass then starts with `--after ship_<h2>`. That file is written only when the first half's pass ends, because two halves' shipped chains at once ran Windows out of commit (`flight_scan`). If the first pass ends before writing `early.done`, the second starts in `step_pass` as before.
- **Following and retrying.** `step_pass` follows each early pass like a step it launched, progress bar included. A pass that fails, or ends without `HALF DONE`, gets the usual one retry without `--after`.
- **When the run stops.** Any early pass still running is ended (`stop_early`), since preflight would refuse the next run beside it. That run resumes it from its marks.

This is not yet timed on a full game; step 3.15's run measures it. With `--no-early`, or once
the camera exists before the passes (a rerun), each pass runs after the calibration as before.

**Fast cores** (session step 3.14, 2026-11-23; off until the owner turns it on). Windows runs
the pipeline's processes in efficiency mode (EcoQoS), which on this PC's i7-14700F keeps them on
its 12 E-cores while the 16 P-core threads sit idle. With `PIQ_FAST_CORES=1`, `process_match`
takes every process a step starts out of efficiency mode within 2 s (`pitchiq/fast_cores.py`; a
child doesn't inherit it, so each one is switched), and the ball step's 3D uses 16
flight-search workers instead of 6. It is a per-process hint, like a priority; no system
setting changes. Outputs are byte-identical. Measured one piece at a time: the pass's ball
detector 1.7x faster, the ball 3D's flight search 4.1x, the TAPNext bridges 4% (they keep the
GPU busy).

## 3b. Ball (`pitchiq.balltrack.ball17 <g> <tag1>,<tag2>`, 150-270 min a game)

This is the shipped ball since 2026-11-18. It runs once per game, after both passes, with
both halves' maps running beside it (section 4). It keeps the coach's progress bar.

**Reads:**
- the game's pieces (`<ptag>.npz`, `cls`, `pan`, `ppl`)
- `pitch_registration/<tag>_ball3dd.npz` (used as the air mask)
- the video
- all of `pipeline/ballfit` and `crop_labels/crops.jsonl`

The fit reads the dev halves' pieces from the frozen `ballfit/devpass`, never from the live
ones in `balltrack/`.

**How it works:**
1. Crop caches of this game's candidates.
2. Three rounds. Each round mines the game's own confident tracks under the last round's scores, trains a ResNet-18 crop classifier on those tracks plus the dev rows (two seeds, about 5 min each), and maps the scores onto `cls_C`'s scale.
3. Two tracking runs, on the round 1 and round 3 scores (`skip:2+enter:1+kin:4`, a lost-ball gate, TAPNext bridges over gaps up to 180 frames).
4. A merge: a frame keeps a ring where either run has one, and none where they disagree by more than 15 px.
5. The 3D chain (`evaluation.mk3d v17`).

**Writes:**
- `PitchIQ-data/ball17/<g>/` (about 510 MB of working files and `log.txt`)
- `evaluation/p2d/v17/<tag>.npz`
- `h3d_v17/<tag>_ball3dd.npz` + `_smooth2.npz`: the ball everything after this reads. Each
  frame's state is 'air', 'ground', 'unsure' (seen, but neither a flight nor the ground path
  vouches for it; the stats treat it as no ball) or 'none' (`merge_air_and_ground`, step 3.3)

## 4. Map (`pitchiq.experiments.wholemap <tag> --procs 8`, 15-40 min a half)

The map links players in back-to-back 3-minute windows and puts them on the pitch. It lines
teams up across windows by shirt colour, and keeps which side each team stands on.

Each map process used to unpack the whole half's features (4.7 GB) for itself, and 8 of them
ran this PC out of memory on wb1. Now the map unpacks them once to `plab/out/<tag>_feat.npy`
and every process reads that one copy through the OS cache (`player_lab.feature_cache`);
`process_match` removes the copy after the map. It also starts only as many processes
(at most 8) as fit in the memory free then, at `MAP_GB` each, and says so in `run.log`.
When `<tag>_feat.npz` is missing, it rebuilds it first (`player_lab features`, about 13 min).

**Beside the ball.** The two halves' maps run one after the other in a thread of their own
while the ball step runs (`step_ball_and_maps`, `map_one`):
- They leave `PIQ_BALL_GB` (6 GB) free for the ball on top of the usual 3 GB, so they may
  start fewer processes than they would alone. With the ball already done they get it all.
- A missing feature file is rebuilt first, before the ball starts, because it needs the GPU.
- The progress bar stays on the ball. A map still running when the ball finishes takes the
  bar over; the maps add no time to the estimate (`"beside": "ball"` in `run-steps.json`).
- A failed map is told at once (`run.log` and the phone), and the run stops once the ball
  finishes. A failed ball waits for the maps to finish before the run stops, so no map is
  cut off halfway.

**Reads:**
- `plab/out/<tag>_dets.npz` and `_feat.npz` (unpacked to `_feat.npy` while it runs)
- `pitch_registration/<tag>_camera.npz` + `_venue.npz`: the per-frame camera and the venue
  (`balltrack/camera.py`, `PannedCamera.from_camera`). No ball file. A half passed before
  2026-11-21 gets its camera file from its pass's pieces the first time it is mapped.
- `halves.json` (for the video)

**Writes:** `PitchIQ-data/stats/map/<tag>.npz` (about 12 MB). It holds every dot on every frame: x and y in metres, track, team, role (player, keeper, referee, other) and formation slot, plus per-window kit colour and side.

## 5. Stats (`pitchiq.experiments.cached_report half <tag>`, 2-5 min a half)

First, `process_match` reads both halves' maps to decide which end ours attack. They must
disagree between halves, or the run stops. Then the stats step counts on the half's
players-and-ball table (`pitchiq/map_table.py`, `pipeline.analyse_table`). The map has already
found, linked, placed and split the players, so the video is not read again. Nothing is
detected, tracked (BoT-SORT) or split by team a second time, and there are no
fixed-camera warnings. The counting code is the same code `analyse_match` runs: possession,
territory, touches, passes, shots, xG, phase of play, keepers, the identity merge and team
shape.

- **Frames in metres.** Each seen dot is a box `PLAYER_HEIGHT_M` (1.8 m) tall standing on it, so
  the distances the stats measure in player heights are fixed lengths: 1.2 player heights is
  2.16 m. The map's guesses (dots nobody saw on that frame) are left out.
- **The ball.** The ball is the v17 ball in metres, observed where it is a detector pick. A ring
  the 3D chain could not place counts as no ball: 15% of wb1's rings, two thirds of them off the
  pitch.
- **Teams and roles.** These come from the map. Ours is the lighter or darker kit by the map's
  kit colours; a grey stretch belongs to neither team. Referees are flagged and kept, with no
  team (as on the video path). Only figures off the field are left out.

- **Pictures.** The coach's who's-who shows a picture of each figure. After the counts, the
  160 figures seen longest get one from a short read (`pitchiq/table_pictures.py`). Each map
  dot is put back on its detector box through the half's camera (on wb1, 2.6 px from the
  box's feet at the median). The tallest boxes nobody else stands over make the shortlist,
  three moments per figure at least 2 s apart. Only those frames are decoded, and the
  sharpest of the three is kept unless it is much shorter. They are then cut to the 400 KB
  budget as before (`player_thumbnails.fit_budget`). On wb1: 426 frames, 13 s, 160 pictures
  kept (the video path had 165). `--no-pictures` skips this. A missing video, or an error
  while cutting them (printed in the step's log as `pictures: FAILED`), only loses the pictures.

Woodbridge took 129 s (wb1) and 132 s (wb2) before the pictures, against 34-36 min a half on
the video path. `--video` still runs the old path (video, cached detections, BoT-SORT, kit
colours), for setting the two side by side.

**Reads:**
- `PitchIQ-data/stats/map/<tag>.npz` (the map's dots, teams, roles and kit colours)
- the v17 ball: `h3d_v17/<tag>_ball3dd.npz` and `p2d/v17/<tag>.npz`. There is no fallback: the
  run stops at the ball step when v17 is missing
- `pitch_registration/<tag>_venue.npz` (only for its fit error, which the report carries)
- the xG model
- the tag log, if one was given
- for the pictures: `plab/out/<tag>_dets.npz` (the boxes), `pitch_registration/<tag>_camera.npz`
  and the video (a few hundred frames)

**Writes:** `stats/<g>/run/pipe_<tag>.json`, the half's report with its pictures, and
`pipe_<tag>.key.json`.

## 6. Publish (3-10 min)

1. `cached_report merge pipe_<g>1.json pipe_<g>2.json --min-minutes 1` writes `pipe_<g>.json`. Counts are summed and shares weighted by half length. Player rows under a minute are dropped, to keep Firestore documents under 1 MB.
2. `pitchiq map_report <g>1.npz <g>2.npz --ours ... --into pipe_<g>.json --halves pipe_<g>1.json pipe_<g>2.json` writes `<g>_report.json`: the report plus the map's figures (team shape, field position) and the stats per position (`pitchiq/positions.py`, schema 17; seconds).
   - Each outfielder gets one of nine positions every half second: the back, middle or front line crossed with left, centre or right, from where they stood in their own team's shape over 6 s. This is not the formation video's fitted formation, which flips between near-ties (session step 3.6).
   - Per position and team it counts minutes, touches, passes, carries, shots, defensive actions, distance (equal to the stats' own) and a heat map. For our side it also lists who held each position each minute, as flat lists, because Firestore takes no arrays inside arrays.
   - `--halves` is needed because the half reports keep the maps' own track ids; the merged report has moved the second half's. Clusters are renumbered as `merge` renumbers them (`cached_report.id_shifts`).
   - On Woodbridge the summary document grows from 22 KB to 68 KB. Positions cover 96-97% of each team's passes and touches; the rest happened while the map had under seven of that team's outfielders.
3. Self-checks (`pitchiq/run_checks.py`, in process_match; seconds). They check:
   - the goals found against the score: `--score`, else the goals in the coach's log (`--tag-log`, else the match's `log` on the site);
   - that the two halves are alike, within 1.5x on rates and shares and 15 points on possession;
   - that possession spells average 5-40 s;
   - that the ball is in the air 3-35% of the time;
   - that the calibration puts the camera within 5 m of earlier games filmed from the same spot (step 2.7's comparison).

   They write `self_checks` into `<g>_report.json` and put each flag at the top of its `warnings`, which the coach's match page shows. A flag also goes to run.log and notify.log, and sets `trustworthy` to false. Nothing stops.
4. `pitchiq publish_report <g>_report.json --team T --match M` writes Firestore `cvStats/{summary,events,identity,thumbs}`. Per-player stats publish only once a coach confirms who each cluster is (`cvMapping`).

## Firestore documents it touches

All under `teams/{t}/matches/{m}/`:

| Document | Read or write | When |
|---|---|---|
| the match document | read | the start-up check |
| `cvRun/request` | read | `--ours`, when left out |
| `cvRun/progress` | write | the whole run: step, fraction done, time left, waiting for pins, stopped or finished |
| `cvPano/h1`, `h2` (+ `h<n>_<i>` chunks) | write | step 2: the panorama to pin, and problems with the pins |
| `cvPins/h1`, `h2` | read | step 2: the coach's pins |
| `cvStats/*` | write | step 6 |

## One game on disk (Woodbridge)

| Where | Size |
|---|---|
| `plab/out/<tag>_feat.npz` (both halves) | about 7.5 GB |
| `balltrack/` root (candidates and pieces) | 217 MB |
| `plab/out/<tag>_dets.npz` (both) | 191 MB |
| `h3d_hyb90/` (not written since 2026-11-21) | 174 MB |
| `h3d_v17/` | 154 MB |
| `pitch_registration/` | 122 MB |
| `evaluation/p2d/*`, `evaluation/tn/` | 22 MB |
| `PitchIQ-data/autocal/<tag>/` (both) | 837 MB |
| `PitchIQ-data/ball17/<g>/` | 513 MB |
| `PitchIQ-data/pincal/<tag>/` (both) | about 100 MB |
| `PitchIQ-data/stats/<g>/run/` + `stats/map/` | 63 MB |

## Known gaps (2026-11-21)

- **A new video's burned-in scoreboard is not masked.** The panorama's SIFT skips `halves.json`'s
  `"overlay"` boxes, found by video name (`autocal.overlay_boxes`). Only Woodbridge has one,
  added by hand. On Woodbridge the scoreboard bent short links' zoom by up to 2%. A new game
  with a scoreboard gets no mask, and nothing finds one.
- **The guessed halves run wide.** On Woodbridge the finder gives 10:30-56:25 and 65:05-113:30
  against 12:50-55:15 and 66:00-111:00. That is about 7 min from before the kickoffs and after the
  whistles, passed and counted as play. It is within the finder's 2.6 min an edge.
- **A reboot is resumed only at logon.** The Task Scheduler task runs at logon, so a restart
  with nobody to log in waits for them. Windows updates are paused until 2026-12-01.

- **Re-mapping an old game outside `process_match` runs without player features.** The feature files of all 21 earlier halves were deleted on 2026-11-21 (79 GB). `process_match` rebuilds a missing one before the map, and `wholemap` now warns, but `player_lab.load_detections` alone still carries on without. Rebuild a half's features by hand with
  `PIQ_VIDEO=<video> python -m pitchiq.experiments.player_lab features --tag <tag>` (about 13 min a half).
- **The lab tools still draw the Hybrid 90 ball** (`match_video`, `maplab`, `spotlab`, `formation_video`,
  `flight_model.Track`). They work on halves passed before 2026-11-21 and not on new ones. The
  shipped steps no longer read it: the map's camera comes from the shipped chain's pieces
  (`<tag>_camera.npz`, byte-identical to the Hybrid 90 one on all 14 game halves) and the pass
  no longer builds it.

- **Stats from the table: the counts are not yet checked.** On
  Woodbridge, team possession moved 4 points on wb1 and 13 on wb2 against the video path. The
  kits are not swapped: each team's territory and event positions agree between the two paths.
  On wb1, interceptions fell by a third to a half, and team A's duels from 23 to 5. Neither choice the step left open explains
  the move: keeping the map's guesses changes wb2 possession by 1 point, and a 2.2 m player
  height by 1 point. Goal 4 checks the counts against a tagged game.

## Keeping this current

The steps are the `step_*` functions in `pitchiq/cli/process_match.py`. Each one launches a
module through `Run.py`, with the module name as the first argument. From there, follow each
module's `np.load` and `open` paths. `half.py` launches its stages through `self.run(<cwd>, <module>, ...)`
and `ball17.py` through `self.run(<name>, [<script>, ...], ...)`. The scripts used for the
2026-11-21 trace, a reachability trace rooted at `process_match` and a size measure over one
game, are in `PitchIQ-data/session-2026-11-20/pipeline-map/`.
