# PitchIQ

PitchIQ turns video of a high-school football (soccer) match into stats a
coach can use: possession, territory, shape, a top-down map of every player,
shots and xG. Players get reports of their own as well. A tablet on the
touchline tags what the camera can't settle by itself (kick-off,
substitutions, restarts). The computer-vision pipeline in `cv/` turns the
match video and that tag log into a report JSON, and the site (GitHub Pages
and Firestore) shows the report to the coach and the players.

No license is granted. All rights reserved.

## Two camera pipelines

**Fixed camera** (`cv/calibration.py`). One homography per camera position
maps pixels to metres. It is fitted once from landmarks clicked on a single
frame. This is the pipeline `FOOTAGE_DAY.md` was first written for. A tripod,
a locked-off shot, and that is all it needs.

**Panning camera** (`cv/panned.py` + `cv/balltrack/`). The footage the team
actually has is from a Hudl camera that follows the play. Every frame gets its
own frame-to-pitch homography:
- `cv/autocal.py` calibrates the camera from the match itself, using the
  paint plus SIFT between frames;
- `cv/balltrack/homog/vreg.py` registers frames to a venue map;
- landmark pins clicked on the camera's panorama on the calibrate page can
  place the pitch instead (the pin solve that reads them, `pin_cal.py`, is not
  in the repo yet).

The ball comes from the Hybrid 90 tracker in `cv/balltrack/`, and
`PannedCamera` moves everything into one still reference view. After that,
`cv/pipeline.py` runs unchanged:

```python
from cv.panned import PannedCamera
from cv.pipeline import analyse_match

camera = PannedCamera.from_tracker('fr00')   # needs hybrid.sh outputs, see below
report = analyse_match(video, panned=camera, start_s=..., end_s=...)
```

`from_tracker` reads files that `sh cv/balltrack/hybrid.sh <lo> <hi> <tag>`
(one window) or `cv/balltrack/half.py` (a whole half) writes. They are
generated outputs, not in git (see `cv/balltrack/README.md`, "Generated, not
in git"). Accuracy figures for the tracker are in that README too, and
`FOOTAGE_DAY.md` ("Fixed or panning") sets out which pipeline a camera needs.

## Repo map

| path | what |
|---|---|
| `cv/` | the pipeline: detection, tracking, identity, calibration, ball, possession, events, top-down map, report JSON, publishing to Firestore |
| `cv/experiments/` | command-line tools: `event_report`, `match_report`, `spike_detect`, `track_report`, `calibrate`, `autocal_run`, `publish_report`, labs |
| `cv/balltrack/` | the ball tracker for panning footage (Hybrid 90: detector + classifier + TAPNext++ bridges + 3D flight chain), and the venue map and registration in `homog/` |
| `assets/` | shared frontend modules and CSS (no build step, plain ES modules) |
| `index.html` | landing page and sign-in |
| `coach/` | coach dashboard: season, roster, matches, staff, player reports |
| `player/` | a player's own reports |
| `halftime/` | the three-minute read for the touchline |
| `live-tagging/` | match-day tablet tool |
| `calibrate/` | landmark pins on a frame or a panorama, exported for both calibrators |
| `PitchIQHelper/` | xG model training (`main.py`, StatsBomb open data -> ONNX) |
| `baselines/` | committed report JSONs that `compare_reports` checks changes against |
| `tests/` | Python (pytest) and JavaScript (`node --test`, Firestore emulator) suites |
| `docs/` | conventions, history and generated figures |

## Setup

Python 3.12, in a virtual environment:

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows; source .venv/bin/activate elsewhere
pip install -r requirements.txt
```

`requirements.txt` pins `torch`. For a GPU, install the CUDA build of that
same version from pytorch.org first. The ball tracker needs a CUDA GPU (it
was built on an RTX 4060). The fixed-camera pipeline runs on a CPU, just
slowly.

The tracker has extra dependencies in `requirements-balltrack.txt`: a tapnet
clone and a 2.4 GB TAPNext++ checkpoint, described there and in
`cv/balltrack/README.md`.

**Detector weights.** `cv/detector.py` loads `cv/weights/yolov8n.pt`, which
is not in git (`*.pt` is ignored). If the file is missing, Ultralytics
downloads its `yolov8n.pt` (release v8.4.0) to that path on first use, so a
clone with network access needs nothing more. To fetch it ahead of time, for
a machine that will be offline at the field:

```bash
mkdir -p cv/weights
curl -L -o cv/weights/yolov8n.pt https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt
```

Either way it is the same file every measurement here used: sha256
`f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36`
(`tests/test_detector_weights.py` checks it).

Scripts take their paths from the environment, with defaults that work from
a clone:
- `PY` is the Python interpreter for the `.sh` scripts (default `python`).
- `PIQ_REPO` is the repo root (default: computed from the script's location).
- `PIQ_VIDEO` is the match video. Set it for every tag.
- `PIQ_DATA` is the folder for large outputs (default: `PitchIQ-data` beside
  the repo).

## Tests

```bash
pip install -r requirements-test.txt
python -m pytest tests/ -q
python -m pyflakes $(git ls-files 'cv/*.py' 'tests/*.py')
python -m ruff format --check cv tests tools PitchIQHelper
python -m ruff check cv tests tools PitchIQHelper
npm test                 # JS suites + Firestore rules (needs Node and a JVM)
```

Before committing a change to any frontend file, run
`python stamp_version.py <n+1>`, where `n` is the `?v=` that `index.html`
carries now (see `assets/README.md`).

## Docs

- [FOOTAGE_DAY.md](FOOTAGE_DAY.md): filming a match, calibrating, and running a
  report, step by step.
- [FIREBASE_SETUP.md](FIREBASE_SETUP.md): the Firebase project, rules and
  service account.
- [cv/balltrack/README.md](cv/balltrack/README.md): the ball tracker, how
  good it is, and what it needs.
- [ROADMAP.md](ROADMAP.md): where things stand and what is next. The full
  history is in [docs/history/ROADMAP-archive.md](docs/history/ROADMAP-archive.md).
- [docs/conventions.md](docs/conventions.md): how the Python is written
  (formatting, names, docstrings, types).
- [assets/README.md](assets/README.md): frontend conventions and cache busting.
- [baselines/README.md](baselines/README.md): regression baselines.
