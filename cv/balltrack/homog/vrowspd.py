"""Cache: for every scan2 row, the ground speed (SB/s) a slowing roll would need
to explain its pixels, and that roll's trimmed px error.  python vrowspd.py [tag] -> <tag>_rowspd.npy"""
import os, sys, numpy as np, vflight as V, vroll as R
tag = sys.argv[1] if len(sys.argv) > 1 else 'vid1'
tr = V.Track(tag); V.ground_all(tr)
Z = np.load('%s_scan2.npz' % tag); S, C = Z['S'], {c: i for i, c in enumerate(Z['cols'].tolist())}
out = np.full((len(S), 2), np.nan)
for r in range(len(S)):
    if not np.isfinite(S[r, C['d_rms']]):
        continue
    a, b = int(S[r, C['a']]), int(S[r, C['b']])
    e, th = R.droll(tr, a, b)
    if th is None:
        continue
    uv0, v, k = th; Tt = (b - a) / V.FPS
    E = Tt if k == 0 else (1 - np.exp(-k * Tt)) / k
    p = tr.to_sb(np.array([uv0, uv0 + v * E]))
    out[r] = (np.hypot(*(p[1] - p[0])) / Tt, e)
np.save('%s_rowspd.npy' % tag, out)
print('done', np.isfinite(out[:, 0]).sum())
