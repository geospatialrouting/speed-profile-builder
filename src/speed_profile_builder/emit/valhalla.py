"""Emit a Valhalla costing document from the normalised IR.

Valhalla is configured per *request* rather than per graph build, so the output
here is a JSON document that can be merged straight into a ``/route`` body: a
``costing`` name, a ``costing_options`` block, and — where the spec defines
polygon zones — ``exclude_polygons``.

Valhalla's costing model is not a superset of OSRM's, and pretending otherwise
would produce silently wrong routes. Two honest compromises are made:

* Valhalla has no per-highway speed table in costing options; speeds come from
  the graph. The spec's per-class speeds are therefore carried in an
  underscore-prefixed ``_speed_profile`` extension block (Valhalla ignores
  unknown keys) so a traffic-injection or graph-build step can consume them,
  and the closest expressible knobs (``top_speed``, ``use_highways``,
  ``use_tracks``, ``use_living_streets``) are set from them.
* Second-denominated penalties map cleanly onto Valhalla's ``*_penalty`` and
  ``*_cost`` options, which is why zone and barrier penalties survive here
  intact while OSRM has to approximate them.
"""

from __future__ import annotations

import json
from typing import Any

from ..model import Profile
from .common import EmitOptions, provenance

#: Spec mode -> Valhalla costing name.
COSTING_FOR_MODE: dict[str, str] = {
    "car": "auto",
    "van": "auto",
    "truck": "truck",
    "motorcycle": "motorcycle",
    "bicycle": "bicycle",
    "foot": "pedestrian",
}

_ROUNDING = 4


def _r(value: float) -> float:
    """Round for deterministic JSON output across platforms."""
    rounded = round(float(value), _ROUNDING)
    return int(rounded) if rounded == int(rounded) else rounded


def _use_scale(penalty_s: float, enabled: bool = True) -> float:
    """Map a seconds penalty onto Valhalla's 0..1 "use X" preference scale.

    Valhalla's ``use_ferry`` / ``use_tolls`` / ``use_highways`` are preferences,
    not costs: 0 avoids, 1 favours, 0.5 is neutral. A spec that merely penalises
    something should nudge the preference down, not slam it to zero, so the
    mapping decays from the neutral 0.5 and never quite reaches 0.
    """
    if not enabled:
        return 0.0
    if penalty_s <= 0:
        return 0.5
    return _r(0.5 / (1.0 + penalty_s / 600.0))


def _barrier_costs(profile: Profile) -> dict[str, Any]:
    """Translate barrier rules into Valhalla's gate/bollard cost options.

    Valhalla exposes only a handful of named barrier knobs, so unnamed barriers
    fall back to the generic ``gate_*`` pair — which is exactly what Valhalla
    itself applies to them.
    """
    out: dict[str, Any] = {}
    named = {"gate": ("gate_cost", "gate_penalty"), "bollard": ("bollard_cost", "bollard_penalty")}
    for barrier in profile.access.barriers:
        keys = named.get(barrier.key)
        if keys is None:
            continue
        cost_key, penalty_key = keys
        if barrier.action == "block":
            out[cost_key] = 0
            out[penalty_key] = 43200  # effectively impassable, still finite
        elif barrier.action == "allow":
            out[cost_key] = 0
            out[penalty_key] = 0
        else:
            out[cost_key] = _r(barrier.penalty_s)
            out[penalty_key] = 0
    return out


def _zone_penalty(profile: Profile) -> float:
    """Total seconds charged by tag-matched penalty zones.

    Valhalla has no per-tag penalty hook, so tag zones are summed into
    ``destination_only_penalty``-adjacent territory via ``_speed_profile``; the
    aggregate is surfaced here so the number is at least visible in the diff.
    """
    return sum(z.penalty_s for z in profile.zones if z.action == "penalty" and z.tag)


