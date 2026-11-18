#!/bin/bash
# held_build.sh: caches, mined tracks and own-game pseudo-labels of the held-out halves (CPU; reads no answers)
cd C:/Users/alexv/Desktop/Repos/PitchIQ-bp-selftrain
PY=C:/Users/alexv/Desktop/Repos/PitchIQ/.venv/Scripts/python.exe
D=pitchiq/experiments/ballpush/selftrain
L=C:/Users/alexv/Desktop/Repos/PitchIQ-data/ball-push/selftrain/held/logs
mkdir -p $L
export PYTHONPATH=. ST_HELD=1 ST_PROCS=8 RP_PROCS=6
$PY $D/cache.py > $L/cache.log 2>&1 || { echo CACHE FAILED; exit 1; }
$PY $D/tcache.py > $L/tcache.log 2>&1 &
$PY $D/mine.py oof > $L/mine.log 2>&1 || { echo MINE FAILED; exit 1; }
for g in nb pi wh co wb; do $PY $D/pseudo.py own_$g > $L/pseudo_own_$g.log 2>&1 || { echo PSEUDO $g FAILED; exit 1; }; done
wait
echo HELD BUILD DONE
