# tools/

Repo maintenance scripts, not part of the pipeline.

- `audit_metrics.py`: code-clarity numbers for `pitchiq/*.py` and
  `pitchiq/balltrack/` (non-code share, missing docstrings, long functions,
  short import aliases, duplicate bodies, personal paths) for tracking a
  clean-up. Run it from the repo root with
  `python tools/audit_metrics.py`. It needs only git and Python.
- `viewer_preview.py` and `viewer-preview.html`: the coach's match viewer
  (`shared/match-viewer.js`) on this PC, from a `python -m pitchiq viewer_export`
  folder and the game's video file, before anything is on the site. Run
  `python tools/viewer_preview.py <export dir> <video>` and open
  http://localhost:8120/tools/viewer-preview.html (`?t=<seconds>` starts there).
