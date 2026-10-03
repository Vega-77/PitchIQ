"""clsscore.py with its output redirected:  python clsrun.py <model.pt> <outdir> <tag> [<tag> ...]"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
sys.path.insert(0, S2)
import clsscore
from train_cls import load_model
out = sys.argv[2]; os.makedirs(out, exist_ok=True)
clsscore.S2 = out
model = load_model(sys.argv[1])
for tag in sys.argv[3:]:
    if os.path.exists(os.path.join(out, '%scls.npz' % tag)):
        print('have', tag, flush=True); continue
    clsscore.run(model, tag)
