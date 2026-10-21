#!/bin/sh
# Ball tracker over one window of the match video, end to end.
#   sh run.sh <lo> <hi> <tag> [workers]      abs frame numbers, 30 fps
#   e.g. sh run.sh 86475 95475 vid1          -> vid1.mp4 (the 5-min review video)
# Video path: env PIQ_VIDEO (default in feat4.py).  8 workers, not 16.
# RVID=0 skips the review video (long windows).  drun and ppl resume by 5-min chunk.
R="$(cd "$(dirname "$0")/../.." && pwd)"
V="$R/.venv/Scripts/python.exe"; [ -x "$V" ] || V="$R/.venv/bin/python"; [ -x "$V" ] || V=python
PY="${PY:-$V}"
cd "$(dirname "$0")" || exit 1
LO=$1; HI=$2; TAG=$3; W=${4:-8}
[ -n "$TAG" ] || { echo "usage: sh run.sh <lo> <hi> <tag> [workers]"; exit 2; }
echo "=== detector $(date +%T)"; "$PY" -W ignore drun.py "$LO" "$HI" "$W" "$TAG" || exit 1
echo "=== people $(date +%T)";   "$PY" -W ignore ppl.py "$TAG" || exit 1
echo "=== classifier $(date +%T)"; "$PY" -W ignore clsscore.py cls_B.pt "$TAG" || exit 1
[ "${RVID:-1}" = 0 ] || { echo "=== render $(date +%T)"; "$PY" -W ignore render_vid.py "$TAG" "$TAG.mp4" || exit 1; }
echo "DONE $(date +%T)"
