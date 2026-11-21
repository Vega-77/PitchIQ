"""Each half's camera on every frame: what the map places the players with.

    python -m pitchiq.balltrack.camera <tag> [<tag> ...]   write it for a half passed before 2026-11-21

pitch_registration/<tag>_camera.npz holds
    abs   (n,)       the half's frames, every one from its first to its last
    H     (n, 3, 3)  image pixels -> StatsBomb units (120 x 80) on that frame

The half-second registrations (`<tag>_reg.npz`) carried to every frame through the pan and
averaged over a few seconds (`pitch_registration/smooth_ball.smooth_H`). No ball goes into
it. `half.py`'s stitch writes it from the shipped chain's pieces, cut at the same seams as
the ball. The map read the same array from the Hybrid 90 ball's `h3d_hyb90/<tag>_smooth2.npz`
until that ball was dropped; rebuilt from the pieces it is byte-identical on all 14 game halves
passed by 2026-11-21. `ensure(tag)` backfills the file for a half passed before then.

`Camera(tag)` adds the venue: `A` (ground in camera heights -> StatsBomb, as
`flight_model.Track` has it), `r_ch` (the centre-circle radius in camera heights), `ref`
(the reference view -> StatsBomb), the pitch's size and the calibration's error. A half
calibrated in-match has its own `<tag>_venue.npz`; the scrimmage uses the venue map.
"""

from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np

S2 = os.path.dirname(os.path.abspath(__file__))
HOM = os.path.join(S2, 'pitch_registration')
CC_M = 9.15  # the centre circle's radius in metres (pitchiq.pitch.CENTRE_CIRCLE_RADIUS_M)
R_CH0 = 71.2 / 40.0  # flight_model.R_CH0: the scrimmage camera's centre-circle radius in heights
MAP_FIT_ERROR = dict(mean_m=0.28, max_m=2.07, p90_m=0.84)  # panning_camera.MAP_FIT_ERROR


def path(tag: str) -> str:
    return os.path.join(HOM, '%s_camera.npz' % tag)


def load(tag: str) -> tuple[np.ndarray, np.ndarray]:
    """(abs, H) for the half; FileNotFoundError names the command that writes it."""
    p = path(tag)
    if not os.path.exists(p):
        raise FileNotFoundError(
            '%s has no camera file (%s): `python -m pitchiq.balltrack.camera %s` writes it '
            "from the pass's pieces" % (tag, p, tag)
        )
    with np.load(p) as z:
        return z['abs'], z['H']


def stitch(tag: str, pieces: list[str], cuts: list[int]) -> str:
    """Write the camera file from the pieces' smoothed cameras (`<ptag>_smooth.npz`), piece k
    keeping cuts[k] <= frame < cuts[k + 1]. The path written."""
    A, H = [], []
    for ptag, a, b in zip(pieces, cuts[:-1], cuts[1:]):
        with np.load(os.path.join(HOM, '%s_smooth.npz' % ptag)) as s:
            m = (s['abs'] >= a) & (s['abs'] < b)
            A.append(s['abs'][m])
            H.append(s['H'][m])
    A, H = np.concatenate(A), np.concatenate(H)
    if not np.array_equal(A, np.arange(cuts[0], cuts[-1])):
        raise ValueError('%s: the stitched camera has gaps' % tag)
    np.savez(path(tag), abs=A, H=H)
    return path(tag)


def pieces_of(tag: str) -> tuple[list[str], list[int]]:
    """The pieces of a passed half and where its stitch cut them, from half/<tag>/."""
    d = os.path.join(S2, 'half', tag)
    ptags = sorted(
        os.path.basename(p)[: -len('.shipped.done')]
        for p in glob.glob(os.path.join(d, '%s_q*.shipped.done' % tag))
    )
    seams = json.load(open(os.path.join(d, 'seams.json')))
    if [s['left'] for s in seams] != ptags[:-1] or [s['right'] for s in seams] != ptags[1:]:
        raise ValueError('%s: half/%s/seams.json does not match its pieces' % (tag, tag))
    with np.load(os.path.join(HOM, '%s_smooth.npz' % ptags[0])) as s:
        lo = int(s['abs'][0])
    with np.load(os.path.join(HOM, '%s_smooth.npz' % ptags[-1])) as s:
        hi = int(s['abs'][-1])
    return ptags, [lo] + [int(s['cut']) for s in seams] + [hi + 1]


