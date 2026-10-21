# Baselines

A baseline is a report JSON from a run **somebody actually looked at and
believed**. Not a run that finished, and not a run whose numbers looked
plausible in passing — one where a person opened the video, checked a handful of
the events against what happened, and decided the output was a fair description
of the match.

That distinction is the whole point. Every threshold in `pitchiq/touches.py`,
`pitchiq/participants.py` and `pitchiq/phases.py` is a guess. The unit tests pin what those
guesses *are*; nothing pins what they *do* to a real match. Change one and every
test still passes. A baseline is the only thing that notices.

## This directory is empty, on purpose

Until October there was no real footage the pipeline could run on, so no run
worth believing. There is now: the team's panning footage runs end to end
through `--panned` (2026-10-20). What there is not yet is a run somebody has
checked against the match and believed, and a synthetic baseline would be worse
than none: it would pin the pipeline to its own current bugs and call that a
regression suite.

**The first one should be a window of a dev half whose ball and lines have been
checked by hand** (the Spot Check Lab sets), made with the panned command below.
Never one from a held-out half. See `FOOTAGE_DAY.md`.

## Making one

```bash
python -m pitchiq event_report \
    --video clips/2026-08-15-first-half.mp4 \
    --calibration clips/2026-08-15.calib.json \
    --tag-log clips/2026-08-15.log.json \
    --video-offset 137 \
    --json baselines/2026-08-15-first-half.json
```

From the panning camera, after the ball tracker has run on that window
(`pitchiq/balltrack/README.md`):

```bash
python -m pitchiq event_report --video clips/first-half.mp4 \
    --panned nb1 --json baselines/nb1-first-half.json
```

Then watch enough of the clip to have an opinion, and commit the JSON **with the
command that produced it** in the table below. A baseline whose command is lost
cannot be reproduced, which makes any diff against it unarguable in both
directions.

## Checking against one

```bash
python -m pitchiq compare_reports \
    baselines/2026-08-15-first-half.json runs/today.json
```

Exit code 1 means something moved. **That is not a failure.** A change to the
pipeline is supposed to change the output; the tool's job is to make you look at
what changed and say whether you meant it. If you did, replace the baseline and
say why in the commit message.

Two flags worth knowing:

- `--tolerance 0.05` when comparing across a change that legitimately shifts
  everything slightly.
- `--any-window` to compare two different clips of the same match, which drops
  `source`, `window` and `duration_s` from the comparison. Everything else still
  has to line up, so use it knowing that half the numbers are counts and counts
  do not survive a change of window.

## Baselines are tied to a schema version

Record the `schema_version` of every baseline in the table below (the
current one is `SCHEMA_VERSION` in `pitchiq/report_export.py`, 15 on 2026-10-20). A report from
an older schema does not merely have fewer keys — a version bump can mean the
same footage now produces different numbers, and the diff will be real rather
than cosmetic.

So far exactly one bump has done that: **schema 7** fitted the smoothing window
to each track's measured wobble instead of holding it at nine frames, which
lowers every distance and speed by up to 29 metres a minute on noisy tracks. Any
baseline taken under schema 6 will diff against a current run, correctly, and
should be retaken rather than argued with.

## What is in here

| file | clip | schema | command | who checked it, and when |
|---|---|---|---|---|
| _(none yet)_ | | | | |
