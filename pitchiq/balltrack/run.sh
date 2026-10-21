#!/bin/sh
# Ball tracker over one window of the match video, end to end.
#   sh run.sh <lo> <hi> <tag> [workers]      abs frame numbers, 30 fps
#   e.g. sh run.sh 86475 95475 vid1          -> vid1.mp4 (the 5-min review video)
# Video path and worker count: PIQ_VIDEO, PIQ_WORKERS (defaults in config.py).
# RVID=0 skips the review video (long windows).  detect_candidates and person_boxes resume by 5-min chunk.
BT="$(cd "$(dirname "$0")" && pwd)"; . "$BT/pyenv.sh"
cd "$BT" || exit 1
LO=$1; HI=$2; TAG=$3; W=${4:-${PIQ_WORKERS:-8}}
[ -n "$TAG" ] || { echo "usage: sh run.sh <lo> <hi> <tag> [workers]"; exit 2; }
echo "=== detector $(date +%T)"; "$PY" -W ignore -m $M.detect_candidates "$LO" "$HI" "$W" "$TAG" || exit 1
echo "=== people $(date +%T)";   "$PY" -W ignore -m $M.person_boxes "$TAG" || exit 1
echo "=== classifier $(date +%T)"; "$PY" -W ignore -m $M.score_candidates cls_B.pt "$TAG" || exit 1
[ "${RVID:-1}" = 0 ] || { echo "=== render $(date +%T)"; "$PY" -W ignore -m $M.render_vid "$TAG" "$TAG.mp4" || exit 1; }
echo "DONE $(date +%T)"
