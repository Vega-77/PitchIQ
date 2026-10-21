"""score_candidates.py with its output redirected.

python -m pitchiq.balltrack.alltests.clsrun <model.pt> <outdir> <tag> [<tag> ...]
"""

import os
import sys

from .. import score_candidates
from ..train_ball_classifier import load_model


out = sys.argv[2]
os.makedirs(out, exist_ok=True)
score_candidates.S2 = out
model = load_model(sys.argv[1])
for tag in sys.argv[3:]:
    if os.path.exists(os.path.join(out, '%scls.npz' % tag)):
        print('have', tag, flush=True)
        continue
    score_candidates.run(model, tag)
