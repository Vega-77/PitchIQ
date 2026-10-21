# Changelog

The notable changes, newest first, by the date they landed. The commit log
has the rest, and `docs/history/ROADMAP-archive.md` has the reasoning.

## 2026-10-21: deep clean

No behaviour change: the e2e report JSON and the ball tracker's outputs are
byte-identical before and after.
- `pyproject.toml` (`pip install -e .[test]`), ruff format and lint in CI,
  `.editorconfig`, and the conventions written down in `docs/conventions.md`.
- Renames: `cv/` is now `pitchiq/`, `PitchIQHelper/` is `xg_training/`,
  `assets/` is `shared/`; modules got descriptive names (`publish` became
  `publish_to_firestore`, `report_json` became `report_export`, and so on).
- The match-day commands moved to `pitchiq/cli/`, behind `python -m pitchiq <command>`.
- Design notes moved out of docstrings into `docs/design/`. `analyse_match`
  takes `AnalysisOptions` and `MatchContext`, and the touch and event
  thresholds each became one object. Members only tests read were deleted or gated.
- `shared/report.js` is split into seventeen parts behind a barrel,
  `coach.js` into view modules and `app.css` into eleven sheets.
- `pitchiq/balltrack` is a package: one tracker, one detector (the slow
  reference versions are in `reference/`), one flight chain, settings in
  `config.py`, scripts and folders renamed (`homog/` became
  `pitch_registration/`), and 37 dead scripts deleted.
- Docs: architecture, glossary, report schema, a README in every folder, and
  this file.

## 2026-10-20

- The root README says what PitchIQ is and describes both camera pipelines.
  ROADMAP.md is short, and the history moved to `docs/history/`.
- The calibrate page is back in the site, as a web version of Pitch Pins.
- No personal paths in tracked files; `requirements-balltrack.txt`; the
  detector weights are pinned by hash.
- Each lab's labels are packed into one `.jsonl` file; 37 unused tracker
  scripts were archived.

## 2026-10-12 to 2026-10-16: panning-camera passes and labs

- The Ball Clicker lab (900 frames over 12 halves) and its scorer.
- `ballgate.py`: no ball is drawn where the classifier is lost for minutes.
- Registrations from the user's pins for the dev and held-out halves; a
  game's two halves are solved as one camera (NB, Wardlaw-Hartridge).
- Team split by frames, with a fallback to the other axis (fixed the NB greys).
- autocal anchors each sample to its own paint.
- The Spot Check Lab (step 5): yes/no questions about what the pipeline drew.
- A ball ring that would show for under 5 frames is not drawn.
- Live tagging dropped the out-of-bounds key and gave each key a colour.
  Minutes charts measure against an 80-minute match.
- Flights use each camera's own height for gravity.

## 2026-10-07: top-down map and click-free calibration

- Players on a top-down map; `pitchiq/autocal` calibrates a panning camera
  from painted lines and player sizes, with no clicks.
- `half.py` runs a whole half in resumable 5-minute pieces; a halves finder
  places kickoff and half-time from the video alone.
- Automatic checks (step 4): feet on the pitch, speeds, crowding.
- Team colours on every page; TAPNext bridges at 384 px by default.

## 2026-10-03: Hybrid 90 and the panning camera

- Hybrid 90 accepted: the tracker owns every confident pick, kick and flight,
  and TAPNext++ only crosses gaps of 90 frames or fewer.
- The panning (Hudl) camera is held still for the website pipeline.

## 2026-09-29: the ball tracker

- The ball tracker for panning footage moved into the repo; its 5-minute
  review video (48:03-53:02) was accepted.
- 2026-09-17: eight passes marked by hand gave the project its first recall figure.

## 2026-08-26 to 2026-09-03: seams and audits

- Tests that hold two halves of a contract together: report fields against
  the pages that draw them, browser writes against `firestore.rules`, ids,
  data attributes, CSS and custom properties, event types and limits held twice.
- Accessibility gates: contrast, control names, focus, headings, landmarks,
  skip link.
- The commands in the field guide are tested.

## 2026-08-10 to 2026-08-20: calibration and coverage

- The camera-bump detector: a camera that moved is told apart from players
  who moved, instead of trusting a stale homography.
- How much of the pitch the camera saw; lens distortion read off the paint;
  the pitch fitted as painted, not as the Laws draw it.

## 2026-08-06: the xG model replaced

- The xG model was fixed: `shot_height` was dropped because it read the answer
  off the outcome. The sandbox uses real shots, and shots can be marked as headers.

## 2026-07-27 to 2026-08-05: the computer-vision pipeline and the app

- Detection, tracking, calibration, ball, teams and possession, events, the
  xG bridge, and the pipeline end to end, streaming frames.
- Firebase for auth and data; coach, player, half-time and live-tagging pages.
- The FastAPI backend that Firestore replaced was deleted.

## 2026-03-21: first version

- The xG visualiser and its ONNX model, deployed as a static site.
