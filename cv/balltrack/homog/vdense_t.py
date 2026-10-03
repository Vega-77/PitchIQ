import numpy as np, sys, vreg, vflight as V, vdrag as D
from multiprocessing import Pool
if __name__ == '__main__':
    lo, hi = int(sys.argv[1]), int(sys.argv[2])
    fr = list(range(lo, hi + 1)); n = -(-len(fr) // 6)
    with Pool(6) as p:
        res = sum(p.map(vreg.work, [(fr[i:i + n],) for i in range(0, len(fr), n)]), [])
    tr = V.Track('vid1'); V.ground_all(tr)
    a0 = tr.abs[0]; diff = []
    for ab, H, ni in res:
        i = ab - a0
        if ni < 30: continue
        H = H / H[2, 2]
        if H.dot([960, 900, 1])[2] < 0: H = -H
        M = tr.Ai.dot(H); M = M * np.sign(M.dot([960, 900, 1])[2])
        if tr.has[i]:
            q0 = tr.M[i].dot([*tr.px[i], 1]); q1 = M.dot([*tr.px[i], 1])
            diff.append(np.hypot(*(tr.to_sb((q0[:2] / q0[2])[None]) - tr.to_sb((q1[:2] / q1[2])[None]))[0]))
        tr.M[i] = M; tr.Mi[i] = np.linalg.inv(M)
    print('ground-reading shift, SB units: median %.2f max %.2f' % (np.median(diff), np.max(diff)))
    np.save('dense_%d.npy' % lo, np.array([(r[0], *r[1].ravel(), r[2]) for r in res]))
    V.ground_all(tr)
    for a, b in [(int(x) - a0, int(y) - a0) for x, y in zip(sys.argv[3::2], sys.argv[4::2])]:
        for km in (1e-4, 1.5):
            th, e = D.fit(tr, a, b, km)
            P = D.drag(th, np.array([th[4], th[4] + th[5]])); sb = tr.to_sb(P[:, :2])
            print(a, b, 'k %.2f rms %.1f med %.1f' % (th[6], np.sqrt(np.mean(e ** 2)), np.median(e)), 'take', sb[0].round(1), 'land', sb[1].round(1))
