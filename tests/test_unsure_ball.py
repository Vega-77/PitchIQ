"""The 3D ball's third state (session step 3.3): 'unsure'.

Where the ball was seen but neither a flight nor the smoothed ground path vouches for it."""

from __future__ import annotations

import numpy as np

from pitchiq.balltrack.pitch_registration import merge_air_and_ground as mg

COLS = ['a', 'b', 'r_trim', 'd_trim', 'd_apex']
CHM = 5.0  # one camera height, m


def _scan(*rows):
    """flight_scan rows (a, b, roll error, arc error, apex in camera heights)."""
    return np.array(rows, float).reshape(-1, len(COLS))


def test_a_window_an_arc_fits_and_a_roll_does_not_marks_its_frames():
    S = _scan((2, 4, 10.0, 1.0, 0.2))  # roll 10x worse, arc fits to 1 px, apex 1 m
    assert mg.raised(S, COLS, 8, CHM).tolist() == [0, 0, 1, 1, 1, 0, 0, 0]


def test_a_window_marks_nothing_when_any_test_fails():
    for row in (
        (2, 4, 3.9, 1.0, 0.2),  # the roll is under 4x worse
        (2, 4, 30.0, 3.0, 0.2),  # the arc itself misses by over 2.5 px
        (2, 4, 10.0, 1.0, 0.09),  # the arc rises under 0.5 m
        (2, 4, np.nan, 1.0, 0.2),  # no roll fit
    ):
        assert not mg.raised(_scan(row), COLS, 8, CHM).any(), row


def test_no_windows_marks_nothing():
    assert not mg.raised(_scan(), COLS, 5, CHM).any()


def test_merge_splits_the_chains_ground_into_ground_and_unsure():
    chain = dict(
        state=np.array(['air', 'ground', 'ground', 'ground', 'ground', 'ground', 'none', 'ground']),
        x=np.array([50.0, 0, 0, 0, 0, 0, np.nan, 0]),
        y=np.array([40.0, 0, 0, 0, 0, 0, np.nan, 0]),
    )
    smooth = dict(
        x=np.array([1.0, 2, 3, 4, np.nan, 6, np.nan, 8]),
        y=np.array([1.0, 2, 3, 4, 5, 6, np.nan, 8]),
        flag=np.array([0, 1, 3, 0, 1, 2, 0, 1]),
    )
    S = _scan((7, 7, 10.0, 1.0, 0.2))  # the last frame lies under a raised-ball window
    x, y, st = mg.merge(chain, smooth, S, COLS, CHM)
    assert st.tolist() == [
        'air',  # a flight keeps the chain's position
        'ground',  # a used reading
        'ground',  # a kick is a used reading too
        'unsure',  # a reading the smoother did not use (off the lines)
        'unsure',  # the smoother placed nothing
        'unsure',  # an outlier
        'none',
        'unsure',  # a raised-ball window
    ]
    assert x[0] == 50.0 and y[0] == 40.0
    assert x[1:4].tolist() == [2.0, 3.0, 4.0] and np.isnan(x[4])
    assert x[7] == 8.0  # an unsure frame keeps the smoother's position for whoever wants it


def test_the_camera_height_comes_from_the_venue_or_the_scrimmage(tmp_path):
    v = tmp_path / 'zz_venue.npz'
    np.savez(v, C=np.array([0.0, 0.0, 2.0]))
    assert mg.camera_height_m(str(v)) == 9.15 * 2.0
    assert mg.camera_height_m(str(tmp_path / 'missing.npz')) == 9.15 / mg.R_CH0
    assert mg.camera_height_m(None) == 9.15 / mg.R_CH0


def test_the_older_call_finds_the_scan_beside_the_chain(tmp_path):
    n = 4
    np.savez(
        tmp_path / 'zz_ball3d2.npz',
        abs=np.arange(n),
        x=np.zeros(n),
        y=np.zeros(n),
        z=np.zeros(n),
        state=np.array(['ground'] * n),
    )
    np.savez(tmp_path / 'zz_smooth.npz', x=np.ones(n), y=np.ones(n), flag=np.array([1, 1, 2, 1]))
    np.savez(tmp_path / 'zz_scan2.npz', S=_scan((3, 3, 10.0, 1.0, 1.0)), cols=np.array(COLS))
    out = tmp_path / 'zz_ball3dd.npz'
    mg.main([str(tmp_path / 'zz_ball3d2.npz'), str(tmp_path / 'zz_smooth.npz'), str(out)])
    with np.load(out) as z:
        assert z['state'].tolist() == ['ground', 'ground', 'unsure', 'unsure']
        assert sorted(z.files) == ['abs', 'state', 'x', 'y', 'z']


def test_the_flight_check_counts_unsure_balls_as_seen():
    from pitchiq import checks

    # 200 of 10000 seen balls in the air is 2%: too few. Counting only 'ground' as seen
    # would make it 9% and pass a camera whose flights are mostly missing.
    st = np.array(['air'] * 200 + ['ground'] * 2000 + ['unsure'] * 7800)
    assert checks.flight_flags([], st) == ['2.0% of the ball in the air']
