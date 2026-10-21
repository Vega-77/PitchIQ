# `pitchiq/report_export.py`

Notes moved out of the code in the deep clean (C-2): the history and dated
measurements behind comments that now say only what the code does.

## Moved from the code

### `build_report_json (ppda)`

Both were missing entirely until 2026-08-02, and `ppda` was therefore
None in every report this project has ever produced — not because the
footage could not support it, but because nothing handed the function a
pitch to measure the pressing zone on.
