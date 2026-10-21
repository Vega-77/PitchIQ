# Archived ball-tracker scripts

Scripts that nothing in the shipping chain runs any more. They were moved here
on 2026-10-20 so the live ones were easier to find, and deleted on 2026-10-21
when the tracker became a package: they imported their siblings by bare name
and found their inputs relative to their own folder, so none of them ran from
here anyway.

Nothing kept imports, runs or names any of them (`run.sh`, `hybrid.sh`,
`half.py`, `retrain.sh`, `pitch_registration/default_ball_path.sh`, the `evaluation/` shell scripts,
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
| earlier Crop Lab rounds: sheets and hints (their labels are in `crop_labels/crops.jsonl`) | `lab_sheets2.py`, `lab_sheets3.py`, `lab_sheets4.py` |
| every 2D version over the labelled windows, and the queue that fed them to the 3D chain (`evaluation/mkpicks_one.py` makes the shipped version for one window) | `evaluation/mkpicks.py`, `evaluation/q3d.py` |
| media for the Air, Flight and Fix Labs, all answered (their labels stay; `score_air_labels.py`, which turns the Air Lab's into `airtruth.npz`, stays too) | `pitch_registration/vairclips.py`, `pitch_registration/vflightlab.py`, `pitch_registration/vfixclips.py` |
| flight-gate tuning and diagnostics from before the label-free chain (`pitch_registration/flight_chain.py`) | `pitch_registration/vfltune.py`, `pitch_registration/vflapply.py`, `pitch_registration/vflfix.py`, `pitch_registration/vflfp.py`, `pitch_registration/vairwhy.py`, `pitch_registration/vfl1.py`, `pitch_registration/vfl2.py`, `pitch_registration/vfl3.py` |
| review videos and the venue-map image | `pitch_registration/vflvideo.py`, `pitch_registration/vflvideo2.py`, `pitch_registration/vflvideo3.py`, `pitch_registration/vfullvideo.py`, `pitch_registration/vphonevideo.py`, `pitch_registration/vrender.py` |
| venue-map, paint and registration diagnostics | `pitch_registration/vbox.py`, `pitch_registration/vchk.py`, `pitch_registration/vdbg.py`, `pitch_registration/vdense_t.py`, `pitch_registration/vend2.py`, `pitch_registration/vendacc.py`, `pitch_registration/vends.py`, `pitch_registration/vmeas.py`, `pitch_registration/vpt.py`, `pitch_registration/vraw.py`, `pitch_registration/vsheet.py`, `pitch_registration/vtilt.py`, `pitch_registration/vy2.py`, `pitch_registration/vyellow.py` |
| a plot of the ball's path, one panel per minute | `pitch_registration/vpathplot.py` |
