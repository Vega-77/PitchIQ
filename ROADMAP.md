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

## Current Status (2026-08-17)

**Built and verified:**
- `cv/` — reusable detection package + `spike_detect` CLI (Phase 5 spike, done)
- **Firebase backend** — Firestore + Google Auth, replacing the local FastAPI server.
  `firestore.rules` is the entire security boundary; 145 emulator tests pass via
  `npm test` (Phase 2 + 14, done for [Demo])
- **`index.html` / `coach/` / `player/`** — landing, coach dashboard, player portal
  (Phase 15, done for [Demo])
- `live-tagging/` — the tablet tool, now on Firestore with offline queueing (Phase 3)
- `xg-sandbox/` — the manual xG sandbox, moved off the site root
- `halftime/` — the three-minute touchline read, from tagged data alone (Phase 15)

**The live-tagging tool was dead from 2026-08-07 to 2026-08-15** and is
fixed. `init()` called `updateOnlineIndicator`, which the commit that
introduced `watchSync` had deleted along with its two window listeners while
leaving the call behind. A `ReferenceError` on the second statement of the
entry point meant no button was ever wired up: the tablet showed a sign-in
screen to a signed-in coach and could do nothing else. Eight days, on the tool
every other number in this system is derived from, and it was found by opening
the page. See Phase 3 for what that says about the test suites.

**That gap is closed** (2026-08-16). `tests/smoke.test.js` loads all seven
pages under `node --test` against an in-memory Firestore and a DOM shim in
`tests/` — no new dependency, no headless browser. Both bugs that walked
through the gap were reinstated to check it fails on them. See Testing
Strategy §9 for what it covers and what it still cannot see.

**What it has found since** (2026-08-17): a verdict taken back that still
counted as one, three interactive surfaces the fixture had never been able to
open, a student's own report showing one fragment of a match the coach's screen
showed whole, and two fields that existed only to go wrong — one written by the
pipeline and readable by nobody, one misspelt in the fixture and therefore
invisible. They are in §9 and Phase 15.

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

Everything reachable without it has now been built, in three passes. First:
non-player exclusion, in-play/dead-ball splitting, the narrowed cluster picker,
the review tool with recall, and match video on every report. Then xG actually
being computed rather than merely computable, the tag-log/CV reconciliation,
precision and recall out of the review tool, and a regression harness waiting
for its first baseline (see the two "built ahead of the footage" sections
below). Then the whole of the stats catalog — passing networks, the pressing
trend, phase of play, the opponent's figures beside our own, season form as
rates, the shot ledger and the xG calibration check, playing positions, camera
coverage, and what the football looked like either side of each substitution —
plus the desktop layout the report is actually read on.

The honest summary of the second pass: most of what it did was **connect things
that were already written and called by nothing**. That is worth saying plainly,
because it means the remaining gap is not a shortage of code. The third pass
built new things, and every one of them is waiting on the same input to find out
whether its numbers mean anything.

**Every remaining unticked item in this document is gated on one of three
things: footage, hardware, or people.** There is no code left that can be
written without at least one of them.

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
4. ~~Untested rather than broken~~ — was "the xG model has never been fed
   CV-derived features, and the pieces have never been run end to end as one
   pipeline." Neither half of that is true any more, and the first half had
   been false for sixteen days before anyone reread the sentence. The bridge
   was wired in on 2026-08-02: `cv/pipeline.py:73` imports `xg_for_shots`,
   `:789` calls it on CV-derived shot positions and `:793` attaches the result,
   and `tests/test_xg_pipeline.py` has run twelve tests from `derive_events`
   through the real ONNX model onto team totals ever since. The second half
   stayed true longer and for a structural reason: `analyse_match` built its
   own detector, so calling it at all meant installing ultralytics and torch,
   which `requirements-test.txt` deliberately leaves out — the one function
   every published number comes out of was the one part of the pipeline
   nothing could test. `tests/test_pipeline_end_to_end.py` closes that
   (2026-08-18): 29 tests, 2.7 seconds, no GPU and no footage.

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
  **[MVP]** hardening step once the concept is proven. **This now has an
  instrument rather than an opinion** — see Phase 6's throughput work, which
  built `cv/timing.py` and `speed_report`, and which established that a clip
  does not scale onto a half by multiplication: the same six seconds of
  synthetic footage read **1.48x cold and 0.33x warm**, a 4.5x spread that
  decides the verdict and none of it about the football. Read those figures as
  a floor on a floor — YOLO detects nothing at all in that clip, so tracking,
  identity, touches and events are all doing no work. What is settled is that
  the question can be answered on the machine that will be at the field,
  before the day rather than after it.
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
  added to the training features is a contained fix. **Measured 2026-08-06** —
  400 trials over five spots averaging 0.188 xG: 0.25 m of position noise
  shifts a shot's xG by 0.019 on average, 0.50 m by 0.030, 1.00 m by 0.043,
  2.00 m by 0.062, 4.00 m by 0.107. Half a metre is the error `calibrate/`
  accepts as good, and it is worth about a sixth of a typical chance. The full
  table, including the p95 and max columns that matter more than the means,
  is in Phase 12.

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

**CI runs all four suites on every push** (`.github/workflows/tests.yml`), which
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
4. **Confidence scores travel with every detection/track/event.** They do, and every
   review row carries its mark. This line used to go on to say the review tool "sorts by
   lowest confidence first, so a human's limited review time goes to what's likely wrong
   instead of skimming everything uniformly" — **it never did**. `reviewFeed` has been
   chronological since it existed, and nobody checked.

   Chronological is not a bug, which is why the fix was a choice rather than a
   correction (2026-08-13). Every row seeks the video, so match order is one forward
   scrub through a half and doubt order is a jump across ninety minutes per verdict —
   a cost the original claim never accounted for. The review block now has a **Work
   through** control: *in match order* (the default) or *least sure first*.

   And the second half of the original claim needed saying out loud rather than
   assuming. Reviewing the least sure first is the fastest way to find what the
   detector gets wrong, and it makes the reviewed set **deliberately the hard cases** —
   so precision measured over it is a floor, not an average. `orderCaveat` says exactly
   that under the scorecard, and only while that order is selected. Without it the tool
   would have reported a worse number than the truth and called it the truth.
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
9. **Load the pages.** Not on this list until 2026-08-16, because nothing here
   thought of the user interface as a thing that could be tested at all. See
   below — it was where two bugs in two days came from.

**The unreliable suite, settled 2026-08-17.** `tests/flow.test.js`'s *"erasing a
player › leaves nothing with their name in it"* had failed roughly half of all
emulator runs since 15 August with
`{"code":499,"message":"call already cancelled...","status":"CANCELLED"}`. This
section used to say the cause was one of two things — the emulator dropping a
connection, or a real race in `erasePlayer`'s per-match `writeBatch` loop — and
that those wanted opposite fixes. It is the first, and the erase loop is
exonerated.

*What the loop showed.* Three things had to be true at once, which is why it
looked random:

| arm | conditions | failed |
|---|---|---|
| A | erase suite alone, warm emulator | **0 / 30** |
| B | whole file, warm emulator | **0 / 15** |
| D | whole file, **cold** emulator | **4 / 12** |
| E | cold emulator, offline-queue suite skipped | **0 / 12** |

Arm A is 210 `clearFirestore` calls and every assertion in the erase path,
thirty times over, without one failure. In a failing run the 499 arrives at
**~12 ms**, out of the `beforeEach` at `tests/flow.test.js:60`, before the test
body executes a single line — and the six sibling tests in that same suite pass
immediately afterwards. So the roadmap's fear, that a cancelled call part-way
through leaves whole matches cleared and whole matches not, is not what was
happening. Nothing was half-erased; nothing was erased at all yet.

*The trigger* is the offline-queue suite above it — the only one in the file that
opens long-lived `Listen` streams and then pulls the network out from under them
with `disableNetwork`. `clearFirestore` is an HTTP DELETE whose handler tears
down live streams, and on a **cold** emulator everything is slow enough for the
next suite's clear to land inside that teardown. A warm emulator hid it across
45 runs, which is exactly why every previous attempt to reproduce it failed.

*Two fixes that did not work*, recorded because both looked obviously right.
`terminate()` on the offline suite's clients throws `firestore._delete is not a
function` — the instance `@firebase/rules-unit-testing` returns is a wrapper
without it. `disableNetwork()` in an `after` hook ran clean syntactically and
left the failure rate where it was (10/15 on a cold emulator). Neither could
have worked: no client-side teardown closes a stream the *server* is still
closing.

*The fix* is a retry on `clearFirestore`, and only on a cancelled call — 15/15
clean on cold emulators, against a contemporaneous unfixed rate of 10/15.

**The old instruction here said "do not add a retry to this one", and it was
right about the retry it meant.** Retrying *the test* would still be wrong. But
its stated reason — that a retry would convert a visible flake into an invisible
partial erase — rested on the erase loop being a suspect, and it no longer is.
The retry added sits on the clear, which runs before the test touches anything,
is idempotent, and cannot conceal a half-finished erase because no erase has
happened yet. It catches a cancelled call and nothing else; a `PERMISSION_DENIED`
or a refused connection still fails loudly. **Do not widen it, and do not move it
onto the test.**

**And the stray java is not bad luck.** `firebase emulators:exec` exits **0**
while leaving its Firestore java child alive and still holding port 8085 —
reproduced 6 times out of 6 with a payload that only prints. That is why the
second `npm test` in a row dies with *"Could not start Firestore Emulator, port
taken"*, and why this file has told people to kill a stray java before every
run. It is a harness bug, not a test bug: reap java before running, not after
being surprised.

### 9. Load the pages — `tests/smoke.test.js` (2026-08-16)

**The gap.** `tests/video.test.js` can only import modules that import nothing.
The emulator suites drive a Firestore client and never load a page. Between them
sat every line of UI in the repo, and two entries above say so in two days:
the live-tagging tool threw a `ReferenceError` on the second statement of
`init()` and was **dead for eight days**, and three section rails closed over
their first subject and showed one student's 708 minutes beside every student
after. Both were found by opening the page, because that was the only thing that
could find them.

Both entries concluded the same way: closing it needs **a real parser or a
headless browser, and neither is in the repo**. That framing was the actual
blocker, and it was wrong twice over. A parser only ever answers *is this name
defined* — it would have caught the first bug and been blind to the second, in
which every name resolves and the numbers are simply somebody else's. And a DOM
shim was filed as a dependency when it is a file in `tests/`.

**Don't analyse the module. Run it.** Four files, no new dependency:

| | |
|---|---|
| `tests/module-hooks.js` | 30 lines. `registerHooks` from `node:module` makes the three `https://www.gstatic.com/firebasejs/…` imports resolvable under Node — the single reason no page module in this repo could ever be imported by a test. The stub it serves exports exactly the names the repo imports, generated from a list rather than hand-written, so a newly-used Firestore function fails at link time instead of arriving as `undefined`. |
| `tests/fake-firebase.js` | Firestore and Auth in memory. Documents keyed by path, `where`/`orderBy`/`limit`, batches, `onSnapshot` in both its three- and four-argument forms, and the `serverTimestamp`/`increment`/`arrayUnion` sentinels resolved the way a server would. |
| `tests/dom-shim.js` | The DOM these pages use and no more: a tolerant HTML parser (because `innerHTML = '<span class="jersey">…'` is how almost every row in this codebase is built), a selector engine covering descendant combinators over tag/class/id/attribute, and a 2D canvas context that draws nothing and records everything. |
| `tests/fixtures.js` | One squad. Deliberately awkward: a substitute who came on at 30′, a player on a yellow, a match with a log and a match without one, and two students whose seasons **must not** read alike. |

**Every test here was checked the only way a test can be.** Each historic bug
was reinstated in the working tree and the suite re-run: it fails with
`ReferenceError: updateOnlineIndicator is not defined`, with *"the rail still
shows Alex's minutes"*, and — once the fake Firestore could go offline — on the
`await`-then-render that left the tablet dead at a field with no signal. A suite
that passes on fixed code has proved nothing until it has failed on the broken
code.

**Three decisions worth keeping, because each is a way the shim could have
lied:**

- **`getElementById` parses the real page HTML** rather than conjuring an
  element for any id asked for. `byId('typo')` has to come back null here
  exactly as it does in a browser, or the shim hides the class of bug it exists
  to catch.
- **`requestAnimationFrame` registers and never fires.** There is no compositor,
  so the next frame genuinely never arrives. Firing it off a timer instead turns
  the xG sandbox's render loop into an infinite loop inside the test process,
  which then throws against whatever document is installed by the time it comes
  round again — a failure in the wrong test, about the wrong page.
- **`classList.toggle(name, force)` does not coerce `force` with a bare `if`.**
  Half this codebase calls it as `toggle('hidden', !thing)` where `thing` can go
  missing, and a shim treating an explicit `undefined` as "flip it" would
  disagree with the browser precisely when a value was absent.

**What passing here means, and what it does not.** It means every module
imported, every top-level statement ran, `init()` wired its handlers, the auth
callback fired, the data path rendered and something arrived on screen. It does
**not** mean any of it is laid out, styled or legible: there is no CSS and no
layout engine, so a bar of the wrong width and a column that collapses on a
phone are still browser work. This suite is a floor under the browser pass, not
a replacement for it.

Also fixed on the way: `tests/fixtures.js` first wrote a `kickoff_first` period,
which the timeline rendered as the literal words *"kickoff first"* among a
column of proper sentences. The real value is `kickoff_1st` — so the fallback in
`describeEvent` did its job and the fixture was the thing that was wrong. Worth
naming because it is the failure mode of every fixture: a test that seeds data
the app never writes tests something the app never does.

**The first thing it found, on the first page it had never loaded.** The coach's
match view is the largest surface in the repo and nothing had ever rendered it
in a test. Opening a filmed match showed the review block reporting **two totals
for one list, six lines apart**: the filter chip read *Everything (433)* and the
note under the rows read *Showing the first 200 of 439*.

The six are the tagged log. `visibleItems` returns the hand-tagged entries under
the `all` filter — that is the whole point of the merged feed, and they are on
screen, marked *tagged by hand*, sitting between the candidates at the minute
they happened. The chip counted `cvEvents.events.length` and left them out. A
reviewer working down four hundred rows has no way to tell which of the two
numbers describes what they are looking at, or whether something has been
silently dropped.

Fixed by counting what selecting the chip actually shows. Deliberately **not**
the same denominator as *"n of m checked"* above it, which stays the candidates
alone and is right to: a tagged entry is a human's own record of the match and
has no verdict to give.

**What it found next, once it could reach the rest of the page** (2026-08-17).
Three surfaces of the coach's match view had never been loaded by any test,
because `filmed()` built its stored event list out of `samplePassEvents()` —
which holds no shots, deliberately, being the preview a coach sees before there
is any footage. A stored fixture is under no such constraint, and reusing the
preview as one left the shot ledger, the xG calibration check and the cluster
picker dark, along with the passing network that needs an identity document.
The fixture now carries five shots and six tracked figures on `SAMPLE_SHAPE`'s
track ids, two named and four not — the state a coach actually opens the page
in.

Behind them was one bug in two halves, both about what counts as *checked*:

- **A verdict taken back still counted.** Tapping ✓ twice clears the verdict and
  keeps whatever the shot ledger put beside it, under `if (kept)` — and `kept`
  is `{}` when there is nothing to keep, so the delete never ran. Every undo
  left an entry with no verdict in it. The progress line read *"1 of 433
  checked · 100% of those were real"* with nothing checked; the rail badge
  agreed; and **"Not checked yet" hid the row**, so a mis-tap took an event out
  of the only list built for working through them.
- **A marked shot counted too.** Tapping "Saved" writes `{result: 'saved'}`
  beside no verdict at all — a statement about what a shot did, not agreement
  that it was one. In one sitting the wrong count is merely late, because
  `markShot` redraws the ledger and not the progress line; opening the match the
  next day is what showed it.

`reviewScore` was right throughout, and says why in a comment — *"counting it as
a confirmation would let the xG log quietly inflate this scorecard"* — so the
page reported one checked on the line directly above a card correctly reporting
none. The rule now lives beside that comment as `hasVerdict`, and every count of
how much has been reviewed goes through it.

**And one number the student saw smaller than the coach did.** The tracker loses
people when they leave frame, and `cv/identity.py` only rejoins fragments a
couple of seconds apart, so anyone who went off and came back stays split — the
cluster picker lets a coach map several figures to one player on purpose.
`cvStatsByPlayer` sums them and says so. `cv/publish.py` wrote one document per
*cluster*, so the second write replaced the first: measured on two fragments,
the coach's screen said **54 touches, 7.3 km, 49 minutes** and the student's own
report said **24, 3.1 km, 21** — for the same afternoon, with the student's the
smaller — while reporting "2 player reports written" for one player.

`merge_tracks` fixes it, and `tests/test_player_merge_parity.py` is the other
half: the fix made two implementations of one piece of arithmetic where there
had been one and a half, and the failure is quiet — both sides go on producing
plausible numbers, and only a coach comparing their own screen against a
student's would ever see it. Seventeen fields, plus the two the sides name
differently (`pass_accuracy` against `passAccuracy`), which is exactly where a
shared test earns its place.

**The calibrate page's sentence, as opposed to its solver.** The fit is checked
against the pipeline's to 0.2 mm; the line underneath it had never been run, and
that line is the product — a coach reads one sentence telling them whether to
save this or click again, and *click again* is the wrong answer for two of the
four states. Driven through the page's own seam with the parity test's synthetic
camera. The case worth having: four points clicked perfectly around one penalty
area report **0.00 m average, 0.00 m worst, 13% of the frame**, and must not be
called a good fit. That branch order was commented as load-bearing and now fails
if it is reordered.

**What a real browser then found, 2026-08-17.** The gap above is not closable by
this suite, so the pages were driven in a real one instead — served on localhost
against the Firestore and Auth emulators, seeded with a coach, a squad and an
eight-match season. (`ident()` requires a verified Google identity, so the
account is made through the auth emulator's `signInWithIdp` endpoint and the
token written into the SDK's IndexedDB record; the sign-in popup cannot work in
an embedded pane.)

