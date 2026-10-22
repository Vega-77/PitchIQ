# pitchiq/cli/

The match-day commands, run as `python -m pitchiq <command> [args]`
(`python -m pitchiq <command> --help` prints each one's options):

- `grab_frame`: save one frame of a video to calibrate against.
- `calibrate`: turn clicked landmarks into a calibration and report its error.
- `event_report`: run the event chain over a clip and print or `--json` it.
- `match_report`: run the whole pipeline over a clip and print the report.
- `compare_reports`: diff two report JSONs (see `baselines/`).
- `publish_report`: write a report to Firestore (`--dry-run` writes nothing).
- `demo`: run the pipeline on a scripted nine-second match (no footage needed).

Each module has `build_parser()` and `main(argv)`; adding one means adding its
name to `COMMANDS` in `__init__.py`.
