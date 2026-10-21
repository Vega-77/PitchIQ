"""One StatsBomb shot event -> the model's features, in StatsBomb 120x80 space.

`parse` reads the shot's location, body part, type and freeze frame and returns
every feature in `FEATURES` plus the columns training needs beside them
(`shot_height`, `is_freekick`, `has_freeze_frame`, the `is_goal` label). The
geometry comes from `pitchiq/xg_model.py`, the same code the pipeline measures
its own shots with.
"""

import numpy as np

from pitchiq.xg_model import FEATURES, distance_sb, in_shot_cone_sb, shot_angle

__all__ = ['FEATURES', 'parse']


def parse(row) -> dict:
    """The features of one StatsBomb shot row, and its label."""
    shot = row['shot']
    loc = np.array(row['location'])
    freeze = shot.get('freeze_frame', [])

    keeper = None
    defenders_in_cone = 0
    defender_pressure = 0.0  # weighted: closer defenders count more

    for player in freeze:
        if not player.get('location'):
            continue
        p_loc = np.array(player['location'])
        pos = player.get('position', {}).get('name', '')

        if pos == 'Goalkeeper':
            keeper = p_loc
            continue

        if not player.get('teammate') and in_shot_cone_sb(p_loc, loc):
            defenders_in_cone += 1
            defender_pressure += 1.0 / (distance_sb(p_loc, loc) + 1e-9)

    # --- body part flags (3-way split, not 2-way) ---
    body = shot.get('body_part', {}).get('name', '')
    is_foot = 1 if body in ('Right Foot', 'Left Foot') else 0
    is_header = 1 if body == 'Head' else 0

    # --- shot end height (z-axis when available) ---
    # Not a model input: it is where the ball finished, an outcome. See README.md.
    end_loc = shot.get('end_location', [])
    shot_height = float(end_loc[2]) if len(end_loc) > 2 else np.nan

    # --- goalkeeper features ---
    if keeper is not None:
        keeper_dist = distance_sb(keeper)
        keeper_ang = shot_angle(keeper)
        keeper_off_line = 1 if keeper_dist > 3.0 else 0
    else:
        keeper_dist = 5.0
        keeper_ang = shot_angle([115.0, 40.0])
        keeper_off_line = 0

    return {
        # location
        'distance_to_goal': distance_sb(loc),
        'angle_to_goal': shot_angle(loc),
        # body part
        'is_foot': is_foot,
        'is_header': is_header,
        # context
        'under_pressure': 1 if row.get('under_pressure') else 0,
        'is_freekick': 1 if shot.get('type', {}).get('name') == 'Free Kick' else 0,
        'is_open_play': 1 if shot.get('type', {}).get('name') == 'Open Play' else 0,
        # shot quality
        'shot_height': shot_height,
        # goalkeeper
        'keeper_distance_to_goal': keeper_dist,
        'keeper_angle_coverage': keeper_ang,
        'keeper_off_line': keeper_off_line,
        # defenders
        'defenders_in_cone': defenders_in_cone,
        'defender_pressure': defender_pressure,
        # data quality flag (used for weighting, not a feature)
        'has_freeze_frame': 1 if len(freeze) > 0 else 0,
        # label
        'is_goal': 1 if shot.get('outcome', {}).get('name') == 'Goal' else 0,
    }
