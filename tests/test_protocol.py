"""Unit tests for nad_rs232.protocol."""

import pytest

from nad_rs232.protocol import (
    db_to_percent,
    format_volume_db,
    parse_message,
    parse_volume_value,
    percent_to_db,
    round_half_db,
)

# Breakpoints measured on a real NAD C368: every (dB, %) pair must round-trip.
MEASURED_POINTS = [
    (-70.0, 0),
    (-60.0, 16),
    (-50.5, 25),  # documented intermediate measurement (interpolated segment)
    (-40.0, 35),
    (-35.0, 40),
    (-30.0, 45),
    (-25.5, 50),
    (-15.0, 60),
    (-10.0, 67),
    (-5.0, 73),
    (0.0, 80),
    (5.0, 88),
    (12.0, 100),
]


@pytest.mark.parametrize(("db", "percent"), MEASURED_POINTS)
def test_db_to_percent_measured_points(db: float, percent: int) -> None:
    assert db_to_percent(db) == pytest.approx(percent, abs=0.1)


@pytest.mark.parametrize(("db", "percent"), MEASURED_POINTS)
def test_percent_to_db_measured_points(db: float, percent: int) -> None:
    assert percent_to_db(percent) == pytest.approx(db, abs=0.25)


def test_volume_clamping() -> None:
    assert db_to_percent(-99.0) == 0.0
    assert db_to_percent(50.0) == 100.0
    assert percent_to_db(-5.0) == -70.0
    assert percent_to_db(150.0) == 12.0


def test_round_half_db() -> None:
    assert round_half_db(-25.3) == -25.5
    assert round_half_db(-25.2) == -25.0
    assert round_half_db(0.26) == 0.5


def test_format_volume_db() -> None:
    assert format_volume_db(-25.5) == "-25.5"
    assert format_volume_db(-25.3) == "-25.5"
    assert format_volume_db(0.0) == "0.0"
    assert format_volume_db(5.0) == "5.0"


def test_parse_message() -> None:
    assert parse_message("Main.Volume=-25.5") == ("Main.Volume", "-25.5")
    assert parse_message("Main.Power=On") == ("Main.Power", "On")
    assert parse_message("Source1.Name=Optical 1") == ("Source1.Name", "Optical 1")
    # Malformed messages must be ignored per the protocol.
    assert parse_message("garbage") is None
    assert parse_message("NoDotHere=1") is None
    assert parse_message("=orphan") is None


def test_parse_volume_value_db_mode() -> None:
    assert parse_volume_value("-25.5", percent_mode=False) == -25.5
    assert parse_volume_value("0", percent_mode=False) == 0.0
    assert parse_volume_value("12", percent_mode=False) == 12.0
    # The infamous corrupted frame from the field: must be rejected.
    assert parse_volume_value("100208", percent_mode=False) is None
    assert parse_volume_value("100208%", percent_mode=False) is None
    assert parse_volume_value("abc", percent_mode=False) is None
    # Out of hardware range -> rejected.
    assert parse_volume_value("-90", percent_mode=False) is None
    assert parse_volume_value("40", percent_mode=False) is None


def test_parse_volume_value_percent_mode() -> None:
    # Reported as percent: converted to dB via the measured curve.
    assert parse_volume_value("50", percent_mode=True) == pytest.approx(-25.5)
    assert parse_volume_value("50%", percent_mode=False) == pytest.approx(-25.5)
    assert parse_volume_value("0", percent_mode=True) == -70.0
    assert parse_volume_value("100", percent_mode=True) == 12.0
    assert parse_volume_value("120", percent_mode=True) is None
