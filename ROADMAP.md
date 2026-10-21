# PitchIQ Roadmap

Goal: point a camera at a high school match and get player and team stats out
the other end. The primary use is the coach at half-time, seeing what they
cannot see from the touchline (fatigue, shape, cumulative xG, dangerous
turnover zones). The secondary use is after the game: each player signs in to
their own report, and the coach gets a fuller debrief.

People stay in the loop where the camera is weak. A sideline tablet records
substitutions and events live (`live-tagging/`). After the game a coach
confirms which tracks are which player, and corrects what the pipeline got
wrong.

This file is forward-looking: where things stand and what is left. Everything
before 2026-10-20 (the phase-by-phase build, every measurement and the reasons
behind each decision) is in
[docs/history/ROADMAP-archive.md](docs/history/ROADMAP-archive.md). Phase
numbers cited in the code refer to that file.

---

## Status (2026-10-20): the panning camera works

The archive was written before any match footage existed, when a camera that
holds still looked necessary and a panning one was called unusable. That has
changed: the team's Hudl footage, which follows the play, now runs end to end.

- **Calibration per frame.** `pitchiq/autocal.py` calibrates the camera from the
  match itself (paint and SIFT, no clicks; `python -m pitchiq.experiments.autocal_run`),
  and `pitchiq/balltrack/homog/vreg.py` registers every frame to it. One camera
  position per game; each half solves its own aim.
- **The ball.** The Hybrid 90 tracker in `pitchiq/balltrack/` (accepted 2026-10-03):
  a detector + classifier tracker that owns every confident pick, with
  TAPNext++ bridging gaps of 90 frames or fewer, then a 3D chain that tells
  ground from air. `half.py` runs a whole half; `ballgate.py` blanks the
  stretches where the classifier is lost.
- **Into the pipeline.** `pitchiq/panned.py` (`PannedCamera.from_tracker(tag)`)
  moves every player box into one still reference view and hands
  `analyse_match` the tracker's ball, so the fixed-camera pipeline runs
  unchanged. `event_report` and `match_report` take `--panned <tag>`.
- **Pins.** `calibrate/` is back in the site (2026-10-20): landmark pins for a
  fixed frame (fitted and checked live) or a panorama (for the pin solve).
- **The site.** `index.html`, `coach/`, `player/`, `live-tagging/` and
  `halftime/` run on Firestore with Google sign-in; `firestore.rules` is the
  whole security boundary. There is no server of our own.

How good the ball is, from `pitchiq/balltrack/README.md`:

| | Ball Truth | wrong picks | Find the Ball | passes |
|---|---|---|---|---|
| Hybrid 90 + default 3D | 78 % | 6 | 36/47 | 14/16 |
| cls_B tracker alone | 72 % | 4 | 34/47 | 14/16 |

On the 18 held-out truth clips (269 hand marks, a pick right within 60 px):
74 % on the first half's clips and 70 % on the second's, with 4 and 0 wrong
picks. Almost every miss is an abstention, which is the intended failure: a
wrong ball is worse than no ball.

What is checked and what is not, in more detail: the status block at the top
of `pitchiq/pipeline.py`. How to run it: `FOOTAGE_DAY.md`, "Fixed or panning", and
the root `README.md`. For one window of a panned game:

```bash
python -m pitchiq.experiments.autocal_run nb1 7200 9000
python -m pitchiq.experiments.event_report --video clips/first-half.mp4 --panned nb1
```

---

## Open work

A checkbox is work: something still to build or decide. Findings and
measurements are written as prose. The tags are the archive's scope tiers:
**[Demo]** for the first test with the team, **[MVP]** for the product after
it, **[Stretch]** for later.

### Computer vision, in rough order

- [ ] **The top-down map.** Players and ball on one pitch drawing, the next
      priority. Team drift guesses about 3 m ten seconds out, against 9-19 m
      for a frozen position; judge changes on held-out halves.
- [ ] **Night games.** Under floodlights the ball is lost at every stage: the
      detector, the ranker and the classifier. The gate blanks those stretches,
      so a night half has almost no ball. Daylight with heavy sun glare
      (Piscataway) held up; that is the rest of the archive's lighting and
      weather check.
- [ ] **Bring the pin solve into the repo.** `pin_cal.py` and the panorama
      builder (`lc/`, with `lc/pano_rgb.py`) still live in the data folder
      beside the repo, not in `pitchiq/`.
- [ ] **Possession, on a second annotated window.** The one hand-counted window
      agreed, but on 12 s of live ball, and half its frames change answer if the
      ball moves 20 px. Report `live_s` beside any split until then.
- [ ] **Shots on real footage.** xG is computed in `pitchiq/xg_bridge.py` and the
      model is measured; whether a shot is spotted, and whether the shooter,
      keeper and defenders handed to it are the right people, has not been
      checked against a match anyone watched.
- [ ] **Decide the tracking trade-off** (BoT-SORT or ByteTrack) on footage that
      can support the judgement. It was deferred until a fixed camera; the
      panned path now holds every box in one still view, so the player labs on
      dev halves can decide it.
