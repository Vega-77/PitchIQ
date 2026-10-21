"""The slow, one-candidate-at-a-time detector the shipping one was checked against.

blobs_per_candidate.blobs is the oracle for blob_candidates.blobs, and
single_process_run.temporal_rows for temporal_features.temporal_rows; check_fast.py
compares them on real frames.  blobs_per_candidate.main and temporal_from_sheets.main
built the ranker's training tables (still4.npz, temporal4.npz) from the first
labelling round (PIQ_LEGACY_SP); single_process_run.main is the original one-process
detector run that detect_candidates.py parallelises.
"""
