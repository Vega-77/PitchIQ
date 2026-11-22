"""Ball push paths: code runs from this worktree, pass outputs stay in the main checkout."""

import os

# BP_BT: another folder of pass outputs, same file names (ball17's fit: the frozen dev pieces in pipeline/ballfit/devpass)
BT = (
    os.environ.get('BP_BT') or 'C:/Users/alexv/Desktop/Repos/PitchIQ/pitchiq/balltrack'
)  # shipped pass outputs (read-only)
DATA = 'C:/Users/alexv/Desktop/Repos/PitchIQ-data'
SCR = DATA + '/scratch-2026-10-26'  # the retrain's probes and out-of-fold scores (read-only)
OUT = DATA + '/ball-push'  # everything the push writes
PIPE = (
    DATA + '/pipeline'
)  # what the shipped pipeline reads (ball17's fit inputs): never an experiment folder
CLICKS = (
    PIPE + '/ballfit/clicks'
)  # frozen copy of the Ball Clicker's items_all.json + db (all answered)
CODE_BT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'balltrack'
)  # this checkout's balltrack code
DEV = ['nb1', 'ww1', 'ww2', 'eb1', 'eb2', 'scrimh1', 'scrimh2', 'wh2', 'wb1', 'pi2', 'co2']
HELD = ['nb2', 'pi1', 'wh1', 'co1', 'wb2']
# BP_HELD=a,b: other halves in the held-out role (a new game run by pitchiq/balltrack/ball17.py)
if os.environ.get('BP_HELD'):
    HELD = os.environ['BP_HELD'].split(',')


def point_at_data():
    """The balltrack modules read data beside their own file; in this worktree that folder is empty."""
    import importlib
    import sys

    for m in ('ball_tracker', 'ballgate', 'gap_fill', 'candidate_crops'):
        importlib.import_module('pitchiq.balltrack.' + m)
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, '__file__', None)
        if not name.startswith('pitchiq.balltrack.') or not f:
            continue
        here = os.path.dirname(os.path.abspath(f))
        if os.path.normcase(here) == os.path.normcase(os.path.abspath(BT)):
            continue
        # module constants built from its own folder (HERE, S2, HG, ...) and inline __file__ uses
        for k, v in list(vars(mod).items()):
            if isinstance(v, str) and os.path.normcase(v).startswith(os.path.normcase(here)):
                setattr(mod, k, BT + v[len(here) :].replace(os.sep, '/'))
        mod.__file__ = BT + '/' + os.path.basename(f)
