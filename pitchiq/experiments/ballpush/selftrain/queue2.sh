#!/bin/bash
# round 2 (after queue1): mine the other games with s1's fold_G (never saw G), retrain
cd C:/Users/alexv/Desktop/Repos/PitchIQ-bp-selftrain
PY=C:/Users/alexv/Desktop/Repos/PitchIQ/.venv/Scripts/python.exe
D=pitchiq/experiments/ballpush/selftrain
L=C:/Users/alexv/Desktop/Repos/PitchIQ-data/ball-push/selftrain/logs
export PYTHONPATH=. RP_PROCS=6 ST_FOLDP=foldp_s1 ST_PRE=r2no_
until [ -f $L/queue1.done ]; do sleep 20; done
$PY $D/foldscore.py s1 > $L/foldscore_s1.log 2>&1
for g in co eb nb pi scrim wb wh ww; do
  $PY $D/mine.py fold $g >> $L/mine_r2.log 2>&1
  $PY $D/pseudo.py r2no_$g > $L/pseudo_r2no_$g.log 2>&1 &
done
wait
bash $D/go.sh s2 global --pseudo r2 --w 0.5 --epochs 12 > $L/go_s2.log 2>&1
bash $D/go.sh s2 game > $L/go_s2_game.log 2>&1
echo done > $L/queue2.done