def _mode_options(profile: Profile) -> dict[str, Any]:
    """Costing options that only apply to some transport modes."""
    out: dict[str, Any] = {}
    mode = profile.mode
    if mode in ("car", "van", "truck", "motorcycle"):
        veh = profile.vehicle
        if veh.height_m is not None:
            out["height"] = _r(veh.height_m)
        if veh.width_m is not None:
            out["width"] = _r(veh.width_m)
        if mode == "truck":
            if veh.length_m is not None:
                out["length"] = _r(veh.length_m)
            if veh.weight_t is not None:
                out["weight"] = _r(veh.weight_t)
            if veh.axle_load_t is not None:
                out["axle_load"] = _r(veh.axle_load_t)
            if veh.axle_count is not None:
                out["axle_count"] = veh.axle_count
            out["hazmat"] = veh.hazmat
    elif mode == "bicycle":
        out["bicycle_type"] = "Cargo" if profile.cargo_bike else "Hybrid"
        out["cycling_speed"] = _r(profile.default_speed_kmh)
        out["avoid_bad_surfaces"] = _r(_avoid_bad_surfaces(profile))
        if profile.cargo_bike is not None:
            out["use_hills"] = _r(max(0.0, 1.0 / profile.cargo_bike.gradient_penalty - 0.5))
            out["use_roads"] = 0.25
    elif mode == "foot":
        out["walking_speed"] = _r(profile.default_speed_kmh)
        out["max_hiking_difficulty"] = 1
    return out


def _avoid_bad_surfaces(profile: Profile) -> float:
    """Derive Valhalla's 0..1 bad-surface aversion from the surface factors.

    Nothing in Valhalla accepts a surface multiplier table, so the spec's
    intent is compressed into the single knob that exists: the harsher the
    unpaved multipliers, the closer to 1 (fully avoid) the result.
    """
    unpaved = [
        profile.surfaces[k]
        for k in ("gravel", "dirt", "ground", "unpaved", "sand", "mud", "grass")
        if k in profile.surfaces
    ]
    if not unpaved:
        return 0.25
    worst = min(unpaved)
    return max(0.0, min(1.0, 1.0 - worst))


def build_document(profile: Profile, options: EmitOptions | None = None) -> dict[str, Any]:
    """Build the Valhalla document as a plain dict (useful for tests and diffs)."""
    options = options or EmitOptions()
    costing = COSTING_FOR_MODE[profile.mode]
    turn = profile.turn

    costing_options: dict[str, Any] = {
        "maneuver_penalty": _r(turn.base_penalty_s),
        "destination_only_penalty": 0 if profile.access.allow_through_destination else 600,
        "use_ferry": _use_scale(profile.ferry.penalty_s, profile.ferry.allowed),
        "use_tolls": _use_scale(profile.toll.penalty_s, not profile.toll.avoid),
        "shortest": False,
        "ignore_restrictions": not turn.obey_restrictions,
    }
    if profile.max_legal_speed_kmh is not None and profile.mode != "foot":
        costing_options["top_speed"] = _r(profile.max_legal_speed_kmh)
    if turn.stop_sign_penalty_s:
        costing_options["stop_sign_penalty"] = _r(turn.stop_sign_penalty_s)
    if turn.traffic_signal_penalty_s:
        costing_options["traffic_signal_penalty"] = _r(turn.traffic_signal_penalty_s)
    if profile.ferry.allowed:
        costing_options["ferry_cost"] = _r(profile.ferry.penalty_s)
    if profile.toll.penalty_s:
        costing_options["toll_booth_cost"] = _r(profile.toll.penalty_s)
    if profile.speeds_kmh:
        costing_options["use_highways"] = _r(_use_highways(profile))
        costing_options["use_tracks"] = _r(_use_tracks(profile))
        costing_options["use_living_streets"] = _r(_use_living_streets(profile))
    costing_options.update(_barrier_costs(profile))
    costing_options.update(_mode_options(profile))

    document: dict[str, Any] = {
        "_generated": {
            "tool": "speed-profile-builder",
            "version": options.tool_version or "unknown",
            "profile": profile.name,
            "mode": profile.mode,
            "spec_chain": [p.rsplit("/", 1)[-1] for p in profile.source_chain]
            or [f"{profile.name}.yaml"],
            "fingerprint": profile.fingerprint(),
            "warning": "generated file - edit the spec and re-run 'speed-profile build'",
        },
        "costing": costing,
        "costing_options": {costing: dict(sorted(costing_options.items()))},
    }

    excluded = [z for z in profile.zones if z.polygon and z.action in ("avoid", "block")]
    if excluded:
        document["exclude_polygons"] = [
            [[_r(lon), _r(lat)] for lon, lat in zone.polygon or ()] for zone in excluded
        ]

    extension: dict[str, Any] = {
        "note": (
            "non-standard block; Valhalla ignores unknown keys. Consumed by "
            "graph-build or traffic-injection steps that can express what "
            "costing options cannot."
        ),
        "speeds_kmh": dict(sorted(profile.speeds_kmh.items())),
        "default_speed_kmh": _r(profile.default_speed_kmh),
        "global_factor": _r(profile.global_factor),
    }
    if profile.surfaces:
        extension["surface_factors"] = {k: _r(v) for k, v in sorted(profile.surfaces.items())}
    if profile.tracktypes:
        extension["tracktype_factors"] = {k: _r(v) for k, v in sorted(profile.tracktypes.items())}
    if profile.smoothness:
        extension["smoothness_factors"] = {k: _r(v) for k, v in sorted(profile.smoothness.items())}
    if profile.zones:
        extension["zones"] = [
            {
                "id": z.id,
                "action": z.action,
                "penalty_s": _r(z.penalty_s),
                **({"tag": z.tag} if z.tag else {}),
                **({"value": z.value} if z.value else {}),
                **({"polygon": [[_r(a), _r(b)] for a, b in z.polygon]} if z.polygon else {}),
            }
            for z in sorted(profile.zones, key=lambda z: z.id)
        ]
        extension["tag_zone_penalty_total_s"] = _r(_zone_penalty(profile))
    if profile.time_factors:
        extension["time_factors"] = [
            {
                "name": t.name,
                "factor": _r(t.factor),
                "start_min": t.start_min,
                "end_min": t.end_min,
                "days": list(t.days),
                "highway": list(t.highway),
            }
            for t in sorted(profile.time_factors, key=lambda t: t.name)
        ]
    if profile.charging is not None:
        extension["charging"] = {
            "range_m": None if profile.charging.range_m is None else _r(profile.charging.range_m),
            "reserve_fraction": _r(profile.charging.reserve_fraction),
            "recharge_penalty_s": _r(profile.charging.recharge_penalty_s),
        }
    if profile.cargo_bike is not None:
        extension["cargo_bike"] = {
            "min_width_m": (
                None
                if profile.cargo_bike.min_width_m is None
                else _r(profile.cargo_bike.min_width_m)
            ),
            "walk_steps": profile.cargo_bike.walk_steps,
            "gradient_penalty": _r(profile.cargo_bike.gradient_penalty),
            "max_gradient": profile.cargo_bike.max_gradient,
        }
    document["_speed_profile"] = extension
    return document


