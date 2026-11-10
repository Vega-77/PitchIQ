# Ball push: brief for an autonomous run

You are running on your own for several days on the PitchIQ repo
(`C:\Users\alexv\Desktop\Repos\PitchIQ`, data in `..\PitchIQ-data`). The owner,
Alex, is a student coaching a high school soccer team. They read your progress
on their phone and will not be steering you. Read this brief at the start of
every wake-up, then read your ledger (below) and carry on from it.

## The goal

The ball tracker is right most of the time it draws a ring, but it leaves the
ball unringed far too often. Get it to:

- **missed / total < 0.25** (coverage above 75%)
- **right / (right + wrong) > 0.90** (accuracy above 90%)

Keep going until both hold or the deadline comes, whichever is first.

**Wrong is worse than missed.** A version that gains coverage by dropping
accuracy below 0.90 is not progress, however large the gain. Never give up
accuracy to buy coverage past that line.

## The scoreboard

Each item is one frame the owner answered in the Ball Clicker. The scorer is
`pitchiq/experiments/clicker/clickscore.py`; read its docstring.

Counts, over frames answered "ball":
- **right:** a ring within 15 px of the click.
- **wrong:** a ring anywhere else.
- **missed:** no ring.

A ring on a frame answered "not in frame" also counts as wrong. "Hidden",
"dead" and "can't tell" answers are reported but not in the two ratios.

The baseline is V23, the shipped pipeline today:

| Set | Right | Wrong | Missed | Missed/total | Accuracy |
|---|---|---|---|---|---|
| Dev (honest) | 311 | 27 | 278 | 0.45 | 0.92 |
| Held out | 62 | 10 | 192 | 0.73 | 0.86 |

**Your first job is to reproduce these numbers yourself.** If you cannot,
find out why before you change anything.

### Done means both sets clear the bar
- **Dev**, scored honestly.
- **Held out**, scored once on the finished version.

### Honest scoring rules
- **Dev scores of anything trained on dev clicks must be out-of-fold.** Train
  without the game being scored (leave one game out), as
  `PitchIQ-data/scratch-2026-10-26/oof_cls.py` and `replay.py` do. In-sample
  dev scores have fooled this project before: a "floor" looked like +25 right
  / +7 wrong in-sample and traded 1:1 when scored honestly.
- **Held-out halves are nb2, pi1, wh1, co1 and wb2.**
  - Never train on them, tune on them, or study their frames for ideas.
  - Score them at most once per 24 hours. Log every look, with what was
    scored and the result, on the live page.
  - Held out is how you learn whether an idea works on a game it has never
    seen. Every extra look spends some of that.
- **Every claimed gain is re-scored from scratch by a fresh verifier subagent**
  before it goes on the scoreboard. The verifier gets the candidate's outputs
  and the scorer, not your reasoning.

## Where things stand

- **Pipeline:** `pitchiq/balltrack/`.
  - `half.py` runs a pass.
  - Ball stages: blob finder, ranker (xgb), classifier (cls_C, calibrated in
    its checkpoint).
  - Then the tracker, Hybrid 90 TAPNext bridges for gaps of 90 frames or less,
    and `ballgate.py`, a game-level gate that blanks lost stretches.
- **Clicks:**
  - The Ball Clicker artifact is https://claude.ai/artifact/86WZCjEKrvXA47AJLgEsmz.
  - Answers and media are in `PitchIQ-data/clicker/`, with `clicks.json` and
    `rings.json` per version.
  - Build code is in `pitchiq/experiments/clicker/`.
  - There are 609 dev ball clicks for training.
- **Last retrain:** labels in `PitchIQ-data/retrain-2026-10-26`. The plan and
  probes are in `PitchIQ-data/HANDOFF-2026-10-26.md` and
  `PitchIQ-data/scratch-2026-10-26/`.
- **Where the 609 dev balls are lost.** This is the funnel from
  `scratch-2026-10-26/clickfunnel.py`, as of the retrain; rerun it on V23
  first.

