"""Kinematic gap bridging (new brief, line 3.1): synthetic picks on a pan-stabilised trajectory.

bridge(F, out, src, G, mode): every run of empty frames of at most G frames between two picks is
filled with synthetic picks ('syn', x, y) in source px, src 3.
  lin   straight line between the two end picks, in pan-stabilised px
  herm  cubic Hermite: end positions plus end velocities taken from the picks just outside the gap
        (up to 4 frames back, same run); falls back to the line where an end has no neighbour
Candidate picks are never replaced.  The same synthetic positions are the centre of line 1.1's
re-proposal window, so the per-click distance of a synthetic ring is 1.1's ceiling measurement.
"""
import numpy as np

from pitchiq.balltrack import gap_fill

KX, KY = gap_fill.KX, gap_fill.KY


def _pos(F, out, t):
    j = out[t]
    return float(F[t][KX][j]), float(F[t][KY][j]), int(F[t]['abs'])


def _vel(F, out, t, step):
    """Velocity (stabilised px per frame) at pick t from the pick up to 4 frames away on the far side."""
    for k in range(4, 0, -1):
        u = t + step * k
        if 0 <= u < len(out) and out[u] is not None and not isinstance(out[u], tuple):
            if all(out[w] is not None for w in range(min(t, u), max(t, u) + 1)):
                x0, y0, a0 = _pos(F, out, t)
                x1, y1, a1 = _pos(F, out, u)
                if a1 != a0:
                    return (x1 - x0) / (a1 - a0), (y1 - y0) / (a1 - a0)
    return None


def bridge(F, out, src, G, mode='lin'):
    out = list(out)
    src = np.array(src)
    T = len(out)
    on = [t for t in range(T) if out[t] is not None]
    for a, b in zip(on, on[1:]):
        if b - a < 2 or b - a - 1 > G:
            continue
        xa, ya, ta = _pos(F, out, a)
        xb, yb, tb = _pos(F, out, b)
        D = float(tb - ta)
        va = vb = None
        if mode == 'herm':
            va, vb = _vel(F, out, a, -1), _vel(F, out, b, 1)
        lin = ((xb - xa) / D, (yb - ya) / D)
        va, vb = va or lin, vb or lin
        for t in range(a + 1, b):
            w = (F[t]['abs'] - ta) / D
            if mode == 'herm':
                h00, h10 = 2 * w ** 3 - 3 * w ** 2 + 1, w ** 3 - 2 * w ** 2 + w
                h01, h11 = -2 * w ** 3 + 3 * w ** 2, w ** 3 - w ** 2
                px = h00 * xa + h10 * D * va[0] + h01 * xb + h11 * D * vb[0]
                py = h00 * ya + h10 * D * va[1] + h01 * yb + h11 * D * vb[1]
            else:
                px, py = xa + w * (xb - xa), ya + w * (yb - ya)
            out[t] = gap_fill._synth(F[t], px, py)
            src[t] = 3
    return out, src
