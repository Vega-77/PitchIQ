# The full pipeline: video to published stats

What `python -m pitchiq process_match` does: each step, the files it reads and writes,
and why. Traced from the code at `de4a918` (2026-11-21), with sizes and times from the
Woodbridge run (game `wb`, halves `wb1` and `wb2`, 18-19 Nov 2026).
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
| pass | `half/<tag>/log.txt` says `HALF DONE`, and `h3d_hyb90/<tag>_smooth2.npz` and `plab/out/<tag>_dets.npz` exist. Inside a half, every stage of every piece leaves `half/<tag>/<ptag>.<stage>.done`. |
| ball | `ball17.version(tag) == 'v17'` (its `p2d/v17` and `h3d_v17` files exist) |
| map | `PitchIQ-data/stats/map/<tag>.npz` exists |
| stats | `pipe_<tag>.json` exists and `pipe_<tag>.key.json` matches this run's ours, attacking end and tag log |
| publish | never skipped: the merge, map report and publish rerun every time |

Each step logs to `PitchIQ-data/stats/<g>/run/logs/<step>.log`, and the run as a whole logs
to `run.log` beside it.

**After a reboot.** While it runs, `process_match` keeps `stats/<g>/run/active.json`. That
file holds the command, the folder, the PIQ_* settings, the key's path and the PID. A run
that ends clears it, whether it finished or stopped. A run the machine killed leaves it.
Two minutes after logon, the Task Scheduler task `\PitchIQ\Resume run` runs
`pythonw -m pitchiq.cli.resume`. That relaunches the newest cut-off run with no window, its
output in `run/resume.log`, and notes it in `run.log`. After 3 resumes in a row it gives up
and leaves a note instead.
- `python -m pitchiq resume --list` shows the marks.
- `--install` and `--uninstall` add and remove the task.

While it runs, the run also asks Windows not to sleep. That is the request a video player
makes, not a power setting.

**When a step fails.** A failed step is tried once more before the run stops, since every
step resumes from what it finished. When the end of its log says it ran out of memory
(`MemoryError`, `Unable to allocate`, the paging file, CUDA out of memory), the second try
runs fewer processes: the map with half its `--procs`, the pass with half its detector
workers and one ball lane. Failing twice stops the run with the end of the step's log.

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
| `balltrack/tapnext_bridge/tapnextpp_512.ckpt` | 2 GB | pass, ball | TAPNext++, which tracks the ball through gaps ("bridges") |
| `pitch_registration/vpitch.npz`, `vtop.npz` | 2 KB | 3D chain, stats | Default pitch template (used when a half has no venue file of its own) |
| `pitchiq/xg_model8.onnx` | 4 MB | stats | xG for shots |
| `botsort.yaml` (ships with Ultralytics) | - | stats | Player tracker settings |
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
   - A loop closure then solves every frame's aim and zoom together, since the camera never moves.
   - Finally a colour panorama is built. The files go to `PitchIQ-data/pincal/<tag>/` (`keys/`, `keys.pkl`, `match.pkl`, `solve.pkl`, `pano_rgb.jpg/json`; about 50 MB).
2. **To the coach**: `upload_pano` writes the panorama to Firestore `cvPano/h1` (plus chunk documents `h1_<i>`). The coach pins it on `calibrate/?team&match&half`. The run polls `cvPins/h1` every minute and saves what it finds as `pincal/<tag>/pins.json`.
3. **Transfer** (`pincal transfer <h1> <h2>`): writes `pincal/<h2>/transfer_from_<h1>.json`. If the tripod stayed put, the first half's pins carry over to the second, so the coach pins once. Otherwise the second half gets its own panorama upload.
4. **Pin check**: `fit_pins` checks every pin against the rest. A set that doesn't fit is renamed `pins_rejected.json`, the coach is told why (`mark_problem` on `cvPano`), and the run waits for new pins.
5. **Calibration** (`pincal cal <tag> --with <other> --install`): pins give camera position, aim and pitch size, and the paint polishes the aim. It writes `pincal/<tag>/cal/` and `pin_pose.json`, and installs:
   - `balltrack/pitch_registration/<tag>_reg.npz`: per frame, the image-to-pitch mapping (about 1 MB)
   - `balltrack/pitch_registration/<tag>_venue.npz`: the camera's reference view, its fit error and the pitch size

   **Every later step uses these two files to put pixels on the pitch.**
