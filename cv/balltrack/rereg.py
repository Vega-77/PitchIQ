"""A finished half's ball stages again, after only its registration changed.

    PIQ_VIDEO=<video> python rereg.py <tag> <lo> <hi> [--lanes 3] [--prev DIR]

half.py from scratch would also redo drun, the classifier and the players (their chunk parts
are merged and gone, so it detects every frame again), none of which reads the registration.
Here, per piece: the new registration cut to its window (Half.registration, as build does),
the shipped chain, TAPNext bridges, the hybrid, then the stitch, the same commands half.py
runs. Two of them skip work when their output exists (tn_engine bridges, mk3d's chain), so
those outputs are cleared first. The old bridges and the picks they were seeded from go to
--prev and TAPNext reuses every track whose gap, direction and seed are unchanged (TN_PREV):
it is deterministic (a fresh run reproduced 10-13's eb2_q027 bridges byte for byte), so only
the gaps the new air flags open are tracked again.
"""
import argparse
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

import half as H

DATA = os.path.join(os.path.dirname(H.REPO), 'PitchIQ-data', 'tnprev')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('tag')
    ap.add_argument('lo', type=int)
    ap.add_argument('hi', type=int)
    ap.add_argument('--overlap', type=int, default=300)
    ap.add_argument('--lanes', type=int, default=H.LANES, help='CPU jobs (shipped chain, hybrid) at a time (at most half.LANES)')
    ap.add_argument('--prev', help='where the old bridges go (default PitchIQ-data/tnprev/<tag>)')
    a = ap.parse_args()
    # never more than half.py's lanes: each hybrid runs a 16-process scan, and at 3 lanes (ww1, ww2
    # 2026-10-15) the CPU jobs starved TAPNext (one piece's bridges took 8-10 min, then died silently)
    a.lanes = min(a.lanes, H.LANES)
    h = H.Half(a.tag, a.lo, a.hi, 0, a.overlap)
    prev = a.prev or os.path.join(DATA, a.tag)
    os.makedirs(prev, exist_ok=True)
    tn = os.path.join(H.AT, 'tn')
    h.say('REREG %s  %d..%d  %d pieces, %d lanes, old bridges -> %s' % (a.tag, a.lo, a.hi, len(h.pieces), a.lanes, prev))
    for p in h.pieces:
        P = p['ptag']
        for st in ('build', 'shipped', 'bridges', 'hybrid'):
            m = os.path.join(h.dir, '%s.%s.done' % (P, st))
            if os.path.exists(m):
                os.remove(m)
        b = os.path.join(tn, 'bridges_%s.npy' % P)
        if os.path.exists(b):
            if not os.path.exists(os.path.join(prev, 'bridges_%s.npy' % P)):    # a restarted rerun keeps the first old copy
                shutil.copy2(os.path.join(H.AT, 'p2d', 'cls_B', '%s.npz' % P), os.path.join(prev, 'cls_B_%s.npz' % P))
                shutil.move(b, os.path.join(prev, 'bridges_%s.npy' % P))
            else:
                os.remove(b)
        d3 = os.path.join(H.H3D, '%s_ball3dd.npz' % P)
        if os.path.exists(d3):
            os.remove(d3)
    h.env['TN_PREV'] = prev

    def ship(p):
        h.registration(p); h.mark(p['ptag'], 'build')
        h.do('shipped', p)

    with ThreadPoolExecutor(a.lanes) as cpu:
        shipped = {p['ptag']: cpu.submit(ship, p) for p in h.pieces}
        hybrids, err = [], []

        def gpu():
            try:
                for p in h.pieces:
                    shipped[p['ptag']].result()
                    h.do('bridges', p)
                    hybrids.append(cpu.submit(h.do, 'hybrid', p))
            except BaseException as e:  # noqa: BLE001 - re-raised below, after the pool drains
                err.append(e)
        t = threading.Thread(target=gpu)
        t.start(); t.join()
        for f in list(shipped.values()) + hybrids:
            f.result()                  # re-raises a failed stage
        if err:
            raise err[0]
    h.stitch()
    h.say('REREG DONE')


if __name__ == '__main__':
    main()