def _speed_ratio(profile: Profile, keys: tuple[str, ...]) -> float | None:
    """Mean speed of ``keys`` relative to the profile default, or ``None``."""
    values = [profile.speeds_kmh[k] for k in keys if k in profile.speeds_kmh]
    if not values or profile.default_speed_kmh <= 0:
        return None
    return (sum(values) / len(values)) / profile.default_speed_kmh


def _use_highways(profile: Profile) -> float:
    """Preference for motorways, inferred from how fast the spec makes them."""
    ratio = _speed_ratio(profile, ("motorway", "trunk"))
    if ratio is None:
        return 0.5
    return max(0.0, min(1.0, ratio / 2.5))


def _use_tracks(profile: Profile) -> float:
    """Preference for tracks, inferred from the track speed and tracktype table."""
    ratio = _speed_ratio(profile, ("track",))
    base = 0.0 if ratio is None else max(0.0, min(1.0, ratio))
    if profile.tracktypes:
        base *= min(profile.tracktypes.values())
    return max(0.0, min(1.0, base))


def _use_living_streets(profile: Profile) -> float:
    """Preference for living streets, inferred from their spec speed."""
    ratio = _speed_ratio(profile, ("living_street",))
    return 0.5 if ratio is None else max(0.0, min(1.0, ratio))


def emit_valhalla(profile: Profile, options: EmitOptions | None = None) -> str:
    """Render ``profile`` as a Valhalla costing JSON document."""
    options = options or EmitOptions()
    document = build_document(profile, options)
    if not options.header:
        document.pop("_generated", None)
    else:
        # Keep the provenance text identical to the Lua header so the two
        # artefacts can be reconciled by eye.
        document["_generated"]["header"] = [
            line.lstrip("- ").rstrip()
            for line in provenance(profile, "--", options)
            if line.strip() != "--"
        ]
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