*Swept and clean.* Every rail section of the match report at 1345 px, and the
whole page at 375 px: no element escapes its block, and the document never
scrolls sideways — the player table's 875 px does what it is supposed to and
scrolls inside its own container. Three things that looked wrong and were not:
the shot map is drawn portrait because its viewBox is the attacking **half**
(56.5 × 72) under `xMidYMid meet`, not a stretched full pitch; the section rail
does print-hide, via `.no-print` rather than anything in the `@media print`
block; and the one-section-at-a-time rule is `@media screen`-scoped on purpose,
so paper still gets every block.

*The calibration canvas, exercised for the first time.* `getBoundingClientRect()`
returns 0 × 0 in the DOM shim, so the click → landmark → fit → overlay path had
never run anywhere. Driven with ground truth: a pitch drawn through a known
homography onto a 1280 × 720 canvas, then eight landmarks clicked at their exact
projected pixels. The page reports **0.03 m average, 0.05 m worst, 53% of the
frame**, and the yellow overlay lands on the painted white lines at every sampled
point — three of five lines within ~1 px, the two worst 10 px near the horizon,
where 0.03 m genuinely spans ten pixels. The coordinate transform back through
the display scale is right.

*The one defect.* `class="btn secondary"` on five controls. No stylesheet has
ever mentioned `.secondary` — `git log -S` finds no rule that was deleted — and
in the browser those buttons compute byte-identical to a plain `.btn`. Harmless
to look at, which is why it lasted: the default already *is* the quiet variant,
so the markup asked for what it would have got anyway, in a word that read as a
decision. Removed, and guarded by a test scoped to `.btn` — the one class here
with a real family of variants, and so the one place an invented variant is
indistinguishable from a chosen one.

**The print layout as paper, 2026-08-17.** The line that used to sit here said
the print rules had been read and their selectors checked, but that nothing had
been through a print preview at a real page size. Now it has — near enough. The
browser tools expose no `printToPDF` and no print media emulation, so the page
size was built by hand instead: find the `CSSMediaRule` whose `media.mediaText`
matches `print`, set it to `all` in place, and size the viewport to Letter's
printable box. **Do not lift the rules into a new `<style>` by copying
`cssText`** — 16 rules go in and 14 come out, the casualty being the `:root`
custom properties, so the palette never inverts and every measurement after that
is taken against the dark screen theme. Mutate the live rule.

The arithmetic, since it is the whole basis of the numbers below. US Letter is
215.9 × 279.4 mm; `@page { margin: 14mm }` leaves 187.9 × 251.4 mm; at the CSS
96 dpi that is **710.2 × 950.1 px**. A4 leaves **687.9 × 1016.5 px** — narrower
and taller, so Letter sets the width and A4 sets nothing. New Jersey prints on
Letter.

*What only a page size could find.* Three columns of the players table were
being cut off. `.table-wrap` carries `overflow-x: auto`, which is exactly right
on a phone — the table is 898 px wide and a phone is 390 — but paper does not
scroll, so on paper that rule is a guillotine at 710 px. `Passes` (645–729),
`Tackles` (729–822) and `m/min` (822–899) all fell past the edge, under a group
header that still read *Estimated from video* across four columns. One of the
four survived. Three of the numbers the entire video pipeline exists to produce
were missing from the printed report, with nothing on the page to say so.

Fixed by measurement rather than guess. Min-content by cell padding, at the
unchanged 0.85 rem: 8/10 px → 743.4, 7/9 → 721.4, 6/8 → 699.4, 5/7 → 677.4.
Only the last clears both papers, and it is one bad surname from not clearing
them. Adding `overflow-wrap: anywhere` to the text cells drops min-content to
**598.9 px and makes it independent of the roster** — stress-tested with
"Papastathopoulos", "Bartholomew Fetherstonhaugh" and a 34-character single
word, all within half a pixel of each other. 111 px of headroom on Letter, 89 on
A4, and the font size does not have to shrink. Numbers keep `nowrap`: a wrapped
figure reads as two figures. `thead th` also goes `position: static`, because
sticky has nothing to stick to on paper and a sticky header is *not* repeated at
the top of each sheet the way a static one is.

Measured on the sample report, which is the fullest state the app reaches with
no footage: **74 elements past the right edge → 0**, players block 979 px → 748,
document 9652 px → 9044. Verified from the real stylesheet after the fix, not
from the trial injection: all eleven columns land inside 708 px of the 710.2
available, and both group headers span what they claim to.

*Instructions that are false on paper.* Scanning the printed DOM for
tap/click/hover/drag/scroll text found six, of which the player page's video
note was the only one already handled — and its comment, *"Tap any row to jump
the video there" is not true of paper*, is the fix for the other five. The shot
map's *Click one to jump the video there*, the shot ledger's *Tap Header on any
that were headed*, the timeline's *Tap one to jump straight to it* on both the
coach and half-time pages, and the season list's *Tap a match to see your part
in it*. Each is now its own `.no-print` element rather than a clause welded into
a sentence that carries real information — `setText` writes `textContent`, so a
sentence cannot carry its own class and the split has to happen in the markup.
The printed page now has zero elements telling the reader to touch it.

*Confirmed sound.* Every figure on the page is SVG — two shot maps at 322 × 416
and a pass map at 598 × 388, no `<canvas>` anywhere — so all of them take the
print palette and come out dark-on-white. Blocks fill the full printable width.
`coach.css` has no print block of its own, so `assets/app.css` is the single
place any of this lives.

*Where the folds land*, at Letter after the fix: team 1.33–5.31, players
5.35–6.14, shots 6.19–6.77, passing 6.81–7.48, pressing 7.53–7.94, subs
7.98–8.86, excluded 8.91–9.27, timeline 9.32–10.52. Three of those blocks carry
`break-inside: avoid` while being taller than a sheet, so the UA has no choice
but to ignore it and split them anyway. That is not a bug to fix — a rule that
cannot be honoured is not a rule that is being violated — but it does mean the
declaration buys nothing for `team-block` or `timeline-block`, and only the
smaller blocks are actually being kept whole.

**Still unswept:** every sheet after the first is anonymous. The claim below
that read *Every sheet says what it is* has been corrected to *The first sheet
says what it is*, with the arithmetic and the reason it was not fixed.

**The half-year flake is settled, 2026-08-17.** `flow.test.js`'s erase test had
failed about half of all emulator runs since 15 August with a gRPC 499. Eighty-
odd runs across five arms say it is the emulator, not this repo: the erase suite
alone is clean 30 times over, the 499 lands in a `beforeEach` at ~12 ms before
any erase code runs, and it needs a **cold** emulator plus the offline-queue
suite's live `Listen` streams to appear at all (4/12 with that suite, 0/12
without). Fixed with a retry on the clear rather than on the test — 15/15 clean.
Two obvious client-side fixes were tried first and both failed; all of it,
including why the retry is safe here and would not be on the test, is in the
Testing Strategy section. Separately and deterministically: `emulators:exec`
exits 0 while leaking the java that holds port 8085, 6 times out of 6, which is
the whole history of "kill the stray java first".

**The other half of that flake, fixed 2026-08-18.** Chasing the 499 turned up
why it was so hard to read: the offline-queue test made two `setDoc` calls it
deliberately did not await — correctly, because offline a write's promise does
not settle until the server acknowledges it, which is the whole reason the
tagging UI must never block on one. But nothing held them either. If anything
after that line failed, the test ended with two writes still queued and the
context torn down underneath them, and the rejection landed later as an
unhandled rejection attributed to whichever test happened to be running when it
fired — which is how a failure in the offline suite got read as a failure in the
erase suite for most of a week. They are now handed to `Promise.allSettled` at
the moment they are created, so they have a handler from birth, and the results
are asserted at the end of the test: *both writes were acknowledged by the
server*, which is a stronger claim than the listener merely no longer calling
them pending. Only reachable after some other failure, so it never made a green
run red — it made a red run lie about where the red was.

**The one control that was not one, 2026-08-18.** The entry further down this
page fixed five `<li>` pickers in the tagging tool; this asks whether anything
else in the repo makes the same claim. **98 click handlers**, of which **8**
attach to a container rather than to a button. Six of those eight are sound, and
`assets/ui.js:400` is the honest case: it builds a `<button>` when `onSeek` is
passed and a `<div>` when it is not, with a comment saying so.

The seventh was the shot map. `assets/shot-map.js` built a bare SVG `<circle>`
with a click listener and `cursor: pointer` on it, under `<svg role="img">`,
while its own docstring said it *"makes each shot a button"*. A `<button>`
cannot live inside an `<svg>` — so the fix is the circle itself carrying
`role="button"`, `tabindex="0"`, an `aria-label` of its own, and a keydown
handler for Enter and Space: the four things a real button would have given for
free, and the four things a click listener on a shape gives none of. `<title>`
is a hover tooltip, not a name; a control needs one whether or not there is a
pointer on the screen.

**The role on the `<svg>` has to move with them.** `role="img"` collapses the
whole subtree into one picture, so focusable children under it are reachable by
tab and announced as nothing — which is worse than not being reachable, because
focus lands somewhere silent. `group` when there is something to activate, `img`
when there is not. And `preventDefault` on Space is not tidiness: on both pages
that draw this map the video being seeked to sits *above* it, so the unprevented
keystroke would jump the video and scroll it out of view in the same breath.

The eighth was `assets/pass-map.js`, which had the identical shape — click
listener on a `<g>`, `cursor: pointer`, no tabindex, no role, under
`role="img"` — and **no caller anywhere**. A pointer-only control that had never
been drawn once. Deleted rather than repaired, with the reason left in the
docstring, because it is the shape the next copy would have been made from.

*Honest severity.* On the coach page the shot ledger already gives every shot a
real `<button>`, so the map dot was a redundant pointer shortcut. On the player
page there is no shot list, and `TEAM_MARK_TYPES` is goals, cards and subs only
— so a saved or blocked shot was reachable there as an unlabelled *Touch*, or
not at all.

*The focus ring was a second bug, and only a browser found it.* The rule written
here first used `var(--accent)`, which is what the rest of the app focuses with.
`.shot-mark.is-on-target` is *filled* with `--accent`. Measured live, focusing an
on-target mark changed nothing but its apparent radius — an accent ring on an
accent disc reads as a slightly bigger dot, not as focus — and `--good` fills the
goals and sits right next to accent, so it was weak there too. `--text`
(`#e6efe9`) is the one token here that no mark can be, roughly **16:1** against
the pitch backdrop, and that is what the rule uses. `paint-order: stroke` keeps
the ring wholly outside the disc: without it half the stroke width eats into a
mark that is **27 px across at its smallest**, and the fill colour is the thing
saying whether the shot went in. Confirmed by real Tab presses against the real
stylesheet on all three mark types.

*Neither map had a test of any kind* — no occurrence of `shotMapSvg`,
`renderShotMap`, `passMapSvg` or `onPick` anywhere under `tests/` — which is how
a docstring got to describe a button for as long as it did. `tests/smoke.test.js`
is the only suite that can hold one: `video.test.js` covers pure functions and
cannot import a module that calls `createElementNS`. What is pinned is the
contract rather than the shapes — a mark you can activate has a role, a tab stop,
a name and answers to the two keys a real button answers to; a mark you cannot
has none of them and the map stays a picture. Proved to fail against the pre-fix
code before it was kept.

*Not a defect today, recorded so it stays that way.* Seven `<button>` elements
omit `type="button"`: `coach/coach.js:1037` and `:2827`,
`live-tagging/tagging.js:260, 313, 635, 664`, `calibrate/calibrate.js:300`. A
missing type only submits something inside a `<form>`, and this app contains no
`<form>` element — the one match in the tree is vendored under `.venv`. The day
somebody adds a form, all seven become real.

**The one function every published number comes out of, 2026-08-18.** The
Reality Check said "the pieces have never been run end to end as one pipeline",
and unlike the other half of that sentence it was true. `analyse_match` is where
twenty-odd subsystems get assembled — detect, track, colour-cluster, ball,
camera, possession, identity, thumbnails, touches, events, xG, movement — and
nothing could call it. It built its own detector, so calling it meant installing
ultralytics and torch, which `requirements-test.txt` deliberately leaves out.
Its only three callers are experiment scripts that all need real footage. Every
number this project publishes comes out of that function and it had no test.

*The seam already existed and stopped one level short.* `TrackedFramePass` has
taken `detector=None, tracker_factory=None` since it was written, with a comment
at `cv/frames.py:283-285` explaining why: everything it does with a frame is
bookkeeping, and bookkeeping is exactly the part worth pinning. The fix is to
pass the same two arguments through from `analyse_match` — two parameters and a
docstring paragraph, no behaviour change on any real path — after which the
whole assembly runs on a synthetic clip in **2.7 seconds** with no GPU, no
ultralytics, no torch and no footage. `tests/test_pipeline_end_to_end.py`, **29
tests**, Python **1044 -> 1073**.

*What comes out, both paths.* Nine seconds of 1470x952 at 30fps: ten coloured
rectangles wandering on their own orbits, a long pass at 3.0s, a strike at 7.0s.

| | calibrated | uncalibrated |
|---|---|---|
| timing stages | 10 | 8 |
| events | carry / pass / carry / **shot** | carry / pass / carry |
| shot | goal, on target, 16.1 m, `in_box`, **xg 0.118** | none |
| every `start_m` | in metres | `None` |
| `pitch` / `movement` / `coverage` / `territory` | present | `None` |
| keeper method | `colour+position` | `unavailable` |
| warnings | 1 | 2 |
| report JSON | 30 478 bytes | 22 979 bytes |

Common to both: ten players split 5/5, nobody excluded, equal minutes, kit
separation 129, 270 ball points with no gaps, camera checked with no shifts, and
`json.dumps(..., allow_nan=False)` succeeds — which is the assertion that would
have caught a `nan` reaching the browser as a parse error. The uncalibrated run
is not a degraded copy of the calibrated one; it is the second product, and the
test asserts the shape of what a calibration actually buys rather than assuming
it is everything.

*Two real bug classes this would have caught, both of which happened here.* The
`ppda` field that was null on every match for a fortnight because nothing ever
ran the chain that fills it, and `attach_xg` sitting fully tested with no caller
at all. Both are invisible to unit tests by construction: every part worked.

*What it does not prove, stated in the file's own docstring.* Detection and
tracking are scripted — a fixed list of boxes per frame and a tracker that hands
back slot indices. Nothing here says YOLO finds a ball, that tracks survive an
occlusion, or that any number is *correct*. It says the chain executes, the
values are the right kind of thing, and the two paths differ where they should.
Treat it as a check that the chain runs, not as a measurement.

*The fixture found something in the pipeline, and the fixture is what changed.*
The scripted strike produced no shot: `speed_after_ph_s` read **4.40** against a
`SHOT_SPEED_PH_S` threshold of 6.0, so the pipeline classified an unmistakable
strike on goal as an ordinary carry, silently. The cause is in `cv/touches.py`
and is real football, not synthetic weirdness. `segment_touches` keeps a
candidate only if it is a local minimum of ball-to-player distance across a
+/-4-frame window — but a ball resting at a player's feet **ties** at every frame
in that window, so every frame passes the gate and the earliest one with enough
motion wins; `_suppress` then keeps it, because it only replaces an earlier
candidate when a later one is *strictly* more confident and both are capped
equal. The touch lands up to four frames early, and `_velocities` uses a
one-sided forward window, so the reported speed averages the strike together
with the still frames before it — about a third of the truth. Changing the
fixture so the shooter nudges the ball a metre ahead and runs onto it made the
strike the only local minimum: **13.51**, shot detected, goal, `xg 0.118`. Touch
count also fell from 13 to 8, which is the same effect showing up as duplicates.
The fixture changed rather than the code because a ball dwelling at feet is not
the case this test exists to pin, and a threshold tuned against a synthetic clip
is a threshold tuned against nothing. **Recorded here as a known property**: any
touch that follows a period of close control is placed early and reads slow, and
the first real footage is what should decide whether that costs a shot.

675 pure JS · 25 pages · 145 emulator · **1073 Python**.

**The last mile had no way to be run, 2026-08-18.** Prompted by the entry above,
which found `attach_xg` fully tested with no caller and called that shape
invisible to unit tests by construction. It is, so the way to find the rest of
it is to walk the call graph rather than the coverage.

An AST pass over `cv/**/*.py`: **560 definitions, 549 reachable, 207 public
top-level names, 21 of them reachable from nothing that runs.** Reachable means
reachable from module-level code, from a `main()` in `cv/experiments/`, or from
`analyse_match`. Tests are deliberately not roots — "called only by its own
test" is the thing being looked for, and counting a test as a caller hides every
finding.

The 21 decomposed cleanly into three piles, and the first one is the finding:

*Eight names, which is the entire public surface of `cv/publish.py`.* `publish`,
`PublishError`, `summary_payload`, `identity_payload`, `events_payload`,
`thumbs_payload`, `participant_notes`, `player_report_fields`. That module is
the last mile of this whole project — every number the pipeline works out is
worth nothing until it is in Firestore where the app can read it — and it has
been written, guarded and tested since the day the pipeline started producing
reports, with no way to be run. No `main`, no `__main__`, no caller outside
`tests/`. Four documented refusals nobody could trigger. `FOOTAGE_DAY.md` walked
the whole intake and stopped one step short, at "save that JSON as the first
baseline", because there was nothing to tell anyone to type next.

`cv/experiments/publish_report.py` is the missing step, and **21 tests**
(`tests/test_publish_report_cli.py`) cover the wrapper only — what gets written
and what gets refused is `tests/test_publish.py`'s job, and duplicating it would
mean two files to change the day a rule does. `--dry-run` swaps in a client that
records writes instead of performing them, so a payload can be looked at before
a credential is anywhere near it. Checked from a real shell rather than from
pytest: a dry run against a two-track report printed five writes with their
sizes (summary 394 bytes, identity 404, events 101, thumbs 17,
playerReports/playerA 423) and exit 0; the same command without `--dry-run` and
with the key unset printed `refused: PITCHIQ_SA_KEY is not set...` and exit 1.
`FOOTAGE_DAY.md` §5 gains the fifth step, including the reason the mapping comes
second: a tracked figure is a guess about which child it is until a coach agrees.

*One thing a dry run cannot know, said out loud rather than implied.* `publish`
skips any player whose report a coach has not published yet, and finds that out
by reading the document. A dry run reads nothing, so it assumes every mapped
player has one and prints an **upper bound**. The output says so; without the
caveat it would promise writes the real run correctly declines to make.

