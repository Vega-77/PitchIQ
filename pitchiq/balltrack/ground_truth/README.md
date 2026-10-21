# balltrack/ground_truth/

The Ball Truth test set: 269 human marks over 18 held-out clips of the
scrimmage (wm00-17), one file per mark in `truth/fNNN.json` (`kind` ball, off
or hidden; `x`, `y` in source pixels). `python -m pitchiq.balltrack.wm_score`
and `evaluation/score2d.py` score against it.

It is the test set: never train on it, pack it or merge it into another label
set (`labels_pack.py` refuses to pack it).
