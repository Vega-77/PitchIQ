#!/bin/sh
# The default ball path, chosen 2026-10-01 on vid2 (unseen) and vid1 (dev + holdout):
#   chain2 flights (the most precise: one false flight on each clip)
#   + ground smoothed by vsmooth (hop 0), merged -> <tag>_ball3dd.npz
# vstrict is left out: on chain2 it lowered precision on 2 of 3 test sets and cost flights.
#
#   sh vdefault.sh vid2        (needs <tag>_scan2.npz from vflscan2.py)
cd "$(dirname "$0")"
PY=C:/Users/alexv/Desktop/Repos/PitchIQ/PitchIQHelper/.venv/Scripts/python.exe
set -e
tag=${1:-vid1}
$PY vflchain2.py $tag
VS_B3=${tag}_ball3d2.npz $PY vsmooth.py $tag
$PY _mk.py ${tag}_ball3d2.npz ${tag}_smooth.npz ${tag}_ball3dd.npz
echo "-> ${tag}_ball3dd.npz"
