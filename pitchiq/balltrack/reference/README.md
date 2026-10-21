# balltrack/reference/

The slow, one-candidate-at-a-time detector the shipping one was checked against.
`python -m pitchiq.balltrack.reference.check_fast [n_anchors] [first_frame]`
runs both on real frames of `PIQ_VIDEO`, prints SAME or DIFFERENT and exits 1
when they differ. `blobs_per_candidate.py`, `single_process_run.py` and
`temporal_from_sheets.py` are the reference pieces; `blobs_per_candidate.py`
and `temporal_from_sheets.py` read the first labelling round's data from
`PIQ_LEGACY_SP`. Do not edit `single_process_run.py`'s LO/HI windows.
