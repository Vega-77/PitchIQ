"""cv/experiments/player_lab.py's read-ahead: same items, same order, errors kept."""
import threading

import numpy as np
import pytest

from cv.experiments import player_lab as L


class Cap:
    def __init__(self, n):
        self.n, self.i = n, 0

    def read(self):
        if self.i >= self.n:
            return False, None
        self.i += 1
        return True, np.full((2, 2), self.i - 1)


def test_frames_come_in_order():
    got = [(f, int(img[0, 0])) for f, img in L._ahead(L._frames(Cap(50), 0, 49), depth=3)]
    assert got == [(f, f) for f in range(50)]


def test_a_short_video_still_raises():
    with pytest.raises(SystemExit, match='video ended at 5'):
        list(L._ahead(L._frames(Cap(5), 0, 9)))


def test_stopping_early_ends_the_thread():
    before = threading.active_count()
    it = L._ahead(L._frames(Cap(1000), 0, 999), depth=2)
    next(it)
    it.close()
    assert threading.active_count() == before


def test_float16_features_link_exactly_as_float32():
    """load_detections keeps features float16; the linker's float64 mean must not notice."""
    from cv.linking import _unit_mean

    rng = np.random.default_rng(0)
    f16 = rng.normal(size=(500, 1024)).astype(np.float16)
    rows = rng.choice(500, 40, replace=False)
    assert np.array_equal(_unit_mean(f16, rows), _unit_mean(f16.astype(np.float32), rows))
