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
- [ ] **The double model pass.** Detection runs over the window, then the tracker
  runs the model again over the same frames — 7.6s of the 19.9s. Collapsing them
  means running the tracker at the ball's much lower confidence threshold, which
  would flood it with junk tracks, so it is not the free win it looks like.

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
- [ ] **[MVP]** Process first-half footage incrementally during play so the halftime report can be ready close to the actual break
- [ ] Frame sampling strategy — every frame, or subsample (e.g. 10-15 fps) to cut compute cost
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
- [ ] **Decide a video retention policy.** Storage volume is not a concern for
      the tracking data itself: at 10fps with 23 objects a full match is roughly
      10-40MB of positions, trivial for a database, including a whole season.
      Video is the heavy cost — a 90-minute 1080p match is several GB. The
      likely answer is to keep full match video only through the post-game
      review window and retain long-term just the clips tied to confirmed
      events. Nothing hosts video today, so this is a decision waiting on a need
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
- [ ] **[MVP]** Offline-first entry with sync-when-available — matters once you're past a single-laptop-on-site setup
- [ ] Decide role split: one app covering both subs + events, or two simpler single-purpose roles/devices — worth testing both at the demo dry run
- [ ] Basic weatherproofing for an outdoor tablet (case, screen usable with sun glare)
- [ ] Live-tagged data feeds directly into the halftime report and pre-populates the Phase 11 review tool as a head start
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

- [ ] Recalibration handling if the camera is bumped or zoom changes mid-game
- [ ] **[Stretch]** Automatic pitch-line detection
- [ ] Strategy for when the camera doesn't see the whole pitch, tied to the coverage risk in the Reality Check

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
- [ ] For *long* occlusions (goalmouth scrambles), fall back to the live-tagged
      event log rather than interpolating across them. Not built: today a long
      gap is left as a gap and reported as `no_ball_s` in the quality block,
      which is honest but throws away a tag log that knows what happened
- [ ] Re-identification after a player is occluded or leaves frame briefly —
      **partly done**. `cv/identity.py` rejoins fragments seconds apart on kit
      colour and timing; anyone who went off and came back later stays split,
      and the picker's many-to-one mapping is what covers that case instead
- [ ] Track smoothing (Kalman filter or similar) before computing speed/distance
- [ ] **[MVP]** Tracking fast enough to keep up during live first-half play, not just accurately in a batch job

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
- [ ] Acceleration

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
- [ ] Half/period and clock tracking, driven by the Phase 3 period-boundary taps

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
- [x] **[Demo]** Timeline UI showing CV-candidate events, synced to video playback (`coach/coach.js`'s review section). Live-tagged events still live on the match page's own separate timeline; a single merged strip showing both is still open.
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
- [ ] Merge-tracks control for split IDs — **largely already possible**: the
      picker is many-to-one and `cvStatsByPlayer` sums across every cluster
      mapped to the same player, so merging two fragments is done by naming
      them both. What is missing is only the convenience of saying so once
      rather than twice, and the count of fragments a player was assembled from
      is already reported as the caveat it is
- [ ] Save finalized data as the source of truth for stats, profiles, xG logging, and the player portal
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
- [ ] `Player` domain model beyond the current UI stub in `xg-sandbox/geometry.js`: identity, team, jersey number, role, per-match stat accumulator
- [ ] `Team` domain model: roster, formation, aggregate stats
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
- [ ] **[MVP]** The rest of the post-game tactical catalog: phase-of-play and
      pressing trends. PPDA already exists in `cv/events.py` and is published;
      neither is drawn anywhere yet
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
- [ ] **[Stretch]** Cross-match / season aggregation per player

## 14. Player Portal & Accounts
Superseded the "shared code + PIN" plan: Firebase Auth made real accounts cheaper
than the workaround, which matters given the data class.
- [x] **[Demo]** Google sign-in. Coaches are gated by a console-managed
      `coachAllowlist`; players are invited by email and claim their own roster slot,
      verified against the roster document's stored address
- [x] Per-player report view, scoped to that player's own stats
- [x] **[Demo]** Season history across matches (one collection-group query)
- [ ] Report delivery beyond the in-app view (email/PDF)
- [ ] **[Stretch]** App Check + browser-key referrer restriction before this is public
- [ ] Parental/guardian awareness before real students' data goes in — the technical
      controls are in place, the consent conversation is not a code change

## 15. Frontend / Dashboard
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
- [ ] **[MVP]** Coach tactical dashboard, player portal view (Phase 14), event timeline synced to video (shared with the Phase 11 review tool)
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
- [ ] **[MVP]** Validate the halftime path end-to-end under a real clock — can it actually finish in time

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
