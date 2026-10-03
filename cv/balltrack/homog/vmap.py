"""The venue map: every keyframe placed in one reference image plane.

The ground is a plane, so any two views of it are related by a homography.
Each keyframe k gets H_k: its pixels -> the reference keyframe's pixel
plane (extended beyond the reference frame's edges).

  1. edges   match each keyframe to the next few in time; RANSAC homography,
             keep edges with >= MIN_INL inliers
  2. chain   spanning tree from the reference -> initial H_k
  3. loops   keyframes whose footprints overlap in the map but are far apart
             in time get matched too (the drift-killers)
  4. solve   least squares over all H_k (reference fixed) on the inlier
             point pairs, error measured in each keyframe's own pixels

    python vmap.py        -> vmap.npz (abs, H, ok), vmap_edges.npz, vmap.log
"""
import collections
import os
import sys
import time

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_INL = 40
NPTS = 25            # point pairs per edge in the solve
REF_ABS = 40050      # a wide midfield view


def load():
    z = np.load(os.path.join(HERE, 'vfeats.npz'))
    off = np.concatenate([[0], np.cumsum(z['n'])])
    K = []
    for i, ab in enumerate(z['abs']):
        a, b = off[i], off[i + 1]
        K.append(dict(abs=int(ab), xy=z['xy'][a:b],
                      d=z['desc'][a:b].astype(np.float32)))
    return K


FLANN = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=48))


def match(A, B):
    """Homography B-pixels -> A-pixels, and the inlier pairs (pa, pb)."""
    if len(A['d']) < 20 or len(B['d']) < 20:
        return None
    ms = [m for m, n in FLANN.knnMatch(B['d'], A['d'], k=2)
          if m.distance < 0.75 * n.distance]
    if len(ms) < MIN_INL:
        return None
    pb = B['xy'][[m.queryIdx for m in ms]]
    pa = A['xy'][[m.trainIdx for m in ms]]
    H, inl = cv2.findHomography(pb, pa, cv2.RANSAC, 4.0)
    if H is None or inl.sum() < MIN_INL:
        return None
    inl = inl.ravel().astype(bool)
    return H, pa[inl], pb[inl]


def apply(H, p):
    q = np.c_[p, np.ones(len(p))].dot(H.T)
    return q[:, :2] / q[:, 2:3]


