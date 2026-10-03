"""Ground roll that slows under drag: uv(t) = uv0 + v E(k, t), k in [0, 1.5].
Linear in (uv0, v) for fixed k, so k is scanned."""
import numpy as np
import vflight as V

KS = np.linspace(0, 1.5, 16)


def trim(e, keep=0.8):
    e = np.sort(e ** 2)
    return float(np.sqrt(np.mean(e[:max(3, int(np.ceil(keep * len(e))))])))


def droll(tr, a, b):
    """-> (trimmed px error, (uv0, v, k)) of the best slowing roll over frames a..b."""
    idx = np.arange(a, b + 1); idx = idx[tr.has[idx]]
    if len(idx) < 4:
        return np.nan, None
    t = (idx - a) / V.FPS
    best = (np.inf, None)
    for k in KS:
        Et = t if k == 0 else (1 - np.exp(-k * t)) / k
        Z = np.zeros_like(t); O = np.ones_like(t)
        Arow = np.stack([np.stack([O, Z, Et, Z], 1), np.stack([Z, O, Z, Et], 1)], 1)
        Am = np.einsum('kij,kjl->kil', tr.J[idx], Arow).reshape(-1, 4)
        bm = np.einsum('kij,kj->ki', tr.J[idx], tr.g[idx]).ravel()
        th = np.linalg.lstsq(Am, bm, rcond=None)[0]
        # robust: refit once without the worst 20 %
        e = np.sqrt(np.sum(((Am.dot(th) - bm).reshape(-1, 2)) ** 2, 1))
        keep = np.repeat(e <= np.quantile(e, 0.8), 2)
        th = np.linalg.lstsq(Am[keep], bm[keep], rcond=None)[0]
        e = np.sqrt(np.sum(((Am.dot(th) - bm).reshape(-1, 2)) ** 2, 1))
        te = trim(e)
        if te < best[0]:
            best = (te, (th[:2], th[2:], k))
    return best
