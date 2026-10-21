# Ball tracker

Finds the ball in every frame of a window of match video, or says it cannot.
Built and tuned on the 8-26-26 Gov Livingston scrimmage (30 fps, 1920x1080).
The 5-minute review video (48:03-53:02) was accepted on 2026-09-29.

```
sh run.sh <lo> <hi> <tag> [workers]     # abs frame numbers; workers default 8
sh run.sh 86475 95475 vid1              # -> vid1.mp4, the review video
```

Settings come from the environment, with defaults in `config.py`: `PIQ_VIDEO`
(the match video; default the scrimmage file in Downloads), `PIQ_WORKERS`
(detector processes for one window, default 8), `PIQ_HALF_WORKERS` (for a
whole half, default 12) and `PIQ_LEGACY_SP` (the first labelling round, for the
legacy training scripts only). The detector needs the CUDA GPU (gpu_matcher). Keep
the workers near 8, not 16: each process holds about 388 MiB of VRAM for its
CUDA context.

The tracker is a package, `pitchiq.balltrack`. The shell scripts source
`pyenv.sh`, which picks the repo's venv python and puts the repo root on
`PYTHONPATH`. To run one script by hand, do the same (or `pip install -e .`)
and run it as a module from the folder it reads and writes:

```
cd homog && python -m pitchiq.balltrack.homog.register_frames <lo> <hi> <tag> 4
```

The `homog/` scripts read and write their files in `homog/` unless
`PIQ_HOMOG` names another folder; `evaluation/mk3d.py` points it at
`h3d_<ver>/` (and sets `PIQ_P2D=<ver>`) to run the 3D chain on one 2D version.

## The chain