*Three functions deleted, one of them wrong.* `pressure_by_team`
(`cv/events.py`) promised "how often each team's **opponents** were closed down
on the ball" and incremented `counts[touch.team]` — the team **on** the ball.
Every figure attributed to the wrong side, in code no test could ever have
reached. It is superseded by `count_pressing`, which is live in two places.
`to_pitch_series` (`cv/metrics.py`) was superseded when task #27 collapsed the
pipeline to one pass; its one piece of hard-won reasoning — project the
bottom-centre of a box, because the box centre floats in mid-air and lands
metres up-pitch — already survives in `cv/detector.py`, `cv/frames.py` and most
fully in `cv/calibration.py`, which is why deleting it loses nothing.
`collect_samples` (`cv/teams.py`) is the same story: `TrackedFramePass._sample_colours`
samples inside the decode loop now. Same precedent as `assets/pass-map.js` —
superseded code gets deleted rather than repaired, because it is the shape the
next copy would have been made from.

*Ten left, which are not mistakes.* This project builds the primitive before the
caller more often than not, because the primitive is the part that can be tested
without footage: `in_goal_area`, `channel`, `zone_grid`, `nearest_player`,
`defensive_line_m`, `pinned_back`, `cluster_of_track`, `drift_notes`,
`predict_xg`, `validate_against_noise`. Each is now recorded in
`tests/test_call_graph.py` with **the reason it has no caller yet**, so the next
audit does not derive any of it a second time.

*The check that keeps it from recurring, and the honest limits on it.*
`tests/test_call_graph.py`, **7 tests**, inside the existing Python CI job
rather than a script nobody types. It fails in both directions: an unlisted
orphan is a finding, and a listed name that has since been wired up or deleted
is a stale entry to remove — otherwise the list rots into a graveyard nobody
dares touch. What it cannot do is more interesting than what it can. Names
propagate by bare match, so anything calling `run` marks every `run` reachable;
resolving attributes back to types would need a type checker and would start
being wrong in ways nobody could audit. It therefore over-approximates
reachability and **under-reports dead code**: a failure is strong evidence, a
pass is weak. It cannot see dispatch through `getattr` or a dict of callables,
and it does not walk the browser modules at all, where an export is reached from
`<script type="module">` and from handlers no Python AST will find.

*The checker's own bug, recorded because this class of tool fails quietly.*
First version keyed its graph on `id(node)`. The trees are dropped as the loop
moves to the next file, CPython reuses the addresses of freed objects, and
unrelated definitions silently merged — it reported 12 orphans including
`analyse_match`, which is a **root**, and that impossibility is the only reason
it got caught. A call-graph walk says "all clear" whether or not it works, so
`TestTheGraphItself` now pins the three properties whose failure would produce
a quiet pass. Negative control run for real: move `publish_report.py` aside and
the check reports `publish` as an orphan again, which is the case it exists for.

675 pure JS · 25 pages · 145 emulator · **1101 Python**.

**How high the back line sat, 2026-08-18.** The fourth figure the shape family
was always missing, and named in the [MVP] catalog above as "defensive line
height and pressing intensity trend" — the trend shipped in task #79 and the
height did not, because `defensive_line_m` (`cv/zones.py`) has existed and been
tested since task #28 with no caller. The previous entry found it and recorded
why. This is the why being answered.

*It is not the same kind of figure as the three beside it.* Width, depth and
compactness are descriptions; a coach reads them and nods. A line height is
acted on — it is the number that decides whether the instruction on Tuesday is
"push up" or "drop off". So the work here is almost entirely about the ways it
can be **confidently wrong**, and only incidentally about computing it.

*The finding, which is why this took a measurement rather than a wiring-up.*
`defensive_line_m` averages the deepest four outfielders, which is exactly right
when all ten are tracked and increasingly wrong below that: every defender the
tracker loses is replaced in the deepest four by a midfielder twenty-five metres
further up. Exhaustive over **every** subset of a synthetic 4-4-2 with its line
at 20 m — not sampled, so these are facts about the estimator and not about a
seed — the mean error of the naive count against the number of outfielders
tracked:

| tracked | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|
| deepest four | **+20.0 m** | +13.9 | +10.0 | +7.5 | +5.0 | +2.5 | 0.0 |
| share of 0.35 | +6.5 | +3.6 | **+1.5** | +0.4 | +1.1 | 0.0 | 0.0 |

A side with six of its ten outfielders on screen would have been reported as
defending **ten metres higher than it did** — a whole pitch-third of artefact on
a figure somebody changes their defending on. Confirmed on two more shapes: a
4-3-3 runs +23.4 m at four tracked and a deep 5-3-2 +16.3 m, both to 0.0 at ten.

Three properties of that error make it worse than it looks. It is
**one-directional** — always upward, so it accumulates into the mean instead of
averaging out across a half the way random error does. It **moves with tracking
density**, which varies within a match, so no constant offset absorbs it and a
side that got harder to track would appear to have pushed up. And it is
**invisible downstream**: 30 m is a perfectly plausible reading, so nothing after
this point could ever flag it.

*A minimum count does not fix it; a proportion does.* `LINE_SHARE = 0.35`,
`players = min(k, max(2, round(0.35 * k)))`. Chosen because it holds the error
under 1.5 m from six tracked upward across all three formations **and** because
`round(0.35 × 10) = 4` — a fully tracked team gets exactly the conventional
deepest four, so good tracking is untouched by any of this and only sparse
tracking degrades away from it. Below six it is still +3.3 to +7.2 m, which no
estimator fixes, so `MIN_LINE_OUTFIELDERS = 6` and the figure is simply absent.
The floor of two lives **inside** the clamp: `max(2, min(...))` returns two out
of one tracked player, which is how a "line" gets averaged from somebody who was
never in it. That ordering was a real bug, caught by an invariant test rather
than by any caller — the guard means nothing live reaches k < 6 — and fixed
before it could become one.

*Absent, not zero, in three separate ways.* No calibration, and there are no
metres to measure in. No `side_of_team`, and nothing can say which goal a colour
cluster was defending — without which the mirrored eleven reads **47.5 m instead
of 20 m**, not slightly off but a different team, which is what half-time sides
changing would do to every reading. Too few tracked, per the table. Each returns
a missing key rather than 0.0, and 0.0 m is a real reading — a side defending on
its own goal line — so it could not have been used as a sentinel even if the
project allowed it. This is the first shape figure that can be absent while its
three neighbours are present, and `groupStats` already handles it: shape rows
carry a truthy `kind`, so a row null on both sides is dropped and an
unassigned run draws three rows rather than a fourth reading a dash. No
conditional construction anywhere.

*Said plainly, because the name means something narrower than the figure.*
"Defensive line height" conventionally means where the back line sat **out of
possession**. This is averaged over every instant of the window, including the
spells the side spent camped in the opposition half with the ball, which pull it
up — and pull it up further for the side that had more of the ball, so the
figure is biased in favour of exactly the team least likely to question it. The
pipeline cannot split a window by possession yet. Until it can, the caveat sits
in `cvQualityNotes` gated on the figure being present, following `options.shots`
gating the header caveat: a caveat about a row that is not on screen gets
attached by the reader to whatever is.

*The drift sentence needed its own wording.* `shape_drift` reports the height at
both ends of the window, and `SHAPE_WORDS` / `DRIFT_TEXT` say "6m deeper" and
"6m higher up the pitch" rather than "line height down 6m". The figure has no
good direction — a side that pushed up won the ball higher and left more grass
behind it — and a number going down reads as a decline whatever the docstring
says. The three keys beside it are held to `min_samples` together; this one is
held **separately**, so a window where the tracking thinned out loses its line
height alone rather than costing the report its other three.

**19 Python tests** (`TestDefensiveLineHeight`, `TestSparseTrackingBias`, five
more in `TestShapeDrift`) and **9 browser tests**. The bias table is pinned as
literals rather than only written down here, so any future change to the
estimator has to argue with it. `SCHEMA_VERSION` 12 → 13.

684 pure JS · 25 pages · 145 emulator · **1120 Python**.

**Where they played against the line they were picked in, 2026-08-18.** The
last item in the [MVP] post-game tactical catalog above that needed no footage:
"individual positional discipline: heatmap vs. assigned role". Two records
already in the database and never once compared — the pipeline's occupancy grid,
which is where a player actually was, and the position the coach typed into the
squad list, which is where they were meant to be.

*A reconciliation, so it names a disagreement and never a culprit.* Neither
record is the truth. The grid can be wrong because the camera favoured one end;
the assignment can be wrong because a coach filled it in months ago and the
player has changed roles since. So the block says "your left-back averaged
further forward than either centre-back" and stops. It never says out of
position, and it never scores anybody.

*The centroid is not the hard part; knowing when it is worth quoting is.* A mean
position collapses ninety minutes into one dot, and two dots four metres apart
mean nothing at all if either player ranged over twenty. Each row therefore
carries a band, not just a point: `POSITION_SIGMAS * spread * sqrt(WANDER_TAU_S
/ (30 * minutes))` — the standard error of the mean, with the sample count taken
as independent looks at the player rather than frames, because consecutive
frames of a footballer are not independent evidence about anything.
`WANDER_TAU_S = 60` is the correlation time assumed: a player's position tells
you roughly where they will be a minute later and roughly nothing about where
they were twenty minutes ago. It is an assumption and is labelled as one; what
makes it safe is that it is applied only to *withhold* comparisons, never to
make one.

Two players are only called apart when the distance between them clears
`hypot(bandA, bandB) + COVERAGE_MARGIN_M`, floored at `POSITION_FLOOR_M = 5`.
The margin **adds** rather than combining in quadrature with the bands, and that
is deliberate: the bands are noise and compose the way noise does, but uneven
camera coverage is a *bias* — it pulls every player on that side of the pitch
the same way at once, and a bias does not shrink by being added in quadrature.
Above `MAX_BAND_M = 8` a player is measured and never compared: that gate
catches a roamer at one end and a player tracked for two minutes at the other,
and both mean the same thing.

*What binning the pitch into 8.75 m cells actually costs, measured.* The whole
feature reads metres off a twelve-by-eight grid, so this had to be a number
rather than a hope. Swept in 0.25 m steps across every mean position, for
spreads of 4 m to 20 m:

| mean position | worst error | median |
|---|---|---|
| 8–97 m (outfield) | 0.229 m | 0.012 m |
| 4–101 m (all of it) | 0.475 m | 0.013 m |

and against how tightly the player held their ground: **2.29 m** at a 1 m
spread, 0.97 m at 2 m, 0.29 m at 3 m. Both worst cases describe the same person
— a keeper, near a goal line, moving less than anybody — and keepers are
measured here and never judged. Spread gets Sheppard's correction, `cellW² /
12`, which recovers a true 4.0 m from 4.73 m binned to 3.999 m; but that 0.01 m
is an average over where the mean falls inside its cell, and one player has one
phase rather than an average of them. Per phase, a true 4 m reads 3.85 m to
4.14 m and a true 3 m reads 2.17 m to 3.65 m. Harmless only because
`POSITION_FLOOR_M` is what binds the comparison for a 3 m player past about ten
minutes tracked and a 4 m player past about eighteen. All of it is pinned as
literals in `tests/video.test.js` against a deterministic quadrature harness, so
changing the grid shape or the correction argues with a test rather than with a
stale docstring.

Re-measuring rather than transcribing is how two figures already shipped in
those docstrings were caught: the centroid sweep had been run over the outfield
and quoted as though it covered the pitch, and the correction's phase-average
had been quoted as a per-player guarantee. Neither was wrong by much. Both were
wrong in the direction of sounding more certain than the arithmetic was.

*The verdict is withheld far more often than it is given, on purpose.* Four
separate ways: the camera saw the thirds unevenly (`COVERAGE_SPREAD_MAX = 0.2`),
the coverage figure is missing entirely so nobody can check, nobody in the squad
has a position recorded, or too few players survive the band gate. In every one
of them the rows still render — the measurement is real and stands on its own,
and only the comparison goes. A "line" claim is also all-or-nothing: being ahead
of the whole midfield is a statement about the whole midfield and cannot be made
from two of its three, so one untrackable midfielder removes the midfield from
every comparison rather than quietly shrinking it.

*Three defects the tests found, two of them in code that was already written.*
The first was live: `shapeSource` read `player.position` off the match roster,
where `setLineup` has never written one, so every real match resolved every
position to null, `positionalPlay` returned `withheld: 'no-lines'`, and the
comparison silently did nothing on every match that had ever been played. It was
visible only because a fixture carrying real occupancy grids forced the
production path through a test instead of the sample preview. The second:
`Number(null)` is `0` and `0` is finite, so a third the run never measured
arrived as a camera that saw nothing of that end — 'uneven' instead of
'unknown', the wrong note under the bars, and the same trap this project had
already caught once in the shot map. The third was arithmetic working exactly as
specified and still being wrong to read: the comparison is symmetric, so a lone
striker dropping in flagged himself **and** all four defenders he dropped behind
— five remarks for one thing that happened, in the most ordinary shape there is.
When two lines have swapped over entirely it is now said from the smaller side
only. Not because the smaller side is at fault, which nothing here can know, but
because both sentences are the same sentence.

*A fourth defect, which no test could have caught.* The smoke suite is explicit
that it does not say anything is laid out correctly, so the block was measured in
a real engine instead: the rendered markup was dumped through that same harness
and served, and every box read back at 375, 768 and 1280. Nothing overflowed and
nothing clipped at any width — but the scale strip under the bars was reading
from the wrong ruler. It stopped at the container's right edge rather than at the
end of the track, because it carried a left margin for the name column and no
matching right margin for the value column, so above 560 px "Halfway" sat
**28.8 px** right of the track's midpoint and "Their goal" ended **57.6 px** past
the end of the bar it labels. A ruler that disagrees with the thing it measures
is worse than no ruler; both margins now mirror the row's own grid, and the scale
box measures 128.2 → 591.4 against a track of 128.2 → 591.4, with the midpoint
label centred at 359.8 against a track centre of 359.8. Mobile was already exact
and stayed exact.

*One thing worth stating before somebody reports it as a bug.* The passing
network draws each player at their mean **pass origin**; this draws them at
their mean **occupancy**. Those are different means over different samples and
they will not agree — a striker who touches the ball twice in the final third
and otherwise presses is in two visibly different places on the two pitches.
Both are correct.

**31 browser tests** — five describe blocks over `gridCentroid`, `gridSpread`,
`orientedCentroid`, `positionBand`, `coverageVerdict` and `positionalPlay` — and
**2 page tests**. No schema change: every input was already being published.

**The two boxes that vanished when the work started, 2026-08-20.** A coach
sent a screenshot of the calibrate page: eight points placed, **1.77 m average,
4.43 m worst, 11% of the frame**, and no idea which of the three causes the page
lists was theirs. It was the third one — the pitch size — and the page had
made it unfixable. `input-length` and `input-width` lived in the Start card
inside `#intro`, and `loadImage` hides `#intro` the moment a picture loads.
There was literally no way to change the field size while clicking, and the
default it silently kept using was a full-size 105 × 68.

*Measuring it instead of asking again.* `measureField` searches (length, width)
for the pair whose homography reprojects the clicked points with the smallest
error, coarse at 2.5 m then fine at 0.25 m. The mechanism is one sentence: a
corner is wherever you say the corner is, so a set of corners fits **every** size
equally well, while the penalty box, the goal and the penalty spot are fixed
distances in the Laws and one of those pins the scale of everything else. Both
halves are load-bearing, and the refusals are the half that keeps the page from
inventing a pitch and scaling every distance the software ever reports by the
invention.

| clicked | reported | mean | interval |
|---|---|---|---|
| 12 perfect points, 105×68 / 100×50 / 110×60 | exact | 0.00 m | both |
| the same, 0.5–2 px of click jitter | within 0.5 m | 0.03–0.11 m | both |
| the same, 4 px | 99.3 × 49.8 | 0.21 m | L[98,101] W[49,50] |
| four corners | **refused on the count** | — | — |
| corners + halfway + centre spot | **refused × refused** | 0.00 m | L[80,130] W[44,90] |
| + one penalty box corner | 100.0 × 50.0 | 0.00 m | exact |
| the coach's own eight | **refused × 51.8** | 0.78 m | L[99,114] W[48,54] |

The fifth row is the interesting refusal: seven points, all placed perfectly,
and the page still cannot say. Every landmark there is defined as a fraction of
the pitch, so rescaling the model rescales all of them together and the fit is
exactly as good. Nothing is broken; there is genuinely no answer, and the page
says which three markings would give it one.

*Two decisions the study forced.* `sizeError` optimises **metres**, not pixels,
because metres are what the page reports and what `is_usable` draws its bar in.
On the coach's real frame the two objectives disagreed by roughly 10% —
117 × 59 pixel-best against 106 × 51 metre-best — and only the metre
answer put the page's own average and worst under the bar. And a dimension is
refused when its profile interval touches a search bound or spans more than a
quarter of the range, which is what catches the cases where the metre objective's
scale bias would otherwise run away. The two dimensions are refused
**independently**: the coach's clicks measure a width confidently and a length
not at all, because one of the three fixed-size landmarks they placed was in the
wrong spot. A version that averaged the two into one verdict would have lost
both. Taking the offered size drops that frame from 1.70 m to **0.49 m average,
0.92 m worst** — from failing the page's bar to passing it, without moving a
single click.

*And a definite way of being done,* which is the other thing they asked for.
Five checks: five points or more, spread across the picture, within half a metre,
a field size that agrees with the points, and one no software can make — the
coach confirming the yellow outline sits on the paint. A todo check is grey, not
red: five crosses on an empty picker would read as five things already gone
wrong. The summary line says **"Done. This calibration is ready to save."** or
counts what is left, and the save button says *Save calibration* or *Save
anyway* — never disabled, because a coach who decides to save an imperfect
calibration should not have to hunt for the button. Clearing the points revokes
the tick, since it was a statement about points that no longer exist. The export
now carries a `quality` block — the two errors, the coverage, the measured
size, the tick — which `from_picker_export` ignores, so a file that turns out
to be wrong later can still be asked whether the page said so at the time.

