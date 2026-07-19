"""The normalised IR: lowering, lookups, flattening and fingerprints."""

from __future__ import annotations

from typing import Any

import pytest

from speed_profile_builder.model import build_profile
from speed_profile_builder.spec.loader import load_bundled

from .conftest import profile_from


def test_lowering_converts_masses_to_tonnes_for_valhalla() -> None:
    spec = load_bundled("truck")
    profile = build_profile(spec)
    assert profile.vehicle.weight_kg == 40000
    assert profile.vehicle.weight_t == 40.0
    assert profile.vehicle.axle_load_t == pytest.approx(11.5)


def test_unconstrained_vehicle_reports_itself_as_such() -> None:
    profile = build_profile(load_bundled("car"))
    assert profile.vehicle.is_constrained is False
    assert build_profile(load_bundled("truck")).vehicle.is_constrained is True


def test_base_speed_falls_back_to_the_default(minimal_document: dict[str, Any]) -> None:
    profile = profile_from(minimal_document)
    assert profile.base_speed("primary") == 60
    assert profile.base_speed("bridleway") == profile.default_speed_kmh


def test_unlisted_multipliers_are_neutral(minimal_document: dict[str, Any]) -> None:
    profile = profile_from(minimal_document)
    assert profile.surface_factor("moon_dust") == 1.0
    assert profile.surface_factor(None) == 1.0
    assert profile.tracktype_factor("grade3") == 1.0
    assert profile.smoothness_factor(None) == 1.0


def test_cap_applies_the_legal_ceiling_and_a_floor(minimal_document: dict[str, Any]) -> None:
    minimal_document["speeds"]["max_legal"] = 50
    profile = profile_from(minimal_document)
    assert profile.cap(90) == 50
    assert profile.cap(0.1) == 5.0


def test_time_window_membership(minimal_document: dict[str, Any]) -> None:
    minimal_document["time_factors"] = [
        {"name": "peak", "factor": 0.5, "hours": "07:00-09:00", "days": ["mo", "tu"]}
    ]
    factor = profile_from(minimal_document).time_factors[0]
    assert factor.applies("mo", 8 * 60, "primary")
    assert not factor.applies("we", 8 * 60, "primary")
    assert not factor.applies("mo", 10 * 60, "primary")


def test_time_window_can_wrap_past_midnight(minimal_document: dict[str, Any]) -> None:
    minimal_document["time_factors"] = [{"name": "night", "factor": 1.2, "hours": "22:00-05:00"}]
    factor = profile_from(minimal_document).time_factors[0]
    assert factor.applies("we", 23 * 60, "primary")
    assert factor.applies("we", 2 * 60, "primary")
    assert not factor.applies("we", 12 * 60, "primary")


def test_time_factor_can_be_restricted_to_highway_classes(
    minimal_document: dict[str, Any],
) -> None:
    minimal_document["time_factors"] = [{"name": "peak", "factor": 0.5, "highway": ["primary"]}]
    factor = profile_from(minimal_document).time_factors[0]
    assert factor.applies("we", 600, "primary")
    assert not factor.applies("we", 600, "residential")


def test_zone_tag_matching_handles_presence_and_value(minimal_document: dict[str, Any]) -> None:
    minimal_document["zones"] = [
        {"id": "any", "tag": "lez", "action": "penalty", "penalty": "60 s"},
        {"id": "exact", "tag": "zone", "value": "b", "action": "penalty", "penalty": "60 s"},
    ]
    profile = profile_from(minimal_document)
    any_zone, exact = profile.zones
    assert any_zone.matches_tags({"lez": "yes"})
    assert not any_zone.matches_tags({"lez": "no"})
    assert not any_zone.matches_tags({})
    assert exact.matches_tags({"zone": "b"})
    assert not exact.matches_tags({"zone": "a"})


def test_polygon_zone_never_matches_on_tags_alone(minimal_document: dict[str, Any]) -> None:
    minimal_document["zones"] = [
        {"id": "poly", "action": "avoid", "polygon": [[0, 0], [1, 0], [1, 1]]}
    ]
    zone = profile_from(minimal_document).zones[0]
    assert zone.matches_tags({"anything": "yes"}) is False


def test_access_verdict_is_tri_state(minimal_document: dict[str, Any]) -> None:
    """An unrecognised value must fall through rather than deny."""
    access = profile_from(minimal_document).access
    assert access.verdict("yes") == "allow"
    assert access.verdict("private") == "deny"
    assert access.verdict("discouraged") is None


def test_flatten_is_sorted_and_stable(minimal_document: dict[str, Any]) -> None:
    profile = profile_from(minimal_document)
    keys = list(profile.flatten())
    speed_keys = [k for k in keys if k.startswith("speeds.highway.")]
    assert speed_keys == sorted(speed_keys)
    assert profile.flatten() == profile.flatten()


def test_fingerprint_changes_only_with_semantics(minimal_document: dict[str, Any]) -> None:
    first = profile_from(minimal_document)
    same = profile_from({**minimal_document, "description": "different prose"})
    changed = profile_from(
        {**minimal_document, "speeds": {"default": 30, "highway": {"residential": 31}}}
    )
    assert first.fingerprint() == same.fingerprint()
    assert first.fingerprint() != changed.fingerprint()


def test_source_chain_is_recorded_for_provenance() -> None:
    from speed_profile_builder.spec.loader import bundled_profiles, load_spec

    path = bundled_profiles()["ev-delivery"]
    spec, chain = load_spec(path)
    profile = build_profile(spec, chain)
    assert [p.rsplit("/", 1)[-1] for p in profile.source_chain] == [
        "car.yaml",
        "van-urban.yaml",
        "ev-delivery.yaml",
    ]


def test_zone_lookup_by_id(minimal_document: dict[str, Any]) -> None:
    minimal_document["zones"] = [{"id": "lez", "tag": "lez", "action": "avoid"}]
    profile = profile_from(minimal_document)
    assert profile.zone_by_id("lez") is not None
    assert profile.zone_by_id("missing") is None
