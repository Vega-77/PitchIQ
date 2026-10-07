# PitchIQ — handoff to a new chat

Paste this whole file into the new chat's first message, or say "read HANDOFF.md".
Written 2026-09-18. Everything below was measured, not assumed; where a number was
published to Alex and later found wrong, both versions are shown.

---

## 0. READ THIS FIRST — the scratchpad does not follow you

Every script, dataset and label file from this work lives in the **old session's
scratchpad**:

```
C:\Users\alexv\AppData\Local\Temp\claude\C--Users-alexv-Desktop-Repos-PitchIQ\7f31f241-b244-4121-aa07-4ef3b9736b2a\scratchpad
```

A new chat gets a **new session id**, so its own `$SP` will be a different folder.
Set the old path explicitly at the top of every Bash command:

```bash
export SP="C:/Users/alexv/AppData/Local/Temp/claude/C--Users-alexv-Desktop-Repos-PitchIQ/7f31f241-b244-4121-aa07-4ef3b9736b2a/scratchpad"
```

Confirmed present as of this writing: `labels.json` (Alex's 75 hand labels — the
irreplaceable file), `label/` (240 jpgs + `index.html` + `manifest.json`),
`motiongate.py` / `.json` / `.log`, `mgspeed.py`, `genlabel.py`, `labeltest.js`,
`wasbcands_*.npz`, `posstable.json`, `gt/truth/p1..p8.json`, `research/wasb/`.
It is a temp directory. **If it ever looks empty, say so immediately rather than
regenerating — `labels.json` is ~50 minutes of Alex's hand-labelling and cannot
be recreated.** (A copy also exists in the published artifact's `db`; see §7.)

---

## 1. Who and what

Alex Vega is building **PitchIQ** with a friend: a hackathon xG calculator being
extended into a computer-vision system that derives soccer statistics from match
footage. Goal is a working demo within about a month, tested with their old
high-school team. GitHub Pages deploys from `main`; repo `Vega-77/PitchIQ`, site
https://vega-77.github.io/PitchIQ/.

**The governing instruction, verbatim:**

> "Okay here's what we are gonna do. I really like B, where i identify the ball
> and we can finally get ball detection working and HOPEFULLY perfect. once you
> have that done, i want you to work overnight and for as long as you can on A,
> getting the camera pan data to somehow become static feild data. any question?"

- **B** = the ball discriminator. Extract candidate crops, **Alex hand-labels
  where the ball is**, train a small discriminator on those labels, score it
  honestly. *(in progress — 75 of 240 frames labelled)*
- **A** = static pitch mapping. Convert camera-pan data into static field data —
  keyframe homography + propagation riding the camera-shift table. **Long-running
  / overnight work on A is explicitly authorized.**

**The live work request (what to build next), verbatim:**

> "I did a bunch of the ball finds. Sometimes though, it is hard to know where it
> is and would be more helpful to maybe see a clip since we can track the ball
> and predict where it will be, narrowing down where the ball could be from the
> whole image to just a small region."

**The framing request, still in force:**

> "I want to get started on testing on the footage asap. My idea is that we work
> together to get the statistics for this video and me, the human, can verify if
> numbers look right or wrong. once they look right, then we must be ready for
> more videos."

And: *"There's still more to be done. Keep working until you count the right number."*

---

## 2. Hard constraints — do not violate these

- **Do not push.** `main` is what GitHub Pages deploys. **28 commits are local and
  unpushed** through `ce02791`. Ask before pushing. Working tree was clean.
- **Do not send the coach email.** Alex said: *"Do not send it, but show me an
  example."*
- **Do not change `firestore.rules` caps, or the `ball-conf` / `imgsz` defaults in
  the repo**, without asking. Pass them as CLI arguments instead.
- **Produce no metres-based figures.** Calibration is unavailable. Use `length_ph`
  (player heights) instead.
- **Do not download an openh264 or ffmpeg binary.** There is no H.264 encoder on
  this machine — `cv2.VideoWriter` with `avc1`/`H264`/`h264` all fail, and `av`,
  `imageio_ffmpeg`, `imageio`, `moviepy` are all absent. This is why "show a clip"
  must be delivered as frames / a blink / an overlay, never a video file.
- **Do not re-run `$SP/genlabel.py`.** Its `main()` deletes and renumbers
  `label/frames/*.jpg` (lines 106-107), which would orphan all 75 labels.
- **Do not ship motion as an automatic ball filter** — measured 0/11 zero-alarm
  frames (§5).
- **Do not score passes with the ±1.0 s time-only proxy.** It is discredited. Use
  `$SP/scoregt.py`'s rule: within 1.0 s **and** within 150 px of the human's
  passer point.
- Credentials/sign-in are prohibited, which is why the published artifact has
  never been rendered by Claude — live verification is Alex's.

**Standing UI preferences:** *"Keep using bars to reflect statistics in the ui."*;
*"there should be information all over the screen... side bars, groupings of stats
by type... the side bar to load in new stats, not scroll down on the page"*;
*"avoid long and large files"*.

**Commit attribution** (if a commit is ever authorized): end the message with
`Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

## 3. The strategic read — the one thing to keep in mind

It is not five broken statistics. It is **one broken input inherited five times.**

```
                    ┌─ passes        (17 claimed, 4/8 recall, 24% precision)
                    ├─ possession    (37/63 from 12s of 60, flips at 20px)
ball position ──────┼─ turnovers     (5 vs Alex's hand count of 3)
  (12 px wide)      ├─ touches       (never scored)
                    └─ territory     (ball-gated AND calibration-gated)
```

Fix the ball and five statistics move. That is why B comes before everything else.

**What is solid and must not be rebuilt:** player detection (median 24
players/frame, p10 18, p90 30), tracking (582 tracks over 1800 frames), kit
classification (4/4 correct at imgsz 1920).

---

## 4. The footage and the annotated window

```
C:/Users/alexv/Downloads/8-26-26 Scrimmage v Gov Livingston - Tactical.mp4
4,611,487,721 bytes   1920x1080   30.000 fps   216120 frames   7204.0 s
```

**The hand-annotated minute: 6220.0–6280.0 s, source frames 186600..188399 (1800).**

Measured apparent ball size on the 1920-wide source: **11.7–14.3 px (median 12.7).**

**Window-relative vs absolute frame indices are a silent killer.** Getting it
wrong yields "no ball" on all 16 marks with no error. Window-relative
(`int(pt['f'])`): `ballpath.py`, `ballvit.py`, `ballcmp.py`, `balldiag.py`,
`consensus.py`, `whereto.py`, `wasbscore.py`, `wasbrank.py`, `wasbsep.py`,
`wasbfilt.py`. Absolute (`FIRST + int(pt['f'])`): `blobpath.py`, `ballgt.py`,
`tiletest.py`, `whiteblob.py`, `genlabel.py`.

In `$SP/labels.json`, `x`/`y` are **source pixels** and `abs` is the **absolute**
source frame — seek the video with it directly, do **not** add `FIRST`.

---

## 5. Everything measured about ball detection

### The full baseline scoreboard — authoritative, do not re-run

```
YOLOv8n raw (per-frame, class 32)      2/16
YOLOv8n tracked (shipped pipeline)     4/16   at median error 262px   <- THE BAR
white blobs raw                       14/16   but 30-397 candidates/frame
                                              (RE-MEASURED: 26/39 = 67%)
white blobs tracked (24 DP configs)     5/16   at median error 432-805px
ballyolo.pt raw @3840                   9/16   at 23.3 candidates/frame
ballyolo.pt raw @2560                   6/16   at  5.8 candidates/frame
ballyolo.pt tracked, ALL configs      0-1/16
WASB whole-frame step=3 raw+tracked     0/16   at median error 227/251
WASB whole-frame step=1 raw+tracked     0/16   at median error 295/490
WASB grid 4x4 step=3 RAW                6/16   at 37.8 cand/frame, median 86px
WASB grid 4x4 step=3 TRACKED            1/16   at median error 501px
WASB grid 3x3 step=3 RAW                6/16   at 27.0 cand/frame
ORB motion rank over white blobs        1/45 top-1, median rank 26
```

**The physical ceiling is 14/16, not 16/16** — p5's From point is entirely
off-frame and p1's From is bare grass at the crosshair.

**The premise, measured: recall is not the bottleneck, RANKING is.** White blobs
find the ball most of the time; nothing ranks it above scenery, so no tracker
converts it.

### Two structural bugs that outlive every experiment

1. **`cv/ball.py::drop_static_candidates` keeps 0 of 9 true ball detections at
   imgsz 3840.** It was density-tuned to sparse COCO output and has now destroyed
   three separate candidate sources. Its scenery test is in **frame space, not
   world space — and the camera pans.** It must be OFF or replaced for any new
   candidate source, including the plan-B discriminator.
2. **`cv/ball.py::build_trajectory`'s score is additive per node** — a 1000-node
   chain always outscores a short correct one, at any LINK including 0. The
   objective is literally "longest smooth chain", which is static scenery. The
   solved path's mean step is 8.7 px. **`$SP/wasbscore.py::track` is a working
   replacement.**

### Tiling geometry (verified at runtime)

| grid | crop | scale to 512×288 | ball px | tiles | runtime |
|---|---|---|---|---|---|
| 1x1 | 1920×1080 | 3.75× down | **3.4** (invisible) | 1 | 23.7 s |
| 3x3 | 768×432 | 0.667× | **8.5** | 9 | **185.8 s** |
| 4x4 | 576×324 | 0.889× | **11.3** | 16 | **319.8 s** |
| 5x5 | 461×259 | 1.11× | **14.1** | 25 | ~500 s (est) |

3×3 gives the same 6/16 on a lighter flood than 4×4 (27.0 vs 37.8 cand/frame) in
185.8 s vs 319.8 s — 3×3 is the better tiling.

### Ruled out by measurement — do not revisit

Box-size gates; excluding candidates inside player boxes (the ball is inside a
player box **10/16** of the time); bigger COCO models (yolo11x labels the ball a
tennis racket 7/16); tiling *with YOLO*; imgsz 4608; LINK_REWARD sweeps;
length-neutral MISS; conf_floor × MISS; multi-scale consensus (3840∩2560 drops
9/16 → 5/16); score thresholds on tiled WASB; peak/area/density as a
hand-designed ranking; persistence/motion filtering on tiled WASB; the whole
tiled-WASB→tracker path (1/16); and camera-compensated motion as an automatic
candidate ranker or filter.

### The motion gate — the decisive recent result

`$SP/motiongate.py` (265 lines) processed 45 ball frames + 11 certified ball-free
frames, k=±4 (0.13 s), with two registration methods side by side. 16 s runtime.
Output at `$SP/motiongate.json`, log at `$SP/motiongate.log`.

**VERDICT: it SPLITS. PASS as a human visual aid, FAIL as an automatic filter.**

ORB/RANSAC registration (use this one):

```
alignment residual (median |diff| over frame, 0-255)
   p50 1.03   p90 1.83   max 2.93          <- the pan is essentially removed

(i) does the ball survive?   motion score at the clicked pixel
   ball      p10 6.9   p50 45.4   p90 106.3   min 4.8
   frame p99 p50 19.3    frame p50 p50 0.6    <- median ball beats the frame's 99th pct

(ii) how much narrows?  fraction of frame scoring >= the ball
   p10 0.0006   p50 0.0035   p90 0.0207   worst 0.0270
   -> median search area 7293 px of 2073600  (0.35% of frame)   <- 285x NARROWING

     white-blob candidates outranking the ball by motion
   rank of ball   p50 26   best 1   worst 100   top-1 hits 1/45
   candidates/frame  p50 169
     top 1: 1/45 2%   top 3: 2/45 4%   top 5: 5/45 11%
     top 10: 14/45 31%   top 20: 20/45 44%

(iii) threshold 6.9 keeps 40/45 balls (89%)
      in the 11 frames with NO ball it passes
      p50 71 of 164 candidates   worst 163   zero-alarm frames 0/11  <- USELESS AS A FILTER
```

Phase-correlation (translation) registration — **discard**: residual p50 22.77
(22× worse), area frac p50 0.0403 rising to p90 0.5898. **The camera pans, zooms
AND rolls, so phase correlation cannot register it. ORB/RANSAC is the right
method and is already written as `motiongate.py::fit_orb`.**

**The trap:** `trans` gets a *better median candidate rank* (13 vs 26) while
producing a far worse map. Do not read the rank column alone.

**f042 is the single top-1 hit**: ball score 117.8, rank 1, area frac 0.0002 —
and `blobNear 50.0`, meaning the white-blob generator missed it entirely. Motion
finds balls the blob generator cannot. **This argues for a motion tint layer over
the raw image, not motion-scored rings on existing candidates.**

**The headline comparison was pre-set and motion did not beat it.** The four
hand-designed WASB features (mass/peak/area/density) gave median rank 21–28 and
0 top-1 hits over 16 marks. ORB motion gives median rank 26, 1/45 top-1. Motion
is not the missing ranking feature.

### Why the motion table is bimodal: ball speed (`$SP/mgspeed.py`)

```
n=35  pearson(speed, motion) = 0.58   pearson(speed, log areaFrac) = -0.49

slow  <100 px/s  n=14  motion p50    8.6   areaFrac p50 0.0125  (1.25% of frame)
fast >=100 px/s  n=21  motion p50   83.8   areaFrac p50 0.0025  (0.25% of frame)
```

**Read it honestly: r = 0.58 is real but not deterministic.** f021 at 423 px/s
scores only 8.0; f019 at 196 px/s scores 11.3 with the worst areaFrac of the set;
f006 at only 24 px/s scores 45.4. Speed explains about a third of the variance;
background contrast and occlusion do the rest. **Never tell Alex "fast balls
always light up".**

Design consequence: even the weak half narrows the search — 1.25% of 1920×1080 =
~25,920 px, an 80× reduction; the strong half is 0.25% = ~5,184 px, a 400×
reduction. But the tint ramp must keep a score of ~5–10 visible, or the aid will
look broken exactly on the frames Alex struggles with most (settled balls at
players' feet).

---

## 6. Alex's 75 labels — the new ground truth

**Progress: 75 of 240 frames (31%).** Persisted at `$SP/labels.json` — no
round-trip to the artifact needed.

**TEST POOL — complete, all 50 frames (f000–f049):**
- **39 `kind:"ball"` with x,y** (source px)
- **10 `kind:"none"`** — certified "no ball visible": f005, f022, f036, f037,
  f040, f041, f043, f045, f047, f049
- **1 `kind:"skip"`**: f023

**The honest test set grew from 15 frames to 39 ball positions plus 10 certified
negatives.** Previously there were zero certified negatives. Any detector
claiming a ball in those 10 frames is provably wrong.

**TRAIN POOL — 25 of 190 attempted (f050–f074):**
- **18 `skip`**: f050–f063 (14 consecutive), f068, f069, f070, f072
- **6 `ball`**: f064, f065, f066, f067, f071, f073
- **1 `none`**: f074

**The skip-rate asymmetry is the quantified form of Alex's complaint:**

```
test pool   1 skip  / 50 =  2%   <- the annotated minute, familiar, play concentrated
train pool 18 skips / 25 = 72%   <- cold footage from across the whole match
```

*"Sometimes it is hard to know where it is"* = **72% of train frames.** The motion
aid is needed *in the train pool*, exactly where the sampled set offers no
neighbours.

> **Superseded 2026-09-21.** This 72% was measured on the first 25 train frames.
> Over the finished 240-frame pass the skip rate is **10%** (23 frames), and the
> skips are ambiguity (several balls out during practice segments), not
> invisibility. See the correction in §8c.

**Frame spacing, verified:** test frames are +23 source frames (0.767 s) apart
(with irregulars where the original 8-pass mark frames were injected: f020, f024,
f035, f039, f042, f044, f046, f048); train frames are ~1026.3 frames = 34.2 s
apart. **Prediction cannot work across the sampled set** — the ball crosses most
of the pitch between train frames. The reliable neighbour window is ±3–4 frames
pulled from the video.

**Labelling rate, measured:** f008–f074 took 789 s for 67 frames ≈ **11.8 s per
frame**. 240 frames ≈ **48 minutes**.

**Only 6 train positives exist. The discriminator cannot be trained yet.** At a
72% skip rate, the remaining 165 train frames yield perhaps 40–50 positives
total — thin for a CNN, workable only with heavy augmentation plus the free
negatives.

---

## 7. The labelling bench (the artifact)

```
https://claude.ai/artifact/DdRfw4VENqx1AoGS8dPFB5
title "Find the Ball"   favicon ⚽   contract 0.2.52   capabilities db   sharing owner
241 published files (manifest.json + frames/000.jpg .. frames/239.jpg)   42 MB
```

Published with `file_path` = `$SP/label/index.html`, `root` = `$SP/label`,
`files` = plain-list form. **The watch on it has ENDED and will not resume on its
own.** Labels are readable on demand with `ArtifactData` (deferred — load via
`ToolSearch select:ArtifactData` first), collection `labels`.

**★ THE FILE-COUNT CEILING:** the artifact holds **241 of 255 permitted files**
and 42 MB of 64 MB. **Only 14 more files are available.** One neighbour image per
frame (240 files) is impossible regardless of bytes. The file count binds long
before the byte count.

**Four deliberate design decisions, all still load-bearing:**
1. **The annotated window is held OUT of training.** TRAIN excludes source frames
   186600–188399. TEST is inside it. **Folding the 39 test labels into training
   would destroy this — do not.**
2. **The interaction is "where is the ball?", one click** — a positive (the click)
   plus free negatives (every white-blob candidate >~40 px away). "No ball
   visible" yields pure negatives.
3. **Frames ship as Artifact supporting `files`, not assets** (255 entries / 64 MB
   per version in ONE call).
4. **Candidate rings are NOT burnt into the JPEG** — coordinates live in
   `manifest.json`, drawn on canvas, toggleable, **default OFF** (`r`). *A ring
   the generator missed must not talk the labeller out of a ball that is really
   there.* Their real value is post-click feedback, which is what produced the
   26/39 measurement.

**Element-ID inventory** (`$SP/label/index.html`, 26,910 bytes — too large for a
Read summary; re-read it or use `sed -n` before editing):

```
bAll bAllN bAllS bTest bTestN bTestS bTrain bTrainN bTrainS
banner btnExport btnNext btnNone btnOk btnPrev btnRings btnSkip
exportHint headSub loupe mAbs mCand mIdx mPool mT mXY over
pAll pTest pTrain stageTag status strip zoomTag
```

The export button is `btnExport`, **not** `btnCopy`. Filmstrip cells are `#strip`
childNodes with class `cell`.

Module state: `ZOOM = 6`, `NEAR = 15`, `M`, `db`, `frames`, `labels` (i →
{kind,x,y}), `cur`, `point`, `hover`, `rings`, `img`, `imgReady`, `cache`;
storage key `findtheball.v1`; doc id `labels/fNNN`.

**Keyboard map** — click sets point; arrows nudge 1 source px (shift = 5);
Enter/Space confirm; `n` not visible; `s` skip; `r` rings toggle; `u` undo;
`,` `.` prev/next. **Free keys for a motion feature: `b`, `m`, `h`, `x`.**

`drawLoupe()` is the function a motion overlay must hook into. `undo()` walks
back to the nearest labelled frame (because committing advances); the fix is
**confirmed working in production** — f021–f024 and f064 carry `"version":2`.

**The jsdom harness `$SP/labeltest.js` (185 lines) is 22/22 PASS and must stay
green after any page change:** `node $SP/labeltest.js`, ~2 s. It tests the
coordinate maths — a centre click must report source pixel 960,540; the loupe
crop span must be 560/(6*2); `near` must agree with an independent
nearest-candidate computation. It cannot test layout, fonts, touch input, or the
real `db` round-trip.

**The `db` capability contract:** declare `capabilities: {db: {}}`; `const db =
await claude.use("db")` resolves **`null`** when the view cannot run db — branch
on it. `set()` is a full replace; `update()` requires the doc to exist; one write
at a time per document, on a user action, never from render code or a timer.
Last-writer-wins, no transactions. Document ≤256 KiB; 64 subscriptions per view.
**Omitting `capabilities` on a redeploy carries the stored declaration forward**
and preserves the contract pin; `{}` clears it. A declaring artifact is
organization-internal and cannot be shared publicly.

**Cannot be served locally.** An attempt to add a `label` config on port 8099 to
`.claude/launch.json` was denied by the auto-mode classifier
(`[Expose Local Services]`), and a `file://` fetch of `manifest.json` is
CORS-blocked. The artifact is the only way to run the page.

---

## 8. THE MOTION AID - BUILT AND SHIPPED 2026-09-18 (version 2 of the artifact)

Shipped to `https://claude.ai/artifact/DdRfw4VENqx1AoGS8dPFB5`, 243 files.
`index.html` 31,110 B, `manifest.json` 627,199 B, `motion.png` 2,580,767 B.
`node $SP/labeltest.js` is **31/31** (9 motion checks added to the original 22).

What the design below became once it was measured against the 45 hand-clicked
balls (`$SP/sheetcheck.py`, `$SP/tophat.py`, `$SP/brightrank.py`):

* **The brightness knee at 128/255 is the whole result.** Cells at or above it
  rank **p50 142 of 20,736**, 23/28 in the top 200, brightest in the frame 4
  times. Cells below it rank p50 1,448 and **0/17** reach the top 200. No ball
  falls between brightness 118 and 168, so the split is observed, not fitted.
  Physically: the ball is either in flight (motion points at it) or settled
  (motion has nothing, and says so by staying dark). 38% of frames stay dark -
  that is an honest answer, not a failure.
* **The precise NMS peaks were dropped.** Top-40 peaks matched the ball in only
  7/45 frames; they point at players. `mp` is not in the shipped manifest.
* **Top-hat is a dead end.** Sizes 15/25/41 all scored the same as plain
  (top-200 = 23 for every variant): player *edges* in a difference image are
  small structures too.
* **The loupe is deliberately NOT tinted**, against step 5 below. The stage
  answers "where do I look"; the loupe answers "is that a ball", and tinting it
  would hide the answer. A regression test pins this.
* The overlay has three states (off / moving only / all motion) on `m`,
  defaults off, and 45/45 hand-clicked balls survive pooling, the sqrt ramp,
  8-bit quantisation and the PNG round-trip to a lit cell.

The original design, for reference:

## 8b. The settled design as written before it was built

This answers Alex's live request. It is a **"look here first" layer, not a filter.**

1. Write `$SP/motionsheet.py` (use the **Write** tool — large file), reusing
   `motiongate.py::gray`, `fit_orb`, `motion_map`. **ORB only** (drop `trans`),
   **k=±4**, over all 240 manifest frames. ~70 s of compute plus video seeks — run
   it in the background.
2. Max-pool each motion map to a **192×108** grid (10 source px per cell), encode
   `v = 0 if m < FLOOR else 255*sqrt((min(m,CAP)-FLOOR)/(CAP-FLOOR))` with
   **FLOOR = 2.5, CAP = 120**. The floor zeroes grass noise so the PNG compresses;
   the sqrt keeps a dim ball (min 4.8 → ~35) visible.
3. Pack all 240 grids into **ONE grayscale PNG sheet**, 16 cols × 15 rows =
   exactly 240 → 3072×1620, written to `label/motion.png`. **Costs 1 of the 14
   remaining file slots** — far smaller and faster than base64-in-JSON.
4. **Patch `manifest.json` in place — do NOT re-run `genlabel.py`.** Add top-level
   `{'m': {'file':'motion.png','gw':192,'gh':108,'cols':16,'rows':15,'cell':10,
   'k':4,'floor':2.5,'cap':120}}` plus per-frame `mok` (ORB registration
   succeeded) and optionally per-frame `mp` = top-40 precise NMS peaks
   `[x,y,score]` (~96 KB total) for crisp loupe markers.
5. Page changes in `$SP/label/index.html`: a toggleable motion tint over the stage
   **and inside `drawLoupe()`**, bound to a free key (`m` preferred), defaulting
   **OFF**. Draw it over the raw image, not onto the candidate rings — f042 proves
   motion finds balls the blob generator misses.
6. **Re-run `node $SP/labeltest.js`; it must stay 22/22.**
7. Republish to **the same URL** `https://claude.ai/artifact/DdRfw4VENqx1AoGS8dPFB5`,
   keeping the stable frame index `i`, with **`capabilities` omitted** so the
   stored `db` declaration and contract pin carry forward. Budget: 14 files and
   ~22 MB remain.

---

## 8c. THE CLIP FLIPBOOK — BUILT AND SHIPPED 2026-09-18 (version 5 of the artifact)

**The heatmap was the wrong answer.** Version 2 (§8) shipped a static motion
tint, and Alex's reply was: *"When I meant motion I meant like a few frames and
a small clip before the frame in question so I can track the ball and see where
it went."* The tint says *where something moved*; the eye needs to *watch the
ball travel*. Keep §8 — the brightness-knee result is still the best ranking
signal measured — but stop treating it as the labelling aid. It is not.

Version 4 shipped that clip at ±4 frames. Alex came back with two things:
*"Make it +- 10 frames instead of 4. Also for some reason the quality majorly
dips whenever the clip starts."* Version 5 is both.

### What shipped

Version 5 at the same URL, `index.html` 38,688 B (was 37,498 B),
`manifest.json` 534,300 B (was 628,202 B — the drop is whitespace only,
`json.dumps(separators=(',',':'))`, not content). Contract 0.2.52,
`capabilities` omitted so `{db:{}, assets:{}}` carried forward. Frame index `i`
unchanged — the 75 existing labels are keyed by it in `db` at `labels/f%03d`.

Every bench frame gets the **twenty-one source frames abs−10 … abs+10**, each
one warped into the current frame's coordinates by the same ORB/RANSAC fit the
motion sheet used (`cv2.estimateAffinePartial2D`, ≥12 matches, ≥10 inliers).
With the camera removed the pitch stands still and the only things moving are
the players and the ball — which is precisely what makes a 4 px dot findable by
eye. **240/240 frames registered clean at ±10; 0 adrift, 0 without a clip** —
the wider reach cost no registrations at all.

That is **667 ms of travel** either side, up from 267 ms. The status line on a
guarded click now reads *"That was 333 ms before this frame — snapped back"*.

In the page: `c` toggles the clip, `p` plays it (ping-pong at 9 fps ≈ 3× slow
motion — a loop would jump-cut +10 → −10 and the eye reads that as
teleporting), `[` / `]` step one frame, and the scrubber does the same by hand.
The loupe magnifies the *clip tile*, not the still, so 6× zoom works while
scrubbing.

**The quality fix, which is the other half of version 5.** The perceived dip
was never JPEG — it was **upscaling**. The still is 1600×900; a tile is 640×360
blown up 2.5× onto the same stage, so merely turning the clip on made the
picture visibly softer. The rule now:

* **Standing still at offset 0, the still wins** — on the stage *and* in the
  loupe. That is the only instant the label is actually clicked, so it is the
  only instant sharpness matters.
* **While playback runs, the tile wins even at offset 0.** Swapping resolution
  once per sweep would read as a flash — worse than one soft frame in
  twenty-one.
* The sheet is still fetched when the clip is switched on, so the first `[` or
  `p` never waits on the network.
* Overlay suppression needed a *second* predicate for this. `clipShifted()`
  (offset ≠ 0) still guards the click; the new `clipMoving()` (offset ≠ 0 **or**
  playing) guards the rings and the tint, because during playback offset 0 is
  one frame in twenty-one and overlays blinking on for it read as a flicker.
* Build quality also went **q58 → q72**, which is what the extra bytes bought.

**The safety rule, unchanged and still the important one:** a shifted tile is a
*different instant*. Candidate rings and the motion tint are suppressed
whenever the clip is moving, and a click at a shifted offset **never becomes a
label** — it snaps back to offset 0 and says how far off it was. A click during
playback at offset 0 now simply *stops* the sweep and labels normally. Arriving
at a new frame always resets to offset 0 unless playback is running.

`CZERO` is no longer a property of the page: `boot()` derives it from
`M.clip.offs.indexOf(0)` and sets the scrubber's range from
`M.clip.offs.length`. **The build owns the reach.** Changing `OFFS` in
`clipbuild.py` and rebuilding is now the whole change — no page edit.

### Where the pixels live, and why

* **The box-delta encoding is dead — measured, not guessed** (`$SP/clipcost.py`).
  The idea was to ship only the pixels that changed after stabilisation. But the
  full-res grass texture still differs by >16 gray levels over **40–120% of the
  frame**, so packed "changed" boxes cost **more** than the plain image. Plain
  JPEG is the encoding. Do not revisit this.
* **Colour costs only ~13% more than grey** (`$SP/clipq.py`) and keeps the one
  channel separating a white ball from a white shirt. Colour.
* **640×360 tiles, q72.** The ball is 4 px there (12 px at source).
* Packing: 21 columns (offsets −10…+10) × **5** rows (frames) per sheet =
  **13440×1800**, **48 sheets** for 240 frames, **145,013,852 bytes total**.

**The geometry was settled before any code was touched, and the constraint is
not bytes.** 21 × 12 rows would be 13440×4320 = 58 Mpx ≈ 232 MB decoded, which
is unsafe. 21 × 5 = **24.19 Mpx**, which matches the proven 5760×4320 sheet
(24.88 Mpx) and keeps the width under Chromium's ~16384 px canvas limit.
Raising tiles to 800×450 was rejected outright: 21 × 800 = 16800 px overruns
that limit, and transposing to dodge it would need 60–80 sheets. **Each sheet is
one upload tool call, so the binding constraint is call count, not quota.**

**The published-file budget could never have held this.** 243 of 255 slots and
45,060,185 of 64,000,000 bytes are used — ~12 slots and ~18 MB left against a
138 MB flipbook. So the sheets live in the **asset store**, whose quota is
**1,073,741,824 bytes (1 GB) and 5,000 files per artifact** — measured, not
documented. The byte pressure that shaped this whole project's designs only
ever applied to *published files*. An uploaded asset serves at
`/_blob/<32 hex id>` in every view and is referenced verbatim as an `<img>` src;
`manifest.clip.sheets` is that list of 48 URLs in sheet order, and
`$SP/ids.txt` is the same list, one id per line, as the build's own record.

**Asset uploads parallelise.** Three sequential probes, then batches of 5 and 8
`Artifact publish … asset:true` calls in one message all succeeded — 48 uploads
in 7 round trips. Worth knowing before the next rebuild.

A decoded 13440×1800 sheet is ~72 MB of bitmap, so the page keeps **2 sheets**
by LRU and nulls `onload`/`onerror` before clearing `src` on eviction (clearing
`src` fires `onerror`, which would otherwise pin the entry and make an evicted
sheet unreloadable).

**The 20 old ±4 sheets are still in the asset store and are now orphaned** —
49,621,309 bytes that nothing references. The store reads 68 files /
194,635,161 of 1,073,741,824 bytes, so they are harmless; cleanup is 20 delete
calls whenever it is worth the round trips.

### Files and tests

* `$SP/clipcost.py` — encoding bake-off (killed box-delta).
* `$SP/clipq.py` — size / colour / quality table.
* `$SP/clipbuild.py` — builds `$SP/clips/clip%03d.jpg` + `clip.json`.
  `OFFS = list(range(-10, 11))`, `PER = 5`, `q = 72`. CLI
  `--from / --to / --q`. The ±10 build took 142 s. Never touches
  `label/frames` or `label/manifest.json`.
* `$SP/clipman.py` — writes **only** `manifest['clip']`. `python clipman.py`
  gives placeholder ids for the harness; `python clipman.py ids.txt` writes the
  real `/_blob/<id>` URLs. Asserts the id count against `nsheet` and the frame
  count against the manifest.
* `$SP/clippatch.py` (v4) and `$S2/patch10.py` (v5) — anchor-and-assert patches
  into `label/index.html`. Every anchor's uniqueness is asserted before any
  edit; CR count is asserted 0 before and after. That uniqueness check is what
  caught `if (rings){` occurring **twice** in v4, and in v5 it is what kept the
  `clipShifted → clipMoving` swap to exactly the 3 overlay guards while leaving
  the click guard alone.
* `node $SP/labeltest.js` is **green against the real 240-frame manifest with
  real asset URLs** — tile row/column arithmetic, offset clamping, overlay
  suppression, the click guard, loupe scaling and cell clamping, ping-pong
  playback, frame reset, and the new still-at-rest quality rule both ways.

**One test lesson worth keeping.** Two failures during the v5 work were *test*
bugs, not page bugs, and both came from the same fact: the scrubber's `input`
handler calls `stopPlay()`. Setting `scrub.value` mid-sweep silently ends
playback, so a test can never scrub and still be playing. The mid-sweep
assertion has to start the sweep and then watch which *columns* were drawn.
The page is also an IIFE — no internal function is reachable — so a repaint has
to be requested the way a user requests one, by re-dispatching the scrubber's
`input` event.

### What this is for

The clip is the tool that makes a cold train frame labellable, and ±10 frames
of stabilised travel is 2.5× the evidence per frame that ±4 gave.

> **CORRECTED 2026-09-21, after the pass finished.** The paragraph that stood
> here said the train pool's *"72% skip rate"* was why the discriminator could
> not be trained, that *"a 12 px ball in a still frame is often genuinely
> invisible"*, and that *"165 of 240 frames remain unlabelled."* All three are
> now wrong. The finished pass is **240 of 240 labelled, 0 unlabelled**, and the
> skip rate is **23/240 = 10%**, not 72% — the 72% was measured on the first 25
> train frames and did not survive the clip. And the reason for the skips is not
> invisibility. Alex: *"Many of the skips are during practice times where there
> are plenty of balls on the field."* A skip means **the ground truth is
> ambiguous**, not that the ball is too small to see. That distinction matters:
> ambiguous frames cannot be recovered by a better aid or a better model, so 10%
> is the floor, and the remaining 217 frames are the whole dataset there will be.

**Final label counts:** train 190 (116 ball, 51 none, 23 skip), test 50
(47 ball, 3 none, 0 skip).

---

## 8d. THE DISCRIMINATOR, BUILT ON THE FINISHED LABELS — 2026-09-21

The labelling pass is done (240/240, §8c). This is what it bought, measured
end to end. Every number below is on the **test minute, which never enters
training** — enforced as `pool == 'train'` for the model fit, for model
selection, and now also for the ball-size regression that the generator uses.

### The headline

```
                                        frames        situations (runs)
generator ceiling                       45 / 47              -
ranking, per-frame argmax               19 / 47  40%      8 of 15
ranking + continuity (Viterbi)          28 / 47  60%      4 of  7
```

The starting point for this whole line of work was 38/47 proposed and 11/47
ranked first. Both halves moved.

### Why the generator was losing, and the fix

`gates.py` walks the five generator gates in order and records the first one
at which no surviving candidate is within 8 px of the hand mark. The loss is
not spread across the gates — it is one gate:

```
lost at      train   test
threshold        1       0
component       17       9      <- the entire loss
contrast         1       0
SURVIVES       106      38
```

`component` means a connected component *exists* at the ball but its centroid
is more than 8 px away. Looking at those 17 gives two populations and neither
is a threshold to tune: **8 merges** (the ball touches a white sock, the pair
share one component, the centroid sits between them) and **7 white floods**
(a 100k–150k px white region swallows the ball whole).

Connectivity is the wrong primitive for both; **scale is the right one.** A
white top-hat subtracts a morphological opening, so anything *wider* than the
structuring element is deleted and anything narrower survives at full
contrast. A ball against a sock keeps its peak while the sock goes to zero. A
merge becomes impossible by construction.

The shipped form is a **union**, not a replacement — an earlier measurement
(recorded in memory) found top-hat masks worse than components at every
operating point, and that finding still holds for *replacement*. `feat3.py`
keeps every component candidate untouched and **adds** top-hat peaks only
inside components that are already too big to be a ball at that image row:

```
SCALES  = (9, 17)   structuring elements: a far ball and a near one
NMS     = 4         peak suppression radius, source px
THR     = 25        minimum top-hat response
OVER    = 1.3       a component this many x the expected ball major is merged
PERCOMP = 12        at most this many peaks from one merged component
```

Cost and benefit, both stated:

```
                       train recall   test recall   candidates/frame
components only         106 / 116      38 / 47            821
+ unrestricted peaks    (46/47 reachable)                 4723
+ peaks in oversized     109 / 116      45 / 47           1837
  the same, at s < 60     113 / 116      45 / 47           1574   <- now
```

2.2x the haystack for +7 test frames of ceiling. It was worth it: top-1 rose
*despite* the bigger pool, because the added candidates land in frames that
were previously unwinnable.

### Two CV numbers, and only one of them is trustworthy

The pools are not comparable samples. Train is 167 frames **34 s apart over
103 minutes**; test is 50 frames **1.1 s apart over 0.9 minutes** — one
passage, one patch of grass, one lighting condition. So `rank4.py` reports:

- **frame CV** — GroupKFold over frames. Optimistic: a held-out frame's
  neighbours in lighting and region are still in the fit.
- **block CV** — GroupKFold over five *contiguous* ~20-minute stretches. A
  whole passage is held out, the same handicap the test pool has.

**Block CV is the selection metric, and MRR is the statistic.**

```
              frame CV MRR   block CV MRR
still             0.442          0.462
still + temporal  0.518          0.489   <- chosen (rank4, s < 70)
the same at s < 60   -           0.473       (rank6; see 8e)
```

This overturns an earlier conclusion recorded in this file. "Temporal
features transfer poorly" came from comparing a frame-CV gain against a test
number measuring something else. Under block CV the temporal block is the
only one that holds. `feat_t2.py`'s matched-filter-over-velocity block
(`temporal2.npz`) is the genuine dead end: it leads on frame CV (0.533) and
*loses* on block CV (0.484).

### The split channel is unlearnable from these labels

With the generator at 45/47, ranking is now the whole residual. `resid.py`
splits it by candidate source and the entire gap is in one place:

```
ball found as:  component 32   split 13
  component   top-1 17/32   median rank    1
  split       top-1  1/13   median rank  124
```

First hypothesis: the two sources are not measured in the same units. A
component candidate's ring is grass; a split candidate's ring is the sock or
flood it is embedded in, so `con`, `grass_ring`, `s_ring` and `rel_con` mean
different things depending on where the candidate came from. Fix: a
**source-independent top-hat family** (`th_peak, th_ratio, th_r, th_over,
th_iso, th_frac`) — same operator everywhere, surround already subtracted,
blind to connectivity.

It measurably helped, and only on one side: components 14/34 -> **17/32**,
median rank 2 -> 1; `th_over` and `th_frac` both land in the top features by
gain. **Splits did not move at all** (1/13).

The reason is countable:

```
train  frames with the ball 109   component can win 106   split-only   3
       split rows 154,847  of which positive 141
test   frames with the ball  45   component can win  38   split-only   7
       split rows  65,582   of which positive  71
```

**Three training frames where the split is the only way to win, against
155,000 split negatives.** That is not a feature deficiency — there is almost
nothing to learn from. It is an artefact of the sampling: the test minute
happens to contain the white-flood run and several merges, which are rare in
a train pool sampled 34 s apart.

**Tested and rejected:** thinning the split pool by expected size. `th_over`
does separate split positives (median 0.98) from negatives (1.51), and the
band `th_over in [0.4, 2.0]` holds the full test ceiling 45/47 while cutting
1837 -> 1438 candidates/frame for one train frame. Re-ranked, it changed
nothing: top-1 18/45 -> 17/45, MRR 0.447 -> 0.445. A fifth less haystack
bought nothing, which confirms the diagnosis — the splits are not drowning in
near-misses, they are untrained. **The unfiltered generator stays.**

The honest conclusion: **the split channel needs labels, not features.** A
handful of extra train frames chosen *because* they contain a merge or a
flood would be worth more than any further feature work here.

### Continuity: `track.py`

Per-frame argmax gets 19/47 with the true ball at **median rank 4 and inside
the top ten 53% of the time**. Nearly right, nearly always, is what a tracker
collects. `track.py` runs a Viterbi over candidates: each test frame
contributes its top 40, the emission is the ranker's score, the transition is
a penalty on how far the ball must have travelled, gated at 1100 px/s applied
to the *real* 1.1 s gap. Scored with `wasbscore.py` semantics (`TOL = 60` px),
raw and tracked reported separately and never merged.

```
lambda      0    0.5    1      2      4      8     16     32     64    128    256
recall/47  19     20    20     20     19     23     24     25     28     24     20
```

**Every weight from 0.5 to 256 beats raw**, so the gain exists independent of
the cell; the peak 28 was chosen on the test pool itself and should be read as
an upper bound, with ~23–25 the defensible figure.

**Two caveats that matter more than the headline.**

1. **Read as runs, the gain mostly is not more situations found.** Raw wins 8
   of 15 runs; tracked wins 4 of 7. What continuity actually buys is
   *coherence* — one unbroken 17-frame correct track instead of four
   scattered singletons. For possession and passes, which integrate over
   time, coherence is the more valuable of the two. But "60% of frames" is
   not "60% of situations", and it never was.
2. **There is no usable "is the ball present" threshold.** The tracker fires
   on all 3 empty frames at every lambda. Sweeping the cost of declaring a
   frame empty:

   ```
   MISS   recall/47   false alarms on 3 empty   ball frames dropped
   0.50   24  51.1%            1 / 3                    13
   1.00   27  57.4%            3 / 3                     1
   1.50   28  59.6%            3 / 3                     0
   ```

   Suppressing one of three false alarms costs 13 ball frames. **The ranker's
   score is a ranking, not a detection confidence** — anything downstream that
   needs "the ball is off the pitch" must get that from somewhere else.

### Two pruning ideas that failed, recorded so they are not retried

- **NMS radius 6** *costs* recall: a brighter neighbour suppresses the ball.
- **The 8-direction isotropy test** costs train recall badly, 98.3% -> 84.5%.
- **Capping the candidate pool by top-hat response** is impossible: the ball's
  median rank among raw peaks is 1017 (train) / 1672 (test).

### Files

**Superseded by the 8e rebuild** - `feat4.py`/`feat_t4.py`/`rank6` replace
the 3-series below, and `track.py`/`verify.py` now load them.

All in this session's scratchpad. `feat3.py` -> `still3.npz` (36 columns,
398,648 rows, 1837 cands/frame); `feat_t3.py` -> `temporal3.npz` (30 columns,
row-aligned); `rank4.py` -> `rank4.model.json`, `rank4.json`; `resid.py` (the
by-source residual); `track.py` -> `track.json`; `gates.py` (the waterfall);
`tophat.py` (the scale-vs-connectivity measurement). `rank5.py` is the
rejected band filter, kept so the negative result is reproducible.

---

## 9. Three corrections owed to Alex — deliver these

1. **White-blob raw recall is 67% (26/39), not 88% (14/16).** The `near` field
   records distance from the click to the nearest white-blob candidate; over the
   39 test ball marks, `near ≤ 15` holds for only 26. The old 14/16 figure was
   measured only on frames a human had already found the ball in — selection
   bias. Including the 6 train positives gives 31/45 = 69% overall. The `near`
   distribution is bimodal (sub-3 px or >14 px, almost nothing between), so there
   is no threshold to tune. **67% must replace 88% everywhere.** (The stale 14/16
   claim is still written into `genlabel.py::candidates()`'s docstring.)
2. **The labelling-effort estimate was wrong.** ~15 minutes was quoted; the
   measured rate is 11.8 s/frame ⇒ **≈48 minutes** for 240 frames.
3. **"Predict where it will be" cannot work across the sampled set** — test frames
   are 0.77 s apart, train frames 34.2 s apart. The reliable window is ±3–4
   frames pulled from the video.

---

## 10. Phase A — static pitch mapping (authorized as overnight work)

**The strongest new evidence that Phase A is tractable:** `motiongate.py::fit_orb`
is a measured, working per-frame similarity estimate — residual p50 1.03, p90
1.83, max 2.93 over 112 fits on this exact footage. It is directly reusable as
Phase A's registration primitive.

Plan: keyframe homography + propagation, riding the camera-shift table
`detect_camera_shift` already computes. Ball-free modules that exist:
`cv/calibration.py` (440 lines), `cv/lines.py` (672), `cv/distortion.py` (690),
`cv/camera.py`, `cv/pitch.py`, `cv/zones.py` (393), `cv/coverage.py` (299).

Then: **map the passes onto the static pitch for coaches** — Alex's original
unaddressed complaint. Note `cv/territory.py` is gated on **both** calibration and
`state.ball_m is not None`, so ball-free coach stats would be new code.

---

## 11. Possession — verdict delivered, still governs

- **`PossessionSummary.share()` divides by `live_s = team_a_s + team_b_s`** —
  `live_s` is **12.13 s of 60**, nine spells. One 4.33 s spell is 36% of it and
  **reverses** the split to 58/42.
- Median player height 59 px ⇒ possession radius (1.0 PH) ≈ 59 px, opponent
  margin (0.5) ≈ 29 px.
- **Radial flip probe over 451 named-holder frames: median 20 px displacement
  changes the answer; 91% flip by 40 px.** The tracker's measured median error is
  **262 px = 4.4 player heights.**
- Jitter ablation: 10 px → dark swings 17–55%; 50 px → live 2.50 s; 100 px+ →
  live 0.00 s.
- Two reassuring findings: interpolation is not the problem (observed-only gives
  38.3/61.7 on 6.00 s); the failure mode under a bad ball is **"no answer", not
  "confidently wrong answer"**.
- Interim presentation: *"light had roughly two-thirds of the 12 seconds we could
  actually call"*, with `live_s` always shown beside it.
- `$SP/posstable.json` (3.8 MB, 1800 frames, 582 tracks) + `$SP/possjit.py` make
  further possession ablation free.
- **Alex's hand count: 34% dark / 66% light**, disregarding times the ball was
  invisible. Pipeline says 37/63.

---

## 12. Passes — current state

Committed at `ce02791`:

| | Before | After |
|---|---|---|
| Passes claimed | 28 | **17** |
| Recall | unmeasured | **4/8 = 50%** (4/7 on visible releases) |
| Precision | 14% | **24%** |
| F1 | 0.22 | **0.32** |
| Kit correct on matched rows | 4/4 | **4/4** |
| Outcome correct on matched rows | — | **1/4** |

Found: p1, p2, p6, p7.

**The eight human annotations** — the original ground truth, at
`$SP/gt/truth/p1.json … p8.json`. Points are in **natural 1120×630 px**; `f` is a
**window-relative** frame (0–1799); `t_release`/`t_arrival` are **file-clock
seconds**. Team values are `"light"`/`"dark"`.

| id | relFrame | arrFrame | t_release | t_arrival | flight | team | outcome | note |
|---|---|---|---|---|---|---|---|---|
| p1 | 440 | 511 | 6234.667 | 6237.033 | 2.37 s | light | completed | — |
| p2 | 657 | 715 | 6241.900 | 6243.833 | 1.93 s | dark | completed | — |
| p3 | 808 | 835 | 6246.933 | 6247.833 | 0.90 s | dark | incomplete | — |
| p4 | 835 | 916 | 6247.833 | 6250.533 | 2.70 s | light | incomplete | "Ball went out of frame on arrival" |
| p5 | 913 | 961 | 6250.433 | 6252.033 | 1.60 s | dark | incomplete | "Ball was out of frame on pass." |
| p6 | 974 | 999 | 6252.467 | 6253.300 | 0.83 s | light | completed | "Ball receive point covered by player." |
| p7 | 1191 | 1239 | 6259.700 | 6261.300 | 1.60 s | light | completed | — |
| p8 | 1657 | 1675 | 6275.233 | 6275.833 | 0.60 s | light | completed | — |

**Two annotations are unfair to score spatially:** p5's passer sits at x=10.45
(→18 src px); p4's arrival at x=95.57 (→164 src px). **Report recall both raw (/8)
and on the 7 with a visible release.**

**21 of 60 seconds have zero observed ball** — 6220–6231 (12 s) and 6271–6279
(9 s). **p8 (6275.233) lives inside the second blind stretch.**

Alex's hand counts: turnovers 3 (pipeline says 5); passes 4/65 right at the time.

---

## 13. Repo code map — the wiring seams

**`cv/frames.py::_ball_candidates` (`:461`) is THE single wiring seam:**

```python
    def _ball_candidates(
        self, balls, absolute: int, timestamp: float
    ) -> list[BallCandidate]:
        out: list[BallCandidate] = []
        for xyxy, score in zip(balls.xyxy, balls.conf):
            if float(score) < self.ball_conf:
                continue
            x1, y1, x2, y2 = (float(v) for v in xyxy)
            out.append(BallCandidate(
                frame_index=absolute,
                timestamp_s=timestamp,
                xy=((x1 + x2) / 2, (y1 + y2) / 2),
                confidence=float(score),
            ))
        return out
```

`cv/frames.py` ball seam map: `:66` import BallCandidate; `:69` import
`median_player_height`; `:121-123` `ball_xy`/`ball_observed`/`ball_confidence`;
`:129` `[(track_id, xyxy)]`; `:225` `ball_coverage`; `:231` `ball_observed_share`;
`:271` `ball_conf: float = 0.08` (repo default — do not change without asking);
`:354` `balls = boxes[classes == CLASS_BALL]`; `:461` `_ball_candidates`; `:479`
`attach_trajectory`. 492 lines.

`cv/ball.py` (318 lines): `MAX_BALL_SPEED_FRAC_PER_S = 1.2` (:30),
`MAX_INTERPOLATION_GAP_S = 1.0` (:35), `STATIC_RADIUS_PX = 20.0` (:62),
`STATIC_SPAN_S = 4.0` (:63), `STATIC_MIN_HITS = 25` (:64). API: `BallCandidate`
(:68, frozen), `BallPoint` (:76), `BallTrajectory` (:85),
`candidates_from_detections` (:114), `drop_static_candidates` (:136),
`build_trajectory` (:175), `_interpolate` (:260), `nearest_player` (:296).

`cv/detector.py`: `LABELS = {CLASS_PERSON: "person", CLASS_BALL: "ball"}` (:24);
`PersonBallDetector.__init__(weights=DEFAULT_WEIGHTS, conf=0.25, imgsz=960,
device=None)`. COCO ball is **class 32**. `cv/weights/` contains only
`yolov8n.pt`.

`cv/events.py` constants: `103:LOOSE_BALL_S = 1.5`; `124:MIN_PASS_LENGTH_PH = 1.0`;
`MAX_PASS_SPEED_PH_S = 12.0`; `SAME_TRANSFER_S = 0.7`, `SAME_TRANSFER_PH = 5.0`;
`166:SHORT_PASS_M = 15.0`; `167:LONG_PASS_M = 30.0`; `CONTROLLED_SPEED_PH_S = 4.0`,
`DUEL_WINDOW_S = 0.5`, `DUEL_DISTANCE_PH = 1.5`, `PRESSURE_PH = 2.5`.

Calibration gates in `cv/pipeline.py`: lines 521, 522, 603, 690, 728, 730, 804,
819, 856. `cv/pitch.py`: `42:class Pitch`, `253:class MatchOrientation`,
`278:def _flip(end: str) -> str`.

Ball-reference census (`grep -c ball cv/*.py`): `events.py` 83, `touches.py` 71,
`pipeline.py` 57, `possession.py` 52, `frames.py` 38, `framing.py` 32, `blind.py`
22, `ball.py` 22, `report_json.py` 19, `xg_bridge.py` 18, `reconcile.py` 17,
`zones.py` 10, `territory.py` 10, `phases.py` 10, `sequences.py` 8, `metrics.py` 8,
`keeper.py` 7, `timing.py` 5, `participants.py` 4, `detector.py` 4, `camera.py` 4,
`tracking.py` 4, `publish.py` 3, `coverage.py` 1, `calibration.py` 1. Zero:
`thumbs.py`, `teams.py`, `pitch.py`, `lines.py`, `identity.py`, `frame_sampler.py`,
`distortion.py`, `__init__.py`.

`cv/` inventory: 33 modules, **15,283 lines**. Largest: `events.py` 1250,
`pipeline.py` 1188, `report_json.py` 922, `metrics.py` 913, `publish.py` 719,
`distortion.py` 690, `lines.py` 672, `touches.py` 647, `xg_bridge.py` 552,
`phases.py` 548, `teams.py` 502, `frames.py` 492, `calibration.py` 440,
`possession.py` 434, `zones.py` 393, `coverage.py` 299.

**Field names:** events use **`type`**, not `kind`. **Kit labels: `team_a` = DARK
(Lab L=76.2), `team_b` = LIGHT (Lab L=160.0).**

---

## 14. Key scratchpad scripts

- **`motiongate.py`** (265 lines) — the honesty gate. `fit_orb` is reusable for
  both the motion sheet and Phase A. CLI: `python motiongate.py [--k 4]
  [--reg orb|trans|both]`. Note the argv quirk: `for i, a in
  enumerate(sys.argv[1:])` then `sys.argv[i + 2]` — works only because the flag
  precedes its value at that offset.
- **`mgspeed.py`** — the speed correlation; reads `motiongate.json` without
  touching the video.
- **`wasbscore.py`** — **the canonical scorer for any new candidate source.**
  Reports raw per-frame recall AND tracked recall separately, so a detector win is
  never confused with a tracker win. `SX = 1920/1120`, `SY = 1080/630`, `TOL =
  60.0`, `MAX_DISP = 300.0`. Its `track()` is the replacement for
  `build_trajectory`. **Extend it to the 39-label test set, not just the 16 marks.**
- **`scoregt.py`** — **the authoritative matching rule.** A claim matches only if
  `|dt| <= 1.0 s` AND `start_xy` within 150 px of the human's passer point;
  one-to-one, greedy on joint cost `dt/TOL_T + dd/TOL_D`. `FIRST, FPS = 186600,
  30.0`.
- **`wasbrun.py`** (212 lines) — the reference for tiling and the npz format. npz
  keys `f` (window-relative), `x`, `y`, `conf`, `peak`, `area`. `INP_W, INP_H =
  512, 288`. CLI: `--step {1,3} --grid RxC --overlap --out --start --count
  --thresh --batch --weights`.
- **`genlabel.py`** (180 lines) — the frame sampler. **Do not re-run.** Its
  `candidates()` is the white-blob generator, lifted unchanged from `whiteblob.py`.
- **`labeltest.js`** (185 lines) — the jsdom harness, 22/22.
- **`possdump.py` / `possjit.py`** + `posstable.json` — possession ablation.

**The reusable mark-loading idiom:**

```python
marks=[]
for fn in sorted(glob.glob(os.path.join(SP,'gt','truth','*.json')),
                 key=lambda p:int(''.join(c for c in os.path.basename(p) if c.isdigit()))):
    g=json.loads(io.open(fn,encoding='utf-8').read())
    for key in ('ballFrom','ballTo'):
        pt=g.get(key)
        if pt: marks.append((g['id'],key[4:],int(pt['f']),pt['x']*SX,pt['y']*SY))
```

**The reusable video-read idiom** — one seek then sequential reads, avoiding three
expensive seeks in a 4.6 GB file:

```python
cap.set(cv2.CAP_PROP_POS_FRAMES, a - k)
got = {}
for off in range(0, 2 * k + 1):
    ok, img = cap.read()
    if not ok: break
    if off in (0, k, 2 * k): got[off] = gray(img)
```

**WASB internals, all confirmed — do not re-derive:** output channel `j` ↔ input
frame `j`; `rescale = trans[0][0,0,0].item()`; `step=3` covers each frame once;
`HRNet.forward` returns a DICT keyed by scale so `model(batch)[0]`;
`HRNet.__init__` mixes `cfg['MODEL']` and `cfg.MODEL` access — hence
`AttrDict`/`attrify`. `trackers/online.py` has two upstream defects: `np.Inf`
(gone in numpy 2.x) and `_select_best` never assigns `xy_pred`;
`wasbscore.py::track` fixes both.

- **`clipcost.py` / `clipq.py` / `clipbuild.py` / `clippatch.py`** - the clip
  flipbook chain (§8c). `clipbuild.py` writes `clips/clip%03d.jpg` +
  `clip.json` and never touches `label/frames` or `label/manifest.json`;
  `clippatch.py` asserts every anchor is unique before it edits anything.
  `clipbuild.py::scaled()` conjugates the affine through the resize properly
  (`S(x) = s*x + 0.5*(s-1)`), not a bare multiply on the translation.

**Known harness bugs if those scripts are reused:** `balldiag.py` divides clutter
counts by 1800 when the 337 clutter candidates come from only 16 mark frames;
`whofp.py`'s "top-conf pick sits on a player: 0%" line is invalid (only 39 boxed
frames); `compare.py` parses args as `a.split('=', 1)` so labels must contain no
`=` and no unquoted spaces.

---

## 15. Environment — the things that bite

- Python is **`C:/Users/alexv/Desktop/Repos/PitchIQ/PitchIQHelper/.venv/Scripts/python.exe`**.
  Bare `python` lacks numpy.
- GPU NVIDIA GeForce RTX 4060; torch 2.13.0+cu130; cv2 5.0.0; **numpy 2.4.4** (no
  `ndarray.ptp` — use `np.ptp(arr)`; **no `np.Inf` — use `np.inf`**);
  ultralytics 8.4.108; pyyaml 6.0.3. node v24.18.0; jsdom in `$SP/node_modules`.
  git 2.45.1.windows.1.
- **pytest has no `--timeout` plugin.** Full suite = 69.50 s, 1879 tests + 18
  subtests.
- **`$SP` is not set in a fresh Bash shell** — `export SP="..."` at the top of
  every command. `cd` inside a Bash command resets the shell cwd afterwards.
- **Line endings:** the repo stores LF but `core.autocrlf=true` checks out CRLF.
  **Every repo patch script must normalize CRLF→LF before matching anchors and
  write back CRLF.** Scratchpad `.py`/`.js`/`label/index.html` are pure LF. The
  working patch idiom:

  ```python
  import io
  s = io.open(p, encoding='utf-8', newline='').read()
  assert s.count(old) == 1
  io.open(p, 'w', encoding='utf-8', newline='').write(s.replace(old, new))
  ```

- **Bash heredocs eat one level of backslash** — use `chr(13)`/`chr(10)` instead of
  escape sequences. **Large heredocs fail outright** — write big files with the
  Write tool. **`sed -i` strips CRLF.**
- Never put two heredocs plus a `||` fallback in one command — it silently no-ops.
  Never put a bare `cat > file` with no input redirect in a compound command.
  `sleep` is blocked in the foreground; never chain sleeps.
  `nohup ... & echo launched` makes the Bash tool report complete immediately, and
  a long-running background job holds the script version it was launched with.
- **No PDF text library in the venv** — fetch `arxiv.org/html/<id>v1` or ar5iv;
  never WebFetch an arXiv PDF. `papers.bmvc2023.org` does not resolve.
- Google Drive downloads work with
  `curl -sL "https://drive.usercontent.google.com/download?id=<ID>&export=download"`.
- **Runtime costs:** 60 s window at imgsz 1920 ≈ 113 s; 2560 ≈ 162 s; 3200 ≈ 506 s;
  `ballgen.py` at 3840 = 352 s; WASB whole-frame step=3 = 23.7 s, step=1 = 52.7 s;
  grid 3x3 step=3 = 185.8 s; grid 4x4 step=3 = 319.8 s; `genlabel.py` 240 frames =
  12.8 s; `node labeltest.js` ≈ 2 s; `motiongate.py` 56 frames × 2 registrations =
  16 s.
- `.claude/launch.json` is tracked by git and serves the **repo root** on port
  8080, not `$SP/label`.

**Lesson worth keeping:** `ast.parse` proves syntax, not correctness — a stray
duplicated loop passed it and then raised TypeError at runtime. jsdom is the JS
analogue: `vm.Script` passed while `setTransform` still crashed.

---

## 16. Open items

### Deferred non-ball work
- **Decide on the measured claim filter** (`team != 'unknown'` AND
  `confidence >= 0.10`): 17 → 13 claims at unchanged recall, precision 24%→31%,
  F1 0.38. **Not implemented.**
- Hold the spread≤500 / seen≥15 rule until a second annotated window exists.
- Passer/receiver attribution via `scoregt.py::box_id_at` — not run.
- Investigate the p3 −1.43 s timing offset. Outcome labelling is 1/4. Turnovers 5
  vs truth 3. Contested 47.3 s of 60.
- Run `$SP/offpitch.py` (written, never executed). Migrate `$SP/capsweep.py` off
  `TOL = 2.5`.
- Regenerate and republish the old verification sheet at
  https://claude.ai/code/artifact/315a95cd-9eb3-40be-a1ee-c0b9eccadacd once
  numbers settle.
- Small and latent: `unknown`-outcome passes count against pass accuracy in
  `report_json.py:368-369` while being unable to reach `passes_completed`.
- `wasbfilt.py`'s area-window list still lacks `(6,13)` and `(3,13)`.
- Tiled TrackNetV2 — weights downloaded (45 MB); `build()` in `wasbrun.py` needs a
  branch. Offered; not taken up.
- Consider deleting `$SP/yolo11x.pt` (109 MB) — measured useless, deliberately not
  deleted unilaterally.

### Awaiting Alex's decision
- **Three constants are active by default**: `MAX_PASS_SPEED_PH_S = 12.0`,
  `SAME_TRANSFER_S = 0.7`, `SAME_TRANSFER_PH = 5.0`.
- **The `imgsz` defaults**: `cv/detector.py:58` 960, `cv/tracking.py:73` 960,
  `cv/frames.py:272` 1280, `cv/pipeline.py:413` 1280; `cv/frames.py:271
  ball_conf = 0.08`.
- **28 unpushed commits** through `ce02791`.
- **A second annotated window** — the only way to learn whether 37/63 reproduces.
- Change the report to show `live_s` next to any possession split.

### Flags owed to Alex
- Whether to raise `firestore.rules`'s `byEvent.size() <= 1500` above
  `cv/publish.py`'s `MAX_EVENTS = 1500` (zero headroom).
- A proposal for splitting `assets/report.js` (5040 lines, 123 exports, 20 section
  seams, 20 importers) against *"avoid long and large files"*.
- **`npm run deploy:rules` is still outstanding on Alex's side.** Hardened rules
  from `f406896`, `299c12d`, `fb9273a` are in the repo and on GitHub but **not
  live in production.**
- Do NOT unilaterally decide `ROADMAP.md:2526`, or why `taggerUids` is never
  populated. `ROADMAP.md:4728` (App Check + referrer restriction) and `:4808`
  (parental consent) stay set aside. `ROADMAP.md:6620` — identify and brief the
  tablet operator.
- The third kit / referee: `OUTLIER_RATIO = 0.55` routes non-matching tracks to
  UNKNOWN.
- **Calibration remains the human bottleneck.** `FOOTAGE_DAY.md` §2: under 0.5 m
  mean is good, over 1.5 m not worth trusting.
- `tests/test_framing.py` records the gap between the YOLOv8 stride argument
  (`PLAYER_FLOOR_PX = 16.0`) and the one real observation — this footage is now
  that second clip. `tests/test_write_seam.py` at 918 lines is worth watching for
  size.

---

## 17. Suggested first move in the new chat

1. `export SP=...` (§0) and confirm `labels.json` and `label/frames/` are still
   there. **Done 2026-09-18 - the scratchpad survived.**
2. Deliver the three corrections in §9. **Done 2026-09-18.**
3. Build the motion aid per §8 and republish. **Done 2026-09-18** - shipped at
   31/31 tests; see the §8 header for what the measurements changed. Alex has
   been told it is ready. **Superseded: the pass is finished, 240/240,
   10% skipped for ambiguity, 116 train positives — see §8c and §8d.**
3b. **The motion tint was the wrong aid.** Alex clarified he wanted a short
   clip around each frame, not a heatmap. Built and shipped the same day as
   **version 4** at 61/61 tests - see **§8c**. That is the bench Alex is
   labelling on now; §8's brightness knee stays as a ranking result, not a
   labelling aid.
4. ~~Once the train pool has 40+ positives, train the discriminator~~ — **DONE
   2026-09-21, see §8d.** 116 train positives; generator ceiling 45/47 on the
   test minute; raw recall 19/47, tracked 28/47 at `TOL = 60`. The next lever is
   **labels, not features**: the split channel (merged and flooded balls) has
   only 3 train frames where it is the only way to win, and no amount of feature
   work moved it. A small targeted pass over frames containing a merge or a
   white flood is worth more than anything else on this list.
5. Then Phase A, using `fit_orb` as the registration primitive.

---

## 18. viterbi5 — a restart priced by distance from where the ball was lost

Alex's second correction, verbatim: *"in terms of fixing the losing the ball,
maybe keep track of where it was lost. Then, when the camera goes to pick it
up, it must be near the region we lost it at."*

**Baseline.** `viterbi4` (`dtrack4.py`) at `lam=0.125, skip=8.0, break=32.0,
kmax=4` scores **41/47** on the deep pool `dense200.npz` (1676 frames, 55.9 s,
98% coverage — see §5 for the manifest).

**The idea, implemented.** `dtrack5.py::viterbi5` adds one thing to viterbi4:
when the tracked path breaks, the restart is priced by distance from where it
broke, not taken for free from the single best-scoring candidate anywhere in
frame.

- `allowed = rad0 + drift * age` — a circle around the loss point that widens
  with `age` (frames since the break), in px.
- `penalty = gate * (max(0, d - allowed) / allowed) ** 2`, added to the score
  of every candidate in an `RESTK`-source (8) restart pool, instead of
  viterbi4's single best-prior-score restart.
- `gate = 0.0` is a genuine control: it makes the penalty identically zero and
  reproduces viterbi4 exactly. Confirmed clean across the full `dtrack5.py`
  grid — every gate-0 cell reproduces 41/47.

**In-sample grid (deep pool):** best = 42/47 at `rad0=0.0, drift=12.0,
gate=0.5`. Flagged by the script's own output as "not a result" until
fold-validated — one label on 47 is exactly what a 5-knob grid invents.

**Incident: `dense200.npz` got clobbered mid-investigation.** An unrelated
background timing job (`dense.py --n 40 --keep 200`) silently overwrote the
real 1676-frame manifest with a 40-frame stub, because `dense.py`'s `--keep
200` flag renames the OUTPUT file to `dense200.npz` regardless of `--n`. The
grid result above was run before the clobber and is valid. Fold validation and
the mark-by-mark diagnostic were first run against the clobbered file (void,
discarded), then re-run against the shallow pool `dense.npz` as a stand-in
while `dense200.npz` was rebuilt from scratch with `python dense.py --keep
200` (~57 minutes). All numbers below are from the rebuilt deep pool unless
marked "shallow pool."

**Fold validation (`dfold3.py`, held-out odd/even mark split, deep pool):**

```
ungated   in-sample 41   held-out 40   (9 cells)
gated     in-sample 42   held-out 42   (162 cells)
```

Both folds independently pick the *same* cell — `skip=8.0, kmax=4, rad0=0.0,
drift=12.0, gate=0.5` — whether tuned on the odd marks or the even marks. Held
out, the gate wins by 2 (40 -> 42), not by the 1 the in-sample grid showed. The
gate is not spending its extra knobs to fit noise; it is finding one config
that generalizes. (Shallow pool, run earlier while the deep pool was down:
ungated held out 37/47, gated held out 38/47, same winning cell — the deep
pool reproduces this finding and gets a larger margin.)

**Mark-by-mark (`dmiss5.py 0.0 12.0 0.5`, deep pool):**

```
time      viterbi4     viterbi5
 15.3 s   none         none          ball at x=1125   (off screen, unresolvable by either)
 16.9 s   72 px        HIT 13        ball at x=638    <- gate WINS this mark
 30.8 s   1244 px      871 px        ball at x=54
 32.0 s   313 px       313 px        ball at x=606
 32.5 s   88 px        88 px         ball at x=684
 55.2 s   73 px        73 px         ball at x=1118
```

The scored count moves 41 -> 42 on the 16.9 s flip. The number that matters
more is the 30.8 s loss, still a miss for both models but **1244 px -> 871
px** — the difference between "guessed somewhere across the pitch" and
"stayed close to where the ball was lost," which is the literal ask. A direct
step-size trace through that window (shallow pool) showed the gated path
traveling 675 px total with a 45 px max single step, against the ungated
path's 2024 px total / 1107 px max step — dramatically smoother even where it
does not flip the final scored mark to a HIT.

**Gate saturates on distance.** Worst-miss distance never drops below ~866-871
px across a broad gate/rad0/drift sweep (shallow and deep pool alike), traced
to `kmax=4` chained steps that can reach ~367 px per break without ever
invoking the restart-pool penalty at all — the gate only prices restarts, it
does not shorten the chain that precedes one.

**Deliverable, sent to Alex:** `span_lost_gated_play.mp4` — the 27-35 s window
rendered on the deep pool with `dspan.py 27 35 span_lost_gated dbest5.json`,
shrunk with `dshrink.py`, next to the original `span_lost_play.mp4` (no
memory) for direct comparison. Both are the same 1676-frame deep pool, so the
comparison is apples-to-apples.

**`pan.npz` / `pan.py` dependency — relevant to a longer time span, not yet
run.** `pan.py` does `import dense as D` and builds its cumulative-pan array
directly over `D.LO`/`D.HI` — `dense.py`'s two module constants (no CLI
override), currently fixing the manifest to exactly 1676 frames = 55.9 s, the
window all 47 hand labels live inside. `dtrack2.load()` then indexes `cum[ab -
plo]` for every absolute frame in the manifest's own range. Widening `LO`/`HI`
for a longer span therefore needs **two** passes, not one: the ~2 hour
`dense.py` detector pass over the new range, plus an additive `python pan.py`
re-run over that same new range — skipping the second pass silently
misaligns or index-errors every frame beyond the old window. There is no hand
label outside the current 55.9 s window either way, so any deliverable past it
is a continuity plot/video, not a hit-rate. Communicated to Alex 2026-09-21,
not yet started.
