#!/bin/bash
# strict mining for every fold: picks of the other games under fold_<game>'s scores, then the labels
cd C:/Users/alexv/Desktop/Repos/PitchIQ-bp-selftrain
PY=C:/Users/alexv/Desktop/Repos/PitchIQ/.venv/Scripts/python.exe
D=pitchiq/experiments/ballpush/selftrain
export PYTHONPATH=. RP_PROCS=6
for g in "$@"; do
  $PY $D/mine.py fold $g || exit 1
  $PY $D/pseudo.py no_$g > C:/Users/alexv/Desktop/Repos/PitchIQ-data/ball-push/selftrain/logs/pseudo_no_$g.log 2>&1 &
  $PY $D/pseudo.py own_$g > C:/Users/alexv/Desktop/Repos/PitchIQ-data/ball-push/selftrain/logs/pseudo_own_$g.log 2>&1 &
done
wait
echo MINE ALL DONE
