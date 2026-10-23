"""A Clicker batch of the frames answered "can't tell", asked again with "Hidden, but it's here".

b1-b5 offered one answer for both "I can't see it and don't know where it is" and "it is behind a
player and I know where". This batch asks those frames again so the second kind gets a click
(v='hidden'). Item ids are the same as in their first batch: clickscore.answers reads batches in name
order, so the new answer replaces the old one.

A 'hidden' answer scores only the tracker (is there a ring near where the ball is?). It is never a
detector training example: the click is on whatever hides the ball, not on the ball.

    python cantbatch.py [b6]     -> PitchIQ-data/clicker/<b>/{items.json, media copied from b1..b5}
"""

import json
import os
import shutil
import sys

import numpy as np

from pitchiq.experiments.clicker import build, clickscore

NOTE = (
    "These are the frames you answered can't tell. If the ball is behind a player and you know "
    "where, press Hidden (H) and click where it is. Can't tell is for when you don't know."
)


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else 'b6'
    root = clickscore.ROOT
    A = clickscore.answers(os.path.join(root, 'db'))
    cant = sorted(k for k, a in A.items() if a['v'] == 'cant')
    rows = {}
    for b in sorted(os.listdir(root)):
        p = os.path.join(root, b, 'items.json')
        if b != name and os.path.exists(p):
            for it in json.load(open(p))['items']:
                rows[it['id']] = (b, it)
    dst = os.path.join(root, name)
    os.makedirs(dst, exist_ok=True)
    rng = np.random.default_rng(build.SEED + 6)
    out = []
    for k in [cant[i] for i in rng.permutation(len(cant))]:
        b, it = rows[k]
        for f in (it['still'], it['loop']):
            if not os.path.exists(os.path.join(dst, f)):
                shutil.copy(os.path.join(root, b, f), os.path.join(dst, f))
        out.append(dict(it, was=b))
    json.dump(
        dict(batch=name, note=NOTE, items=out),
        open(os.path.join(dst, 'items.json'), 'w'),
        indent=0,
    )
    print("%s: %d can't-tell items" % (name, len(out)))


if __name__ == '__main__':
    main()
