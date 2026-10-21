# `pitchiq/pipeline.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

The whole pipeline in one place: video in, match report out.

    STATUS (2026-10-20) — RUNS ON REAL MATCHES; PARTLY VERIFIED.

Every component below is individually tested, and the joins between them are
tested against synthetic data. Since October it also runs end to end on real
matches: the team's Hudl footage, which pans and zooms, goes in through
`pitchiq/panning_camera.py` (`--panned <tag>` on event_report and match_report), with the
ball from the tracker in pitchiq/balltrack.

What has been checked against people who watched the footage:
- The ball tracker: 70-74 % of 269 hand marks on 18 held-out clips, with few
  wrong picks (pitchiq/balltrack/README.md). At night it loses the ball almost
  everywhere, and `ballgate.py` blanks those stretches rather than guess.
- Registration, roles, team split and missed players: yes/no spot checks on
  every dev half and on the held-out halves. On the clean held-out game
  (Piscataway, 2026-10-15) the drawn pitch lines sat on the paint in 29 of 30
  checks and the team split was right in 14 of 17.

What has not:
- Possession. The one hand-counted window agreed, but on 12 s of live ball,
  and half its frames change answer if the ball moves 20 px.
- The fixed-camera path. No real match has been filmed from a still camera
  yet, so `calibration_path` has only met synthetic and lab frames.
- Anything at night beyond the lines: the ball, and so everything built on it.

So numbers that depend on the ball (possession, events, xG) are not ready to
show a coach as fact. ROADMAP.md, "Status (2026-10-20)", has the open items.

    from pitchiq.pipeline import analyse_match
    report = analyse_match('match.mp4', calibration_path='home.calib.json')

## Moved from the code

### `PlayerReport`

Mapping a track to a real player is a human job (ROADMAP archive, Phase 11), and at
the fragmentation currently measured a single player spans several tracks —
so these are fragments of players until a reviewer merges them.

## `analyse_match`

Its docstring before the deep clean (C-4) split it into stages and grouped
its arguments into `AnalysisOptions` and `MatchContext`. The measurements
still describe the code; the argument names are now fields of those two.

Run the full pipeline over a video.

Without a calibration this still returns possession, the team split and the
tracks themselves, which need only pixels. With one it adds everything
expressed in metres.

`tag_log` is the hand-tagged match log, as `shared/db.js::listLog` returns
it. It is what tells this pipeline when the ball was out of play; without it
every stoppage counts as possession for whoever was standing over the ball.
`video_offset_s` relates the two clocks, and is the same number the coach
already types in beside the video link. It is only the first half of that
relation: the tablet's clock stops for the interval and the footage does
not, so `second_half_video_s` — where the restart sits in this file — is
what makes a second-half timestamp land where it happened. Without it the
clock stays single-anchored and everything after the break is out by the
length of the break. See `phases.VideoClock`.

Speed and fragmentation, on the same 15s 720p window (450 frames, RTX
4060). Three fresh processes each, one model per process, medians given
with the observed spread:

    two passes   detect 8.0s + track 29.9s = 37.8s   (37.1-49.0)  146 tracks
    one pass                                17.1s    (17.0-18.4)  125 tracks

So roughly 2.2x faster. Two things produce that. The duplicated decode and
duplicated inference are gone; and the surviving inference is batched,
where `model.track()` forces one frame at a time. That second point is also
why the old path's spread is so much wider than the new one's — anything
that perturbs per-call overhead gets multiplied by 450 there and by 29
here.

Fragmentation came down slightly rather than up, which is the ByteTrack
bargain: detection now runs at the ball's lower threshold, and those extra
weak boxes go to the tracker, which uses them to re-associate tracks it
already has while still refusing to create tracks from them. The old
arrangement detected at 0.25 for tracking and threw that away.

125 is still far more than the 22 people on a pitch, so per-track figures
remain fragments. `pitchiq/identity.py` merges them — on this window, into 37
clusters — and a human confirms the result. That made the problem smaller,
not absent.

`sample_fps` is the speed lever, and it is stated in hertz because that is
the only unit any of this is true in. `stride` still works and is what
actually reaches the frame loop, but a stride is a ratio to a number nobody
said out loud: stride 2 is fifteen frames a second on a camcorder and
thirty on a phone, and those are different analyses run by the same flag.
Given `sample_fps`, the stride is worked out from the source and the rate
that really ran is published.

What it costs splits cleanly in two, and the two halves disagree.

**The movement figures do not care.** Measured synthetically against
constructed truth in tests/test_sampling.py: distance, mean speed, sprints
and bursts are flat from 60Hz down to 6Hz, and top speed moves less with
the rate than it does with a tenth of a metre of positional wobble. The
reason is that the smoothing window is stated in seconds, so the smoothed
path is nearly the same curve at any rate — the average simply holds fewer
samples. That ends at three samples, which is `metrics.min_sample_hz()` and
currently six a second; below it the burst count starts reporting wobble.

**Tracking and the ball do care**, and they are what actually bounds the
rate. Measured on real footage:

    botsort.yaml,   stride 1      100 tracks, longest 449/450
    bytetrack.yaml, stride 1      119 tracks, longest 418
    botsort.yaml,   stride 2       70 tracks, longest 225/225

A third of the tracks and half the longest one, for half the compute. Since
ball coverage bounds every event statistic downstream and fragmentation is
already the weakest link, the default stays every frame.

So the useful finding is narrower than "subsample everything": the movement
figures were never the reason to run at full rate. The one place that is
free money is a source above 30fps — a phone export at stride 1 does twice
the inference of a camcorder for figures that measure the same to within a
percent, and `sample_fps=30` is how to stop paying for it.

(`track_imgsz` is gone. It set a separate image size for the tracking pass,
and there is no longer a separate pass for it to size.)

`detector` and `tracker_factory` are handed straight to `TrackedFramePass`,
which has taken both since it was written so that its bookkeeping could be
checked without a GPU (see pitchiq/frame_table.py). For a long time the seam stopped
one level short of here, and this — the function every published number
comes out of — was the one part of the pipeline nothing could test: the
detector was built inside, so calling this at all meant installing
ultralytics and torch, which `requirements-test.txt` deliberately leaves
out. Passing them through costs two arguments and makes the assembly of
twenty-odd subsystems something a test can run.

`panned` is a `pitchiq.panning_camera.PannedCamera`, for footage from a camera that
follows the play. It replaces `calibration_path`: every box is moved into
one reference view before anything is measured, and the ball comes from
the tracker in `pitchiq/balltrack` instead of this pass's own detections.
