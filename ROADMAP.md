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
- `demo/` — the original manual xG sandbox, moved off the site root
- `backend/` — the original FastAPI + SQLite server, kept runnable as a fallback
  until a full match has been tagged against Firestore

**Blocked on:** footage from the coach — specifically a raw/native-resolution export
rather than a screen recording, ideally the uncropped wide feed rather than the
auto-tracked crop (see Phase 1). Also pending: permission to use game footage, and
confirmation of what camera system the school actually runs.

**Also needed before real use:** a Firebase project — see `FIREBASE_SETUP.md`. The
console steps (creating the project, enabling Google sign-in, deploying rules, adding
coaches to the allowlist) can't be scripted from the repo.

**Next up:** re-measure tracking on native-resolution footage once the coach
sends it (Phase 6), then the possession/event layer (Phases 9–10).

### What the CV work has established so far

Detection works, calibration works, and **tracking identity is the wall**.
Players are detected in 99% of frames and a homography puts them on the pitch
to within centimetres, but a single player currently splits into ~10 separate
tracks over 30 seconds, so per-player totals are not yet trustworthy. Team-level
metrics — possession, shape, territory — need no identity and are unaffected,
which is the sensible thing to lead a demo with. See Phase 6 for the numbers and
the three candidate fixes.

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

Applies across every phase below — how we know each piece actually works, not just
"the final number looked plausible":

1. **Ground-truth clips first.** Hand-label a handful of short clips (exact player
   positions per frame, exact event timestamps/types) before trusting any full game.
   Rerun the pipeline against these fixed clips after every change and diff the output
   — this is your regression suite.
2. **Metrics per stage, not just the final stat.** If a final number looks wrong, you
   need to trace *which stage* broke it:
   - Detection → mAP against labeled boxes
   - Tracking → identity-switch rate / IDF1 (does a track ID stay on the same real person)
   - Calibration → reprojection error in metres on known pitch landmarks
   - Events → precision/recall per event type against the ground-truth clips
3. **Feature-parity test (specific to this codebase).** `main.py`'s `parse()` and
   `script.js`'s live feature calc must produce identical 12-feature vectors for the
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
6. **Validate model behavior on noisy inputs, not just clean ones.** Feed the existing
   xG model synthetic features with realistic CV-derived noise (jitter, occasional
   gaps) added, not just the clean values `main.py` was trained on — a fast way to find
   out if calibration degrades before relying on it live.
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
- **Model interchange: ONNX**, exactly like the existing `xg_model6.onnx` — train in
  Python, run inference in the browser via `onnxruntime-web`, already proven in this
  codebase. Any future detector could follow the same pattern if in-browser inference
  is ever wanted, though server-side Python inference is simpler for month 1.
