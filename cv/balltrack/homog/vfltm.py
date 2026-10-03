import numpy as np, sys
sys.argv = ['x']
src = open('vfltune.py').read().split("grid = list")[0]
exec(src)
g = tuple(np.load('vid1_tuned.npz', allow_pickle=True)['gates'])
ch = run(*g)
def frames2(ch, m, sh):
    pa = np.zeros(n, bool)
    for r in ch:
        a = max(int(np.floor(t_take[r])) + sh, int(S[r, C['a']]) - m, 0)
        b = min(int(np.ceil(t_land[r])) - sh, int(S[r, C['b']]) + m, n - 1)
        pa[a:b + 1] = True
    return pa
for m in (0, 2, 4, 6, 99):
    for sh in (0, 1, 2):
        out = []
        for part in (0, 1):
            pa = frames2(ch, m, sh); mm = vis & (split == part)
            tp = (pa & Tair & mm).sum(); fp = (pa & ~Tair & mm).sum(); fn = (~pa & Tair & mm).sum()
            P = tp / (tp + fp); R = tp / (tp + fn); out.append('P %.2f R %.2f F %.3f' % (P, R, 2*P*R/(P+R)))
        print('margin %2d shrink %d  train %s | test %s' % (m, sh, out[0], out[1]))
