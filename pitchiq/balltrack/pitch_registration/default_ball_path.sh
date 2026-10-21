#!/bin/sh
# The default ball path, chosen 2026-10-01 on vid2 (unseen) and vid1 (dev + holdout):
#   chain2 flights (the most precise: one false flight on each clip)
#   + ground smoothed by smooth_ball (hop 0), merged -> <tag>_ball3dd.npz
# strict_flights is left out: on chain2 it lowered precision on 2 of 3 test sets and cost flights.
#
#   sh default_ball_path.sh vid2        (needs <tag>_scan2.npz from flight_scan.py)
BT="$(cd "$(dirname "$0")/.." && pwd)"; . "$BT/pyenv.sh"
cd "$BT/pitch_registration"
set -e
tag=${1:-vid1}
"$PY" -m $M.pitch_registration.flight_chain $tag
VS_B3=${tag}_ball3d2.npz "$PY" -m $M.pitch_registration.smooth_ball $tag
"$PY" -m $M.pitch_registration.merge_air_and_ground ${tag}_ball3d2.npz ${tag}_smooth.npz ${tag}_ball3dd.npz
echo "-> ${tag}_ball3dd.npz"