| Stage | Balls |
|---|---|
| No blob | 116 |
| Ranker | 5 |
| Classifier ranks another blob higher | 28 |
| Classifier score too low | 95 |
| Tracker / gate | 59 |
| Ring off the ball | 18 |
| **Right** | **288** |

  The ball is about 12-16 px across on a wide, panning, often dark or
  floodlit camera.
- **Your memory notes** load with every session (MEMORY.md and the files it
  links). They hold this machine's traps and every past result. Read the
  ball-related ones before planning.

## Improvement is the measure

What counts is beating the baseline on the scoreboard, not how new an idea is.

- **Ratchet.** When a change beats the current best and its verifier agrees,
  keep it. It becomes the new baseline that every later idea builds on and is
  scored against, so gains stack.
- **Old ideas are welcome in a new form.** Reworking a past idea is fine, and
  so is combining it with something else or pushing it harder (more data,
  another stage, another way of building it). Each such attempt goes in the
  ledger as "rework of X", with what is different and why that should change
  the result. Re-running a dead idea the same way is not allowed.
- **Keep a mix.** At any time, at least one line of work builds on the current
  best, and at least one tries something not in the list below.
- **Unstick.** If 12 hours pass with no kept gain, re-rank:
  - rerun the funnel on the current best
  - move effort to the stage losing the most balls
  - try a family of ideas not yet touched

  Write down why the earlier lines stalled.

## Already tried: how each went

Treat these as results to build on or route around, not as a ban. Each needs
the "rework of X" note above before it is tried again.

- **Detector swaps:** COCO YOLOv8n, a soccer YOLO11n, WASB whole-frame and
  tiled. All see the ball sometimes; none rank it above scenery. Also dead:
  score cuts and per-blob features on top of them.
- **WASB heatmap model:**
  - Alone: 182/609 ranked first, versus the chain's 235, even with 5x the data.
  - As a chain feature: +8 first, rings +6 right / -5 wrong, at about 10 GPU
    hours a half.
- **Local-contrast blobs:** +13 right for +12 wrong.
- **Stillness or feet distance to separate clutter:** they don't separate it.
- **Five-frame classifier crops:** 233 versus 235 for a single frame.
- **Carrying the ball with its holder:** +7 right / +16 wrong at best.
- **Long TAPNext bridges (90-450 frames):** 9 of 205 landed; +3 right / +4 wrong.
- **Per-frame score floors, lower off-scores, neighbour pooling:** about 1
  wrong per right on honest scores.
- **Night games:** KEEP caps, colour normalisation and thresholds don't help.
  The models don't know this footage's night ball.
- **What did work:** the relative-brightness blob cut, the ranker and
  classifier retrain, and score calibration. Together: 171/48/400 to 288/22/303.

## How to work

- **Ideas from anywhere.** Machine learning or not, all are welcome. Some
  directions:
  - Better source video.
  - Physics of the ball's flight and roll.
  - Where the players look and run.
  - Kick moments from motion or audio.
  - Multi-hypothesis tracking.
  - Self-training on confident tracks.
  - Synthetic balls pasted into frames.
  - Training data from other footage.

  Start with whatever the funnel says loses the most balls.
- **Run several lines of work at once.** Use subagents, each in its own git
  worktree. One idea, or one stage, per agent.
- **One GPU.** It is an RTX 4060 with 8 GB, and a full pass peaks near 6.9 GB.
  - Keep a GPU queue so jobs never run two big GPU loads at once.
  - CPU work and analysis can run beside the GPU job.
  - Before relaunching anything, check for orphaned processes and kill them by
    PID.
- **Data is shared.** Worktrees share `PitchIQ-data`. Write new outputs to
  your own folders under `PitchIQ-data/ball-push/`. Never overwrite the
  shipped pass outputs, the clicks or the scoring snapshots.
