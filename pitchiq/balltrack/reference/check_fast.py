"""The shipping detector against its reference, on real frames of PIQ_VIDEO.

    python -m pitchiq.balltrack.reference.check_fast [n_anchors] [first_frame]

blob_candidates.blobs must return blobs_per_candidate.blobs's candidates in the
same order with the same numbers, except two that move in the last float32
ulp: v_std (a sum of squares in a different order) and th[1] (the reference
takes the window mean in float32).  temporal_features.temporal_rows must
match single_process_run.temporal_rows to 1e-5 (closed-form line fit instead
of lstsq).  Tiles are unwarped here: this checks the functions, not registration.
"""

import os
import sys

import cv2
import numpy as np

from .. import blob_candidates, candidate_features, config, temporal_features
from . import blobs_per_candidate, single_process_run


def same_blobs(fast, slow):
    """Number of mismatched (candidate, field) pairs; v_std and th to 1e-6 relative."""
    if len(fast) != len(slow):
        print('  candidate count %d vs %d' % (len(fast), len(slow)))
        return max(1, abs(len(fast) - len(slow)))
    bad = 0
    for a, b in zip(fast, slow):
        assert a.keys() == b.keys()
        for k in a:
            if k in ('v_std', 'th'):
                bad += not np.allclose(a[k], b[k], rtol=1e-6, atol=1e-6)
            else:
                bad += not np.array_equal(np.asarray(a[k]), np.asarray(b[k]))
    return bad


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    first = int(sys.argv[2]) if len(sys.argv) > 2 else single_process_run.LO
    z = np.load(os.path.join(single_process_run.S2, 'still4.npz'), allow_pickle=True)
    cmaj, cara = z['cmaj'], z['cara']
    nbuf = candidate_features.NOFF
    cap = cv2.VideoCapture(config.VIDEO)
    assert cap.isOpened(), config.VIDEO
    cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    bgrs = []
    for _ in range(nbuf + n - 1):
        ok, img = cap.read()
        assert ok
        bgrs.append(img)
    cap.release()

    bad_b = bad_t = 0
    for a in range(n):
        buf = bgrs[a : a + nbuf]
        img = buf[candidate_features.CZERO]
        fast, _ = blob_candidates.blobs(img, cmaj)
        slow, _ = blobs_per_candidate.blobs(img, cmaj)
        nb = same_blobs(fast, slow)
        _, pts = candidate_features.still_rows(fast, cmaj, cara)
        tiles = candidate_features.tiles_for(buf, [None] * nbuf)
        tf = temporal_features.temporal_rows(tiles, pts)
        ts = single_process_run.temporal_rows(tiles, pts)
        nt = int((~np.isclose(tf, ts, rtol=1e-5, atol=1e-5)).sum())
        print(
            'anchor %d: %d candidates, blob mismatches %d, temporal cells off %d / %d'
            % (first + a + candidate_features.CZERO, len(fast), nb, nt, tf.size),
            flush=True,
        )
        bad_b += nb
        bad_t += nt
    print('SAME' if bad_b == bad_t == 0 else 'DIFFERENT')
    return 0 if bad_b == bad_t == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
