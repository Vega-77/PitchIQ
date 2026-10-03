"""Test score of a ball3d file against the Air Lab truth (report only).

    python vscore2.py <ball3d npz> [<ball3d npz> ...]
"""
import sys
import numpy as np

T = np.load('airtruth.npz')
truth = dict(zip(T['abs'].tolist(), T['truth'].tolist()))
ab = np.array(sorted(truth)); tt = np.array([truth[a] for a in ab])
vis = tt != 'hidden'
# true flights = runs of air
runs = []; i = 0
while i < len(ab):
    if tt[i] == 'air':
        j = i
        while j + 1 < len(ab) and tt[j + 1] == 'air' and ab[j + 1] == ab[j] + 1:
            j += 1
        runs.append((i, j)); i = j + 1
    else:
        i += 1
for p in sys.argv[1:]:
    B = np.load(p)
    st = dict(zip(B['abs'].tolist(), B['state'].tolist()))
    pr = np.array([st.get(a, 'none') for a in ab])
    PA, TA = (pr == 'air') & vis, (tt == 'air') & vis
    tp = (PA & TA).sum()
    hit = sum(((pr[a:b + 1] == 'air').mean() >= 0.5) for a, b in runs)
    # detected air runs that touch no true air frame
    fr = 0; nd = 0; i = 0
    while i < len(ab):
        if pr[i] == 'air':
            j = i
            while j + 1 < len(ab) and pr[j + 1] == 'air':
                j += 1
            nd += 1; fr += not (tt[i:j + 1] == 'air').any(); i = j + 1
        else:
            i += 1
    P, R = tp / max(1, PA.sum()), tp / max(1, TA.sum())
    print('%-28s air frames P %.2f R %.2f F %.2f | true flights hit %d/%d | detected runs %d, false %d' % (
        p, P, R, 2 * P * R / max(P + R, 1e-9), hit, len(runs), nd, fr))
