# `pitchiq/frame_table.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

One pass over the video, holding everything a frame knows at once.

An event needs three facts about the same instant: where the ball is, which
players are where, and who they play for. Until this module existed the pipeline
could not supply all three, because it ran the model twice over two disjoint
identity spaces — a detection pass that knew the ball and the shirt colours but
numbered players per-detection, and a tracking pass that gave players persistent
identities but discarded the ball. Neither half could see the other, which is
why `PlayerReport.team` was hardcoded to UNKNOWN.

The reason the tracking pass loses the ball is worth writing down, because it
looks like a threshold problem and is not. In ultralytics, tracking is a
callback that *replaces* the results with the tracked subset:

    tracks = tracker.update(det, result.orig_img)
    idx = tracks[:, -1].astype(int)
    predictor.results[i] = result[idx]          # trackers/track.py

A small, fast, low-confidence object never associates consistently enough to
confirm, so the ball is filtered out of the output entirely rather than merely
scored low. No confidence setting recovers it.

So this module drives the tracker itself. Detection stays batched, which is
where the speed is; the tracker is pure CPU and does not need to sit inside the
inference loop. That buys three things at once:

  * one video decode instead of two, and one inference instead of two,
  * the ball, kept out of the tracker entirely so it can never be associated
    into a player track,
  * shirt colours sampled against *real* track ids, which is what finally makes
    a player's team knowable.

There is a second, less obvious win. Detection here runs at the ball's low
threshold, and those extra low-confidence boxes are handed to the tracker too.
That sounds like it would flood the tracker with junk tracks, and it cannot:
ByteTrack gates track *creation* on `new_track_thresh` (0.25 in both shipped
configs) while still using detections above `track_low_thresh` (0.10) to
re-associate tracks that already exist. Low-confidence boxes can only rescue a
track, never invent one. That is the whole idea of ByteTrack, and the previous
two-pass arrangement was throwing it away.

Measured on one 15s 720p window (450 frames, RTX 4060), three fresh processes
each: a median 17.1s against the two-pass arrangement's 37.8s, and 125 tracks
against 146. So roughly 2.2x faster and slightly less fragmented, which is the
outcome the threshold argument above predicted.

Worth knowing before trusting any of that: the old path's timings ranged from
37 to 49 seconds across three runs while the new path's ranged from 17.0 to
18.4. Unbatched per-frame inference multiplies every source of overhead by the
frame count, so the two-pass numbers are noisy in a way the one-pass numbers
are not. Single measurements on this machine are not to be believed — an
earlier run of the old path came in at 20.5s and was simply an outlier.

What this does *not* fix: 125 tracks is still five times the number of people on
a pitch, so per-track figures remain fragments of players until `pitchiq/identity.py`
merges them and somebody confirms the merge.
