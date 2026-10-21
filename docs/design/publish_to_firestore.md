# `pitchiq/publish_to_firestore.py`

Moved verbatim from the module docstring in the deep clean (C-2). The docstring
in the code keeps what the module does, its inputs and outputs and its limits;
this keeps the reasoning, the measurements and the history behind it.

---

Getting CV stats out of Python and into the app.

This module holds a credential that **bypasses every Firestore security rule**.
The rules are the entire security boundary of this app, and the app stores the
names and email addresses of minors. So the interesting part of this file is
not the writing; it is the four things it refuses to do.

**The key never lives in the repo.** Its path comes from `PITCHIQ_SA_KEY` and
nowhere else, and `_check_key` refuses to run if that path resolves anywhere
inside the working tree. A service-account JSON committed to a public GitHub
Pages repo is a total compromise of every team's data, and it is an easy
mistake — the file looks like config.

**It writes to an allowlist of paths, not wherever it is told.** Everything
here goes under `teams/{t}/matches/{m}/cvStats/*` or adds `cv*` fields to an
existing player report. It never touches `players`, `users`, `invites` or
`teams` — the documents holding personal data — so a bug in this file cannot
leak or destroy any of it.

**It never creates player reports, only updates them.** A report exists because
a coach published a match. If one is missing, that is a mismatch worth
surfacing rather than papering over with a document nobody asked for.

**It refuses to write per-player stats from unconfirmed clusters.** At the
fragmentation this pipeline measures, a cluster is a guess about identity until
a human agrees with it. Writing guesses into a named player's season is exactly
the thing that would make the whole feature untrustworthy.

Setup, once:

    setx PITCHIQ_SA_KEY "C:\Users\you\keys\pitchiq.sa.json"

The key is issued from the Firebase console under Project settings → Service
accounts → Generate new private key. Keep it outside the repo. `*.sa.json` is
gitignored as a second line of defence, but the check below is the real one.

## Moved from the code

### `player_report_fields`

No `cvPassAccuracy`. It was written here until 2026-08-17 and read by
nothing: every page divides the two fields above instead. That made it
the one field this side writes and `cvReportFields` does not clear, so
a coach who un-mapped a player and re-published left a pass accuracy
standing beside the two nulls it was supposedly derived from — a trap
for whoever reads it next, in exchange for a number already on the
document twice over.

### `merge_tracks`

Until 2026-08-17 this function did not exist and the loop below wrote one
document per *cluster*. Two fragments of the same player meant two writes to
the same report, so the second silently replaced the first and the student
was shown whichever fragment happened to come last in the mapping: 24
touches of 54, 3.1 km of 7.3. The browser has always summed them, and says
so in `cvStatsByPlayer`'s docstring — so the coach's screen and the
student's own report disagreed about the same afternoon, with the student's
the smaller of the two.