- [ ] **[MVP] Fine-tune the person detector on our own footage.** The ball side
      is done (the crop classifier and ranker are trained on our labels, and
      the ball gates replace a single confidence threshold). People are still
      found by the stock YOLOv8n weights.
- [ ] **Re-identification** after a player is hidden or leaves the frame.
      Partly done: `pitchiq/identity.py` rejoins fragments seconds apart, and the
      coach's picker suggests the rest. Anyone gone for longer stays split.
- [ ] **[MVP] Speed, for half-time.** A half takes about 2.2 hours of GPU on the
      RTX 4060 (`pitchiq/balltrack/README.md`, speed), so nothing yet reaches the
      break. Processing the first half incrementally during play needs a much
      faster tracker first; `pitchiq.experiments.speed_report` measures the rest.
- [ ] **[Stretch]** Multiple cameras, a drone, or pan-tilt-zoom coverage.
- [ ] **[Stretch]** Jersey-number OCR, only as a suggestion for the mapping.
- [ ] **[Stretch]** Offside detection. It stays human-marked.
- [ ] **[Stretch]** A live overlay on the video (boxes, ids, a mini-map).

### Match day and people

- [ ] **[Demo] The whole path under a real clock.** Footage off the camera,
      pipeline, publish, the coach opening `halftime/`, each step done by a
      real person with a stopwatch running. The pipeline half has run on the
      team's footage; the match-day half has not.
- [ ] Decide role split: one app covering both subs and events, or two simpler
      single-purpose roles or devices. Worth testing both at the dry run.
      (`taggerUids` in the rules is ready for either.)
- [ ] Identify and brief whoever runs the tablet at the demo game. The briefing
      is written (`FOOTAGE_DAY.md` §3); the person is not chosen.
- [ ] Weatherproofing for an outdoor tablet: a case, and a screen usable in sun
      glare.
- [ ] Parental or guardian awareness before real students' data goes in. The
      technical half (removing a player removes their data) is done; the
      conversation is not a code change.

### Site

- [ ] **[Stretch]** App Check and a browser-key referrer restriction before the
      site is public.

---

## Closed in the 2026-10-20 reconciliation

The archive ended with 24 open boxes. Those not carried into the list above
were closed or merged:

| archive item | outcome |
|---|---|
| [Demo] a single fixed camera, a hard requirement (Phase 1) | superseded: panning footage runs end to end |
| [Stretch] support moving, auto-tracking cameras (Phase 1) | done: `pitchiq/autocal.py`, `vreg.py`, `pitchiq/panned.py` |
| [Demo] a native-resolution export, not a screen recording (Phase 1) | met: every game since is a downloaded export |
| [Demo] record to a file, process afterwards (Phase 1) | done: how every run works |
| [MVP] process the first half incrementally (Phase 1) | merged into "Speed, for half-time" |
| tune the detector's confidence on real footage (Phase 5) | ball: done by the gates; people: merged into the detector item |
| [MVP] fine-tune detectors (Phases 5 and 6, twice) | merged into one detector item |
| re-measure tracking on native-resolution footage (Phase 6) | superseded by the player labs on the team's footage |
| [Demo] ground truth on a labelled clip before a full game (Phase 16) | done another way: `truthdb/` (269 marks), the Spot Check Lab and the Ball Clicker |
| lighting and weather robustness (Phase 16) | merged into "Night games" (sun glare measured) |
| [Demo] a dry run on the team's real footage (Phase 16) | pipeline half done; the rest is "The whole path under a real clock" |
| [MVP] the half-time path under a real clock (Phase 16) | merged into the same item |

---

## Repo layout

```
PitchIQ/
├── index.html           landing page and sign-in
├── assets/              shared frontend modules (app.css, auth.js, db.js, report.js, ...)
├── coach/               the coach's dashboard: roster, matches, stats, publish, track mapping
├── player/              the player's portal: their own reports only
├── live-tagging/        the match-day tablet tool
├── halftime/            the three-minute touchline read
├── calibrate/           landmark pins: a fixed frame, or a panorama for the pin solve
├── firestore.rules      the security boundary (with firestore.indexes.json)
├── tests/               Python tests, JS tests and the Firestore emulator suites
├── pitchiq/                  the video pipeline: detection, tracking, calibration, events, xG
│   ├── experiments/        the command-line entry points (python -m pitchiq.experiments.<name>)
│   └── balltrack/          the ball tracker (Hybrid 90) and its labs
├── baselines/           report JSONs a person checked against the match
├── xg_training/         xG model training (main.py)
├── docs/history/        the ROADMAP archive
├── FOOTAGE_DAY.md       the field guide: filming, calibrating, running a match
├── requirements*.txt    Python dependencies (base, tests, ball tracker)
└── package.json         dev tooling only; the frontend has no build step
```

One venv at the repo root, `.venv`, covers all the Python. The frontend loads the
Firebase SDK as CDN ES modules, so `npm` is only for the emulator and the tests.
