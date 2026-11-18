#!/bin/bash
# go.sh <run> <cal: global|game> [train.py fit args ...]: all eight folds, calibrate, replay, per-half table
cd C:/Users/alexv/Desktop/Repos/PitchIQ-bp-selftrain
PY=C:/Users/alexv/Desktop/Repos/PitchIQ/.venv/Scripts/python.exe
D=pitchiq/experiments/ballpush/selftrain
O=C:/Users/alexv/Desktop/Repos/PitchIQ-data/ball-push/selftrain
export PYTHONPATH=. RP_PROCS=4
run=$1; cal=$2; shift 2
if [ ! -f $O/runs/$run/fold_ww.pt ]; then
  $PY $D/${TRAIN:-train.py} fit $run "$@" 2>&1 | grep -E "^fold .*:|DONE|Error|Traceback" || exit 1
fi
$PY $D/train.py finish $run --cal $cal 2>&1 | grep -vE "Warning|tot \+=|Consider|Triggered"
rm -rf $O/runs/${run}_$cal; cp -r $O/runs/$run/cls $O/runs/${run}_$cal
$PY pitchiq/experiments/ballpush/replay.py $O/runs/${run}_$cal base | tail -n 1
$PY $D/cmp.py oof_cls ${run}_$cal
