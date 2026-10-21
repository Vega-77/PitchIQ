# Report schema

The contract between the pipeline and the site. A match report is built once in
Python, written as a JSON file, and published to Firestore as a few documents
that the browser reads. Nothing on the browser side sees the JSON file itself.

```
pitchiq/pipeline.py  MatchReport.to_json()  ->  report_export.build_report_json()
        |                                            (the report JSON file)
        v
pitchiq/publish_to_firestore.py  summary / events / identity / thumbs payloads,
        |                        cv* fields merged onto player reports
        v
Firestore  teams/{t}/matches/{m}/cvStats/{summary,events,identity,thumbs}
           teams/{t}/matches/{m}/playerReports/{p}   (cv* fields)
        |
        v
shared/db.js (reads)  ->  shared/report*.js (derives)  ->  coach/, player/, halftime/
```

Rules that hold across all of it:

- **Keys are never renamed.** Fields can be added. A change of meaning bumps
  `SCHEMA_VERSION` in `pitchiq/report_export.py`, whose comment block lists every
  version and why it changed. The current version is 15.
- **Absent is not zero.** A figure nothing could measure is `null`, never `0`.
  Every page renders `null` as a dash. A key that is spelt wrong also reads as
  `undefined`, which is why the seam tests below exist.
- **Units are in the key**: `_m` metres, `_s` seconds, `_kmh`, `_ms2`, `_share`
  (0-1), `_ph` player heights. `possession_pct` is also a 0-1 share; the name
  predates the convention and keys are not renamed. Positions are pitch metres,
  origin at the bottom-left corner of the broadcast view, x along the touchline
  and y along the goal line (`pitchiq/pitch.py`). Shot maps and published event positions are drawn on
  the standard 105 x 68 pitch (`report_export.drawn`), so every page plots into
  one frame whatever the measured pitch was.
- **JSON is snake_case and Firestore is camelCase.** `publish_to_firestore.py` does
  the renaming, field by field. The `cv` prefix on player-report fields marks a
  figure the pipeline estimated, as opposed to one a human tagged.

## The report JSON

Top level, from `report_export.build_report_json` (written by
`python -m pitchiq event_report --video <file> --json <path>`, read by `publish_report` and
`compare_reports`):

| key | type | meaning |
|---|---|---|
| `schema_version` | int | `SCHEMA_VERSION` of the code that wrote it |
| `source` | str | file name of the video the report came from |
| `window` | dict | the stretch analysed (`start_s`, `end_s`), empty when none was given |
| `duration_s` | float | footage seconds covered |
| `period`, `period_source` | str | which half, and whether a tag or the clock said so |
| `calibrated` | bool | false: nothing in metres was computed, and those keys are null |
| `calibration_error_m` | float or null | landmark residual of the calibration |
| `pitch` | dict or null | `length_m`, `width_m` of the measured pitch (null uncalibrated) |
| `trustworthy` | bool | false when `warnings` says a number should not be shown as fact |
| `warnings` | list of str | plain-language reasons, shown to the coach as written |
| `quality` | dict | how much was seen: ball seen / filled share, tracks per cluster, position noise, blind spells, pitch coverage, realtime factor, ... |
| `timing` | dict or null | where the run's seconds went, and whether it keeps up with a live half (not published; null when the run was not timed) |
| `teams` | dict | `team_a`, `team_b`: possession, passes, shots, xG, territory, shape, pressing, ... per team |
| `tracks` | list | per track: `track_ids`, `cluster_id`, `team`, touches, passes, distance, speed, heatmap, shot map, ... |
| `clusters` | list | per figure a reviewer maps to a name: `cluster_id`, `track_ids`, `colour`, first / last seen, `thumb` (base64 JPEG) |
| `participants` | list | per track the pipeline decided about: `role` (`player`, `offfield`, `official`, `unsure`), the `reason`, and the evidence (`screen_time_s`, `off_pitch_share`, `spread_ph`, ...) |
| `keepers` | list | per keeper: saves, shots faced, claims, distributions, sweeper actions, ... |
| `events` | list | `event_id`, `type`, `timestamp_s`, `frame_index`, `track_id`, `team`, `start_m`, `end_m`, `distance_m`, `confidence`, `in_play`, `tags` |
| `touches` | list | `timestamp_s`, `track_id`, `team`, `confidence`, `observed` |
| `reconciliation` | dict or null | where the tag log and the pipeline disagree (needs a tag log) |
| `processing_s` | float | wall-clock seconds of the run |

