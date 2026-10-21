# live-tagging/

The match-day tablet: tag kick-off, restarts, substitutions, cards and goals as
they happen. The result is the tag log the pipeline uses to know when the ball
was dead and who was on the pitch.

It works offline. Every write is a Firestore batch that queues until the
connection returns, and undo needs no query. `sheet.js` asks each question in
that event's own wording (awarded to, committed by, scored by). Run it with
the rest of the site (`shared/README.md`, "Local development"). It needs a
coach account with a squad.