*What a real browser found, that no test could.* Two layout defects, both in the
markup the tests are explicit about not covering. The site had **never had a
checkbox**: the global `input` rule is written for text boxes and gives every one
of them `width: 100%`, `min-height: 50px` and 13 px of padding, so the first tick
on the site rendered as a white slab the width of its own label, with the
sentence squeezed into a column one word wide. Ticks and radios now opt out of
that rule, next to it, so the next checkbox anywhere does not repeat it. And the
mobile rule for the measured readout sized the **box** rather than the flex item
it sits in, which left it at 137 px inside a 343 px column at 375 px wide. Both
fixed and re-measured at 375, 768 and 1280: single column, one column, two
columns, and the document never scrolls sideways at any of them.

**8 browser tests** over `measureField` — recovery, jitter, and four separate
shapes of refusal — and **1 page test** that drives the whole path through the
picker's own seam, from a size that disagrees to a save button that says done.
The page test opens by asserting that neither size input is inside `#intro`: the
regression stated as a place in the tree rather than as a symptom.

739 pure JS · 35 pages · 146 emulator · 1210 Python.

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
- Phase-of-play breakdown: buildup vs. progression vs. final-third entry success — **built 2026-08-16 and never marked here;** `phase_of_play` (`cv/sequences.py`) is wired at `cv/pipeline.py:818` and rendered at `assets/report.js:2493`
- Defensive line height and pressing intensity trend — **the trend built 2026-08-14 (task #79), the height 2026-08-18;** the height carries a measured correction for sparse tracking and is absent rather than guessed below six tracked outfielders
- Set-piece outcomes (corner delivery zones, aerial duels won)
- Substitution impact: team stats in the window before vs. after each sub (exact sub timing comes straight from Phase 3's live log) — **built 2026-08-15, and shipped under a different name;** see the Phase 13 entry for why "impact" is a promise the arithmetic cannot keep
- Individual positional discipline: heatmap vs. assigned role — **built 2026-08-18;** a reconciliation of the occupancy grid against the coach's own position assignment, which withholds its verdict far more often than it gives one

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
- [x] Lens distortion correction if using a wide-angle/action camera.
      **Measured 2026-08-12, when the diagnostic half turned out to be
      impossible from clicked points and the pages stopped pretending
      otherwise. Reopened and built 2026-08-20 from a signal the
      clicks never carried — the paint itself.**

      The motivation is real and bigger than expected. On a synthetic camera,
      barrel distortion as mild as `k1 = -0.03` gives about **1.1m of mean
      reprojection error with every landmark clicked perfectly** — already past
      the 0.5m bar `is_usable` sets. At `-0.06` it is 2.9m, at `-0.25`
      (GoPro-ish) 6.2m. A wide lens does not degrade a calibration, it
      disqualifies it.

      The trouble is that **the error looks exactly like careless clicking**:
      ±12px of jitter on perfect optics gives 2.3m, indistinguishable from mild
      barrel's 2.0m. Three statistics were tried as a discriminator and all
      three failed:

      * **residual magnitude against radius from the image centre** — barrel
        0.52-0.81, but sloppy clicking reached -0.63 and the sign flipped
        between barrel strengths, because the homography absorbs much of the
        distortion.
      * **radial alignment of the pixel residuals** — barrel -0.33 to +0.22,
        noise spanning -0.39 to +0.39. No separation at all.
      * **fitting one radial parameter and measuring how far the error drops** —
        promising at first (noise 1.0-1.3x, mild barrel 2.9-6.6x) and then not:
        across camera geometries barrel reached as low as 0.99x and noise as
        high as 6.4x.

      A fourth — **how concentrated the error is in the single worst point** —
      looked perfect and was an artefact. Under Python's `Calibration.fit` one
      bad click carries **100% of the residual in every trial at eight or more
      points**, because `cv2.findHomography` uses RANSAC and fits the remaining
      points exactly. The picker page solves normal equations instead, and under
      least squares the same statistic reads 0.27-0.42 for a bad click, a lens
      and general imprecision alike. **The measurement had to be redone against
      the fit the page actually uses**, and that is what killed it.

      So no correction and no diagnosis. What did change is that both surfaces
      stopped asserting a cause they cannot know. The picker said *"One point is
      probably in the wrong place or named wrong"* and the CLI said *"Usually
      one landmark is misplaced or mislabelled"*; both now list three
      candidates — the lens, a misplaced point, a guessed pitch size — and lead
      with the lens, because it is the only one where the obvious next action
      (re-click everything) is the wrong one. Checked in the browser by feeding
      the page six perfectly-placed landmarks from a `k1 = -0.06` camera: 1.15m
      average error, and the new verdict where the old single-cause claim used
      to be.

      The Python side keeps its "most suspect points" list, which the same
      measurement **vindicates** — RANSAC really does isolate the culprit there.

      What remained open then was the correction itself, and the blocker was
      written down as a fact about the world: it needs a per-camera lens
      calibration (OpenCV chessboard, once per camera), which nobody can produce
      before there is a camera, and building the undistort path meanwhile would
      add a code path with no caller — something this repo had already had to
      clean up twice.

      **Reopened and built 2026-08-20, because both halves of that stopped being
      true.** `cv/lines.py` had since made the **plumb-line constraint**
      available: painted lines are straight by the Laws, so a straight line is
      straight in the image only if the lens is rectilinear. Every calibration
      frame is therefore its own chessboard, already shot, by a camera the coach
      already owns. And `calibrate.py --frame` was a caller sitting there
      waiting.

      That is also why this works where the three statistics above failed, and
      the reason is not that they were badly chosen. All three read **clicked
      points through the homography**, which absorbs much of the distortion
      before any statistic gets to see it — those failures are that absorption,
      measured. Curvature in a painted line does not go through the homography
      at all. **Click jitter cannot bend a painted line; a lens can, and only a
      lens does.**

      `cv/distortion.py` traces connected chains of the paint `cv/lines.py`
      already finds and searches for the one coefficient that makes them
      straightest. The **division model** rather than Brown's polynomial,
      because it inverts in closed form in both directions and the overlay needs
      metres → pixels as often as the reverse; radii normalised by half the
      image diagonal, so `k1` means the same thing at any resolution. Negative
      is barrel.

      **The refusal is the part worth defending, more than the correction is.**
      `lens_for_frame` returns no model more readily than it returns one, and
      that is not timidity: a line through the image centre stays straight under
      *any* coefficient, so a frame whose paint all runs near the centre has
      genuinely nothing to say, and fitting a number to it anyway would move
      every landmark in exchange for nothing. It wants bow above the tracer's
      own noise floor, a real straightening gain, and agreement across repeated
      runs before it will answer.

      The sweep, on synthetic cameras with **every landmark clicked perfectly**,
      so nothing below is contaminated by click error. Errors are mean / worst
      landmark, in metres:

      | true `k1` | recovered | gain | verdict | no lens | with lens |
      | --- | --- | --- | --- | --- | --- |
      | +0.000 | +0.0001 | 1.0x | refused | 0.00 / 0.00 | 0.00 / 0.00 |
      | −0.015 | −0.0111 | 1.2x | refused | 0.07 / 0.16 | 0.02 / 0.04 |
      | −0.020 | −0.0187 | 1.5x | refused | 0.10 / 0.22 | 0.01 / 0.01 |
      | −0.025 | −0.0263 | 2.1x | **applied** | 0.12 / 0.62 | 0.01 / 0.01 |
      | −0.030 | −0.0335 | 2.1x | **applied** | 0.15 / 0.74 | 0.02 / 0.04 |
      | −0.040 | −0.0392 | 2.6x | **applied** | 0.20 / 0.97 | 0.00 / 0.01 |
      | −0.050 | −0.0507 | 3.2x | **applied** | 0.24 / 1.20 | 0.00 / 0.01 |
      | −0.100 | −0.1027 | 4.8x | **applied** | 0.55 / 1.42 | 0.01 / 0.03 |
      | −0.200 | −0.2004 | 7.5x | **applied** | 0.70 / 4.26 | 0.00 / 0.00 |
      | +0.050 | +0.0489 | 3.5x | **applied** | 0.26 / 1.35 | 0.01 / 0.01 |
      | +0.100 | +0.0990 | 8.0x | **applied** | 0.45 / 2.66 | 0.01 / 0.01 |

      **The headline is where the boundary landed.** Every frame the gate
      refuses would have cost at most **0.22m** at its worst landmark. Every
      frame it accepts would have cost at least **0.62m**, and comes back at
      0.04m or better once corrected. So the refuse/accept line sits on the same
      **0.5m** bar `CalibrationError.is_usable` draws — and it was not tuned to
      it. The thresholds came out of the tracer's own noise floor (0.31px of bow
      on a rectilinear frame), fixed before that bar was looked at, which is the
      only reason the coincidence is worth anything. It is pinned by
      `test_the_gate_lands_on_the_usability_bar`, so a change that drifts the
      gate off that bar fails instead of shipping.

      **One invariant runs through the whole wiring: a homography is only ever
      fitted to straightened pixels.** Hand it raw ones and it quietly absorbs
      the curvature it cannot represent, and the stored lens then fights a
      matrix that has already half-corrected itself — the same absorption that
      killed the 2026-08-12 statistics, met again from the other side. Writing
      that rule down immediately found two bugs in `cv/lines.py:refine`, which
      had shipped the day before: it fitted its ICP correspondences to raw
      pixels, and then dropped the lens entirely when it built the refined
      `Calibration`. Either alone makes the saved file wrong, silently, and
      `--refine` is the flag most likely to be reached for on exactly the
      wide-angle footage that needs the lens most.

      Overlays had to change too, for a reason that only shows up in a
      screenshot. `draw_pitch_lines` drew each pitch line as one straight
      stroke, which is right through a bare homography and wrong through a lens:
      the camera bends the paint, so a straight drawn line misses paint that a
      **correct** calibration is tracking perfectly, and the overlay indicts the
      calibration for the lens's work. It now subdivides into `DRAW_STEPS = 16`
      points and draws a polyline. Worst gap between polyline and true curve on
      a full-length touchline at `k1 = -0.2`: **20.3px undivided, 1.4px at 4
      steps, 0.09px at 16, 0.02px at 32.** 16 is where it stops being visible on
      a 2px stroke; past that the cost is `to_pixels` calls for something nobody
      can see.

      End to end on the real CLI, one flag apart, same eight perfectly-placed
      clicks on the same `k1 = -0.05` frame:

      | | reprojection | leave-one-out | verdict |
      | --- | --- | --- | --- |
      | `--frame` | 0.36m / 1.40m | 0.68m / 1.40m (too high) | NEEDS WORK, exit 2 |
      | `--frame --lens` | 0.00m / 0.01m | 0.01m / 0.02m | OK, exit 0 |

      with the line above it reading `lens  k1 -0.0507, lines bow 4.4px and
      straighten 1.26px -> 0.39px (3.2x) over 7 runs, +/-0.0001 across them
      (confident)`. The clicks were never the problem — which is what the
      picker's three-candidate verdict from 2026-08-12 could only suggest and
      can now demonstrate. The advice block's first bullet moved with it: it
      used to say the fix is a narrower lens setting and a fresh frame, and it
      now says re-run with `--lens`, because if the paint in that frame bows the
      lens is measurable from it, and if it does not bow the answer comes back
      honestly empty.

      The coefficient is saved with the calibration and restored by
      `Calibration.load`, so `cv/pipeline.py` — which reaches every calibration
      through that one door — corrects for a wide lens with nothing further to
      wire and nothing to remember to switch on.

      **What none of this has been shown to do.** Every figure above is a
      synthetic camera. `cv.distortion` has never seen a real lens, and a real
      one brings tangential distortion, a decentred optical axis and rolling
      shutter, none of which one radial coefficient can express. What it can
      honestly claim today: on a frame with visibly bowed paint it recovers the
      coefficient to within 0.004 and turns an unusable calibration into a
      usable one, and on a frame that cannot support that claim it declines and
      costs the calibration nothing — pinned by
      `test_a_refusal_costs_the_calibration_nothing`, which demands the two
      saved homographies be byte-identical rather than merely close. That test
      is what makes the flag safe to leave on, and leaving it on is what makes
      it useful on a day nobody thought to check the lens.

      `SCHEMA_VERSION` unchanged at 14, and nothing served changed, so the
      version stamp stays at 96. 731 pure JS · 34 smoke · 146 emulator · 1210
      Python (38 new).
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
- [x] **The tool that has to work with no signal was waiting for the server**
      (2026-08-16). The comment at the top of `tagging.js` has said since the
      Firestore migration that *"it has to work with no connectivity"*, and the
      writes were built for exactly that — batches, which queue, rather than
      transactions, which fail. Then every one of the six handlers `await`ed the
      commit promise before touching the screen. **That promise is about the
      server**, and on a touchline the server is routinely minutes away.

      Measured, with the emulator stopped mid-match and the page left open:

      | tap | what the tagger saw |
      |---|---|
      | corner | "Just recorded" still showed the previous tag; nothing in the undo stack |
      | substitution | the sheet never closed, Confirm stayed live — **three presses queued three substitutions** |
      | kick-off | nothing: no clock, no view change, on the tap the whole match hangs off |
      | undo | dead for anything tagged since the signal went |

      None of it was data loss — every write was queued and the sync chip
      reported "1 waiting", "2 waiting", "4 waiting" correctly throughout, which
      is the one part of this that was already right. It was worse than data
      loss in one way: the screen said the tap had not registered, and the
      remedy a person reaches for is to tap again.

      The rule now, in `sendWrite` and applied in all six places: **the local
      write is the fact, and the server acknowledgement is the sync chip's
      job.** Firestore has the write in IndexedDB the moment it is issued. A
      rejection — bad data, a rules failure — is a *correction* rather than a
      delay, so it undoes the optimistic update and says so loudly.

      Two smaller things fell out of it. `state.seq -= 1` on failure is gone:
      offline, several taps are in flight at once, so a rejection arriving after
      three more tags would hand the next tap a sequence number one of them had
      already used, and the log is written with `setDoc`, which overwrites. And
      the substitution now moves `state.roster` in memory rather than re-reading
      it afterwards, which is what makes the second press of Confirm hit the
      `if (!outEntry.isActive) throw` guard instead of writing the change again.

      Re-measured after the fix, same conditions: sheet closes on the tap, the
      strip names the substitution, the undo stack has it, two further presses
      of Confirm do nothing, the roster shows one stint closed at 2730 and one
      opened at 2730, and Undo reverses all of it while still offline.

- [x] **Five player lists that were not buttons, and a squad that did not fit**
      (2026-08-15). Every picker in the tool a match day depends on — the
      starting eleven, the scorer, the assist, and both halves of a
      substitution — was an `<li>` with a click handler on it. Not focusable,
      not reachable from a keyboard, and read out by a screen reader as a line
      of text rather than as something you can press. `assets/timeline.js`
      states the opposite standard for its own marks — "real buttons rather than
      styled spans, so the whole thing is reachable from a keyboard" — and the
      one place that did not follow it was the tool every number in this system
      comes from.

      One `pickRow` builds all five now, with the padding and the border on the
      button rather than on the `<li>`, so the tap target is the whole card and
      not a word inside it. `toggle` is the distinction between the two kinds of
      picker here: choosing a starter or the player going off is something you
      change your mind about, so it carries `aria-pressed`; choosing the scorer
      closes the step, so it is a plain button and a pressed state would
      describe a control that is already gone.

      Fixing that exposed a second bug the first was hiding. Both toggling
      pickers rebuilt their whole list on every tap, which throws away the
      button that was just pressed — invisible while these were unfocusable list
      items, and the first thing anyone tabbing through them would hit. They
      repaint in place now, so a keyboard reader keeps their position
      mid-substitution, with the clock running.

      **And the layout.** One name to a full-width row put a fourteen-player
      squad four screens deep on a phone and left 700px of nothing beside every
      name on a tablet — for a job done minutes before kick-off by someone who
      is also being talked to. `auto-fill` at 250px is one column on a phone and
      three across a landscape iPad: the whole squad and the save button on one
      screen, and the eleven-name scorer picker with no scrolling at all. The
      BENCH label went with it — fourteen repetitions of the default, taking the
      width that was pushing half the names onto a second line, under a heading
      that already says everyone you don't tap becomes a substitute.

      Driven end to end at 1024×768 against the emulator: lineup, kick-off, a
      goal with side, scorer and assist, and a substitution. Read back out of
      Firestore the log is in clock order and the change closed one stint at
      59.062s with the other opening at the same instant.

      **Unexplained, and recorded rather than guessed at:** the very first
      lineup save of that session came back `PERMISSION_DENIED` from the roster
      rule. It did not recur across two further clean runs, and a probe from the
      same page — a single write, then batches of 5, 9, 10, 11 and 14 against
      the same rule and the same account — passed every time. So the rule
      accepts this shape from this user, and what failed was that one commit
      rather than the code path. Worth watching if it turns up on a real tablet.

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
- [x] **The whole tool was dead for eight days, and nothing could have
      noticed** (2026-08-15).

      Commit `db4abc5` replaced `updateOnlineIndicator` with
      `updateSyncIndicator` — deleting the function and the two `window`
      listeners that used it, and **leaving the call in `init()`**. From that
      commit on, `live-tagging/tagging.js` threw a `ReferenceError` on the
      second statement of its entry point. Nothing after it ran: no sign-in
      handler, no match picker, no lineup. A signed-in coach opening the tablet
      saw the signed-out screen and had no way forward. The fix is one word.

      That commit's own message ends *"Verified at 768: all four chip states,
      correct colours, the label hidden when everything is up"* — the CSS states
      were checked in isolation and the page was never opened.

      **Why no test caught it, which is the part worth keeping.** Three
      independent reasons, and none of them is carelessness:

      - `tests/video.test.js` can only import modules that import nothing, and
        `tagging.js` imports Firebase.
      - the emulator suites drive a Firestore client directly; they never load a
        page. `tests/flow.test.js` covers the *offline queue* this very commit
        added — through the SDK, not through the tool.
      - `pyflakes cv/ tests/` catches exactly this class of error on the Python
        half every CI run. **The JavaScript half has no equivalent.**

      A dependency-free static check was attempted and **abandoned, on purpose**.
      Finding bare `name(` call sites and subtracting what a file declares needs
      a parser, not regexes: the first run produced false positives in fourteen
      of twenty-nine modules, mostly from template literals containing `${}`
      throwing off comment and string stripping. A check that cries wolf in half
      the codebase teaches everyone to skip it, which is the same failure as
      having none. `esprima` is present transitively but is stuck at ES2017 and
      cannot parse `?.` or `??`, which this codebase uses throughout.

      So the honest state was: **catching this class of bug in CI needs a real
      parser (acorn/eslint as a devDependency) or a headless browser, and
      neither is in the repo.** Until one is, the rule is the one that actually
      worked — open the page. Every one of the seven was swept afterwards and
      the other six render fine.

      **Closed on 2026-08-16, and by neither of those two things.** The third
      option was not considered here: don't analyse the module, *run* it. See
      Testing Strategy — `tests/smoke.test.js` loads all seven pages under
      `node --test`, and reinstating this exact bug fails it with
      `ReferenceError: updateOnlineIndicator is not defined`.

      The tool itself was then driven end to end at 768px against the emulator:
      match picker, eleven tapped into a lineup, kick-off, a goal with side and
      scorer and assist, a corner, a substitution, half-time, second-half
      kick-off. Read back out of Firestore, the log is in clock order, the
      substitution closes one stint at 59.779s and opens the other at the same
      instant, and `halfTimeClockS` is written at the restart — the anchor the
      whole match-clock map depends on.

      One thing left alone and worth recording: the lineup, scorer, assist and
      substitution lists are `<li>` elements with click handlers, so they are
      not focusable and not announced as actionable. `assets/timeline.js` states
      the opposite standard for its own marks — *"real buttons rather than
      styled spans, so the whole thing is reachable from a keyboard"*. Five
      lists, and a demo run on a tablet, so it is noted rather than fixed here.

      **That held for seven hours.** Written at 16:31 on 2026-08-15; fixed at
      23:24 the same evening in `c7bde72`, written up one entry above this one
      as *"Five player lists that were not buttons"*. This page said otherwise
      for two days, which is how a note meant to be honest about a limit turns
      into a wrong claim about the code — the paragraph aged out and nothing
      went back for it. Left standing rather than deleted, because the shape of
      the mistake is worth keeping: *noted rather than fixed* is a promise, and
      a promise with no date on it is the kind this document has to check.
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
- [x] **[Stretch]** Automatic pitch-line detection
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
- [x] **Grading a calibration on evidence the human did not supply** (2026-08-19).
      Every number this repo has printed about a calibration was measured on the
      coach's own clicks. `Calibration.error` is fitted and graded on the same
      eight points, so it is optimistic by construction; `holdout_error` is
      honest and still only knows what the human typed in. The painted lines in
      the frame are the first evidence in this loop that nobody supplied.

      `cv/lines.py` finds them — an HSV green field mask, white paint inside it,
      Canny, probabilistic Hough, then a merge that puts the merged segment on
      the paint's *centre* rather than on one of its two Canny edges — and
      scores them against a model of the pitch built from the Laws constants
      already in `cv/pitch.py`. `refine` runs ICP against that model, and
      `--frame` / `--refine` / `--overlay` put all of it behind
      `python -m cv.experiments.calibrate`.

      **What it buys.** Twenty independently jittered click sets at three pixels
      of jitter — a coach concentrating on a phone screen — had a typical worst
      landmark of 1.50m, sitting exactly on the bar `CalibrationError.is_usable`
      sets, so half of them would have been rejected. Refinement improved all
      twenty, made none worse, and brought that figure to 0.20m. At five pixels:
      3.08m → 0.59m.

      **What it cannot buy, which is the part worth keeping.** A painted line
      constrains a fit only perpendicular to itself. A homography is free to
      slide *along* the touchlines, and every sample point stays on its own line
      while it does. Measured: one eight-pixel click set refines to a line-fit
      median of 0.08m — a better score than the true calibration manages, since
      rasterised paint has width and the truth has none — with its worst
      landmark 17.10m from where it belongs. The median saw nothing at all. The
      90th percentile objected, at 3.13m, and it was the only line-based number
      that did, which is why `LineFit.is_usable` is coverage-and-median-and-tail
      rather than the median-and-coverage it was first written as.

      So a line-fit tick means *this calibration is on the pitch*, never *this
      calibration is accurate*. Across sixty refinements every calibration the
      line fit passed was within 2.93m at its worst landmark — about twice the
      1.5m the click-based check promises, and now pinned by a test so that a
      change which widens it fails instead of shipping.

      Two things found by writing the tests rather than the code. Gaps are not
      bridged: two fragments of one touchline with a player-sized hole between
      them stay two segments, because a merged segment would be sampled across
      the stretch nobody saw and those samples counted as paint that was seen —
      the same rule `BallPoint.observed` already draws. And the penalty arcs
      were missing from the model: metres of real paint the detector finds and
      the model then fails to explain, depressing coverage on every frame
      containing one. An unmodelled line is indistinguishable from a wrong
      calibration.

      `SCHEMA_VERSION` unchanged at 14, and nothing served changed, so the
      version stamp stays at 96. 731 pure JS · 34 smoke · 146 emulator · 1172
      Python (47 new).
- [x] **The pitch measured from the clicks, and a way to be done** (2026-08-20).
      The size a coach types scales every distance the software will ever
      report, and until now the two boxes for it sat in a card that `loadImage`
      hides the moment a picture loads — so it could not be changed while
      clicking, and a wrong one looked exactly like bad clicking. The boxes moved
      into the workspace, and `measureField` now volunteers the answer: a coarse
      then fine search over (length, width) for the pair whose homography
      reprojects the clicked points with the least error in **metres**, which is
      the unit the page grades itself in.

      It refuses more often than it answers, on purpose. Under five points there
      is nothing to measure — a homography maps four points to four points
      exactly whatever size you assume. Corners, the halfway line and the centre
      spot all scale with the pitch, so a set made only of those fits every size
      equally well and both dimensions are refused; adding one penalty box
      corner, penalty spot or goalpost — fixed distances in the Laws —
      recovers the pitch exactly. Each dimension is refused independently, by
      whether its profile interval touches a search bound or spans more than a
      quarter of the range. The full study, including the coach's own frame
      going from 1.70m to 0.49m average without a click moving, is in the log
      above.

      Alongside it, five readiness checks and a summary line that says *Done.
      This calibration is ready to save.* or counts what is left. The fifth is
      one no software can make — the coach confirming the yellow outline sits
      on the paint — and clearing the points revokes it. The save button never
      disables; it changes from *Save calibration* to *Save anyway*.

      `SCHEMA_VERSION` unchanged; the export gains a `quality` block that
      `from_picker_export` ignores. Version stamp 98. 739 pure JS · 35 smoke
      · 146 emulator · 1210 Python.
- [x] **A magnifier, so the coach can see the pixel they are aiming at**
      (2026-08-20). The page grades its own clicking in metres and hands the
      number back, but it never gave anyone the means to click better. Measured
      in a browser: a 1920×1080 frame displays in the calibrate column at
      704×396 — **0.367×**, so one screen pixel is nearly three source
      pixels, and on a phone the finger covers the pixel it is aiming for. The
      earlier study put four pixels of click jitter at 0.21m of reported error.

      A loupe, not a pan-and-zoom canvas. The landmarks are scattered to the
      four corners of the frame, so a zoomed view would mean panning between
      every one of them; the job is *click this one, now the next one*. Press
      and hold opens it, dragging moves it, and the point is stored on release
      — so the finger can be nowhere near the target at the moment that
      matters. It shows `LOUPE_SPAN = 44` source pixels across its width at
      nearest neighbour, because a smoothed magnifier invents pixels between the
      real ones and the real ones are the entire reason it was opened. On the
      desktop column that is 3.82× life size against a 0.367× picture: about
      ten times what the eye had before.

      The overlay and the placed points are drawn inside it at one over the
      zoom, so they come out the same thickness there as on the picture behind.
      That makes the magnifier the only place on the page where the fifth
      readiness check — the coach confirming the yellow outline sits on the
      paint — can be answered honestly rather than guessed at. Points are
      drawn hollow inside it: at four times life size a filled dot covers the
      very paint it is being lined up against. And the crosshair has a hole in
      the middle with a yellow box the size of one source pixel, so *the exact
      pixel* is something visible rather than a claim.

      Then arrow keys, one source pixel a press and ten with Shift, on a
      focusable stage — the finish that a magnifier alone cannot give, since
      the last pixel of a placement is below the resolution of any pointing
      device a coach owns. Re-picking a landmark already placed re-opens the
      magnifier on it and aims the keys at it, which is the whole of *that one
      is slightly off*.

      Two things a real browser found and no test could. `touch-action: none`
      is applied only while a landmark is selected, or a swipe across the
      picture would stop scrolling the page for the other 99% of the time. And
      the magnifier is sized from the stage on every redraw rather than fixed:
      the anchor parks it in whichever half of the stage the aim is not in, so
      it only stays off the paint while it fits inside that half, and a fixed
      168 does not fit the 341px picture a 375px phone gives it. Swept 121 aim
      positions at 375, 768 and 1280 — zero covered the aim, zero hung off
      the stage.

      The geometry is pure functions taking numbers — `loupeAnchor`,
      `loupeSize`, `nudge` — because `tests/dom-shim.js` returns all zeros
      from `getBoundingClientRect` on purpose, and a made-up rectangle would let
      a test claim an accuracy nothing measured.

      `SCHEMA_VERSION` unchanged. Version stamp 99. 739 pure JS · 36 smoke
      · 146 emulator · 1210 Python.

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
- [x] **Put the goalkeeper's numbers on a screen** (2026-08-18). `cv/keeper.py`
      had computed a full block per keeper since the pipeline's second month and
      `cv/publish.py` had been writing it to `cvStats/summary.keepers` the whole
      time. `readCvStats` spreads the summary document, so it was sitting on
      `state.match.cv.keepers` unread on every match ever published. Found by
      the field sweep of the same day, which is the only reason it was written
      down at all — Phase 6 ticks keeper *detection* and nothing anywhere asked
      for keeper *display*.

      New `keeperStatRows` in `assets/report.js`, and a `keeping` group in
      `STAT_TYPES` between Defending and Shape. It goes out through
      `teamStatRows`, so **not one line of `coach/coach.js` or
      `coach/index.html` changed**: the block inherits bars, confidence marks,
      the em dash for an absent figure and the desktop column layout from the
      path every other stat group already takes. Eleven rows on the sample —
      saves, save percentage, claims, sweeper actions, furthest from goal,
      distributions, kick/punt/throw accuracy, average kick, average punt —
      taking the coach's match report from 36 rows in six groups to 47 in seven.

      **Shots faced, shots on target faced and goals conceded are deliberately
      left out.** They are the opposition's shots and the opposition's goals,
      already on the screen in Attacking with the columns the other way round.
      Printing them again under a keeper's name would look like a second
      measurement and would be the same one; the group's note says where they
      are instead.

      **Only the rate with the workload divided out of it is marked good or
      bad.** A keeper with eight saves was either excellent or abandoned by the
      ten in front of him, and a count cannot tell those apart — so saves,
      claims and sweeper actions are drawn uncoloured, and `better: 'high'`
      belongs to save percentage and the three distribution accuracies alone.
      The metre rows carry `shapeConfidence(calibrationErrorM)` rather than the
      event band, because 21 metres is a claim about the homography and not
      about the event detector.

      **Either keeper may be the missing one.** `identify_keepers` works per
      team, so a side whose keeper wore a colour close to his outfielders can be
      the only side without one. The rows draw from whichever were found and
      leave the other column empty, which is what `groupStats` keeps a one-sided
      row for: dropping the block would take the keeper who *was* found down
      with the one who was not. Verified against a `team_b`-only array — eleven
      rows, our column empty throughout.

      **A defect fell out of writing the display, and it was in Python.**
      `keeper_reports` only calls `_count_positional` and `_collect_distribution`
      when something said which goal that keeper defends; without it, claims,
      sweeper actions, the furthest sweep and distributions kept their `0`
      defaults and were published as measurements. A keeper nobody looked at
      read as a keeper who claimed nothing and never left his line. New
      `end_known` flag on `KeeperReport`, and `to_json` sends null for all four
      without it. The furthest sweep is null separately whenever there were no
      sweeper actions, because a maximum over an empty set is not 0.0 metres —
      and `keeperStatRows` reads the *count* rather than the distance for that
      row, so a report published under the old schema cannot draw a keeper who
      held his line as one who came out and got exactly nowhere.
      **`SCHEMA_VERSION` 13 → 14**, and the first version bump here that is not
      additive: the same four keys changed meaning.

      **Not on the half-time page**, and that is a decision rather than an
      omission. `cvTallies` builds its own eleven rows and has never carried the
      Shape or Phase-of-play groups either; the page is read standing up in
      three minutes and eleven keeper rows would nearly double it. The rule the
      half-time page follows is that a row earns its place by changing what the
      coach says in the next ninety seconds, and "our keeper's average kick was
      39 metres" does not.

      `assets/sample-report.js` fixed in the same pass, as the item asked: the
      one-line stub had `track_id` where the payload has `track_ids` and was
      missing a dozen fields, so the preview was never exercising the real
      shape. Every figure in the replacement is read off the two shot maps
      already in that file rather than invented — our keeper faced the four in
      `THEIR_SHOTS`, theirs the six in `OUR_SHOTS` — and every fraction is
      rational against its own count (7/14, 8/9 ours; 7/20, 2/5, 3/4 theirs).
      Our keeper never punted, so the preview shows the case this feature most
      needed to get right: a dash against their 40%, not a nought per cent.

      The item's last sentence is honoured in the **Shape** group's note rather
      than the keeping one, because that is where a reader is looking at the
      numbers it affects: the four shape figures now say out loud that the
      goalkeepers are excluded from all of them, and why — `cv/metrics.py` has
      documented the reason in Python for months and no screen ever said it.

      Ten new tests in `tests/video.test.js`, five in `tests/test_keeper.py`.
      **725 pure JS · 28 pages · 145 emulator · 1125 Python**, all green.
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
- [x] **A whistle nobody tagged is not a whistle at second zero** (2026-08-15).
      Not a roadmap line — found by pulling the thread on a bug in the
      substitution block shipped an hour earlier, which turned out to be one
      instance of a defect that had been in the minutes arithmetic since it was
      written.

      `aggregateMatch` derived the end of the match as `max(matchClockS)` over
      the tag log, **defaulting to zero**. And `setLineup` writes a starter's
      `{inS: 0, outS: null}` the moment a lineup is set, hours before kick-off.
      So the most ordinary demo there is — *film a match, run the pipeline,
      nobody ran the tablet* — produced a full roster of open stints against a
      whistle at second zero.

      What that did, in order:

      - `minutesFrom` returned **0 for every starter**.
      - `trackedCoverage` guards on `matchEndS != null`, which zero passes, so a
        starter came out with `onPitchS` of 0 and `watchedS` of 0. `share` then
        fell to null and `coverageNote` — the sentence that exists to explain a
        shortfall — **said nothing at all**.
      - `cvReportFields` published `cvMinutesOnPitch: 0` beside a real
        `cvDistanceM` of four kilometres. Three numbers on one card that cannot
        all be true, and two of them written into a document.
      - `rankRosterForCluster` scored every stint overlap at zero, so the
        cluster picker could not offer the right player first — on exactly the
        run where the picker is the only tool a coach has.
      - and the player portal read `minutesPlayed` of 0 and told a student, on
        their own report, that they had been **"an unused substitute"**.

      That last one is the reason this was worth a commit on its own. Everything
      else is a wrong number; that is a wrong number addressed to a sixteen-year-
      old about whether they played.

      **The fix is provenance, not a better default.** `whistleFrom(log)` returns
      the end of the match *and how it was arrived at*: a tagged `full_time`, the
      last thing anybody tapped, or nothing. Nothing is null, never zero.

      **A closed stint stays knowable without a whistle.** A player who came off
      on 60 minutes played sixty of them whether or not anyone tagged full time,
      so only an *open* stint needs the end of the match. Verified in the
      browser on a fifteen-player untagged squad: five real numbers with bars,
      ten em dashes without — where before it was fifteen zeros.

      `minutesPlayed` stays a **number** in the published document, because
      `firestore.rules` requires one and every report already written carries
      one. `minutesKnown` beside it says whether that number is a measurement,
      and needed **no rules change** — the `playerReports` block has no
      `hasOnly`. Absent reads as true, which is both the old behaviour and the
      right default for the reports written before the question was asked.
      Season totals count only the matches with a clock, since a placeholder
      zero in a per-90 denominator makes every rate above it read high.

      Two things fell out of it that were wrong on their own. `stintOverlapS`
      closed an open stint at second zero rather than at the end of the window
      asked about — "never on the pitch" rather than "on from here". And
      `tests/flow.test.js` carried a **hand-copied duplicate** of `minutesFrom`
      under a comment reading *"mirrors minutesFrom() in assets/db.js"*, with
      four tests pinning the copy rather than the code. `minutesFrom` moved into
      the zero-import `report.js` for the same reason `playerTimeline` lives
      there, and its tests moved with it — out of a suite whose file-scope
      `beforeEach` charges every test a full `clearFirestore` whether it touches
      a database or not.

      618 pure JS · 145 emulator · 1008 Python. The emulator count fell by four
      because those four were pure arithmetic in the wrong file.
- [x] **Three follow-ups from verifying the last change in a browser**
      (2026-08-15).

      **A match still being played has no final whistle to have missed.** The
      note shipped an hour earlier read *"nobody tagged the final whistle"* on
      the coach's view of a match whose status was `halftime` — an accusation
      about something the coach has not had the chance to do yet, and it would
      have fired at every half-time of every match, which is exactly how a
      warning becomes wallpaper. Gated on the match being over. The *no log at
      all* case is deliberately **not** gated the same way: a tablet that has
      recorded nothing by half-time is a problem the coach can still fix.

      **The half-time page was re-verified end to end**, which is where this
      was found — it had been changed in the same commit and never opened.
      Against a tagged first half stopped at the interval it reads correctly:
      46′ for the ten who never came off, 32′ and 14′ either side of the
      substitution on 32 minutes, every tile agreeing with the log.

      **A shot with no position is not a shot at the corner flag.** The shot map
      read `Number(mark.x_m) || 0`, so a positionless mark would have been drawn
      at (0, 0) and been indistinguishable from a real shot from the goal line.
      Nothing produces one — `shot_marks` in `cv/report_json.py` drops them —
      but an invented point on a picture a coach reads as measurement should be
      impossible rather than merely unused. **The first attempt at the guard
      reproduced the bug**: `Number.isFinite(Number(null))` is `true`, because
      `Number(null)` is `0`. It now demands an actual number.

- [x] **The intermittent test failure was a timer nobody cancelled**
      (2026-08-15). `tests/flow.test.js` had been failing on roughly half of
      runs with a 499 *"call already cancelled"* out of `clearFirestore` in a
      `beforeEach`, on a test that touches none of the machinery involved. It
      was written off as an emulator flake.

      It is not. `until()` in the offline-queue suite created a five-second
      timeout and **never cleared it**. That test calls `until()` four times, so
      four stray timers fired five seconds later — into whatever test happened
      to be running by then — each calling `stop()` on a listener already torn
      down. `erasing a player` starts inside that window, and the cancelled
      `Listen` stream raced its own `clearFirestore`.

      Worth recording that the first diagnosis was **wrong**. Failing runs
      carried `RESOURCE_EXHAUSTED: Received message larger than max
      (2158073886 vs 4194304)` — a corrupted gRPC length prefix — and passing
      runs did not, which looked like a clean correlation across three runs. A
      fourth run failed with no such error at all. The byte-count garbage was a
      variant symptom of the same stream teardown, not its cause, and stopping
      at the first correlation would have shipped a plausible story about a
      Firebase bug instead of a fix.

      Three consecutive clean runs after clearing the timers, from a baseline of
      about one failure in two. 622 pure JS · 145 emulator · 1008 Python.

      **This entry claimed more than it had earned, and the next day disproved
      it.** Three clean runs against a coin-flip failure is p = 0.125 — not
      evidence, a shrug. The 499 came back on 16 August and again on the 17th,
      and the real cause turned out to be the offline-queue suite's *streams*
      rather than its *timers*: cancelling the strays was a genuine fix for a
      genuine bug and simply not this one. Settled in the Testing Strategy
      section above, on 30 and 15 run arms rather than three. The lesson worth
      keeping is about the arithmetic, not the bug: a fix for an intermittent
      failure needs enough runs that a clean streak would be surprising.
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
- [x] **What the football looked like either side of each change** (2026-08-15).
      The last unbuilt line of the post-game tactical catalog, and the only one
      that pays the tablet back. Somebody stands at the side of a pitch for
      ninety minutes tapping substitutions into an iPad; until now the whole of
      what those taps bought was a minutes-played column.

      **The heading is "Around each change", and the word "impact" appears
      nowhere.** That is the finding, not modesty about it. A coach makes a
      change *because* of what is already happening — a side being overrun
      brings on a defender, a side chasing brings on a forward — so the change
      and the reason for it arrive together and one match cannot separate them.
      The opposition also made changes, and none of theirs are in anybody's log.
      The scoreline moved on its own. Every one of those confounds a
      before-and-after, and the note says so in a sentence that is not
      conditional on anything: *"this says what the football either side looked
      like — it does not say the substitution did it."*

      **Three ways a window lies, all of them fixed by refusing rather than by
      adjusting.** Nine minutes of football holds more of everything than six,
      so both sides are cut to the shorter — not converted to per-minute rates,
      because the noise test needs counts and a rate would hide that a
      comparison rests on eleven events. A second change six minutes later means
      the ten minutes after the first *are* the ten minutes around the second,
      so windows stop at the neighbouring change and both are dropped if what is
      left is under four minutes. And events only exist where the pipeline was
      looking, so a window running past the end of a clip finds nothing — a fact
      about the clip that reads on screen as a team that stopped playing.

      **Half-time is a wall, not a window.** The most common change there is, and
      the least measurable: what sits between the two halves of that comparison
      is fifteen minutes and a team talk. Those changes are listed, because a
      coach should see that they happened, and never scored. The same wall clips
      ordinary windows — ten minutes either side of a change on 43 minutes would
      otherwise compare the end of one half against the start of the next.

      **Nothing is scored at all without a placeable clock.** Second-half stints
      are match minutes and second-half events are video minutes, and without the
      second-half kick-off saved against the footage the offset relates them
      wrongly by the whole interval — ten to fifteen minutes, in the direction
      that slides a window quietly off the football it claims to describe. A
      first-half clip needs no anchor and is scored normally, which is the case
      the half-time page cares about.

      **What the bar can and cannot resolve, measured.** The reading is our share
      of the on-ball events the video found, and whether it moved is a
      two-proportion test at the same two-sigma bar `insideNoise` uses — it has
      to be a different test, because the claim on screen is about a share
      before against a share after, not about one window's two sides. At an even
      split the smallest swing that clears it is **32 points on 20 events a
      window, 18 on 60, 14 on 100, 12 on 150, 7 on 400**. Ten minutes of
      football is in the low hundreds at best, so **this calls most changes a
      draw and is right to**: a tool that flagged a ten-point swing off a hundred
      events would be pointing a coach at a coin toss with a teenager's name
      attached. Shots are printed as counts and never compared, because ten
      minutes holds one or two.

      Found by the browser and not by the tests: `matchEndS` comes off the tag
      log, so a match nobody tagged has one of **zero** — which is not a final
      whistle, it is the absence of one. Taken literally it is a whistle before
      kick-off, and it deleted every player who came off (their `outS` was not
      "before" it) and clipped every second-half window to nothing. The block
      rendered four changes where somebody came on and nobody went off, and four
      refusals reading "too near kick-off or the final whistle". Now pinned.

      Previewable without footage: `sampleSubRoster` and `sampleSubEvents` are a
      match with four changes chosen to show one of each thing the block can
      say — the interval, a swing large enough to survive, a draw, and a double
      change grouped into one row and refused. Kept separate from
      `samplePassEvents`, which is one team's passes over half an hour and right
      for a passing network; this needs both teams and a clock that reaches past
      half-time.

      604 pure JS · 149 emulator · 1008 Python.
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

      **The first sheet says what it is.** `printStamp` puts the subject, the
      fixture, and the date it was *printed* along the top edge. A page that has
      left the app has left everything that made it readable — the navigation
      saying whose account it came from, the tab title saying which match — and
      three months later it is a piece of paper with a teenager's name and some
      numbers on it. The printing date is deliberately not the match date: a
      sheet re-printed after a coach corrected the review says something
      different from one printed on the night, and nothing else tells them
      apart.

      **This read "every sheet" until 2026-08-17, and that was an overclaim.**
      `.print-stamp` is one static `<p>` at the top of the document
      (`coach/index.html:383`, `player/index.html:205`), so it prints once, on
      sheet one. The sample coach report measures 9.52 Letter sheets, which
      leaves **nine sheets carrying a teenager's name and some numbers with
      nothing on them saying where either came from** — the exact artifact the
      paragraph above says the stamp exists to prevent, for every page of it but
      the first.

      It is written down rather than fixed because the fix cannot be checked
      here. Chrome supports no `@page` margin boxes, so the only recipe for a
      running header is `position: fixed`, which does repeat on every sheet but
      reserves no space for itself: content on sheets two and later would print
      underneath it, and how much it swallows is visible only in a print
      preview, which the browser tools here do not expose. A header that might
      be printing over the numbers is a worse artifact than one labelled sheet
      followed by eight unlabelled ones.

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
- [x] **A compensator that restores less than its forward path changed**
      (2026-08-19). Every optimistic write in the tagging tool moves the screen
      first and tells the server after, which is the only shape that works at a
      field with no signal. The price is `sendWrite`'s `undo` — the function
      that has to put the screen back when the server answers *no* rather than
      *later*. Six call sites carry one. **Two of them restored less than their
      forward path had changed**, and a third piece of bookkeeping disagreed
      with the record it was meant to match.
      **Undo restored two of the four things it moves.** `undoLast` changes the
      entry list, the score, the roster and the period; its compensator put back
      the first two. A refused undo of a substitution therefore left
      `state.roster` reverted against a record that still held the sub — and
      `activeRoster()` is exactly who the event sheet offers a goal to, so the
      next goal was attributed to a player the record has on the bench. A
      refused undo of a period tap left `state.match.status` reverted, which the
      clock, the period button and `inPlay()` all read. Fixed by
      `undoRestorePoint`, which captures the whole restore before anything moves,
      because by the time a refusal comes back the screen has moved on.
      **Half-time restored the button but not the clock.** The tap freezes the
      clock, relabels the period and writes the reading the second half is
      anchored to; the compensator restored the status alone, leaving a clock
      running against a record saying the half never ended and every later
      timestamp wrong by the length of the break.
      **And undo's version arithmetic was two behind the record.** `undoEntry`
      writes `version + 2` — one for the sub, one for taking it back — while
      `putBack` restored the pre-sub version, so the tablet's copy was two
      behind the optimistic lock at `firestore.rules:403`. Honestly:
      `openSubSheet` re-reads the roster from Firestore every time it opens, so
      this was masked in practice, and it has **no test** because it has no
      screen-level observable. It is corrected as bookkeeping, not as a shipped
      defect.
      None of this had ever been reachable in a test, because
      `tests/fake-firebase.js` could refuse nothing: `goOffline` models a write
      nobody has answered yet, and there was no way to model one answered with
      no. New `refuseWrites` / `acceptWrites` reject before anything lands, which
      is what the real SDK does with a rules failure — it is still not a rules
      engine, and `tests/rules.test.js` remains the only thing that can say
      *which* writes the server would refuse. Two page tests use it, and both
      were run against the pre-fix file first: the roster one reports the goal
      sheet offering `7Rae Nkemelu / 14Sam Okonjo` where the record has Rae on
      the bench, and the period one reports `half-time` on the label and
      `Half-time` on the button where the record says the first half is still
      running. **731 · 34 · 146 · 1125 green.**
- [x] **The third way a write and a read fail to meet: a field nothing ever
      asks for** (2026-08-19). The two guards below catch a page reading a field
      nothing writes, and a pipeline figure no page draws. This one is the
      quietest case — a field the tablet or the dashboard writes to Firestore on
      every match, stored and replicated and consulted by nobody.
      `firestore.rules` is the writer of record and the only honest one: a field
      not named in a `hasOnly` or a `changed` list cannot reach Firestore at all,
      whatever the client passes. That is **75 writable fields across 11 document
      shapes**, none of them listed by hand, checked against every property
      access, `where`/`orderBy` string and Python field name the site and the
      pipeline contain. **Three had no reader.** The scan only works because a
      type check is not counted as one: `request.resource.data.isStarter is bool`
      constrains the write's shape and nobody ever looks at the boolean, so
      counting shape constraints marks every field alive by construction — the
      first version of the sweep did exactly that and found nothing at all. A
      *comparison* still counts, because restricting a vocabulary is a decision
      made on the value.
- [x] **A saved lineup was safe in Firestore and unreachable on the tablet**
      (2026-08-19). The first of the three dead fields, and the only one that was
      a bug. `roster/{p}.isStarter` was written by `setLineup` on every match and
      read by nothing, and it was covering a hole rather than sitting idle: the
      picker painted from `player._starter`, an in-memory flag set by tapping. On
      a reload `onMatchChosen` saw a non-empty roster, went straight to kick-off,
      and never showed the lineup block again — on a screen that says in as many
      words *"you can change this later"*. A coach who spotted a wrong name after
      closing the tablet between the team sheet and the whistle had one move
      left: kick off and substitute at clock zero, which is precisely what makes
      `stints[0].inS === 0` useless as a starter test and precisely why this
      field is not derivable from the stints. `restoreLineup` is the reader now,
      guarded by `scheduled()` at every call site because after kick-off `stints`
      is a record of who was on the pitch and re-saving would flatten it.
- [x] **And the correction it enabled would have been refused** (2026-08-19).
      Making the picker reachable twice exposed a second defect underneath the
      first, which nobody could have hit while it was unreachable: `setLineup`
      sent a create-shaped write with `version: 0` every time, and the roster's
      `allow update` rule requires `version == resource.data.version + 1`. The
      second save was rejected by the rules, silently, behind a promise the coach
      was waiting on. `setLineup` now takes the roster as it stands and branches:
      `set` for a player with no document, `update` for one that has it, carrying
      only the four keys `changed(['isStarter','isActive','stints','version'])`
      admits — so correcting who starts cannot quietly overwrite a name or a
      shirt number edited since. Pinned at both layers: a page test that reloads
      into a saved eleven, corrects it and checks the versions, and an emulator
      test that watches the real rules refuse the old write and accept the new
      one. **32 page tests, 146 emulator tests.**
- [x] **A field whose name was a lie about what it held** (2026-08-19). The
      second dead field. `staff/{s}.joinedAt` was written unconditionally by
      `saveStaffProfile`, which runs on every sign-in to backfill the other three
      keys — so it recorded the last time somebody opened the dashboard under a
      name claiming it was a joining date. Nothing read it, which is the only
      reason nobody had been misled by it yet; a field with a wrong value is
      worse than a missing one the moment it reaches a screen. It is now
      read-before-write, following the `saveHint` precedent five lines below, and
      only the first save may set it. Worth saying plainly: this makes it *pass*
      the scan while it still appears on no screen, because reading a value to
      preserve it is a read. Whether it should be shown is the display guard's
      question, not this one's.
- [x] **The one that stays dead, and what the excuse costs** (2026-08-19).
      `log/{entryId}.tappedAt` is genuinely read by nothing and is meant to be.
      It is the only record of when a tap happened rather than when the write
      landed — `createdAt` is a server timestamp, so an entry the SDK queued
      while the tablet was off the network is stamped minutes after the moment it
      describes, and nothing else on the entry could ever say so. Its excuse now
      lives in the test as an entry that must name both the shape and the field,
      and a second assertion fails if an excused field gains a reader or stops
      being writable — an excuse may not outlive its reason. The limit is the one
      the display guard already states: this cannot tell which object a `.key`
      was read off, so short generic names are effectively unchecked, and `source`
      and `detail` pass here despite `assets/db.js`'s own docstring calling them
      dead, because the rules constrain both values. What survives that is the
      failure worth catching — a field no line anywhere mentions.
- [x] **All five payloads, not one — and the figure that was hiding in the other
      four** (2026-08-19). The display-gap guard below scanned `summary_payload`
      alone, because that was where `keepers` had been hiding. Covering one of
      five payloads is the kind of half-check that reads as a solved problem, so
      it now covers every builder in `cv/publish.py`: `summary_payload` (15
      keys), `events_payload` (17), `identity_payload` (3), `thumbs_payload` (3)
      and `player_report_fields` (20) — 58 published figures against 791 distinct
      property names across the 28 JavaScript files the site serves. The builder
      table is itself checked against the file, so a sixth payload added without a
      line in it fails the test rather than quietly falling outside the scan.
      `player_report_fields` needed its own parse: it builds every name out of one
      prefix and twenty suffixes, so no finished field appears in the source as a
      literal.

      Widening it found `cvPositionNoiseM` the same afternoon, and it was a
      display gap of exactly the `keepers` kind. Published onto every player
      report, mirrored into the browser's own `cvReportFields`, listed in
      `CV_REPORT_KEYS` so that clearing a run clears it too, sitting in the sample
      report at 0.14 — and rendered nowhere. What hid it is a near-miss:
      `cvQualityNotes` in `assets/report.js` does say a version of this to a
      coach, off `quality.position_noise_m`, which is a different field on a
      different object. The run's team-wide average, not this player's track.

      That distinction is the whole reason the gap mattered rather than being
      tidy-up. `cv/metrics.py` withholds the burst count when *that track's*
      noise passes `MAX_ACCEL_NOISE_M`, measured per track from the track itself
      — so a player whose Bursts card is missing while the team average sat under
      the ceiling would read the coach's note and conclude the card was missing
      for some other reason. `player/player.js` had already promised the
      explanation: the comment on the Bursts card says absent-not-zero "when the
      tracking was too jittery to tell one from the wobble — see
      `position_noise_m` in cv/metrics.py, **and the note under this grid**", and
      the note under the grid said nothing about wobble. `playerWobbleNote` in
      `assets/report.js` now keeps that promise, joined into the same sentence as
      the coverage and clock notes so the page has one place that says what these
      figures rest on.

      Three things it deliberately does not say. It quotes no phantom-metres
      rate: the smoothing window is fitted per track and published per run, so a
      rate worked out on the browser's side would be quoting a window it cannot
      see — the same trap `cvQualityNotes` documents at its own noise note. It
      returns nothing at all for a zero, because a fragment too short to measure
      a wobble reports none, a JSON round trip turns that into `0`, and "0.00m of
      wobble" claims a precision nobody established. And it stops at the ceiling
      rather than crossing it: `metrics.py` withholds on `noise_m >
      MAX_ACCEL_NOISE_M`, so a track sitting exactly on 0.30 still has a burst
      count, and the boundary test pins that — a note explaining away a card that
      is on the screen is worse than no note.

      Six tests on the function, and the player portal's page test now opens a
      match and reads the sentence off `#md-stats-note` rather than stopping at
      "the page came up". That second half is the point: the scan can only prove
      a key is *mentioned* somewhere in the served JavaScript, which is as far as
      reading source text can go. Whether the mention reaches a screen is the
      thing that was actually missing, and the thing a scan can never tell you.
      The fixture is built to be the awkward case — 0.42m of wobble and no
      `cvAccelerations` beside it — so the test fails if the note ever starts
      apologising for a card that is present.

      Five keys still travel without a reader, three more than before, and each
      is excused in the test with the reason written out. Both `schemaVersion`s
      are provenance and nothing may branch on them. `trustworthy` is derived
      from `warnings`, which `coach/coach.js` draws in full.
      `droppedBelowConfidence` is a tuning figure for whoever is fitting the
      detector, and `events_payload` already argues no page should draw it —
      confidence is a model-internal scale nothing on screen explains.
      `identity_payload.playerByCluster` records the naming as it stood when the
      run was published, while every page joins on the live
      `cvMapping/players.byCluster` a coach can change afterwards. Two of those
      five were claims sitting in Python comments that nobody rechecked; they are
      now assertions.

      The limit is unchanged and gets worse with more payloads, so it is worth
      restating: the scan cannot say *which* object a `.key` was read off. Short
      generic names are effectively unchecked — `id`, `type` and `team` on an
      event are each a property of something else somewhere in this codebase — so
      the events payload is the weakest covered of the five. It still catches the
      failure it was built for, which is a key nobody anywhere mentions, and no
      amount of aliasing produces that by accident. 731 pure JS · 30 pages · 145
      emulator · 1125 Python.
- [x] **Every figure the pipeline publishes, against every page that could draw
      one** (2026-08-19). The field sweep below named three distinct ways a write
      and a read can fail to meet, and automated a guard for one of them. This is
      the second, and it is the one that has actually cost something: a *display
      gap* — a number the pipeline computes, serialises and ships in every
      published document, that no page puts on a screen. `keepers` sat exactly
      like that for months, and nothing anywhere was wrong. The pipeline was
      right, the document was right, and every page was right about everything it
      did draw. The only symptom was a screen missing something, which looks
      exactly like a feature nobody has asked for yet.

      `tests/smoke.test.js` now parses the keys out of `summary_payload`'s own
      return dict in `cv/publish.py` — 15 today — and looks for each as a
      property access across every JavaScript file the site serves: 28 files,
      789 distinct property names. Both sides come out of the real files, so a
      figure added to the payload fails this the day it is added, instead of the
      month somebody notices the screen never changed. `assets/sample-report.js`
      is skipped on purpose: it writes these keys rather than reading them, and
      counting it as a reader would let the preview keep a figure alive that no
      page has ever drawn.

      The reader scan matches `.key` after a name, a `)` or a `]`, not a bare
      word. `window`, `source` and `period` are all ordinary identifiers in this
      codebase, and every one of them would match a naked name search on a page
      that never touches a summary. What it still cannot say is *which* object a
      `.key` was read off — a page using `participants` for something unrelated
      would satisfy this on the summary's behalf. That is the price of a check
      that needs no runtime and no fixture, and it is worth paying, because the
      failure it catches is a key nobody anywhere mentions, and no amount of
      aliasing produces that by accident.

      Two keys travel with no reader, and each is allowed to with the reason
      written down rather than assumed. `schemaVersion` is provenance, and
      nothing may branch on it — a client that renders differently per version is
      two clients; it exists so a document found in the console can be dated
      against `cv/report_json.py::SCHEMA_VERSION`. `trustworthy` is `not
      warnings`, and `cvWarnings` in `coach/coach.js` already draws every warning
      in full — a page rendering the boolean as well would tell a coach this run
      cannot be trusted directly above the list of what was wrong with it. The
      allowlist is checked in both directions, so a key that later gains a
      reader, or leaves the payload, cannot keep its excuse.

      A second test pins `assets/sample-report.js` to the same shape, key for
      key. That fixture is not a fixture in the usual sense — it is the demo, and
      the only version of a match report anybody has looked at, since no footage
      exists yet. A key it spells differently from the pipeline is a feature that
      works in the preview and fails on the first real match, which has already
      happened once: its keeper block carried `track_id` where the pipeline
      writes `track_ids`, alongside a dozen stats it did not have at all, so the
      fixture was not exercising the real shape of the one field nothing
      rendered. `isSample` is the only key excused, and only in one direction —
      it is the marker `isSample()` tests for so a page can label an invented
      figure as invented, and it has no business in a document written from real
      footage.

      **And the sweep below now covers both match pages.** The `state.match?.X`
      union check read `coach/coach.js` only; `halftime/halftime.js` reads three
      fields off the same document and was checked by nothing. The two pages get
      different writer sets on purpose: the coach page assembles its match out of
      a document plus several subcollections, so its merge literal is part of the
      writer side, while the half-time page assigns the snapshot straight through
      — `state.match = match` — so the document alone is the whole of what it may
      read, and a field the coach page merges in is not one it can borrow.
      `player/player.js` stays out, and the test says why: its `report` is a
      published player report from a different collection that happens to carry
      an opponent name and a video link too, so checking it against the match
      rules would compare a document against somebody else's writer list and pass
      or fail for no reason.

      Page suite 28 → 30, all four suites green.
- [x] **Every field the browser reads, against everything that writes one**
      (2026-08-18). A sweep in both directions across `assets/db.js`,
      `cv/publish.py`, `firestore.rules` and the three pages: every read with no
      writer, every write with no reader, and every field whose two ends
      disagree about shape. It found four broken things, one field that should
      never have existed, and six that look dead and are not.

      **Two reads of fields that do not exist.** The opposition shot map was
      captioned from `state.match?.opponent`; the field is `opponentName`. The
      downloaded label file stamped `match.playedOn`; the field is `date`. Both
      failed the way this class of bug always fails — silently, into the `||`
      beside them. The caption read **"Them"** for every opponent the app has
      ever had, which is exactly what it reads for a match whose opponent was
      genuinely left blank, so nothing on screen could tell the two apart. The
      label file was worse: it is an export meant to be joined to other exports
      later, and it carried `opponent: null` beside a `matchId` that knew the
      name perfectly well. The tag-log download two hundred lines away had the
      spelling right the whole time.

      **A total of two things that mean different things.** `publishReports`
      wrote `cards: yellow + red` onto every player report. Nothing read it —
      `assets/ui.js::cardChips` renders the two separately and says in its own
      docstring why a sum is meaningless — but four fixture files carried it, so
      the tests agreed with the writer and neither agreed with the product. It
      is gone from the writer and from all four.

      **Two sample fields that had drifted.** `assets/sample-report.js` claimed
      `schemaVersion: 5` against a pipeline on 13, and carried a `cvPassAccuracy`
      that was deleted from the real writer in the sweep before this one. A
      sample report is the only version of this product most people will ever
      see; it should not be the least accurate document in the repo.

      **Six write-only fields, each kept for a stated reason.** This is the half
      of the sweep with no code change, and it is the half that stops the next
      sweep re-deleting them. `taggerUids` and `archived` are *required by the
      rules on create* — a team document without them fails outright — and each
      is a seat something is going to sit in: `taggerUids` is a live access grant
      waiting on the role-split decision, `archived` is the switch a
      team-hiding control will flip. `source`, `detail` and `tappedAt` on every
      log entry carry three different arguments, now written out on `baseEntry`:
      `source` is what will tell a tapped event from a pipeline-proposed one the
      day the pipeline may append here, and writing it now means the entries
      from *before* that day still answer the question; `tappedAt` is the only
      record of when the tap happened rather than when the write landed, which a
      server timestamp cannot recover for a tablet that was offline.
      `schemaVersion`, `playerByCluster` and `droppedBelowConfidence` in
      `cv/publish.py` are provenance and diagnostics — none should be branched
      on, and the comment on `playerByCluster` claiming it was a gate was simply
      wrong and now says what it is: the naming as it stood when the run was
      published, against a browser copy a coach can change afterwards.

      **The regression test.** `tests/smoke.test.js` now reads every
      `state.match?.X` out of `coach/coach.js` and checks each against the union
      of the fields `firestore.rules` permits on a match document and the keys
      the page merges on itself after loading it. Both sides come out of the
      real files, so renaming a field in the rules, or dropping one from the
      merge, fails here instead of on a screen. Confirmed by putting
      `state.match?.playedOn` back and watching it fail. 19 reads today.

      **What it surfaced and did not fix: nobody can see the goalkeeper
      numbers.** `cvStats/summary.keepers` carries a complete stat block per
      keeper — shots faced, saves, goals conceded, save percentage, claims,
      sweeper actions and how far out they came, distribution accuracy split by
      kick, punt and throw — computed by `cv/keeper.py`, published by
      `cv/publish.py`, and rendered by no page at all. Goalkeeper *detection* is
      ticked in Phase 6; goalkeeper *display* was never written down anywhere,
      which is how a finished figure ends up with nowhere to go. It is a display
      gap rather than a dead field, and the third distinct reason a write can
      have no reader. `assets/sample-report.js:461` mirrors it with the wrong
      key as well — `track_id` where the pipeline writes `track_ids` plus a
      dozen stats — so the fixture is not exercising the real shape. Both halves
      of that are closed now: the keeper block went on the coach's match view,
      and the published-field sweep above turned "no page draws this" from
      something a person had to notice into something a test fails on.

- [x] **The player's own season got the sidebar the coach's report already had**
      (2026-08-15). The rail — a column that loads one section in rather than
      scrolling to it — had lived on the coach's match report since the desktop
      layout landed. The player portal, which is the only page most of this
      system's users will ever open, did not have one. Same season, two shapes,
      depending on who opened it.

      `assets/rail.js` is that rail, lifted out of `coach/coach.js` and shared.
      `railTarget` is the one decision in it that needs no DOM, so it lives in
      `report.js` where `tests/video.test.js` can reach it — including the case
      that made extracting it worth doing: `block.id` is `''` for a section
      nobody named, and falling back to it would toggle every block on the page
      at once.

      Three things the rail gained on the way out.

      **Facts.** 168px of a 1500px screen was a column of eight words. It now
      stands the figures a reader wants without going to find them — the
      scoreline, the minutes, how much of the season was filmed — and they stay
      put while the middle of the page changes, so switching sections never
      takes the frame away with the content. On the coach's side the facts do
      real work immediately: on a match tagged for 59 seconds and never
      finished, the rail reads **Tagged to 00:59, last tag not the whistle**,
      which is the absent-is-not-zero rule surfacing where it is cheapest to
      see.

      **Arrow keys.** A rail is a list of alternatives, and every other list of
      alternatives on a screen — a radio group, a tab strip — moves with the
      arrows. Without it a keyboard reader tabbed through eight buttons to reach
      the ninth, which is the reason tab strips stopped being made of tab stops.

      **Groups.** One rail entry may now cover two blocks (`data-rail-group`). A
      heatmap caps itself at 620px and a shot map at 380, so showing either
      alone on a monitor left most of the row empty beside it while the other
      sat hidden. They are one entry, "On the pitch", because they are one
      thing to look at: two pictures of the same pitch.

      And the player's season is **grouped by the question it answers** instead
      of piled into one ribbon. Thirteen cards in a row is a wall you read left
      to right to find the one thing you came for, and it flattened the
      distinction this project cares most about — a goal somebody pressed a
      button for and a distance a machine estimated were the same box with a
      small dot in the corner. Now they are The season / On the ball / Running /
      Defending, each saying which it is, with the four season traces split
      across the groups they belong to: a metres-per-minute line beside the
      kilometres it is made of, not in a panel of four unrelated instruments.

      **New: how much was filmed.** `formNote` says "5 of 8 matches were filmed
      and tracked long enough to place on a line", which is true and leaves a
      player knowing neither which five nor how near the other three came. It is
      a bar per match now, and the denominator is the match rather than their
      own minutes — a substitute followed for all twenty of their twenty minutes
      has been measured completely, and a full bar beside a starter's would say
      the two had the same evidence behind them. Amber for filmed-but-too-thin,
      an empty track for not filmed at all: absent is not zero in a bar chart
      either.

- [x] **Two fields that existed only to go wrong** (2026-08-17). Found while
      diffing what `cv/publish.py` writes against what `cvReportFields` clears.

      **`cvPassAccuracy`** was written by the pipeline and read by nothing —
      every page divides the two fields beside it. It was also the only field
      the pipeline wrote that the browser's null-out list did not know about,
      so un-naming a tracked figure and re-publishing left an accuracy standing
      next to the two nulls it came from. Harmless today only because nothing
      read it, which is the worst reason for a number to be on a document.
      `tests/test_player_merge_parity.py` now asserts the general form: every
      field the pipeline writes must be one the browser can take back. The
      other direction stays open deliberately — minutes filmed, fragment count
      and whether a human reviewed anything are things the pipeline cannot know.

      **`cvPasses: 31`** sat on a published report in `tests/fixtures.js`,
      spelt as no field in the repo. This is the invisible kind of fixture
      error: a misspelt `cv` field fails nowhere, the page reads `undefined`
      and renders a dash, and the result is indistinguishable from a match
      nobody filmed — so a test can pass while exercising the empty path and
      calling it coverage. Same shape as the `kickoff_first` period and the
      `midfield` position before it. The fixture check now takes its list of
      allowed names from `cvReportFields(null)` rather than restating them.

- [x] **A student tracked as two figures was shown the second one**
      (2026-08-17). The tracker loses people when they leave frame and
      `cv/identity.py` only rejoins fragments a couple of seconds apart, so
      anyone who went off and came back stays split. The cluster picker lets a
      coach map several figures to one player **on purpose** — a wrong automatic
      merge would credit one student with another's work and could not be undone
      — and `cvStatsByPlayer` sums them, saying so in its docstring.

      `cv/publish.py` wrote one document per *cluster*. Two figures of one player
      meant two `update()` calls on the same report, so the second replaced the
      first. Measured on two fragments: the coach's screen said **54 touches,
      7.3 km, 49 minutes**; the student's own page said **24, 3.1 km, 21** — the
      same afternoon, told two ways, with the student's the smaller of the two.
      It also reported "2 player reports written" for one player.

      `merge_tracks` groups the mapping by player before anything is written,
      with the rules named to match `assets/report.js`: touches add, top speed is
      the fastest they ran rather than the last, the wobble is taken at the worst
      fragment rather than averaged, accuracy is 26 of 35 rather than the mean of
      two fractions, touch times are re-sorted into a timeline, heatmaps add cell
      by cell. None stays none: a fragment too short to measure a burst
      contributes nothing rather than pulling the count towards zero.

      **The fix made two implementations where there had been one and a half**,
      so `tests/test_player_merge_parity.py` is the other half of it. Seventeen
      fields plus the two the sides name differently (`pass_accuracy` against
      `passAccuracy`). The failure it guards is quiet — both sides go on
      producing plausible numbers, and only a coach comparing their own screen
      against a student's would see it. Checked by narrowing the Python side's
      maxed fields alone: three tests fail, across both files.

      1044 Python · 675 pure JS · 23 pages · 145 emulator.

- [x] **A verdict taken back still counted as a verdict** (2026-08-17). Found by
      tapping the review buttons. Tapping ✓ twice clears the verdict and keeps
      whatever the shot ledger put beside it, under `if (kept)` — and `kept` is
      `{}` when there is nothing to keep, so the delete never ran and every undo
      wrote an entry with no verdict in it.

      Three counts read that as a checked event and a fourth did not. The
      progress line said *"1 of 433 checked · 100% of those were real"* with
      nothing checked and nothing real; the rail badge agreed; **"Not checked
      yet" hid the row**, so a mis-tap took an event out of the only list built
      for working through them; and the scorecard directly below went on
      correctly reporting none, because `reviewScore` asks for a status
      specifically and says why in a comment.

      The same shape arrives honestly from the shot ledger — marking a shot
      "Saved" writes `{result: 'saved'}` beside no verdict at all, which is a
      statement about what a shot did rather than agreement that it was one. So
      the rule moved next to that comment as `hasVerdict` and every count goes
      through it. Fixing the undo alone would have left the ledger case; fixing
      the counts alone would have left an empty map per mis-tap eating the
      1500-entry budget `firestore.rules` allows.

      Reaching the second half meant widening `tests/fixtures.js`, which had
      been reusing the **preview's** event list as a stored one — and the preview
      holds no shots deliberately. Three interactive surfaces had therefore never
      been loaded by any test: the shot ledger, the xG calibration check, and the
      cluster picker. See Testing Strategy §9.

- [x] **The two halves of the calibration had never been checked against each
      other** (2026-08-17). `calibrate/pitch-model.js` imports nothing — the one
      property that makes a module testable here — and **no test in the repo
      touched `fitHomography` or `landmarks`.** The solver that turns a coach's
      clicks into the mapping every metre in this system comes from had no
      coverage at all.

      Two independent implementations sit either side of that export. A coach
      clicks eight points in the browser, reads a reprojection error off the
      page, and hands the correspondences to a pipeline that re-fits them with
      OpenCV. If those disagree, **the error a coach was shown is not the error
      the pipeline has** — and nothing announces it, because the numbers stay
      plausible and are simply about a different pitch.

      `tests/test_calibration_parity.py`, on the pattern of the xG parity test:

      - **The landmark tables are the same pitch.** 27 names, identical
        coordinates. This is the likelier drift of the two, because it is a
        table somebody edits.
      - **The solvers agree, and both are right.** Same clicks in, same metres
        out, checked against the synthetic camera that generated the clicks —
        which is the half that catches the two of them being wrong together.
        **Measured: 0.2 millimetres apart**, and both recover the camera
        exactly. Not a shared implementation agreeing with itself: the browser
        solves the normal equations by hand, OpenCV uses
        `getPerspectiveTransform` at four points and RANSAC above that.

      Checked by moving the penalty spot one metre in the browser's table: both
      tests fail, naming the pixel and the gap. **And the interesting part of
      that check is the number** — a one-metre table error came out as a
      **0.21m** disagreement in the fit, because eight points absorb most of it.
      That is exactly why comparing the tables directly earns its place beside
      comparing the fits; a solver check alone would have reported a fifth of
      the error and called it small.

      One assumption of mine was wrong and the code was right: I expected
      `fitHomography` to return null below four points and it throws. The page
      never reaches that — `drawPitchOverlay` counts first and `renderQuality`
      wraps the call, because an exception in the canvas draw would take the
      picker down between a coach's first and fourth click. The test now asserts
      that guard rather than the return value.

      1011 Python · 675 pure JS · 20 pages · 145 emulator.

- [x] **Publishing did not say so on the page the coach was looking at**
      (2026-08-17). Found by driving the publish button — the write that reaches
      children, and the last major path nothing had ever exercised through the
      UI.

      `doPublish` refreshed `state.matches`, the hero and the match list — the
      dashboard *behind* the match view — and never touched `state.match`. So
      the page a coach is actually looking at went on reading
      `finalized: false`: the button still said **"Publish player reports"** and
      the subtitle still omitted **"reports published"**. The only sign anything
      had happened was a toast that clears itself in 2.6 seconds.

      A coach who steps away and comes back cannot tell whether every student
      has their report. The safe thing to do is press it again, which is at
      least harmless now — re-publishing merges rather than overwrites, since
      the day it wiped the video fields.

      `renderMatchStatus()` is the two lines that answer the question, called
      from `openMatch` and now from `doPublish`, with `state.match` updated to
      match what `publishReports` wrote.

      **What publishing actually produces, now pinned:** one report per squad
      member, minutes that add up across the substitution — 30 for the player
      who came off on thirty, 60 for the one who came on then, 90 for the one
      who was on throughout — and a `linkedUid` only on the student who has
      signed in, the rest claimed when they do.

      675 pure JS · 20 pages · 145 emulator · 1008 Python.

- [x] **"Lost. You played an unused substitute."** (2026-08-17). The lede on a
      student's own match report, found by opening the player portal in the page
      suite and reading what it says rather than checking that it says
      something.

      Three faults in one sentence, and the same sentence the roadmap already
      records as *"the single worst this app could produce"* — that fix swapped
      the condition and never revisited the grammar.

      1. **It is not a sentence.** The phrases were spliced in after a fixed
         "You played", so the branch for a squad member who never came on read
         *"You played an unused substitute."* On the page of the one reader most
         likely to go over it twice.
      2. **"1 minutes."** The count was interpolated with a hard-coded plural,
         so a substitute who got a minute was told so ungrammatically.
      3. **Zero minutes is two different afternoons, and only one of them means
         they did not play.** `minutesFrom` rounds, so somebody who came on with
         twenty-five seconds left comes back as **0 — with a stint against their
         name**, and somebody who never left the bench comes back as 0 with
         none. The number alone cannot tell them apart. Telling a student who
         came on that they did not is exactly the failure this sentence was
         rewritten for the first time.

      `matchLine` moved to `report.js`, where it can be tested without a DOM —
      the repo's own rule for anything this consequential, and this is the one
      string in the product addressed to a sixteen-year-old about whether they
      played. Every branch now carries its own verb, `stints` (or a goal, which
      is its own evidence) separates the two zeroes, and one property is checked
      across all nine shapes: it opens with a capital and ends with a full stop.

      675 pure JS · 18 pages · 145 emulator · 1008 Python.

- [x] **The tablet is now driven tap by tap, including with the signal gone**
      (2026-08-17). The tool every number in this system derives from had
      exactly one assertion against it — that `init()` ran — which is one more
      than it had during the eight days it was dead, and not enough for
      anything else.

      **The demo path, as a test.** Pick a match, set a lineup, kick off, tag
      through the side sheet, substitute, undo, half-time, restart. It reads the
      *database* back after each tap rather than the screen, because what the
      screen said and what reached Firestore is precisely the pair that came
      apart in August. Along the way it pins the things that are easy to get
      quietly wrong: a starter's stint opens at zero when the lineup is saved
      and an unused substitute gets **no stint at all** rather than one of zero
      length; a substitution closes one stint and opens another and moves the
      version; undo puts all three writes back; `halfTimeClockS` reaches the
      match document, without which every second-half video moment lands late by
      the length of the interval.

      **And the resume path, which the setup screen promises in as many words**
      — *"if you started one earlier and had to stop, choose it again, nothing
      you already tapped is lost"*. A tablet dying mid-half is the likeliest
      thing to happen on the day. Checked: the match in progress is offered and
      marked `resume`, choosing it lands in the live view rather than asking for
      a lineup again, the clock picks up at the last thing anybody tapped
      (2100s, not zero), and a player already substituted off is offered back
      marked **"been on"** rather than hidden or offered as if fresh. All
      correct — a negative result, now nailed down.

      **The fake Firestore learned to go offline, which is the point.** A fake
      where every write resolves immediately can never catch the bug that
      mattered most here: `persistentLocalCache` makes a write durable the
      instant it is issued, and the promise resolves only on *server*
      acknowledgement. `goOffline()` now keeps applying writes locally and
      leaves their promises pending, exactly as Firestore does. The new test
      goes offline mid-match and asserts the sheet closes, the tag reaches the
      cache, the substitution moves the roster, **two further presses of Confirm
      do nothing**, and reconnecting settles the queue with no duplicate and one
      closed stint. Reinstating the old `await`-then-render in `confirmSub`
      fails it.

      That is the third bug class this suite can now see, and the only one of
      the three that could not be found by reading code.

      668 pure JS · 17 pages · 145 emulator · 1008 Python.

- [x] **The full match report said less than the three-minute one**
      (2026-08-16). Found by rendering one match on both pages and diffing every
      figure that appears on both — the first thing the new page suite was
      pointed at rather than a roadmap line.

      `taggedStatRows` in `coach.js` was nine hand-written one-sided cards.
      `taggedTallies` in `halftime.js` was five hand-written two-sided bars. One
      tag log, two lists, and they had drifted:

      - **the opponent's cards and offsides never reached the full report.**
        "Our cards" and "Offsides against us" and nothing about the other team,
        while the touchline page showed 1–1 and 0–1.
      - **free kicks were not on the coach's page at all.** A tagged event type
        the post-match report silently dropped.
      - and the same figures were drawn as two different kinds of thing — a
        grid of boxes against a column of comparison bars.

      A coach reading the half-time page knew the opposition had been booked and
      caught offside; the same coach opening the same match afterwards could not
      find out. That is the third time this shape has been fixed here — the
      video rows got it, the season got `seasonGroups` — and the tagged rows
      were the pile nobody had merged.

      **`taggedTeamRows` in report.js is now the one place**, and it reads both
      sides through one `pick` so our column and theirs can never come off
      different fields. Two differences between the pages survive on purpose and
      are arguments to the function rather than separate lists: the report keeps
      goals and keeps rows neither side registered, and the touchline page drops
      both, because one is looked up and the other is read standing up.

      **And a match nobody tagged stopped reporting nine zeroes.**
      `aggregateMatch` initialises every count to zero, so the most ordinary
      demo there is — *film a match, nobody runs the tablet* — produced a full
      report of a game in which nobody took a corner, gave away a foul or was
      booked, under a scoreline reading **0–0** in the largest type on the page.
      Same defect as a whistle at second zero, and this one is louder. Both
      pages now show an em dash for a score nobody recorded, and the report says
      in a sentence that nobody ran the tablet, keeping the video rows because
      those are still real.

      One stale comment went with it. `taggedTallies` said "every one of these
      is a count, so none is drawn as a split" and cited `comparePair` — which
      says the opposite two hundred lines up: *shares and counts are both
      splits; only their honesty differs.* The code is right and the comment was
      wrong. Five corners to one and fifty to ten are not the same half, and the
      thing that separates them is `tentative` — a lead smaller than chance
      would hand out is drawn hollow. Checked against the real stylesheet: at
      2–1, 4–1, 3–1, 2–5, 0–2 and 1–0 every bar comes out hollow, which is the
      correct reading of a half that small.

      The shim gained something on the way. `el.style` now writes through to the
      `style` attribute, because half the charts here are an `<i>` whose width
      is set in JavaScript — and a style object living only in memory handed
      back `outerHTML` with every bar collapsed to nothing. Taking real markup
      out and rendering it against `app.css` is the one way a bar chart can be
      looked at from a test process, and it silently did not work.

      668 pure JS · 14 pages · 145 emulator · 1008 Python.

- [x] **A bug class the suites cannot see, found by driving the page**
      (2026-08-16). `mountRail` builds once and keeps the callback it was given.
      All three rails added on 15 August handed it a closure over a function
      argument — the report, the player, the totals — so the callback held
      whichever subject was opened first and went on showing it. Open one
      student, go back, open another, and the rail beside the second student's
      empty season still read **8 matches, 708 minutes, 7 goals and assists,
      Portal: signed in.**

      Nothing about that looks wrong. The numbers are well formed, plausible and
      internally consistent; they are simply somebody else's. It is the same
      shape of failure as the eight-day-dead tagging tool: **`tests/video.test.js`
      cannot import a module that touches the DOM, and the emulator suites drive
      Firestore without loading a page**, so a stale closure inside a UI
      component is invisible to every test in the repo. It was found the only
      way it could be — by opening two players in a row and reading the rail.

      Fixed by reading from live state (`open.report`, `open.season`,
      `state.openPlayer`) rather than from a captured parameter, and the reason
      is now a comment on all three, because the next rail anyone adds will be
      copied from one of them.

      This is the second entry in two days pointing at the same gap. Closing it
      needs either a headless browser or a DOM shim in the test suite, and both
      are dependencies this repo does not have — see Phase 3.

      **Half of that sentence was wrong, and it was closed the next day.** A
      headless browser is a dependency; a DOM shim is a file in `tests/`. See
      Testing Strategy: reinstating this bug now fails `tests/smoke.test.js`
      with *"the rail still shows Alex's minutes"*.

- [x] **A coach and a player now read one season, not two shapes of it**
      (2026-08-15). Giving the player portal grouped sections and a rail left
      the coach's view of that same player as a flat ribbon of thirteen boxes —
      so the two people who look at it together were looking at two different
      pages about the same eight matches. That is the exact failure the player
      portal's own comment warns about, introduced by the commit that wrote it.

      **`seasonGroups` in report.js is now the one place that decides which
      figures belong together**, and it hands back specs rather than elements so
      it stays importable by a test file with no DOM. The two hand-built piles
      it replaced had already drifted: one called it "Tackles" and the other
      "Tackles won", one counted interceptions and the other did not, and only
      one of them had ever heard of carries. `coverageStrip` moved into ui.js
      for the same reason, so the bar chart of how much of each match was
      filmed is the same picture on both screens.

      The coach's view gains the rail, the four groups, the split traces and the
      coverage strip; its rail also carries one fact the player's does not —
      whether that student has signed in to the portal — because it is the thing
      a coach opens the page to check before sending an invite.

      **One bug I wrote and caught in the browser an hour later.** The two pages
      differ in one sentence: "the minutes the tracker held on to *them*" is
      "…on to *you*" on the player's own page. I did that with
      `note.replace(' them.', ' you.')` on the finished string, and it also hit
      the passing note — which then read "a pass is two touches with a ball
      between you." The voice is an argument to `seasonGroups` now, and the test
      that pins it asserts the passing note is unchanged in both persons.

- [x] **The home page tells a coach what today needs, not which teams exist**
      (2026-08-15). The signed-in landing page was a greeting, a team card and
      three links. Its one attempt at saying something useful was a line reading
      "2 matches waiting to be tagged", counted from every fixture that was not
      finalized — which meant **a match set up for Saturday was reported as
      overdue work on the Tuesday before it.** A coach who is well organised got
      told off for being organised.

      **Next up, above everything.** `nextFixture` finds the soonest unplayed
      match dated today or later, across every squad the account coaches, and
      the card links straight into the tagging tool with `?team=` already set —
      so a coach who has just read which team plays today does not answer the
      same question again on the next screen. The tool honours that parameter
      only when it names a squad `resolveAccess` actually returned, so a
      hand-edited URL cannot smuggle in a team. Lit in the accent colour at one
      day out or less; anything a week away is a diary entry and should not look
      like a thing that is happening.

      A fixture whose date has passed is deliberately **not** the next match. It
      is a job, and `seasonJobs` already says so — showing it under "next up"
      would park a game that has already been played at the top of the page
      every week until somebody dealt with it.

      **Still to do**, pooled across squads, from the same arithmetic as the
      squad page's sidebar rather than a second copy of it — two panels that say
      what is outstanding is exactly the pair that drifts into disagreeing. The
      shared CSS moved to `app.css` for the same reason.

      And the line under the greeting is chosen in a deliberate order: a match
      today outranks everything, because nothing on the page matters as much as
      the game that is happening; then the outstanding count; then the quiet
      answer. `daysBetween` parses both dates at **UTC noon** rather than
      midnight — midnight is exactly where a timezone offset flips a calendar
      date to the day before, which would put "today" a day out for every coach
      west of Greenwich, which is all of them.

- [x] **The column beside the fixture list stopped being 500px of nothing**
      (2026-08-15). The coach's squad page had a "New match" form in its right
      column and dead space under it — the most visible piece of unused screen
      in the product, on the page a coach opens most. It now answers the three
      questions they open it with, in the order those matter.

      **Still to do.** `seasonJobs` works out what is actually waiting on this
      coach: matches played and tagged but never published, fixtures whose date
      has passed and which nobody ever opened the tagging tool for, players who
      have never signed in, players with no position. Every row is a button that
      switches to the tab the work is on — a to-do list that names a job and
      leaves you to find it is half a feature. The panel hides itself when there
      is nothing, because a panel that is always on screen is a panel nobody
      reads.

      The test that mattered most while writing it is the one about the future.
      A match created for Saturday is not outstanding work on Thursday, and a
      panel that said so every week would be noise inside a fortnight — so
      `today` is passed in rather than read off the clock, the comparison is
      strictly `date < today`, and with no date to compare against nothing is
      called late at all. It is the **local** date rather than UTC: a coach in
      New Jersey opening this at eight in the evening should not be told
      tomorrow's fixture is already overdue.

      **Recent form and where the goals went.** The hero above says 4-2-2 and
      +5, which is the season summed into two numbers; three wins and two
      defeats reads very differently depending on which three, and a record
      cannot say. Five pills, oldest on the left, each carrying its date and
      scoreline. A fixture nobody played is not a gap in the run — it is simply
      not in it, because five results with a hole in the middle would say a
      match was played and produced nothing. The two goal bars are both scaled
      against the larger of the pair, so the longer one is full and the shorter
      reads as a fraction of it; scaling each to its own maximum would draw two
      full bars and say nothing.

      The column is sticky above 900×700, so the outstanding work and the run of
      results stay on screen while the fixture list scrolls past. Below either
      threshold it scrolls with the page, where a sticky panel would be a thing
      that jitters rather than a help.

- [x] **One rule about what moves: a length draws itself, and nothing else does**
      (2026-08-15). Every bar in this product is an argument about a quantity —
      12 goals against 7, 74 tracked minutes out of 90 — and a bar that grows
      from nothing makes the eye follow the length instead of reading the number
      at the end of it. That is the one thing worth 420ms of a coach's
      attention, so it is the only thing that got it. Nothing else animates on
      arrival: a page of things sliding in from different directions is a page
      you have to wait for, and this one is read on a touchline and at
      half-time.

      `scaleX` rather than `width`, because the widths are inline styles
      computed from the data and animating them from a keyframe would need a
      second copy of every number in the stylesheet. The transform costs no
      layout, and the `transition: width` several of these already carry still
      handles the case that matters more — a bar whose value changes while you
      are looking at it.

      The two halves of a comparison grow **outward from the line between
      them**, which is the thing being compared, rather than both from the left
      edge of the screen. And the section the rail loads gets 10px and 200ms of
      fade, because an instant swap between two blocks of numbers reads as the
      page having been wrong a moment ago; it fires only when the section
      actually changes, since `classList.toggle` on an element that already has
      the class is a no-op.

      All of it is off under `prefers-reduced-motion`, which the stylesheet
      already handled globally with `!important` — so the whole layer could be
      added without a second thought for the people who have asked not to see
      it.

      One thing the wide layout made visible while this was going in: a note
      under a heading had no reading measure. `.lede` has capped itself at 62ch
      since the design system was written and `p.muted` never did, which did not
      matter while every block was a column in a 1120px shell and ran to about
      **190 characters a line** once a section could show alone on a monitor.

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
- [x] **The rail loads a section in, instead of scrolling to it** (2026-08-13).
      Asked for directly, and the right correction to the entry above: two
      columns took the report from 6.9 screens to 4.2, but **it was still one
      long page** and the rail was a way of moving around it rather than a way
      of not having it.

      Clicking a rail entry now puts that section in the content area and takes
      the others out. The longest single section is **1.9 screens** and most are
      about **one** — Team 1.51, Players 1.08, Shot log 1.04, Timeline 1.04,
      Tracked figures 1.09. A coach reads one thing at a time, and the page is
      the height of that thing.

      **The gate is in CSS, not in the click handler**, and `screen` in that
      media query is load-bearing rather than decoration. Two places must never
      section: below 1180px there is no rail, so a phone showing one section
      would be a page with most of itself missing; and on paper a report is a
      report only if all of it is there.

      Written first as a print override — `display: block !important` to put
      the sections back — and that was wrong in a way worth recording.
      `.hidden` is `display: none !important` at one class of specificity, and
      the override outranked it, so printing would have revealed the blocks a
      match has **no data for**. Scoping the sectioning to `screen` means print
      never sees the rule and there is nothing to override. Verified by lifting
      the real `@media print` block out of the loaded stylesheet and applying
      it: Team, Players, Shot log and Timeline print, the interactive tooling
      stays `no-print` as before, and every data-hidden block stays hidden.

      A section that disappears cannot strand a reader. Turning the sample
      preview off removes four sections, and if one of them is the one being
      read the view falls back to the first — checked in the browser, reading
      Passing, preview off, lands on Team with the rail lit.

      Scroll-spy went with it. Nine sections' worth of `getBoundingClientRect`
      on a throttled scroll listener existed to answer "which of these are you
      looking at", and there is now exactly one — `aria-current` says so, which
      is what a set of alternatives with one chosen should announce.

      573 pure JS · 149 emulator · 1008 Python.
- [x] **A reviewer can work least-sure-first, and is told what that costs**
      (2026-08-13). Found by checking a claim rather than reading it. Testing
      Strategy item 4 said the review tool *"sorts by lowest confidence first, so
      a human's limited review time goes to what's likely wrong instead of
      skimming everything uniformly"*. **It never did** — `reviewFeed` has been
      chronological since it existed.

      The obvious fix was the wrong one. Chronological earns its place: every
      row seeks the video, so match order is one forward scrub through a half
      and doubt order is a jump across ninety minutes for every verdict. That
      cost is real and the original claim never accounted for it. So the review
      block gained a **Work through** control — *in match order*, still the
      default, or *least sure first* — rather than having its ordering replaced
      by a line of documentation nobody had tested.

      **The half of the claim that needed saying out loud.** Checking the least
      sure events first is the fastest way to find what the detector gets wrong,
      and it makes the reviewed set deliberately the hard cases — so precision
      measured over it is a **floor, not an average**. The scorecard already said
      "out of the N you have checked"; it now also says, and only while that
      order is selected, that the N was not drawn evenly. Without it the tool
      would have reported a worse number than the truth and presented it as the
      truth.

      Ordering is not filtering, and the control says so by sitting apart from
      the chips: those six hide rows, these two change nothing but which comes
      first. The tagged log is not ranked at all — a tap carries no confidence,
      because the log is a person rather than a detector, and sorting it into a
      doubt ranking would invent a certainty nobody recorded.

      Verified in the browser: 14 rows before and after, chronological becoming
      confidence-ordered and back, the caveat appearing and disappearing with the
      control, and the group wrapping without a stray rule at 375px.

      583 pure JS · 149 emulator · 1008 Python.
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
