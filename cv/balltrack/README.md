# Ball tracker

Finds the ball in every frame of a window of match video, or says it cannot.
Built and tuned on the 8-26-26 Gov Livingston scrimmage (30 fps, 1920x1080).
The 5-minute review video (48:03-53:02) was accepted on 2026-09-29.

```
sh run.sh <lo> <hi> <tag> [workers]     # abs frame numbers; workers default 8
sh run.sh 86475 95475 vid1              # -> vid1.mp4, the review video
```

Set `PIQ_VIDEO` to point at another video. The default is the scrimmage file
in Downloads. The detector needs the CUDA GPU (gmatch2). Use 8 workers, not
16: each process holds about 388 MiB of VRAM for its CUDA context.

## The chain

| step | script | writes |
|---|---|---|
| candidates: blob detector + xgboost ranker (`rank6.model.json`), about 40 per frame, plus camera pan | `drun.py` | `<tag>.npz`, `<tag>pan.npz` |
| person boxes (the repo's YOLOv8n, via `cv.detector`), used only as classifier context | `ppl.py` | `<tag>ppl.npz` |
| crop classifier log-odds on every candidate | `clsscore.py cls_B.pt` | `<tag>cls.npz` |
| tracker: `dtrack6.viterbi7` on `0.75*p + 0.25*rank`, then `gapfill.fill` | `render_vid.py` | `<tag>.mp4` |

Tracker settings (these produced the accepted video): viterbi7 `e_off=-0.5,
c_enter=12, c_near=6, skip=4, drift=12, gate=2`. Gap-fill `maxgap=32, rad0=20,
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
hybrid picks (vflchain2 flights + vsmooth ground). The output is
`h3d_hyb90/<tag>_ball3dd.npz`, which holds StatsBomb 120x80 positions, height
in CR, and the state (ground or air) for every frame.

Accepted after a never-used minute (75:39-76:39). Every version was tested
against every lab; the full table is in `alltests/report.html`, built by
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

### Layout

| dir | what |
|---|---|
| `homog/` | venue map (`vmap*`), pitch registration (`vreg.py` -> `<tag>_reg.npz`), 3D flight chain, Air/Flight/Fix lab labels and scorers |
| `tapnext/` | `tn_engine.py` (bridges), `hyb_video.py` (the review video) |
| `alltests/` | `mkpicks_one.py`, `hybrid.py`, `mk3d.py` (3D per 2D version in `h3d_<ver>/`), the 2D/3D test harness, Find the Ball / Fix Lab / pass labels, `results/` |

Some files are not in git:
- The TAPNext++ checkpoint `tapnext/tapnextpp_512.ckpt` (2.4 GB).
- The tapnet code, cloned at `tapnext/tapnet` from google-deepmind/tapnet at
  commit 730cda1.
- `homog/vfeats.npz` (102 MB, over GitHub's limit; rebuild with
  `python vfeats.py`).
- The per-window detector outputs (`*.npz`).
- The lab clip media.

All of these exist in this checkout and in the full backup at
`Desktop/Repos/PitchIQ-data/scratch-2026-10-03`. The layout keeps every
script's `S2 = dirname(HERE)` pointing here, so no path was edited in the move.

## How good it is

The held-out test set is 18 clips of 1200 frames. `truthdb/` holds 269 human
marks (ball / off-screen). Score with `python gapfill_fixed.py`. A pick counts
as right if it is within 60 px of the mark.

| | accuracy over all marks | wrong picks |
|---|---|---|
| H1 clips (wm00-08) | 74 % | 4 |
| H2 clips (wm09-17) | 70 % | 0 |

Almost all misses are abstentions (no ball chosen), not wrong picks. Dead-ball
stretches (throw-ins, goal kicks, substitutions) correctly show nothing.
Treat a wrong pick as worse than a missing one when changing anything here.

## Labels and retraining

- `labdb5/crops/` holds 1520 Crop Lab labels (ball, cleat, head, otherball,
  other, unsure). These come from the human labelling rounds.
  `croplab/index.html` is the labelling page. Label `near` ("Beside the
  ball", added in round 6) marks a crop within a ball-width or two of the
  real ball. `train_cls.py` skips it, because it counts as a right position
  but not a clean ball crop.
- `retrain.sh` trains `cls_A`/`cls_B` from those labels. It needs the training
  crop caches (`trNNcrops.npz`), which are not committed. Rebuild them with
  `run.sh` over the training clip windows, then `pseudolab.py` and `crops.py`.
- Never train on `truthdb/`; it is the test set.

## Known limits

- The classifier rejects many blurred or airborne balls; gap-fill recovers
  some of them. About 20 of 68 missed test balls were never proposed by the
  detector at all.
- Cleats are the classifier's most confident false ball.
- `feat4.py`, `feat_t4.py` and `dtrack2.py` still name an old scratchpad for
  the first labelling round's data. Only the legacy training paths read it;
  `run.sh` does not.
- Positions are image pixels. Mapping them onto the pitch is the next step.
