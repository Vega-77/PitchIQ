"""Labels for the crop classifier that no human had to click.

Scored against Alex's 269 whole-match labels, viterbi6 at the precision end
(skip 4, e_off 0, c_leave 6, c_enter 12) names a ball on 48 frames and is
right on 46.  Run over the TRAINING clips (tr_plan.json - none of them
within 60 s of a test clip), that path is a source of ball crops, and the
continuity is the point: it carries the label through frames where the
ball scored -1 or -2 and ranked tenth, which are exactly the crops a
detector-score threshold would never have labelled.

In a frame the path commits on:
    the picked candidate                 -> ball
    any candidate > FAR px from it       -> not ball
    candidates between NEAR and FAR px   -> unused (duplicate blobs of the
                                            ball, or a foot touching it)
Frames the path does not commit on give nothing: "no pick" means not sure,
not "no ball", and a ball there would become a negative.

2 in 48 is still ~4% of positives wrong, so these carry weight W_PSEUDO
and Alex's clicks (croplab) override them.

    python pseudolab.py tr00 tr01 ...    # -> pseudo_labels.json,
                                         #    <tag>frames.json (for crops.py)
"""
import json
import os
import sys

import numpy as np

import dfree
import dtrack6 as D6

S2 = os.path.dirname(os.path.abspath(__file__))
PREC = dict(skip=4.0, kmax=4, e_off=0.0, c_leave=6.0, c_enter=12.0,
            A=2.3, ab=0.0)
NEAR, FAR = 20.0, 60.0
EVERY = 3            # crop every 3rd committed frame: neighbours are near-copies
BACKGROUND = 15      # plus every 15th frame regardless, for the classifier's
                     # own score pass on uncommitted frames
W_PSEUDO = 0.5


def main():
    tags = sys.argv[1:]
    out = []
    for tag in tags:
        frames = dfree.load(tag)
        pick = D6.viterbi6(frames, **PREC)
        com = [k for k, j in enumerate(pick) if j is not None]
        use = set(com[::EVERY])
        npos = nneg = 0
        for k in sorted(use):
            fr, j = frames[k], pick[k]
            d = np.hypot(fr['x'] - fr['x'][j], fr['y'] - fr['y'][j])
            for i in range(len(d)):
                if i == j:
                    lab = 'ball'
                    npos += 1
                elif d[i] > FAR:
                    lab = 'notball'
                    nneg += 1
                else:
                    continue
                out.append(dict(tag=tag, f=int(fr['abs']), j=i, label=lab,
                                weight=W_PSEUDO, src='viterbi6-prec'))
        want = sorted({int(frames[k]['abs']) for k in use}
                      | {int(fr['abs']) for fr in frames[::BACKGROUND]})
        json.dump(want, open(os.path.join(S2, '%sframes.json' % tag), 'w'))
        print('%s  %d frames, path commits on %d (%.0f%%), labelled %d of '
              'them: %d ball, %d not-ball; crop list %d frames'
              % (tag, len(frames), len(com), 100.0 * len(com) / len(frames),
                 len(use), npos, nneg, len(want)), flush=True)
    json.dump(out, open(os.path.join(S2, 'pseudo_labels.json'), 'w'))
    print('pseudo_labels.json: %d rows' % len(out))


if __name__ == '__main__':
    main()
