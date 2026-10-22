"""The ball ring is not drawn for runs shorter than MIN_RUN frames: on the
labelled ball points every such flash was a wrong ring."""

from __future__ import annotations

import numpy as np

# match_video draws the rings with these; it is not imported here because its
# import reads the generated venue-map files, which a clone does not have.
from pitchiq.balltrack import ballgate


def test_short_runs_marks_only_runs_under_k():
    on = np.array([1, 0, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1], bool)
    got = ballgate.short_runs(on, 5)
    assert got.tolist() == [
        True,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        True,
        True,
        True,
        True,
    ]


def test_short_runs_keeps_a_run_of_exactly_k():
    assert not ballgate.short_runs(np.ones(5, bool), 5).any()


def test_short_runs_empty_and_all_off():
    assert ballgate.short_runs(np.zeros(0, bool)).shape == (0,)
    assert not ballgate.short_runs(np.zeros(7, bool)).any()


def test_default_is_min_run():
    assert ballgate.short_runs(np.ones(ballgate.MIN_RUN - 1, bool)).all()
    assert not ballgate.short_runs(np.ones(ballgate.MIN_RUN, bool)).any()
