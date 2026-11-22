"""Flight units per camera: flight_model works in camera heights, so gravity and the
centre circle must follow the camera that filmed the flight."""

from __future__ import annotations


import numpy as np
import pytest

from pitchiq.balltrack.pitch_registration import drag_roll, flight_chain, flight_model


@pytest.fixture(autouse=True)
def _restore():
    yield
    flight_model.set_camera(flight_model.R_CH0)


@pytest.mark.parametrize('h_m', [5.14, 6.04, 15.0])
def test_gravity_is_9_81_in_metres_for_any_camera(h_m):
    flight_model.set_camera(9.15 / h_m)  # the circle is 9.15 m, the camera h_m up
    assert flight_model.G / flight_model.R_CH * 9.15 == pytest.approx(9.81)


@pytest.mark.parametrize('h_m', [5.14, 6.04, 15.0])
def test_one_second_flight_peaks_at_the_same_height_in_metres(h_m):
    flight_model.set_camera(9.15 / h_m)
    z = flight_model.ballistic(np.array([0, 0, 1.0, 0, 0, 1.0]), np.linspace(0, 1, 101))[:, 2]
    assert z.max() * h_m == pytest.approx(9.81 / 8, rel=1e-3)


def test_scrimmage_units_are_unchanged():
    assert flight_model.R_CH0 == pytest.approx(71.2 / 40.0)
    assert flight_model.G == pytest.approx(9.81 * 71.2 / 40.0 / 9.15)


# flight_chain once copied G at import (the scrimmage camera's), and scaled its apex gate the
# wrong way round: no Woodbridge or Wardlaw-Hartridge flight could fit (session step 3.2).
@pytest.mark.parametrize('h_m', [5.14, 6.3, 11.8])
def test_chain_segment_falls_at_this_cameras_gravity(h_m):
    flight_model.set_camera(9.15 / h_m)
    P0, P1 = np.zeros(3), np.array([1.0, 0, 0])
    z = flight_chain.seg_pos(P0, P1, 1.0, 1e-6, np.linspace(0, 1, 101))[:, 2]
    assert z.max() * h_m == pytest.approx(9.81 / 8, rel=1e-3)


@pytest.mark.parametrize('h_m', [5.14, 6.3, 11.8])
def test_ball_dropped_from_one_metre_lands_in_0_45_s(h_m):
    flight_model.set_camera(9.15 / h_m)
    s, _ = flight_chain.ground_hit(np.array([0, 0, 1.0 / h_m]), np.zeros(3), +1)
    assert s == pytest.approx(np.sqrt(2 / 9.81), rel=1e-6)


@pytest.mark.parametrize('h_m', [5.14, 6.3, 11.8])
def test_apex_gate_is_0_41_m_for_any_camera(h_m):
    flight_model.set_camera(9.15 / h_m)
    assert flight_chain.apex_gate() * h_m == pytest.approx(0.08 * 9.15 / flight_model.R_CH0)


class _Line:
    """A ball rolling at constant speed, seen on 50 frames with identity Jacobians."""

    def __init__(self, n=50):
        self.has = np.ones(n, bool)
        self.J = np.tile(np.eye(2), (n, 1, 1))
        self.g = np.c_[np.arange(n) * 0.5, np.full(n, 3.0)]


def test_roll_test_keeps_to_the_half_at_its_end():
    tr = _Line()
    e, th = drag_roll.droll(tr, 40, 60)  # a chain's last node time past the last frame
    assert e == pytest.approx(0, abs=1e-9)
    assert th[0] == pytest.approx([20.0, 3.0])


def test_roll_test_does_not_wrap_before_the_half():
    tr = _Line()
    e, th = drag_roll.droll(tr, -5, 10)  # frame -5 must not read frame 45
    assert e == pytest.approx(0, abs=1e-9)
    assert th[0] == pytest.approx([-2.5, 3.0])
