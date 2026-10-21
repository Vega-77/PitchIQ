"""Score the pipeline's ball rings against the Ball Clicker answers.

These are the step-7 grid's two ball rows.

Each answered item is one frame of one half. The ring there is the one the videos draw
(match_video.ball's steps, at the answered frames only: Hybrid 90 + 3D air ball, short flashes and
ballgate-lost stretches dropped).

  ring drawn  -> 'ballring' (Ball ring is on the ball): right when the answer is a ball click
                 within TOL px of the ring's centre; a ring on a 'not in frame' frame is wrong.
  no ring     -> 'ballmiss' (No ball left without a ring): right when the answer is 'not in frame';
                 a clicked ball with no ring is a miss.
  'dead' (ball there, out of play) and 'cant' answers are not scored.

    python clickscore.py [<db dir>]    (default PitchIQ-data/clicker/db: the artifact's answer docs,
                                        saved as <db dir>/batches/<b>/answers/cNNN.json)
        -> <db dir>/../clicks.json  {tag: {ballring: [right, n], ballmiss: [right, n], wrong: [...], ...}}

When a batch is done: ArtifactData list batches/<b>/answers (out_dir = the db dir) on
https://claude.ai/artifact/86WZCjEKrvXA47AJLgEsmz, this script, then the step-7 report's
scores.py + build_page.py and a publish to https://claude.ai/artifact/JRfTwH2bYhtBJKGu22VzE9.

A half with no pass yet (Piscataway H2) is listed as waiting and scores nothing.
"""

import glob
import json
import os
import sys
from collections import defaultdict

import numpy as np

# PIQ_DATA: the data folder beside the repo, as cv/experiments/autocal_run.py has it.
DATA = os.environ.get(
    'PIQ_DATA',
    os.path.join(
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            )
        ),
        'PitchIQ-data',
    ),
)
ROOT = os.path.join(DATA, 'clicker')
BT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'balltrack')
TOL = 15.0  # px at 1920x1080: the drawn ring's radius (14) plus one


def rings(MV, tag, frames):
    """match_video.ball's image point and kind, at the given frames only (the whole half takes minutes)."""
    tr = MV.V.Track(tag)
    a0 = int(tr.abs[0])
    B = np.load(os.path.join(MV.HG, '%s_ball3dd.npz' % tag))
    P = np.load(os.path.join(MV.HERE, 'alltests', 'p2d', 'hyb90', '%s.npz' % tag))
    air = (B['state'] == 'air') & np.isfinite(B['x'])
    flash = MV.short_runs(air | ((P['src'] > 0) & np.isfinite(P['x'])))
    flash |= MV.ballgate.lost(tag, a0, (P['src'] > 0) & np.isfinite(P['x']))
    out = {}
    for f in frames:
        i = f - a0
        if flash[i]:
            out[f] = (None, '')
        elif air[i]:
            q = np.linalg.solve(tr.A, [B['x'][i], B['y'][i], 1.0])
            uv = q[:2] / q[2]
            b_, _ = tr.project(
                np.full(2, i), np.array([[uv[0], uv[1], B['z'][i]], [uv[0], uv[1], 0.0]])
            )
            out[f] = (b_, 'air')
        elif P['src'][i] > 0 and np.isfinite(P['x'][i]):
            out[f] = ((P['x'][i], P['y'][i]), 'ground')
        else:
            out[f] = (None, '')
    return out


def answers(d):
    A = {}
    for f in sorted(glob.glob(os.path.join(d, 'batches', 'b*', 'answers', 'c*.json'))):
        doc = json.load(open(f))
        A.update(doc['a'] if 'a' in doc else doc.get('data', {}).get('a', {}))
    return A


def main():
    adir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'db')
    items = {it['id']: it for it in json.load(open(os.path.join(ROOT, 'items_all.json')))['items']}
    A = answers(adir)
    by = defaultdict(list)
    for k, a in A.items():
        by[items[k]['half']].append((items[k]['f'], a, k))
    sys.path.insert(0, os.path.abspath(BT))
    import match_video as MV

    out = {}
    for tag in sorted(by):
        vals = defaultdict(int)
        for _, a, _ in by[tag]:
            vals[a['v']] += 1
        row = dict(answered=len(by[tag]), values=dict(vals))
        if not os.path.exists(os.path.join(BT, 'alltests', 'p2d', 'hyb90', tag + '.npz')):
            row['waiting'] = 'no pass yet'
            out[tag] = row
            print(
                '%-8s %3d answered  %s  (no pass yet: not scored)' % (tag, len(by[tag]), dict(vals))
            )
            continue
        at = rings(MV, tag, [f for f, _, _ in by[tag]])
        ring, miss, wrong = [0, 0], [0, 0], []
        for f, a, k in sorted(by[tag]):
            if a['v'] not in ('ball', 'out'):
                continue
            p, kind = at[f]
            if kind:
                ring[1] += 1
                d = float(np.hypot(p[0] - a['x'], p[1] - a['y'])) if a['v'] == 'ball' else None
                if d is not None and d <= TOL:
                    ring[0] += 1
                else:
                    wrong.append(
                        dict(
                            id=k,
                            f=f,
                            why='ring %s, %s'
                            % (kind, 'not in frame' if d is None else '%.0f px off' % d),
                        )
                    )
            else:
                miss[1] += 1
                if a['v'] == 'out':
                    miss[0] += 1
                else:
                    wrong.append(dict(id=k, f=f, why='no ring on a visible ball'))
        row.update(ballring=ring, ballmiss=miss, wrong=wrong)
        out[tag] = row
        print(
            '%-8s %3d answered  ring on ball %d/%d  no ball left %d/%d  %s'
            % (tag, len(by[tag]), ring[0], ring[1], miss[0], miss[1], dict(vals))
        )
    json.dump(
        out,
        open(os.path.join(os.path.dirname(os.path.abspath(adir)), 'clicks.json'), 'w'),
        indent=1,
    )


if __name__ == '__main__':
    main()
