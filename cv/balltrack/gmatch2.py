"""One matmul per anchor instead of twenty bf.match calls.

gmatch.py showed a single 2000x2000 crossCheck Hamming match reproduces
bf.match exactly on the GPU.  It matched one pair at a time, which wastes
the card: a 2000x256x2000 matmul is far too small to fill an RTX 4060, so
almost all of the measured time was launch overhead and the PCIe round trip,
not arithmetic.

The anchor's own shape fixes that.  dense.transforms fits every buffer frame
AGAINST THE CENTRE, so all twenty direct fits share one train side:

    fit_pair(kds[k], fids[k], kds[c], fids[c])      for every k != c

Stack the query descriptor sets and it is one (K*2000, 256) by (256, 2000)
matmul instead of K of them, with the reductions batched alongside: each
pair's crossCheck is independent, so reshaping to (K, na, nb) makes both
argmins one call each.

How large K should be is a measurement, not a principle, and the
measurement came out against stacking.  gvram timed 14 anchors at each
chunk size, twice:

    chunk 1   17.8 ms/anchor    168 MiB
    chunk 2   21.9 ms/anchor    260 MiB
    chunk 6   21.8 ms/anchor    552 MiB

Stacking LOSES about 20%.  The reason is the 4060's 24 MB L2: at chunk 1
the 2000x2000 int32 distance block is 16 MB and stays resident while both
argmin passes read it, and at chunk 2 it is 32 MB and does not.  That
matches what the kernel work already found - this is bandwidth-bound, not
launch-bound, so removing launches was never going to be the lever.

The batching machinery is kept because it is what makes CHUNK a dial at
all, and because memory is the harder constraint: twenty workers share one
8 GB card, and chunk 6 wanted 11 GB of it.

Three things carry over from gmatch.py unchanged, because they are what
make the result bf.match's result and not merely a similar one:

  * hamming(a, b) = popcount(a) + popcount(b) - 2 * (a . b), with the dots
    from cuBLAS.  fp16 inputs accumulate into fp32 and the true value never
    exceeds 256, so it comes back exact.
  * OpenCV's tie-break is LOWEST index, because batchDistance only replaces
    on a strict <.  torch.argmin promises nothing about ties, so the tie is
    removed rather than trusted: argmin over dist * 2048 + index, a total
    order whose minimum is the first minimum of dist.
  * bf.match returns ascending queryIdx, which a mask over the query axis
    produces for free.

Ragged query sets are handled by padding to the chunk's widest set and
giving the pad rows a sentinel distance.  The sentinel has to be large
enough never to win an argmin and small enough that sentinel * 2048 stays
inside int32; 30000 is both, with three orders of magnitude to spare on
each side.

    python gmatch2.py [n_anchors] [warm_s]
"""
import os
import sys
import time

import cv2
import numpy as np
import torch

import dense as D

S2 = os.path.dirname(os.path.abspath(__file__))
DEV = 'cuda' if torch.cuda.is_available() else 'cpu'
PAD = 30000          # sentinel distance for padded query rows
CHUNK = 1            # query sets per matmul; see the docstring - 1 wins

_BITS = torch.tensor(
    np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1),
    dtype=torch.float16, device=DEV)


