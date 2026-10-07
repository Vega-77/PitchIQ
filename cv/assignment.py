"""Minimum-cost matching, with "match nothing" as a priced option.

Player tracking asks the same question three times: which detection is which
track, which fragment continues which, and which tracked box is which labelled
player. Each is a bipartite matching where some pairs are impossible and where
leaving something unmatched is a legitimate answer with a cost of its own — a
detection that fits no track is a new player, not an error.

scipy has `linear_sum_assignment`, but the test suite deliberately installs
only numpy and OpenCV (see requirements-test.txt), and this is short enough to
own. It is the classic Hungarian method with potentials, O(n^2 m), vectorised
over columns, on matrices that here are tens to a few hundred wide.

    min_cost_matching(cost, unmatched)

turns optional matching into a square assignment the standard way: every row
and every column gets a private "unmatched" partner at `unmatched` cost, and
pairs that are forbidden (inf or NaN) are never chosen. So a pair is matched
only when that is cheaper than leaving both sides alone — 2 * unmatched is the
real threshold — and the result never contains a forbidden pair.
"""

from __future__ import annotations

import numpy as np


def _hungarian(a: np.ndarray) -> np.ndarray:
    """Column chosen for each row of a finite n x m matrix, n <= m."""
    n, m = a.shape
    u = np.zeros(n + 1)
    v = np.zeros(m + 1)
    p = np.zeros(m + 1, dtype=int)        # row assigned to column j (1-based), 0 = none
    way = np.zeros(m + 1, dtype=int)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = np.full(m + 1, np.inf)
        used = np.zeros(m + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            free = ~used[1:]
            cur = a[i0 - 1] - u[i0] - v[1:]
            better = free & (cur < minv[1:])
            minv[1:][better] = cur[better]
            way[1:][better] = j0
            cand = np.where(free, minv[1:], np.inf)
            j1 = int(np.argmin(cand)) + 1
            delta = cand[j1 - 1]
            cols = np.flatnonzero(used)
            u[p[cols]] += delta
            v[cols] -= delta
            minv[1:][free] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    rows = np.full(n, -1, dtype=int)
    for j in range(1, m + 1):
        if p[j]:
            rows[p[j] - 1] = j - 1
    return rows


def min_cost_assignment(cost) -> list[tuple[int, int]]:
    """Full assignment of the smaller side, cheapest total; forbidden pairs dropped.

    Maximises the number of allowed pairs first and minimises cost among those,
    because a forbidden pair is priced above any total of allowed ones.
    """
    c = np.asarray(cost, dtype=np.float64)
    if c.ndim != 2 or 0 in c.shape:
        return []
    flip = c.shape[0] > c.shape[1]
    if flip:
        c = c.T
    ok = np.isfinite(c)
    if not ok.any():
        return []
    span = float(np.abs(c[ok]).max()) + 1.0
    a = np.where(ok, c, span * (min(c.shape) + 1))
    rows = _hungarian(a)
    pairs = [(i, int(j)) for i, j in enumerate(rows) if j >= 0 and ok[i, j]]
    if flip:
        pairs = [(j, i) for i, j in pairs]
    return sorted(pairs)


def min_cost_matching(cost, unmatched: float) -> list[tuple[int, int]]:
    """Cheapest optional matching: leaving a row or column alone costs `unmatched`.

    A pair (i, j) is used only when cost[i, j] < 2 * unmatched and the whole
    arrangement is cheapest that way. inf/NaN marks a pair as impossible.
    """
    c = np.asarray(cost, dtype=np.float64)
    if c.ndim != 2 or 0 in c.shape:
        return []
    n, m = c.shape
    big = np.full((n + m, m + n), np.inf)
    big[:n, :m] = np.where(np.isfinite(c), c, np.inf)
    big[:n, m:][np.arange(n), np.arange(n)] = unmatched
    big[n:, :m][np.arange(m), np.arange(m)] = unmatched
    # dummy-dummy: free, so unused dummies pair off among themselves
    big[n:, m:] = 0.0
    return [(i, j) for i, j in min_cost_assignment(big) if i < n and j < m]
