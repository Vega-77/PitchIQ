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
