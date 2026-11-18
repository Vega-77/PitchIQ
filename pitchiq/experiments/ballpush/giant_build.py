"""Pieces of a half that was re-detected in this worktree (a changed blob finder, e.g. PIQ_GIANT=50000).

detect_candidates writes its chunk parts beside its own file, so a re-detection run from this worktree lands in
this worktree's pitchiq/balltrack (git-ignored), never in the main checkout.  This cuts those chunks into the
same pieces the shipped pass made (same names, same windows, read from the shipped pieces), and copies each
piece's people boxes.  The experiment tools then read this folder instead of the shipped one through BP_BT.

    python giant_build.py <tag>        (after  python -m pitchiq.balltrack.detect_candidates <lo> <hi> <procs> <tag>)
    then, from pitchiq/balltrack:  python -m pitchiq.balltrack.score_candidates cls_C.pt <piece> ...
"""
import glob
import os
import shutil
import sys

import numpy as np

SRC = 'C:/Users/alexv/Desktop/Repos/PitchIQ/pitchiq/balltrack'  # shipped pass outputs (read-only)


def main():
    from pitchiq.balltrack import detect_candidates as dc

    tag = sys.argv[1]
    dst = dc.S2
    assert os.path.normcase(os.path.abspath(dst)) != os.path.normcase(os.path.abspath(SRC)), 'run from the worktree'
    wins = []
    for p in sorted(glob.glob(os.path.join(SRC, '%s_q[0-9][0-9][0-9].npz' % tag))):
        with np.load(p, allow_pickle=True) as z:
            wins.append((os.path.basename(p)[:-4], int(z['lo']), int(z['hi']), len(z['f'])))
    lo, hi = wins[0][1], wins[-1][2]
    chunks = dc.chunk_edges(lo, hi)
    for P, a, b, n0 in wins:
        res = []
        for clo, chi in chunks:
            if chi < a or clo > b:
                continue
            r = dc.load_chunk(tag, clo // dc.CHUNK, clo, chi)
            assert r is not None, 'chunk %d..%d of %s is not finished' % (clo, chi, tag)
            res.extend(r)
        dense, fr, nc, step, nfail = dc.merge(P, a, b, res)
        shutil.copy2(os.path.join(SRC, '%sppl.npz' % P), os.path.join(dst, '%sppl.npz' % P))
        with np.load(dense) as z:
            n1 = len(z['f'])
        print('%s  %d..%d  candidates %d (shipped %d)  blobs per frame median %d  pan fails %d' % (
            P, a, b, n1, n0, int(np.median(nc)), nfail), flush=True)
    if not os.path.exists(os.path.join(dst, 'cls_C.pt')):
        shutil.copy2(os.path.join(SRC, 'cls_C.pt'), os.path.join(dst, 'cls_C.pt'))
    print('GIANT BUILD DONE', tag, flush=True)


if __name__ == '__main__':
    main()
