# pitchiq/

The Python package: match video in, report JSON out, then published to
Firestore for the pages. One module per stage (detector, tracking, teams,
identity, calibration, ball, possession, touches, events, topdown, report
export, publishing); `docs/architecture.md` walks through them in order and
`docs/design/<module>.md` keeps each one's reasoning.

Run it as `python -m pitchiq <command>` after `pip install -e .` from the repo
root (the commands are in `cli/`). It needs the packages in
`requirements.txt` and `weights/yolov8n.pt`, which Ultralytics downloads on
first use. `cli/` holds the match-day commands, `experiments/` the research
tools and labs, and `balltrack/` the ball tracker for panning footage.
