# xg_training

Trains the expected-goals (xG) model the pipeline runs, from StatsBomb open data,
and exports it to ONNX beside the code that loads it.

## Retraining

Install the repo with the training extras once, from the repo root
(`pip install -e .[xg-training]`; the scripts import `pitchiq.xg_model`), then
from this folder:

```bash
python main.py
```

downloads any matches missing from `xg_raw_shots_cache.pkl` (gitignored; the
first run takes a long time), trains, writes the plots to `docs/xg/`, writes the
model to `pitchiq/<MODEL_VERSION>.onnx`, and runs the written file against the
fitted model. Flags:

- `--offline` works from the cached shots alone, touches no network and draws no
  windows. This is what re-exporting after a fix needs.
- `--fast` skips the five-fold cross-validation (25 fits).

A retrain that changes the model bumps `MODEL_VERSION` in `pitchiq/xg_model.py`.
That renames the file both training and `pitchiq/xg_bridge.py` use, so the two
cannot drift apart. Then re-run `tests/test_sample_xg.py`, which pins the sample
report's xG figures to the real model.

## Files

| File | What it does |
| --- | --- |
| `main.py` | Load, train, plot, export, verify. |
| `data.py` | StatsBomb download, the raw-shot cache, `TOURNAMENTS`. |
| `features.py` | One shot event -> the features, in StatsBomb 120x80 space. |
| `train.py` | Filtering, the calibrated XGBoost fit, its printed scores and plots. |
| `export.py` | ONNX export of the whole calibrated model, and the check of the written file. |

The feature order (`FEATURES`), the model's file and version, and the goal
geometry (`distance_sb`, `shot_angle`, `in_shot_cone_sb`) live in
`pitchiq/xg_model.py`. Training and the pipeline import the same code, so a
defender is counted in the cone the same way in both.

## Why it is built this way

Moved verbatim from comments in `main.py` in the deep clean (D-2).

### The model file

xg_model6 -> 7 on 2026-08-06, when the export below was found to be throwing
away the calibration step; 7 -> 8 the same day, dropping shot_height. See the
note above the ONNX export, and the one above FEATURES.

Written straight to pitchiq/, where pitchiq/xg_bridge.py loads it: one copy, so the
model trained here and the model the pipeline runs cannot drift apart. A
retrain that changes the model bumps the number, as 6 -> 7 -> 8 did, and
MODEL_PATH in xg_bridge.py with it. The plots go to docs/xg/.

### Offline runs

Offline re-runs the whole thing from the cache and touches no network.
The cache is the raw shot events, so parse() and everything below it can
be changed and re-run without re-downloading two thousand matches — which
is what this file was already structured for, and what makes fixing an
export bug a five-minute job rather than an afternoon.

### Feature order

Order matters. The model takes a bare 12-wide float array with no names
attached, so this list is mirrored by hand in one other place — FEATURE_ORDER
in pitchiq/xg_bridge.py — and a reordering here is silent there. Nothing can check
the two against each other but a person.

(Since D-3 there is one list, `FEATURES` in `pitchiq/xg_model.py`, and both
sides import it.)

### No shot height

shot_height is deliberately not in that list, and parse() still computes it so
this note has something to point at.

It is `end_location[2]` — the height the ball was at when it finished, which
is known only after the shot is taken. It is an outcome wearing a feature's
clothes, and the model used it exactly that way: 8,733 of these shots end
above 3 m and convert at 0.000, because a ball four metres up went over the
bar and the model can see it.

A single fixed camera plus a homography cannot measure the z axis at all, so
pitchiq/xg_bridge.py could only ever substitute a constant — and scored that way
the 12-feature model predicted 9,213 goals against the 6,014 actually scored,
because the constant sits in the band where most shots on target live. The
feature was not free information; it was a leak that made the training numbers
look good and the production numbers wrong by half.

Dropping it costs real accuracy on this dataset and buys accuracy on the only
inputs the pipeline can actually produce.

### Free kicks and penalties

Direct free kicks are dropped: they have their own physics and a wall in
front of them, and one model cannot hold both them and open play. The
comment here used to say penalties were dropped too. They are not — a
penalty is type "Penalty", not "Free Kick", so it survives this line and
reaches the model with is_open_play = 0.

### The goals sum

The one line that would have caught the export bug. A set of xG values
that does not add up to the goals actually scored is not xG, whatever
else it is, and this says so in one number.

### The ONNX export

The whole calibrated model — not the classifier inside it.

Until 2026-08-06 this line exported `xgb_estimator`, which is one of the
five fold estimators CalibratedClassifierCV fitted, with the isotonic step
left behind in this process. Every figure printed above was measured on
`model`; every figure the app has ever shown came out of that estimator.
They are not the same model, and the difference is not subtle: the base is
deliberately reweighted by scale_pos_weight (about 9, since roughly one
shot in ten is a goal) and the calibration exists to undo exactly that.
Exported raw, it read about six times high near goal — 0.69 for a clear
shot from 14 metres, 0.89 for a penalty.

onnxmltools.convert_xgboost cannot see a calibrated wrapper, which is
presumably how it happened. Registering the XGBoost converter with
skl2onnx lets convert_sklearn walk the whole estimator and emit the
isotonic step with it. zipmap=False keeps `probabilities` a plain float
tensor: the shape pitchiq/xg_bridge.py reads, and the only one onnxruntime-web
could when the browser ran the model (it no longer does).

Check what was written, not what was fitted. The bug this replaces was
invisible precisely because nobody ever ran the exported file.

### Verifying the export

Not a formality. The bug this replaces was invisible for as long as it was
because the exported file was never once run against the model it came from
— every figure anyone checked came from `model`, and every figure the app
showed came from the file.

The two do not agree to the last bit, and cannot. Isotonic calibration is a
step function, the trees are exported with float32 thresholds, and a shot
whose raw margin lands within rounding distance of a step edge comes out on
the other plateau. Measured over 8001 held-out shots: identical to 3.7e-10
for 98% of them, over 0.001 for 0.7%, and one shot at 0.0129. The mean
agrees to five decimal places, so nothing that sums a match's worth of shots
can tell the difference.

So the thresholds are set to catch a real regression — a dropped calibration
step moves the mean by ~0.35, an ordering or feature mistake moves
everything — and not the plateau edges.

### The console encoding

Every print below has an arrow in it, and a Windows console defaults to
cp1252, which cannot encode one. Without this the script trains for two
minutes and then dies on its first line of output.
