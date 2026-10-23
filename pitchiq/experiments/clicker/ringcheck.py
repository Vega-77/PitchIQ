"""Check that clickscore's ring is the ring the videos draw, at every answered frame.

clickscore.rings is a copy of match_video.ball cut down to the answered frames (the whole half takes
minutes). This runs the real match_video.ball over each half's answered span and compares the two at
every answered frame: the same kind ('', ground, air) and the same point to 0.01 px. It exits 1 on any
difference, so a change to match_video.ball that the copy misses shows up here, not as a silently
wrong score.

match_video draws the ring at exactly ball()'s image point (main: ring(cam, bimg[k] * SC, ...)).

    python ringcheck.py [<db dir>]    (default PitchIQ-data/clicker/db, as clickscore.py)
        -> <db dir>/../rings.json  {item id: {half, f, kind, x, y}}: the video's ring at each
           answered frame (kind '' = no ring), for the ring review page
"""

import json
import os
import sys
import time

import numpy as np

from pitchiq.experiments.clicker import clickscore


def main():
    adir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(clickscore.ROOT, 'db')
    items = {
        it['id']: it
        for it in json.load(open(os.path.join(clickscore.ROOT, 'items_all.json')))['items']
    }
    A = clickscore.answers(adir)
    from pitchiq.balltrack import match_video

    by = {}
    for k in A:
        by.setdefault(items[k]['half'], []).append((items[k]['f'], k))
    out, bad = {}, 0
    for tag in sorted(by):
        if not os.path.exists(
            os.path.join(clickscore.BT, 'evaluation', 'p2d', 'hyb90', tag + '.npz')
        ):
            print('%-8s no pass yet: skipped' % tag, flush=True)
            continue
        fs = sorted(by[tag])
        t0 = time.time()
        lo, hi = fs[0][0], fs[-1][0] + 1
        a0, img, _, _, kind = match_video.ball(tag, lo, hi)
        copy = clickscore.rings(match_video, tag, [f for f, _ in fs])
        diff = []
        for f, k in fs:
            kv, pv = kind[f - lo], img[f - lo]
            pc, kc = copy[f]
            same = kv == kc and (not kv or bool(np.allclose(np.asarray(pc, float), pv, atol=0.01)))
            if not same:
                diff.append((k, f, kv, kc))
            out[k] = dict(
                half=tag,
                f=f,
                kind=kv,
                x=round(float(pv[0]), 2) if kv else None,
                y=round(float(pv[1]), 2) if kv else None,
            )
        bad += len(diff)
        print(
            '%-8s %3d frames  %3d ringed  %s  (%.0f s)'
            % (
                tag,
                len(fs),
                sum(1 for f, _ in fs if kind[f - lo]),
                'same' if not diff else '%d DIFFER: %s' % (len(diff), diff[:5]),
                time.time() - t0,
            ),
            flush=True,
        )
    json.dump(
        out,
        open(os.path.join(os.path.dirname(os.path.abspath(adir)), 'rings.json'), 'w'),
        indent=0,
    )
    print('RINGCHECK', 'OK' if not bad else 'FAILED (%d frames differ)' % bad, flush=True)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
