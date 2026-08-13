# PitchIQ — Camera-Only Stats Roadmap

Goal: go from "type in shot geometry manually" to "point a camera at a high school
match and get player/team stats out the other end." Primary use case: give the coach
things at halftime they genuinely can't see with their own eyes (fatigue, shape,
cumulative xG, dangerous turnover zones). Secondary use case: after the game, players
log in to see their own report, and the coach gets a fuller tactical debrief.

Three scope tiers, after a hard look at what a month with two people actually buys:
- **[Demo]** — realistic for the actual 1-month test with the old team. Deliberately narrow.
- **[MVP]** — the real product baseline once the demo proves the concept; probably 2-3 more months.
- **[Stretch]** — long-term, not urgent.

Read the Feasibility Reality Check below before treating anything tagged **[MVP]** as
a month-1 commitment — a lot of it isn't, on purpose.

**Design decision: human-in-the-loop, not full autonomy.** There are two points where a
human is intentionally in the loop instead of asking the CV to be perfect:
- **Live, during the game** — a sideline tablet captures substitutions and event types
  as they happen (Phase 3).
- **After the game** — a reviewer confirms/corrects everything against the recorded
  video (Phase 11).

Both are cheaper and more reliable than teaching the CV to classify fouls or read
jersey numbers robustly in month 1.

---

## Current Status (2026-07-27)

**Built and verified:**
- `cv/` — reusable detection package + `spike_detect` CLI (Phase 5 spike, done)
- **Firebase backend** — Firestore + Google Auth, replacing the local FastAPI server.
  `firestore.rules` is the entire security boundary; 43 emulator tests pass via
  `npm test` (Phase 2 + 14, done for [Demo])
- **`index.html` / `coach/` / `player/`** — landing, coach dashboard, player portal
  (Phase 15, done for [Demo])
- `live-tagging/` — the tablet tool, now on Firestore with offline queueing (Phase 3)
- `xg-sandbox/` — the manual xG sandbox, moved off the site root
- `halftime/` — the three-minute touchline read, from tagged data alone (Phase 15)

**Blocked on:** footage from the coach — specifically a raw/native-resolution export
rather than a screen recording, ideally the uncropped wide feed rather than the
auto-tracked crop (see Phase 1). Also pending: permission to use game footage, and
confirmation of what camera system the school actually runs.

**Also needed before real use:** a Firebase project — see `FIREBASE_SETUP.md`. The
console steps (creating the project, enabling Google sign-in, deploying rules, adding
coaches to the allowlist) can't be scripted from the repo.

**Next up:** get the footage. Native resolution, from a camera that holds still,
framed so jersey numbers are faintly legible — `FOOTAGE_DAY.md` is the whole
briefing, including what to check before leaving the field and what order to run
things in afterwards. That single input unblocks calibration, tracking quality
and possession simultaneously.

Everything reachable without it has now been built, in two passes: non-player
exclusion, in-play/dead-ball splitting, the narrowed cluster picker, the review
tool with recall, and match video on every report; then xG actually being
computed rather than merely computable, the tag-log/CV reconciliation, precision
and recall out of the review tool, and a regression harness waiting for its
first baseline (see the two "built ahead of the footage" sections below).

The honest summary of that second pass: most of what it did was **connect things
that were already written and called by nothing**. That is worth saying plainly,
because it means the remaining gap is not a shortage of code.

### Built ahead of the footage

`cv/pipeline.py` and `cv/xg_bridge.py` wire the whole chain together — video in,
match report out, including the 12-feature bridge to the existing xG model.

**`cv/pipeline.py` has now been run end to end** (2026-07-30) via
`cv/experiments/match_report.py`. It executes: detection, ball tracking, kit
clustering, possession, tracking, projection, movement stats, heatmaps and team
shape all complete without crashing. The footage available has a camera that
pans and zooms, so none of the numbers mean anything about football — the run
was driven with a deliberately synthetic calibration to reach the metre-based
half. It proved the assembly, not the analysis.

What that run found, and what was fixed:

- **Memory made a real match impossible.** `analyse_match` buffered its entire
  window in RAM — 2.8 MB per 720p frame, ~5 GB a minute, ~227 GB for a
  45-minute half. It now streams in fixed-size batches: measured peak growth
  was 1060 MB for a 15-second window and 1091 MB for a 60-second one, so memory
  no longer scales with match length. Output is byte-identical before and after.
- **A zero that meant "not attempted".** Without a calibration the summary
  printed `players tracked 0`, because tracking sits inside the calibration
  branch and never ran. It says so now.
- **Team shape had no sanity check.** The run reported a team 126m deep on a
  105m pitch without comment. Physically impossible shapes now raise a warning
  that names the calibration as the likely cause.
- **Seeking does not land where it is aimed.** `CAP_PROP_POS_FRAMES` seeks to
  the nearest keyframe — asking for frame 30 delivered frame 26 in test. The
  old code assumed the first frame sat exactly at `start_s`, quietly dating
  every detection wrong. The reader now reports the frame it actually landed
  on. This matters for lining CV output up with the tagged event log.

**Speed, measured properly (2026-07-31).** An earlier version of this section
claimed 150-400ms per frame and "hours, not minutes" for a half. That was wrong,
and wrong in a specific way worth recording: the benchmark harness built four
`PlayerTracker` instances in one process, each loading its own YOLO model, and
the resulting GPU memory pressure produced `NMS time limit exceeded` warnings
and timings inflated by more than 10x. Measure one model per process.

The real figures, on a 15s 720p clip on an RTX 4060 (450 frames), each from a
fresh process:

| stage | time | vs realtime |
| --- | --- | --- |
| detection only | 7.6s | 0.5x |
| detection + tracking | 19.2-19.9s | 1.3x |

So a 45-minute half is about an hour, not hours. Detection is 12.5ms a frame
batched; tracking is roughly 26ms because ByteTrack and BoT-SORT carry state
between frames and cannot be batched.

`analyse_match` now exposes the tracking levers, measured on the same clip:

| config | time | tracks kept | longest track |
| --- | --- | --- | --- |
| `botsort.yaml`, stride 1 (default) | 13.1s | 100 | 449/450 |
| `bytetrack.yaml`, stride 1 | 7.0s | 119 | 418 |
| `botsort.yaml`, stride 2 | 7.2s | 70 | 225/225 |
| `bytetrack.yaml` + stride 2, whole pipeline | 10.7s | 83 | — |

That last row is **0.7x realtime**, which is what a live half-time report would
need. The defaults deliberately stay on the slower, better option: every one of
these levers buys speed with identity, and fragmentation is already the weakest
link in the pipeline. Lowering `track_imgsz` saves about 5% and is not worth the
detections it costs.

Still open:

- [ ] **Decide the tracking trade-off on footage that can support the judgement.**
  All of the above was measured on a panning, zooming camera where tracks
  fragment for reasons that have nothing to do with the tracker. The choice
  between BoT-SORT and ByteTrack should be made against a fixed camera.
- [x] **The double model pass** — gone since the pipeline became one pass, and
  this entry outlived it. `TrackedFramePass.run` calls the detector once per
  batch and hands the tracker the detections it already has, so no frame is
  inferred twice.

  The objection recorded here was real and is worth keeping: collapsing the two
  by running the tracker at the ball's much lower confidence threshold would
  have flooded it with junk tracks. That is not how it was solved. The model
  runs once at the low threshold, the output is split by class, and **only
  people reach the tracker** — so the ball keeps its permissive threshold and
  the tracker keeps its strict one. Two thresholds on one pass, rather than one
  threshold on two.

**`cv/xg_bridge.py`'s model half is now verified** (2026-07-31). `onnxruntime`
is in the venv, `_predict` has run against the real model, and Python agrees
with the browser to three decimals on the same shot — the sandbox's default
position reads 0.068 in both. tests/test_xg_parity.py pins twelve scenarios
across the two languages plus the feature order.

Two real defects came out of writing it:

- **The two languages disagreed when no keeper was visible.** The training
  script substitutes `shot_angle([115, 40])`; the browser was substituting the
  shooter's own angle, a value the model never saw in training. The sandbox
  never hit it because it always draws a keeper — the CV pipeline would hit it
  every time the keeper was missed. Fixed in `xg-sandbox/xg-model.js`.
- **~~There are two different models both called `xg_model6.onnx`.~~** Resolved
  on 2026-08-06, and by then the smaller half of a bigger problem — see *The xG
  model was never the model that was measured* below. Both `xg_model6` files are
  retired; `xg_model8.onnx` is the only model either path loads, and the parity
  test now reads the browser's filename out of `xg-model.js` rather than
  restating it, so a future swap is one edit and not a test failure about a
  name.

What remains unverified is upstream of the bridge: whether a shot can be spotted
in footage at all, and whether the right shooter, keeper and defenders reach
`ShotContext`. That needs footage. Every component is individually tested and the joins are tested against
synthetic data; what has never happened is one run end to end on a real match,
because that needs a calibration. Points most likely to need work are marked
`UNVERIFIED` inline.

### Also built ahead of the footage (2026-08-02)

Four more phases that need no calibration and no real footage — chosen in that
order on purpose, the small corrections to numbers that were already wrong
first, then the tool that needs the other three to have produced something to
review.

**Non-players are classified, not silently mixed into team stats**
(`cv/participants.py`). Every track gets a verdict — `player` / `offfield` /
`official` / `unsure` — from screen time, how far it strayed from its own
median position measured in its own body heights (so a zooming camera doesn't
change the answer), and, when a calibration exists, how much of the time it sat
off the pitch. Only `offfield` is auto-excluded; a referee and a goalkeeper are
indistinguishable on these features without a goalmouth to measure against, so
`official` is flagged and kept — carrying a referee is a smaller error than
dropping a keeper. Every exclusion travels with the reason string that produced
it, all the way into the report JSON and onto the coach's screen. Fixed two real
bugs on the way past: `team_shape` was being built from every track on the
pitch, both teams and the referee together, and reported as Team A's; and
`no_ball_s` was structurally always zero regardless of the footage.

**In-play vs. dead-ball, from the tag log alone** (`cv/phases.py`) — the first
thing under `cv/` to read the human tag log rather than the video. A dead span
is shrunk at both ends by a slop margin and never grown, because calling live
football dead deletes possession invisibly; an untagged restart times out
rather than swallowing the rest of the half (with a shorter cap after a goal,
since nothing tags the kickoff that follows one). Feeds `cv/possession.py` — a
player standing over the ball waiting for a throw-in no longer counts as
possession, and `dead_ball_s` / `live_share` are new quality figures — and
`cv/events.py`, where every derived event now carries `in_play` and PPDA
(defined on open play) excludes the dead ones. This is *not* yet a cross-check
against an independent CV signal for out-of-bounds; today the tag log is the
only source, which is the gap the original Phase 9 bullet below still names.

**The cluster picker narrows by who was actually on** (`assets/report.js`,
`coach/coach.js`) — `rankRosterForCluster` converts a cluster's first/last
sighting from video time into match-clock time using the video offset, then
ranks the roster by how much each player's stints overlap it. The picker groups
into "On the pitch then" / "Everyone else" rather than filtering: the video
offset is the single most fiddly number in the app, and hiding based on it
would hide the correct player exactly when the offset is wrong.

**The review tool, including recall** (`cv/publish.py`, `coach/coach.js`) —
`cvStats/events` is now published (capped at 1500, keeping the most confident
but re-sorted into clock order so a truncated list reads as a truncated list
rather than as the pipeline having stopped finding things), plus a
coach-writable `cvReview/decisions` overlay kept deliberately apart from the
pipeline-authored `cvStats`, so a coach's correction can never be mistaken for
a measurement. A coach confirms, reassigns or rejects each candidate against
the embedded video — and separately records what the video *missed*, because
precision alone doesn't say whether the ball detector is good enough; recall
does.

**The match video reaches every report, not just the player portal.** A shared
`assets/match-video.js` puts the same embed-or-link-or-nothing decision and the
same tappable tick strip on the coach's match view, the half-time page, and
player reports — one module instead of three copies of the same judgement call.
Goals, cards and substitutions are marked on the team-facing versions, not
every restart; a strip with eighty ticks on it stops being something to scan.
A video link pasted after reports were already published used to never reach
players until somebody thought to re-publish; it now pushes onto the existing
`playerReports` documents directly.

**What none of this needed:** a calibration or a frame of real match footage.
All four ran against the human tag log, roster stints, and fixture data, and
are covered by `tests/test_participants.py`, `tests/test_phases.py`, and
`tests/video.test.js`.

Two open questions found while writing them, both pinned by tests so they cannot
be quietly forgotten:
- **Pitch dimensions move every xG number.** `to_statsbomb` normalises by the
  configured pitch length, so a penalty spot reads as ~13.9 StatsBomb units on a
  95m pitch and ~12.0 on a 110m one. Whether that matches how StatsBomb
  normalises their own data has not been confirmed against their documentation.
  Until it is, measure the real field rather than accepting the default.
- **~~`shot_height` is not recoverable~~ — and never should have been asked
  for.** It is a z-axis value and a homography maps a plane, so a constant was
  substituted. It was also `end_location[2]` in the training script: where the
  ball *finished*, an outcome rather than a property of the shot. Dropped from
  the model on 2026-08-06; see Phase 12 for the three-way comparison.

### The xG model was never the model that was measured (2026-08-06)

Found while wiring the model into the sample fixture, and it is the largest
single error in the project so far: **every xG figure PitchIQ has ever shown —
the sandbox since the hackathon, the CV pipeline since it was connected — came
out of an uncalibrated model that read about six times high near goal.**

`PitchIQHelper/main.py` trains `CalibratedClassifierCV(base, method="isotonic")`
and the comment above that line says exactly why: *"so predicted probabilities
are meaningful as xG values"*. Every figure the script printed — AUC, Brier, the
calibration curve — was measured on that wrapped model. The ONNX export then
reached inside it, pulled out `calibrated_classifiers_[0].estimator`, and
exported that. The isotonic step never left the script, and the base model
carries `scale_pos_weight ≈ 9` — deliberately pushing the positive class up,
which is the exact thing the calibration existed to undo.

What that produced, against what the same shots actually convert at:

Both models fed the same shot, with the height the pipeline would have sent:

| shot | `xg_model6` | `xg_model8` | real |
|---|---|---|---|
| clear, 6 m, central | 0.907 | 0.373 | ~0.4 |
| clear, 14 m, central | 0.612 | 0.153 | ~0.15 |
| clear, 22 m, central | 0.216 | 0.043 | ~0.05 |
| penalty, keeper on his line | 0.806 | 0.282 † | ~0.76 |

† still wrong, and not for this reason. Almost every penalty in the training
data has no freeze frame, so the model has barely seen one with a keeper drawn
on the line; over the 776 real central set-piece shots, which convert at 0.737,
it predicts 0.743. The sandbox's penalty preset says so on the button.

The retrained export (the whole calibrated estimator, via `skl2onnx` with the
XGBoost converter registered so the wrapper converts too) **predicts 6,057 goals
across the 53,337 training shots against the 6,014 actually scored**, and tracks
the real conversion rate closely in every distance band. AUC 0.884, Brier
0.073.

Three things worth keeping from this:

- **A model that is never run from the file it was written to is not a tested
  model.** `main.py` now verifies the exported ONNX against the fitted estimator
  before it finishes, and `--offline` re-runs the whole thing from the cached
  shots without touching the network, so re-exporting after a fix is a
  two-minute job.
- **The one check that would have caught it is one line.** A set of xG values
  that does not sum to roughly the goals actually scored is not xG, whatever
  else it is. `main.py` prints that line now.
- **The old model was not obviously broken.** It ranked shots correctly — six
  yards beat the penalty spot beat long range — so every test about ordering
  passed, the sandbox behaved sensibly when you dragged players around, and the
  number was only wrong in a way that needed comparing against reality to see.

Still open: the recalibrated model reads a little high in the 0.05–0.10 band
(predicts 0.075, converts 0.031) and a little low above 0.3 (predicts 0.386,
converts 0.447). Both are small next to what was fixed, and neither is worth
chasing before there is real footage.

**And behind it, a bigger one: `shot_height` was an outcome, not a feature.**
It is where the ball *finished*, so it is known only after the shot; 8,733
training shots end above 3 m and convert at exactly 0.000, because the model can
see they went over the bar. No camera can supply it, so the pipeline sent a
constant, and scored that way the freshly-calibrated model predicted **9,213
goals against 6,014**. Dropped the same day:

| model / input | AUC | Brier | predicted | actual |
|---|---|---|---|---|
| 12 features, real end height | 0.935 | 0.061 | 6,061 | 6,014 |
| 12 features, height fixed at 0.6 | 0.857 | 0.080 | **9,213** | 6,014 |
| **11 features, no height at all** | **0.884** | **0.073** | **6,057** | 6,014 |

The middle row is what the pipeline actually produced. The top row is what every
printed metric described and no camera could ever reach. **Dropping the feature
beats feeding it a constant on every axis at once.**

The general lesson is worth more than the fix: a feature the production path
cannot supply is not a bonus that degrades gracefully. It is a hole the model
has learned to lean on, and filling it with a constant puts the model somewhere
it has never been.

### The sandbox was modelling every shot at twice its distance (2026-08-06)

Found while adding presets, which are written in metres and therefore asked the
question nothing had asked before: *is the shot where the sliders say it is?*

`toStatsBomb` in `xg-sandbox/xg-model.js` mapped the sandbox's **half** pitch
onto StatsBomb's **full** 120-unit length. A shot the Distance slider called
20 m arrived at the model as 45.7 units — 40 m — with the goalmouth subtending
10° instead of 20°, and the halfway line landing on the opposite goal line.

`tests/test_xg_parity.py` could not catch it and was never going to: it builds a
scenario in StatsBomb space and converts it into each side's convention, so it
proves Python and JavaScript agree about a point without ever asking whether the
point is where the page says it is — and its own inverse carried the identical
wrong constant, so the two agreed perfectly the whole time. **A test that
converts both ways with the same function tests the function against itself.**
The checks that close it now start from the metres on the sliders
(`tests/video.test.js`), and the parity file says in its docstring what it does
and does not prove.

Two smaller things fell out of the same work:

- **The sandbox and the pipeline described the same shot differently.** The shot
  height slider started at 0 while `cv/xg_bridge.py` substitutes 0.6. Now both
  are 0.6.
- **The sandbox could not run the new model.** Its render loop fired an
  inference every frame, which worked only because the old model answered inside
  one; the current model averages five folds and does not, so every frame failed with
  "Session already started" and the readout sat on "—". Runs are serialised now,
  latest-wins, so a drag lands on the position the mouse is at rather than one
  it left forty frames ago.

### Connecting what was already built (2026-08-02, later)

Three things in the build order turned out to be **written, tested in isolation,
and called by nothing**. The repo looked further along than it was, and the
symptom in each case was silence rather than an error.

**xG was never computed.** `cv/xg_bridge.py` is 351 lines, agrees with the
browser's copy of the model to six decimal places across eleven scenarios, and
had no caller: `attach_xg` appeared exactly once in the repo, at its own
definition. So `Shot.xg` was permanently null, `teams.*.xg` was permanently
null, and `coach/coach.js` — which has always rendered an "Expected goals" row —
filtered it out on every match. The UI was finished; the number behind it was
never plugged in. `xg_for_shots` now assembles a `ShotContext` per shot from the
frame it happened in and `cv/pipeline.py` calls it, gated on a calibration.

Wiring it surfaced a real bug in code nothing had ever exercised.
`shot_context_from_tracking` guessed the goalkeeper as the opponent nearest the
goal *the shooter was defending* — the opposite end. On a shot at the right-hand
goal, the keeper standing on his line was counted as a defender blocking the
shot, and whichever opponent had dropped deepest, often fifty metres the wrong
side of the ball, was handed to the model as the keeper. The model was therefore
told the goal was unguarded on every shot where nobody named the keeper, which
inflates xG. No published number was ever affected, because nothing called it.
Fixed, and `tests/test_xg_bridge.py::TestKeeperGuess` now pins the geometry in
both halves.

**The noise question got an answer.** `validate_against_noise` was written for
Testing Strategy #6 and had never been executed. Re-measured 2026-08-06 against
`xg_model8`, 400 trials over five spots averaging 0.188 xG:

