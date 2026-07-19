"""Normalised intermediate representation of a routing profile.

The IR sits between the YAML surface and the code generators. It exists for
three reasons:

* **Emitters need certainty.** A ``ProfileSpec`` has optional fields and
  shorthand forms; a :class:`Profile` has every value resolved, every lookup
  table populated, and every time window reduced to minutes-since-midnight.
* **Diff and lint need a flat view.** Comparing two nested pydantic trees is
  awkward; comparing two ordered ``{dotted.key: scalar}`` maps is trivial and
  produces stable, reviewable output. :meth:`Profile.flatten` provides that.
* **Codegen must be deterministic.** Every collection here is stored in sorted
  or declaration order, never in set iteration order, so building the same spec
  twice produces identical bytes.

The IR is engine-agnostic: no OSRM or Valhalla vocabulary appears in this file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .spec.schema import ProfileSpec

#: Speed applied when a way matches nothing at all and no default is usable.
ABSOLUTE_FALLBACK_SPEED = 5.0


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


@dataclass(frozen=True)
class Vehicle:
    """Resolved physical envelope. ``None`` means "not constrained"."""

    height_m: float | None = None
    width_m: float | None = None
    length_m: float | None = None
    weight_kg: float | None = None
    axle_load_kg: float | None = None
    axle_count: int | None = None
    hazmat: bool = False
    trailer: bool = False

    @property
    def weight_t(self) -> float | None:
        """Weight in tonnes, the unit Valhalla's costing options expect."""
        return None if self.weight_kg is None else self.weight_kg / 1000.0

    @property
    def axle_load_t(self) -> float | None:
        """Axle load in tonnes, for Valhalla."""
        return None if self.axle_load_kg is None else self.axle_load_kg / 1000.0

    @property
    def is_constrained(self) -> bool:
        """Whether any dimension restricts which ways the vehicle may use."""
        return any(
            v is not None
            for v in (self.height_m, self.width_m, self.length_m, self.weight_kg, self.axle_load_kg)
        )


@dataclass(frozen=True)
class Barrier:
    """Resolved barrier handling for one ``barrier=*`` value."""

    key: str
    action: str
    penalty_s: float = 0.0


@dataclass(frozen=True)
class Access:
    """Resolved access model."""

    hierarchy: tuple[str, ...]
    allowed: tuple[str, ...]
    blocked: tuple[str, ...]
    barriers: tuple[Barrier, ...]
    respect_oneway: bool
    allow_through_destination: bool

    def verdict(self, value: str) -> str | None:
        """Classify an access tag value as ``allow``/``deny``, or ``None``.

        ``None`` means "this value says nothing", which is different from
        "denied" — an unrecognised value must fall through to the next tag in
        the hierarchy rather than blocking the way.
        """
        if value in self.allowed:
            return "allow"
        if value in self.blocked:
            return "deny"
        return None


@dataclass(frozen=True)
class Turn:
    """Resolved turn and node penalties, all in seconds."""

    base_penalty_s: float
    u_turn_penalty_s: float
    traffic_signal_penalty_s: float
    stop_sign_penalty_s: float
    crossing_penalty_s: float
    angle_penalty_s: float
    allow_u_turns: bool
    obey_restrictions: bool


@dataclass(frozen=True)
class Zone:
    """Resolved restricted zone."""

    id: str
    action: str
    penalty_s: float
    tag: str | None
    value: str | None
    polygon: tuple[tuple[float, float], ...] | None
    description: str

    def matches_tags(self, tags: dict[str, str]) -> bool:
        """Whether a way's tags place it inside this zone.

        Polygon zones never match on tags alone — deciding that requires
        geometry the profile does not carry — so they are reported separately by
        the simulator instead of silently doing nothing.
        """
        if self.tag is None:
            return False
        present = tags.get(self.tag)
        if present is None:
            return False
        if self.value is None:
            return present not in ("no", "false", "0")
        return present == self.value


