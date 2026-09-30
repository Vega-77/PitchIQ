#!/bin/sh
# Ball tracker over one window of the match video, end to end.
#   sh run.sh <lo> <hi> <tag> [workers]      abs frame numbers, 30 fps
#   e.g. sh run.sh 86475 95475 vid1          -> vid1.mp4 (the 5-min review video)
# Video path: env PIQ_VIDEO (default in feat4.py).  8 workers, not 16.
PY="${PY:-C:/Users/alexv/Desktop/Repos/PitchIQ/PitchIQHelper/.venv/Scripts/python.exe}"
cd "$(dirname "$0")" || exit 1
LO=$1; HI=$2; TAG=$3; W=${4:-8}
[ -n "$TAG" ] || { echo "usage: sh run.sh <lo> <hi> <tag> [workers]"; exit 2; }
echo "=== detector $(date +%T)"; "$PY" -W ignore drun.py "$LO" "$HI" "$W" "$TAG" || exit 1
echo "=== people $(date +%T)";   "$PY" -W ignore ppl.py "$TAG" || exit 1
echo "=== classifier $(date +%T)"; "$PY" -W ignore clsscore.py cls_B.pt "$TAG" || exit 1
echo "=== render $(date +%T)";   "$PY" -W ignore render_vid.py "$TAG" "$TAG.mp4" || exit 1
echo "DONE $(date +%T)"
