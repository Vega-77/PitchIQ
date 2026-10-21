# `pitchiq/xg_bridge.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Turn a detected shot into the 11 features the existing xG model expects.

    STATUS — the model half is verified; the shot-detection half is not.

`build_features`, `feature_vector` and `_predict` now run against the real model
(tests/test_xg_bridge.py, `TestTheRealModel`). This is the only place xG is
computed: in Python, when the pipeline runs, and stored with the report. The
browser's copy of the feature code, which this once agreed with to three
decimal places, left with the xG sandbox on 2026-10-07; the site only shows
the stored figures. `xg_for_shots` connects that to the pipeline, which
until 2026-08-02 nothing did — `attach_xg` had no caller, so every xG figure
from the report JSON to a player's phone was null. What remains unverified (still, 2026-10-20) is
upstream of here: whether a shot can be spotted in footage at all, and whether
the shooter, keeper and defenders handed to `ShotContext` are the right ones.

The model itself was replaced on 2026-08-06. Everything before that date ran
xg_model6, which was exported out of the middle of a CalibratedClassifierCV
without the isotonic step, and read about six times high near goal. It was
replaced twice the same day — xg_model7 restored the calibration, xg_model8
dropped `shot_height` (see below). Over the 53,337 training shots xg_model8
predicts 6,057 goals against the 6,014 actually scored, AUC 0.884, and tracks
the real conversion rate closely in every distance band. Any figure, note or
screenshot from before 2026-08-06 is on a different scale.

This is where the computer vision work meets the model already trained in
`xg_training/main.py`, which writes it beside this file as `xg_model8.onnx`
(the only copy; its plots are in docs/xg/). The feature
order below must match `FEATURES` in main.py exactly; a mismatch does not raise,
it silently produces a plausible-looking wrong number.

(Since the deep clean, `FEATURES`, the model path and version, and the goal
geometry live in `pitchiq/xg_model.py`, which training and this module both
import; `xg_training/export.py` writes the file.)

Two things are known to be unresolved rather than merely untested:

* **shot_height was never a feature, and is gone.** *(Resolved 2026-08-06 —
  kept here because it is the most instructive thing this module has hit.)*

  It is a z-axis value, and a homography maps a plane, so there was no way to
  get it from one fixed camera and a constant was substituted on every shot.
  That much was known and documented. What was not: in the training script it is
  `end_location[2]` — **where the ball finished** — which is known only after the
  shot. An outcome dressed as a feature. 8,733 of the training shots end above
  3 m and convert at exactly 0.000, because a ball that ended four metres up
  went over the bar and the model could see that.

  Measured over all 53,337 training shots:

        model / input                     AUC     Brier   predicted   actual
        12 features, real end height     0.935   0.061      6,061      6,014
        12 features, height fixed at 0.6 0.857   0.080      9,213      6,014
        11 features, no height at all    0.884   0.073      6,057      6,014

  The middle row is what this module produced for as long as the feature
  existed: a total half again too high, in one direction, at every band. The
  first row is the one every printed metric described and no camera could ever
  reach. Dropping the feature beats feeding it a constant on **every** axis at
  once — better AUC, better Brier, and a total that lands on the goals actually
  scored.

  The general lesson, which is worth more than the fix: a feature the production
  path cannot supply is not a bonus that degrades gracefully. It is a hole the
  model has learned to lean on, and filling it with a constant puts the model
  somewhere it has never been.

