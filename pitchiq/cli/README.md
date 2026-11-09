# pitchiq/cli/

The match-day commands, run as `python -m pitchiq <command> [args]`
(`python -m pitchiq <command> --help` prints each one's options):

- `grab_frame`: save one frame of a video to calibrate against.
- `calibrate`: turn clicked landmarks into a calibration and report its error.
- `event_report`: run the event chain over a clip and print or `--json` it.
- `match_report`: run the whole pipeline over a clip and print the report.
- `compare_reports`: diff two report JSONs (see `baselines/`).
- `publish_report`: write a report to Firestore (`--dry-run` writes nothing).
- `map_report`: a report JSON from a match's top-down maps (both halves give
  one match report): team shape and where each team stood (`--ours
  lighter|darker` says which side is the coach's).
- `demo`: run the pipeline on a scripted nine-second match (no footage needed).
- `process_match`: a match video to statistics on the site, every step in
  one command (about 10 hours a game; rerun the same command to resume):

  ```
  python -m pitchiq process_match --list-matches
  python -m pitchiq process_match <video> --game xx --ours lighter --plan
  python -m pitchiq process_match <video> --game xx --ours lighter --team T --match M
  ```

  `--ours` names our shirts (lighter or darker). `--h1`/`--h2` give the halves
  on the video clock (otherwise they are guessed to the minute). `--lines white`
  is for white-lined fields. `--tag-log` adds the coach page's log and redoes
  only the stats and the publish. Publishing needs `PITCHIQ_SA_KEY`.

  The coach pins the pitch for every game. The run puts each half's
  panorama on the match, and the match page links to the calibrate page to
  pin it. The run waits for the pins (`--no-wait` stops instead; rerun once
  they are in). If the camera did not move at half time, one set of pins does
  both halves. `--no-pins` calibrates without pins (autocal), for a field it
  can read alone.

Each module has `build_parser()` and `main(argv)`; adding one means adding its
name to `COMMANDS` in `__init__.py`.
