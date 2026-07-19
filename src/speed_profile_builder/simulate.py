"""Apply a profile to sample OSM ways without building a graph.

Rebuilding an OSRM graph to find out whether a speed edit did what you meant
costs hours. This module evaluates the profile directly against a set of tag
dictionaries and reports the resulting speed and cost per way, which turns that
loop into milliseconds.

The evaluation order deliberately mirrors the generated ``process_way``: ferry
handling, then access, then dimensions, then class speed, then surface and
tracktype and smoothness multipliers, then the global factor, then the posted
``maxspeed``, then the legal cap, then time-of-day, then penalties. If the two
ever diverge the simulator is lying, so the ordering is asserted by the test
suite rather than left to comments.

Nothing here talks to a routing engine — the sample ways come from a fixture
file or a CSV, so the whole feature is testable offline.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import SpecError, SpecIssue, UnitError
from .model import Profile
from .units import parse_length, parse_mass

#: Nominal edge length used when a sample carries none. One kilometre makes the
#: reported duration read directly as seconds-per-kilometre.
DEFAULT_LENGTH_M = 1000.0

#: OSM keys that restrict which vehicles may use a way, and the IR attribute
#: each is compared against.
_DIMENSION_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("maxheight", "height_m", "length"),
    ("maxwidth", "width_m", "length"),
    ("maxlength", "length_m", "length"),
    ("maxweight", "weight_kg", "mass"),
    ("maxaxleload", "axle_load_kg", "mass"),
)


@dataclass(frozen=True)
class WaySample:
    """One sample way: a name, its OSM tags, and an optional real length."""

    name: str
    tags: dict[str, str]
    length_m: float = DEFAULT_LENGTH_M

    @property
    def highway(self) -> str:
        """The way's highway class, or an empty string."""
        return self.tags.get("highway", "")


@dataclass
class WayResult:
    """The profile's verdict on one sample way."""

    sample: WaySample
    routable: bool
    speed_kmh: float = 0.0
    duration_s: float = 0.0
    penalty_s: float = 0.0
    cost_s: float = 0.0
    reason: str = ""
    steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready view, used by ``--format json``."""
        return {
            "name": self.sample.name,
            "highway": self.sample.highway,
            "routable": self.routable,
            "speed_kmh": round(self.speed_kmh, 3),
            "duration_s": round(self.duration_s, 3),
            "penalty_s": round(self.penalty_s, 3),
            "cost_s": round(self.cost_s, 3),
            "reason": self.reason,
            "steps": list(self.steps),
        }


@dataclass(frozen=True)
class Comparison:
    """Before/after result for one way under two profiles."""

    before: WayResult
    after: WayResult

    @property
    def speed_delta_pct(self) -> float | None:
        """Percentage change in speed, or ``None`` if either side is unroutable."""
        if not self.before.routable or not self.after.routable or self.before.speed_kmh == 0:
            return None
        return (self.after.speed_kmh - self.before.speed_kmh) / self.before.speed_kmh * 100.0

    @property
    def cost_delta_pct(self) -> float | None:
        """Percentage change in cost, the number that decides route choice."""
        if not self.before.routable or not self.after.routable or self.before.cost_s == 0:
            return None
        return (self.after.cost_s - self.before.cost_s) / self.before.cost_s * 100.0

    @property
    def routability_changed(self) -> bool:
        """Whether the way became routable, or stopped being routable."""
        return self.before.routable != self.after.routable

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready view of the comparison."""
        return {
            "name": self.after.sample.name,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "speed_delta_pct": (
                None if self.speed_delta_pct is None else round(self.speed_delta_pct, 2)
            ),
            "cost_delta_pct": (
                None if self.cost_delta_pct is None else round(self.cost_delta_pct, 2)
            ),
            "routability_changed": self.routability_changed,
        }


def _measure(raw: str, kind: str) -> float | None:
    """Parse an OSM restriction value, tolerating unit suffixes and junk.

    Returns ``None`` for values that carry no number, which callers must read as
    "unrestricted" rather than "zero" — treating an unparseable ``maxweight`` as
    zero would delete half the road network.
    """
    try:
        if kind == "mass":
            # OSM writes maxweight in tonnes unless a unit says otherwise.
            return parse_mass(raw) * (1.0 if any(u in raw for u in ("kg", "lb")) else 1000.0)
        return parse_length(raw)
    except UnitError:
        # A value like "variable" or "see notes" carries no limit we can enforce.
        return None


