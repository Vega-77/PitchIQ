"""Paths and settings the tracker reads from the environment, with their defaults.

PIQ_VIDEO        the match video every stage reads (default: the scrimmage file
                 in Downloads)
PIQ_LEGACY_SP    a copy of the first labelling round's scratchpad (label/manifest.json,
                 clips/); only the legacy training scripts read it
PIQ_WORKERS      detector processes for one window (run.sh, drun.py); default 8.
                 Each holds ~388 MiB of VRAM for its CUDA context, so 16 pages.
PIQ_HALF_WORKERS detector processes for a whole half (half.py); default 12
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO = os.environ.get(
    'PIQ_VIDEO',
    os.path.join(
        os.path.expanduser('~'), 'Downloads', '8-26-26 Scrimmage v Gov Livingston - Tactical.mp4'
    ),
)
LEGACY_SP = os.environ.get('PIQ_LEGACY_SP', os.path.join(HERE, 'legacy_sp'))
WORKERS = int(os.environ.get('PIQ_WORKERS', '8'))
HALF_WORKERS = int(os.environ.get('PIQ_HALF_WORKERS', '12'))