* **The model has never seen CV-derived inputs**, and it is sensitive to that.
  It was trained on clean, human-verified StatsBomb data; positions from
  detection and homography are not. Re-measured 2026-08-06 by
  `validate_against_noise` against xg_model8, 400 trials over five spots
  averaging 0.188 xG:

        noise    mean shift   p95     max     share of the baseline
        0.25 m     0.019     0.065   0.099     10%  /  34%
        0.50 m     0.030     0.095   0.253     16%  /  50%
        1.00 m     0.043     0.168   0.267     23%  /  89%
        2.00 m     0.062     0.242   0.538     33%  / 129%
        4.00 m     0.107     0.380   0.624     57%  / 202%

  So half a metre of position error — the band `pitchiq/calibrate.py` accepts as a
  good calibration — moves a single shot's xG by about 0.030 typically and 0.095
  at the 95th percentile, half the number. That is fine for "a decent chance"
  and not fine for ranking two shots 0.1 apart. At 2 m the p95 shift exceeds the
  xG itself, which is the point past which per-shot numbers are meaningless.

  **Removing shot_height made the model more position-sensitive, not less**, and
  in hindsight obviously: with eleven features instead of twelve, distance and
  angle carry more of the answer, so moving a player moves the answer further.
  The p95 at half a metre went from a third of the number to half of it, and the
  per-shot display band in shared/report.js tightened from 1 m to 0.5 m to
  match. A more honest model is a fussier one about where its inputs came from.

  Summing helps, and by a measured amount rather than a hoped-for one: simulated
  over a half's six shots, the *total* lands within 8% of the truth at 0.5 m,
  12% at 1 m, 18% at 2 m and 26% at 4 m, while a single shot at 1 m already
  carries an error bar nearly as wide as itself. That gap between the two is the
  whole reason `xgTrust` has a band where the total shows and no individual
  figure does. Pinned by tests/test_xg_noise.py.

* **Pitch dimensions change the answer.** `Pitch.to_statsbomb` normalises by
  the configured pitch length, so a penalty spot — 11m out on any field — reads
  as ~13.9 StatsBomb units on a 95m pitch and ~12.0 on a 110m one, and the model
  sees two different shots. Whether that matches how StatsBomb normalises their
  own data has not been confirmed against their documentation; if they assume a
  standard pitch instead, this should use a fixed metres-per-unit factor. Until
  that is settled, the pitch dimensions in a calibration are not a cosmetic
  setting — measure the real field. Pinned by a test in tests/test_xg_bridge.py.

## Moved from the code

### `DEFAULT_SHOT_HEIGHT`

DEFAULT_SHOT_HEIGHT was substituted on every shot until 2026-08-06, when
`shot_height` turned out to be where the ball *finished* rather than anything
about the shot — so feeding a constant was feeding the model a claim about the
outcome, and a wrong one. See the module docstring.

### `MODEL_PATH`

The model behind every xG number anyone has actually looked at. The browser
loaded the same file until the xG sandbox left the site on 2026-10-07; naming
one file here was what kept the pipeline and the sandbox on one model.

Two predecessors were retired on 2026-08-06.

xg_model6 was the raw XGBoost classifier out of the middle of a
CalibratedClassifierCV, exported without the isotonic step that made its
probabilities mean anything, and with scale_pos_weight ~= 9 still pushing them
up — so it read about six times high near goal. Every xG this project showed
before that date came from it.

xg_model7 fixed the calibration and still took `shot_height`, which nothing
here can measure, so a constant went in and the totals came out about half
again too high. xg_model8 drops the feature; see the module docstring.

### `shot_context_from_tracking`

"Their own goal" is the goal the shooter is *attacking*. This searched the
shooter's own end instead until 2026-08-02, which inverted the guess
completely: the keeper standing on his line was counted as a defender
blocking the shot, and whichever opponent had dropped deepest — often fifty
metres the wrong side of the ball — was handed to the model as the keeper.
The model was therefore told the goal was unguarded on every shot where
nobody named the keeper, which inflates xG. Nothing called this function at
the time, so no published number was ever affected; see
tests/test_xg_bridge.py::TestKeeperGuess.

### `_SESSION`

One session per process. Loading a model per call is slow, and the project
has already paid once for putting several models in one process — see the
benchmark note in docs/history/ROADMAP-archive.md that produced figures
wrong by 10x.
