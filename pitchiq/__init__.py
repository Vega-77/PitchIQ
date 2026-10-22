"""Match statistics from a single sideline video of a youth football game.

The entry point is `analyse_match(video, calibration)`, which detects and tracks
players and the ball, maps them onto the pitch, and returns a `MatchReport`;
`report_export.build_report_json` turns that into the JSON the website reads.
A fixed camera is placed on the pitch by a `Calibration` (pixels -> metres on a
`Pitch`); a panning one by a `PannedCamera`, which reads the per-frame
registration and ball the tracker in `pitchiq/balltrack/` leaves behind.

The commands a match day uses are in `pitchiq/cli/` (`python -m pitchiq <command>`);
research tools and labs are in `pitchiq/experiments/`.

`import pitchiq` stays light on purpose: the detector imports ultralytics, and
with it torch, only when one is built (see `detector.py`).
"""

from importlib.metadata import PackageNotFoundError, version

from .calibration import Calibration
from .detector import Detection, PersonBallDetector
from .panning_camera import PannedCamera
from .pipeline import AnalysisOptions, MatchContext, MatchReport, analyse_match
from .pitch import Pitch
from .video_io import SampledFrame, sample_frames, video_info

try:
    __version__ = version('pitchiq')
except PackageNotFoundError:  # a source tree that was never pip-installed
    __version__ = '0+unknown'

__all__ = [
    'analyse_match',
    'AnalysisOptions',
    'MatchContext',
    'MatchReport',
    'Calibration',
    'Pitch',
    'PannedCamera',
    'Detection',
    'PersonBallDetector',
    'SampledFrame',
    'sample_frames',
    'video_info',
]