6. **Check** (`pitchiq.experiments.cal_health <tag>`): reads the video, reg and venue, and measures paint on the lines, far and near player height, and feet on the pitch. It writes `stats/<g>/run/cal_health_<tag>.json`. If `checks.cal_flags` finds a problem, the run stops unless `--trust-cal` is given.

With `--no-pins`, `pitchiq.experiments.autocal_run` replaces steps 1-5. It first calibrates one minute
(`autocal/<g>cam<k>/full.pkl`), then each half.

## 3a. Pass (`pitchiq.balltrack.half <tag> <lo> <hi>`, 50-125 min a half)

The half is cut into 5-minute pieces (9,000 frames, plus 300 frames of overlap each side;
Woodbridge had 9 and 10). The stages run in parallel lanes:
- the ball detector on the CPU
- players on the GPU
- the classifier and TAPNext bridges on the GPU
- two lanes of CPU ball jobs

| Stage | Reads | Writes |
|---|---|---|
| Ball candidates (`detect_candidates`, 12 processes) | video, ranker | `balltrack/<tag>.npz` (every candidate blob, about 28 MB), `<tag>pan.npz` (camera pan), `<tag>_timing.json` |
| Players (`player_lab`, GPU) | video, YOLO | `plab/out/parts/*`, merged into `plab/out/<tag>_dets.npz` (boxes, confidence, kit colour, colour descriptors; about 95 MB) and `plab/out/<tag>_feat.npz` (appearance features the map's linker uses; **about 3.8 GB**) |
| Build a piece | the above, reg | `balltrack/<ptag>.npz`, `<ptag>pan.npz`, `<ptag>ppl.npz`, the reg cut to the piece's window, a copy of the venue |
| Classify (`score_candidates cls_C.pt`) | video crops | `balltrack/<ptag>cls.npz` (each candidate's ball score) |
| Shipped chain (`mkpicks_one`, then `ball_on_pitch`, `image_track`, `flight_scan`, `roll_speed_cache`, `flight_chain`, `smooth_ball`, `merge_air_and_ground`) | picks, reg, venue | `evaluation/p2d/cls_B/<ptag>.npz` (the older tracker's 2D picks), `pitch_registration/<ptag>_*` (scan, chains, smooth, `ball3dd` = ball in 3D) |
| Bridges (`tn_engine`) | video, TAPNext | `evaluation/tn/bridges_<ptag>.npy` |
| Hybrid 90 (`evaluation.hybrid`, then `evaluation.mk3d hyb90`) | picks + bridges | `evaluation/p2d/hyb90/<ptag>.npz`, `h3d_hyb90/<ptag>_*` |
| Stitch | every piece | whole-half `p2d/{cls_B,hyb90}/<tag>.npz`, `pitch_registration/<tag>_ball3dd.npz`, `h3d_hyb90/<tag>_ball3dd.npz`, `_smooth2.npz` (ball, plus the per-frame camera `H`), `half/<tag>/seams.json`, `log.txt` |

## 3b. Ball (`pitchiq.balltrack.ball17 <g> <tag1>,<tag2>`, 150-270 min a game)

This is the shipped ball since 2026-11-18. It runs once per game, after both passes.

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
- `h3d_v17/<tag>_ball3dd.npz` + `_smooth2.npz`: the ball everything after this reads

## 4. Map (`pitchiq.experiments.wholemap <tag> --procs 8`, 15-40 min a half)

The map links players in back-to-back 3-minute windows and puts them on the pitch. It lines
teams up across windows by shirt colour, and keeps which side each team stands on.

Each map process used to unpack the whole half's features (4.7 GB) for itself, and 8 of them
ran this PC out of memory on wb1. Now the map unpacks them once to `plab/out/<tag>_feat.npy`
and every process reads that one copy through the OS cache (`player_lab.feature_cache`);
`process_match` removes the copy after the map. It also starts only as many processes
(at most 8) as fit in the memory free then, at `MAP_GB` each, and says so in `run.log`.
When `<tag>_feat.npz` is missing, it rebuilds it first (`player_lab features`, about 13 min).

**Reads:**
- `plab/out/<tag>_dets.npz` and `_feat.npz` (unpacked to `_feat.npy` while it runs)
- `pitch_registration/<tag>_reg.npz` + `_venue.npz` (the projector)
- `h3d_hyb90/<tag>_smooth2.npz` (its per-frame camera `H`)
- `halves.json` (for the video)

**Writes:** `PitchIQ-data/stats/map/<tag>.npz` (about 12 MB). It holds every dot on every frame: x and y in metres, track, team, role (player, keeper, referee, other) and formation slot, plus per-window kit colour and side.

## 5. Stats (`pitchiq.experiments.cached_report half <tag>`, 30-75 min a half)

First, `process_match` reads both halves' maps to decide which end ours attack. They must
disagree between halves, or the run stops. Then the stats step runs the analysis
(`pipeline.analyse_match`: BoT-SORT, kit colours, teams, possession, events, keepers, the
identity merge) on cached detections instead of running YOLO again.

**Reads:**
- the video (decoded frames)
- `plab/out/<tag>_dets.npz` (rows at confidence 0.08 and above)
- the ball through `PannedCamera.from_tracker`: `h3d_v17/<tag>_smooth2.npz`, `_ball3dd.npz` and `p2d/v17/<tag>.npz` (Hybrid 90 when v17 is missing)
- `pitch_registration/<tag>_venue.npz`
- `halves.json`, the xG model and `botsort.yaml`
- the tag log, if one was given

**Writes:** `stats/<g>/run/pipe_<tag>.json`, the half's report, and `pipe_<tag>.key.json`.

## 6. Publish (3-10 min)

1. `cached_report merge pipe_<g>1.json pipe_<g>2.json --min-minutes 1` writes `pipe_<g>.json`. Counts are summed and shares weighted by half length. Player rows under a minute are dropped, to keep Firestore documents under 1 MB.
2. `pitchiq map_report <g>1.npz <g>2.npz --ours ... --into pipe_<g>.json` writes `<g>_report.json`: the report plus the map's figures (team shape, field position).
3. `pitchiq publish_report <g>_report.json --team T --match M` writes Firestore `cvStats/{summary,events,identity,thumbs}`. Per-player stats publish only once a coach confirms who each cluster is (`cvMapping`).

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
| `h3d_hyb90/` | 174 MB |
| `h3d_v17/` | 154 MB |
| `pitch_registration/` | 122 MB |
| `evaluation/p2d/*`, `evaluation/tn/` | 22 MB |
| `PitchIQ-data/autocal/<tag>/` (both) | 837 MB |
| `PitchIQ-data/ball17/<g>/` | 513 MB |
| `PitchIQ-data/pincal/<tag>/` (both) | about 100 MB |
| `PitchIQ-data/stats/<g>/run/` + `stats/map/` | 63 MB |

## Known gaps (2026-11-21)

- **Re-mapping an old game outside `process_match` runs without player features.** The feature files of all 21 earlier halves were deleted on 2026-11-21 (79 GB). `process_match` rebuilds a missing one before the map, and `wholemap` now warns, but `player_lab.load_detections` alone still carries on without. Rebuild a half's features by hand with
  `PIQ_VIDEO=<video> python -m pitchiq.experiments.player_lab features --tag <tag>` (about 13 min a half).
- **The pass still builds the Hybrid 90 ball** (bridges, hybrid and its own 3D chain for every piece). Since v17 shipped, it is read only by:
  - the "pass done" check
  - the map's per-frame camera (`h3d_hyb90/<tag>_smooth2.npz`)
  - the fallback when v17 is missing

  Whether the camera can come from elsewhere is unchecked.

## Keeping this current

The steps are the `step_*` functions in `pitchiq/cli/process_match.py`. Each one launches a
module through `Run.py`, with the module name as the first argument. From there, follow each
module's `np.load` and `open` paths. `half.py` launches its stages through `self.run(<cwd>, <module>, ...)`
and `ball17.py` through `self.run(<name>, [<script>, ...], ...)`. The scripts used for the
2026-11-21 trace, a reachability trace rooted at `process_match` and a size measure over one
game, are in `PitchIQ-data/session-2026-11-20/pipeline-map/`.
