"""Unit parsing, conversion and round-trips."""

from __future__ import annotations

import math

import pytest

from speed_profile_builder.errors import UnitError
from speed_profile_builder.units import (
    convert,
    format_quantity,
    parse_duration,
    parse_length,
    parse_mass,
    parse_quantity,
    parse_speed,
    round_trip,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("90 km/h", 90.0),
        ("90km/h", 90.0),
        ("90", 90.0),
        (90, 90.0),
        (90.5, 90.5),
        ("60 mph", 96.56064),
        ("25 m/s", 90.0),
        ("  50 kph  ", 50.0),
    ],
)
def test_parse_speed_accepts_common_spellings(raw: object, expected: float) -> None:
    assert parse_speed(raw) == pytest.approx(expected)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("4 m", 4.0), ("400 cm", 4.0), ("13.12336 ft", 4.0), ("1 km", 1000.0)],
)
def test_parse_length_converts_to_metres(raw: str, expected: float) -> None:
    assert parse_length(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("18 t", 18000.0), ("18000 kg", 18000.0), ("18", 18.0), ("2 tonnes", 2000.0)],
)
def test_parse_mass_treats_bare_numbers_as_kilograms(raw: str, expected: float) -> None:
    """A bare mass is kg, unlike OSM's maxweight; the linter catches the confusion."""
    assert parse_mass(raw) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("raw", "expected"), [("30 s", 30.0), ("5 min", 300.0), ("1 h", 3600.0), (45, 45.0)]
)
def test_parse_duration(raw: object, expected: float) -> None:
    assert parse_duration(raw) == pytest.approx(expected)  # type: ignore[arg-type]


def test_unknown_unit_lists_the_known_ones() -> None:
    with pytest.raises(UnitError, match="unknown speed unit 'furlongs'"):
        parse_speed("30 furlongs")


def test_unparseable_string_names_the_dimension() -> None:
    with pytest.raises(UnitError, match="cannot parse 'fast' as a speed"):
        parse_speed("fast")


def test_boolean_is_rejected_as_a_quantity() -> None:
    """YAML turns bare ``yes`` into True; that must never become a speed."""
    with pytest.raises(UnitError, match="boolean"):
        parse_quantity(True, "speed")


def test_non_finite_number_is_rejected() -> None:
    with pytest.raises(UnitError, match="finite"):
        parse_speed(math.inf)


def test_unknown_dimension_is_rejected() -> None:
    with pytest.raises(UnitError, match="unknown dimension"):
        parse_quantity(1, "luminosity")


def test_wrong_type_is_rejected() -> None:
    with pytest.raises(UnitError, match="expected a number or string"):
        parse_quantity([1, 2], "speed")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "dimension", "unit"),
    [
        ("55 mph", "speed", "mph"),
        ("120 km/h", "speed", "km/h"),
        ("7.5 t", "mass", "t"),
        ("3200 kg", "mass", "kg"),
        ("4.25 m", "length", "m"),
        ("14 ft", "length", "ft"),
        ("90 min", "duration", "min"),
    ],
)
def test_round_trip_preserves_the_original_value(raw: str, dimension: str, unit: str) -> None:
    """Parsing then re-rendering in the same unit must be the identity."""
    assert round_trip(raw, dimension, unit) == raw


def test_round_trip_across_units_is_reversible() -> None:
    kmh = parse_speed("62.5 mph")
    back = convert(kmh, "speed", "mph")
    assert back == pytest.approx(62.5)


def test_format_quantity_trims_trailing_zeros() -> None:
    assert format_quantity(50.0, "speed") == "50 km/h"
    assert format_quantity(0.0, "duration") == "0 s"
    assert format_quantity(7.25, "mass", "t", digits=3) == "0.007 t"


def test_convert_rejects_a_unit_from_another_dimension() -> None:
    with pytest.raises(UnitError, match="unknown speed unit 'kg'"):
        convert(50.0, "speed", "kg")
