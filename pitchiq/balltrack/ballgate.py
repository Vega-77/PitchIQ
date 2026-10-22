"""Stretches where the ball classifier is lost: draw no ball there (2026-10-15).

On the practice sets the user's ballring answers went wrong in whole stretches, not ring by ring.
Per-ring features (classifier score at the ring, margin to the next candidate, run length, share of
tracker picks) only separated the rings by game: thresholds picked on three games and tried on the
fourth removed 5 of 50 wrong rings for 24 of 99 right ones. What does carry over is how sure the
classifier is around the ring for minutes on end: the median, over the rings within +-LOST_W frames,
of the best classifier score in each ringed frame. It sat below LOST_P for 97-100% of East Brunswick's
rings (35 of their 45 answered rings were wrong) and for 0-0.8% of NB H1, West Windsor and the
scrimmage (2 + 7 + 6 wrong of 104). A wrong ring is worse than a missing one, so there we draw nothing.

    lost(tag, abs0, on) -> bool per frame of the half (True: don't draw a ball)
    short_runs(on)      -> bool per frame: rings that would show for under MIN_RUN frames
"""

import functools
import glob
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LOST_P = 2.0  # classifier logit
LOST_W = 9000  # frames each side (5 min at 30 fps)
STEP = 300  # evaluate every 10 s; a stretch is minutes long
MIN_N = 75  # need this many scored rings in the window to call it lost
MIN_RUN = 5  # frames; on the labelled ball points every ring shown for fewer was wrong (2026-10-12)


@functools.lru_cache(maxsize=8)
def best_p(tag):
    """{abs frame: best classifier score among that frame's candidates}, over the half's pieces."""
    m = {}
    for p in sorted(glob.glob(os.path.join(HERE, '%s_q[0-9][0-9][0-9].npz' % tag))):
        c = p.replace('.npz', 'cls.npz')
        if not os.path.exists(c):
            continue
        F, P = np.load(p, allow_pickle=True)['f'], np.load(c)['p']
        if not len(F):
            continue
        u, s = np.unique(F, return_index=True)
        for f, v in zip(u.tolist(), np.maximum.reduceat(P, s).tolist()):
            if v > m.get(f, -np.inf):
                m[f] = v
    return m


def lost(tag, abs0, on):
    """True on frames whose surrounding rings (`on`, per frame from abs0) the classifier barely backs."""
    on = np.asarray(on, bool)
    out = np.zeros(len(on), bool)
    m = best_p(tag)
    if not m:
        return out
    idx = np.flatnonzero(on)
    sc = np.array([m.get(int(abs0 + i), np.nan) for i in idx])
    ok = np.isfinite(sc)
    idx, sc = idx[ok], sc[ok]
    for c in range(0, len(on), STEP):
        a, b = np.searchsorted(idx, c - LOST_W), np.searchsorted(idx, c + LOST_W, 'right')
        if b - a >= MIN_N and np.median(sc[a:b]) < LOST_P:
            out[max(0, c - STEP // 2) : c + STEP // 2 + 1] = True
    return out


def short_runs(on, k=MIN_RUN):
    """True on the frames of each run of `on` shorter than k frames."""
    d = np.diff(np.r_[0, np.asarray(on, bool).astype(np.int8), 0])
    out = np.zeros(len(on), bool)
    for b, e in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
        out[b:e] = e - b < k
    return out