- **Frontend (all the surfaces above): JavaScript, matching `script.js`/`classes.js`
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
├── demo/                the original manual xG sandbox (moved off the root)
├── firestore.rules      the security boundary
├── tests/               emulator suites — rules.test.js + flow.test.js
├── PitchIQHelper/       xG model training (main.py) + the shared .venv
├── cv/                  detection + frame sampling
│   ├── detector.py         PersonBallDetector
│   ├── frame_sampler.py    sample_frames()
│   └── experiments/        spike_detect CLI
├── backend/             legacy FastAPI + SQLite (fallback, no longer used by the UI)
├── requirements.txt     Python deps — see the CUDA note inside
└── package.json         dev tooling only; the frontend has no build step
```

One shared venv at `PitchIQHelper/.venv` covers all the Python. Installing
`requirements.txt` alone gives CPU-only torch; the CUDA build needs a separate
install from the PyTorch index first (documented in the file).

The frontend loads the Firebase SDK as CDN ES modules, so there is still no
bundler — `npm` is only for the emulator and tests.

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
- [ ] **Confirmed empirically:** a ball-following camera drops the ball out of frame entirely for stretches — measured two separate ~12s gaps in a 2.5-minute clip, both during penalty-box phases where the camera pulled wide. Possession, passes, and the xG bridge all go blank during those windows, which is a different and worse failure mode than low confidence. If the platform can export the raw wide/panoramic feed instead of the auto-cropped view, that removes the problem outright (the underlying sensor never moved) — this is the single most valuable thing to ask the coach for.
- [ ] **[Stretch]** Multi-camera stitching, drone or pan/tilt/zoom coverage

## 2. Data Storage & Backend
Moved up front — Phase 3's tablet needs somewhere to write to before anything else can happen.
**Built: `backend/` (FastAPI + SQLAlchemy + SQLite). Run with
`uvicorn backend.main:app --host 0.0.0.0 --port 8000`; interactive docs at `/docs`.**
- [x] **[Demo]** Stand up a minimal backend early — a single local server + SQLite/Postgres is enough
- [x] **[Demo]** Run it locally, on a laptop sharing WiFi/hotspot with the tablet at the field — sidesteps whether the school field has reliable internet, which you don't need to solve yet
- [x] Schema for teams, players, matches, roster entries, substitutions, events — with the `source` field (`live_tag` / `cv_candidate` / `reviewer_confirmed`) already in place, though only `live_tag` is written today. Tracking-frame tables deliberately deferred until Phase 6 exists.
- [x] Undo endpoint spanning both events and substitutions, reverting roster state and rolling back match status when a period marker is undone
- [ ] Storage volume isn't a concern for tracking data itself: even at 10fps with 23 tracked objects, a full match is roughly 10-40MB of positions — trivial for any database, including a full season. **Video is the actual heavy cost** (a 90-minute 1080p match is several GB) — decide a retention policy: keep full match video only through the post-game review window, then retain long-term just the short clips tied to confirmed events, not every full match forever
- [ ] **[MVP]** Real auth/accounts (Phase 14) and a cloud-hosted option, once coaches/players need access without being near the field laptop
- [ ] API layer connecting the tablet, processed match data, and the frontend/portal — including the tablet's offline-sync writes

## 3. Live Match-Day Input Tool
A tablet used pitchside during the game by an assistant coach and/or a dedicated data
collector — runs *concurrently* with play, distinct from the Phase 11 tool that runs
*after* it.
**Built: `live-tagging/` (vanilla JS, no build step). Open `index.html` with the
backend running; set `API_BASE` in `config.js` to the laptop's LAN address on match day.**
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
- [ ] **Known gap:** resuming an interrupted match picks the clock up *paused* at the last logged event, since real elapsed time can't be recovered after a reload. Fine for a crash; needs a manual clock-adjust control before it's trustworthy in a real game.

## 4. Field Calibration (pixel space → pitch metres)
**Built.** `calibrate/` is a browser tool for clicking landmarks with a live
overlay of the projected pitch outline; `python -m cv.experiments.calibrate
points.json` fits and grades it. Grab a frame first with
`python -m cv.experiments.grab_frame "<video>" --at 120`.
- [x] **[Demo]** Manual calibration: click 4+ known pitch landmarks once per camera setup, producing a homography into pitch metres, with a conversion into the StatsBomb 120×80 space the xG model expects
- [x] **[Demo]** Attacking direction per period (`MatchOrientation`), so second-half shots are measured against the goal the team is actually attacking
- [x] Honest error reporting: reprojection error is shown but flagged as optimistic (zero by construction on a 4-point fit), with leave-one-out error as the real number and a suspect-point ranking to locate a mis-click
- [ ] Recalibration handling if the camera is bumped or zoom changes mid-game
- [ ] **Pitch dimensions must be measured, not assumed** — distance and speed inherit the error directly, so a 105m default on a 100m field overstates every figure by 5%
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
- [x] Class filtering at predict time (person + sports ball only), which removes the spurious car/dog/umbrella detections a generic COCO model produces on stadium footage
- [ ] Referee detection and exclusion from team stats
- [ ] Excluding non-players in frame: coaches, subs, ball boys, sideline spectators
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

- [x] **[Demo]** Stable per-player track ID using an existing tracking library — done, quality measured above
- [x] Track smoothing before computing speed/distance (`cv/metrics.py`)
- [ ] **Re-measure on native-resolution footage before concluding anything** — the current numbers may be an artefact of the screen recording
- [ ] **[MVP]** Fine-tune the detector on real footage; more stable boxes is the most likely real fix
- [ ] Treat the Phase 11 review tool's merge-tracks control as **required, not optional** — at 10x fragmentation a human stitching tracks is the only route to per-player stats in the short term
- [ ] Team-level stats (possession, shape, territory) do not need identity and remain viable at this quality — worth leading the demo with them
- [ ] **[Demo]** Stable per-player track ID across frames using an existing tracking library (ByteTrack/BoT-SORT-style) — don't build a custom tracker
- [ ] Ball-specific tracking with interpolation through short occlusion; for longer occlusions (goalmouth scrambles), fall back to the live-tagged event log rather than guessing
- [ ] Re-identification after a player is occluded or leaves frame briefly
- [ ] Track smoothing (Kalman filter or similar) before computing speed/distance
- [ ] **[MVP]** Tracking fast enough to keep up during live first-half play, not just accurately in a batch job

## 7. Team & Player Identification
Automatic tracking only needs to be *internally consistent* — resolving a track ID to a
real roster player is a human job, cheaper now thanks to Phase 3.
- [ ] **[Demo]** Team discrimination via jersey color clustering (k-means on shirt pixels) — Team A / Team B / goalkeepers / referee
- [ ] Goalkeeper detection (distinct kit, stays near one goal)
- [ ] **[Demo]** Consume the Phase 3 live substitution log to know exactly which roster players are on the field per team at any timestamp. This doesn't automatically solve *which* tracked blob is *which* name — a human still makes that call once per continuous tracking segment (Phase 11); the sub log shrinks the candidate list and gives a sanity check (if 11 players should be visible and only 10 are tracked, that's a missed-detection alert)
- [ ] **[Demo]** Remaining manual track-ID → roster-player mapping happens in the review tool, narrowed down by the sub log
- [ ] **[Stretch]** Jersey number OCR, used only as a suggestion to speed up mapping
- [ ] Handling broken tracks (same player split into 2+ IDs) — reviewer merges them

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
- [ ] Acceleration, and per-third territory splits

## 9. Ball Possession & Game State
- [ ] **[Demo]** Determine which player/team currently has the ball (proximity + velocity correlation)
- [ ] **[Demo]** In-play vs. dead-ball state — cross-check CV-detected out-of-bounds against the Phase 3 tablet's tap rather than trusting either alone
- [ ] Half/period and clock tracking, driven by the Phase 3 period-boundary taps

## 10. Event Detection
CV produces automatic *candidates*, reconciled against the Phase 3 live-tagged events
rather than replacing them.
- [ ] **[Demo]** Pass detection (completed vs. intercepted)
- [ ] **[Demo]** Shot detection
- [ ] **[Demo]** Goal detection — cross-checked against a live tap if the collector caught it
- [ ] Turnover / tackle detection
- [ ] Stoppage candidate flagging (ball out, play stopped) — the live tap usually already has the type (e.g. "corner"), so this is mostly a cross-check, not the primary source
- [ ] Reconciliation logic: where CV and live tags agree, treat as high confidence; where they disagree or one is missing, flag prominently for the Phase 11 reviewer
- [ ] **[Stretch]** Offside detection — leave human-marked-only for the foreseeable future

## 11. Post-Game Review & Annotation Tool
Pre-populated with Phase 3's live-tagged events and subs, plus Phase 10's CV
candidates — the reviewer's job is verifying/correcting/filling gaps, not labeling from
scratch.
- [ ] **[Demo]** Timeline UI showing both live-tagged and CV-candidate events, synced to video playback
- [ ] **[Demo]** Confirm / edit / delete / add-new-event controls per timeline entry
- [ ] **[Demo]** Track-ID → roster-player assignment UI with thumbnail crops, pre-narrowed by the sub log
- [ ] Merge-tracks control for split IDs
- [ ] Save finalized data as the source of truth for stats, profiles, xG logging, and the player portal
- [ ] Doubles as the ground-truth labeling tool for Phase 16 validation, and as a source of labeled data for fine-tuning detectors later (Phase 5)

## 12. Shot Feature Extraction → Existing xG Model
Where the CV pipeline plugs into what already works (`script.js` / `xg_model6.onnx`).
- [ ] **[Demo]** At shot detection/confirmation, extract the same 12 features the model expects, using the correct attacking-goal direction for the half (Phase 4)
- [ ] Body part classification (foot vs. header) — pose estimation, or a manual tag as post-game fallback
- [ ] `shot_height` is a z-axis value a flat single camera + homography can't give directly — pose estimation or ball-trajectory arc fitting needed; flagged as an open problem
- [ ] **[Demo]** Feed features into the existing ONNX model, log predicted xG
- [ ] **[Demo]** Validate the model against CV-derived (noisier) features before trusting it live (Testing Strategy #6) — retrain with realistic noise added if calibration visibly degrades
- [ ] Log actual outcome (goal/save/block/miss) to check predictions against reality later

## 13. Player & Team Statistics / Profiles
- [ ] `Player` domain model beyond the current UI stub in `classes.js`: identity, team, jersey number, role, per-match stat accumulator
- [ ] `Team` domain model: roster, formation, aggregate stats
- [ ] **[Demo]** Compute the halftime-tier stats first (possession, shot map/xG, distance, sprint counts, live-tagged event counts)
- [ ] **[MVP]** Full post-game tactical catalog (passing networks, phase-of-play, pressing trends)
- [ ] Use the Phase 3 sub log to scope each player's stats to their actual minutes played
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
- [ ] **[Demo]** Coach halftime view: sideline/mobile-friendly, high-signal, minimal reading — built from the Stats Catalog's halftime section
- [ ] **[MVP]** Coach tactical dashboard, player portal view (Phase 14), event timeline synced to video (shared with the Phase 11 review tool)
- [ ] **[Stretch]** Live tracking overlay on video (bounding boxes, IDs, mini-map)

## 16. Validation & Demo Logistics
- [ ] **[Demo]** Ground-truth comparison using the Phase 11 tool on a short labeled clip before trusting a full game
- [ ] **[Demo]** Camera hardware/placement plan for the actual high school field — resolve the elevation/coverage question from the Reality Check concretely, don't leave it open until game day
- [ ] Lighting/weather robustness check (outdoor field, not a broadcast studio)
- [ ] Identify and briefly train whoever will run the Phase 3 tablet during the actual demo game — the live data is only as good as the person entering it
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