| position noise | mean xG shift | p95 | max | share of the baseline |
|---|---|---|---|---|
| 0.25 m | 0.019 | 0.065 | 0.099 | 10% / 34% |
| 0.50 m | 0.030 | 0.095 | 0.253 | 16% / 50% |
| 1.00 m | 0.043 | 0.168 | 0.267 | 23% / 89% |
| 2.00 m | 0.062 | 0.242 | 0.538 | 33% / 129% |
| 4.00 m | 0.107 | 0.380 | 0.624 | 57% / 202% |

Half a metre — the error `calibrate/` accepts as good — moves one shot's xG by
about 0.030 typically and 0.095 in the tail — half the number: fine for "that
was a decent chance", not fine for ranking two shots 0.1 apart. At 2 m the p95
shift exceeds the xG itself. Summing helps, by a measured amount: simulated over
a half's six shots the *total* lands within 8% at 0.5 m, 12% at 1 m, 18% at 2 m
and 26% at 4 m. The coach's quality note carries both this and the header bias.

Measured now against three models, and the trend runs the opposite way to
intuition:

| model | what changed | p95 at 0.5 m, as a share |
|---|---|---|
| `xg_model6` | (uncalibrated) | 37% |
| `xg_model7` | calibration restored | 33% |
| `xg_model8` | `shot_height` dropped | 50% |

Recalibrating the outputs barely moved the ratios. **Removing a feature moved
them a lot**, and for a plain reason: with eleven features instead of twelve,
distance and angle carry more of the answer, so moving a player moves the answer
further. A more honest model is a fussier one about where its inputs came from,
and the per-shot display band tightened from 1 m to 0.5 m to match.

**The two records of a match are compared** (`cv/reconcile.py`). The tagged
vocabulary is about why play stopped, the derived vocabulary about what a player
did, and they intersect on exactly one word — `goal` — which is also the one
worth getting right above all others. Goals are matched within 15 s and anything
unmatched becomes a warning and a row at the top of the review block, seekable.
Separately, `zones.leaves_play` finally has a caller: walking the **observed**
ball points (an interpolated point is a straight line drawn between two
sightings, and a straight line through the corner flag is not evidence) gives
ball-exit candidates, checked against tagged throw-ins and corners. That is the
independent cross-check Phase 9 has been asking for — until now the tag log was
the pipeline's only source, so a stoppage nobody tagged was invisible and a
mistaken tap was unquestionable. `SCHEMA_VERSION` → 3.

**The review tool computes the numbers it exists for.** It had been collecting
confirmations, edits, rejections and misses since it shipped, and deriving from
them one blended "% of those were real". Now precision and recall per event
type, with the edit case handled honestly: an edit that only reassigns the
player leaves the type standing, while an edit that changes the type counts
against the type claimed *and* as a detection of the type it should have been —
because a mislabelled event was still found, and finding is what recall
measures. Both are `null` rather than `0` where nothing has been reviewed, and
the caption says out loud that these describe the events checked, not the match.
"Download the labels" exports the labelled set for fine-tuning later, built in
the browser from data already loaded.

**A regression suite, finally** (`cv/experiments/compare_reports.py`,
`baselines/`). There was no golden file, snapshot or stored expected output
anywhere in the repo. Every threshold in `cv/touches.py`, `cv/participants.py`
and `cv/phases.py` is a guess; the unit tests pin what those guesses *are*, not
what they *do* to a real match, so changing one leaves every test passing. The
diff compares two report JSONs within a tolerance, ignores `processing_s`, and
treats null-becoming-zero as a difference rather than a rounding — that
particular change being the one this project has spent the most effort
preventing. `baselines/` is empty on purpose: there is no footage worth
believing yet, and a synthetic baseline would pin the pipeline to its own
current bugs and call that a regression suite.

**The half-time catalog, and a third dead field** (`cv/territory.py`,
`metrics.shape_drift`, `events.turnovers_by_third`, `report.cvReads`). Possession
said how much and never where; shape said what it was and never whether it held;
turnovers were counted with no position attached, so "giveaways in your own
defensive third" — a line the catalog asks for by name — could not be answered
at all. All three are now measured, and surface on the half-time page as
sentences rather than as more rows, because that page is read standing up in
three minutes.

Wiring those up turned up the same failure a third time: `build_report_json`
never passed `team_stats` a pitch, so the guard `if pitch is not None` was never
satisfied and **`ppda` has been null in every report this project has ever
produced** — not for want of footage, but because nothing handed the function a
pitch to measure the pressing zone on. The coach page has been rendering a PPDA
row that could never appear. `MatchReport` now carries the pitch and each team's
attacking end, and `tests/test_report_json.py::TestPositionalFieldsReachTheJson`
pins the plumbing separately from the arithmetic.

**`FOOTAGE_DAY.md`** collects what a person needs at the field — camera framing
and the measured cost of getting it wrong, the calibration frame, what to brief
the tagger on (restarts above all, since an untagged one deletes real football),
a leaving-the-field checklist, and the intake order with the stop conditions.

### What the CV work has established so far

Ranked by how much trouble each is, after auditing everything built so far:

1. **Calibration cannot currently be done on the available footage, and it
   blocks every metric.** The auto-tracking camera pans and zooms continuously,
   so no single homography exists; at any instant only ~30-40% of the pitch is
   visible and the landmarks that are visible cluster in one region — the
   degenerate case the calibration tool itself warns about. Everything in
   `cv/metrics.py` is expressed in metres and therefore has nothing valid to
   run on. The calibration tests all pass because they use synthetic cameras
   with known ground truth: that validates the maths, not its applicability.
   **This is a footage problem and it is the reason the raw wide feed matters.**
2. **Player track fragmentation, ~10x** (Phase 6). Tracker-agnostic, likely
   improves with native-resolution footage.
3. ~~Ball tracking~~ — was the second-worst problem at 1.6% coverage; fixed by
   giving the ball its own tracker (Phase 5), now 83%.
4. **Untested rather than broken:** the xG model has never been fed CV-derived
   features, and the pieces have never been run end to end as one pipeline.

Detection is solid (99% of frames contain players), the calibration maths is
correct and cross-verified between Python and JS, and team-level metrics —
possession, shape, territory — need no player identity, so they survive
fragmentation and are the sensible thing to lead a demo with.

### Security model, in one paragraph

Authorization never derives from a document the subject can write. A first-draft design
put `role` and `teamId` on a self-written `users/{uid}` doc, which would have let any
signed-in student write `{role:'coach', teamId:'<any team>'}` and read every teammate's
report plus every rostered minor's email address. Coach access now proves out of
`teams/{t}.coachUids` and player identity out of `teams/{t}/players/{p}.linkedUid`,
neither of which the subject can set for themselves. Separately, because Firestore
evaluates a `list` rule against the query rather than per document, there is no way to
let a player read just their own row out of shared match data — so coaches publish
denormalized `playerReports` docs instead, and a player's entire read surface is four
uid-scoped paths.

---

## Feasibility Reality Check

Two people, part-time around everything else going on — realistically ~100-200
focused hours over a month, not the 300+ it'd take to build everything below at full
[MVP] quality. Specific calls worth making now rather than discovering in week 3:

- **~~Ball detection is the single biggest technical risk in the whole project.~~
  SPIKE COMPLETE (2026-07-27) — answer: viable, but entirely dependent on camera
  framing.** Ran a pretrained YOLO detector against two real screen-recorded clips of
  the same team, via `python -m cv.experiments.spike_detect`:
  - **Tight, dedicated soccer framing** (players clearly resolved, jersey numbers
    faintly legible): **43% of sampled frames had a ball detection, 99% had players**,
    with zero fine-tuning. Good enough to build on.
  - **Wide multi-sport stadium panorama** (whole football field + track + stands in
    frame): **0 ball detections across 300 sampled frames**, and *no on-field players
    detected either* — only sideline staff close to the camera. At that framing a
    player is ~4-8px wide and the ball is under 2px. Cropping and upscaling does not
    recover it; the pixels were never captured.
  The risk is therefore **retired as a CV problem and reclassified as a camera-setup
  requirement** (see the camera bullet below). Downstream features (possession, passes,
  shots, the xG bridge) are viable *given adequate framing*.
- **True live/real-time halftime processing is a hard systems constraint, not just a
  CV one.** The pipeline has to process 45 minutes of footage in less than the ~10-15
  minutes of an actual break — average per-frame processing has to run faster than
  real time. Plausible on a laptop with a discrete GPU running a lightweight detector,
  but it's a real hardware/throughput requirement, not a given. For month 1: decouple
  "prove the halftime concept" from "hit the literal wall clock at the real game." A
  halftime report that lands a few minutes into the second half instead of at the
  exact whistle is still a very compelling demo. Save true real-time performance as an
  **[MVP]** hardening step once the concept is proven.
- **Don't train or fine-tune anything from scratch in month 1.** Use off-the-shelf
  pretrained models (a pretrained person detector, an existing tracking library like
  ByteTrack) as-is. Fine-tuning needs labeled soccer-specific data you don't have yet
  — and conveniently, the Phase 11 review tool produces exactly that data as a side
  effect of normal use, so fine-tuning becomes much cheaper *after* the demo.
- **Camera framing is now the single hardest requirement in the project** — promoted
  from "a real problem" to *the* gating constraint by the spike above. Empirically:
  a shared multi-sport stadium camera framed for a whole football field yields
  literally zero usable detections of players or ball on the pitch, and no software
  can recover that. The target is the framing of the tighter clip — pitch filling most
  of the frame — held **fixed**, which also preserves the cheap one-time homography
  from Phase 4. Two practical notes learned the hard way:
  - **Screen recordings are capped at the browser window's pixel count**, which may be
    well below what the camera natively records. Always work from a real export.
  - The wide stadium rig is a **once-a-season venue** for this team; the tighter
    camera position is what almost every game actually uses. Design for the latter.
- **The existing xG model was trained on clean, human-verified StatsBomb data.**
  CV-derived positions from a single camera + homography will be noisier — more
  jitter, occasional missed frames, homography projection error. The model's
  calibration may degrade on inputs noisier than what it was trained on. Validate this
  explicitly (see Testing Strategy and Phase 12) rather than assuming it just works
  once features are wired up. If it doesn't, retraining `main.py` with realistic noise
  added to the training features is a contained fix.

None of this is a reason to cut ambition — it's why **[Demo]** below is deliberately
narrower than **[MVP]**, on purpose, not as a shortfall.

---

## Delivery Modes

Two very different outputs come out of the same pipeline — keep them distinct, because
they have opposite constraints:

| | **Halftime Report** | **Post-Game Report** |
|---|---|---|
| Data | First half only | Full match |
| Human input | Live tagging only (Phase 3 tablet: subs + event types) — no post-hoc review | Live tagging (Phase 3) **plus** a full reviewer pass (Phase 11) |
| Time budget | Ready within the ~10-15 min break | No real time pressure — can run overnight |
| Accuracy bar | Directionally useful | High fidelity |
| Audience | Coach only | Coach (tactical) + individual players (portal) |

Architectural consequences:
- **Phase 1** needs to process first-half footage incrementally during play (or the
  instant the half ends) for the **[MVP]** version — "upload the whole match
  afterward" doesn't work for a true halftime deadline. For the **[Demo]**, it's fine
  to relax this (see Reality Check).
- **Phase 3**'s tablet taps need to be timestamp-aligned with the video/CV pipeline, so
  a live "corner" tap lands on the correct moment in both reports.
- The halftime report is **not** "automated CV only" — it's CV metrics (positions,
  speed, distance) combined with live human-tagged events and subs. The post-game
  report adds a third layer: the reviewer pass.

---

## Testing, Validation & Debugging Strategy

**CI runs all three suites on every push** (`.github/workflows/tests.yml`), which
until 2026-08-03 nothing did — 834 tests existed and the only thing between a
broken pipeline and a deployed site was whether somebody remembered. The Python
job installs `requirements-test.txt` rather than the full pipeline: `cv/detector.py`
imports ultralytics lazily, so nothing under test pulls in torch.

Applies across every phase below — how we know each piece actually works, not just
"the final number looked plausible":

1. **Ground-truth clips first.** Hand-label a handful of short clips (exact player
   positions per frame, exact event timestamps/types) before trusting any full game.
   Rerun the pipeline against these fixed clips after every change and diff the output
   — this is your regression suite.
   **Half built (2026-08-02):** `cv/experiments/compare_reports.py` is the diff,
   and `baselines/` is where a believed run goes. What is still missing is a
   clip worth believing — the directory is deliberately empty, because a
   synthetic baseline would pin the pipeline to its own current bugs. The
   per-frame hand-labelling half does not exist at all; the review tool produces
   *event* labels as a side effect of normal use, which is a cheaper substitute
   for the event half and no substitute for the box half.
2. **Metrics per stage, not just the final stat.** If a final number looks wrong, you
   need to trace *which stage* broke it:
   - Detection → mAP against labeled boxes
   - Tracking → identity-switch rate / IDF1 (does a track ID stay on the same real person)
   - Calibration → reprojection error in metres on known pitch landmarks
   - Events → precision/recall per event type against the ground-truth clips
     (**built**: `reviewScore` in `assets/report.js`, shown as a scorecard in the
     review tool. Detection mAP and tracking IDF1 remain unbuilt — both need
     per-frame labels, which nothing produces)
3. **Feature-parity test (specific to this codebase).** `main.py`'s `parse()` and
   `xg-sandbox/xg-model.js`'s feature calc must produce identical 12-feature vectors for the
   same synthetic scenario. Write a small harness that feeds known synthetic
   player/ball positions through both and diffs the output — this is pure, already-written
   math, so it's the cheapest first automated test to write, before any CV exists at all.
4. **Confidence scores travel with every detection/track/event.** The Phase 11 review
   tool sorts by lowest confidence first, so a human's limited review time goes to what's
   likely wrong instead of skimming everything uniformly.
5. **Reconciliation check between live tags and CV candidates.** Log the agreement rate
   between what the Phase 3 tablet recorded (e.g. "corner") and what the CV pipeline
   independently inferred (ball crossed the goal line). A rising disagreement rate over
   several games is an early warning that either the CV or the live-tagging process has
   a problem — treat it as a standing metric, not a one-time check.
   **Built** (`cv/reconcile.py`): goals compared head to head, ball exits
   compared against tagged restarts, both rates carried in `quality` and
   published. The cross-match trend it is meant to feed needs several matches to
   exist first.
6. **Validate model behavior on noisy inputs, not just clean ones.** Feed the existing
   xG model synthetic features with realistic CV-derived noise (jitter, occasional
   gaps) added, not just the clean values `main.py` was trained on — a fast way to find
   out if calibration degrades before relying on it live.
   **Done, 2026-08-02** — figures in the Current Status section above. Position
   jitter is covered; *gaps* are not, because a shot with no position simply
   gets no xG rather than a degraded one.
7. **Staged rollout.** Don't move on to team identification until detection+tracking
   hit an acceptable bar on the ground-truth clips. Don't trust the halftime path until
   several full games' worth of it have been checked against the fuller post-game
   version and found close.
8. **Staged/synthetic footage before full games.** Film simple clips with a known
   answer first — two people passing a ball a measured distance apart, one shot from a
   marked spot — before testing on messy 18-player game footage.

---

## Stats & Insights Catalog

Concrete answer to "what do we actually show people" — the thing Phase 13 computes and
Phases 14/15 display. *All of this assumes ball detection works well enough to be
useful (see Reality Check). If it doesn't, several of these degrade gracefully to
player-only stats — distance, sprints, positioning are safe; possession, passes,
shots, and xG are what's at risk.*

**Coach — Halftime** *(CV metrics + live-tagged events/subs, first half, must be skimmable in under a minute)*
- Possession % overall and by pitch third
- Shot map with live xG per shot, cumulative xG for/against
- Distance covered per player, flagged against their typical range — who's already tiring
- Team shape: average width/depth/compactness and how it drifted over the half
- Sprint count and top speed per player (intensity/fatigue signal)
- Dangerous turnover locations (giveaways in your own defensive third)
- Actual corner/foul/throw-in counts from the live tags (no CV guessing needed)
- Plain-language flags over raw tables: *"RB has covered 20% less ground than LB,"*
  *"team compactness dropped in the last 15 minutes"*

