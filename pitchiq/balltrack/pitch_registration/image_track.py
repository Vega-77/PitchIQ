"""The tracker's ball in image pixels, frame by frame, for the 3D chain.

    python -m pitchiq.balltrack.pitch_registration.image_track <tag>   -> <tag>_img.npz

Reads <tag>_ballsb.npz (ball_on_pitch) for the frame list and takes each frame's pick from
render_review_video.picks_for (PIQ_P2D selects a saved 2D version).  Writes abs, ix, iy:
the pick in source-video pixels, NaN where the tracker has no ball; flight_model reads it.
Run in the work folder.  Also prints the ground speeds (StatsBomb units/s) and the fast
runs (> 30 units/s, merged across gaps of <= 6 frames) as a quick look at likely kicks.
"""

import sys
import numpy as np

from .. import render_review_video

TAG = sys.argv[1] if len(sys.argv) > 1 else 'vid1'

z = np.load(TAG + '_ballsb.npz')
a = z['abs']
H = z['H']
by = render_review_video.picks_for(TAG)
ix = np.array([by.get(f, {}).get('x', np.nan) if by.get(f, {}).get('src') else np.nan for f in a])
iy = np.array([by.get(f, {}).get('y', np.nan) if by.get(f, {}).get('src') else np.nan for f in a])
np.savez(TAG + '_img.npz', abs=a, ix=ix, iy=iy)
x, y = z['x'], z['y']
v = np.hypot(np.diff(x), np.diff(y)) * 30  # SB units / s
ok = np.isfinite(v)
print('speed pct (units/s):', np.nanpercentile(v, [50, 90, 95, 98, 99]).round(1))
fast = np.where(ok & (v > 30))[0]
runs = []
for i in fast:
    if runs and i - runs[-1][1] <= 6:
        runs[-1][1] = i
    else:
        runs.append([i, i])
print(len(runs), 'fast runs')
for s, e in runs[:40]:
    seg = slice(max(0, s - 5), e + 6)
    print(
        a[s],
        a[e],
        'len',
        e - s + 1,
        'maxv %.0f' % np.nanmax(v[s : e + 1]),
        'x %.0f..%.0f y %.0f..%.0f'
        % (np.nanmin(x[seg]), np.nanmax(x[seg]), np.nanmin(y[seg]), np.nanmax(y[seg])),
    )
