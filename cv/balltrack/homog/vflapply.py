"""Write per-frame ball 3D from the tuned flights (vfltune -> <tag>_tuned.npz)."""
import os
import sys
import numpy as np
import vflight as V
import vdrag as D
import vflscan as S1

tag = sys.argv[1] if len(sys.argv) > 1 else 'vid1'
HERE = V.HERE
Z = np.load(os.path.join(HERE, '%s_scan2.npz' % tag))
S, C = Z['S'], {c: i for i, c in enumerate(Z['cols'].tolist())}
S = S[np.isfinite(S[:, C['d_rms']])]           # same filtering as vfltune
ch = np.load(os.path.join(HERE, '%s_tuned.npz' % tag), allow_pickle=True)['chosen']
tr = V.Track(tag); V.ground_all(tr)
fl = []
for r in ch:
    th = S[r, [C[c] for c in 'u0 v0 vu vv tau Tf k'.split()]]
    a = int(S[r, C['a']])
    P = D.drag(th, np.array([th[4], th[4] + th[5]]))
    fl.append(dict(th=th, a=a, b=int(S[r, C['b']]), rms=float(S[r, C['d_trim']]),
                   take=P[0, :2], land=P[1, :2], t_take=a + th[4] * V.FPS,
                   t_land=a + (th[4] + th[5]) * V.FPS, apex=float(S[r, C['d_apex']])))
print('%d flights' % len(fl))
S1.per_frame(tr, tag, fl)
