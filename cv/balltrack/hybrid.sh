#!/bin/sh
# Hybrid 90 + default flight chain over one window, end to end (accepted 2026-10-03).
#   sh hybrid.sh <lo> <hi> <tag>          abs frame numbers, 30 fps
#   RENDER=1 sh hybrid.sh ...             also write tapnext/hyb90_<tag>_<lo>.mp4
# Steps already done for the tag are skipped (detector outputs, registration).
#
#   1 run.sh           detector + people + cls_B classifier        -> <tag>.npz, <tag>cls.npz ...
#   2 homog/vreg.py    pitch registration every 15 frames          -> homog/<tag>_reg.npz
#   3 shipped picks    cls_B tracker (viterbi7 + gapfill)          -> alltests/p2d/cls_B/<tag>.npz
#   4 shipped 3D       flights of the shipped picks: the air gate  -> homog/<tag>_ball3dd.npz
#   5 tn_engine        TAPNext++ fwd + bwd over gaps <= 90 frames  -> alltests/tn/bridges_<tag>.npy
#   6 hybrid.py        accept bridges (20 px anchors, 24 px agree) -> alltests/p2d/hyb90/<tag>.npz
#   7 mk3d.py          default 3D chain on the hybrid picks        -> h3d_hyb90/<tag>_ball3dd.npz
#
# The result: h3d_hyb90/<tag>_ball3dd.npz  (abs, x, y, z in StatsBomb 120x80 / CR, state ground|air)
# Speed: step 5 costs ~4 min of GPU per match minute; run it during the half.
set -e
PY="${PY:-C:/Users/alexv/Desktop/Repos/PitchIQ/PitchIQHelper/.venv/Scripts/python.exe}"
cd "$(dirname "$0")"
B=$(pwd -W 2>/dev/null || pwd)
LO=$1; HI=$2; T=$3
[ -n "$T" ] || { echo "usage: sh hybrid.sh <lo> <hi> <tag>"; exit 2; }
export PYTHONPATH="$B/../..${PYTHONPATH:+;$PYTHONPATH}"

[ -f "${T}cls.npz" ] || sh run.sh "$LO" "$HI" "$T"
if [ ! -f "homog/${T}_reg.npz" ]; then
    echo "=== registration $(date +%T)"; (cd homog && "$PY" -W ignore vreg.py "$LO" "$HI" "$T" 4)
fi

echo "=== shipped picks $(date +%T)"
"$PY" -W ignore alltests/mkpicks_one.py "$T"

echo "=== shipped 3D $(date +%T)"
cd homog
"$PY" -W ignore vballsb.py "$T" > "${T}_chain.log" 2>&1
"$PY" -W ignore vfl0.py "$T" >> "${T}_chain.log" 2>&1
"$PY" -W ignore vflscan2.py "$T" 8 >> "${T}_chain.log" 2>&1
"$PY" -W ignore vrowspd.py "$T" >> "${T}_chain.log" 2>&1
"$PY" -W ignore vflchain2.py "$T" >> "${T}_chain.log" 2>&1
VS_B3="${T}_ball3d2.npz" "$PY" -W ignore vsmooth.py "$T" >> "${T}_chain.log" 2>&1
"$PY" -W ignore _mk.py "${T}_ball3d2.npz" "${T}_smooth.npz" "${T}_ball3dd.npz" >> "${T}_chain.log" 2>&1

echo "=== TAPNext bridges $(date +%T)"
cd "$B/tapnext"
"$PY" -u -W ignore tn_engine.py bridges "$T" 2>&1 | grep -v Warning | tail -3

echo "=== hybrid $(date +%T)"
cd "$B/alltests"
"$PY" -W ignore hybrid.py "$T"
mkdir -p "$B/h3d_hyb90"
cp "$B/homog/${T}_reg.npz" "$B/h3d_hyb90/"

echo "=== hybrid 3D $(date +%T)"
"$PY" -W ignore mk3d.py hyb90 "$T"
echo "-> h3d_hyb90/${T}_ball3dd.npz"

if [ -n "$RENDER" ]; then
    echo "=== render $(date +%T)"
    cd "$B/h3d_hyb90"
    "$PY" -W ignore ../tapnext/hyb_video.py "$T" "$LO" "$HI"
fi
echo "HYBRID DONE $(date +%T)"
