"""Static analysis of a resolved profile.

Every rule here exists because the corresponding mistake is expensive: it
survives validation, compiles to legal Lua and legal Valhalla JSON, and only
shows itself hours later as a route that takes a ferry to cross a river with a
bridge, or a network that has quietly fragmented into islands.

Rules are pure functions from a :class:`~speed_profile_builder.model.Profile` to
a list of findings. They never mutate, never raise on well-formed input, and are
registered in :data:`RULES` so ``--select``/``--ignore`` can address them by
name.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .errors import LintFinding
from .model import Profile
from .spec.schema import KNOWN_HIGHWAYS

#: Plausible upper bound on free-flow speed per mode, in km/h. Anything above is
#: almost certainly a unit mistake (mph typed as km/h) rather than an intention.
MODE_SPEED_CEILING: dict[str, float] = {
    "car": 200.0,
    "van": 160.0,
    "truck": 120.0,
    "motorcycle": 200.0,
    "bicycle": 50.0,
    "foot": 15.0,
}

#: Penalty at or above which an edge is effectively unroutable; past this point
#: a detour of any length is cheaper, so ``action: avoid`` states the intent
#: better and lets the engine optimise for it.
EFFECTIVELY_IMPASSABLE_S = 3600.0

#: Surfaces a motor-vehicle profile should say something about; ignoring them
#: routes lorries down farm tracks at full residential speed.
IMPORTANT_SURFACES = ("unpaved", "gravel", "ground", "dirt")

Rule = Callable[[Profile], list[LintFinding]]


def _finding(rule: str, severity: str, message: str, path: str = "", hint: str = "") -> LintFinding:
    return LintFinding(rule=rule, severity=severity, message=message, path=path, hint=hint)


def rule_speed_above_legal_max(profile: Profile) -> list[LintFinding]:
    """A class speed above ``speeds.max_legal`` is silently clamped at build time."""
    out = []
    ceiling = profile.max_legal_speed_kmh
    if ceiling is None:
        return out
    for highway, speed in profile.speeds_kmh.items():
        if speed > ceiling:
            out.append(
                _finding(
                    "speed-above-legal-max",
                    "warning",
                    f"{highway} speed {speed:g} km/h exceeds speeds.max_legal "
                    f"{ceiling:g} km/h and will be clamped",
                    f"speeds.highway.{highway}",
                    "raise max_legal or lower the class speed so the spec says what it means",
                )
            )
    if profile.default_speed_kmh > ceiling:
        out.append(
            _finding(
                "speed-above-legal-max",
                "warning",
                f"default speed {profile.default_speed_kmh:g} km/h exceeds "
                f"speeds.max_legal {ceiling:g} km/h",
                "speeds.default",
            )
        )
    return out


def rule_implausible_speed(profile: Profile) -> list[LintFinding]:
    """A speed beyond physical plausibility for the mode, usually a unit slip."""
    ceiling = MODE_SPEED_CEILING.get(profile.mode, 200.0)
    out = []
    for highway, speed in profile.speeds_kmh.items():
        if speed > ceiling:
            out.append(
                _finding(
                    "implausible-speed",
                    "error",
                    f"{highway} speed {speed:g} km/h is implausible for mode "
                    f"{profile.mode!r} (ceiling {ceiling:g} km/h)",
                    f"speeds.highway.{highway}",
                    "did you write a value in mph without the unit suffix?",
                )
            )
    return out


def rule_impassable_penalty(profile: Profile) -> list[LintFinding]:
    """Penalties so large the edge is unroutable in practice."""
    out = []
    for zone in profile.zones:
        if zone.action == "penalty" and zone.penalty_s >= EFFECTIVELY_IMPASSABLE_S:
            out.append(
                _finding(
                    "impassable-penalty",
                    "warning",
                    f"zone {zone.id!r} penalty {zone.penalty_s:g} s makes matching ways "
                    "effectively impassable",
                    f"zones.{zone.id}.penalty",
                    "use action 'avoid' so the engine can exclude them cleanly",
                )
            )
    for barrier in profile.access.barriers:
        if barrier.action == "penalty" and barrier.penalty_s >= EFFECTIVELY_IMPASSABLE_S:
            out.append(
                _finding(
                    "impassable-penalty",
                    "warning",
                    f"barrier {barrier.key!r} penalty {barrier.penalty_s:g} s is equivalent "
                    "to blocking it",
                    f"access.barriers.{barrier.key}",
                    "use action 'block' to say so directly",
                )
            )
    if profile.turn.u_turn_penalty_s >= EFFECTIVELY_IMPASSABLE_S:
        out.append(
            _finding(
                "impassable-penalty",
                "warning",
                f"u_turn_penalty {profile.turn.u_turn_penalty_s:g} s effectively forbids u-turns",
                "turn.u_turn_penalty",
                "set allow_u_turns: false instead",
            )
        )
    return out


def rule_contradictory_zones(profile: Profile) -> list[LintFinding]:
    """Two zones matching the same tag with incompatible actions."""
    out = []
    seen: dict[tuple[str, str | None], str] = {}
    for zone in sorted(profile.zones, key=lambda z: z.id):
        if zone.tag is None:
            continue
        key = (zone.tag, zone.value)
        first = seen.get(key)
        if first is None:
            seen[key] = zone.id
            continue
        other = profile.zone_by_id(first)
        assert other is not None
        if other.action != zone.action:
            out.append(
                _finding(
                    "contradictory-zones",
                    "error",
                    f"zones {first!r} and {zone.id!r} both match "
                    f"{zone.tag}={zone.value or '*'} but disagree: "
                    f"{other.action} vs {zone.action}",
                    f"zones.{zone.id}",
                    "merge them, or narrow one with an explicit 'value'",
                )
            )
        else:
            out.append(
                _finding(
                    "contradictory-zones",
                    "warning",
                    f"zones {first!r} and {zone.id!r} match the same tag "
                    f"{zone.tag}={zone.value or '*'}; their penalties compound",
                    f"zones.{zone.id}",
                )
            )
    return out


def rule_unreachable_time_factor(profile: Profile) -> list[LintFinding]:
    """A time factor that can never apply, or applies without any effect."""
    out = []
    known = set(profile.speeds_kmh)
    for factor in profile.time_factors:
        if not factor.days:
            out.append(
                _finding(
                    "unreachable-time-factor",
                    "error",
                    f"time factor {factor.name!r} lists no days and can never apply",
                    f"time_factors.{factor.name}.days",
                )
            )
        missing = [h for h in factor.highway if h not in known]
        if missing and len(missing) == len(factor.highway):
            out.append(
                _finding(
                    "unreachable-time-factor",
                    "warning",
                    f"time factor {factor.name!r} targets only highway classes with no "
                    f"speed entry: {', '.join(sorted(missing))}",
                    f"time_factors.{factor.name}.highway",
                    "these classes fall back to speeds.default, so the factor does nothing",
                )
            )
        if abs(factor.factor - 1.0) < 1e-9:
            out.append(
                _finding(
                    "unreachable-time-factor",
                    "info",
                    f"time factor {factor.name!r} has factor 1.0 and changes nothing",
                    f"time_factors.{factor.name}.factor",
                )
            )
    return out


def rule_dead_zone_rule(profile: Profile) -> list[LintFinding]:
    """A zone whose configuration means it can never affect a route."""
    out = []
    for zone in profile.zones:
        if zone.action == "penalty" and 0 < zone.penalty_s < 1.0:
            out.append(
                _finding(
                    "dead-zone-rule",
                    "info",
                    f"zone {zone.id!r} penalty {zone.penalty_s:g} s is below one second and "
                    "will not measurably change routing",
                    f"zones.{zone.id}.penalty",
                )
            )
    return out


def rule_polygon_zone_osrm(profile: Profile) -> list[LintFinding]:
    """Polygon-only zones silently do nothing in an OSRM build."""
    return [
        _finding(
            "polygon-zone-osrm",
            "info",
            f"zone {zone.id!r} is polygon-only; OSRM cannot evaluate geometry in "
            "process_way, so it is emitted for Valhalla only",
            f"zones.{zone.id}.polygon",
            "add a 'tag' matcher if the zone must also apply to OSRM builds",
        )
        for zone in sorted(profile.zones, key=lambda z: z.id)
        if zone.polygon is not None and zone.tag is None
    ]


def rule_network_fragmentation(profile: Profile) -> list[LintFinding]:
    """Access rules strict enough to cut the network into islands."""
    out = []
    blocked = set(profile.access.blocked)
    for value in ("permissive", "destination", "delivery"):
        if value in blocked:
            out.append(
                _finding(
                    "network-fragmentation",
                    "warning",
                    f"access value {value!r} is blocked; this commonly severs the last "
                    "leg of urban deliveries and isolates whole estates",
                    "access.blocked",
                    "block it only if the fleet genuinely may not use such ways",
                )
            )
    if "yes" in blocked:
        out.append(
            _finding(
                "network-fragmentation",
                "error",
                "access value 'yes' is blocked, which rejects explicitly permitted ways",
                "access.blocked",
            )
        )
    if not profile.access.allowed:
        out.append(
            _finding(
                "network-fragmentation",
                "error",
                "access.allowed is empty, so no access tag can ever grant passage",
                "access.allowed",
            )
        )
    blocking = [b.key for b in profile.access.barriers if b.action == "block"]
    if profile.mode in ("bicycle", "foot") and "gate" in blocking:
        out.append(
            _finding(
                "network-fragmentation",
                "warning",
                f"barrier 'gate' blocks mode {profile.mode!r}; gates are usually passable "
                "on foot or by bike and blocking them fragments paths",
                "access.barriers.gate",
            )
        )
    return out


def rule_missing_surface_handling(profile: Profile) -> list[LintFinding]:
    """No surface, tracktype or smoothness handling where it clearly matters."""
    out = []
    if profile.mode == "foot":
        return out
    if not profile.surfaces:
        out.append(
            _finding(
                "missing-surface-handling",
                "warning",
                "no surface multipliers are defined; unpaved ways will be costed at their "
                "full class speed",
                "surfaces",
                f"start with {', '.join(IMPORTANT_SURFACES)}",
            )
        )
    else:
        missing = [s for s in IMPORTANT_SURFACES if s not in profile.surfaces]
        if missing:
            out.append(
                _finding(
                    "missing-surface-handling",
                    "info",
                    f"surface multipliers omit {', '.join(missing)}",
                    "surfaces",
                )
            )
    if "track" in profile.speeds_kmh and not profile.tracktypes:
        out.append(
            _finding(
                "missing-surface-handling",
                "warning",
                "a speed is set for highway=track but no tracktype multipliers exist, so "
                "grade5 mud is costed like grade1 hardcore",
                "tracktypes",
            )
        )
    return out


def rule_unknown_highway(profile: Profile) -> list[LintFinding]:
    """Speeds keyed by a highway value OSM does not use."""
    return [
        _finding(
            "unknown-highway",
            "warning",
            f"highway class {highway!r} is not a standard OSM value and will never match",
            f"speeds.highway.{highway}",
            "check the spelling against the OSM highway key",
        )
        for highway in sorted(profile.speeds_kmh)
        if highway not in KNOWN_HIGHWAYS
    ]


def rule_suspicious_mass(profile: Profile) -> list[LintFinding]:
    """A vehicle mass small enough to suggest tonnes were written as kilograms."""
    out = []
    for label, value in (
        ("vehicle.weight", profile.vehicle.weight_kg),
        ("vehicle.axle_load", profile.vehicle.axle_load_kg),
    ):
        if value is not None and value < 100:
            out.append(
                _finding(
                    "suspicious-units",
                    "error",
                    f"{label} is {value:g} kg, which is implausibly light",
                    label,
                    "bare numbers are kilograms here; write '18 t' if you meant tonnes",
                )
            )
    return out


def rule_missing_vehicle_limits(profile: Profile) -> list[LintFinding]:
    """A heavy-vehicle profile that declares no dimensions restricts nothing."""
    if profile.mode not in ("truck", "van"):
        return []
    if profile.vehicle.is_constrained:
        return []
    return [
        _finding(
            "missing-vehicle-limits",
            "warning",
            f"mode {profile.mode!r} declares no height, width, length or weight, so "
            "maxheight/maxweight restrictions on ways are ignored",
            "vehicle",
            "declare at least height and weight",
        )
    ]


def rule_no_speeds(profile: Profile) -> list[LintFinding]:
    """An empty speed table means every way falls back to one number."""
    if profile.speeds_kmh:
        return []
    return [
        _finding(
            "no-speeds",
            "error",
            "speeds.highway is empty; every way would be routed at speeds.default "
            f"({profile.default_speed_kmh:g} km/h)",
            "speeds.highway",
        )
    ]


def rule_extreme_global_factor(profile: Profile) -> list[LintFinding]:
    """A global factor far from 1.0 usually means the speed table is wrong."""
    factor = profile.global_factor
    if 0.4 <= factor <= 1.6:
        return []
    return [
        _finding(
            "extreme-global-factor",
            "warning",
            f"speeds.global_factor is {factor:g}; scaling every speed this hard hides "
            "the real per-class values",
            "speeds.global_factor",
            "fold the factor into the speed table so diffs stay readable",
        )
    ]


def rule_default_speed_outlier(profile: Profile) -> list[LintFinding]:
    """A default faster than every declared class is almost always a mistake."""
    if not profile.speeds_kmh:
        return []
    fastest = max(profile.speeds_kmh.values())
    if profile.default_speed_kmh <= fastest:
        return []
    return [
        _finding(
            "default-speed-outlier",
            "warning",
            f"speeds.default ({profile.default_speed_kmh:g} km/h) is faster than every "
            f"declared class (max {fastest:g} km/h), so untagged ways become the "
            "fastest roads in the network",
            "speeds.default",
        )
    ]


def rule_ferry_sanity(profile: Profile) -> list[LintFinding]:
    """Ferries left at road speed, or blocked while a ferry penalty is set."""
    out = []
    if profile.ferry.allowed and profile.ferry.speed_kmh > 40:
        out.append(
            _finding(
                "ferry-sanity",
                "warning",
                f"ferry speed {profile.ferry.speed_kmh:g} km/h is faster than most ferries "
                "and will make sea crossings beat road routes",
                "extras.ferry.speed",
            )
        )
    if profile.ferry.allowed and profile.ferry.penalty_s == 0 and profile.mode != "foot":
        out.append(
            _finding(
                "ferry-sanity",
                "info",
                "ferries carry no penalty; boarding, waiting and unloading time is not "
                "modelled at all",
                "extras.ferry.penalty",
            )
        )
    return out


def rule_turn_restriction_bypass(profile: Profile) -> list[LintFinding]:
    """Ignoring turn restrictions produces routes that are illegal to drive."""
    if profile.turn.obey_restrictions:
        return []
    return [
        _finding(
            "turn-restriction-bypass",
            "error",
            "turn.restrictions is 'ignore'; generated routes may violate no-turn "
            "restrictions and banned manoeuvres",
            "turn.restrictions",
            "only correct for coverage analysis, never for navigation",
        )
    ]


#: Every rule, keyed by the name used in ``--select`` / ``--ignore``.
RULES: dict[str, Rule] = {
    "speed-above-legal-max": rule_speed_above_legal_max,
    "implausible-speed": rule_implausible_speed,
    "impassable-penalty": rule_impassable_penalty,
    "contradictory-zones": rule_contradictory_zones,
    "unreachable-time-factor": rule_unreachable_time_factor,
    "dead-zone-rule": rule_dead_zone_rule,
    "polygon-zone-osrm": rule_polygon_zone_osrm,
    "network-fragmentation": rule_network_fragmentation,
    "missing-surface-handling": rule_missing_surface_handling,
    "unknown-highway": rule_unknown_highway,
    "suspicious-units": rule_suspicious_mass,
    "missing-vehicle-limits": rule_missing_vehicle_limits,
    "no-speeds": rule_no_speeds,
    "extreme-global-factor": rule_extreme_global_factor,
    "default-speed-outlier": rule_default_speed_outlier,
    "ferry-sanity": rule_ferry_sanity,
    "turn-restriction-bypass": rule_turn_restriction_bypass,
}

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def lint_profile(
    profile: Profile,
    select: Iterable[str] | None = None,
    ignore: Iterable[str] | None = None,
    min_severity: str = "info",
) -> list[LintFinding]:
    """Run the rule set over ``profile``.

    ``select`` restricts to named rules; ``ignore`` removes them. Findings are
    returned sorted by severity then rule name then path, so output is stable
    enough to diff between CI runs.

    :raises KeyError: if a name in ``select`` or ``ignore`` is not a known rule.
    """
    names = set(select) if select else set(RULES)
    for name in set(select or ()) | set(ignore or ()):
        if name not in RULES:
            raise KeyError(f"unknown lint rule {name!r}; known rules: {', '.join(sorted(RULES))}")
    names -= set(ignore or ())

    threshold = SEVERITY_ORDER[min_severity]
    findings: list[LintFinding] = []
    for name in sorted(names):
        for finding in RULES[name](profile):
            if SEVERITY_ORDER[finding.severity] <= threshold:
                findings.append(finding)
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.rule, f.path))
    return findings
