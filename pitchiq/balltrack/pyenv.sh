#!/bin/sh
# Sourced by the tracker's shell scripts, with BT set to pitchiq/balltrack:
#   BT="$(cd "$(dirname "$0")" && pwd)"; . "$BT/pyenv.sh"
# Sets PY (the repo's venv python, unless PY is already set) and M (the package:
# a script runs as "$PY" -m "$M.<module>"), and puts the repo root on PYTHONPATH,
# so each script can run from the folder it reads and writes.
R="$(cd "$BT/../.." && pwd)"
V="$R/.venv/Scripts/python.exe"; [ -x "$V" ] || V="$R/.venv/bin/python"; [ -x "$V" ] || V=python
PY="${PY:-$V}"
M=pitchiq.balltrack
RW="$(cd "$R" && { pwd -W 2>/dev/null || pwd; })"  # the root as python sees it (C:/... on Windows)
case "$RW" in [A-Za-z]:*) SEP=';' ;; *) SEP=':' ;; esac
export PYTHONPATH="$RW${PYTHONPATH:+$SEP$PYTHONPATH}"
