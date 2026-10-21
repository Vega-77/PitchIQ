"""The commands a match day uses, run as `python -m pitchiq <command>`.

Each module here has a `build_parser()` and a `main(argv)`; `pitchiq/__main__.py`
picks one by name and hands it the rest of the arguments. Research tools and
labs stay in `pitchiq/experiments/` and are still run as modules of their own.
"""

COMMANDS = (
    'grab_frame',
    'calibrate',
    'event_report',
    'match_report',
    'compare_reports',
    'publish_report',
)
