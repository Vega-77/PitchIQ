"""One ball path: the flight chain's air frames, the smoothed roll everywhere else.

    python -m pitchiq.balltrack.pitch_registration.merge_air_and_ground <chain.npz> <smooth.npz> <out.npz>

<chain.npz> is flight_chain's <tag>_ball3d2.npz (abs, x, y, z, state); <smooth.npz> is
smooth_ball's <tag>_smooth.npz on the same frames.  Where state is 'air', x and y stay the
chain's; on every other frame they are the smoothed ground position.  z and state are the
chain's unchanged.  x, y in StatsBomb 120 x 80; z in camera heights.  Writes
abs, x, y, z, state to <out.npz> (hybrid.sh: <tag>_ball3dd.npz, the shipped 3D ball).
"""

import sys
import numpy as np

B = np.load(sys.argv[1])
M = np.load(sys.argv[2])
st = B['state']
x = B['x'].copy()
y = B['y'].copy()
g = st != 'air'
x[g] = M['x'][g]
y[g] = M['y'][g]
np.savez(sys.argv[3], abs=B['abs'], x=x, y=y, z=B['z'], state=st)
