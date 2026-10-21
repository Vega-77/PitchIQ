#!/bin/bash
# GPU jobs, one at a time (they page if run together)
BT="$(cd "$(dirname "$0")/.." && pwd)"; . "$BT/pyenv.sh"
cd "$BT/tapnext"
T0=$(date +%s); st(){ echo "=== $1  $(( $(date +%s) - T0 )) s  $(date +%T)"; }
st bridges
"$PY" -u -m $M.tapnext.tn_engine bridges pm vid3 vid1 vid2 wm00 wm01 wm02 wm03 wm04 wm05 wm06 wm07 wm08 wm09 wm10 wm11 wm12 wm13 wm14 wm15 wm16 wm17 2>&1 | grep --line-buffered -v Warning
st cls_all
bash ../alltests/cls_all.sh 2>&1 | grep --line-buffered -v Warning
st yolo
cd ../alltests && "$PY" -u -m $M.alltests.yolo_run pm vid1 vid2 vid3 wm00 wm01 wm02 wm03 wm04 wm05 wm06 wm07 wm08 wm09 wm10 wm11 wm12 wm13 wm14 wm15 wm16 wm17 2>&1 | grep --line-buffered -v Warning
st alone
cd ../tapnext
for t in pm vid1 vid2 vid3 wm00 wm01 wm02 wm03 wm04 wm05 wm06 wm07 wm08 wm09 wm10 wm11 wm12 wm13 wm14 wm15 wm16 wm17; do
  [ -f ../alltests/tn/alone_$t.npz ] || "$PY" -u -m $M.tapnext.tn_engine alone $t 2>&1 | grep --line-buffered -v Warning
done
st "GPU ALL DONE"
