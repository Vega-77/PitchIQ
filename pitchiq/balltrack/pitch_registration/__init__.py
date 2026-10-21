"""Pitch registration (venue map, per-frame homographies) and the 3D flight chain.

The scripts here read and write their files in one work folder, `WORK`: this
folder, unless PIQ_HOMOG names another. evaluation/mk3d.py points it at
h3d_<ver>/ to run the 3D chain on one 2D version without touching the shipped
outputs here; the review videos set it to h3d_hyb90/ before importing flight_model.
"""

import os

WORK = os.environ.get('PIQ_HOMOG') or os.path.dirname(os.path.abspath(__file__))
