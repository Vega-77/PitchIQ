# Footage day

Everything in here is already established somewhere in this repo — in a module
docstring, in a measured number, in `ROADMAP.md`. None of it was anywhere a
person standing on a touchline could act on. That is what this file is for.

Read it once before the game. Skim points 1 and 4 at the field.

---

## 0. The week before: find out how fast this machine is

Only if a **half-time** report is wanted. If the plan is to hand something over
the next morning, skip this — the pipeline can take all night.

```bash
python -m cv.experiments.speed_report clips/anything.mp4 --seconds 60
```

Any football footage will do; it is measuring the machine, not the match. It
prints the seconds of work per second of footage at each sample rate and names
the fastest rate that leaves headroom, and that rate is what to pass as
`--sample-fps` on the day.

Two things about it that are easy to get wrong:

- **Run it on the laptop that will actually be at the field.** The same machine
  on battery and plugged in are different answers, and so is one with a browser
  open. This is not a property of the pipeline.
- **Do it in the week, not on the day.** If the answer is "nothing fits", the
  fix is a smaller model or a shorter window, and neither is something to
  discover twenty minutes before kick-off. A report that arrives at full-time is
  still a good report; one that was promised at half-time and arrived at
  full-time is a broken promise.

---

## 1. The camera is the whole thing

This is not the most important item because it is first. It is first because
**nothing downstream can recover a bad one**, and we have measured that.

Two clips of the same team, same detector, no fine-tuning:

| framing | players found | ball found |
|---|---|---|
| tight, pitch fills the frame | 99% of frames | 43% of frames |
| wide stadium panorama | **0 on-field players** | **0 across 300 frames** |

At the wide framing a player is 4–8 pixels across and the ball is under two.
Cropping and upscaling do not bring it back — those pixels were never recorded.

**Rules:**

- **Pitch fills most of the frame.** The test is whether jersey numbers are
  faintly legible. If they are not, the ball is not there either.
- **The camera does not move.** Fixed on a tripod, not handheld, and **not
  auto-tracking**. Two reasons: a panning camera breaks the one-time homography,
  so the calibration would have to be redone every frame; and the auto-tracking
  camera we tested lost the ball for **12 seconds at a stretch** while chasing it.
- **Elevated, if there is any choice.** Higher is better for the homography —
  players overlap less and the ground plane is better conditioned. A press box or
  a stand beats standing on the touchline.
- **Export from the camera. Never a screen recording.** A screen recording is
  capped at the browser window's pixel count, which is usually far below what the
  camera actually captured. Every measurement we have was taken on a 720p screen
  recording, and they are close to a worst case for that reason.
- **One continuous file per half** if the camera will do it. Splits mean a second
  video offset to keep track of; one file for both halves means noting where the
  second half kicks off instead. Either way it is one number, and it is far
  easier to read off the camera on the day than to reconstruct a week later.

The wide stadium rig is a once-a-season venue for this team. Design for the
tighter position, which is what almost every game actually uses.

## 2. The calibration frame

One frame, once, per camera position. It is what turns pixels into metres, and
without it there is no distance, no speed, no shape, no shots and no xG.

```bash
python -m cv.experiments.grab_frame clips/first-half.mp4 --at 60
```

Pick a moment where the pitch markings are clearly visible — the centre circle,
the penalty box corners, the six-yard box. Then open `calibrate/` in the browser,
click the landmarks, and check the reported error.

- **Under 0.5 m mean is good.** Over 1.5 m and positions are not worth trusting.
- **Measure the actual pitch and enter the real numbers.** This is not a cosmetic
  setting. `Pitch.to_statsbomb` normalises by the configured length, so a penalty
  spot reads as ~13.9 StatsBomb units on a 95 m pitch and ~12.0 on a 110 m one —
  the model sees two different shots. A 105 m assumption on a 100 m pitch also
  overstates every distance-covered figure by 5%.
- **Redo it if the camera is moved**, including between halves.

## 3. Brief whoever is on the tablet

The live data is only as good as the person entering it, and they should hear
this before kick-off rather than during.

Three things matter more than the rest:

1. **The kick-off marker.** It is what relates the match clock to the video
   position. Everything the pipeline does with the tag log — stoppages, goal
   reconciliation, every "jump to this moment" link in a player's report — goes
   through that one number.
2. **Stoppages *and* their restarts.** `out_of_bounds` → `throw_in` / `corner` /
   `goal_kick`. The opener alone is not enough: a stoppage whose restart nobody
   tagged gets capped at two minutes and **silently deletes real football from
   the possession figures**. If the restart is missed, that is worse than
   missing both.
3. **Substitutions.** They are what scope each player's stats to the minutes they
   were actually on.

Goals and cards are worth getting right too, but for a different reason — a goal
is the one thing the pipeline and the tagger both claim to see, so it is the
cross-check. Tapping a goal thirty seconds late is fine; the matcher allows
fifteen seconds and reports the rest as a disagreement to look at, which is a
prompt, not a mark against anybody.

Tell them it is fine to miss things. A gap is recoverable. A wrong tap that
nobody knows about is not.

## 4. Before leaving the field