`tests/test_quality_seam.py`, `test_teams_seam.py`, `test_keepers_seam.py`,
`test_events_seam.py`, `test_identity_seam.py` and `test_reconcile_seam.py` each
pin one block: every key the pipeline writes is read somewhere (or listed with a
reason), and every key a page reads is written.

## Firestore documents

All under `teams/{t}/matches/{m}/`. `publish_to_firestore.py` writes only these
paths (`_check_path` refuses anything else) and never creates a player report.

| document | built by | holds |
|---|---|---|
| `cvStats/summary` | `summary_payload` | `schemaVersion`, `source`, `window`, `durationS`, `calibrated`, `period`, `periodSource`, `calibrationErrorM`, `quality`, `warnings`, `trustworthy`, `teams`, `keepers`, `participants` (capped notes), `reconciliation`. Not events, touches or timing. |
| `cvStats/events` | `events_payload` | up to `MAX_EVENTS` (1500) events, trimmed to what the review tool and passing network need (`startM` drawn on 105 x 68), plus `truncated`, `droppedBelowConfidence`, `counts` |
| `cvStats/identity` | `identity_payload` | `clusters`, `tracks`, `playerByCluster` (the reviewer's mapping) |
| `cvStats/thumbs` | `thumbs_payload` | `byCluster`: the cropped pictures, apart so a coach can delete them alone |
| `playerReports/{p}` | `player_report_fields` | the `cv*` fields below, merged onto a report a human created |

The service account writes through the Admin SDK, which bypasses
`firestore.rules`, so these limits are enforced by the code. `MAX_EVENTS` matches
the rules' cap on a review's `byEvent` map (`cvReview/decisions`, 1500), so every
published event can be reviewed. On `playerReports` the rules cap the key count
rather than list the keys, because the coach's merge write carries the pipeline's
`cv` fields too.

## Player-report fields

The pipeline writes 21 `cv` fields per player (`player_report_fields`):

`cvTouches`, `cvPassesAttempted`, `cvPassesCompleted`, `cvCarries`, `cvTackles`,
`cvInterceptions`, `cvRecoveries`, `cvShots`, `cvXg`, `cvDistanceM`,
`cvTopSpeedKmh`, `cvSprintCount`, `cvAccelerations`, `cvTopAcceleration`,
`cvPositionNoiseM`, `cvMinutesTracked`, `cvTouchTimes`, `cvHeatmap`,
`cvAttackingEnd`, `cvShotMap`, `cvCalibrationErrorM`.

The browser writes the same report from the coach's match view
(`cvReportFields` in `shared/report-player.js`), adding the roster-derived
`cvMinutesOnPitch`, `cvMinutesFilmed`, `cvTrackedShare`, `cvClusterCount` and
`cvReviewed`. `CV_REPORT_KEYS` beside it is the clearing list: the 26 names
nulled when a coach un-maps a cluster, so last week's numbers cannot stay behind.

The field names are assembled from `CV_FIELD_PREFIX` in Python, so no text
search connects the two sides. `tests/test_cv_field_seam.py` does: the union of
what the two writers send is exactly the clearing list, and exactly what the
pages read. `tests/test_player_merge_parity.py` checks that the two writers
compute the same numbers. `tests/test_write_seam.py` covers the rules side.
