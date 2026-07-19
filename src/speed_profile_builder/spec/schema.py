"""Declarative schema for a routing profile spec.

This module owns the *document shape* only. It knows nothing about OSRM, about
Valhalla, or about how a speed is eventually applied to a way — that belongs to
:mod:`speed_profile_builder.model` and the emitters. Keeping the boundary sharp
means the YAML surface can grow without the code generators noticing, and vice
versa.

Every quantity field accepts either a bare number in the canonical unit or a
string with an explicit unit (``"90 km/h"``, ``"7.5 t"``, ``"30 s"``), and is
stored canonically (km/h, m, kg, s). ``extra="forbid"`` everywhere is
deliberate: a silently ignored ``speedz:`` key produces a profile that is subtly
wrong after a three-hour graph build, which is the exact failure this tool
exists to prevent.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..units import parse_duration, parse_length, parse_mass, parse_speed

#: Highway values the tool understands. Anything else is accepted but linted,
#: because private tagging schemes exist and hard-failing on them is unhelpful.
KNOWN_HIGHWAYS: tuple[str, ...] = (
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "road",
    "track",
    "path",
    "footway",
    "cycleway",
    "bridleway",
    "steps",
    "pedestrian",
    "ferry",
)

#: Access tag values that grant passage, in the order OSM consumers expect.
POSITIVE_ACCESS: tuple[str, ...] = ("yes", "designated", "permissive", "destination", "delivery")

#: Access tag values that deny passage.
NEGATIVE_ACCESS: tuple[str, ...] = ("no", "private", "agricultural", "forestry", "military")

TransportMode = Literal["car", "truck", "van", "motorcycle", "bicycle", "foot"]

_TIME_RANGE_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)\s*-\s*([01]\d|2[0-3]):([0-5]\d)$")
_DAYS = ("mo", "tu", "we", "th", "fr", "sa", "su")
_IDENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)


def _unbool(value: Any) -> str:
    """Map a YAML 1.1 boolean back to the OSM tag value it was written as."""
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value)


def _speed(v: Any) -> Any:
    return parse_speed(v) if not isinstance(v, bool) else v


def _length(v: Any) -> Any:
    return parse_length(v)


def _mass(v: Any) -> Any:
    return parse_mass(v)


def _duration(v: Any) -> Any:
    return parse_duration(v)


Speed = Annotated[float, Field(gt=0, le=400)]
Length = Annotated[float, Field(gt=0, le=100)]
#: A travel distance rather than a vehicle dimension; metres, up to 2000 km.
Distance = Annotated[float, Field(gt=0, le=2_000_000)]
Mass = Annotated[float, Field(gt=0, le=200_000)]
Duration = Annotated[float, Field(ge=0, le=86_400)]
Multiplier = Annotated[float, Field(gt=0, le=10)]


class _Base(BaseModel):
    """Common config: forbid unknown keys and freeze after construction."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class VehicleSpec(_Base):
    """Physical envelope and legal class of the vehicle being modelled.

    These drive both access filtering (``maxheight``, ``maxweight`` and friends)
    and Valhalla's costing options, which take them as first-class inputs.
    """

    height: Length | None = None
    width: Length | None = None
    length: Length | None = None
    weight: Mass | None = None
    axle_load: Mass | None = None
    axle_count: Annotated[int, Field(ge=1, le=12)] | None = None
    hazmat: bool = False
    trailer: bool = False

    _n_len = field_validator("height", "width", "length", mode="before")(
        staticmethod(lambda v: _length(v) if v is not None else v)
    )
    _n_mass = field_validator("weight", "axle_load", mode="before")(
        staticmethod(lambda v: _mass(v) if v is not None else v)
    )

    @model_validator(mode="after")
    def _axle_load_below_weight(self) -> VehicleSpec:
        if self.axle_load is not None and self.weight is not None and self.axle_load > self.weight:
            raise ValueError(
                f"axle_load ({self.axle_load:.0f} kg) exceeds total weight "
                f"({self.weight:.0f} kg); one of the two is in the wrong unit"
            )
        return self


