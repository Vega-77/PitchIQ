"""TAPNext++ in batches, for the all-tests comparison.

    python tn_engine.py bridges <tag> [<tag> ...]   -> ../alltests/tn/bridges_<tag>.npz
    python tn_engine.py alone <tag> [<tag> ...]     -> ../alltests/tn/alone_<tag>.npz

bridges  every gap between two confident picks of the shipped tracker (cls_B), up to MAXGAP
         frames: TAPNext seeded on the pick before the gap and run forward to the pick after,
         and seeded on the pick after and run backward.  Both tracks are kept; the hybrid
         decides later what to accept.
alone    TAPNext on its own: seeded once on the window's first confident pick, then left to
         run to the end of the window (several windows run side by side in one batch).

Tracking is the 'lead' policy that held flights best: a native 512 px window centred on
the last estimate plus its own velocity, so the 12 px ball is never shrunk.
"""
import os
import sys
import time

import cv2
import numpy as np
import torch

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, 'tapnet')); sys.path.insert(0, S2)
sys.path.append('C:/Users/alexv/Desktop/Repos/PitchIQ/cv/balltrack')   # feat4 (video path) now lives in the repo
from tapnet.tapnextpp.votsp2026.model import TAPNextPP   # noqa: E402

AT = os.path.join(S2, 'alltests'); OUT = os.path.join(AT, 'tn')
S = 512
MAXGAP = int(os.environ.get('MAXGAP', 90))
B = int(os.environ.get('TNB', 1))
SPAN = 600                      # frames one group may span (read once, sequentially)


def model():
    m = TAPNextPP.from_checkpoint(os.path.join(HERE, 'tapnextpp_512.ckpt'), device='cuda', input_resolution=S)
    return m._model


def crop(fr, c):
    ox = int(round(np.clip(c[0] - S / 2, 0, 1920 - S))); oy = int(round(np.clip(c[1] - S / 2, 0, 1080 - S)))
    return fr[oy:oy + S, ox:ox + S], np.array([ox, oy], float)


def to_t(crops):
    a = np.stack(crops)[..., ::-1].copy()
    return torch.from_numpy(a).cuda().float().div_(127.5).sub_(1.0)[:, None]


class Group:
    """B tracks advanced in lock-step; each gets one frame per step from get(j, k)."""

    def __init__(self, net, seeds):
        self.net = net; self.n = len(seeds)
        self.seed = np.array(seeds, float); self.c = self.seed.copy(); self.v = np.zeros((self.n, 2))
        self.st = None; self.prev = None

    @torch.no_grad()
    def step(self, frames):
        crops, offs = [], []
        for j, fr in enumerate(frames):
            im, off = crop(fr, self.c[j] + self.v[j]); crops.append(im); offs.append(off)
        offs = np.array(offs); x = to_t(crops)
        with torch.amp.autocast('cuda', dtype=torch.float16):
            if self.st is None:
                q = np.zeros((self.n, 1, 3), np.float32)
                q[:, 0, 1] = (self.seed[:, 1] - offs[:, 1]) / 2.0; q[:, 0, 2] = (self.seed[:, 0] - offs[:, 0]) / 2.0
                tr, _, vl, self.st = self.net(video=x, query_points=torch.from_numpy(q).cuda())
            else:
                tr, _, vl, self.st = self.net(video=x, state=self.st)
        xy = tr[:, 0, 0].float().cpu().numpy()[:, ::-1] * 2.0 + offs
        vis = (vl[:, 0, 0, 0] > 0).cpu().numpy()
        if self.prev is not None:
            self.v = 0.6 * self.v + 0.4 * (xy - self.prev)
        self.prev = xy.copy(); self.c = xy.copy()
        return xy, vis


def read_range(cap, lo, hi, need):
    cap.set(cv2.CAP_PROP_POS_FRAMES, lo); out = {}
    for f in range(lo, hi + 1):
        if f in need:
            ok, fr = cap.read()
            assert ok, 'read failed %d' % f
            out[f] = fr
        elif not cap.grab():
            raise RuntimeError('grab failed %d' % f)
    return out


