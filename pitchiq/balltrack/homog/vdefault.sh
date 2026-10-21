#!/bin/sh
# The default ball path, chosen 2026-10-01 on vid2 (unseen) and vid1 (dev + holdout):
#   chain2 flights (the most precise: one false flight on each clip)
#   + ground smoothed by smooth_ball (hop 0), merged -> <tag>_ball3dd.npz
# vstrict is left out: on chain2 it lowered precision on 2 of 3 test sets and cost flights.
#
#   sh vdefault.sh vid2        (needs <tag>_scan2.npz from flight_scan.py)
BT="$(cd "$(dirname "$0")/.." && pwd)"; . "$BT/pyenv.sh"
cd "$BT/homog"
set -e
tag=${1:-vid1}
"$PY" -m $M.homog.flight_chain $tag
VS_B3=${tag}_ball3d2.npz "$PY" -m $M.homog.smooth_ball $tag
"$PY" -m $M.homog._mk ${tag}_ball3d2.npz ${tag}_smooth.npz ${tag}_ball3dd.npz
echo "-> ${tag}_ball3dd.npz"