def _access_verdict(profile: Profile, tags: dict[str, str]) -> tuple[bool | None, bool, str]:
    """Resolve the access hierarchy, returning (allowed, destination_only, tag).

    ``allowed`` is tri-state on purpose: ``None`` means no tag in the hierarchy
    said anything, which is different from being denied and is what lets a way
    fall through to its highway class default.
    """
    for key in profile.access.hierarchy:
        value = tags.get(key)
        if value is None:
            continue
        verdict = profile.access.verdict(value)
        if verdict == "deny":
            return False, False, f"{key}={value}"
        if verdict == "allow":
            destination_only = (
                value == "destination" and not profile.access.allow_through_destination
            )
            return True, destination_only, f"{key}={value}"
    return None, False, ""


def evaluate(
    profile: Profile,
    sample: WaySample,
    day: str = "we",
    minute: int | None = None,
) -> WayResult:
    """Evaluate one sample way against ``profile``.

    ``day`` and ``minute`` select a point in time for time-of-day factors;
    ``minute`` of ``None`` skips them entirely, which is the right default when
    comparing profiles that do not both define windows.
    """
    tags = sample.tags
    steps: list[str] = []

    if tags.get("route") == "ferry":
        if not profile.ferry.allowed:
            return WayResult(sample, False, reason="ferries are not allowed by this profile")
        speed = profile.ferry.speed_kmh
        duration = sample.length_m / 1000.0 / speed * 3600.0
        steps.append(f"ferry speed {speed:g} km/h")
        penalty = profile.ferry.penalty_s
        if penalty:
            steps.append(f"ferry penalty +{penalty:g} s")
        return WayResult(
            sample,
            True,
            speed_kmh=speed,
            duration_s=duration,
            penalty_s=penalty,
            cost_s=duration + penalty,
            reason="ferry",
            steps=steps,
        )

    highway = tags.get("highway")
    if not highway:
        return WayResult(sample, False, reason="no highway tag and not a ferry")

    allowed, destination_only, access_tag = _access_verdict(profile, tags)
    if allowed is False:
        return WayResult(sample, False, reason=f"access denied by {access_tag}")
    if access_tag:
        steps.append(f"access {access_tag}")

    for osm_key, attr, kind in _DIMENSION_CHECKS:
        limit = getattr(profile.vehicle, attr)
        if limit is None or osm_key not in tags:
            continue
        posted = _measure(tags[osm_key], kind)
        if posted is not None and posted < limit:
            return WayResult(
                sample,
                False,
                reason=f"{osm_key}={tags[osm_key]} is below the vehicle's {attr}",
            )
    if profile.vehicle.hazmat and tags.get("hazmat") == "no":
        return WayResult(sample, False, reason="hazmat=no excludes this vehicle")

    if highway in profile.speeds_kmh:
        speed = profile.speeds_kmh[highway]
        steps.append(f"{highway} base {speed:g} km/h")
    elif allowed is True:
        speed = profile.default_speed_kmh
        steps.append(f"unlisted class {highway!r}, default {speed:g} km/h")
    else:
        return WayResult(
            sample,
            False,
            reason=f"highway={highway} has no speed entry and no explicit access tag",
        )

    for label, value, factor_fn in (
        ("surface", tags.get("surface"), profile.surface_factor),
        ("tracktype", tags.get("tracktype"), profile.tracktype_factor),
        ("smoothness", tags.get("smoothness"), profile.smoothness_factor),
    ):
        factor = factor_fn(value)
        if value is not None and factor != 1.0:
            speed *= factor
            steps.append(f"{label}={value} x{factor:g} -> {speed:.1f} km/h")

    if profile.global_factor != 1.0:
        speed *= profile.global_factor
        steps.append(f"global factor x{profile.global_factor:g} -> {speed:.1f} km/h")

    posted_speed = _posted_speed(tags.get("maxspeed"))
    if posted_speed is not None and posted_speed < speed:
        speed = posted_speed
        steps.append(f"maxspeed={tags['maxspeed']} caps to {speed:g} km/h")

    capped = profile.cap(speed)
    if capped != speed:
        steps.append(f"clamped to {capped:g} km/h")
        speed = capped

    if minute is not None:
        for factor in profile.time_factors:
            if factor.applies(day, minute, highway):
                speed *= factor.factor
                steps.append(f"time factor {factor.name} x{factor.factor:g} -> {speed:.1f} km/h")
        speed = profile.cap(speed)

    penalty = 0.0
    for zone in profile.zones:
        if not zone.matches_tags(tags):
            continue
        if zone.action in ("avoid", "block"):
            return WayResult(sample, False, reason=f"zone {zone.id!r} action {zone.action}")
        penalty += zone.penalty_s
        steps.append(f"zone {zone.id} +{zone.penalty_s:g} s")

    if tags.get("toll") == "yes":
        if profile.toll.avoid:
            return WayResult(sample, False, reason="toll=yes and extras.toll.avoid is set")
        if profile.toll.penalty_s:
            penalty += profile.toll.penalty_s
            steps.append(f"toll +{profile.toll.penalty_s:g} s")

    if destination_only:
        penalty += 600.0
        steps.append("destination-only +600 s")

    duration = sample.length_m / 1000.0 / speed * 3600.0
    return WayResult(
        sample,
        True,
        speed_kmh=speed,
        duration_s=duration,
        penalty_s=penalty,
        cost_s=duration + penalty,
        reason="routable",
        steps=steps,
    )