@dataclass(frozen=True)
class TimeFactor:
    """Resolved time-of-day multiplier with the window in minutes."""

    name: str
    factor: float
    start_min: int
    end_min: int
    days: tuple[str, ...]
    highway: tuple[str, ...]

    def applies(self, day: str, minute: int, highway: str) -> bool:
        """Whether this factor is active for a given day/minute/highway class."""
        if day.lower()[:2] not in self.days:
            return False
        if self.highway and highway not in self.highway:
            return False
        if self.start_min <= self.end_min:
            return self.start_min <= minute <= self.end_min
        # Window wraps past midnight (e.g. 22:00-05:00).
        return minute >= self.start_min or minute <= self.end_min


@dataclass(frozen=True)
class Ferry:
    """Resolved ferry handling."""

    allowed: bool
    speed_kmh: float
    penalty_s: float


@dataclass(frozen=True)
class Toll:
    """Resolved toll handling."""

    avoid: bool
    penalty_s: float


@dataclass(frozen=True)
class CargoBike:
    """Resolved cargo-bike constraints."""

    min_width_m: float | None
    walk_steps: bool
    gradient_penalty: float
    max_gradient: float | None


@dataclass(frozen=True)
class Charging:
    """Resolved electric-range model."""

    range_m: float | None
    reserve_fraction: float
    recharge_penalty_s: float