- **Prefer cheap tests.** Replay tracker and gate changes on stored pass
  outputs before running a full pass, which takes about an hour a half.
- **Keep a ledger** in `PitchIQ-data/ball-push/LEDGER.md`. Every idea gets:
  - what it is
  - new, or "rework of X" with what is different
  - why it might work
  - the exact command
  - dev and held-out numbers
  - a verdict (works / dead / promising)
  - commit hashes

  This file is how you resume after a restart. The app restarts itself
  sometimes, and the session can drop for hours. Update it as you go, not at
  the end.
- **Lessons go into memory.** When an idea dies or works, write a short memory
  note for it with the numbers, the way the existing notes are written.

## More labels from Alex

You may ask for more clicks: a new Ball Clicker batch of a few hundred frames.

- **Never wait for them.** Keep working on other lines as if no answers will
  come. When answers arrive, use them.
- **Training frames come from dev games only.** New held-out frames may be
  added for scoring only.
- **Labs are answered on a PC,** in batches. Retire answered batches. A
  version holds at most 256 MB and 511 files, and each file at most 16 MB.

## Reporting

**Every hour, two things:**

1. A short push notification:
   - the current best dev score (right / wrong / missed and both ratios),
     and the change from V23
   - what changed since the last hour
   - what is running now
2. An update to one private live page, a claude.ai artifact named "Ball Push".
   It shows:
   - the scoreboard over time as a chart
   - the bar
   - each held-out look, with its date and result
   - every idea tried, with its result and verdict
   - what is running now, with ETAs
   - GPU use
   - any lab waiting for Alex

   Build it once, keep its data in its database, and update the rows hourly.
   Send the link in the first push.

The pushes are for a phone: short, plain words, numbers first.

## Deadline and handoff

Everything stops by **Monday 2026-11-16, 11:59 PM Pacific**. That is Tuesday
2:59 AM Eastern on the PC's clock, and 08:59 CET on 2026-11-17. After that the
plan that pays for this run ends.

- **6 PM Pacific on Monday (9 PM Eastern):** start no new experiments.
- **10 PM Pacific (1 AM Eastern):** have the handoff done.

The handoff is a full report another model can pick up cold:
`PitchIQ-data/HANDOFF-2026-11-16-ball.md`, with the same content on the live
page. It covers:
- the best version: its commits, its exact scores on both sets, and how to
  reproduce them
- every idea tried, with numbers and verdicts
- the open leads, ranked, with what each needs next
- what not to retry, and why
- the state of every file, branch and worktree
- confirmation that every job is stopped

Send a final push with the link.

If you clear the bar earlier, do the held-out check and the handoff then, and
stop.

## Hard rules

- **Git:**
  - Work on a branch of your own (e.g. `ball-push`) in a worktree.
  - Commit finished work with explicit paths. End each commit message with the
    co-author line from your system prompt.
  - Never push.
  - Never touch the main checkout's branch or working tree: other sessions use
    it.
- **Leave the live product alone:**
  - Never deploy Firestore rules.
  - Never write to the production Firestore.
  - Never change the website.
  - Don't change shipped pipeline defaults outside your branch. The best
    version is handed over for Alex to adopt.
- **Security:**
  - Never read `C:\Users\alexv\pitchiq-sa.json`.
  - Use `torch.load(..., weights_only=True)`, or safetensors.
  - Models, weights and pip packages from trusted sources are allowed. Put
    each in its own folder and say in the ledger what you downloaded and from
    where.
  - Never run or import code from cloned repositories or other downloads.
    Reimplement what you need.
- **Truthfulness:**
  - Report every number as measured, including the bad ones.
  - When a run fails or is cut short, the ledger and the page say so.

## Starting

Ask Alex nothing up front: they have answered everything in this brief.

1. Ask the app to keep the PC awake.
2. Reproduce the baseline.
3. Rerun the funnel on V23.
4. Build the live page and send the first push.
5. Plan the first parallel lines of work, then go.
