#!/bin/sh
PY="C:/Users/alexv/Desktop/Repos/PitchIQ/PitchIQHelper/.venv/Scripts/python.exe"
cd "$(dirname "$0")" || exit 1
TAGS="wm00 wm01 wm02 wm03 wm04 wm05 wm06 wm07 wm08 wm09 wm10 wm11 wm12 wm13 wm14 wm15 wm16 wm17 pm vid1 vid2 vid3"
for m in A:../cls_A.pt orig:../cls.pt h160:../cls_h160.pt Aprev:../cls_A2/cls_A_prev.pt Ar3:../cls_A2/cls_A_r3.pt Ar4:../cls_A2/cls_A_r4.pt Bprev:../cls_A2/cls_B_prev.pt Br3:../cls_A2/cls_B_r3.pt Br4:../cls_A2/cls_B_r4.pt; do
  n=${m%%:*}; p=${m#*:}
  mkdir -p cls/$n
  echo "=== $n  $(date +%T)"
  "$PY" -W ignore clsrun.py $p cls/$n $TAGS || echo "FAILED $n"
done
echo "=== CLS DONE $(date +%T)"
