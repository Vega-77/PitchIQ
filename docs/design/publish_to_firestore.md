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

### `merge_track_stats`

Until 2026-08-17 this function did not exist and the loop below wrote one
document per *cluster*. Two fragments of the same player meant two writes to
the same report, so the second silently replaced the first and the student
was shown whichever fragment happened to come last in the mapping: 24
touches of 54, 3.1 km of 7.3. The browser has always summed them, and says
so in `cvStatsByPlayer`'s docstring — so the coach's screen and the
student's own report disagreed about the same afternoon, with the student's
the smaller of the two.

---

## Moved from comments

Moved verbatim from comments beside the code in the deep clean (C-2).

### Thumbnails apart

The cropped pictures, kept apart from everything else in cvStats — and this is
a privacy decision rather than a data-modelling one.

They are photographs of children, cut out of match footage. They exist for one
job: letting a coach look at a tracked figure and say which of their players it
is. Once that mapping is confirmed the job is done, and what is left is a set
of pictures of minors with no remaining purpose, sitting in a database
forever.

While they lived inside `identity` alongside the clusters and the per-track
stats, nobody could remove them without destroying every number in the same
document — and `cvStats` is `allow write: if false` to every client, so in
practice nobody could remove them at all. In their own document a coach can
delete the pictures and keep the match. See `firestore.rules`, which grants
delete on this one path and nothing else: a client may destroy these, and
still may not fabricate a statistic.

### Event cap

Individual events written to Firestore, for the coach review tool.

Deliberately capped rather than paginated. A half produces a few hundred of
these; a run that produces thousands has gone wrong, and the right response to
that is a truncated document plus a flag saying so, not a document that fails
to write at all because it crossed a megabyte.

Fits under `byEvent.size() <= 1500` in firestore.rules. Nothing bounds this
document — the Admin SDK bypasses rules — but the coach's review of it is
written by a browser and capped, one decision per event. The two numbers were
reached by separate arguments about megabytes and happen to land equal, which
leaves the review no headroom at all: nothing clears `cvReview` when a run is
re-published, so a second run's decisions accumulate on top of the first's
until the whole document stops saving. Lowering this is safe; raising it is
not, and raising the rule is what the pair actually wants.

### Participant notes

Why the pipeline left figures out, written alongside the stats they were left
out of. Only the tracks something was decided about — the ones it kept and
believed carry no news, and there are forty of those for every one of these.

Capped because a school pitch with a crowd behind it produces a lot of
stationary people. The authoritative counts are in `quality.excluded_tracks`
and `quality.flagged_officials`, so truncating this list loses the reasons for
the least significant few and never the size of the correction.

### Keepers

A full stat block per keeper — saves, goals conceded, save
percentage, sweeper actions, distribution accuracy by type — drawn
on the coach's match view since the keeper block shipped. It sat
here rendered by nothing for as long as the field existed, which is
the failure `tests/smoke.test.js` now watches this dict for: a figure
that is computed, published and downloaded by every client, and put
on no screen. The track ids in it are also what the outfield shape
figures were computed against, since `pitchiq/metrics.py` excludes keepers
from every one of them.

### Dropped track fields

Track-row figures the report JSON carries and this document does not.

`team` and `track_ids` are not merely similar to the cluster row's — they are
the same values. `report_export.track_stats` seeds every `TrackStats` straight
off its `PlayerCluster`, and neither is touched again, so publishing both put
each team name and each fragment list on the wire twice in one document. The
copies that are read live on the cluster row: `coach.js::clusterRow`,
`passing.js::playersByTrack`, `review.js::whoIs`.

`pass_accuracy` is a quotient of two fields published beside it, and every
page that wants it divides for itself — `report.js::cvStatsByPlayer` computes
`passAccuracy` from the merged totals, which is the only correct way to get
it once two fragments are joined. Dropping it here is the same call, for the
same reason, as dropping `cvPassAccuracy` from `player_report_fields` below.
