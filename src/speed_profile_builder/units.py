"""Unit parsing and canonicalisation.

Routing engines disagree about units: OSRM speaks km/h and metres, Valhalla
speaks km/h but takes vehicle mass in tonnes, and OSM tags in the wild carry
``mph``, ``t``, ``kg``, ``ft`` and bare numbers whose meaning depends on the key.
Rather than sprinkling conversions through the emitters, every quantity is
normalised exactly once at spec-parse time into a canonical SI-ish unit:

===========  ================
dimension    canonical unit
===========  ================
speed        km/h
length       m
mass         kg
duration     s
===========  ================

Emitters convert out of canonical form at the last moment, which keeps the IR
comparable (a diff can subtract two speeds without asking what unit they are in)
and makes round-trip tests meaningful.
"""

from __future__ import annotations

import math
import re
from typing import Final

from .errors import UnitError

#: Multiplicative factor from a unit to its dimension's canonical unit.
_SPEED_UNITS: Final[dict[str, float]] = {
    "km/h": 1.0,
    "kmh": 1.0,
    "kph": 1.0,
    "km/hr": 1.0,
    "mph": 1.609344,
    "m/s": 3.6,
    "ms": 3.6,
    "knots": 1.852,
    "kn": 1.852,
}

_LENGTH_UNITS: Final[dict[str, float]] = {
    "m": 1.0,
    "metre": 1.0,
    "metres": 1.0,
    "meter": 1.0,
    "meters": 1.0,
    "cm": 0.01,
    "mm": 0.001,
    "km": 1000.0,
    "ft": 0.3048,
    "feet": 0.3048,
    "foot": 0.3048,
    "in": 0.0254,
    "inch": 0.0254,
    "inches": 0.0254,
    "mi": 1609.344,
    "mile": 1609.344,
    "miles": 1609.344,
}

_MASS_UNITS: Final[dict[str, float]] = {
    "kg": 1.0,
    "g": 0.001,
    "t": 1000.0,
    "ton": 1000.0,
    "tonne": 1000.0,
    "tonnes": 1000.0,
    "tons": 1000.0,
    "lb": 0.45359237,
    "lbs": 0.45359237,
    "st": 907.18474,  # US short ton, spelled "st" in some OSM data
}

_DURATION_UNITS: Final[dict[str, float]] = {
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
}

_TABLES: Final[dict[str, dict[str, float]]] = {
    "speed": _SPEED_UNITS,
    "length": _LENGTH_UNITS,
    "mass": _MASS_UNITS,
    "duration": _DURATION_UNITS,
}

#: Unit assumed when a bare number is given, chosen to match OSM conventions.
CANONICAL: Final[dict[str, str]] = {
    "speed": "km/h",
    "length": "m",
    "mass": "kg",
    "duration": "s",
}

_QUANTITY_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?P<value>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(?P<unit>[a-zA-Z/]*)\s*$"
)


def parse_quantity(raw: float | int | str, dimension: str) -> float:
    """Parse ``raw`` into the canonical unit for ``dimension``.

    Bare numbers are accepted and interpreted in the canonical unit, matching how
    OSM writes ``maxspeed=50`` (km/h) and ``maxweight=7.5`` — note the latter is
    tonnes in OSM, so mass specs should always be written with an explicit unit;
    ``parse_mass`` documents that trap.

    :raises UnitError: if the string is unparseable, the unit is unknown for this
        dimension, or the value is not finite.
    """
    table = _TABLES.get(dimension)
    if table is None:
        raise UnitError(f"unknown dimension {dimension!r}")

    if isinstance(raw, bool):  # bool is an int subclass; never a quantity
        raise UnitError(f"expected a {dimension} value, got boolean {raw!r}")

    if isinstance(raw, (int, float)):
        value = float(raw)
        if not math.isfinite(value):
            raise UnitError(f"{dimension} value must be finite, got {raw!r}")
        return value

    if not isinstance(raw, str):
        raise UnitError(f"expected a number or string for {dimension}, got {type(raw).__name__}")

    match = _QUANTITY_RE.match(raw)
    if match is None:
        raise UnitError(
            f"cannot parse {raw!r} as a {dimension}; "
            f"expected e.g. '90 {CANONICAL[dimension]}' or a bare number"
        )

    value = float(match.group("value"))
    unit = match.group("unit").lower()
    if not unit:
        return value

    factor = table.get(unit)
    if factor is None:
        known = ", ".join(sorted(table))
        raise UnitError(f"unknown {dimension} unit {unit!r} in {raw!r}; known units: {known}")
    return value * factor


def parse_speed(raw: float | int | str) -> float:
    """Parse a speed into km/h."""
    return parse_quantity(raw, "speed")


def parse_length(raw: float | int | str) -> float:
    """Parse a length into metres."""
    return parse_quantity(raw, "length")


def parse_mass(raw: float | int | str) -> float:
    """Parse a mass into kilograms.

    A bare number is kilograms here, not tonnes. OSM's ``maxweight`` is tonnes,
    so specs that mirror an OSM tag must write ``18 t`` explicitly; the linter
    flags suspiciously small bare masses to catch the confusion.
    """
    return parse_quantity(raw, "mass")


def parse_duration(raw: float | int | str) -> float:
    """Parse a duration into seconds."""
    return parse_quantity(raw, "duration")


def convert(value: float, dimension: str, to_unit: str) -> float:
    """Convert a canonical value into ``to_unit``.

    :raises UnitError: if ``to_unit`` is not valid for ``dimension``.
    """
    table = _TABLES.get(dimension)
    if table is None:
        raise UnitError(f"unknown dimension {dimension!r}")
    factor = table.get(to_unit.lower())
    if factor is None:
        raise UnitError(f"unknown {dimension} unit {to_unit!r}")
    return value / factor


def format_quantity(value: float, dimension: str, unit: str | None = None, digits: int = 2) -> str:
    """Render a canonical value for display, trimming trailing zeros.

    Used by the diff and simulate renderers so numbers line up in tables instead
    of drifting between ``50.0`` and ``50.00000000001``.
    """
    unit = unit or CANONICAL[dimension]
    out = convert(value, dimension, unit)
    text = f"{out:.{digits}f}".rstrip("0").rstrip(".")
    if text in ("", "-0"):
        text = "0"
    return f"{text} {unit}"


def round_trip(raw: str, dimension: str, unit: str) -> str:
    """Parse then re-render a quantity in ``unit``; used to normalise specs."""
    return format_quantity(parse_quantity(raw, dimension), dimension, unit)
