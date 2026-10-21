# tools/

Repo maintenance scripts, not part of the pipeline.

- `audit_metrics.py`: code-clarity numbers for `pitchiq/*.py` and
  `pitchiq/balltrack/` (non-code share, missing docstrings, long functions,
  short import aliases, duplicate bodies, personal paths) for tracking a
  clean-up. Run it from the repo root with
  `python tools/audit_metrics.py`. It needs only git and Python.
