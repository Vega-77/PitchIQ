"""rfast.Registrar with the surviving half of its matches moved to the GPU.

rfast removed the redundant half of bf.match by caching: OFFS is symmetric,
so every unordered pair is asked for twice over a sequential run and the
second ask is served from store.  What is left is the other half - about ten
2000x2000 crossCheck Hamming matches per anchor - and on a machine running
twenty of these processes at once that half is the single largest block of
CPU in the frame.

gmatch2 does those ten on the card in one batch.  The batching is possible
because of transforms' own shape: every direct fit is AGAINST THE CENTRE, so
all ten share one train side and stack into a single matmul.  The chain
fallbacks (k against k-1) do not share it, but they only run when a direct
fit failed, so they stay on the CPU one at a time.

Measured under nineteen single-threaded CPU burners, which is the operating
point dpar4 runs at, one anchor's twenty pairs cost:

    bf.match x20      237.0 ms
    match_many         26.2 ms      9.05x

The idle-machine figure is 1.5x, not 9x, and it is the misleading one: with
no other work the card drops to 210 MHz between calls and every batch pays
the ramp.  Under load it sits at 2760 MHz and the GPU number gets BETTER
while the CPU number gets worse.

Exactness is gmatch2's, unchanged - 120 / 120 pairs identical to bf.match as
a set and in list order - and the whole point of routing through _pre rather
than replacing match() is that everything downstream of the index arrays is
still rfast's code running on rfast's data.

    reg = Registrar(bf, gmatch2.Matcher())     # matcher=None -> plain rfast
    mats, how = reg.transforms(kds, fids, CZERO)
"""
import cv2
import numpy as np

import dense as D


class Registrar:
    """rfast.Registrar, plus an optional batched matcher for the direct fits."""

    def __init__(self, bf, matcher=None):
        self.bf = bf
        self.M = matcher
        self._mm = {}       # (src, dst) -> (queryIdx, trainIdx), bf.match order
        self._pt = {}       # frame id -> (n, 2) float32 keypoint coordinates
        self._pre = {}      # (src, dst) -> the same, from the batched matcher
        self.hits = 0
        self.misses = 0
        self.batched = 0

    # -- housekeeping ----------------------------------------------------
    def evict(self, oldest):
        """Forget everything that can no longer be asked for."""
        for k in [k for k in self._mm if k[0] < oldest or k[1] < oldest]:
            del self._mm[k]
        for k in [k for k in self._pt if k < oldest]:
            del self._pt[k]
        for k in [k for k in self._pre if k[0] < oldest or k[1] < oldest]:
            del self._pre[k]
        if self.M is not None:
            self.M.evict(oldest)

    def coords(self, fid, kp):
        a = self._pt.get(fid)
        if a is None:
            a = np.float32([k.pt for k in kp]).reshape(-1, 2)
            self._pt[fid] = a
        return a

    # -- the matching itself ---------------------------------------------
    def match(self, fa, da, fb, db):
        """Correspondences fa -> fb as index arrays, in bf.match's own order."""
        got = self._mm.pop((fb, fa), None)
        if got is not None:
            q, t = got
            self.hits += 1
            # Reverse the direction: the pairs are the same, but the query
            # side is now the old train side, so the list has to be put back
            # into ascending query order - bf.match's order.
            o = np.argsort(t, kind='stable')
            return t[o], q[o]

        self.misses += 1
        got = self._pre.pop((fa, fb), None)
        if got is not None:
            q, t = got
            self.batched += 1
        else:
            mm = self.bf.match(da, db)
            n = len(mm)
            q = np.fromiter((x.queryIdx for x in mm), np.int32, n)
            t = np.fromiter((x.trainIdx for x in mm), np.int32, n)
        # Only a pair whose far end is still in the future can be asked for
        # again; the other direction of a backward offset was consumed when
        # that frame was the anchor.
        if fa > fb:
            self._mm[(fa, fb)] = (q, t)
        return q, t

    def fit_pair(self, kda, fa, kdb, fb):
        """dense.fit_pair, same matrix, without re-matching or re-listing."""
        ka, da = kda
        kb, db = kdb
        if da is None or db is None \
                or len(ka) < D.MINMATCH or len(kb) < D.MINMATCH:
            return None
        q, t = self.match(fa, da, fb, db)
        if len(q) < D.MINMATCH:
            return None
        p = self.coords(fa, ka)[q].reshape(-1, 1, 2)
        d = self.coords(fb, kb)[t].reshape(-1, 1, 2)
        M, inl = cv2.estimateAffinePartial2D(p, d, method=cv2.RANSAC,
                                             ransacReprojThreshold=3.0)
        if M is None or inl is None or inl.sum() < D.MININL:
            return None
        return M

    # -- the batch -------------------------------------------------------
    def prefetch(self, kds, fids, c):
        """Match every direct fit that the cache will not serve, in one call.

        The guards mirror fit_pair's exactly, so a pair skipped here is a
        pair fit_pair would have refused anyway; anything missed just falls
        through to bf.match, which is slower but never different.
        """
        if self.M is None:
            return
        kb, db = kds[c]
        if db is None or len(kb) < D.MINMATCH:
            return
        fb = fids[c]
        need = []
        for k in range(len(kds)):
            if k == c:
                continue
            ka, da = kds[k]
            if da is None or len(ka) < D.MINMATCH:
                continue
            fa = fids[k]
            if (fb, fa) in self._mm:        # the cache has this one
                continue
            need.append((fa, da))
        if not need:
            return
        for (fa, _), qt in zip(need, self.M.match_many(need, fb, db)):
            self._pre[(fa, fb)] = qt

    # -- the drop-in ------------------------------------------------------
    def transforms(self, kds, fids, c):
        """dense.transforms, with the buffer's absolute frame ids alongside."""
        self.prefetch(kds, fids, c)
        n = len(kds)
        mats = [None] * n
        how = [0] * n
        mats[c] = np.eye(3)
        how[c] = 2
        for k in range(n):
            if k == c:
                continue
            M = self.fit_pair(kds[k], fids[k], kds[c], fids[c])
            if M is not None:
                mats[k] = D.homog(M)
                how[k] = 2

        for k in range(c + 1, n):
            if mats[k] is None and mats[k - 1] is not None:
                M = self.fit_pair(kds[k], fids[k], kds[k - 1], fids[k - 1])
                if M is not None:
                    mats[k] = mats[k - 1].dot(D.homog(M))
                    how[k] = 1
        for k in range(c - 1, -1, -1):
            if mats[k] is None and mats[k + 1] is not None:
                M = self.fit_pair(kds[k], fids[k], kds[k + 1], fids[k + 1])
                if M is not None:
                    mats[k] = mats[k + 1].dot(D.homog(M))
                    how[k] = 1
        return mats, how