**Coach — Post-Game Tactical** *(reviewer-confirmed data, full match)*
- Everything from halftime, full-match and half-by-half comparison
- Passing network: who combines with whom, completion rate by pair/zone
- Phase-of-play breakdown: buildup vs. progression vs. final-third entry success
- Defensive line height and pressing intensity trend
- Set-piece outcomes (corner delivery zones, aerial duels won)
- Substitution impact: team stats in the window before vs. after each sub (exact sub timing comes straight from Phase 3's live log)
- Individual positional discipline: heatmap vs. assigned role

**Player — Individual Post-Game Report**
- Distance covered, sprint count, top speed (with season-average context once history exists)
- Touches, passes attempted/completed, pass accuracy
- Shots, xG generated, goals
- Personal heatmap
- Short list of their confirmed events with a video-timestamp link, so they can watch their own moments
- Correct minutes-played window, since Phase 3's sub log defines exactly when each player was on the field

---

## Application Structure — Pages & Surfaces

One web application, not separate native apps — six role-based surfaces:

1. **Public demo** (exists today) — the manual xG sandbox at the site root, unchanged.
2. **Live Match-Day Input Tool** *(Phase 3)* — used pitchside during the game.
   - Match setup: pick/create the match, confirm starting lineups, run the kickoff sync marker
   - Live tagging: one-tap event buttons, period-boundary buttons, an always-visible undo, a substitution sub-screen
   - Optional: a scrollable log of taps so far, for double-checking
3. **Post-Game Review & Annotation Tool** *(Phase 11)* — used after the game by whoever reviews footage.
   - Match picker
   - Main review screen: video player + synced timeline (live tags + CV candidates), confirm/edit/delete/add controls
   - Player-identification panel: track thumbnail crops → assign roster names, merge split tracks
   - Finalize action that locks in reviewed data and kicks off stats computation
4. **Coach Halftime View** *(Phase 15)* — one skimmable screen, phone/tablet, no deep navigation: possession, shot map/xG, fatigue flags, shape drift, turnover zones, live event counts.
5. **Coach Post-Game Tactical Dashboard** *(Phase 15)* — match overview, passing network/phase-of-play, player list → drill into an individual, substitution impact — likely viewed on a laptop.
6. **Player Portal** *(Phase 14)* — name-select/PIN, personal match report: stats, heatmap, clickable highlights.

**Recommendation: a single responsive Progressive Web App (PWA), not native iOS/Android
apps.** A PWA installs to a home screen and — critically — supports offline entry,
which the live tablet tool needs anyway (Phase 3). Native apps would mean a third
and/or fourth language/toolchain (Swift, Kotlin, or a cross-platform framework) for a
2-person team in a month — not justified yet. Revisit native only if this becomes an
ongoing product needing deeper camera/background integration than a browser gives.

---

## Tech Stack

Reuse what's already proven in this repo rather than introducing new stacks for their
own sake:

- **CV / detection / tracking / calibration: Python.** This is where the ecosystem
  actually lives — OpenCV for frame handling and homography math, Ultralytics YOLO (or
  similar) for detection, an existing tracker (ByteTrack, or the `supervision` library,
  which conveniently wraps detection+tracking+annotation) rather than a custom tracker.
- **Backend / API: Python (FastAPI).** Sits next to the CV code with no cross-language
  bridge needed to run the pipeline and serve results.
- **Database: SQLite for local demo development, Postgres if/when it needs to run
  somewhere shared.** Both are boring, well-understood choices — no need for a
  specialized time-series database at the data volume estimated in Phase 2.
- **Model interchange: ONNX**, exactly like the existing `xg_model8.onnx` — train in
  Python, run inference in the browser via `onnxruntime-web`, already proven in this
  codebase. Any future detector could follow the same pattern if in-browser inference
  is ever wanted, though server-side Python inference is simpler for month 1.
- **Frontend (all the surfaces above): JavaScript, matching `xg-sandbox/sandbox.js`/`geometry.js`
  today.** Stay vanilla for the simpler surfaces (halftime view, player portal). The
  Review & Annotation Tool is the one surface complex enough (synced video, timeline,
  forms, thumbnail crops) that a lightweight component framework could save real time
  — pick one (e.g. Svelte or React) and use it only there, rather than introducing a
  build toolchain across the whole site.
- **Frontend↔backend: a plain REST/JSON API.** The live tablet's real-time feel can
  start as simple polling and move to WebSockets only if polling actually feels
  laggy in practice — don't build the harder version first.

### Repo layout

```
PitchIQ/
├── index.html           landing page + sign-in
├── assets/              shared frontend modules
│   ├── app.css             design system
│   ├── firebase-init.js    SDK init + offline persistence  <- paste config here
│   ├── auth.js             sign-in, roster claim, role resolution
│   └── db.js               every Firestore read/write, plus stats aggregation
├── coach/               dashboard: roster, matches, stats, publish
├── player/              portal: own reports only
├── live-tagging/        the match-day tablet tool
├── xg-sandbox/          the manual xG sandbox (moved off the root)
├── firestore.rules      the security boundary
├── tests/               emulator suites — rules.test.js + flow.test.js
├── PitchIQHelper/       xG model training (main.py) + the shared .venv
├── cv/                  detection + frame sampling
│   ├── detector.py         PersonBallDetector
│   ├── frame_sampler.py    sample_frames()
│   └── experiments/        spike_detect CLI
├── requirements.txt     Python deps — see the CUDA note inside
└── package.json         dev tooling only; the frontend has no build step
```

One shared venv at `PitchIQHelper/.venv` covers all the Python. Installing
`requirements.txt` alone gives CPU-only torch; the CUDA build needs a separate
install from the PyTorch index first (documented in the file).

The frontend loads the Firebase SDK as CDN ES modules, so there is still no
bundler — `npm` is only for the emulator and tests.

---

## How to read the lists below

A checkbox is **work**: `[ ]` is something still to build or decide, `[x]` is
something that exists in the repo and can be pointed at. Anything that is a
finding, a measurement or a constraint is written as prose instead, because a
fact about the world never gets ticked and leaving it in a checkbox quietly
inflates how much is left.

That distinction had drifted by 2026-08-06 — seven items were built and never
ticked, six findings were sitting in checkboxes, and one line appeared twice —
so the open count meant nothing. Fixed then; worth keeping true.

---

## 1. Video / Camera Input
- [ ] **[Demo]** Single **fixed** camera, framed so the pitch fills most of the frame — not a wide shot that merely includes it. This is now a hard requirement, not a preference: see the Reality Check for the measured difference between the two.
- [ ] **[Demo]** Source footage must be a real export at native resolution. A screen recording of a video player is capped at the browser window's pixels and can silently discard the resolution detection depends on.
- [ ] **[Demo]** Record to a file and process afterward for the first working pipeline; treat true incremental/live processing as later hardening
- [ ] **[MVP]** Process first-half footage incrementally during play so the
      halftime report can be ready close to the actual break. The **prerequisite
      is now measurable**: `cv.experiments.speed_report` says whether this
      machine's real-time factor leaves room to be fed frames as they arrive, and
      at what sample rate (Phase 6, 2026-08-10). Building the incremental feed
      before knowing that number would have been building for a deadline nobody
      had checked was reachable
- [x] **Frame sampling strategy — every frame, or subsample to cut compute
      cost** (2026-08-09). The question had half an answer already: the `stride`
      docstring records what skipping frames costs **tracking**, measured on
      real footage — 100 tracks down to 70, the longest falling from 449 frames
      to 225. Nothing had measured what it costs the numbers a coach reads.

      Measured now, synthetically, against constructed truth the way the
      smoothing window was fitted (`tests/test_sampling.py`). A 30Hz source at
      each stride, against its own noiseless truth:

          rate     distance %   top speed %   sprints   bursts
          30.0        -0.3         -1.4        +0.0      -0.1
          15.0        -0.3         -1.3        -0.1      -0.4
          10.0        -0.4         -1.4        +0.0      -0.2
           7.5        -0.3         -1.1        -0.1      -0.9
           5.0        -0.2         -0.7        -0.2      -0.4

      **Nothing happens.** Distance, mean speed, sprints and bursts are flat
      from 60Hz down to 6Hz, and top speed moves less with the rate than it does
      with a tenth of a metre of positional wobble — measured: the spread across
      every rate is smaller than the gap between a clean track and one wobbling
      by 0.15m at a fixed rate.

      That is the previous item paying out. The smoothing window is stated in
      **seconds**, so the smoothed path is nearly the same curve whatever the
      sample rate; the average simply holds fewer samples. Which is also exactly
      where it ends. Below three samples a window stops being an average, and
      the failure it exists to prevent arrives immediately:

          6 Hz    bursts  +0.0 a minute   (three samples in the narrowest band)
          2 Hz    bursts  +4.0 a minute   (wobble counted as acceleration)
          1 Hz    bursts  -6.8 a minute   (nothing left to see one through)

      So there is a floor, it is `metrics.min_sample_hz()`, and it is a fact
      about the smoothing rather than about football — widen the bands and it
      moves with them, which is pinned by a test. At the current fit it is six
      a second.

      **The lever is now stated in hertz**, because that is the only unit any of
      this is true in. A stride is a ratio to a number nobody said out loud:
      *stride 2* is fifteen frames a second on a camcorder and thirty on a
      phone, and those are different analyses run by the same flag — the same
      mistake the smoothing window made when it was nine frames instead of 0.7
      seconds. `analyse_match(sample_fps=...)` works the stride out from the
      source, and the report publishes both the source rate and the rate that
      actually ran. Not the rate that was asked for: a 30fps clip asked for 12
      gets 15, and quoting 12 would describe a run nobody did.

      **Where this is free money.** `FOOTAGE_DAY.md` asks for a native export
      and phones shoot 60. At stride 1 that is twice the inference of a 30fps
      clip for figures that measure the same to within half a percent, so
      `--sample-fps 30` halves the run for nothing. Going *below* 30 is a
      different trade and the footage-day guide says not to: the movement
      figures would survive it and the tracker would not, and identity is
      already the weakest link.

      Which is the useful shape of the answer, and narrower than "subsample
      everything": **the movement figures were never the reason to run at full
      rate.** Tracking and ball coverage are, and they are the two things a
      lower rate actually damages.

      `SCHEMA_VERSION` 8 → 9. 477 pure JS · 120 emulator · 912 Python.
- [ ] Lens distortion correction if using a wide-angle/action camera
- [x] **[Demo]** Ingestion accepts any decodable video file — `cv/frame_sampler.py` handles this via OpenCV; the format was never the hard constraint, framing is
- [ ] **[Stretch]** Support moving/auto-tracking camera footage (e.g. Hudl/Veo-style ball-following cameras) — requires continuous homography re-estimation (pitch-line detection or frame-to-frame motion tracking) instead of one-time calibration; sports-broadcast camera-calibration research exists to lean on rather than invent from scratch
- [ ] **[Stretch]** Multi-camera stitching, drone or pan/tilt/zoom coverage

**Measured, on the clip we have:** a ball-following camera drops the ball out of
frame entirely for stretches — two separate ~12s gaps in a 2.5-minute clip, both
during penalty-box phases where the camera pulled wide. Possession, passes and
the xG bridge all go blank across those windows, which is a different and worse
failure mode than low confidence. If the platform can export the raw
wide/panoramic feed instead of the auto-cropped view that removes the problem
outright, since the underlying sensor never moved. **This is the single most
valuable thing to ask the coach for**, and it is in `FOOTAGE_DAY.md` §1.

## 2. Data Storage & Backend
Moved up front — Phase 3's tablet needs somewhere to write to before anything else can happen.
**Built: Firestore, via `firestore.rules` + `assets/db.js`. There is no server
to run.**

A FastAPI + SQLAlchemy + SQLite server (`backend/`) filled this role first and was
kept as a fallback until a full match had been tagged against Firestore without
it. That happened, so it was deleted — 735 lines describing a schema that no
longer matched the one in use, which is worse than no reference at all.
- [x] **[Demo]** Stand up a minimal backend early — done first as a local server, then replaced by Firestore
- [x] **[Demo]** Run it locally, on a laptop sharing WiFi/hotspot with the tablet at the field — **superseded**: Firestore's offline cache queues taps on the tablet itself and replays them when signal returns, so there is no laptop to keep alive at the field
- [x] Schema for teams, players, matches, roster entries, substitutions, events — with the `source` field (`live_tag` / `cv_candidate` / `reviewer_confirmed`) already in place, though only `live_tag` is written today. Tracking-frame tables deliberately deferred until Phase 6 exists.
- [x] Undo endpoint spanning both events and substitutions, reverting roster state and rolling back match status when a period marker is undone
- [x] **Retention, for the imagery that actually exists** (2026-08-10). This
      line used to end *"nothing hosts video today, so this is a decision waiting
      on a need"*. That stopped being true the day thumbnails shipped, and
      nobody noticed the premise had expired.

      `cv/thumbs.py` cuts a crop of every tracked figure out of the footage so a
      coach can look at a row and say which of their players it is. Those are
      **photographs of children**. They were written into `cvStats/identity`
      beside the per-track statistics and kept indefinitely, and `cvStats` is
      `allow write: if false` to every client — which covers delete — so nobody
      could remove them. Not the coach, not the team, not the erase-a-player path
      built the same day, which could unpick the mapping that ties a crop to a
      name and could not touch the crop.

      **The retention rule needs no policy debate**, which is why this closes
      rather than waiting on one: the pictures exist to let a coach do the
      mapping. Once it is done they have no remaining purpose, and what is left
      is pictures of minors in a database with a job that has finished.

      So they live in `cvStats/thumbs` now, alone, and `firestore.rules` grants a
      coach `delete` on that one document and nothing else. **Deleting is not
      forging** — the invariant that made `cvStats` untouchable is that no client
      may *invent* a statistic, and destroying a photograph cannot invent
      anything. `tests/rules.test.js` draws that line explicitly: a coach may
      delete the pictures, may not write them, may not touch `identity` or
      `summary` at all, and a tagger, a player and a stranger may do none of it.

      The control sits above the figure list rather than under it, because it is
      a statement about students' data and not a footnote to a feature. It says
      how many pictures are held; once they are gone it says **"No pictures of
      the tracked figures are stored for this match"**, which is the reassuring
      half and only reassures if it is stated. Not automatic on the last name
      being picked: a coach may want to check their work in the morning, and a
      control that destroyed evidence the moment it judged you finished would be
      worse than one that waits.

      A deleted picture and one the tracker never caught cleanly both render as
      *"no clear view"*, deliberately — both are figures you cannot look at, and
      neither is a fault. Re-running the pipeline writes them again, which the
      confirmation says out loud, because the point is not keeping them lying
      about rather than never having them.

      Verified against the emulator: three pictures shown, deleted, and the three
      figures keep their spans, frame counts and pickers exactly; `cvStats/thumbs`
      returns 404 while `identity` and `summary` return 200 with every cluster
      field intact; and it survives a reload.

      **Video itself is still not hosted**, and the paragraph this replaces was
      right about that part — a 90-minute 1080p match is several GB and nothing
      stores one. If that changes, the rule above is the one to extend: keep it
      while it has a job, and have a control that ends it.

      554 pure JS · 139 emulator · 985 Python.
- [x] **[MVP]** Real auth/accounts and a cloud-hosted option — Google sign-in plus Firestore, so nobody needs to be near a field laptop
- [x] API layer connecting the tablet, processed match data, and the frontend/portal — the Firestore SDK is the API, and its offline cache is the sync layer

## 3. Live Match-Day Input Tool
A tablet used pitchside during the game by an assistant coach and/or a dedicated data
collector — runs *concurrently* with play, distinct from the Phase 11 tool that runs
*after* it.
**Built: `live-tagging/` (vanilla JS, no build step). Deployed at
`/live-tagging/`; sign in with Google and pick the squad and match. Nothing to
configure and no server to start.**
- [x] **[Demo]** Tablet-friendly UI: large touch targets (236×269px on tablet, 2-column grid on phones), minimal menu depth
- [x] **[Demo]** Substitution entry: player X off / player Y on, per team, at the current live timestamp, starting from the pre-game roster/lineup. Already-used substitutes stay visible but dimmed so they don't look like fresh options.
- [x] **[Demo]** One-tap event buttons: out of bounds, corner, throw-in, goal kick, free kick, foul, card, goal, offside — auto-timestamped on tap
- [x] **[Demo]** Period-boundary buttons: kickoff (1st half), halftime, kickoff (2nd half), full-time. Halftime freezes the clock and the 2nd-half kickoff resumes from that same value, so the break never gets counted as match time.
- [x] **[Demo]** "Undo last" control — one tap, always visible in the header
- [x] **[Demo]** Timestamp sync: the kickoff screen prompts for the clap/marker at the moment of the tap, giving one clock offset for the whole match
- [x] **[MVP] Offline-first entry with sync-when-available** (2026-08-07). Most
      of the machinery was already here and unticked: `persistentLocalCache`
      with a multi-tab manager in `assets/firebase-init.js`, every write a
      `writeBatch` rather than a transaction (a batch queues offline, a
      transaction fails outright), undo by direct document reference so it needs
      no query, and log ordering by `matchClockS` rather than
      `serverTimestamp()` — which reads as null locally and then resolves to
      sync time, misordering exactly when offline.

      What was missing was the **truth**. The sync dot read `navigator.onLine`,
      which reports a link and not a reachable server, so a school Wi-Fi with a
      captive portal or a dead uplink read as connected and the tooltip said
      "Saved" while nothing had been saved. And nothing counted outstanding
      writes, so the person holding the tablet could not know when it was safe to
      close it — which is the only question they actually have.

      Firestore knew both all along. `watchSync` listens with
      `includeMetadataChanges`, without which the one moment that matters — a
      queued write finally being acknowledged, which changes no field on any
      document — never fires a snapshot at all. `hasPendingWrites` is an exact
      per-document count of what is still only on the tablet; `fromCache` is the
      honest replacement for `onLine`. When the two signals disagree the
      pessimistic one wins: claiming a connection that is not there is the error
      that loses a match, claiming none while one exists costs twenty seconds.

      The count went from a `title` to the screen, because a tooltip on a tablet
      is a tooltip nobody can open. The chip stays a bare green dot while
      everything is up, so the words appearing is itself the signal.

      **Zero pending while disconnected is reassuring and true** — an
      acknowledged write is on the server by definition — and is worded that way
      rather than hedged. **Unknown is not.** `safeToClose` returns false for a
      count it has not been given, and the exit sheet, which used to say
      "Everything you've tapped is saved" unconditionally at the exact moment
      that claim matters most, now has three branches. It had two, and the
      not-yet-asked one printed *"null taps have not reached the server yet"* —
      caught in the browser, along with a sentence that started singular and
      finished plural.

      `tests/flow.test.js` drives a real client through `disableNetwork` and
      back: two taps made offline, counted as pending, cleared on reconnect, and
      present on the server afterwards. The `setDoc`s there are deliberately not
      awaited — offline that promise does not settle until the server
      acknowledges, which is precisely why the tagging UI must never block on
      one.
- [ ] Decide role split: one app covering both subs + events, or two simpler single-purpose roles/devices — worth testing both at the demo dry run
- [ ] Basic weatherproofing for an outdoor tablet (case, screen usable with sun glare)
- [x] **Live-tagged data feeds directly into the halftime report and
      pre-populates the Phase 11 review tool as a head start** (2026-08-09).
      The half-time half has been true since `halftime/` shipped — that page is
      built from tagged data alone. The review half was not, and Phase 11's own
      opening line had been claiming it: *"pre-populated with Phase 3's
      live-tagged events and subs... the reviewer's job is verifying,
      correcting, filling gaps, not labeling from scratch."*

      What was actually there was one column of the pipeline's candidates. The
      tagged log sat in a different strip on a different part of the page, so a
      reviewer wanting to know what a human had said about 34:11 had to scroll
      away from the row they were judging and find it by eye.

      That is the wrong shape for the work. Judging a candidate is almost always
      a question about context — *was the ball even in play, and what had just
      happened?* — and the log is the only record that can answer it. So
      `reviewFeed` in `assets/report.js` merges the two into one column in time
      order, and each candidate now carries the tagged entry nearest to it:
      **"pass · throw-in 2s before"**, which is the touch the detector gets
      wrong most and the hardest one to judge without scrubbing.

      **A tagged row is not a candidate, and the code says so in four places.**
      It carries no verdict buttons, because there is nothing for a reviewer to
      confirm about a human's own record. It never enters precision, never
      enters recall, and never moves the "checked so far" count — a tagged
      corner is not something the pipeline claimed, and counting it as agreement
      would credit the detector with work nobody did. It is drawn dashed and
      unweighted so it cannot read as one more thing waiting to be checked. And
      it stays out of the type filters: filtering to `pass` means "show me the
      passes it claims", so the log gets a chip of its own instead.

      The one place the log has something to tell the pipeline is **a goal it
      tapped that no candidate stands near** — a miss the pipeline made and a
      human already proved. Until now recording that meant reading a clock off
      one strip and typing it into a box on another, which is asking a coach to
      be a worse copy of a file that already exists. One tap now, and the row
      says "miss recorded" afterwards rather than offering again.

      Two windows, both with reasons. Six seconds for context, which is a
      restart — the whistle, the walk, the throw; wider and every pass in a busy
      passage picks up a foul it had nothing to do with. Fifteen for pairing a
      goal, mirroring `GOAL_WINDOW_S` in `cv/reconcile.py` so the browser cannot
      offer to record a miss the pipeline's own reconciliation counted as found.
      **Any** shot nearby counts as found, even one scored as saved: recall asks
      whether the moment was found, which is the rule `reviewScore` already
      applies to a retyped event.

      Verified against a seeded emulator with both records of the same match, so
      the merge, the rules and the save were exercised together rather than
      separately: the throw-in at 03:34 lands between the two passes it
      produced, the goal at 04:35 with a shot beside it offers nothing, and the
      one at 11:30 records a missed `shot` at 690s that reaches Firestore
      through the real rules.

      471 pure JS · 120 emulator.
- [x] **Fixed:** resuming an interrupted match still picks the clock up *paused* at the last logged event — real elapsed time genuinely can't be recovered after a reload — but the clock is now a control. It turns amber and reads "paused · tap to set" whenever the half is live and the clock is stopped, and tapping it opens a sheet to wind the time to the referee's watch and restart. Before this, a stopped clock looked like an ordinary clock that happened to read 34:12, and stamped every remaining event of the half with 34:12.

## 4. Field Calibration (pixel space → pitch metres)
**Built.** `calibrate/` is a browser tool for clicking landmarks with a live
overlay of the projected pitch outline; `python -m cv.experiments.calibrate
points.json` fits and grades it. Grab a frame first with
`python -m cv.experiments.grab_frame "<video>" --at 120`.
- [x] **[Demo]** Manual calibration: click 4+ known pitch landmarks once per camera setup, producing a homography into pitch metres, with a conversion into the StatsBomb 120×80 space the xG model expects
- [x] **[Demo]** Attacking direction per period (`MatchOrientation`), so second-half shots are measured against the goal the team is actually attacking
- [x] Honest error reporting: reprojection error is shown but flagged as optimistic (zero by construction on a 4-point fit), with leave-one-out error as the real number and a suspect-point ranking to locate a mis-click
**Pitch dimensions must be measured, not assumed.** Distance and speed inherit
the error directly, so a 105m default on a 100m field overstates every figure by
5% — and `Pitch.to_statsbomb` normalises by the configured length, so guessed
dimensions move every xG number too. The action is in `FOOTAGE_DAY.md` §2:
measure the actual pitch before the camera goes up.

- [x] **Notice when the camera moved** (2026-08-10) — the detection half of
      recalibration. Re-fitting the homography from the new framing is still
      open below; knowing you need to is not.

      The calibration is one homography, fitted once, from one frame, and every
      metre goes through it: distance, top speed, sprints, shot positions, xG,
      heatmaps, territory, shape. A fixed camera is a hard requirement in Phase 4
      and in `FOOTAGE_DAY.md` §1 — which is the right rule and not a mechanism.
      Somebody walks into the tripod at half-time and the rule is broken by
      accident, silently.

      **Silently is the problem.** A stale homography does not fail. It keeps
      returning plausible coordinates, and every figure downstream keeps its
      shape — a player still covers seven kilometres, a shot still lands
      somewhere in the box. The numbers are simply about a pitch the camera is no
      longer pointed at, and nothing in the report has ever said which half of a
      match that applies to.

      **Done with data the pipeline already has**, no new model and no second
      pass over the pixels. Over a second the *median* per-track displacement is
      near zero, because twenty-two people move in twenty-two directions; a frame
      that shifts moves all of them by one vector. Measured in player heights via
      `scale_px`, so one threshold holds at any resolution and camera distance.

      **The median alone is not enough, and the tests found the case that proves
      it.** With exactly half the pitch sprinting one way and the other half
      still, the median sits midway between the two groups — dead on any
      threshold worth setting — because a median describes the middle player, and
      the middle player is not the frame. What actually separates a camera move
      is **agreement**: a shift is called only when the median is large *and*
      most tracks are individually within half a player height of it. In the
      half-and-half sprint nobody is near the midpoint, so agreement collapses.
      `tests/test_camera.py` keeps both the rejection and, with agreement
      disabled, the proof that the median alone would have fired.

      Most of that test file is football that must **not** register — a lone
      sprinter, a counter-attack, a whole pitch pressing as a unit — because
      `trustworthy` is `not warnings` and a false positive condemns a good
      report. When it does fire it is a real warning, unlike the real-time factor
      added the same day: a slow run is not an inaccurate one, and this is.

      **What it cannot do**, each stated in the module rather than discovered
      later. It says the calibration went stale and roughly when, not by how
      much — recovering that needs the pitch lines re-found, which is the
      `[Stretch]` item below. It cannot tell a bump from a deliberate pan or
      zoom, and does not try, because both invalidate the homography exactly as
      completely. And it cannot see a *slow* drift: a tripod settling into soft
      ground moves the median by a hair per sample, indistinguishable from a team
      shifting up, and turning the threshold down far enough to catch it turns
      every counter-attack into a camera bump. A step is what this finds.

      **A verification note worth keeping.** Running it end to end against the
      real pipeline is what revealed that YOLO detects **zero people** in the
      synthetic clip these experiments have been using — so `analyse_match`
      correctly reported `checked=False` and the positive path never ran. The
      integration test builds the frame table directly instead. The same finding
      is why the speed figures in Phase 6 above have been corrected.

      **And the flake that had been blamed on the emulator was ours.** The
      emulator suites ran as `node --test` over three files, which the runner
      executes in *parallel processes* against a single emulator. Three
      `clearFirestore()` calls contending on one instance is a race, and it
      surfaced as whichever test happened to be first in the newest describe
      block failing in under 20ms with `CANCELLED`. Each file has its own
      project id, so no data was ever crossing over — it was contention, not
      interference, which is why it looked so much like infrastructure. Adding
      `--test-concurrency=1` serialises them; four consecutive full runs clean,
      against a roughly one-in-three failure rate before.

      `SCHEMA_VERSION` 10 → 11. 554 pure JS · 132 emulator · 979 Python.
- [ ] **[Stretch]** Automatic pitch-line detection
- [x] **How much of the pitch the camera actually saw** (2026-08-12). The
      strategy this line asked for, and it starts with measuring the thing:
      nothing did.

      `Calibration.sanity_check` projects the frame corners and asks whether
      **any** of the image lands near the pitch — the test for a scrambled fit.
      It never asked how much of the pitch the image covers, and a homography
      will map a pitch coordinate to a pixel that was never in shot without
      complaint.

      **An unseen third does not read as unseen. It reads as football that did
      not happen.** `territory` divides each team's possession across the
      thirds, so a band out of frame contributes no seconds and comes out as a
      side that never went there; a heatmap draws it cold; a shot map and every
      xG behind it need the goalmouth, and a goal out of shot does not produce
      fewer shots, it produces none.

      `cv/coverage.py` samples the pitch on a metre grid — about 7,000 points,
      one matrix multiply — and reports the visible share, the share of each
      third, and whether each six-yard box was in frame. Sampling rather than
      clipping polygons: no geometry library, and no convex-hull argument about
      a quadrilateral perspective has bent. Published as
      `quality.pitch_coverage`, warned on, and said in the browser's caveat list
      — a goalmouth out of shot outranks and suppresses the percentage, because
      they are the same mistake seen twice.

      **Two things this turned up that the tests did not have to find.**

      First, a claim of mine that measurement did not support. The docstring
      said the horizon guard was load-bearing — that grass behind the camera
      would otherwise "land neatly inside the picture and count as visible".
      Swept over 750 camera positions, focal lengths and aim points, guarding
      the sign moves the answer by **at most 0.002**. A camera on the halfway
      line filming one goal has 3,536 of 7,140 cells behind it and their mirror
      images land outside the frame anyway. The check stays because it is three
      lines and is right; the docstring now carries the figure so nobody later
      assumes otherwise, and `TestProjection` pins it at the only level where it
      is visible. Two tests that *looked* like they pinned it did not — verified
      by breaking the guard and watching them pass.

      Second, the sign was inferred from the majority across the batch, which is
      wrong in exactly the case the check exists for: half the pitch behind the
      camera makes the vote a coin toss. It is resolved from a reference point
      instead — whatever is at the centre of the frame is in front of the
      camera, by construction — so one point answers the same as seven thousand.

      **And the guard that should have caught the new field did not.**
      `tests/test_sample_report.py` exists to fail when Python publishes a field
      the preview fixture lacks. It checked top-level keys only, and `quality` is
      where fields actually get added — so `quality.pitch_coverage` sailed past
      it. Extended to look inside, it immediately found **thirteen of
      twenty-nine quality keys missing** from the fixture, drifted in over
      months: `tracks`, `clusters`, `camera`, `realtime_factor`,
      `keeper_method` and eight more. All filled in, consistent with the story
      the fixture already tells rather than freshly invented — 22 figures at the
      measured 3.4 tracks each is the 75 tracks now recorded.

      `SCHEMA_VERSION` 11 → 12. 573 pure JS · 149 emulator · 1008 Python.

      Not measured yet on a real calibration, because there is no footage. What
      it will say about the school's camera is the point of measuring it before
      the first match rather than after.

## 5. Object Detection (per frame)
**Built: `cv/` — `PersonBallDetector` (YOLO filtered to person + sports ball) and
`frame_sampler`. Rerun the feasibility test on any new footage with:**
```
python -m cv.experiments.spike_detect "<video>" --conf 0.08 --imgsz 1280 [--save-annotated]
```
It reports per-class hit rate, average/max confidence, throughput, and the longest
stretch with no ball detected — that last one is what exposed the auto-tracking
camera losing the ball for 12s at a time.
- [x] **[Demo]** Player detection using an off-the-shelf pretrained detector as-is — no fine-tuning in month 1
- [x] **[Demo]** Ball detection feasibility spike — done, see Reality Check for results
- [x] **Ball tracked separately from players** (`cv/ball.py`). Measured on 30s of real footage: the detector finds the ball in 60% of frames, but routing those through the multi-object tracker yielded **1.6%** — MOT confirms a track only when detections associate consistently frame to frame, and a small, fast, low-confidence object never clears that bar. Treating the ball as a single-object path-finding problem instead (dynamic programming over candidates, then interpolation) gives **83% coverage**, visually verified against the actual ball in frame
- [x] Class filtering at predict time (person + sports ball only), which removes the spurious car/dog/umbrella detections a generic COCO model produces on stadium footage
- [x] Referee flagged and kept, not excluded — `cv/participants.py` marks anyone
      moving like a player but matching neither kit as `official`, because
      without a calibration a referee and a goalkeeper are indistinguishable
      and dropping a keeper is the worse mistake. A true referee-vs-goalkeeper
      split still needs a calibration to give it a goalmouth to measure against.
- [x] Excluding non-players in frame — `cv/participants.py`: static touchline
      figures (coaches, subs, spectators) excluded by how little they moved in
      their own body heights; genuinely off-pitch figures excluded once a
      calibration exists. Every threshold is a guess never checked against a
      real touchline, so every exclusion carries its reason into the report.
- [ ] Tune confidence threshold against real footage — the spike used 0.08 to surface marginal detections for analysis, which is too permissive for production
- [ ] **[MVP]** Fine-tune detectors on your own footage once you have labeled data — the Phase 11 review tool produces this as a side effect of normal use

## 6. Multi-Object Tracking (identity over time)
**Measured 2026-07-28 — this is now the biggest open problem in the pipeline.**

`python -m cv.experiments.track_report "<video>" --compare-trackers` on 30s of
the tight camera clip, against ~25 people on screen:

| tracker | tracks kept | fragmentation | window coverage |
|---|---|---|---|
| bytetrack | 283 | 11.3x | 32% |
| botsort | 250 | 10.0x | 40% |
| ocsort | 267 | 10.7x | 35% |
| deepocsort | 242 | 9.7x | 36% |

Fragmentation is tracks-kept ÷ 25. **Around 10x means the average player is
being cut into ten different people**, so any per-player total — distance
covered, sprints, minutes — is really ten unattributed fragments. Detection is
not the problem (99% of frames contain players); holding an identity is.

Three things this tells us:
- **Swapping tracker is not the fix.** All four cluster in 9.7–11.3x. The
  bottleneck is upstream: players are only tens of pixels tall, so appearance
  is nearly identical between them and motion cues are weak.
- **Frame-skipping makes it worse**, which contradicts the compute-saving idea
  in Phase 1. ByteTrack matches on frame-to-frame motion, so a stride makes
  players appear to teleport. Stride 1 runs at 2x real time on the RTX 4060
  anyway, so there is no reason to skip.
- **The test footage is a 720p screen recording.** A native-resolution export
  would give larger, more stable boxes and should improve this materially, so
  the numbers above are close to a worst case rather than a verdict.

**Update 2026-08-01 — the single-pass rewrite helped, and automatic merging
helps more.** `analyse_match` used to run the model twice over two identity
spaces that could not see each other. Collapsing that into one pass
(`cv/frames.py`) also let detection run at the ball's lower threshold and hand
those weak boxes to the tracker, which is the ByteTrack bargain the old
arrangement was throwing away. Measured on one 15s window, three fresh
processes each:

| | time (median, range) | raw tracks |
|---|---|---|
| two passes | 37.8s (37.1–49.0) | 146 |
| one pass | 17.1s (17.0–18.4) | 125 |

Then `cv/identity.py` merges fragments into clusters using the one hard
constraint available — two tracks seen in the same frame are two people — plus
shirt colour and spatio-temporal continuity. On that window: **125 tracks → 37
clusters**, and the largest clusters are present in ~300 of 300 frames, which is
what a real player looks like.

37 is still above 22, so this has not solved identity; it has made the human
step small enough to be worth doing. Mapping 125 fragments to a roster is
something nobody finishes; confirming ~37 is a couple of minutes.

- [x] **[Demo]** Stable per-player track ID using an existing tracking library — done, quality measured above
- [x] Track smoothing before computing speed/distance (`cv/metrics.py`)
- [x] Automatic fragment merging (`cv/identity.py`) — 3.4 tracks per cluster on the measured window
- [x] Coach-facing view of the clusters (`coach/`), so the mapping can be worked out at all
- [ ] **Re-measure on native-resolution footage before concluding anything** — the current numbers may be an artefact of the screen recording
- [ ] **[MVP]** Fine-tune the detector on real footage; more stable boxes is the most likely real fix
- [x] Writing the confirmed cluster→player mapping back — a picker per figure on the coach's match view, saved to `cvMapping/players`
- [x] Treat the merge-tracks control as **required, not optional** — a human confirming clusters is the only route to per-player stats, and it is now the gate: no mapping, no `cv*` field on anyone's report

The mapping lives in its own collection rather than in `cvStats`, which stays
pipeline-authored and client-read-only. Keeping them apart means a coach
correcting an identity can never be mistaken for the pipeline having measured
something.

Many-to-one on purpose: `cv/identity.py` only rejoins fragments seconds apart,
so a player who went off and came back is genuinely several figures. Counting
stats sum across them; top speed takes the maximum, because a player who hit
31 km/h in one fragment did not hit 62 across two. How many figures a player
was assembled from is carried through to the report and drives the confidence
mark — one clean track is a much stronger claim than nine stitched together.
**Worth knowing:** team-level stats (possession, shape, territory) need no
identity at all and stay viable at this fragmentation, which is why the demo
should lead with them rather than with per-player figures.

- [x] **[Demo]** Stable per-player track ID across frames using an existing
      tracking library — `cv/tracking.py` wraps Ultralytics' ByteTrack. No
      custom tracker, per the note above. IDs are internally consistent only;
      resolving one to a name is the human job in Phase 11
- [x] Ball tracking with interpolation through short occlusion — `cv/ball.py`.
      `BallPoint.observed` is the load-bearing field: a filled-in point is a
      straight line drawn between two sightings, and `cv/reconcile.py` refuses
      to treat one as evidence that the ball crossed a line
- [x] **For *long* occlusions, fall back to the live-tagged event log rather
      than interpolating across them** (2026-08-09). Not a fallback in the
      literal sense, and working out why was the item. The log has no ball
      positions in it. Anything filled in from it would be invented, and
      strictly worse than the honest gap that was already there — the reason
      `ball.MAX_INTERPOLATION_GAP_S` refuses to draw a line across more than a
      second in the first place is that a real ball gets kicked.

      What the log genuinely knows is **why nobody could see the ball**, and
      that turned out to be worth more than a position would have been, because
      `no_ball_s` has been one number covering two things a coach would never
      put in the same sentence:

      > the ball spent eleven seconds in a teenager's hands behind the
      > touchline — and there is twenty seconds of live football nobody saw.

      The first is not a failure. Those frames are dead-ball time, already
      excluded from possession by `cv/phases.py`, and no camera was going to
      find that ball. The second is a hole in the match: possession, territory,
      the pressing trend and every event derived from a touch are all silent
      about it and none of them say so. Added together the first hides the
      second — and hides it **more the better the tagging was**, so a
      well-tagged half reported a bigger number than an untagged one for
      exactly the same tracking. That is backwards, and it had been on the
      coach's screen since possession was first measured.

      New `cv/blind.py` cuts each unseen stretch against the dead-ball spans and
      then checks what is left against the log. Three kinds come out: `dead`
      (inside a tagged stoppage), `accounted` (live by the phase table, but a
      tag sits inside it or just outside), and `unexplained` — live football,
      nothing tagged anywhere near, the pipeline simply lost the ball. Only the
      third is a defect, and it is the figure the whole module exists to
      produce.

      Three decisions worth writing down:

      - **A straddling gap is split, not voted on.** Twenty seconds covering a
        twelve-second stoppage is twelve seconds explained and eight not.
        Deciding it on a majority would either forgive eight seconds of real
        blackout or invent twelve seconds of one, depending which way the gap
        happened to lean.
      - **`accounted` needs a window, not strict containment** — five seconds,
        for two structural reasons. `phases.TAG_SLOP_S` shrinks every dead span
        by two seconds at each end on purpose, so a gap that really was a
        stoppage keeps a live-looking sliver at both ends. And taggers tap the
        restart without tapping what caused it: a `corner` with no
        `out_of_bounds` in front of it opens no span at all, while the ball
        spent the previous few seconds out of play and out of shot.
      - **"Nobody looked" is its own kind, not zero.** Without a log the
        stretches are `unchecked` and the three figures are published as null.
        Three zeroes beside a four-minute total would read as a clean bill of
        health for a run where no check was possible.

      The split is drawn as a stacked bar on the coach's banner — the fourth
      shape of bar on these pages and the first that is not about two teams:
      one whole and what it turned out to be made of. Stoppages in the dimmest
      colour available, because they are not a problem; the unaccounted-for
      stretch in the warning amber, because it is the only part of the bar that
      is a hole in the report. The longest single stretch travels beside the
      total, since the same lost minutes as one blackout and as a hundred
      flickers are different failures and only the first takes a passage of
      football with it. A stretch over thirty seconds with nothing to explain
      it raises a warning — one warning with a count, not one per stretch,
      because `trustworthy` is `not warnings` and a list that is all one thing
      is a list nobody reads.

      `SCHEMA_VERSION` 7 → 8. 451 pure JS · 120 emulator · 872 Python.
- [ ] Re-identification after a player is occluded or leaves frame briefly —
      **partly done**, and the human half of it is now finished. `cv/identity.py`
      rejoins fragments seconds apart on kit colour and timing; anyone who went
      off and came back later stays split, and the picker's many-to-one mapping
      is what covers that case instead. Since 2026-08-10 the picker also
      *suggests* the rejoin rather than waiting to be told (see the merge-tracks
      entry in Phase 12), which is a coach pressing one button instead of
      scrolling forty rows looking for a face.

      What remains open is the automatic half: an appearance model good enough
      to rejoin a player across a minute rather than across two seconds. That is
      gated on footage, not on effort — on this camera framing a player is a few
      dozen pixels tall and the only appearance signal that survives is the kit
      colour already being used
- [x] **Track smoothing before computing speed and distance** — built since
      `cv/metrics.py` existed, and deliberately **not** a Kalman filter: one
      needs tuning that cannot be validated without ground-truth tracks, and an
      untuned filter lags hardest during direction changes, which are exactly
      the moments that matter. A centred moving average is crude and has no
      hidden parameter to get wrong.

      What was missing was knowing what it costs, and that is now measured
      (`tests/test_bursts.py`, at the nine-frame window the pipeline actually
      uses). Positional jitter of size σ gives a player who **never moved**
      about 353σ metres every minute — 18m at 0.05, 35m at 0.10, 71m at 0.20 —
      while a player genuinely jogging 180m in that minute is inflated only to
      189m at σ=0.20. The error is one-sided, since noise can only add distance,
      and it lands almost entirely on players who were standing about. A
      substitute warming up on the touchline is the worst case in the report.

      `position_noise_m` measures σ per track from the second difference of the
      raw position, which is what separates a wobble from a movement: a real
      trajectory is smooth, so its second difference is roughly a·dt², about a
      centimetre at 30fps, while white noise gives σ√6. A **median** rather than
      a mean is what lets the real accelerations mixed in be ignored. It
      recovers σ to within 0.7% from 0.02m to 0.20m, and it is the first figure
      this pipeline has published about the quality of its own tracking rather
      than about the football.
- [x] **Re-fit the smoothing window against curved paths** (2026-08-08). The
      window distance was read off had been nine frames since the day movement
      metrics were written, as a bare number in `cv/pipeline.py`, fitted to
      nothing. It is the one parameter every figure in metres rests on, and the
      note above could only say what a *shorter* window costs. This is the other
      half.

      Two errors set it, and they pull opposite ways. Phantom distance falls as
      1/W and is exact rather than fitted: smoothing white noise leaves
      neighbouring samples correlated (W-1)/W, so the step between them is σ√2/W
      per axis and its 2D magnitude averages σ√π/W — **60·fps·σ·√π / W metres a
      minute**, matching measurement to three figures at every window and noise
      level tried.

      The corner loss was the fear, and measuring it is what dissolved it. A
      moving average over an arc pulls the path onto radius R·sin(θ)/θ with
      θ = vT/2R, so a turn of angle φ loses φ·R·(1 − sinc θ). Substituting the
      **tightest radius a person can actually hold** — a footballer manages
      about 4.5 m/s² sideways, so R ≥ v²/a — cancels the speed out entirely and
      leaves φ·a·T²/24. A 90° turn costs 0.03m at the old window and 0.29m at a
      full second, *whatever speed it is taken at*. A fast turn is necessarily a
      wide one, and that is the whole reason a long window is affordable.

      The real cost is not the curve but the cusp — sprint in, plant, come back
      the way you came — which an average smears straight through. Metres of
      real path lost to one 180° stop-turn:

          window      0.2s stop   0.4s stop   0.8s stop
           0.30s        0.36        0.19        0.09
           0.50s        0.82        0.52        0.26
           1.03s        2.12        1.71        1.10

      Two metres a turn sounds ruinous until it is set beside what it buys: at
      σ=0.20, lengthening the window from 0.3s to 1.0s stops **50 metres a
      minute** of invented distance. It would take 23 full stop-and-reverses a
      minute — one every two and a half seconds, all match — before the longer
      window is the worse of the two.

      So it was fitted rather than argued. Twenty minutes of synthetic ground
      truth (speeds from the match-play gears, direction changes at a stated
      rate, every turn held to 4.5 m/s²) at a real 181 m/min, in metres per
      minute of error:

          window    σ=0.05   σ=0.10   σ=0.15   σ=0.20   σ=0.30
           0.17s     +2.9    +11.3    +24.4    +41.3    +81.4
           0.30s     +0.7     +3.5     +7.8    +13.6    +29.4   <- was here
           0.50s     -0.2     +0.8     +2.5     +4.7    +10.8
           0.70s     -0.7     -0.2     +0.7     +1.9     +5.2
           1.03s     -1.7     -1.4     -1.0     -0.5     +1.1
           1.50s     -3.2     -3.1     -2.9     -2.6     -1.8

      **There is no best window in that table, and that is the finding.** The
      right one moves with the noise — and `position_noise_m`, added last week,
      measures the noise per track. So the window now follows it: 0.5s below
      0.075m of wobble, 0.7s below 0.15m, 1.0s above. That holds the error
      inside ±2 m/min from σ=0.05 to σ=0.30, where the fixed window drifted to
      +29 m/min — 1.8km over a match, on a figure a coach reads as kilometres
      run. Chosen per track rather than per run, because a player at the far
      touchline is projected through a stretch of the homography a player in
      the centre circle never touches.

      Fitted at 8 direction changes a minute and then checked against that
      assumption, since a rule that only holds at the rate it was fitted at is
      not a rule: across 3 to 25 turns a minute the worst error is -3.3 m/min,
      and across fragments from 60s down to 5s it is +2.1.

      Top speed and sprint counts came along for free. At σ=0.20 the old window
      reported an 8.0 m/s sprint as 10.4 and found 8 sprints where there were
      15; the fitted window reports 8.7 and finds 12.

      Two things fell out of doing it:

      **The window is now in seconds, not frames.** Nine frames is 0.3s at 30fps
      and 0.6s at 15fps, so the moment anyone subsamples the video to save
      compute — which is on this roadmap — every distance figure moves without a
      line of code changing. Written in seconds the frame rate cancels out of
      the phantom law exactly, and a still player costs the same metres a minute
      at any sampling.

      **The ends were being shortened, once per fragment.** Padding by repeating
      the last position pins it and drags the average toward it: 0.37m at the
      old window, 1.29m at a second, paid at both ends of every track, and the
      tracker hands over 3.4 fragments per player. Reflecting through the
      endpoint instead extends the line the player was on, and costs exactly
      zero on a straight run at any window — even one a third as long as the
      track. The price is 0.14m more noise per fragment, against 1.29m of real
      path no longer thrown away.

      The browser's `PHANTOM_M_PER_MINUTE = 353` had to go: it was one number
      only while the window was one number. The pipeline knows σ, W and the
      frame rate, so it publishes the rate, and `report.js` reads it — falling
      back to the old constant for reports already in Firestore, which is not a
      guess but the answer that was true of them. Schema 7, and the first
      version where a figure *changed* rather than appeared, so any baseline
      taken under 6 will correctly diff.

      What did not get better, stated so it is not only in a docstring: **top
      speed is still biased upward**, because a maximum of a noisy series can
      only ever be pushed up, never down. The fitted window roughly halves it
      (+30% to +9% at σ=0.20) and cannot do more. And **the smoothed path is
      still shorter than the real one** — the corner it cuts is real. The totals
      agree because the two errors are of a size, not because either went away.

      417 pure JS · 120 emulator · 801 Python.
- [x] **[MVP] Tracking fast enough to keep up during live first-half play**
      (2026-08-10) — *measured, not achieved*. The deadline is real and it is the
      only one in this project: a half-time report handed over ten minutes into
      the second half describes a match the coach is already losing differently.
      What was missing was not speed, it was the ability to say anything about
      speed at all.

      `processing_s` has always been reported. One number, and it answers a
      different question. "This took eleven minutes" cannot tell anyone whether
      the report would have existed at the break, and it cannot tell anyone what
      to change — a smaller model, a lower sample rate and a shorter window are
      three different fixes and the total looks the same under all of them.

      Now there is `cv/timing.py`: a stage per piece of work, a **real-time
      factor** (seconds of work per second of football), and the figure worth
      putting in front of a person, which is not a ratio but a length of time —
      *how late the report would be*. Plus
      `python -m cv.experiments.speed_report`, which runs the whole pipeline at
      several sample rates and names the fastest one that fits a live budget.

      **Three judgements are load-bearing, and one of them was wrong until it was
      measured.**

      *The remainder is a line, not a rounding error.* Stages never add up to the
      whole — setup, imports, teardown and everything nobody wrapped fall
      outside. A breakdown that hides its own gap sends the reader optimising the
      biggest named stage while a third of the run sits elsewhere. On the very
      first instrumented run the gap was **60% of the total**, which is exactly
      the case the decision was made for.

      *A slow run is not an inaccurate one.* `realtime_factor` is deliberately
      **not** a warning. `trustworthy` is `not warnings`, so counting slowness as
      a defect would mark a perfectly good batch report unreliable for taking its
      time. It sits in the quality block; the browser says something only when
      the factor is at or above 1, and says it as *"this took longer to work out
      than the football it watched — about 18m behind a live half"*, because
      "1.4x real time" does not tell a coach they waited.

      *A clip does not scale onto a half by multiplication* — and this is the one
      the first design got wrong. Three runs back to back over six seconds of
      synthetic footage:

          cold   8.86s total   1.86s loading the detector   6.99s detecting
          warm   2.62s total   0.04s loading                2.57s detecting
          warm   2.02s total   0.04s loading                1.96s detecting

      Divided flat, the cold run reads **1.48x — "cannot keep up, abandon it"**.
      The same pipeline on the same clip once warm reads **0.33x, comfortably
      live**. A 4.5x spread that decides the verdict, and none of it about the
      football. So every stage now declares how its cost grows: `FIXED` for
      loading a model (paid once, kept out of the rate entirely), `LINEAR` for
      decode and inference, `SUPERLINEAR` for `identity.merge_tracks`, which is
      quadratic in tracks that themselves grow with the footage — so a projection
      containing a meaningful one says **at least** this late rather than this
      late. The unmeasured remainder is assumed to scale, because on a deadline
      the pessimistic assumption is the right default.

      The same effect bit the tool itself: whichever sample rate ran first paid
      the warm-up and read slow, producing **6Hz at 0.42x and 15Hz at 0.36x**,
      which is backwards. `speed_report` now does a throwaway pass first, after
      which the same three rates come out 0.14x / 0.37x / 0.71x, in order.

      **What this does not do.** It does not process footage incrementally during
      play — that is the separate open item in Phase 4 — and it does not say
      whether *this* pipeline keeps up with *real* football, because the only
      footage available is a synthetic clip of coloured blobs — and, as the
      camera-shift work turned up the next day, **YOLO detects nothing at all in
      it**: zero people, every frame. So the rates above are decode plus
      inference over an empty pitch, with tracking, identity, touches and events
      all doing no work whatsoever. They are a floor on a floor, and the real
      figure will be far worse. The tool is right; the only clip it has been
      pointed at is not football. Every number above is a statement about one
      machine, one clip and one model on one afternoon, which is why the tool
      prints exactly that under its own results and why none of its figures are
      recorded here as a property of the pipeline. What is settled is that the
      question now has a way of being answered, on the machine that will actually
      be at the side of the field, before the day rather than after it.

      `SCHEMA_VERSION` 9 → 10. 536 pure JS · 120 emulator · 952 Python.

## 7. Team & Player Identification
Automatic tracking only needs to be *internally consistent* — resolving a track ID to a
real roster player is a human job, cheaper now thanks to Phase 3.
- [x] **[Demo]** Team discrimination via jersey colour clustering — `cv/teams.py`.
      Three decisions carry the accuracy: sample the torso rather than the box
      (a box is mostly grass, hair and socks), cluster in Lab rather than RGB
      (shadowed red and sunlit red are far apart in RGB and half a pitch is in
      shadow), and decide per *track* rather than per frame. Keepers and
      referees match neither kit and are left unassigned rather than forced
      into whichever they resemble least
- [x] Goalkeeper detection (distinct kit, stays near one goal) — `cv/keeper.py`.
      Colour proposes, position confirms, a human overrides both. Without a
      calibration there is no position and it returns `method='unavailable'`
      rather than guessing, because a wrong keeper poisons save percentage,
      distribution and every keeper feature in the xG model at once
- [x] **[Demo]** Consume the Phase 3 live substitution log to narrow which
      roster players could be a given figure — `rankRosterForCluster`. It does
      not solve *which* blob is *which* name; a human still makes that call
      once per tracking segment. What it does is shrink the candidate list
- [x] **[Demo]** Remaining manual track-ID → roster-player mapping happens in the review tool, narrowed down by the sub log — `rankRosterForCluster` plus the coach picker's "On the pitch then" / "Everyone else" grouping. Reorders rather than filters, since a wrong video offset would otherwise hide the right answer along with the wrong ones.
- [ ] **[Stretch]** Jersey number OCR, used only as a suggestion to speed up mapping

Broken tracks — one player split across several IDs — are handled by the
many-to-one picker in Phase 11, and tracked there rather than duplicated here.

## 8. Coordinate Transformation & Movement Metrics
**Built** in `cv/metrics.py`, though gated on Phase 6's fragmentation before
per-player figures mean anything.

The load-bearing detail is jitter. A detection box wobbles a few pixels even
when a player stands still, and every wobble reads as movement — measured, a
stationary player accumulates **30m+ of phantom distance over 10 seconds**.
Smoothing is not polish here, it is the difference between a number and a
fiction; `tests/test_metrics.py` pins both the problem and the fix.
- [x] **[Demo]** Project each detection's ground contact point through the homography — the bottom-centre of the box, the only point on a standing person that lies on the mapped ground plane
- [x] **[Demo]** Per-player speed, distance covered, sprint counts, heatmaps
- [x] Implausible-speed rejection, so one identity switch cannot dominate a total; the discard count is reported, since a high one means the whole figure deserves suspicion
- [x] Team shape: width, depth, compactness — needs no player identity, so it survives Phase 6's fragmentation
- [x] Per-third territory splits (`cv/territory.py`) — where each team had the
      ball, named from that team's own attacking direction, taken from the same
      smoothed per-frame labels the possession split is built from so the two
      figures cannot disagree about the same half. Contested and dead-ball time
      belongs to nobody. Needs a calibration, like everything in metres.
- [x] Shape drift (`metrics.shape_drift`) — the same width/depth/compactness
      figures early in the window against late, so "you were four metres wider
      by the end" is sayable. Two averages either side of a split rather than a
      fitted gradient, because two averages are a thing a coach can be told.
      Returns None rather than a zero drift when either side is too short to
      average.
- [x] **Acceleration**, as bursts rather than as a second derivative. Each
      derivative multiplies the noise: the jitter that fakes 6 m/s of speed
      between two frames fakes 180 m/s² between three, fifteen times what a
      human can produce. A frame-to-frame acceleration is therefore not a noisy
      version of the real thing, it is noise with the real thing somewhere
      inside it.

      So a burst is a **speed gain sustained across a second** — one derivative
      of an already-smoothed speed, averaged over long enough that zero-mean
      jitter cancels while a real acceleration does not. Measured on synthetic
      tracks (`tests/test_bursts.py`), reading bursts straight off the
      pipeline's own smoothing gives 40 false ones per minute at σ=0.20 against
      37 real, and no threshold separates them because they are the same noise.
      Smoothing the burst pass over a full second instead gives **zero** false
      bursts at every level to 0.20m and finds all eight real ones exactly, on
      all forty seeds tried. At 0.20m the count drops to 6-8, and the shortfall
      is one-sided: noise loses bursts, it does not invent them.

      Two costs, both stated rather than warned about. A burst shorter than the
      window reads low — a true 10 m/s² over 0.4s comes out at 3.6 — which is
      the direction worth being wrong in. And past 0.3m of positional wobble the
      count is refused outright rather than reported, because there it is a
      count of the jitter and it would sit on a player's card looking exactly
      like a count of their runs. Null, never zero: a player watched for half a
      second did not fail to accelerate.

## 9. Ball Possession & Game State
- [x] **[Demo]** Determine which player/team currently has the ball —
      `cv/possession.py`, and the one substantial team statistic reachable from
      pixels alone: it only asks who is nearest the ball, never how far in
      metres. Two problems had to be solved for the answer to mean anything.
      **Scale** — "near the ball" is a distance, and in pixels it changes with
      every zoom, so the radius is expressed in player heights measured from
      that same frame. **Flicker** — two opponents contesting the ball swap
      nearest-player several times a second, so a change is only accepted once
      a team has held it for a minimum spell
- [x] Dead-ball spans derived from the Phase 3 tag log (`cv/phases.py`) — feeds
      possession (a throw-in wait no longer counts as possession) and stamps
      `in_play` on every derived event. See "Also built ahead of the footage" above.
- [x] **[Demo]** Cross-check that against an independent CV signal (ball leaving
      frame / going out of bounds) rather than trusting the tag log alone —
      `cv/reconcile.py`'s `ball_exits` walks the observed ball points through
      `zones.leaves_play` and checks the result against tagged throw-ins,
      corners and goal kicks. Only observed points count: an interpolated one is
      a straight line drawn between two sightings, so letting it cross a
      boundary would invent stoppages precisely where the pipeline saw least.
      Needs a calibration, and needs ball coverage good enough to mean anything
      — so the rate it produces will be meaningless until the footage improves.
- [x] **Half tracking, driven by the Phase 3 period-boundary taps** (2026-08-07).
      `MatchOrientation.attacking_end` flips the whole pitch on the period, and
      the period came from one string defaulting to `first_half`. Process a
      second half and forget `--period` and every shot map, heatmap reading,
      pressing zone, territory split, turnover-by-third, passing network and xG
      figure in the report is mirrored — and the output looks **exactly as
      plausible** as a correct one. There is no downstream check that could
      catch it: a shot map at the wrong end is a shot map.

      The tagger has been tapping `kickoff_1st`, `kickoff_2nd`, `halftime` and
      `full_time` since Phase 3 and nothing had ever read them for this.
      `periods_from_log` in `cv/phases.py` turns them into spans; the CLI flag
      now defaults to **None** and exists to override the log or to stand in
      when there is none. A flag that disagrees with the log loses, and the
      disagreement is said out loud, because one of the two is wrong and it
      matters which.

      Three refusals. The break belongs to **neither** half — nobody is
      attacking anything while the sides swap over — and so does everything
      before the first kickoff, because a shot in the warm-up is not first-half
      football. A half nobody closed runs open-ended rather than being assumed
      to last forty-five minutes, since that would invent the one boundary the
      whole thing turns on. And a window that runs **through** the break has no
      single right answer: it is drawn as whichever half holds most of it, and
      warned about in terms of what that costs, which makes the run
      untrustworthy — correctly, since half of it is backwards.

      A window that touches none of the log's halves warns about the **video
      offset** instead, which is the likeliest cause and is also silently
      misplacing every dead span.

      Published as `period` and `period_source` (schema 5), and the quality note
      speaks only when the source is `default` — a correct answer arrived at
      correctly is not a caveat, and a note that fires on every run takes the
      ones that matter with it.

      **Found by asking where the new warning would appear: nothing had ever
      drawn `warnings` at all.** `report_json` has published the list since the
      pipeline existed, `trustworthy` is defined as `not warnings`, and no page
      rendered either. Every warning written so far — the untagged restarts, the
      disputed goals, the fragmentation cap, the missing tag log — was a
      judgement the pipeline made about its own output that no coach could see,
      which also means the rule *"a warning is only ever about data quality,
      never a cosmetic limit"* was being enforced on behalf of a reader who did
      not exist. `.cv-warnings` now sits above the quality note on the coach's
      match view, amber, kept separate from it: the note is what every figure
      below rests on and is always true, a warning is something that went wrong
      once and may be fixable.
- [x] **Match-clock tracking within a period** (2026-08-07). The tablet's clock
      **stops at half-time** — `advancePeriod` freezes it at the break and
      restarts it from the same second — and the video's clock stops for
      nothing. So `clockS = videoS - videoOffsetS` was exact for the first half
      and wrong for the whole of the second by however long the interval ran, in
      both directions at once: a goal tagged at 52:30 seeked into the middle of
      the oranges, and a shot found at video 68:00 was reported as the 68th
      minute of a match that was 53 minutes old. Ten to fifteen minutes of error
      on every second-half timestamp in the app, invisible because both numbers
      come out looking like plausible minutes.

      Four independent copies of that subtraction in the browser — `videoTime`,
      an inline one in `renderMatchVideo`, three in `report.js`, `toMatchClock`
      in coach.js — and three more in Python, through `PhaseTable.shifted`,
      `PeriodTable.shifted` and `reconcile.tagged_times`. All seven replaced by
      one map on each side: `matchClockMap` in report.js and `phases.VideoClock`.
      The Python one is the worse loss: `PeriodTable` is what decides which way
      each team is attacking, so a shift across the very boundary it exists to
      find mirrors every shot map, heatmap, pressing zone, territory split and
      xG figure in the report.

      Fixing it needs exactly one fact the offset does not carry — where in the
      video the second half kicks off. Nothing can derive it: the break's length
      is not in the tag log, because the tag log is the thing that froze. So the
      coach supplies it (`secondHalfVideoS`, a second field beside the offset)
      and the clock it restarts on comes from the tablet, which knew it at the
      time and now writes it down (`halfTimeClockS`, written by `writePeriod` at
      the half-time tap and cleared again if that tap is undone). In Python
      only the video position has to be given, because `VideoClock.from_periods`
      reads the restart's clock reading straight out of the log's own spans.

      Three rules the arithmetic follows. A pair implying a **negative** break is
      refused rather than used — a non-monotonic clock maps two positions in the
      footage to one reading, which is wrong in a way nothing downstream could
      describe, where a shifted one is wrong by a known amount in a known
      direction. Inside the break the clock returns the **frozen** reading and
      labels it, because that is the truth and because printing 45:12 on three
      things that did not happen together is worse than saying "half-time". And
      the whistle and the restart are one reading and two positions, so
      `to_video` sends a tag there to the restart while `to_video_end` sends the
      first half's boundary to the whistle — without that distinction the first
      half's span swallowed the interval and a frame of somebody eating an
      orange was reported as first-half football, drawn at whichever end that
      implies. That one was found by a test, not by reasoning.

      With no second anchor everything degrades to exactly the old behaviour and
      **says so**: no period is claimed, and the coach's form states the
      consequence rather than showing an empty field — a coach who never fills
      it in has no other way to suspect anything, since the timestamps they get
      look like ordinary minutes. The strip beside the fields draws the lead-in,
      the half and the break to scale, so a break taking three quarters of the
      bar is a typo caught before it saves rather than after.

      384 pure JS · 120 emulator · 739 Python.

## 10. Event Detection
CV produces automatic *candidates*, reconciled against the Phase 3 live-tagged events
rather than replacing them.

Everything below rests on one primitive: **touch segmentation** (`cv/touches.py`) —
the moments the ball's motion changed while a specific player was close enough to
have caused it. Given an ordered list of touches, the rest is mostly geometry over
adjacent pairs (`cv/events.py`), not seven separate detectors.

- [x] **[Demo]** Pass detection (completed vs. intercepted), with length buckets, direction, progressive / final-third / box-entry / switch / cross tagging
- [x] **[Demo]** Shot detection, with outcome (goal / saved / blocked / off target)
- [x] **[Demo]** Goal detection — via `zones.enters_goal_mouth`; must still be cross-checked against a live tap
- [x] Turnover / tackle detection, plus interceptions, recoveries and ground duels
- [x] Carries, pressure counts, PPDA
- [x] Stoppage candidate flagging (ball out, play stopped) — `reconcile.ball_exits`,
      and used as the cross-check rather than as a primary source, exactly as
      this bullet anticipated
- [x] Turnovers located by third (`events.turnovers_by_third`) — the catalog's
      "dangerous turnover locations" ask. `PossessionSummary` counts turnovers
      but a spell carries no position at all, so the count comes off the event
      log, at the point the pass was played rather than where it ended up
- [x] Reconciliation logic: where CV and live tags agree, treat as high
      confidence; where they disagree or one is missing, flag prominently for
      the Phase 11 reviewer — `cv/reconcile.py`. The two vocabularies intersect
      on exactly one word, `goal`, and that is deliberately all that is compared
      head to head; inventing overlap where there is none would manufacture
      agreement. Disagreements become warnings and sit at the top of the review
      block, seekable. **Not an accuracy**: both records can be wrong about the
      same moment in the same direction, and this would call that agreement. It
      is a standing metric to watch drift on, exactly as Testing Strategy #5
      frames it.
- [ ] **[Stretch]** Offside detection — leave human-marked-only for the foreseeable future

**What is written but unmeasured.** Every threshold in `cv/touches.py` is a guess,
never yet compared against a human watching the same footage. The synthetic tests
prove the algorithms do what their docstrings say; they say nothing about whether
that matches real football. `cv/experiments/event_report.py` exists to make that
check cheap — print the timestamped list, scrub, mark each one right or wrong.

**Why it currently finds nothing.** On the available footage `segment_touches`
returns zero touches, and that is the correct outcome rather than a failure. The
ball is detected in ~65% of frames, but the nearest player to a detected "ball"
sits a **median 6.3 player heights away** — so most of those detections are not
the ball. Ball recall from a camera that holds still is the gate on this whole
phase, not more event logic.

**Two structural limits, neither tunable.** *Intent* is not observable from boxes,
so a deflection that reaches a teammate counts as a completed pass and a good
clearance counts as a failed long one. *Height* is unrecoverable from one camera,
so aerial duels, headers and punt-versus-goal-kick stay out of reach — the same
wall `shot_height` hits in Phase 12.

## 11. Post-Game Review & Annotation Tool
Pre-populated with Phase 3's live-tagged events and subs, plus Phase 10's CV
candidates — the reviewer's job is verifying/correcting/filling gaps, not labeling from
scratch.
- [x] **[Demo]** Timeline UI showing CV-candidate events, synced to video
      playback (`coach/coach.js`'s review section). **The merged list shipped
      2026-08-09**: the review column now holds both records in time order, with
      the tagged entries read-only and each candidate carrying whatever a human
      tapped within six seconds of it. See Phase 3 for why a tagged row is
      deliberately not a candidate. The match page keeps its own timeline, which
      is a different job — that one is the match as it happened, for a coach who
      is not reviewing anything.
- [x] **[Demo]** Confirm / edit / delete / add-new-event controls per timeline entry — confirm, reassign-type-or-player ("edited"), reject, plus recording an event the video missed, which is the recall half of validating the detector, not an extra.
- [x] **[Demo]** Track-ID → roster-player assignment UI with thumbnail crops,
      pre-narrowed by the sub log. The narrowing shipped with Phase 7; **the
      crops shipped 2026-08-06** (`cv/thumbs.py`). Until then the picker
      described each figure as *"team a · 3 fragments · 12:04–19:31 · 2,410
      frames"* beside a swatch of kit colour, which describes a figure without
      answering the only question being asked, and the question is visual:
      *that is the tall one who plays left back.*

      Cut inside the decode loop, because what survives a batch is a few
      numbers per detection and never the image — the same constraint that puts
      colour sampling there, and the reason this is a scoring function plus an
      encoder rather than a pass of its own. It shares `colour_every` rather
      than getting a cadence of its own: both are asking the same thing of the
      same pixels, and a second interval would be a second thing to tune with
      no evidence for either setting.

      **Most of the work is the refusals**, because a picker full of bad crops
      is worse than a picker full of swatches — a coach can tell a swatch is
      uninformative and cannot tell that the smudge they just named was two
      people. Nothing clipped by the frame edge, since half a player is not
      recognisable and reads as a smaller one. Nothing wider than it is tall,
      which is a merged box or somebody on the ground; that rule earns its keep
      because a merged box is *large*, so ranking on size alone would make it
      the portrait of both players. And **never upscaled** on the way in: a
      player forty pixels tall gets a forty-pixel picture, because blowing every
      crop up to a uniform size would invent detail the sensor never recorded
      and make an unusable one look usable. Under 40 px the row says so.

      One thing the browser check caught that no unit test would have:
      `object-fit: cover` on a 35×104 crop threw away 40% of the height —
      the head and the feet, which is most of what makes a teenager
      recognisable at this distance. It is `contain` in a person-shaped frame.

      Cheap enough not to need managing: a real crop encodes to about **1.1 KB**,
      so forty figures is ~47 KB against a 1 MB document. The budget in
      `fit_budget` is a safety net that serves the longest-tracked figures
      first, and deliberately raises **no warning** when it bites —
      `trustworthy` is `not warnings`, and a run whose briefest figures went
      without a picture is not a run whose numbers are worse.
- [x] **Merge-tracks control for split IDs** (2026-08-10). The mechanism was
      already there and this line said so: the picker is many-to-one,
      `cvStatsByPlayer` sums across every cluster mapped to the same player, and
      merging two fragments is done by naming them both. What was missing was
      only the saying-so.

      That turns out not to be a small thing. `cv/identity.py` will not bridge
      an absence longer than two seconds, so a player who goes off, or who
      leaves frame while the camera pans, comes back as a second figure — and
      the list is ordered by how long each figure was tracked, which puts the
      two halves of one player's match as far apart as the list gets. Finding
      the second one means recognising a face in a forty-row list you have
      already scrolled past, and the cost of not bothering is that a player's
      distance and touches are quietly the first half of their match only.

      So naming a figure now opens a shortlist under it: **the other figures
      that could be this same person**, each with a picture, and one press to
      say so. Press it again to undo. The row's own line becomes "Alex Vega is
      2 figures · change", which is also the first time the app has said out
      loud that a player was assembled from more than one.

      **The one thing that can be said with certainty**, and it is the same one
      `identity.py` leans on: two figures on screen in the same frame are two
      people, whatever they look like. That is the only candidate the control
      refuses outright — disabled, not merely dimmed.

      Making that call in the browser needed an argument, because the browser
      has intervals and not frame sets, and an interval is normally far too weak
      a thing to reason from — two intervals can overlap while sharing no frame.
      They cannot here. Every merge in `identity.py` joins a pair with a gap
      between 0 and `MAX_BRIDGE_S`, and a cluster is connected through such
      pairs, so **no cluster has a hole in it longer than two seconds**: its
      interval is solid, and an overlap wider than one bridge really is two
      people. `tests/test_identity.py::TestSolidInterval` asserts that against
      the merger itself, including a deliberately messy twelve-fragment run,
      because it is the load-bearing half of the argument and it lives in the
      other language. If a future merge rule bridges a longer absence, that test
      fails and the browser's exclusion has to be loosened with it.

      **Everything else is evidence, so it orders the list rather than
      shortening it.** How long the gap was, whether the shirt colour matches
      within the same Lab threshold `identity.py` uses, whether the player just
      named was even on the pitch for it. Each of those is written on the card —
      *"40s later, from 07:40"*, *"a different shirt"*, *"— but they were off the
      pitch"* — and none of them removes a row, for the same reason the picker
      above it refuses to filter: the video offset is the fiddliest number in the
      app, and hiding the poor fits would hide the right answer on the day it is
      wrong. A figure somebody else already has is offered too, saying whose it
      is; a coach who named a fragment wrongly finds that out here.

      The ruled-out ones are shown as well, greyed, with the objection on them.
      An empty space where a figure used to be reads as a bug, and the coach
      wondering why Figure 12 is not offered deserves the answer on screen.

      One regression caught while building it and worth naming, because it was
      created by this change rather than found: naming a figure now rebuilds
      every row, since one answer changes what the others can suggest. That is
      fine for a mouse and quietly hostile to a keyboard — the `<select>` just
      used no longer exists, focus falls back to the body, and the next Tab
      starts from the top of the page, fifteen times over. The control the coach
      pressed is put back under the cursor.

      533 pure JS · 120 emulator · 915 Python.
- [x] **Save finalized data as the source of truth for stats, profiles, xG
      logging, and the player portal** (2026-08-09). The review tool exists to
      correct the pipeline, and its corrections reached the scorecard, the shot
      map and the xG check — and **nothing a player ever saw**. A coach could
      reject thirty phantom passes, watch precision fall on the scorecard, and
      still publish a report crediting the player with all thirty.
      `cvStatsByPlayer` summed the pipeline's per-cluster totals and had never
      been shown the coach's verdicts at all.

      One rule now: **the coach's verdicts are the source of truth for the
      event-derived figures**, on their own screen and in the published report,
      which are computed from the same call.

      What moves is the event counts, and only those. A rejected pass is a pass
      that did not happen. A pass retyped as a tackle is one fewer pass and one
      more tackle. A tackle reassigned to another player moves whole — which is
      the correction that matters most, because it is how a coach fixes an
      identity the cluster mapping got wrong without redoing the mapping.

      What does not move, and the reason is the same each time:

      - **Distance, top speed, sprints and bursts** come from the *track*, not
        from the event list. No verdict about an event is a verdict about where
        a player ran, and subtracting metres because a pass was imaginary would
        be inventing a correction nobody made.
      - **Touches**, for the same reason: a touch is a moment the ball's motion
        changed near a player, and rejecting the event derived from it does not
        prove the ball never moved.
      - **Shots and xG** are left to the ledger that already decides them. A
        second subtraction here would take a rejected shot off twice. What did
        change is that `correctedShotMarks` now **drops** a rejected shot rather
        than keeping it with its original number — unlike an unscorable header,
        which happened and cannot be scored, a rejected shot did not happen, and
        a dot on a shot map is a claim that it did. The count and the xG are
        both read off that one list, so they cannot disagree.

      And what stays counted: **everything unreviewed**. The review is partial by
      nature — twelve events out of five hundred — so this starts from the
      pipeline's totals and subtracts what a human contradicted, rather than
      starting from nothing and adding what a human confirmed. The other way
      round, a coach who checked ten events would wipe out the match.

      A corrected figure says so, on both pages. A number that moved between two
      visits looks like a bug unless something says a person moved it — *"your
      review has adjusted the video columns for 2 players"* on the coach's
      table, and on the player's own page *"your coach has checked some of these
      against the video and corrected them"*.

      **The browser found the defect, and it was the same one as last time.**
      The corrections computed correctly and the player table never redrew: on
      the emulator, rejecting a tackle dropped it from the scorecard and left it
      in the player's row. `redrawShotViews` is the one function every write to
      `cvReview` goes through, and the player table is now a view of the review
      like the four surfaces already on it. Verified live afterwards: 5 tackles
      → reject → 4 with the note appearing → undo → 5 with the note gone.

      505 pure JS · 120 emulator.
- [x] Doubles as the ground-truth labeling tool for Phase 16 validation, and as
      a source of labeled data for fine-tuning detectors later (Phase 5) —
      "Download the labels" exports the reviewed set as JSON, built in the
      browser from data already loaded. It states in the file that unreviewed
      events are **not** negatives, since a consumer that assumed otherwise
      would train on the pipeline's own unchecked guesses.
- [x] Report precision and recall per event type from those decisions
      (`reviewScore`) — the numbers the tool exists to produce, and previously
      collected but never computed. An edit that only reassigns the player
      leaves the type standing; an edit that changes the type counts against the
      type claimed and as a detection of the type it should have been, because a
      mislabelled event was still found. Null rather than zero where nothing has
      been checked, and captioned with the denominator.

## 12. Shot Feature Extraction → Existing xG Model
Where the CV pipeline plugs into what already works (`xg-sandbox/` / `xg_model8.onnx`).
- [x] **[Demo]** At shot detection/confirmation, extract the same 12 features
      the model expects, using the correct attacking-goal direction for the half
      (Phase 4) — `xg_for_shots`, called from `cv/pipeline.py`. The direction
      now comes from one shared `attacking_end_for`, rather than the event layer
      and the xG layer each working it out; two places deciding which way a team
      kicks is how a second-half sign error gets in, and it would show up as
      plausible xG for shots at the wrong goal rather than as a crash.
- [x] **Body part classification (foot vs. header)** — the manual tag, taken as
      far as it goes. Pose estimation is still open and now much less urgent.

      **Measured first.** Against the real model, the same position struck with
      the head is worth a fraction of what it is worth off the foot: **1.25x at
      a tight angle, 1.69x from six yards, 1.78x from the penalty spot, 3.71x
      from the edge of the box.** Every shot in the report was being scored as a
      foot shot, so headed chances — mostly close range, where the ratio is
      worst — were inflated by a third to three times over. That is larger than
      the position noise the entire `xgTrust` ladder exists to manage, and
      unlike noise it runs one way. It was also about to corrupt the new xG
      check: headers inflate `predicted`, so the calibration would have drifted
      toward "the model is rating these too high" for a reason that has nothing
      to do with the model.

      **Both answers precomputed.** `xg_for_shots` now returns `(foot, header)`
      per shot and `Shot.xg_header` carries the second. The question is binary
      and the model is cheap, so two numbers ship instead of an inference
      runtime on a coach's phone — no onnxruntime in the browser, no async, no
      CDN dependency on the match page, and the correction works offline.

      **`event_id` on every shot mark**, which is what makes the join sound. A
      rounded timestamp is not an identity, and two shots inside the same second
      would have swapped corrections with nothing looking wrong.

      **The tag lives in `cvReview`, never in `cvStats`.** Same boundary as
      `cvMapping`: a coach's judgement must stay distinguishable from a
      measurement. `teams.*.xg` remains what the pipeline measured; the
      correction is applied at render time to the map, its caption, the shot log
      and the xG check together, so the page never shows a corrected total
      beside an uncorrected one.

      A header tagged on a run made before this shipped is scored as **nothing**
      rather than falling back to the foot figure — that fallback is precisely
      the error being corrected. It leaves the totals and the row says so.
      Found in the browser: with every tagged header unscorable, the note read
      *"which took 0.00 xG down to 0.00"*, which is true and says the opposite of
      what happened. `headerNote` now keeps the two facts as two sentences.

      The tags also travel in the labels export beside both readings. Body part
      is the one field in that file the pipeline cannot produce at all, and it is
      exactly what a pose model would have to be trained on.

      **The corrections reach the players.** A publish carries the tagged
      headers into each player's own `cvXg` and `cvShotMap`, so the same match
      cannot read one way on the coach's page and another on a sixteen-year-old's
      — the worst kind of disagreement, because neither side can see the other
      well enough to notice. Their shot map labels a headed dot as one, since it
      is drawn smaller than a foot shot from the same spot and nothing else on
      that page would explain why.

      This turned up a **pre-existing bug that the header tag made likely**:
      `publishReports` wrote each report with `set()` and no merge, so every
      re-publish silently deleted the heatmap, attacking end and calibration
      error that `cv/publish.py` adds *afterwards*. Nothing read those fields
      back before the player's own page did, by which point the coach was long
      gone. It is a merge now, with every video field explicitly nulled when a
      player has no confirmed mapping — which is what the overwrite used to do
      for free — and `tests/flow.test.js` walks the real publish → pipeline →
      re-publish order.
- [x] ~~`shot_height` is a z-axis value a flat single camera + homography can't give directly~~ — resolved by deleting the question. It was the height the ball *ended* at, so no amount of pose estimation would have recovered it before the shot was taken. See the retrain item below
- [x] **[Demo]** Feed features into the existing ONNX model, log predicted xG —
      and note that until 2026-08-02 this was written but never called, so every
      xG in a schema-2 document is null because nothing computed it, not because
      no shots were found
- [x] **[Demo]** Validate the model against CV-derived (noisier) features before
      trusting it live (Testing Strategy #6) — measured, see the table above.
      Half a metre of position error moves a single shot by ~0.030 on a 0.188
      baseline; at 2 m the p95 shift exceeds the xG itself. Pinned by
      `tests/test_xg_noise.py`. Retraining with noise is not needed yet: the
      per-shot spread is wide but team totals average most of it out, and the
      real gate is still whether a shot can be detected at all
- [x] Show per-shot xG only when the calibration supports it — `xgTrust`
      (`assets/report.js`) turns the measured noise table into three states
      rather than a number with a warning beside it. Under 1 m the per-shot
      figure shows and the shot map sizes by it; to 4 m only the team total
      shows and every dot on the map draws the same size, because a size
      difference is a claim that two chances differ by the amount they look
      like they do; past 4 m nothing shows, since even a six-shot total moves
      26% by then. The hover label drops the number on exactly the
      bands the radius does — a figure the map has stopped drawing but a
      tooltip still reports is the same claim made quietly. The band is applied
      identically on the coach, half-time and player pages, which is why
      `cvCalibrationErrorM` is now published onto each player report: a player
      never reads the team document, and without it the portal would have sized
      a map the coach's own page had flattened
- [x] **Retrain and re-export the model with its calibration attached** —
      `xg_model8.onnx`, 2026-08-06. See *The xG model was never the model that
      was measured* above for what was wrong and by how much. `main.py --offline`
      now re-runs the whole thing from the cached shots without touching the
      network, verifies the exported file against the fitted estimator before it
      finishes, and prints predicted-goals against goals-actually-scored, which
      is the one line that would have caught the original bug
- [x] **Retrain without `shot_height`** — `xg_model8.onnx`, 2026-08-06, eleven
      features. See the table above for what it was costing. The sandbox's
      height slider went with it, since there is no longer a feature for it to
      drive, and `xgTrust`'s per-shot band tightened from 1 m to 0.5 m because
      the leaner model leans harder on position
- [x] **Log actual outcome (goal/save/block/miss) to check predictions against
      reality** — a shot ledger under the shot maps on the coach's match page,
      one row per detected shot, five buttons on each. Stored as `result` beside
      the existing verdict in `cvReview/decisions`, which is the whole reason it
      goes there: a shot the coach *rejected* is not a shot, and it falls out of
      the check for free.

      Two rules make the marking worth anything. **The verdict has to come from
      a person.** `Shot.outcome` is already in the report, read off a ball the
      pipeline sees in about 60% of frames; grading the xG model against a ball
      detector's guess measures the agreement of two guesses. It is printed
      beside the buttons and never preselects one — a prefilled answer clicked
      past is an unmarked shot with a signature on it. **And goals are rare.**
      Under the model each shot is its own weighted coin, so a set of shots
      should produce `Σ xg` goals with variance `Σ xg(1−xg)`; two standard
      deviations is the gap the sample could actually have found, and it is
      printed in the same breath as the difference rather than as a footnote.

      The two "no gap found" states are deliberately **not** the same word.
      `consistent` means the sample was big enough that a model 50% out would
      have shown; `inconclusive` means it was not. The threshold is a **share of
      the prediction, not a number of goals** — the band grows with √n and the
      prediction with n, so a goal-denominated threshold calls three shots
      conclusive and a season inconclusive, exactly inverted. On typical
      chances, telling a good model from one half out takes about **150 shots**:
      a season of both teams', and the app says so instead of implying sooner.

      A directional verdict is also withheld below four expected goals, because
      the band is a normal approximation to a sum of coin flips and is worthless
      when the expected count is tiny. Caught in the browser, not by a test: two
      long-range efforts and one lucky finish were being reported as *"the model
      is rating these chances too low"*.

      The four-number tally is stored on the **match document**, so the season
      line on the matches tab costs no reads at all — `listMatches` already
      returns whole documents. `variance` travels rather than a standard
      deviation because variances add and roots do not, and storing the root
      would widen every season band and turn real miscalibration into "cannot
      tell". Its own coach-only rule, separate from the tagger-writable one
      above, and every bound in it is an arithmetic invariant rather than a
      guessed cap: `predicted ≤ shots`, `scored ≤ shots`, `variance ≥ 0`. The
      marks also travel in the labels export beside the xG that predicted them,
      which is the only ground truth this system produces.

      Withheld entirely when `xgTrust` is `'none'` — a run whose positions are
      too loose for the app to print a total is too loose to quietly become a
      season's evidence. The marks are still kept and can be re-checked against
      a better calibration.

## 13. Player & Team Statistics / Profiles
- [x] **The domain model is the documents, and the one thing it was missing was
      a position** (2026-08-12). These two lines asked for `Player` and `Team`
      classes. Checked before building them: `aggregateMatch`, `seasonTotals`,
      `cvStatsByPlayer`, `cvReportFields` and `trackedCoverage` are **imported by
      the coach page, the player portal and the half-time page alike** — there is
      no duplicated per-player arithmetic to consolidate. The model exists. It is
      a Firestore document shape plus a set of pure functions over it, and
      wrapping that in classes would add indirection over data that arrives as
      JSON and has to be serialised straight back.

      **But the item named something that genuinely did not exist**: a playing
      position. `role` in this repo has only ever meant *access* — coach, tagger,
      player — and the two must not be confused, since one decides what a person
      may read and the other where they stand on a pitch.

      It is not decoration. The player table is ranked down one column of metres
      a minute, and **a goalkeeper covers a fraction of the ground an outfielder
      does**, so a keeper sat in that ranking as the least mobile player on the
      pitch — which is not a finding, it is the job. The table now groups into
      lines, keepers first, and says why once underneath.

      **Four positions and no more.** Football names them as finely as you like,
      and every extra name is another judgement about a sixteen-year-old who
      probably played three of them this season. Four is what these figures can
      be read against: a line of the team.

      **Nothing happens until somebody fills it in.** `groupByPosition` returns a
      single untitled group when no player has a position, so a coach who has
      never touched the field gets exactly the table they had, in exactly the
      order they had it — headings appear on the first position set and not
      before. An unrecognised value counts as unset, so one stray string cannot
      switch the layout for the whole squad. The involvement sort survives
      *inside* each line: grouping adds a heading, it does not take the ranking
      away.

      A closed vocabulary in `firestore.rules`, not just in the browser. A
      free-text field on a roster is somewhere to type anything at all about a
      named minor, and this collection already holds students' email addresses.
      Verified from a real client with the select bypassed: `'sweeper'`, a map
      and an int are all refused, and adding `position` to the permitted keys did
      not open the door it sits beside — a write pairing a valid position with
      somebody else's `linkedUid` is still rejected.

      The position lives on the squad, not on the match. Nothing here records
      what a player actually played on a given day, so a report groups by where
      they play now; snapshotting it into the match roster would look more
      careful and be worse, since that document is written when the lineup is set
      and most positions get filled in long afterwards.

      567 pure JS · 149 emulator · 985 Python.
- [x] **[Demo]** Compute the halftime-tier stats first (possession, shot map/xG,
      distance, sprint counts, live-tagged event counts) — all computed,
      published and drawn. Shot coordinates travel beside the counts rather than
      in the event list, which still drops positions for passes and carries for
      the reasons it always did; a half has a dozen shots and those are the ones
      worth placing.
- [x] **Sort the figures into the questions they answer** — the coach's match
      view was a flat grid of twenty-five boxes in the order they were written
      and the half-time page a flat column of bars, both built separately from
      the same document. Both now group by one shared list in
      `assets/report.js`: the match, possession, passing, attacking, defending,
      shape. Sharing it is the point — two pages quietly disagreeing about
      whether a switch of play is passing or attacking is the failure worth
      preventing in a project whose premise is that the half-time view and the
      full report describe the same match. Grouping is also what made room for
      four figures that had been published since they were computed and drawn
      nowhere: **territory by third, giveaways in your own third, passes by
      length and passes by direction**. The last two are shown as shares of what
      was attempted rather than as counts, because how direct a side was is the
      question the buckets exist to answer and 142 forward passes does not
      answer it
- [x] **[MVP] Passing networks** (2026-08-06) — the first thing on the coach's
      page that draws a *shape* rather than a total: which players the ball
      actually travelled between, and which pairs never connected at all. A
      table of pass counts cannot say that however many columns it has.

      Nodes sit at each player's **mean pass origin**, not their heatmap
      centroid. The centroid was available and easier, and it averages defensive
      shape and off-ball running into a diagram whose edges are passes — two
      questions, one picture, and no way for a reader to tell they had been
      mixed. That needed `start_m` published on each event; the reason it was
      originally dropped ("null without a calibration and nothing plots a pitch
      yet") had quietly stopped being true on both counts.

      Three refusals do most of the work, and the note under the diagram names
      every one. A pass by a figure **nobody has named** is counted and never
      drawn, because a line to an unnamed track looks exactly like a fact. An
      **incomplete** pass belongs in the passer's own count and joins no line —
      drawing it to whoever was nearest would invent the one thing the diagram
      exists to show. And a pair who exchanged a single pass is **not a
      connection**; a hairline between every pair of names is a mesh in which
      nothing stands out.

      Node area is proportional to passes played, which took two attempts. The
      obvious `MIN + (MAX − MIN) × √share` looks like the same thing and is
      proportional to nothing: with the floor added to the *radius*, a player
      with three times another's passes came out **1.85× the area** on the
      sample squad, so the picture flattened exactly the difference it exists to
      show. Clamped at the bottom instead, so it is exact above the floor.

      Previewable without footage: `samplePassEvents` is a 4-3-3 that built down
      its own left, with two unnamed passers and a realistic 81% completion rate
      so the caveats preview too. Kept as a separate export from
      `sampleCvSummary` — that fixture carries no events so the review tool and
      the shot log stay empty under the preview, since both write back. Drawing
      writes nothing, so this one is safe to hand over.
- [x] **[MVP] Pressing trends — how long the press lasted, not just whether
      there was one** (2026-08-07). PPDA has been computed and published as one
      number per team since 2026-08-02, and one number for a whole match answers
      the question nobody asks. A side that squeezed for twenty minutes and then
      sat off has the same match figure as one that pressed evenly throughout,
      and pressing is exhausting enough that which of those happened is most of
      the read. This is also the first video block on the coach's page that is
      about *time* rather than a total or a shape.

      `ppda` is now a thin wrapper over `count_pressing`, which both it and the
      new `pressing_segments` call. Two implementations of "outside their own
      40%" is exactly how a chart ends up disagreeing with the number printed
      directly above it, and that number is printed directly above it.

      **The counts travel with the ratio, because the ratio breaks at the bottom
      end.** A quarter-hour with thirty passes allowed and no challenge at all is
      the strongest non-press there is, and it has no PPDA — dividing by zero is
      undefined, so the one figure that would describe it best is the one that
      cannot exist. `PressingCount` carries both numbers so that spell still
      says something, and the page draws no bar for it in the warning colour
      with *never challenged* written where the bar would be. Below five
      challenges a block keeps its counts and loses its ratio: a floor for
      legibility, not a significance test, and even at five the ratio carries
      about ±45%. The note under the chart names both silences separately,
      because only one of them is news and they look identical.

      Fifteen-minute blocks, set by the denominator rather than by taste — a
      team makes roughly fifty defensive actions in the pressing zone across a
      match, so a quarter-hour holds about ten. Five-minute blocks would give a
      prettier chart of two or three apiece, which is a picture of noise. A
      window too short for two blocks returns None rather than one bar, and a
      remainder under half a block is folded into the one before it: three
      minutes of football drawn beside fifteen is two bars the eye compares
      directly and should not.

      **A longer bar is a worse press**, which runs backwards from everything
      else on the site. Plotting the reciprocal so bigger meant harder reads
      more naturally and would put a number on screen disagreeing with the PPDA
      row above it, so the direction is stated in words instead. Bars are
      measured from zero, and `pressingRead` only calls a fade when the last
      scored block costs 1.8× the first — set from the ±45% two such blocks move
      on chance alone, not from what looks like a slope.

      Timestamps stay in video seconds all the way to the browser, where the
      offset is; the half-time read quotes video minutes when nobody has synced
      the clock, since a wrong match minute reads as fact and a video minute
      reads as a position in the footage.
- [x] **Show the opposition's figures beside our own** (2026-08-07). The
      pipeline has run `team_stats` over both TEAM_A and TEAM_B since the
      function existed, and `build_report_json` has published both; the coach's
      report read `teams.team_a` and stopped. Twenty-five figures about the
      opposition, computed, published, and rendered nowhere — a possession of
      58% saying very little on its own and everything beside their 42%.

      Adding the second column meant deciding what a bar between two figures is
      allowed to claim, and there turned out to be four different claims sharing
      one list. `tally` drew all of them as a split.

      **SHARE** — a share of a continuous whole. Their possession IS the rest of
      ours, the boundary between them is the whole story, and there is no count
      behind it that could be too small. Possession, and nothing else on the
      page.

      **COUNT** — a share of some number of discrete events. Also a split: twelve
      shots to four really is three quarters of the shots. But three shots to one
      is *also* three quarters, and it is four shots in a match, so a count
      carries whether its lead is bigger than chance. Each event is a coin toss
      under a null of no difference, so the gap has standard deviation √n and the
      band is `|a − b| < 2√n`: 3–1 is inside it, 12–4 sits on the line, 30–10 is
      outside. Inside it the bar is drawn hollow and neither side is coloured —
      a green number is a verdict and that one has not earned it. Same reasoning
      as `xgCalibration` and `pressingRead`, for the same reason.

      **RATE** — each figure a percentage of its own denominator. 84% pass
      accuracy against 71% is not a 54/46 split of anything, so each runs 0–100
      on its own scale from a fixed centre. Drawn as a split, which is what the
      half-time page has been doing, any two accuracies look like a dead heat.

      **LEVEL** — a magnitude with no denominator at all: PPDA, and every shape
      figure in metres. Drawn against whichever side was larger. Splitting these
      would be the worst of the four available mistakes — a PPDA of 6.9 against
      14.8 reading as *32% of the pressing*, which is not a quantity.

      Two of those were found by looking at it rather than by reasoning: the
      rate bars came out one pixel wide, because a row's `usN` was the pipeline's
      fraction while its text was the formatted percentage. Every figure a row
      carries is now in the unit the row prints, so a bar cannot disagree with
      the number beside it.

      Each side's breakdowns are shares of **its own** total — dividing their
      forward passes by our attempts would be a number about nobody, and would
      make any side that passed less look like one that never went forward. A
      row survives on either side having a figure, because PPDA is null for a
      side that made no defensive actions and dropping the row would take the
      opposition's number with it.

      415 pure JS · 120 emulator · 765 Python.
- [x] **[MVP]** **The last of the post-game tactical catalog: phase-of-play**
      (2026-08-08). The shape of the answer was the open question, and settling
      it settled everything else:

      > `territory` says **where the ball was**. Phase-of-play says **what the
      > team was trying to do there, and whether it worked**.

      That is why neither of the two closest existing things was it. A clearance
      hoofed out of your own box and a move worked out from the goalkeeper are
      the same square metre and the same second of possession; territory cannot
      separate them and a coach cares about almost nothing else.

      So the unit is the **possession**, not the frame and not the event — which
      is the one structural decision here, and the rest follows from it. New
      `cv/sequences.py` splits the event log into spells, ending one when any of
      three things happen: the other team does something, a gap of more than
      five seconds opens (two touches half a minute apart are two spells with
      something unrecorded between them), or the ball goes dead. Only a tagged
      log knows the third, and a throw-in starts a new possession however
      cleanly it is taken. An event with no position is skipped rather than
      ending the spell — the homography failing on one frame is not the defence
      winning the ball.

      Each spell carries where it began, **the furthest third it reached** — not
      where it ended, because a move worked to the byline and pulled back to the
      penalty spot reached the final third — and how it finished. A shot beats
      everything: a move that got the shot away achieved what it was for. An
      incomplete last pass is `lost`; anything else is `stopped`, which is
      deliberately not the same thing, for the same reason `turnovers_by_third`
      counts only incomplete passes rather than every change of hands.

      Rolled up per team that makes a funnel, and a funnel is bars: of this
      side's own possessions, how many reached midfield, the final third, a
      shot. On the sample fixture we start 88 possessions to their 71 and get
      33% of them into the final third against their 44% — more of the ball,
      less done with it, which is a sentence a coach can act on in a way "47%
      possession in our own third" is not.

      Two denominators, and the difference between them is the point:

      - the funnel is out of **all** of that side's possessions, and counts one
        by how far it got — so a possession that *began* in the final third
        counts as having reached it. A side that wins the ball high is flattered
        by that, and the caption says so rather than leaving a reader to guess.
      - **"out from the back"** is out of the possessions that *started* there.
        56% against their 73%. That is the row that actually answers "can we
        play out", and the whole-funnel share cannot, because it is flattered by
        every possession that began in midfield already. A side that never won
        the ball in its own third gets `null`, not zero: they did not fail to
        play out from the back.

      Passing is split by the third it was played *from* — where the player was
      when they made the decision, the convention `turnovers_by_third` already
      uses. That is the number the whole feature exists for: 92% at the back,
      70% in midfield, 54% up front is a normal healthy side, and one overall
      figure of 72% hides all three. Every row is a `RATE`, so the four kinds of
      bar from the previous item carried the opposition column across for free.

      **One defect, and the browser found it rather than the tests.** 10
      possessions in 88 and 8 in 71 are different numbers that both print
      **"11%"** — and the row coloured one of them green, putting a verdict
      beside its own disproof. The tie is now decided on the printed text rather
      than the floats behind it, and the rule moved out of `ui.js` into
      `report.js` as `verdict()`, because whether to state an opinion is a
      judgement and not a layout, and it should not go untested for want of a
      DOM.

      436 pure JS · 120 emulator · 828 Python.
- [x] **Use the Phase 3 sub log to scope each player's stats to their actual
      minutes played** (2026-08-06). Every per-player card printed *Minutes 71*
      from the sub log directly beside *km covered 1.9* from the video, and
      those two have never had the same denominator: the first is the whole time
      they were on the pitch, the second is however much of it the tracker held
      on to, inside whatever window of footage was processed. Unlabelled and
      side by side they read as one claim, and the comparison a coach actually
      makes — who ran the most — was mostly a ranking of who the tracker
      followed. At 3.4 tracks per player that is not a small effect.

      `trackedCoverage` in `assets/report.js` intersects each player's stints
      with the processed window and divides the tracked minutes by the result.
      Intersecting is the part that has to be right: without it a three-minute
      clip would score every player against a ninety-minute match and report 3%
      coverage for everyone, which is a statement about the clip and not about
      them. Filmed and played are reported separately whenever they differ,
      because they are two different shortfalls and only one is the software's.

      **Nothing is scaled up.** A total measured over a third of someone's match
      is a third of a total, and tripling it would be inventing the other two
      thirds. The comparable figure is a rate, so the coach's roster column
      changed from **km to m/min** — the same measurement with minutes played
      and tracker coverage both divided out. That reordered the table
      immediately on test data: a player subbed at 25 minutes read 1.40 km
      against a starter's 5.20 and looked like she barely moved; as a rate both
      are 127 m/min. The hardest worker on the pitch, at 144, was a substitute
      the kilometre column ranked last.

      Rows resting on under 70% of a player's filmed minutes are marked, and the
      players concerned are **named** in the note under the table rather than
      counted — a dotted underline is not something a phone can hover over, and
      "four players are thin" only sends a coach hunting. A share above 1.15 is
      reported as what it is: two figures mapped to one player were on screen at
      once, which one player cannot be, so the mapping is double-counting and
      every total above it is inflated. Said outright rather than clamped to a
      tidy 100%, which would hide the only symptom.

      The join lives in JavaScript because Python never sees the sub log — these
      are the first two `cv*` fields with no twin in `cv/publish.py`, and
      `cvReportFields` takes the coverage as an optional second argument so a
      report published without one is exactly what it was before.
- [x] **[Stretch] Cross-match aggregation per player — as rates across a run of
      matches, not as one pile of totals** (2026-08-07). `seasonTotals` added the
      video-derived fields up, and for the headline counts that is right. For
      anything a reader would *compare* it is the wrong shape twice: it hides
      which matches it came from, and it treats a match tracked for six minutes
      as equal evidence to one tracked for seventy. Coverage has ranged that far
      and will keep ranging that far.

      `assets/season.js` (zero imports, so the pure suite covers it) turns a
      season into per-match rates and one season figure, **pooled** — the sum of
      the numerators over the sum of the denominators. Averaging the per-match
      rates instead gives a twelve-minute fragment the same say as a full match,
      and on this pipeline fragments are the common case. Pinned by a test where
      the two answers are 70 and 90.

      **Which measures survive partial coverage, and one that does not.** A rate
      is measured over exactly the span it is divided by, so less coverage makes
      it noisier and not wrong. A ratio of two counts from the same span — pass
      accuracy — is safest of all; both halves are missing the same minutes, and
      it is floored on attempts rather than on minutes for that reason. **Top
      speed is different**: a maximum over a partial observation is biased
      *down*, it does not average out, and a season best from thin coverage is a
      floor rather than a figure. That is named in the note, once, because a
      downward-bent number plotted beside three unbent ones reads as a player
      who is slowing down.

      Three refusals in the drawing. Every match keeps its slot including the
      ones nobody filmed, since closing the gaps would space the filmed matches
      evenly and imply they were evenly spaced in the season — a claim about
      time nobody measured. A line joins consecutive measured matches and
      **never crosses a gap**. And a match tracked below ten minutes is counted
      and named but not plotted, and is left out of the season figure too — a
      reference line the surrounding dots cannot explain is worse than a
      slightly narrower one.

      The axis does **not** start at zero, which reverses the rule the pressing
      bars and the passing nodes follow, and the reversal is the point: a bar's
      length is a quantity and a dot's height is a position. The two ends of the
      range are printed on every card as the price of it. Caught in the browser:
      `preserveAspectRatio="none"` — copied from the minutes chart, which draws
      rectangles — stretched every dot into an oval at phone width, 3.13 pixels
      per unit across against 1.94 down. Fixed by matching the container's
      aspect ratio to the viewBox instead of distorting the marks.

      Also caught in the browser and invisible to every test: `Element.append()`
      returns undefined where `appendChild()` returns the node, so the chained
      hover labels were setting `textContent` on nothing. The pure suite has no
      DOM and passed throughout.

## 14. Player Portal & Accounts
Superseded the "shared code + PIN" plan: Firebase Auth made real accounts cheaper
than the workaround, which matters given the data class.
- [x] **[Demo]** Google sign-in. Coaches are gated by a console-managed
      `coachAllowlist`; players are invited by email and claim their own roster slot,
      verified against the roster document's stored address
- [x] Per-player report view, scoped to that player's own stats
- [x] **[Demo]** Season history across matches (one collection-group query)
- [x] **Report delivery beyond the in-app view — the PDF half** (2026-08-09).
      A print stylesheet and a *Print / save as PDF* button on the coach's match
      view and on a player's own report. No library, no server, no new
      dependency: the browser already makes PDFs and the only thing missing was
      a page worth handing over.

      Three things change on paper and each is a claim rather than a taste.

      1. **The palette inverts.** This design is a near-black pushed toward
         turf; printed as-is it either empties a cartridge or comes out a grey
         rectangle with numbers hidden in it. Every colour is a variable, so the
         inversion is one block — and the pitch drawings and heatmaps follow for
         free, because they were already drawn in `currentColor`.
      2. **The chrome goes.** Nothing on a sheet of paper can be signed out of,
         filtered, scrubbed or played, and a printed button is a small lie about
         what the reader can do. Whole panels go with it: hiding a control but
         keeping the prose explaining that control — *"paste a link to the
         footage"* — is worse than keeping both, which is what the first browser
         check showed and what `.no-print` now fixes.
      3. **The caveats stay, and stay attached.** The one that matters. On
         screen the quality banner sits above the figures and a reader who
         forgets it can scroll back; on paper they cannot, and a page of
         confident-looking numbers with *"measured from video, treat as
         estimates"* two sheets earlier is a worse artifact than no printout at
         all. So the banner is never hidden, and it is glued to what follows it
         with `break-after: avoid`. Nothing readable is allowed to split across
         a fold either — a stat cut in half is not half a stat, it is a misprint.

      **Every sheet says what it is.** `printStamp` puts the subject, the
      fixture, and the date it was *printed* along the top edge. A page that has
      left the app has left everything that made it readable — the navigation
      saying whose account it came from, the tab title saying which match — and
      three months later it is a piece of paper with a teenager's name and some
      numbers on it. The printing date is deliberately not the match date: a
      sheet re-printed after a coach corrected the review says something
      different from one printed on the night, and nothing else tells them
      apart.

      The stamp also explains the confidence marks, which the second browser
      check turned up: on screen `···` carries its meaning in a title
      attribute, and on paper it is three bare dots beside a number — a
      reference to a footnote that is not there.

      **Email is not built and this is not a step toward it.** It needs a server
      that can send mail; the whole app is static files on GitHub Pages plus
      Firestore rules, and adding Cloud Functions for it means a billing account
      and a new attack surface for a system holding students' data. A coach who
      wants to send a report can already attach the PDF. Worth revisiting only
      if somebody asks for it.

      484 pure JS · 120 emulator.
- [ ] **[Stretch]** App Check + browser-key referrer restriction before this is public
- [x] **Removing a player actually removes them** (2026-08-10). This line used
      to read *"the technical controls are in place, the consent conversation is
      not a code change"*. Half of that was true. The **rules** allowed a coach
      to delete every document naming a student and always had. **Nothing asked
      them to.**

      `removePlayer` deleted exactly one document — `teams/{t}/players/{p}`, the
      squad entry — and the coach was shown *"Player removed"*. What stayed:
      their name, shirt number and stints in the roster of **every match they
      had played**; their whole published report, with minutes, distance,
      heatmap and shot map, still readable through the portal by their own
      account; their email address as an invite key; and their id in
      `cvMapping.byCluster`, which is the thing that ties a person to a
      photograph cut out of the footage. A guardian asking for their child's
      data to be removed would have been told it had been.

      **"Remove" was two intentions wearing one word**, and the button served
      neither. *They left the team* has to keep the match reports — a report is
      an account of a match that happened, and deleting it changes the team's
      own results. *A guardian asked* has to keep nothing. So the row now opens
      a choice, phrased as the consequence rather than the action:

      - **They left the team** — `active: false`, which had been written on
        every player document since the first one and which **nothing had ever
        read**. Now the lineup picker skips them, the roster greys them and
        sorts them to the bottom, and one press puts them back.
      - **Erase everything** — every document above, across every match, then
        the invite, then the squad entry last, because it is the only place the
        coach could find them again if something failed part way.

      Before confirming, the coach is shown **what is actually there**, read
      rather than assumed: how many matches hold their name, how many published
      reports go, how many tracked figures stop pointing at them. A confirmation
      quoting a number nobody checked is worse than one quoting none. Typed
      confirmation rather than a click — every other destructive control here can
      be undone by entering again what was lost, and this one cannot.

      **What is deliberately kept, and said out loud.** The match log stays. It
      records substitutions by player id and never by name, so once the named
      documents are gone it holds an identifier that resolves to nobody — and it
      is also the arithmetic behind *every other player's* minutes. Deleting the
      entry that put someone on would silently take time off whoever came off
      for them. Pseudonymous and load-bearing is a good reason to leave
      something alone; it is not a good reason to let a coach believe it went.

      **Two bugs the tests caught, both in the erase itself.**

      `batch.set(ref, {...}, { merge: true })` on the cluster mapping *looked*
      right and did nothing: Firestore merges a map field key by key, so writing
      a smaller `byCluster` leaves every removed key exactly where it was. The
      erase would have reported success with the figures — and the photographs —
      still pointing at the student. It is a whole-document write now, which the
      rules already constrain to exactly three fields.

      And the erase now **reads everything back before saying it worked**. The
      first version of the emulator test passed intermittently in 17ms, which is
      too fast to have done the work: the match list had come back empty, the
      per-match loop had nothing to do, and both the code and the test called
      that a success. A partial erase reported as a complete one is the single
      worst outcome this feature has, because nobody will ever check again.

      The consent conversation is still not a code change, and it is still
      required. What has changed is that the answer to *"can you delete
      everything you hold about my child?"* is now yes, demonstrably —
      `tests/flow.test.js` erases a player and then reads the documents back as
      the **player's own account**, through the collection-group query their
      portal uses.

      **And a pre-existing bug the browser found that no test would have.**
      `emailShape` was applied to the roster document as well as to the invite
      key, and it rejects a blank — so a coach could not add a player without an
      email address **at all**, nor rename, renumber or deactivate one. The
      roster says out loud *"No email yet — they cannot see their report without
      one"*, so this was a supported case that had never worked. Every emulator
      test happened to use a player who had an address; pressing the button did
      not. Split into `optionalEmail` for the roster and `emailShape` still
      guarding the invite key, where a blank really is meaningless.

      551 pure JS · 132 emulator.
- [ ] Parental/guardian awareness before real students' data goes in — the
      consent conversation itself, which is not a code change. The technical
      half is done and demonstrable (see above); what remains is asking

## 15. Frontend / Dashboard
- [x] **The match report is a dashboard on a monitor, not a scroll**
      (2026-08-12). Fifteen blocks stacked one to a row came to **8,723px — 6.9
      screens** on a laptop, for a page that is read at a desk the evening after
      a game far more often than on a touchline. Measured on the same match with
      the same data, the two-column layout is **5,274px, 4.2 screens**: the
      report lost two fifths of its length without losing a block.

      **A grid laid over the existing markup, not a second set of it.** There is
      one report in the DOM in one order; the desktop arrangement is a
      stylesheet's opinion about it. So a block that only appears when a match
      was filmed needs no layout code, print needs none, and the phone keeps the
      stack it should have — a phone has one column and scrolling is how you use
      it. Blocks are full width by default and opt into half (`.col-half`),
      which is the safe way round: too wide is roomy, too narrow breaks its own
      contents.

      **The rail is built from the blocks that are actually there.** Half of
      them exist only for a filmed match, and a jump list offering sections that
      are not on the page is a menu of dead ends. It carries the two counts
      worth having off-screen — figures still unnamed, events still unchecked —
      because those decide whether the video columns mean anything and they live
      eight screens down. Rebuilt only when the set of sections changes, not on
      every save: the badges tick over constantly while a coach names figures,
      and throwing the buttons away each time would take the keyboard focus with
      it, which is the bug the "same figure" strip already had to fix once.

      **More than one rail entry lights at a time, and that is the honest
      answer.** Two blocks share a row, so at most heights the section you are
      reading really is two sections. A band — everything overlapping the top
      45% — lit four of nine, which is a highlight that has stopped pointing at
      anything; a line across the upper third can only be crossed once per
      column, so it says at most two.

      **The layout found a bug by reproducing an old one.** The cluster, review
      and shot rows all had a narrow layout already, keyed to
      `@media (max-width: 640px)`, with comments about "the mistake the cluster
      rows made at 375px". Halving the column reproduced that mistake at
      1440px — a 420px row on a screen nowhere near 640px, stacking its label
      one letter per line. The rule those rows want has never been about the
      screen; it is **"this row is narrow"**, and they are now
      `@container matchblock (max-width: 640px)` over a block that measures
      itself. One rule serves a phone and a half-width column instead of two
      that have to be kept in step.

      **The report opts out of the reading measure.** `--shell` is 1120px
      because that is tuned on prose, and this page is not prose: the widest
      thing on it is an eleven-column table needing 897px. Halving the shell
      left it 33px short and put a horizontal scrollbar under the one table a
      coach reads most, while 450px of monitor sat unused either side.
      `#view-match` now takes `min(100vw - 64px, 1500px)` — 64px of inset, not
      48, because `100vw` counts a scrollbar the viewport does not. The top bar
      follows it via `body:has(#view-match:not(.hidden))`, scoped to the view
      that is actually wide: a header inset 80px from the content beneath it
      reads as a mistake, and on the squad and roster screens the bar still
      lines up with the 1120px column that is under it there.

      Two smaller things the width paid for. The player table's eleven columns
      now carry a band naming where each number came from — **Tagged during the
      match** against **Estimated from video** — because the rule between them
      already said there was a difference and not what it was. And minutes are
      drawn as well as written: a bar behind each figure, as a share of the
      match's own length rather than of the longest shift, so a squad nobody
      rotated does not draw itself as though somebody had. The table is sorted
      by goals and assists, so nothing in the row order otherwise says who
      actually played.

      Verified against the emulator at 375, 768, 1280 and 1440: the rail hides
      below 1180px, every container query fires on the phone exactly as the
      media queries did, no page scrolls horizontally at any width, the rail
      rebuilds when the sample preview turns four blocks on, and a rail jump
      lands on its 20px scroll-margin.

      554 pure JS · 139 emulator · 985 Python.
- [x] **The season views, and a width that was being handed to the wrong page**
      (2026-08-12). The same grid, now on all four report screens, and the id it
      was keyed to turned out to be shared.

      `#view-match` exists in **`coach/index.html` and `player/index.html`**.
      The width rule from the entry above was written against that id, so the
      student's own match report — one column of blocks, no grid — was being
      stretched to 1500px and looked worse for it. A width that only makes sense
      with columns under it has to be **asked for**, so it is now a
      `.wide-report` class each view opts into, and the classes are
      `.report-layout` / `.report-body` rather than `.match-*` because three of
      the four views are not a match.

      A coach's view of a player's season went **3,056px to 1,992px — 3.4 screens
      to 2.2**; the student's own season, which is the same season, went 3.0 to
      1.8. Minutes and form sit side by side on both. **No rail on either**: four
      blocks is not fifteen, and a jump list of four is more chrome than
      navigation.

      The biggest single saving was not the columns. **"Match by match" was eight
      cards stacked down 1,376px** — eight rows of one card and a great deal of
      nothing beside each, 750px of page. Two abreast it is 329px, and read
      left-to-right in pairs it is still a season in order. Scoped to a
      full-width block inside a report grid: a card list in a half column has no
      room to split, and the roster and staff lists elsewhere on the coach page
      are not seasons.

      On the student's match view the two pitch pictures pair — a heatmap caps
      itself at 620px and a shot map at 380px, so neither was ever using a
      full-width row. The saving there is small because the heatmap sets the row
      height either way; the reason to do it is that **two pitches of the same
      match belong beside each other**, which is where you would hold them to
      compare.

      Verified against the emulator with a seeded eight-match season, on the
      coach page and the player portal, at 375, 768 and 1440: single column and
      a flex card list below 1180px, no page scrolls horizontally at any width,
      and the top bar tracks the widened view on both pages.

      554 pure JS · 139 emulator · 985 Python.
- [x] **[Demo]** Coach halftime view: sideline/mobile-friendly, high-signal,
      minimal reading — built from the Stats Catalog's halftime section. The
      catalog's "plain-language flags over raw tables" is `report.cvReads`:
      pinned-back territory, shape drift, giveaways in your own third, and
      chances made against chances taken, as sentences in the decisions block
      rather than as more rows. Styled deliberately quieter than a card
      somebody was actually shown. Every threshold behind them is a guess, set
      high on purpose — a flag that fires every match stops being read and takes
      the ones that matter with it.
- [x] Match video (YouTube or a direct file link) on the coach match view and
      the half-time page, not just the player portal — goals, cards and subs
      marked on a shared tick strip (`assets/match-video.js`), one module
      instead of three copies of the embed-or-link-or-nothing decision. A link
      saved after reports were already published now pushes onto the existing
      `playerReports` documents rather than waiting for a re-publish.
- [x] Shot maps and heatmaps, on both the coach's match view and a player's own
      report, each shot clickable to seek the video. Both halves of a shot map
      are mirrored to attack right in `report_json` rather than in a renderer, so
      a second-half shot cannot be plotted at the wrong end by a page that forgot.
- [x] A sample run the pages can be checked against before there is footage
      (`assets/sample-report.js`). Every video-derived block hides itself when
      there is nothing to draw, which is right and which also meant that until
      today most of what has been built was invisible and a mis-wiring between
      the pipeline and a renderer could not be seen at all. The fixture is
      shaped key-for-key like `summary_payload` and `player_report_fields` and
      goes through the same renderers — a preview with a path of its own would
      only prove that path works — and `tests/test_sample_report.py` fails when
      Python starts publishing a field it does not carry. It found two on its
      first run.

      Deliberately not flawless: 83% ball coverage, 3.4 tracks per player, two
      officials it could not rule out, one goal the records disagree about. A
      preview of a perfect run would hide every caveat these pages exist to
      show and set an expectation the footage will not meet. The one generous
      figure is the 0.42 m calibration, so the shot map previews its sized
      form; that is a stated choice rather than an accident.
- [x] **The sample's xG comes out of the model** (2026-08-06). It was
      hand-picked before — figures chosen to make the shot map look about right,
      which is a fixture whose numbers are decoration and which teaches whoever
      reads it to check the layout instead of the output. Each shot now has a
      freeze frame behind it — where the keeper stood, who was in the lane —
      recorded in `tests/test_sample_xg.py`, which re-runs the real model and
      fails if the fixture drifts from it. That makes it a golden file for the
      one number here nobody can sanity-check by eye: the same ten frames were
      worth 4.15 xG under `xg_model6`, 1.83 under `xg_model7` and 1.23 under
      `xg_model8`, and
      before this file nothing in the repo would have noticed a swap that size.

      Two of the shots are also presets in the sandbox, so the number on the
      preview can be reproduced by clicking its name — checked in the browser:
      0.098 and 0.479 in both places. One shot is 0.0 — a block from 29 metres
      with two defenders in front of it, which is a calibrated model saying
      *nothing*, not a model that failed to run. Absent is not zero, and the
      preview now carries the zero.
- [x] **Presets and a feature readout in the xG sandbox.** Eight scenarios that
      place all ten players and set the toggles, because dragging dots until
      they resemble a chance you had in mind is slow and never quite the shot
      you meant. Alongside them, the eleven numbers the model actually receives,
      in the model's own units — which is the panel that would have made both of
      this page's coordinate bugs obvious on the day they were written, while
      the only thing on screen was the answer.

      What they read now, which is the first time these have been worth
      quoting: tap-in 0.79, one on one 0.34, edge of the box 0.08, twenty-eight
      metres 0.02. The penalty is 0.26 and should be about 0.76 — the model's
      one visible blind spot, because almost every penalty it trained on had no
      freeze frame, so it has barely seen a keeper drawn on the line from twelve
      yards. The button says so.

      Opt-in and offered only where there is no real run to confuse it with —
      never near the cluster picker or the review tool, both of which write
      back, and a confirm tapped against an invented event id would put a
      decision about nothing into a real document.
- [x] **[MVP] Coach tactical dashboard, player portal view (Phase 14), event
      timeline synced to video** (2026-08-09). Two of those three shipped long
      ago — the dashboard is the match view with its shot map, pass network,
      pressing trend and territory split, and the portal is Phase 14. This line
      stayed open on the third clause, and on one word in it: **synced**.

      The sync was one-way. The page could tell the video where to go; the video
      never told the page anything back. That is not a missing polish item, it
      is the thing that made a whole panel of the review tool produce wrong
      data, and the apology was written into the interface: *"the time box does
      not follow the video — YouTube will not tell a page where it is — so read
      it off the player and type it in."*

      **It will. It waits to be asked, and nobody had asked.** The embed's API
      is postMessage in both directions: send `{event: 'listening'}` and it
      starts sending `infoDelivery` frames carrying `currentTime`, about four a
      second while playing. Measured in the browser against a real embed — the
      handshake answers with `initialDelivery` at 0.0 and then streams
      42.08 → 42.36 → 42.61 → … once it is running. This is what the official
      IFrame API script does underneath; it is done directly rather than by
      loading that script, because the only thing wanted out of it is one
      number and the script is a third-party dependency on every page that shows
      a video. A `<video>` element needs none of this and reports `timeupdate`.

      **The wrong data it was producing.** "The video missed something at ▁▁"
      is stored as a *match clock* reading, and the number a coach reads off the
      player is a position in the *footage*. Those are the same number only when
      kick-off is the first frame, which it never is — the field right below it
      exists to say so. So every miss recorded by a careful coach reading the
      player honestly was filed at the wrong moment, off by the offset, and
      recall was being measured against it. There is now a **Use the video's
      time** button that does the conversion, and the conversion is also shown
      as a sentence, because a coach who can see that 20:07 of footage is 18:07
      of football can tell at a glance whether the offset above is right — and a
      wrong offset is otherwise invisible until every marker lands in the
      warm-up.

      It goes through `matchClockMap`, so the break is handled: inside half-time
      the button **refuses** rather than filling in the frozen reading. Every
      second of the interval reads as the same second, and a miss filed there
      would be filed at a moment the match was not being played. Verified across
      all three periods on a seeded match — 00:44 of footage → 00:44 first half,
      01:21 → *half-time*, 02:31 → 01:51 second half.

      **What the sync looks like.** A playhead on every strip, on all three
      pages, since they all go through `renderMatchVideo`. The mark the video is
      inside lights up, as does its row in the list underneath — `nowIndex` in
      `timeline.js`, pure and tested, with a 25-second window because a coach
      seeking to a goal lands before the ball crosses the line and then watches
      the celebration, and nothing before a mark ever counts, because lighting
      up a moment that has not happened gives it away.

      And the Timeline block, which was a dead list of text, is now every tagged
      entry as a button that seeks. The strip above it is deliberately thin —
      goals, cards and subs, because eighty ticks is a texture rather than a set
      of things to jump to — so the restarts and the fouls had nowhere to be
      tappable. Now they do, and the entry the video is inside is picked out of
      the seven.

      One property worth naming because it fell out rather than being built:
      **the playhead freezes during half-time**, at the second the first half
      ended on, and starts moving again at the restart. It is drawn on the match
      clock, and the match clock stopped.

      Two players sit on the coach's page at once — the match video and the
      review tool's own — so a message is only ours if `event.source` is our own
      frame. An origin check is not enough, and without the source check the
      match strip would follow the review player. Checked: seeking one moved one
      playhead and left the other at 0%.

      516 pure JS · 120 emulator.
- [ ] **[Stretch]** Live tracking overlay on video (bounding boxes, IDs, mini-map)

## 16. Validation & Demo Logistics
- [ ] **[Demo]** Ground-truth comparison using the Phase 11 tool on a short labeled clip before trusting a full game
- [x] **[Demo]** Camera hardware/placement plan for the actual high school field
      — `FOOTAGE_DAY.md`, written to be read at the field rather than at a desk:
      framing rules with the measured cost of getting them wrong, fixed not
      auto-tracking, native export not a screen recording, and elevation
      preferred. What it cannot settle from here is which camera the school
      actually runs and where it can be put — that is a conversation, not a
      commit.
- [x] Brief whoever runs the tablet, in writing, before game day —
      `FOOTAGE_DAY.md` §3. The kick-off marker, stoppage **and restart** pairs
      (an untagged restart caps a dead span and silently deletes real football),
      and substitutions, in that order of importance.
- [x] An intake order with stop conditions — `FOOTAGE_DAY.md` §5. `spike_detect`
      first, because if ball coverage is near zero nothing downstream is worth
      running.
- [ ] Lighting/weather robustness check (outdoor field, not a broadcast studio)
- [ ] Identify and briefly train whoever will run the Phase 3 tablet during the actual demo game — the live data is only as good as the person entering it. The briefing is written (`FOOTAGE_DAY.md` §3); the person is not identified.
- [ ] **[Demo]** Dry run on real footage from the old team well before the target test date — also the first real test of the ball-detection spike and the homography/attacking-direction logic
- [ ] **[MVP]** Validate the halftime path end-to-end under a real clock — can
      it actually finish in time. The pipeline half of this is instrumented and
      answerable without footage (`speed_report`, Phase 6); what is still
      untested is the whole path under a stopwatch — footage off the camera,
      pipeline, publish, coach opens the half-time page — on real footage with a
      real person doing each step

---

## Suggested build order for a 1-month demo

**Done:** ~~ball detection feasibility spike~~, ~~minimal backend (2)~~, ~~live tablet
tool (3)~~, ~~player + ball detection (5)~~. The three highest-uncertainty items are
behind us, and the answer on detection is "yes, given the right camera framing."

**Now:** calibration, including the halftime attacking-direction flip (4) → tracking
(6) → team color split only, no OCR
(7) → coordinates/metrics (8) → possession/game state (9) → basic auto-candidates:
pass + shot + goal + stoppage flagging (10) → write the feature-parity test and set up
ground-truth clips now (Testing Strategy) → review & annotation tool (11), pre-populated
from the live tags → xG feature bridge (12), including the noisy-input validation check
→ minimal stats covering just the halftime catalog first (13) → a bare-bones halftime
coach view (15) to prove the whole loop end-to-end on the dry-run footage. Only after
that loop works end-to-end on real footage — not before — layer on true
live/incremental halftime timing, the post-game portal (14), and the tactical
dashboard (15). Treat live overlay, season aggregation, per-player login, jersey OCR,
and auto stoppage-type classification as post-demo work.
