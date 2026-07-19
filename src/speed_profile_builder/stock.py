"""Built-in approximations of the engines' stock profiles, for diffing.

The point of ``speed-profile diff`` is to answer "what did I actually change
relative to what the engine would have done anyway". That needs a baseline, and
shipping one avoids making every user clone OSRM's source tree to read
``car.lua``.

These are *approximations*, transcribed from the stock profiles and Valhalla's
documented costing defaults, and they say so in their descriptions. They are
pinned data, not a live import: a diff that changes because upstream edited a
comment would be worse than useless. Where a number is genuinely absent from an
engine (Valhalla has no per-class speed table) the nearest honest stand-in is
used and noted.
"""

from __future__ import annotations

from typing import Any

from .errors import SpecError, SpecIssue
from .model import Profile, build_profile
from .spec.loader import validate_document

_CAR_SURFACES = {
    "asphalt": 1.0,
    "concrete": 1.0,
    "paved": 1.0,
    "cobblestone": 0.5,
    "compacted": 0.8,
    "gravel": 0.6,
    "unpaved": 0.6,
    "ground": 0.5,
    "dirt": 0.5,
    "sand": 0.3,
    "mud": 0.4,
    "grass": 0.4,
}

#: Stock baselines keyed by the name users pass to ``--against``.
STOCK_DOCUMENTS: dict[str, dict[str, Any]] = {
    "car": {
        "version": 1,
        "name": "stock-car",
        "mode": "car",
        "description": "Approximation of the OSRM stock car.lua speed table and penalties.",
        "speeds": {
            "default": 10,
            "highway": {
                "motorway": 90,
                "motorway_link": 45,
                "trunk": 85,
                "trunk_link": 40,
                "primary": 65,
                "primary_link": 30,
                "secondary": 55,
                "secondary_link": 25,
                "tertiary": 40,
                "tertiary_link": 20,
                "unclassified": 25,
                "residential": 25,
                "living_street": 10,
                "service": 15,
            },
        },
        "surfaces": _CAR_SURFACES,
        "tracktypes": {"grade1": 1.0, "grade2": 0.75, "grade3": 0.6, "grade4": 0.5, "grade5": 0.4},
        "smoothness": {"intermediate": 0.8, "bad": 0.4, "very_bad": 0.2, "horrible": 0.1},
        "access": {
            "hierarchy": ["motorcar", "motor_vehicle", "vehicle", "access"],
            "barriers": {"gate": "block", "bollard": "block", "lift_gate": "allow"},
        },
        "turn": {
            "base_penalty": 7.5,
            "u_turn_penalty": 20,
            "traffic_signal_penalty": 2,
            "stop_sign_penalty": 2,
        },
        "extras": {"ferry": {"allowed": True, "speed": 5, "penalty": 0}},
    },
    "bicycle": {
        "version": 1,
        "name": "stock-bicycle",
        "mode": "bicycle",
        "description": "Approximation of the OSRM stock bicycle.lua speeds and access rules.",
        "speeds": {
            "default": 15,
            "highway": {
                "cycleway": 18,
                "primary": 15,
                "primary_link": 15,
                "secondary": 15,
                "secondary_link": 15,
                "tertiary": 15,
                "tertiary_link": 15,
                "residential": 15,
                "unclassified": 15,
                "living_street": 10,
                "road": 12,
                "service": 12,
                "track": 12,
                "path": 12,
                "footway": 6,
                "pedestrian": 6,
                "steps": 2,
            },
        },
        "surfaces": {"asphalt": 1.0, "cobblestone": 0.5, "gravel": 0.6, "unpaved": 0.6},
        "access": {
            "hierarchy": ["bicycle", "vehicle", "access"],
            "allow_through_destination": True,
            "barriers": {"cycle_barrier": "allow", "gate": "allow", "bollard": "allow"},
        },
        "turn": {"base_penalty": 0, "u_turn_penalty": 20, "traffic_signal_penalty": 2},
        "extras": {"ferry": {"allowed": True, "speed": 5, "penalty": 0}},
    },
    "foot": {
        "version": 1,
        "name": "stock-foot",
        "mode": "foot",
        "description": "Approximation of the OSRM stock foot.lua: a flat 5 km/h walking speed.",
        "speeds": {
            "default": 5,
            "highway": {
                "primary": 5,
                "secondary": 5,
                "tertiary": 5,
                "residential": 5,
                "unclassified": 5,
                "living_street": 5,
                "service": 5,
                "footway": 5,
                "path": 5,
                "pedestrian": 5,
                "steps": 2,
                "track": 5,
                "cycleway": 5,
            },
        },
        "access": {
            "hierarchy": ["foot", "access"],
            "allow_through_destination": True,
            "barriers": {"gate": "allow", "stile": "allow", "kissing_gate": "allow"},
        },
        "turn": {"base_penalty": 0, "u_turn_penalty": 0, "allow_u_turns": True},
        "extras": {"ferry": {"allowed": True, "speed": 5, "penalty": 0}},
    },
    "truck": {
        "version": 1,
        "name": "stock-truck",
        "mode": "truck",
        "description": (
            "Valhalla stock truck costing defaults. Valhalla has no per-class speed table, "
            "so speeds here are the stock car values capped at the default 90 km/h top speed."
        ),
        "vehicle": {
            "height": "4.11 m",
            "width": "2.6 m",
            "length": "21.64 m",
            "weight": "21.77 t",
            "axle_load": "9.07 t",
            "axle_count": 5,
            "hazmat": False,
        },
        "speeds": {
            "default": 10,
            "max_legal": 90,
            "highway": {
                "motorway": 90,
                "motorway_link": 45,
                "trunk": 85,
                "trunk_link": 40,
                "primary": 65,
                "primary_link": 30,
                "secondary": 55,
                "secondary_link": 25,
                "tertiary": 40,
                "tertiary_link": 20,
                "unclassified": 25,
                "residential": 25,
                "living_street": 10,
                "service": 15,
            },
        },
        "surfaces": _CAR_SURFACES,
        "access": {
            "hierarchy": ["hgv", "motor_vehicle", "vehicle", "access"],
            "barriers": {"gate": "block", "bollard": "block"},
        },
        "turn": {"base_penalty": 5, "u_turn_penalty": 20},
        "extras": {"ferry": {"allowed": True, "speed": 5, "penalty": 300}},
    },
}

#: Which stock baseline is the natural comparison for each transport mode.
DEFAULT_STOCK_FOR_MODE: dict[str, str] = {
    "car": "car",
    "van": "car",
    "motorcycle": "car",
    "truck": "truck",
    "bicycle": "bicycle",
    "foot": "foot",
}


def stock_names() -> list[str]:
    """Names accepted by ``--against``."""
    return sorted(STOCK_DOCUMENTS)


def stock_profile(name: str) -> Profile:
    """Build the named stock baseline as a normalised :class:`Profile`.

    :raises SpecError: if ``name`` is not a known baseline.
    """
    document = STOCK_DOCUMENTS.get(name)
    if document is None:
        raise SpecError(
            SpecIssue(
                message=f"no stock profile named {name!r}",
                hint=f"available: {', '.join(stock_names())}",
            )
        )
    spec = validate_document(dict(document))
    return build_profile(spec)


def stock_for_mode(mode: str) -> Profile:
    """Return the stock baseline that best matches ``mode``."""
    return stock_profile(DEFAULT_STOCK_FOR_MODE.get(mode, "car"))