def backfill(tag: str) -> str:
    """The camera file for a half passed before half.py wrote one: stitched from its pieces,
    or, where they are gone, the same array from the Hybrid 90 ball's folder (then v17's,
    whose whole-half smoothing differs by under a micrometre). How it was made."""
    try:
        stitch(tag, *pieces_of(tag))
        return 'stitched from the pieces'
    except (OSError, ValueError, IndexError) as e:
        why = str(e)
    for ver in ('hyb90', 'v17'):
        old = os.path.join(S2, 'h3d_' + ver, '%s_smooth2.npz' % tag)
        if os.path.exists(old):
            with np.load(old) as z:
                np.savez(path(tag), abs=z['abs'], H=z['H'])
            return 'copied from h3d_%s (pieces: %s)' % (ver, why)
    raise FileNotFoundError('%s: no pieces and no older camera to copy (%s)' % (tag, why))


def ensure(tag: str) -> str:
    """The camera file's path, backfilled first (and said so) when the half has none yet."""
    if not os.path.exists(path(tag)):
        print('%s camera: %s -> %s' % (tag, backfill(tag), path(tag)), flush=True)
    return path(tag)


class Camera:
    """A half's camera with its venue (see the module docstring)."""

    def __init__(self, tag: str):
        self.tag = tag
        self.abs, self.H = load(tag)
        own = os.path.join(HOM, '%s_venue.npz' % tag)
        if os.path.exists(own):  # calibrated in-match (pitchiq/autocal.py, pincal.py)
            with np.load(own) as v:
                self.A = v['A']
                self.r_ch = 1.0 / float(v['C'][2]) if 'C' in v.files else R_CH0
                self.ref = v['ref']
                L, W = float(v['L']), float(v['W'])
                self.error = dict(
                    mean_m=float(v['err_mean']),
                    max_m=float(v['err_max']),
                    p90_m=float(v['err_p90']),
                )
        else:  # the venue map (the scrimmage)
            with (
                np.load(os.path.join(HOM, 'vpitch.npz')) as p,
                np.load(os.path.join(HOM, 'vtop.npz')) as t,
            ):
                self.A = p['P'].dot(t['T'])
                self.r_ch = R_CH0
                self.ref = p['P'] @ t['T'] @ t['B'] @ np.linalg.inv(t['K'])
                L, W = float(p['L']), float(p['W'])
            self.error = dict(MAP_FIT_ERROR)
        # the venue measures the pitch in centre-circle radii, the one length the laws fix
        self.length_m = round(L * CC_M, 1)
        self.width_m = round(W * CC_M, 1)

    @property
    def ch_m(self) -> float:
        """Metres in one camera height."""
        return CC_M / self.r_ch

    def at(self, frame: int) -> np.ndarray:
        """Image -> StatsBomb on one frame."""
        return self.H[int(frame) - int(self.abs[0])]


def main(argv=None) -> int:
    tags = (argv if argv is not None else sys.argv[1:]) or []
    if not tags:
        print(__doc__)
        return 2
    rc = 0
    for t in tags:
        if os.path.exists(path(t)):
            print('%s: already there (%s)' % (t, path(t)))
            continue
        try:
            print('%s: %s -> %s' % (t, backfill(t), path(t)))
        except FileNotFoundError as e:
            print(e)
            rc = 1
    return rc


if __name__ == '__main__':
    sys.exit(main())
