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