def bridges(net, tag):
    import feat4
    z = np.load(os.path.join(AT, 'p2d', 'cls_B', '%s.npz' % tag))
    ab, px, py, src = z['abs'], z['x'], z['y'], z['src']
    anc = np.flatnonzero(src == 1)
    jobs = []                       # (a, b, direction, order, seed)
    for i, j in zip(anc[:-1], anc[1:]):
        L = j - i - 1
        if 1 <= L <= MAXGAP:
            a, b = int(ab[i]), int(ab[j])
            jobs.append((a, b, +1, list(range(a, b + 1)), (px[i], py[i])))
            jobs.append((a, b, -1, list(range(b, a - 1, -1)), (px[j], py[j])))
    jobs.sort(key=lambda t: (min(t[3]), -len(t[3])))
    cap = cv2.VideoCapture(feat4.VIDEO)
    res = []; t0 = time.time(); k0 = 0; done = 0
    while k0 < len(jobs):
        span = [jobs[k0]]; lo = min(jobs[k0][3]); k = k0 + 1
        while k < len(jobs) and max(jobs[k][3]) - lo <= SPAN:
            span.append(jobs[k]); k += 1
        k0 = k
        need = set(f for t in span for f in t[3]); hi = max(need)
        frames = read_range(cap, lo, hi, need)
        for g0 in range(0, len(span), B):
            g = span[g0:g0 + B]
            G = Group(net, [t[4] for t in g]); L = max(len(t[3]) for t in g)
            xy = np.full((len(g), L, 2), np.nan); vis = np.zeros((len(g), L), bool)
            for s in range(L):
                fs = [frames[t[3][min(s, len(t[3]) - 1)]] for t in g]
                xy[:, s], vis[:, s] = G.step(fs)
            for q, t in enumerate(g):
                n = len(t[3]); res.append((t[0], t[1], t[2], xy[q, :n], vis[q, :n]))
            done += len(g)
            if done % 80 < len(g):
                print('%s  %d/%d tracks  %.0f s' % (tag, done, len(jobs), time.time() - t0), flush=True)
        del frames
    os.makedirs(OUT, exist_ok=True)
    np.save(os.path.join(OUT, 'bridges_%s.npy' % tag), np.array(res, dtype=object), allow_pickle=True)
    print('%s  %d tracks  %d frames  %.0f s' % (tag, len(res), sum(len(r[3]) for r in res), time.time() - t0), flush=True)


def alone(net, tags):
    import feat4
    caps, seeds, spans = [], [], []
    for tag in tags:
        z = np.load(os.path.join(AT, 'p2d', 'cls_B', '%s.npz' % tag))
        i = int(np.flatnonzero(z['src'] == 1)[0])
        s = int(z['abs'][i]); spans.append((s, int(z['abs'][-1]), int(z['abs'][0]), len(z['abs'])))
        seeds.append((z['x'][i], z['y'][i]))
        cap = cv2.VideoCapture(feat4.VIDEO); cap.set(cv2.CAP_PROP_POS_FRAMES, s); caps.append(cap)
    L = max(e - s + 1 for s, e, _, _ in spans)
    G = Group(net, seeds); out = [np.full((n, 2), np.nan) for *_, n in spans]; vis = [np.zeros(n, bool) for *_, n in spans]
    last = [None] * len(tags); t0 = time.time()
    for k in range(L):
        fs = []
        for j, cap in enumerate(caps):
            if spans[j][0] + k <= spans[j][1]:
                ok, fr = cap.read(); assert ok; last[j] = fr
            fs.append(last[j])
        xy, vv = G.step(fs)
        for j in range(len(tags)):
            f = spans[j][0] + k
            if f <= spans[j][1]:
                out[j][f - spans[j][2]] = xy[j]; vis[j][f - spans[j][2]] = vv[j]
        if k % 300 == 0:
            print('alone %s  %d/%d  %.0f s' % (','.join(tags), k, L, time.time() - t0), flush=True)
    os.makedirs(OUT, exist_ok=True)
    for j, tag in enumerate(tags):
        np.savez(os.path.join(OUT, 'alone_%s.npz' % tag), abs=np.arange(spans[j][2], spans[j][2] + spans[j][3]),
                 x=out[j][:, 0], y=out[j][:, 1], vis=vis[j], seed=spans[j][0])
    print('alone done %s  %.0f s' % (','.join(tags), time.time() - t0), flush=True)


if __name__ == '__main__':
    net = model()
    if sys.argv[1] == 'bridges':
        for t in sys.argv[2:]:
            if not os.path.exists(os.path.join(OUT, 'bridges_%s.npy' % t)):
                bridges(net, t)
    else:
        alone(net, sys.argv[2:])
