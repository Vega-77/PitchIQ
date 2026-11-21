"""A finished half's ball stages again, after only its registration changed.

    PIQ_VIDEO=<video> python -m pitchiq.balltrack.rereg <tag> <lo> <hi> [--lanes 2]

half.py from scratch would also redo detect_candidates, the classifier and the players (their chunk parts
are merged and gone, so it detects every frame again), none of which reads the registration.
Here, per piece: the new registration cut to its window (Half.registration, as build does)
and the shipped chain, then the stitch, the same commands half.py runs. The v17 ball's 3D
reads the registration too: run ball17 for the game again afterwards (move its h3d_v17 files
aside first; it skips a half whose files exist).

Until 2026-11-21 this also re-ran each piece's TAPNext bridges (reusing every unchanged track
through TN_PREV) and the Hybrid 90 ball; half.py no longer builds that ball.
"""

import argparse
import os
from concurrent.futures import ThreadPoolExecutor

from . import half


def main():
    """Command line: <tag> <lo> <hi> [--overlap] [--lanes] - per piece the new registration and
    the shipped chain, then the stitch.
    """
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('tag')
    ap.add_argument('lo', type=int)
    ap.add_argument('hi', type=int)
    ap.add_argument('--overlap', type=int, default=300)
    ap.add_argument(
        '--lanes',
        type=int,
        default=half.LANES,
        help='shipped chains at a time (at most half.LANES)',
    )
    a = ap.parse_args()
    # never more than half.py's lanes: each chain runs a 16-process scan (flight_scan)
    a.lanes = min(a.lanes, half.LANES)
    h = half.Half(a.tag, a.lo, a.hi, 0, a.overlap)
    h.say('REREG %s  %d..%d  %d pieces, %d lanes' % (a.tag, a.lo, a.hi, len(h.pieces), a.lanes))
    for p in h.pieces:
        for st in ('build', 'shipped'):
            m = os.path.join(h.dir, '%s.%s.done' % (p['ptag'], st))
            if os.path.exists(m):
                os.remove(m)

    def ship(p):
        h.registration(p)
        h.mark(p['ptag'], 'build')
        h.do('shipped', p)

    with ThreadPoolExecutor(a.lanes) as cpu:
        for f in [cpu.submit(ship, p) for p in h.pieces]:
            f.result()  # re-raises a failed stage
    h.stitch()
    h.say('REREG DONE')


if __name__ == '__main__':
    main()
