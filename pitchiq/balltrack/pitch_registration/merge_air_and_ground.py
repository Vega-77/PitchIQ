"""One ball path: the chain's flights, the smoothed roll on the ground, 'unsure' between.

Unsure is a ball that was seen but that the 3D cannot vouch for on the ground.

    python -m pitchiq.balltrack.pitch_registration.merge_air_and_ground <chain.npz> <smooth.npz> <out.npz> [<scan.npz> [<venue.npz>]]

<chain.npz> is flight_chain's <tag>_ball3d2.npz (abs, x, y, z, state); <smooth.npz> is
smooth_ball's <tag>_smooth.npz on the same frames; <scan.npz> is flight_scan's
<tag>_scan2.npz; <venue.npz> is the camera calibrated in-match (<tag>_venue.npz), missing for
the scrimmage camera, as flight_model.Track does.  Left out, both are the <tag>_ files beside
<chain.npz>.  Where state is 'air', x and
y stay the chain's; on every other frame they are the smoothed ground position.  z is the
chain's unchanged.  x, y in StatsBomb 120 x 80; z in camera heights.  Writes abs, x, y, z,
state to <out.npz> (<tag>_ball3dd.npz, the shipped 3D ball).

state: 'air' a flight that fits (the chain's), 'none' no ball, and the chain's 'ground' split
in two (session step 3.3, 2026-11-22):
  ground  the smoothed ground path agrees: the smoother used the reading (flag 1, or 3 a kick)
          and placed the ball
  unsure  seen, but no ground position stands: the smoother placed nothing (faster than
          smooth_ball's vmax, about 35 m/s; far off the lines; no reading near) or did not use
          the reading (flag 0 off the lines, 2 an outlier), or a flight_scan window over the
          frame says raised ball: an arc fits it to ARC_FIT_PX, a straight roll fits ARC_OVER_ROLL
          times worse, and the arc rises over RISE_M (a ball read flat races away from the camera
          and back).  Its x, y are kept (the smoother's, NaN where it placed nothing); readers
          draw it apart and measure nothing on the ground with it.
On the scrimmage (vid1 Air Lab, dev, Hybrid 90) 620 of the 4633 rings the chain called ground
were in the air; 81% of those become unsure, and 15% of the truly grounded ones (12% the
smoother had already refused).
"""

import os
import sys

import numpy as np

from .flight_model import R_CH0

ARC_OVER_ROLL = 4.0  # a roll's trimmed error over the arc's
ARC_FIT_PX = 2.5  # the arc's own trimmed error, px
RISE_M = 0.5  # the arc's apex above the ground, m
USED = (1, 3)  # smooth_ball flags of a reading the ground path used (3: a kick)


def camera_height_m(venue_path=None):
    """One camera height in metres: the in-match camera's (pitchiq/autocal.py) when its venue
    file is given and there, else the scrimmage camera's (flight_model.R_CH0)."""
    if venue_path and os.path.exists(venue_path):
        with np.load(venue_path) as v:
            if 'C' in v.files:
                return 9.15 * float(v['C'][2])
    return 9.15 / R_CH0


def raised(S, cols, n, chm):
    """Per frame (n of them): some flight_scan window over it fits an arc that a roll cannot.

    S, cols: flight_scan's rows and column names; chm: one camera height in metres."""
    C = {c: i for i, c in enumerate(cols)}
    with np.errstate(invalid='ignore', divide='ignore'):
        ok = (
            (S[:, C['r_trim']] >= ARC_OVER_ROLL * S[:, C['d_trim']])
            & (S[:, C['d_trim']] < ARC_FIT_PX)
            & (S[:, C['d_apex']] * chm > RISE_M)
        )
    edge = np.zeros(n + 1, int)
    for a, b in S[ok][:, [C['a'], C['b']]].astype(int):
        edge[a] += 1
        edge[b + 1] -= 1
    return np.cumsum(edge)[:n] > 0


def merge(chain, smooth, S, cols, chm):
    """(x, y, state) from the chain's and smoother's arrays (dicts or npz) and the scan."""
    st = np.asarray(chain['state']).astype('<U8')
    x = np.asarray(chain['x'], float).copy()
    y = np.asarray(chain['y'], float).copy()
    g = st != 'air'
    x[g] = smooth['x'][g]
    y[g] = smooth['y'][g]
    weak = ~np.isfinite(x) | ~np.isin(smooth['flag'], USED) | raised(S, cols, len(st), chm)
    st[(st == 'ground') & weak] = 'unsure'
    return x, y, st


def beside(chain_path, kind):
    """<tag>_<kind>.npz in the chain file's folder (its name is <tag>_ball3d...npz)."""
    d, name = os.path.split(chain_path)
    return os.path.join(d, '%s_%s.npz' % (name.split('_ball3d')[0], kind))


def main(argv):
    if len(argv) == 3:  # the older call: the scan and the camera sit beside the chain file
        argv = argv + [beside(argv[0], 'scan2'), beside(argv[0], 'venue')]
    if len(argv) not in (4, 5):
        sys.exit(__doc__)
    with np.load(argv[0]) as B, np.load(argv[1]) as M, np.load(argv[3]) as sc:
        x, y, st = merge(
            B, M, sc['S'], sc['cols'].tolist(), camera_height_m(argv[4] if len(argv) > 4 else None)
        )
        np.savez(argv[2], abs=B['abs'], x=x, y=y, z=B['z'], state=st)
    n = np.isin(st, ('ground', 'unsure')).sum()
    print(
        "merge: air %d, ground %d, unsure %d (%.1f%% of the chain's ground)"
        % (
            (st == 'air').sum(),
            (st == 'ground').sum(),
            (st == 'unsure').sum(),
            100.0 * (st == 'unsure').sum() / max(n, 1),
        )
    )


if __name__ == '__main__':
    main(sys.argv[1:])
