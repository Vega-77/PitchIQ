#!/bin/bash
# One half through the ball-push recipe, end to end (after the self-trained classifier's per-frame scores exist):
#   tracker replay (skip:2+enter:5+kin:4) -> TAPNext bridges on every gap up to 180 frames (GPU, 30-55 min)
#   -> export picks -> shipped 3D chain on them (CPU, ~30-60 min) -> score with the real ring rule + lost gate.
#
#   bash pitchiq/experiments/ballpush/run_half.sh <tag> [run=ko1_s10] [cls=<selftrain run dir>]
#
# Writes only under PitchIQ-data/ball-push (trk2/<run>, tn/, port/).  Reads the main checkout read-only.
# Steps whose output exists are skipped, so it can be re-run after a crash.  Launch detached for long halves
# (Start-Process bash.exe ... -WindowStyle Hidden): a session stop kills run_in_background jobs.
set -e
TAG=${1:?tag}
RUN=${2:-ko1_s10}
DATA=/c/Users/alexv/Desktop/Repos/PitchIQ-data/ball-push
CLS=${3:-$DATA/selftrain/runs/${RUN}_global}
MAIN=/c/Users/alexv/Desktop/Repos/PitchIQ
PY=$MAIN/.venv/Scripts/python.exe
WT=$(cd "$(dirname "$0")/../../.." && pwd)
NM="skip:2+enter:5+kin:4"
cd "$WT"
log() { echo "$(date -u +%FT%TZ) $TAG $*"; }

log "1 tracker replay"
if [ ! -f $DATA/trk2/$RUN/replay_cache/$(basename "$CLS")_skip_2+enter_5+kin_4_$TAG.npz ]; then
  RP_TAGS=$TAG RP_OUT=$DATA/trk2/$RUN RP_PROCS=4 PYTHONPATH=. $PY pitchiq/experiments/ballpush/replay.py "$CLS" kin:4 "$NM"
fi

log "2 bridges (GPU)"
ALLGAPS=1 TN_TAGS=$TAG MAXGAP=180 PYTHONPATH=. $PY pitchiq/experiments/ballpush/tnclick.py run $RUN "$NM"

log "3 export picks + click score"
ALLGAPS=1 TN_TAGS=$TAG MAXGAP=180 EXPORT=$DATA/port/picks_all_${RUN} PYTHONPATH=. \
  $PY pitchiq/experiments/ballpush/tnclick.py score $RUN "$NM" | tail -3

log "4 3D chain"
cd $DATA/port
PYTHONPATH=$MAIN $PY mk3d_bp.py bp_$RUN $DATA/port/picks_all_${RUN} $TAG

log "5 score (real ring rule; lost gate on this classifier's scores)"
GATE_RUN=$RUN PYTHONPATH=$MAIN $PY mk3d_bp.py score bp_$RUN $TAG
log done
