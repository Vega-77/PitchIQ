"""clsscore.py with its output redirected.

python -m pitchiq.balltrack.alltests.clsrun <model.pt> <outdir> <tag> [<tag> ...]
"""

import os
import sys

from .. import clsscore
from ..train_cls import load_model


out = sys.argv[2]
os.makedirs(out, exist_ok=True)
clsscore.S2 = out
model = load_model(sys.argv[1])
for tag in sys.argv[3:]:
    if os.path.exists(os.path.join(out, '%scls.npz' % tag)):
        print('have', tag, flush=True)
        continue
    clsscore.run(model, tag)