def _posted_speed(raw: str | None) -> float | None:
    """Interpret an OSM ``maxspeed`` value, honouring an ``mph`` suffix."""
    if not raw:
        return None
    text = raw.strip().lower()
    if text in ("none", "signals", "variable", "walk"):
        return 7.0 if text == "walk" else None
    try:
        if "mph" in text:
            return float(text.replace("mph", "").strip()) * 1.609344
        return float(text.split()[0])
    except (ValueError, IndexError):
        return None


def simulate(
    profile: Profile,
    samples: list[WaySample],
    day: str = "we",
    minute: int | None = None,
) -> list[WayResult]:
    """Evaluate every sample against one profile, preserving input order."""
    return [evaluate(profile, s, day=day, minute=minute) for s in samples]


def compare(
    baseline: Profile,
    candidate: Profile,
    samples: list[WaySample],
    day: str = "we",
    minute: int | None = None,
) -> list[Comparison]:
    """Evaluate every sample against both profiles, pairing the results."""
    return [
        Comparison(
            evaluate(baseline, s, day=day, minute=minute),
            evaluate(candidate, s, day=day, minute=minute),
        )
        for s in samples
    ]


def load_samples(path: Path) -> list[WaySample]:
    """Load sample ways from a YAML fixture or a CSV of OSM tags.

    The format is chosen by extension. CSV is supported because the fastest way
    to get real samples is an Overpass export, and that arrives as CSV.

    :raises SpecError: if the file is missing, empty or structurally wrong.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(SpecIssue(message=f"cannot read samples: {exc}", source=path)) from exc
    if path.suffix.lower() == ".csv":
        return parse_samples_csv(text, path)
    return parse_samples_yaml(text, path)


def parse_samples_yaml(text: str, source: Path | None = None) -> list[WaySample]:
    """Parse the YAML fixture format: a list of ``{name, tags, length}`` maps."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError(
            SpecIssue(message=f"invalid YAML in samples: {exc}", source=source)
        ) from exc
    if isinstance(data, dict) and "ways" in data:
        data = data["ways"]
    if not isinstance(data, list) or not data:
        raise SpecError(
            SpecIssue(
                message="sample file must contain a non-empty list of ways "
                "(optionally under a top-level 'ways' key)",
                source=source,
            )
        )
    samples = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise SpecError(
                SpecIssue(message=f"sample {i} is not a mapping", path=str(i), source=source)
            )
        tags = entry.get("tags", {})
        if not isinstance(tags, dict) or not tags:
            raise SpecError(
                SpecIssue(
                    message=f"sample {i} has no 'tags' mapping",
                    path=f"{i}.tags",
                    source=source,
                )
            )
        length = entry.get("length_m", DEFAULT_LENGTH_M)
        samples.append(
            WaySample(
                name=str(entry.get("name", f"way-{i}")),
                tags={str(k): _tag_value(v) for k, v in tags.items()},
                length_m=float(length),
            )
        )
    return samples


def parse_samples_csv(text: str, source: Path | None = None) -> list[WaySample]:
    """Parse a CSV where each column is an OSM key.

    ``name`` and ``length_m`` columns are reserved; every other column becomes a
    tag, and empty cells are dropped so a sparse Overpass export works unchanged.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise SpecError(SpecIssue(message="sample CSV has no header row", source=source))
    samples = []
    for i, row in enumerate(reader):
        tags = {
            k: v.strip()
            for k, v in row.items()
            if k not in ("name", "length_m") and k is not None and v and v.strip()
        }
        if not tags:
            raise SpecError(
                SpecIssue(message=f"sample row {i + 1} has no tag columns set", source=source)
            )
        raw_length = (row.get("length_m") or "").strip()
        samples.append(
            WaySample(
                name=(row.get("name") or f"row-{i + 1}").strip(),
                tags=tags,
                length_m=float(raw_length) if raw_length else DEFAULT_LENGTH_M,
            )
        )
    if not samples:
        raise SpecError(SpecIssue(message="sample CSV contains no rows", source=source))
    return samples


def _tag_value(value: Any) -> str:
    """Coerce a YAML scalar to the string OSM would have stored."""
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value)
