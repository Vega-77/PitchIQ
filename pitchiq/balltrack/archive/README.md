# Archived ball-tracker scripts

Scripts that nothing in the shipping chain runs any more. They were moved here
on 2026-10-20 so the live ones were easier to find, and deleted on 2026-10-21
when the tracker became a package: they imported their siblings by bare name
and found their inputs relative to their own folder, so none of them ran from
here anyway.

Nothing kept imports, runs or names any of them (`run.sh`, `hybrid.sh`,
`half.py`, `retrain.sh`, `homog/vdefault.sh`, the `alltests/` shell scripts,
the scorers the README documents, and everything under `pitchiq/` outside
`balltrack/`). `hybrid.sh` gave byte-identical outputs on a one-minute window
before and after each step.

Git still has every one. To read or rerun one, restore it from the last commit
that had it, then fix its imports to the package layout:

    git log --oneline -1 -- pitchiq/balltrack/archive/homog/vfltune.py
    git restore --source <that commit>~1 -- pitchiq/balltrack/archive/homog/vfltune.py

`git log --follow` on the restored file shows its history from before the move.

| what | scripts |
|---|---|
| earlier Crop Lab rounds: sheets and hints (their labels are in `labdb5/crops.jsonl`) | `lab_sheets2.py`, `lab_sheets3.py`, `lab_sheets4.py` |
| every 2D version over the labelled windows, and the queue that fed them to the 3D chain (`alltests/mkpicks_one.py` makes the shipped version for one window) | `alltests/mkpicks.py`, `alltests/q3d.py` |
| media for the Air, Flight and Fix Labs, all answered (their labels stay; `vairscore.py`, which turns the Air Lab's into `airtruth.npz`, stays too) | `homog/vairclips.py`, `homog/vflightlab.py`, `homog/vfixclips.py` |
| flight-gate tuning and diagnostics from before the label-free chain (`homog/flight_chain.py`) | `homog/vfltune.py`, `homog/vflapply.py`, `homog/vflfix.py`, `homog/vflfp.py`, `homog/vairwhy.py`, `homog/vfl1.py`, `homog/vfl2.py`, `homog/vfl3.py` |
| review videos and the venue-map image | `homog/vflvideo.py`, `homog/vflvideo2.py`, `homog/vflvideo3.py`, `homog/vfullvideo.py`, `homog/vphonevideo.py`, `homog/vrender.py` |
| venue-map, paint and registration diagnostics | `homog/vbox.py`, `homog/vchk.py`, `homog/vdbg.py`, `homog/vdense_t.py`, `homog/vend2.py`, `homog/vendacc.py`, `homog/vends.py`, `homog/vmeas.py`, `homog/vpt.py`, `homog/vraw.py`, `homog/vsheet.py`, `homog/vtilt.py`, `homog/vy2.py`, `homog/vyellow.py` |
| a plot of the ball's path, one panel per minute | `homog/vpathplot.py` |
