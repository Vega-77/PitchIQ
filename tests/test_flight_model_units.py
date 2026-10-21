"""Flight units per camera: flight_model works in camera heights, so gravity and the
centre circle must follow the camera that filmed the flight."""

from __future__ import annotations


import numpy as np
import pytest

from pitchiq.balltrack.pitch_registration import flight_model


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