- [ ] Video copied off the camera, and it plays
- [ ] Both halves present, and which file is which is written down
- [ ] **Video offset noted** — seconds from the start of the video to kick-off
- [ ] **Second-half kick-off noted** — seconds from the start of the video to
      the restart, when both halves are in one file. The tablet's clock stops
      for the break and the recording does not, so without this every
      second-half timestamp lands late by however long the interval ran — ten
      to fifteen minutes, reading as plausible minutes the whole time. Scrub
      to the restart and read the time off the player. Both numbers go in the
      coach page's video form, which draws them back as a strip so a typo is
      visible before it saves
- [ ] Tag log downloaded from the coach page
- [ ] Calibration frame grabbed, or the landmark clicks saved
- [ ] Pitch dimensions measured and written down
- [ ] Anything odd noted: camera nudged, rain, floodlights, a half that started
      late

## 5. Intake, in this order

Run these in order and stop if one of them says stop. Each is cheap and rules
out a class of wasted time downstream.

**First — is the footage usable at all?**

```bash
python -m cv.experiments.spike_detect clips/first-half.mp4 --conf 0.08 --imgsz 1280
```

Look at the ball hit rate and the longest stretch with no ball. If ball coverage
is near zero, the framing was wrong and **nothing downstream is worth running** —
possession, touches, passes, shots and xG all rest on it. Fix the camera before
the next game rather than tuning code against footage that cannot support it.

**Second — how badly is identity fragmenting?**

```bash
python -m cv.experiments.track_report clips/first-half.mp4 --start 30 --end 60
```

Tracks-per-player around 10 is what the 720p screen recording gave. A native
export should be materially better, and finding out is one of the most useful
measurements available from the first real clip. Anything per-player — distance,
sprints, top speed — is a fragment until this comes down.

**Third — the full run.**

```bash
python -m cv.experiments.event_report \
    --video clips/first-half.mp4 \
    --calibration clips/first-half.calib.json \
    --tag-log clips/first-half.log.json \
    --sample-fps 30 \
    --json baselines/2026-08-15-first-half.json
```

`--sample-fps 30` matters only if the camera shot faster than that, and phones
shoot 60 by default. Above thirty a second the run is doing double the
inference for figures that measure the same to within a percent — distance,
speed, sprints and bursts are all flat from 60 down to 6. **Do not go below 30
to save time on the first clip.** The movement figures would survive it; the
tracker would not, and identity is already the weakest link (a stride of 2 cost
a third of the tracks and half the longest one on the spike footage). See
`tests/test_sampling.py` for the measurement and `analyse_match` for both
halves of the trade.

Both timing numbers ride in the downloaded log, so there is nothing to retype.
`--video-offset` and `--second-half-video` exist to override the file, which is
what you want after re-cutting the video and before saving the new numbers on
the coach page.

Read the output next to the video. Specifically:

- Do the **participants** ruled out look like coaches and substitutes, with
  reasons that make sense?
- Is **live share** below 1.0 and are the stoppages roughly where they happened?
- Does it say the **right half**? The kickoff taps in the log answer this, so
  there is no `--period` flag in the command above and there should not need to
  be. If the report says the wrong one, or says it decided by `default`, stop:
  the period flips which goal each side is attacking, so every shot map,
  heatmap, pressing figure and passing diagram is mirrored — and all of them
  will look completely normal.
- Do the **goals reconcile** — same count as the tag log, at the same times?
- Are the **shape** figures plausible for how the team actually set up?

**Fourth — save the baseline.**

Once the output is something you would defend, commit that JSON to `baselines/`
with the command that produced it. From then on:

```bash
python -m cv.experiments.compare_reports baselines/2026-08-15-first-half.json runs/today.json
```

catches any change to a threshold that quietly moves the numbers. Every threshold
in `cv/touches.py`, `cv/participants.py` and `cv/phases.py` is a guess, the unit
tests pin what those guesses are rather than what they do, and a baseline is the
only thing that notices when one of them starts doing something different. See
`baselines/README.md`.

## 6. What to expect to be disappointing

Worth saying in advance so it reads as a known limit rather than a failure:

- **Per-player numbers will be fragments** until tracking improves. Team-level
  figures — possession, shape, territory — need no identity and are the ones to
  lead with.
- **Events may find nothing.** On the current clip `segment_touches` returns zero
  touches, because the nearest player to a detected "ball" sits a median 6.3
  player heights away — so most of those detections are not the ball. That is the
  correct outcome given the input, not a bug, and better footage is the fix.
- **Headers are scored as foot shots**, always, so xG for headed chances reads
  high. One camera cannot see the ball's height.
- **Per-shot xG is loose, and the calibration decides whether you see it at
  all.** Measured: half a metre of position error moves a single shot's xG by
  ~0.030 on a 0.188 baseline, with a tail at half the number. Past 0.5 m of
  calibration error the per-shot figures stop being shown and only the team
  total is — which is another reason to get the reprojection error under 0.5 m
  before you leave the field. Read the total, not the gap between two shots.
- **A referee may be counted as a player.** Without a calibration a referee and a
  goalkeeper look identical on every feature available, so both are kept — losing
  a keeper is the worse error. The count of flagged figures is shown next to the
  stats.
