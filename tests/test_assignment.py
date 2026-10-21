import itertools

import numpy as np

from pitchiq.assignment import min_cost_assignment, min_cost_matching


def _brute_assignment(c):
    n, m = c.shape
    best = None
    if n <= m:
        for cols in itertools.permutations(range(m), n):
            pairs = [(i, j) for i, j in enumerate(cols) if np.isfinite(c[i, j])]
            key = (-len(pairs), sum(c[i, j] for i, j in pairs))
            if best is None or key < best[0]:
                best = (key, pairs)
    else:
        for rows in itertools.permutations(range(n), m):
            pairs = [(i, j) for j, i in enumerate(rows) if np.isfinite(c[i, j])]
            key = (-len(pairs), sum(c[i, j] for i, j in pairs))
            if best is None or key < best[0]:
                best = (key, pairs)
    return best[0]


def _brute_matching(c, unmatched):
    n, m = c.shape
    best = np.inf
    # every partial injection rows -> cols
    for k in range(min(n, m) + 1):
        for rows in itertools.combinations(range(n), k):
            for cols in itertools.permutations(range(m), k):
                if not all(np.isfinite(c[i, j]) for i, j in zip(rows, cols)):
                    continue
                total = sum(c[i, j] for i, j in zip(rows, cols)) + unmatched * (n + m - 2 * k)
                best = min(best, total)
    return best


def test_assignment_matches_brute_force():
    rng = np.random.default_rng(0)
    for _ in range(150):
        n, m = rng.integers(1, 5, size=2)
        c = rng.uniform(0, 10, size=(n, m))
        c[rng.random((n, m)) < 0.25] = np.inf
        pairs = min_cost_assignment(c)
        assert all(np.isfinite(c[i, j]) for i, j in pairs)
        assert len({i for i, _ in pairs}) == len(pairs) == len({j for _, j in pairs})
        key = (-len(pairs), sum(c[i, j] for i, j in pairs))
        want = _brute_assignment(c)
        assert key[0] == want[0]
        assert abs(key[1] - want[1]) < 1e-9


def test_matching_matches_brute_force():
    rng = np.random.default_rng(1)
    for _ in range(150):
        n, m = rng.integers(1, 5, size=2)
        c = rng.uniform(0, 10, size=(n, m))
        c[rng.random((n, m)) < 0.25] = np.nan
        u = float(rng.uniform(0.5, 6))
        pairs = min_cost_matching(c, u)
        assert all(np.isfinite(c[i, j]) for i, j in pairs)
        total = sum(c[i, j] for i, j in pairs) + u * (n + m - 2 * len(pairs))
        assert abs(total - _brute_matching(c, u)) < 1e-9


def test_matching_leaves_expensive_pairs_alone():
    c = np.array([[1.0, 50.0], [50.0, 30.0]])
    assert min_cost_matching(c, unmatched=10.0) == [(0, 0)]


def test_empty_inputs():
    assert min_cost_assignment(np.zeros((0, 3))) == []
    assert min_cost_matching(np.zeros((2, 0)), 1.0) == []
    assert min_cost_assignment(np.full((2, 2), np.inf)) == []