def main():
    t0 = time.time()
    K = load()
    N = len(K)
    ref = int(np.argmin([abs(k['abs'] - REF_ABS) for k in K]))
    print('%d keyframes, reference %d (abs %d)' % (N, ref, K[ref]['abs']),
          flush=True)
    edges = {}                       # (i, j) i<j -> (H j->i, pi, pj)

    def add(i, j):
        if (i, j) in edges:
            return True
        r = match(K[i], K[j])
        if r is not None:
            edges[(i, j)] = r
        return r is not None

    # 1. temporal edges
    for i in range(N):
        for j in range(i + 1, min(N, i + 4)):
            add(i, j)
    print('temporal edges %d  (%.0f s)' % (len(edges), time.time() - t0),
          flush=True)

    def tree():
        adj = collections.defaultdict(list)
        for (i, j), (H, _, _) in edges.items():
            adj[i].append((j, H))                  # j -> i
            adj[j].append((i, np.linalg.inv(H)))   # i -> j
        Hs = {ref: np.eye(3)}
        q = collections.deque([ref])
        while q:
            i = q.popleft()
            for j, Hji in adj[i]:
                if j not in Hs:
                    Hs[j] = Hs[i].dot(Hji)
                    q.append(j)
        return Hs

    Hs = tree()
    print('connected to reference %d / %d' % (len(Hs), N), flush=True)

    # link stragglers (other half, after cuts) by brute force against a
    # sample of connected keyframes
    rng = np.random.default_rng(0)
    for rnd in range(3):
        miss = [k for k in range(N) if k not in Hs]
        if not miss:
            break
        conn = np.array(sorted(Hs))
        for k in miss[::max(1, len(miss) // 60)]:
            for c in rng.choice(conn, min(40, len(conn)), replace=False):
                i, j = min(k, c), max(k, c)
                if add(i, j):
                    break
        Hs = tree()
        print('link round %d: connected %d / %d' % (rnd, len(Hs), N),
              flush=True)

    # 3. loop edges by footprint overlap in the map
    corners = np.float32([[200, 400], [1720, 400], [1720, 1000], [200, 1000]])
    cen = {k: apply(H, corners).mean(0) for k, H in Hs.items()}
    ks = sorted(cen)
    C = np.array([cen[k] for k in ks])
    nloop = 0
    for a, k in enumerate(ks):
        d = np.hypot(*(C - C[a]).T)
        cand = [ks[b] for b in np.argsort(d)[1:12]
                if abs(ks[b] - k) > 3 and d[b] < 400]
        for c in cand[:5]:
            i, j = min(k, c), max(k, c)
            if (i, j) not in edges and add(i, j):
                nloop += 1
    Hs = tree()
    print('loop edges %d, total edges %d, connected %d  (%.0f s)' % (
        nloop, len(edges), len(Hs), time.time() - t0), flush=True)

    # 4. global solve
    idx = {k: n for n, k in enumerate(k for k in sorted(Hs) if k != ref)}

    def getH(x, k):
        if k == ref:
            return np.eye(3)
        v = x[8 * idx[k]:8 * idx[k] + 8]
        return np.append(v, 1.0).reshape(3, 3)

    x0 = np.concatenate([(Hs[k] / Hs[k][2, 2]).ravel()[:8]
                         for k in sorted(idx, key=idx.get)])
    obs = []
    for (i, j), (H, pi, pj) in edges.items():
        if i not in Hs or j not in Hs:
            continue
        s = rng.choice(len(pi), min(NPTS, len(pi)), replace=False)
        obs.append((i, j, pi[s], pj[s]))

    def resid(x):
        r = []
        for i, j, pi, pj in obs:
            # j's points -> map -> i's pixels, compare with i's points
            Hij = np.linalg.inv(getH(x, i)).dot(getH(x, j))
            r.append((apply(Hij, pj) - pi).ravel())
        return np.concatenate(r)

    nres = sum(2 * len(o[2]) for o in obs)
    S = lil_matrix((nres, len(x0)), dtype=np.int8)
    row = 0
    for i, j, pi, pj in obs:
        for k in (i, j):
            if k != ref:
                S[row:row + 2 * len(pi), 8 * idx[k]:8 * idx[k] + 8] = 1
        row += 2 * len(pi)
    r0 = resid(x0)
    sol = least_squares(resid, x0, jac_sparsity=S, loss='huber', f_scale=3.0,
                        x_scale='jac', max_nfev=60, verbose=0)
    r1 = sol.fun
    e0 = np.hypot(r0[0::2], r0[1::2])
    e1 = np.hypot(r1[0::2], r1[1::2])
    print('solve: point error px  median %.2f -> %.2f   p95 %.1f -> %.1f  '
          '(%.0f s)' % (np.median(e0), np.median(e1), np.percentile(e0, 95),
                        np.percentile(e1, 95), time.time() - t0), flush=True)

    H = np.zeros((N, 3, 3))
    ok = np.zeros(N, bool)
    for k in Hs:
        H[k] = getH(sol.x, k)
        ok[k] = True
    np.savez(os.path.join(HERE, 'vmap.npz'), abs=[k['abs'] for k in K], H=H,
             ok=ok, ref=ref)
    E = list(edges)
    np.savez(os.path.join(HERE, 'vmap_edges.npz'), e=np.array(E),
             ninl=[len(edges[e][1]) for e in E])
    print('saved vmap.npz  %d / %d keyframes placed' % (ok.sum(), N))


if __name__ == '__main__':
    main()
