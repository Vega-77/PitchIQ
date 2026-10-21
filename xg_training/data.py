"""StatsBomb open-data shots, downloaded once and cached raw, parsed into features.

`load_data` returns one row per shot with the columns `features.parse` makes.
The cache holds the raw shot events, not the features, so `parse` can change
and be re-run with `offline=True` without touching the network.
"""

import os
import time

import pandas as pd

from features import parse

# Relative to the working directory: run from xg_training/.
CACHE_FILE = 'xg_raw_shots_cache.pkl'

# (competition_id, season_id) pairs in StatsBomb open data.
TOURNAMENTS = [
    # --- the original five seasons ---
    (11, 90),
    (12, 27),
    (2, 27),
    (7, 235),
    (9, 281),
    # --- internationals ---
    (43, 106),
    (43, 3),
    (55, 43),
    (16, 4),
    (72, 30),
    # --- historical La Liga ---
    (11, 42),
    (11, 4),
    (11, 1),
    (11, 2),
    (11, 27),
    (11, 26),
    (11, 25),
    (11, 24),
    # --- FA Women's Super League ---
    (37, 90),
    (37, 42),
    (37, 4),
    # --- historical Champions League ---
    (16, 37),
    (16, 39),
    (16, 27),
    (16, 26),
    (16, 22),
    # --- Premier League 2003/04 ---
    (2, 44),
    # --- Indian Super League 2021/22 ---
    (1238, 108),
]


def load_data(offline=False) -> pd.DataFrame:
    """Every cached shot parsed into features, downloading missing matches first unless `offline`."""
    # Offline re-runs the whole thing from the cache and touches no network.
    if offline:
        if not os.path.exists(CACHE_FILE):
            raise SystemExit(f'No {CACHE_FILE} to work from — run without --offline once.')
        shots = pd.read_pickle(CACHE_FILE)
        print(f'Offline: {len(shots)} cached shots from {shots["match_id"].nunique()} matches.')
        print('Parsing features from raw data...')
        return pd.DataFrame([parse(r) for _, r in shots.iterrows()])

    # Imported here rather than at the top so a machine that only wants to
    # re-export from the cache does not need the StatsBomb client installed.
    from statsbombpy import sb

    print('Fetching match lists from StatsBomb (with safety delays)...')

    match_dfs = []
    for comp_id, season_id in TOURNAMENTS:
        try:
            match_dfs.append(sb.matches(competition_id=comp_id, season_id=season_id))
            time.sleep(0.5)  # the API drops requests that come too fast
        except Exception as e:
            print(f'Skipping {comp_id}/{season_id}: {e}')

    matches = pd.concat(match_dfs, ignore_index=True)
    all_match_ids = set(matches['match_id'])

    # 1. Load what we already have
    if os.path.exists(CACHE_FILE):
        print('Loading existing cache to check for missing data...')
        shots = pd.read_pickle(CACHE_FILE)
        cached_match_ids = set(shots['match_id'].unique())
    else:
        shots = pd.DataFrame()
        cached_match_ids = set()

    # 2. Find the difference
    missing_match_ids = list(all_match_ids - cached_match_ids)

    # 3. Download only what's missing
    if missing_match_ids:
        print(f'Found {len(missing_match_ids)} NEW matches to download. Skipping the rest!')
        new_shots_list = []

        for i, mid in enumerate(missing_match_ids):
            if i % 50 == 0:
                print(f'  {i}/{len(missing_match_ids)} new matches downloaded...')
            try:
                events = sb.events(match_id=mid, split=True, flatten_attrs=False)
                if 'shots' in events:
                    new_shots_list.append(events['shots'])
                time.sleep(0.5)  # Delay for the actual downloads too
            except Exception:
                pass

        if new_shots_list:
            new_shots = pd.concat(new_shots_list, ignore_index=True)
            shots = pd.concat([shots, new_shots], ignore_index=True)

        # Resave the updated master cache
        shots.to_pickle(CACHE_FILE)
        print(f'Appended new data and updated cache → {CACHE_FILE}')
    else:
        print('Cache is already 100% up to date!')

    # 4. Parse features
    print('Parsing features from raw data...')
    df = pd.DataFrame([parse(r) for _, r in shots.iterrows()])

    return df