class Matcher:
    """Batched crossCheck Hamming matching, with the bit expansion cached.

    A frame's descriptors are unpacked to bits once and kept, because a
    frame sits in the sliding buffer for 21 anchors and would otherwise be
    unpacked 21 times.  evict() mirrors rfast.Registrar.evict.
    """

    def __init__(self, chunk=CHUNK):
        self.chunk = chunk
        self._bits = {}
        self._pop = {}
        self.calls = 0
        self.pairs = 0

    def evict(self, oldest):
        for k in [k for k in self._bits if k < oldest]:
            del self._bits[k]
            del self._pop[k]

    def bits(self, fid, desc):
        t = self._bits.get(fid)
        if t is None:
            d = torch.from_numpy(np.ascontiguousarray(desc)).to(DEV).long()
            t = _BITS[d].reshape(d.shape[0], -1)
            self._bits[fid] = t
            self._pop[fid] = t.sum(1)
        return t, self._pop[fid]

    def match_many(self, queries, fb, db):
        """[(fa, da), ...] all matched against (fb, db).

        Returns a list of (queryIdx, trainIdx) int32 arrays in bf.match's
        own order, one per entry of `queries`, same order as given.
        """
        if not queries:
            return []
        b, pb = self.bits(fb, db)
        nb = b.shape[0]
        bt = b.T.contiguous()
        out = []
        for i in range(0, len(queries), self.chunk):
            out.extend(self._chunk(queries[i:i + self.chunk], bt, pb, nb))
        self.calls += 1
        self.pairs += len(queries)
        return out

    def _chunk(self, qs, bt, pb, nb):
        parts, pops, ns = [], [], []
        for fa, da in qs:
            a, pa = self.bits(fa, da)
            parts.append(a)
            pops.append(pa)
            ns.append(a.shape[0])
        K = len(qs)
        na = max(ns)

        if min(ns) == na:
            A = torch.cat(parts, 0)
            PA = torch.cat(pops, 0)
        else:
            A = torch.zeros((K * na, parts[0].shape[1]),
                            dtype=torch.float16, device=DEV)
            PA = torch.full((K * na,), float(PAD),
                            dtype=torch.float16, device=DEV)
            for k, (p, q) in enumerate(zip(parts, pops)):
                A[k * na:k * na + ns[k]] = p
                PA[k * na:k * na + ns[k]] = q

        # hamming(i, j) = popcount(a_i) + popcount(b_j) - 2 * a_i . b_j
        dist = (PA[:, None] + pb[None, :] - 2.0 * (A @ bt)).to(torch.int32)
        if min(ns) != na:
            # A padded row's popcount was PAD, but the dot term perturbs it;
            # overwrite outright so the sentinel is exact.
            m = torch.ones((K, na), dtype=torch.bool, device=DEV)
            for k in range(K):
                m[k, :ns[k]] = False
            dist = dist.reshape(K, na, nb)
            dist[m] = PAD
        else:
            dist = dist.reshape(K, na, nb)

        jr = torch.arange(nb, device=DEV, dtype=torch.int32)
        ir = torch.arange(na, device=DEV, dtype=torch.int32)
        ja = torch.argmin(dist * 2048 + jr[None, None, :], dim=2)      # K, na
        ib = torch.argmin(dist * 2048 + ir[None, :, None], dim=1)      # K, nb

        back = torch.gather(ib, 1, ja)                                 # K, na
        keep = back == ir[None, :].long()
        # One transfer each, not two per pair.  The per-pair split is the
        # same work either way, but 2 * chunk small copies cost more in
        # launch and sync than the two (K, na) blocks they carry.
        ja_h = ja.to(torch.int32).cpu().numpy()
        keep_h = keep.cpu().numpy()
        res = []
        for k in range(K):
            qi = np.nonzero(keep_h[k, :ns[k]])[0].astype(np.int32)
            res.append((qi, ja_h[k, :ns[k]][qi]))
        return res


def warm(seconds):
    a = np.random.rand(700, 700)
    t0 = time.time()
    while time.time() - t0 < seconds:
        a @ a


def main():
    n_anchor = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    warm_s = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0
    assert DEV == 'cuda', 'no CUDA, nothing to measure'
    print('torch %s on %s' % (torch.__version__, torch.cuda.get_device_name(0)))

    nbuf = len(D.OFFS)
    cap = cv2.VideoCapture(D.VIDEO)
    assert cap.isOpened()
    cap.set(cv2.CAP_PROP_POS_FRAMES, D.LO)
    orb = cv2.ORB_create(2000)   # dense.py:283
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    need = nbuf + n_anchor - 1
    descs = []
    print('detecting ORB on %d frames...' % need, flush=True)
    for _ in range(need):
        ok, img = cap.read()
        assert ok
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, d = orb.detectAndCompute(g, None)
        descs.append(d)
    cap.release()
    print('  descriptor counts %d..%d' % (min(len(d) for d in descs),
                                          max(len(d) for d in descs)))

    M = Matcher()
    M.match_many([(0, descs[0])], 1, descs[1])     # burn in the context
    torch.cuda.synchronize()
    M._bits.clear()
    M._pop.clear()

    print('warming CPU %.0f s...' % warm_s, flush=True)
    warm(warm_s)

    same_set = same_order = 0
    npair = 0
    t_cpu = t_gpu = 0.0
    for a in range(n_anchor):
        c = a + D.CZERO
        qs = [(a + k, descs[a + k]) for k in range(nbuf) if k != D.CZERO]

        t = time.time()
        cpu = []
        for fa, da in qs:
            mm = bf.match(da, descs[c])
            n = len(mm)
            cpu.append((np.fromiter((x.queryIdx for x in mm), np.int32, n),
                        np.fromiter((x.trainIdx for x in mm), np.int32, n)))
        t_cpu += time.time() - t

        t = time.time()
        gpu = M.match_many(qs, c, descs[c])
        torch.cuda.synchronize()
        t_gpu += time.time() - t

        for (cq, ct), (gq, gt) in zip(cpu, gpu):
            npair += 1
            same_set += int(set(zip(cq.tolist(), ct.tolist()))
                            == set(zip(gq.tolist(), gt.tolist())))
            same_order += int(len(cq) == len(gq) and np.array_equal(cq, gq)
                              and np.array_equal(ct, gt))
        M.evict(a + 1)

    print()
    print('MATCHING  %d anchors x %d pairs = %d pairs' % (n_anchor, nbuf - 1, npair))
    print('  same pair SET          %d / %d' % (same_set, npair))
    print('  same pair LIST order   %d / %d' % (same_order, npair))
    print()
    print('SPEED per anchor (%d pairs)' % (nbuf - 1))
    print('  bf.match x%-2d      %8.2f ms' % (nbuf - 1, 1000.0 * t_cpu / n_anchor))
    print('  match_many        %8.2f ms' % (1000.0 * t_gpu / n_anchor))
    print('  %.1fx' % (t_cpu / t_gpu if t_gpu > 0 else 0.0))


if __name__ == '__main__':
    main()
