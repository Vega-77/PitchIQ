# live-tagging/

The match-day tablet: tag kick-off, restarts, substitutions, cards and goals as
they happen. The result is the tag log the pipeline uses to know when the ball
was dead and who was on the pitch.

It works offline. Every write is a Firestore batch that queues until the
connection returns, and undo needs no query. `sheet.js` asks each question in
that event's own wording (awarded to, committed by, scored by). Run it with
the rest of the site (`shared/README.md`, "Local development"). It needs a
coach account with a squad.

## Tagging a recording as if it were live

The clock runs on the tablet's own time from the kick-off tap, so a recording
works the same as a touchline as long as it plays at normal speed.

- Start the video a little before kick-off. Press **KICK OFF** on the whistle.
- Tap fouls, offsides, cards and goals when the whistle goes. Tap throw-ins,
  corners, goal kicks and free kicks when they are **taken**: the pipeline
  counts the time between the two as dead ball (`pitchiq/phases.py`).
- If you pause the video, pause the clock too (tap the clock). Its reading is
  every later tag's timestamp.
- At half-time press **Half-time**, skip the video to just before the second
  kick-off, and press kick-off on that whistle. The break is not counted.
- On the coach page, the footage form takes the video time of each kick-off
  (`videoOffsetS`, `secondHalfVideoS`). The log download carries both, and
  `pitchiq.experiments.cached_report half ... --tag-log <file>` reads them.