| step | script | writes |
|---|---|---|
| candidates: blob detector + xgboost ranker (`rank6.model.json`), about 40 per frame, plus camera pan | `detect_candidates.py` | `<tag>.npz`, `<tag>pan.npz` |
| person boxes (the repo's YOLOv8n, via `pitchiq.detector`), used only as classifier context | `person_boxes.py` | `<tag>ppl.npz` |
| crop classifier log-odds on every candidate | `score_candidates.py cls_B.pt` | `<tag>cls.npz` |
| tracker: `ball_tracker.track_ball` on `0.75*p + 0.25*rank`, then `gap_fill.fill` | `render_review_video.py` | `<tag>.mp4` |

Tracker settings (these produced the accepted video): `track_ball(off_score=-0.5,
enter_cost=12, return_cost=6, skip_cost=4, return_drift=12, return_gate=2)`. Gap-fill `maxgap=32, rad0=20,
rgrow=5`, with synth off. Gap-fill interpolates across a gap of up to 32
frames in pan-stabilised coordinates and takes the nearest candidate to that
line. It never invents a point.

In the video:
- green: the tracker's pick
- orange: a gap-fill pick
- grey: the best candidate the tracker declined

## Hybrid 90: the accepted tracker (2026-10-03)

```
sh hybrid.sh <lo> <hi> <tag>              # runs run.sh first if <tag>cls.npz is missing
RENDER=1 sh hybrid.sh 136170 137970 fr00  # + tapnext/hyb90_fr00_136170.mp4
```

The tracker above owns every confident pick, kick and flight. TAPNext++ (Google
DeepMind) only crosses gaps of 90 frames or fewer between two confident picks.
It is seeded on the pick before the gap and run forward, then seeded on the
pick after and run backward. A bridge is accepted only when:
- each direction lands within 20 px of our pick at the far end,
- the two directions agree within 24 px on every frame,
- the gap touches no frame the 3D chain calls air.

Rejected gaps keep the shipped gap-fill. The default 3D chain then runs on the
hybrid picks (flight_chain flights + smooth_ball ground). The output is
`h3d_hyb90/<tag>_ball3dd.npz`, which holds StatsBomb 120x80 positions, height
in CR, and the state (ground or air) for every frame.

Accepted after a never-used minute (75:39-76:39). Every version was tested
against every lab; the full table is in `evaluation/report.html`, built by
`score2d.py`, `score3d.py` and `build_report.py`.

| | Ball Truth | wrong | Find the Ball | passes | Air Lab hit/false | Flight Lab 2 hit/false |
|---|---|---|---|---|---|---|
| Hybrid 90 + default | 78 % | 6 | 36/47 | 14/16 | 14/1 | 13/1 |
| cls_B (above) | 72 % | 4 | 34/47 | 14/16 | in report | in report |
| TAPNext alone | 9 % | 70 | 9/47 | 0/16 | 0 flights | 0 flights |

Speed (RTX 4060, fr00): 174 s of GPU per match minute, about 2.2 h per half.
TAPNext is compute-bound at 160 ms a step, so the savings come from running
fewer steps. Neither of these changes the output:
- gaps touching air are never tracked (hybrid.py would reject them anyway);
- the backward run is skipped when the forward track already missed its
  landing.
Together they cut a quarter of the track-frames. Things that did not help:
cudnn.benchmark, batching tracks (B=2 and B=4 are slower per track), and a
second process. `TNS=384` (a 384 px window instead of 512) runs in 91 s per
minute and changes 3% of fr00's frames; it is not the default until the labs
say it is no worse.

## A whole half: `half.py` and the ball gate

```
PIQ_VIDEO=<video> python -m pitchiq.balltrack.half <tag> <lo> <hi> [--workers 12]
```

`half.py` runs the chain `hybrid.sh` runs on a window over a whole half: detect_candidates
on the CPU in 5-minute chunks, each chunk (plus 300 frames of overlap) through
the classifier, the shipped picks, TAPNext bridges, Hybrid 90 and the 3D chain,
and the person detector on the GPU meanwhile. The pieces are stitched where
neighbours agree on the ball. It needs `homog/<tag>_reg.npz` and
`<tag>_venue.npz` first, from `python -m pitchiq.experiments.autocal_run <tag> ...
--homog`. Every stage leaves a marker in `half/<tag>/`, so a rerun after a
crash redoes only what did not finish. `rereg.py` reruns a finished half's
ball stages when only its registration has changed, without detecting again.

`ballgate.py` decides where not to draw a ball at all. Wrong rings came in
stretches minutes long, not one at a time, so per-ring thresholds did not
carry from one game to the next. What did is how sure the classifier is around
the ring for minutes on end: where the rolling median of the best classifier
score sits below 2.0, the stretch is blanked (`lost(tag, abs0, on)`). It blanks
almost all of a night half and of East Brunswick, and almost none of the
daytime dev games. `pitchiq/panning_camera.py` applies it before the pipeline sees the
ball, and `match_video.py` before drawing it.

### Layout

| dir | what |
|---|---|
| `homog/` | venue map (`venue_map.py` -> `vmap.npz`), pitch registration (`register_frames.py` -> `<tag>_reg.npz`), 3D flight chain, Air/Flight/Fix lab labels and scorers |
| `tapnext/` | `tn_engine.py` (bridges), `hyb_video.py` (the review video) |
| `reference/` | the one-candidate-at-a-time detector the shipping one was checked against (`blobs_per_candidate.py`, `single_process_run.py`, `temporal_from_sheets.py`); `python -m pitchiq.balltrack.reference.check_fast` compares them on real frames |
| `archive/` | a README listing the scripts nothing runs any more (deleted 2026-10-21) and how to restore one from git |
| `evaluation/` | `mkpicks_one.py`, `hybrid.py`, `mk3d.py` (3D per 2D version in `h3d_<ver>/`), the 2D/3D test harness, Find the Ball / Fix Lab / pass labels, and the score tables in `evaluation/results/` (`2d.json`, `3d.json`) |

### Generated, not in git

Downloaded inputs (`requirements-balltrack.txt` at the repo root says how to
get them):
- The TAPNext++ checkpoint `tapnext/tapnextpp_512.ckpt` (2.4 GB).
- The tapnet code, cloned at `tapnext/tapnet` from google-deepmind/tapnet at
  commit 730cda1.

Built here, by the scripts that read them:
- `homog/vfeats.npz` (102 MB, over GitHub's limit; rebuild with
  `python -m pitchiq.balltrack.homog.venue_features`).
- The per-window detector outputs: `<tag>.npz`, `<tag>pan.npz`,
  `<tag>ppl.npz`, `<tag>cls.npz` (`run.sh`).
- The picks: `evaluation/p2d/<version>/<tag>.npz` (`cls_B` shipped, `hyb90`
  Hybrid 90), and the TAPNext-alone runs in `evaluation/tn/`.
- The 3D chain: `h3d_<version>/<tag>_ball3dd.npz` and `_smooth2.npz` (what
  `pitchiq/panning_camera.py` reads), and `homog/<tag>_ball3dd.npz` for the shipped picks.
- The ball on the pitch: `homog/<tag>_ballsb.npz` and `.csv` (`ball_on_pitch.py`).
- A half's working state: `half/<tag>/` (stage markers, seams, log) and
  detect_candidates's `*_kNNN.json` chunk manifests; the players in `plab/out/`.
- Every video (`*.mp4`, `*.webm`), image and log, and the lab clip media.

The registrations themselves, `homog/<tag>_reg.npz`, are small and are in git.
The inputs and the scrimmage's outputs are also in the full backup at
`PitchIQ-data/scratch-2026-10-03` beside the repo. The layout keeps every
script's `S2 = dirname(HERE)` pointing here, so no path was edited in the move.

## How good it is

The held-out test set is 18 clips of 1200 frames. `ground_truth/` holds 269 human
marks (ball / off-screen). Score with `python -m pitchiq.balltrack.gap_fill_settings`. A pick counts
as right if it is within 60 px of the mark.

| | accuracy over all marks | wrong picks |
|---|---|---|
| H1 clips (wm00-08) | 74 % | 4 |
| H2 clips (wm09-17) | 70 % | 0 |

Almost all misses are abstentions (no ball chosen), not wrong picks. Dead-ball
stretches (throw-ins, goal kicks, substitutions) correctly show nothing.
Treat a wrong pick as worse than a missing one when changing anything here.

## Labels and retraining

- `labdb5/crops.jsonl` holds 1520 Crop Lab labels (ball, cleat, head,
  otherball, other, unsure). These come from the human labelling rounds.
  `croplab/index.html` is the labelling page. Label `near` ("Beside the
  ball", added in round 6) marks a crop within a ball-width or two of the
  real ball. `train_ball_classifier.py` skips it, because it counts as a right position
  but not a clean ball crop.
- `retrain.sh` trains `cls_A`/`cls_B` from those labels. It needs the training
  crop caches (`trNNcrops.npz`), which are not committed. Rebuild them with
  `run.sh` over the training clip windows, then `pseudolab.py` and `candidate_crops.py`.
- Every lab's answers are packed one set to a file: `labdb5/crops.jsonl`,
  `evaluation/findball/labels.jsonl`, `evaluation/fixlab/fixes.jsonl`,
  `evaluation/passbench/truth.jsonl`, and in `homog/` the `airlabels/clips`,
  `flightlabels/flights` and `fl2db/flights` sets. Each line is
  `["<name>.json", doc]`, the document exactly as the lab's database gave it.
  A fresh download comes down as one file per document; pack it with
  `python -m pitchiq.balltrack.labels_pack <dir>` and delete the directory.
- Never train on `ground_truth/`; it is the test set. It stays one file per mark,
  and `labels_pack.py` refuses to pack it.

## Known limits

- The classifier rejects many blurred or airborne balls; gap-fill recovers
  some of them. About 20 of 68 missed test balls were never proposed by the
  detector at all.
- Cleats are the classifier's most confident false ball.
- `reference/blobs_per_candidate.py` and `reference/temporal_from_sheets.py` read the first labelling round's
  data from an old scratchpad that no longer exists; set `PIQ_LEGACY_SP` to a
  copy to rerun them. Only the legacy training paths read it; `run.sh` does
  not.
- The tracker works in image pixels. `homog/register_frames.py` registers each frame to
  the venue map and `ball_on_pitch.py` and the 3D chain carry the ball onto the
  pitch (StatsBomb 120x80); `pitchiq/panning_camera.py` does the same for the players. The
  pitch position is only as good as the registration, which is checked by
  hand on every dev half (the Spot Check Lab's line questions).