@dataclass(frozen=True)
class Profile:
    """A fully resolved, engine-agnostic routing profile."""

    name: str
    mode: str
    description: str
    default_speed_kmh: float
    max_legal_speed_kmh: float | None
    global_factor: float
    speeds_kmh: dict[str, float]
    surfaces: dict[str, float]
    tracktypes: dict[str, float]
    smoothness: dict[str, float]
    vehicle: Vehicle
    access: Access
    turn: Turn
    zones: tuple[Zone, ...]
    time_factors: tuple[TimeFactor, ...]
    ferry: Ferry
    toll: Toll
    cargo_bike: CargoBike | None
    charging: Charging | None
    metadata: dict[str, str] = field(default_factory=dict)
    source_chain: tuple[str, ...] = ()

    def base_speed(self, highway: str) -> float:
        """Free-flow speed for a highway class, falling back to the default."""
        return self.speeds_kmh.get(highway, self.default_speed_kmh)

    def surface_factor(self, surface: str | None) -> float:
        """Multiplier for a ``surface=*`` value; unlisted surfaces are neutral."""
        return 1.0 if surface is None else self.surfaces.get(surface, 1.0)

    def tracktype_factor(self, tracktype: str | None) -> float:
        """Multiplier for a ``tracktype=*`` value; unlisted grades are neutral."""
        return 1.0 if tracktype is None else self.tracktypes.get(tracktype, 1.0)

    def smoothness_factor(self, smoothness: str | None) -> float:
        """Multiplier for a ``smoothness=*`` value; unlisted values are neutral."""
        return 1.0 if smoothness is None else self.smoothness.get(smoothness, 1.0)

    def cap(self, speed: float) -> float:
        """Clamp a speed to the legal ceiling and to a non-zero minimum.

        A zero or negative speed would divide by zero downstream in both
        engines, so the floor is enforced here rather than in each emitter.
        """
        if self.max_legal_speed_kmh is not None:
            speed = min(speed, self.max_legal_speed_kmh)
        return max(speed, ABSOLUTE_FALLBACK_SPEED)

    def zone_by_id(self, zone_id: str) -> Zone | None:
        """Look up a zone by id, or ``None``."""
        return next((z for z in self.zones if z.id == zone_id), None)

    def flatten(self) -> dict[str, Any]:
        """Flatten the profile into an ordered ``{dotted.key: scalar}`` mapping.

        This is the canonical comparable form used by ``diff``. Keys are stable
        and sorted within each collection so a diff never reports spurious
        reordering.
        """
        out: dict[str, Any] = {
            "mode": self.mode,
            "speeds.default": self.default_speed_kmh,
            "speeds.max_legal": self.max_legal_speed_kmh,
            "speeds.global_factor": self.global_factor,
        }
        for key in sorted(self.speeds_kmh):
            out[f"speeds.highway.{key}"] = self.speeds_kmh[key]
        for group, table in (
            ("surfaces", self.surfaces),
            ("tracktypes", self.tracktypes),
            ("smoothness", self.smoothness),
        ):
            for key in sorted(table):
                out[f"{group}.{key}"] = table[key]
        for attr in (
            "height_m",
            "width_m",
            "length_m",
            "weight_kg",
            "axle_load_kg",
            "axle_count",
            "hazmat",
            "trailer",
        ):
            out[f"vehicle.{attr}"] = getattr(self.vehicle, attr)
        out["access.hierarchy"] = ",".join(self.access.hierarchy)
        out["access.allowed"] = ",".join(sorted(self.access.allowed))
        out["access.blocked"] = ",".join(sorted(self.access.blocked))
        out["access.respect_oneway"] = self.access.respect_oneway
        out["access.allow_through_destination"] = self.access.allow_through_destination
        for barrier in sorted(self.access.barriers, key=lambda b: b.key):
            out[f"access.barriers.{barrier.key}"] = (
                f"{barrier.action}:{barrier.penalty_s:g}" if barrier.penalty_s else barrier.action
            )
        for attr in (
            "base_penalty_s",
            "u_turn_penalty_s",
            "traffic_signal_penalty_s",
            "stop_sign_penalty_s",
            "crossing_penalty_s",
            "angle_penalty_s",
            "allow_u_turns",
            "obey_restrictions",
        ):
            out[f"turn.{attr}"] = getattr(self.turn, attr)
        for zone in sorted(self.zones, key=lambda z: z.id):
            out[f"zones.{zone.id}.action"] = zone.action
            out[f"zones.{zone.id}.penalty_s"] = zone.penalty_s
            out[f"zones.{zone.id}.match"] = (
                f"{zone.tag}={zone.value}"
                if zone.tag and zone.value
                else (zone.tag or f"polygon[{len(zone.polygon or ())}]")
            )
        for tf in sorted(self.time_factors, key=lambda t: t.name):
            out[f"time_factors.{tf.name}.factor"] = tf.factor
            out[f"time_factors.{tf.name}.window"] = f"{tf.start_min}-{tf.end_min}"
            out[f"time_factors.{tf.name}.days"] = ",".join(tf.days)
            out[f"time_factors.{tf.name}.highway"] = ",".join(sorted(tf.highway)) or "*"
        out["extras.ferry.allowed"] = self.ferry.allowed
        out["extras.ferry.speed_kmh"] = self.ferry.speed_kmh
        out["extras.ferry.penalty_s"] = self.ferry.penalty_s
        out["extras.toll.avoid"] = self.toll.avoid
        out["extras.toll.penalty_s"] = self.toll.penalty_s
        if self.cargo_bike is not None:
            for attr in ("min_width_m", "walk_steps", "gradient_penalty", "max_gradient"):
                out[f"extras.cargo_bike.{attr}"] = getattr(self.cargo_bike, attr)
        if self.charging is not None:
            for attr in ("range_m", "reserve_fraction", "recharge_penalty_s"):
                out[f"extras.charging.{attr}"] = getattr(self.charging, attr)
        return out

    def fingerprint(self) -> str:
        """Stable SHA-256 of the semantic content, excluding provenance.

        Generated files embed this so a reviewer can tell whether a regenerated
        artefact reflects a real change or only a moved source file.
        """
        payload = json.dumps(self.flatten(), sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_profile(spec: ProfileSpec, source_chain: list[Path] | None = None) -> Profile:
    """Lower a validated :class:`ProfileSpec` into the normalised IR.

    This is the only place spec vocabulary is translated into IR vocabulary; the
    emitters read the IR and never touch the pydantic models.
    """
    vehicle = Vehicle(
        height_m=spec.vehicle.height,
        width_m=spec.vehicle.width,
        length_m=spec.vehicle.length,
        weight_kg=spec.vehicle.weight,
        axle_load_kg=spec.vehicle.axle_load,
        axle_count=spec.vehicle.axle_count,
        hazmat=spec.vehicle.hazmat,
        trailer=spec.vehicle.trailer,
    )
    access = Access(
        hierarchy=tuple(spec.access.hierarchy),
        allowed=tuple(spec.access.allowed),
        blocked=tuple(spec.access.blocked),
        barriers=tuple(
            Barrier(key=key, action=rule.action, penalty_s=rule.penalty)
            for key, rule in sorted(spec.access.barriers.items())
        ),
        respect_oneway=spec.access.respect_oneway,
        allow_through_destination=spec.access.allow_through_destination,
    )
    turn = Turn(
        base_penalty_s=spec.turn.base_penalty,
        u_turn_penalty_s=spec.turn.u_turn_penalty,
        traffic_signal_penalty_s=spec.turn.traffic_signal_penalty,
        stop_sign_penalty_s=spec.turn.stop_sign_penalty,
        crossing_penalty_s=spec.turn.crossing_penalty,
        angle_penalty_s=spec.turn.angle_penalty,
        allow_u_turns=spec.turn.allow_u_turns,
        obey_restrictions=spec.turn.restrictions == "obey",
    )
    zones = tuple(
        Zone(
            id=z.id,
            action=z.action,
            penalty_s=z.penalty,
            tag=z.tag,
            value=z.value,
            polygon=tuple((lon, lat) for lon, lat in z.polygon) if z.polygon else None,
            description=z.description,
        )
        for z in spec.zones
    )
    time_factors = tuple(
        TimeFactor(
            name=tf.name,
            factor=tf.factor,
            start_min=_minutes(tf.hours.split("-")[0].strip()),
            end_min=_minutes(tf.hours.split("-")[1].strip()),
            days=tuple(tf.days),
            highway=tuple(tf.highway),
        )
        for tf in spec.time_factors
    )
    return Profile(
        name=spec.name,
        mode=spec.mode,
        description=spec.description,
        default_speed_kmh=spec.speeds.default,
        max_legal_speed_kmh=spec.speeds.max_legal,
        global_factor=spec.speeds.global_factor,
        speeds_kmh=dict(sorted(spec.speeds.highway.items())),
        surfaces=dict(sorted(spec.surfaces.items())),
        tracktypes=dict(sorted(spec.tracktypes.items())),
        smoothness=dict(sorted(spec.smoothness.items())),
        vehicle=vehicle,
        access=access,
        turn=turn,
        zones=zones,
        time_factors=time_factors,
        ferry=Ferry(
            allowed=spec.extras.ferry.allowed,
            speed_kmh=spec.extras.ferry.speed,
            penalty_s=spec.extras.ferry.penalty,
        ),
        toll=Toll(avoid=spec.extras.toll.avoid, penalty_s=spec.extras.toll.penalty),
        cargo_bike=(
            None
            if spec.extras.cargo_bike is None
            else CargoBike(
                min_width_m=spec.extras.cargo_bike.min_width,
                walk_steps=spec.extras.cargo_bike.walk_steps,
                gradient_penalty=spec.extras.cargo_bike.gradient_penalty,
                max_gradient=spec.extras.cargo_bike.max_gradient,
            )
        ),
        charging=(
            None
            if spec.extras.charging is None
            else Charging(
                range_m=spec.extras.charging.range,
                reserve_fraction=spec.extras.charging.reserve_fraction,
                recharge_penalty_s=spec.extras.charging.recharge_penalty,
            )
        ),
        metadata=dict(sorted(spec.metadata.items())),
        source_chain=tuple(str(p) for p in (source_chain or ())),
    )
