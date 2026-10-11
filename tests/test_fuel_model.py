"""Checks on the fuel model.

These are the tests that matter most in the project. The fuel figures are
modelled rather than measured, so if the model drifts, every number
downstream is wrong and nothing else would notice.
"""

import pytest

from flightperf.fuel.model import FuelEstimator


@pytest.fixture(scope="module")
def a320():
    return FuelEstimator("A320")


def test_short_flight_is_in_the_right_ballpark(a320):
    """An A320 on a Paris-Toulouse sector burns roughly 2 to 3 tonnes."""
    result = a320.estimate(309)
    tonnes = result["fuel_kg"] / 1000
    assert 1.8 < tonnes < 3.2, f"got {tonnes:.2f} tonnes"


def test_longer_flight_burns_more_but_less_per_mile(a320):
    """Fuel per mile falls with distance, because climb is a fixed cost."""
    short = a320.estimate(300)
    long = a320.estimate(1200)

    assert long["fuel_kg"] > short["fuel_kg"]
    assert long["fuel_kg"] / 1200 < short["fuel_kg"] / 300


def test_fuel_rises_with_every_extra_mile(a320):
    """The curve has to be monotonic, or interpolating along it is unsafe."""
    distances = [100, 200, 400, 800, 1600, 3000]
    fuels = [a320.estimate(d)["fuel_kg"] for d in distances]
    assert fuels == sorted(fuels)


def test_takeoff_weight_stays_within_limits(a320):
    """The aircraft cannot take off heavier than its maximum weight."""
    result = a320.estimate(2500)
    assert result["takeoff_mass_kg"] <= a320.info["mtow_kg"]
    assert result["takeoff_mass_kg"] > a320.info["oew_kg"]


def test_co2_follows_fuel(a320):
    """CO2 is fuel times a fixed factor - nothing clever, but worth pinning."""
    result = a320.estimate(500)
    assert result["co2_kg"] == pytest.approx(result["fuel_kg"] * 3.16)


def test_zero_or_missing_distance_gives_nothing(a320):
    """No distance means no answer, not a zero."""
    assert a320.estimate(0) is None
    assert a320.estimate(None) is None


def test_a_bigger_aircraft_burns_more_on_the_same_route():
    """Sanity check across types: a widebody burns more than a narrowbody."""
    narrow = FuelEstimator("A320").estimate(1000)["fuel_kg"]
    wide = FuelEstimator("A332").estimate(1000)["fuel_kg"]
    assert wide > narrow * 1.5
