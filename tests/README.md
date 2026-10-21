# tests/

Two suites. `python -m pytest tests/ -q` runs the Python one (`pip install -e
.[test]` first); `npm test` runs the JavaScript one, which needs Node 22.15+ and,
for the Firestore rules, a JVM for the emulator. This page sorts the files;
most open with a docstring or comment saying what they hold.

## Unit tests

One pipeline module each, on inputs built in the test (synthetic tracks,
hand-written touch sequences, cameras constructed in code), so the right answer
is known: `test_<module>.py` for `pitchiq/<module>.py` (`test_ball`,
`test_touches`, `test_events`, `test_identity`, `test_calibration`, ...), plus
the CLI tests (`test_calibrate_cli`, `test_publish_report_cli`,
`test_compare_reports`), the guards on the Firestore credential
(`test_publish_to_firestore`), and the balltrack and experiments pieces that have
tests (`test_half_lanes`, `test_resumable_runs`, `test_match_video_rings`,
`test_flight_model_units`, `test_spotlab`, `test_player_lab_ahead`).
`test_pipeline_end_to_end` and `test_pipeline_stream` run `analyse_match` on a
clip made of rectangles; `test_xg_pipeline` runs a shot through to an xG number.
`data/training_bibs.json` feeds `test_possession`.

The JS unit suites (`npm run test:unit`) import `shared/report.js` and the
other pure modules directly: `players`, `season`, `review`, `shots`, `passing`,
`pitch-maps`, `match-stats`, `match-brief`, `tagged-log`, `video`, `cv-notes`,
`sample-report`, `kit`. `npm run test:pages` loads every page in a small DOM
(`dom-shim.js`, `fake-firebase.js`, `fixtures.js`): `smoke`, `sheets`.
`rules`, `flow` and `onboarding` run against the Firestore emulator.

## Seam and contract tests

Two pieces of code that must agree but never import each other: Python and the
browser, the browser and `firestore.rules`, HTML and CSS.

- Report to page: `test_cv_field_seam` (player-report field names), and
  `test_events_seam`, `test_identity_seam`, `test_keepers_seam`,
  `test_quality_seam`, `test_reconcile_seam`, `test_teams_seam` (every figure
  in that report block reaches a page, or says why not).
- Shared vocabularies: `test_event_types_seam`, `test_review_verdicts`,
  `test_pitch_dimensions`, `test_player_merge_parity`, `test_calibrate_landmarks`.
- Browser to Firestore: `test_write_seam` (every field the browser writes is
  admitted by a rule), `test_firestore_indexes` (every compound query has an
  index, and every index a query).
- Markup and styles: `test_id_seam`, `test_data_seam`, `test_css_seam`,
  `test_custom_property_seam`, `test_dialog_seam`, `test_export_seam`.
- Fixtures: `test_sample_report` and `test_sample_xg` keep the sample match the
  same shape as a real run.

## Hygiene meta-tests

These scan the whole repo, not one module. Most have no allowlist; where one
exists (`PENDING_BY_DESIGN` in `test_call_graph`, and its siblings in
`test_member_graph` and `test_browser_graph`) every entry carries its reason,
and an entry that stops being true fails the test.

| test | protects against |
|---|---|
| `test_call_graph` | a public `pitchiq/` function that is written and tested but unreachable from anything that runs (how `publish_to_firestore` sat unusable while green) |
| `test_member_graph` | a method, property or field read by nothing but tests |
| `test_browser_graph` | a browser module that ships but no page or module imports |
| `test_free_names` | a browser module using a name nothing binds: a `ReferenceError` only a person clicking would find (it shipped twice) |
| `test_unread_names` | a name a browser module declares and never reads, such as a constant the code beside it spells out by hand |
| `test_duplicate_bodies`, `test_duplicate_bodies_py` | copy-pasted functions (site JS; `pitchiq/*.py`), where a fix reaches only some of the copies |
| `test_contrast_floor` | text below WCAG's 4.5 contrast, or chart marks and other graphical ink below 3.0, measured from the stylesheets |
| `test_contrast_universe` | the contrast scan quietly looking at too little (or the wrong) CSS, so zero findings means nothing |
| `test_control_names` | a button or field a screen reader announces as nothing |
| `test_focus_visible`, `test_heading_outline`, `test_landmarks`, `test_skip_link` | keyboard focus that disappears, views with no heading, pages without regions, a missing skip link |
| `test_cap_pairs` | a limit held twice, in `firestore.rules` and in a client guard, drifting apart when one end is raised |
| `test_naming` | the checkable naming rules in `docs/conventions.md` |
| `test_version_stamp` | an asset reference without the same `?v=` cache-busting stamp as the rest |
| `test_report_parts`, `test_package_api` | `shared/report.js`'s parts importing the DOM or Firestore; `import pitchiq` pulling in torch |
| `test_requirements`, `test_detector_weights`, `test_field_commands` | requirements files disagreeing on a version; the wrong YOLO weights; a command in the field guide that no longer runs |