class SpeedsSpec(_Base):
    """Free-flow speeds by highway class, plus the fallback and legal ceiling."""

    default: Speed = 40.0
    max_legal: Speed | None = None
    highway: dict[str, Speed] = Field(default_factory=dict)
    #: Multiplier applied to every speed after all other factors; the knob
    #: people actually reach for when a whole fleet runs slow.
    global_factor: Multiplier = 1.0

    @field_validator("default", "max_legal", mode="before")
    @classmethod
    def _norm_scalar(cls, v: Any) -> Any:
        return _speed(v) if v is not None else v

    @field_validator("highway", mode="before")
    @classmethod
    def _norm_highway(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        return {str(k): _speed(val) for k, val in v.items() if val is not None}

    @field_validator("highway")
    @classmethod
    def _keys_look_sane(cls, v: dict[str, float]) -> dict[str, float]:
        for key in v:
            if not _IDENT_RE.match(key):
                raise ValueError(
                    f"highway key {key!r} is not a plausible OSM highway value "
                    "(expected e.g. 'motorway', 'residential')"
                )
        return v


class BarrierRule(_Base):
    """What to do when a way crosses a node carrying ``barrier=<key>``."""

    action: Literal["block", "allow", "penalty"] = "block"
    penalty: Duration = 0.0

    _n_pen = field_validator("penalty", mode="before")(staticmethod(_duration))

    @model_validator(mode="after")
    def _penalty_requires_action(self) -> BarrierRule:
        if self.action == "penalty" and self.penalty <= 0:
            raise ValueError("barrier action 'penalty' requires a penalty greater than zero")
        if self.action != "penalty" and self.penalty:
            raise ValueError(f"barrier action {self.action!r} cannot carry a penalty")
        return self


class AccessSpec(_Base):
    """Access tag resolution order and barrier handling.

    ``hierarchy`` is evaluated most-specific-first: the first tag present on the
    way decides, which is how OSRM's stock profiles behave and what mappers
    assume when they add ``hgv=no`` to a road that is otherwise ``access=yes``.
    """

    hierarchy: list[str] = Field(default_factory=lambda: ["motor_vehicle", "vehicle", "access"])
    allowed: list[str] = Field(default_factory=lambda: list(POSITIVE_ACCESS))
    blocked: list[str] = Field(default_factory=lambda: list(NEGATIVE_ACCESS))
    barriers: dict[str, BarrierRule] = Field(default_factory=dict)
    respect_oneway: bool = True
    #: Whether ``access=destination`` roads are usable for through traffic.
    allow_through_destination: bool = False

    @field_validator("allowed", "blocked", "hierarchy", mode="before")
    @classmethod
    def _undo_yaml11_booleans(cls, v: Any) -> Any:
        """Restore ``yes``/``no`` that YAML 1.1 helpfully turned into booleans.

        Unquoted ``yes`` in a YAML list parses as ``True``. That is exactly the
        value an access list needs most often, so silently rejecting it would
        make the most natural spelling of the most common rule fail.
        """
        if isinstance(v, list):
            return [_unbool(item) for item in v]
        return v

    @field_validator("hierarchy")
    @classmethod
    def _hierarchy_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError(
                "access.hierarchy must list at least one tag; an empty hierarchy "
                "makes every access tag unreadable and opens private roads"
            )
        if len(set(v)) != len(v):
            dupes = sorted({t for t in v if v.count(t) > 1})
            raise ValueError(f"access.hierarchy contains duplicate tags: {', '.join(dupes)}")
        return v

    @model_validator(mode="after")
    def _no_overlap(self) -> AccessSpec:
        overlap = sorted(set(self.allowed) & set(self.blocked))
        if overlap:
            raise ValueError(
                f"access values appear in both allowed and blocked: {', '.join(overlap)}"
            )
        return self

    @field_validator("barriers", mode="before")
    @classmethod
    def _shorthand_barriers(cls, v: Any) -> Any:
        """Accept ``gate: block`` as shorthand for ``gate: {action: block}``."""
        if not isinstance(v, dict):
            return v
        out: dict[str, Any] = {}
        for key, val in v.items():
            name = _unbool(key)
            out[name] = {"action": val} if isinstance(val, str) else val
        return out


class TurnSpec(_Base):
    """Turn cost model, including node-level penalties applied at intersections."""

    base_penalty: Duration = 0.0
    u_turn_penalty: Duration = 20.0
    traffic_signal_penalty: Duration = 2.0
    stop_sign_penalty: Duration = 2.0
    crossing_penalty: Duration = 0.0
    #: Extra seconds per 90 degrees of heading change; scaled linearly.
    angle_penalty: Duration = 0.0
    allow_u_turns: bool = True
    restrictions: Literal["obey", "ignore"] = "obey"

    _n = field_validator(
        "base_penalty",
        "u_turn_penalty",
        "traffic_signal_penalty",
        "stop_sign_penalty",
        "crossing_penalty",
        "angle_penalty",
        mode="before",
    )(staticmethod(_duration))

    @model_validator(mode="after")
    def _u_turn_consistency(self) -> TurnSpec:
        if not self.allow_u_turns and self.u_turn_penalty:
            raise ValueError(
                "u_turn_penalty is meaningless when allow_u_turns is false; set one or the other"
            )
        return self


class ZoneSpec(_Base):
    """A restricted or low-emission zone, matched by tag or by polygon.

    Matching by tag is what actually works in an OSRM build (the tag has to be
    on the way); polygons are carried through to Valhalla's exclude regions and
    are emitted as a documented data block for OSRM so a preprocessing step can
    consume them.
    """

    id: str
    action: Literal["penalty", "avoid", "block"] = "penalty"
    penalty: Duration = 0.0
    tag: str | None = None
    value: str | None = None
    polygon: list[tuple[float, float]] | None = None
    description: str = ""

    _n_pen = field_validator("penalty", mode="before")(staticmethod(_duration))

    @field_validator("tag", "value", mode="before")
    @classmethod
    def _unbool_match(cls, v: Any) -> Any:
        """``value: yes`` is an OSM tag value, not a YAML boolean."""
        return v if v is None else _unbool(v)

    @field_validator("id")
    @classmethod
    def _id_is_identifier(cls, v: str) -> str:
        if not _IDENT_RE.match(v):
            raise ValueError(f"zone id {v!r} must be alphanumeric with . _ - separators")
        return v

    @field_validator("polygon")
    @classmethod
    def _polygon_is_closed_ring(
        cls, v: list[tuple[float, float]] | None
    ) -> list[tuple[float, float]] | None:
        if v is None:
            return v
        if len(v) < 3:
            raise ValueError(f"zone polygon needs at least 3 vertices, got {len(v)}")
        for lon, lat in v:
            if not -180 <= lon <= 180 or not -90 <= lat <= 90:
                raise ValueError(
                    f"zone polygon vertex ({lon}, {lat}) is out of range; "
                    "vertices are [longitude, latitude] pairs"
                )
        return v

    @model_validator(mode="after")
    def _needs_a_matcher(self) -> ZoneSpec:
        if self.tag is None and self.polygon is None:
            raise ValueError(f"zone {self.id!r} must define either 'tag' or 'polygon'")
        if self.value is not None and self.tag is None:
            raise ValueError(f"zone {self.id!r} sets 'value' without 'tag'")
        if self.action == "penalty" and self.penalty <= 0:
            raise ValueError(
                f"zone {self.id!r} has action 'penalty' but no penalty; "
                "use action 'avoid' or 'block' to exclude it outright"
            )
        if self.action != "penalty" and self.penalty:
            raise ValueError(f"zone {self.id!r} has action {self.action!r} and cannot take penalty")
        return self


class TimeFactorSpec(_Base):
    """A time-of-day speed multiplier, e.g. an urban AM-peak slowdown."""

    name: str
    factor: Multiplier
    hours: str = "00:00-23:59"
    days: list[str] = Field(default_factory=lambda: ["mo", "tu", "we", "th", "fr", "sa", "su"])
    highway: list[str] = Field(default_factory=list)

    @field_validator("hours")
    @classmethod
    def _hours_range(cls, v: str) -> str:
        if not _TIME_RANGE_RE.match(v):
            raise ValueError(f"hours {v!r} must look like 'HH:MM-HH:MM' in 24-hour time")
        return v

    @field_validator("days", mode="before")
    @classmethod
    def _normalise_days(cls, v: Any) -> Any:
        if not isinstance(v, list):
            return v
        out = []
        for day in v:
            key = str(day).strip().lower()[:2]
            if key not in _DAYS:
                raise ValueError(f"unknown day {day!r}; expected one of {', '.join(_DAYS)}")
            out.append(key)
        return out

    @field_validator("name")
    @classmethod
    def _name_is_identifier(cls, v: str) -> str:
        if not _IDENT_RE.match(v):
            raise ValueError(f"time factor name {v!r} must be alphanumeric with . _ - separators")
        return v


class FerrySpec(_Base):
    """How ``route=ferry`` ways are treated."""

    allowed: bool = True
    speed: Speed = 10.0
    penalty: Duration = 0.0

    _n_speed = field_validator("speed", mode="before")(staticmethod(_speed))
    _n_pen = field_validator("penalty", mode="before")(staticmethod(_duration))


class TollSpec(_Base):
    """Toll road handling: a soft penalty, or a hard exclusion."""

    avoid: bool = False
    penalty: Duration = 0.0

    _n_pen = field_validator("penalty", mode="before")(staticmethod(_duration))


class CargoBikeSpec(_Base):
    """Cargo-bike specifics that ordinary bicycle profiles get wrong.

    A long-tail or box bike cannot use narrow contraflow lanes, cannot be lifted
    over steps, and loses far more speed on a climb than a commuter bike.
    """

    min_width: Length | None = None
    walk_steps: bool = False
    gradient_penalty: Multiplier = 1.0
    max_gradient: Annotated[float, Field(ge=0, le=100)] | None = None

    _n_len = field_validator("min_width", mode="before")(
        staticmethod(lambda v: _length(v) if v is not None else v)
    )


class ChargingSpec(_Base):
    """Electric-fleet range model used by ``ev-delivery``-style profiles."""

    range: Distance | None = None
    reserve_fraction: Annotated[float, Field(ge=0, le=0.9)] = 0.2
    recharge_penalty: Duration = 0.0

    _n_len = field_validator("range", mode="before")(
        staticmethod(lambda v: _length(v) if v is not None else v)
    )
    _n_pen = field_validator("recharge_penalty", mode="before")(staticmethod(_duration))


class ExtrasSpec(_Base):
    """Mode-specific extras that do not fit the common vehicle model."""

    ferry: FerrySpec = Field(default_factory=FerrySpec)
    toll: TollSpec = Field(default_factory=TollSpec)
    cargo_bike: CargoBikeSpec | None = None
    charging: ChargingSpec | None = None


class ProfileSpec(_Base):
    """A complete, already-resolved profile document.

    ``extends`` is resolved by :mod:`speed_profile_builder.spec.loader` *before*
    this model is constructed, so a validated ``ProfileSpec`` is always
    self-contained. That ordering matters: validating each layer separately
    would reject legal partial overrides.
    """

    version: Literal[1] = 1
    name: str
    mode: TransportMode = "car"
    description: str = ""
    extends: str | None = None
    vehicle: VehicleSpec = Field(default_factory=VehicleSpec)
    speeds: SpeedsSpec = Field(default_factory=SpeedsSpec)
    surfaces: dict[str, Multiplier] = Field(default_factory=dict)
    tracktypes: dict[str, Multiplier] = Field(default_factory=dict)
    smoothness: dict[str, Multiplier] = Field(default_factory=dict)
    access: AccessSpec = Field(default_factory=AccessSpec)
    turn: TurnSpec = Field(default_factory=TurnSpec)
    zones: list[ZoneSpec] = Field(default_factory=list)
    time_factors: list[TimeFactorSpec] = Field(default_factory=list)
    extras: ExtrasSpec = Field(default_factory=ExtrasSpec)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name_is_identifier(cls, v: str) -> str:
        if not _IDENT_RE.match(v):
            raise ValueError(
                f"profile name {v!r} must be alphanumeric with . _ - separators "
                "(it becomes the generated filename)"
            )
        return v

    @field_validator("tracktypes")
    @classmethod
    def _tracktype_keys(cls, v: dict[str, float]) -> dict[str, float]:
        for key in v:
            if not re.match(r"^grade[1-5]$", key):
                raise ValueError(f"tracktype key {key!r} must be one of grade1..grade5")
        return v

    @model_validator(mode="after")
    def _unique_collection_keys(self) -> ProfileSpec:
        for field_name, key in (("zones", "id"), ("time_factors", "name")):
            items = getattr(self, field_name)
            seen = [getattr(i, key) for i in items]
            dupes = sorted({s for s in seen if seen.count(s) > 1})
            if dupes:
                raise ValueError(f"duplicate {field_name} {key}(s): {', '.join(dupes)}")
        return self

    @model_validator(mode="after")
    def _mode_consistency(self) -> ProfileSpec:
        if self.extras.cargo_bike is not None and self.mode != "bicycle":
            raise ValueError(
                f"extras.cargo_bike is only valid for mode 'bicycle', not {self.mode!r}"
            )
        if self.mode in ("bicycle", "foot") and self.vehicle.weight is not None:
            raise ValueError(f"vehicle.weight is not meaningful for mode {self.mode!r}")
        return self


__all__ = [
    "KNOWN_HIGHWAYS",
    "NEGATIVE_ACCESS",
    "POSITIVE_ACCESS",
    "AccessSpec",
    "BarrierRule",
    "CargoBikeSpec",
    "ChargingSpec",
    "ExtrasSpec",
    "FerrySpec",
    "ProfileSpec",
    "SpeedsSpec",
    "TimeFactorSpec",
    "TollSpec",
    "TransportMode",
    "TurnSpec",
    "VehicleSpec",
    "ZoneSpec",
]
