"""Inheritance: merge semantics, chains, and their failure modes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from speed_profile_builder.errors import SpecError
from speed_profile_builder.spec.loader import deep_merge, flatten, load_bundled, load_spec

from .conftest import write_spec


def test_scalars_replace() -> None:
    assert deep_merge({"a": 1, "b": 2}, {"a": 9}) == {"a": 9, "b": 2}


def test_mappings_merge_recursively() -> None:
    base = {"speeds": {"highway": {"primary": 60, "residential": 30}, "default": 20}}
    override = {"speeds": {"highway": {"primary": 50}}}
    assert deep_merge(base, override) == {
        "speeds": {"highway": {"primary": 50, "residential": 30}, "default": 20}
    }


def test_null_removes_an_inherited_key() -> None:
    """The only way to unset something a base profile set."""
    base = {"speeds": {"max_legal": 120, "default": 30}}
    assert deep_merge(base, {"speeds": {"max_legal": None}}) == {"speeds": {"default": 30}}


def test_plain_lists_replace_wholesale() -> None:
    base = {"access": {"hierarchy": ["motorcar", "motor_vehicle", "access"]}}
    override = {"access": {"hierarchy": ["hgv", "access"]}}
    assert deep_merge(base, override)["access"]["hierarchy"] == ["hgv", "access"]


def test_keyed_lists_merge_by_identity() -> None:
    base = {"zones": [{"id": "a", "penalty": 60}, {"id": "b", "penalty": 30}]}
    override = {"zones": [{"id": "a", "penalty": 120}, {"id": "c", "penalty": 10}]}
    merged = deep_merge(base, override)["zones"]
    assert [z["id"] for z in merged] == ["a", "b", "c"]
    assert merged[0]["penalty"] == 120


def test_keyed_list_entry_can_be_removed() -> None:
    base = {"time_factors": [{"name": "am", "factor": 0.7}, {"name": "pm", "factor": 0.8}]}
    override = {"time_factors": [{"name": "am", "remove": True}]}
    merged = deep_merge(base, override)["time_factors"]
    assert [t["name"] for t in merged] == ["pm"]


def test_keyed_list_entry_without_its_key_is_an_error() -> None:
    base = {"zones": [{"id": "a"}]}
    with pytest.raises(SpecError, match="must be a mapping with a 'id' field"):
        deep_merge(base, {"zones": [{"penalty": 10}]})


def test_two_level_chain_applies_overrides_in_order(tmp_path: Path) -> None:
    write_spec(
        tmp_path,
        "base",
        {
            "version": 1,
            "name": "base",
            "mode": "car",
            "speeds": {"default": 30, "highway": {"primary": 60, "residential": 30}},
            "turn": {"u_turn_penalty": "20 s"},
        },
    )
    write_spec(
        tmp_path,
        "middle",
        {
            "version": 1,
            "name": "middle",
            "extends": "base",
            "speeds": {"highway": {"primary": 55}},
            "turn": {"u_turn_penalty": "40 s"},
        },
    )
    leaf = write_spec(
        tmp_path,
        "leaf",
        {"version": 1, "name": "leaf", "extends": "middle", "speeds": {"highway": {"primary": 45}}},
    )
    spec, chain = load_spec(leaf)
    assert [p.stem for p in chain] == ["base", "middle", "leaf"]
    assert spec.speeds.highway["primary"] == 45
    assert spec.speeds.highway["residential"] == 30
    assert spec.turn.u_turn_penalty == 40


def test_child_name_is_never_inherited(tmp_path: Path) -> None:
    """Inheriting a base's name would overwrite the base's generated files."""
    write_spec(tmp_path, "base", {"version": 1, "name": "base", "mode": "car"})
    leaf = write_spec(tmp_path, "leaf", {"version": 1, "name": "leaf", "extends": "base"})
    spec, _ = load_spec(leaf)
    assert spec.name == "leaf"


def test_extends_resolves_a_bundled_profile(tmp_path: Path) -> None:
    leaf = write_spec(
        tmp_path,
        "mine",
        {"version": 1, "name": "mine", "extends": "truck", "speeds": {"highway": {"motorway": 70}}},
    )
    spec, chain = load_spec(leaf)
    assert spec.mode == "truck"
    assert spec.vehicle.weight == 40000
    assert spec.speeds.highway["motorway"] == 70
    assert chain[0].stem == "truck"


def test_local_file_wins_over_a_bundled_name(tmp_path: Path) -> None:
    """Vendoring a base profile must not require renaming every child spec."""
    write_spec(
        tmp_path,
        "truck",
        {"version": 1, "name": "truck", "mode": "truck", "speeds": {"default": 11}},
    )
    leaf = write_spec(tmp_path, "mine", {"version": 1, "name": "mine", "extends": "truck"})
    spec, _ = load_spec(leaf)
    assert spec.speeds.default == 11
    assert spec.vehicle.weight is None


def test_unknown_extends_suggests_a_bundled_profile(tmp_path: Path) -> None:
    leaf = write_spec(tmp_path, "mine", {"version": 1, "name": "mine", "extends": "truk"})
    with pytest.raises(SpecError) as excinfo:
        load_spec(leaf)
    assert "did you mean 'truck'?" in excinfo.value.issues[0].hint


def test_circular_extends_is_detected(tmp_path: Path) -> None:
    write_spec(tmp_path, "a", {"version": 1, "name": "a", "extends": "b"})
    write_spec(tmp_path, "b", {"version": 1, "name": "b", "extends": "a"})
    with pytest.raises(SpecError, match="circular 'extends' chain"):
        load_spec(tmp_path / "a.yaml")


def test_non_string_extends_is_rejected(tmp_path: Path) -> None:
    leaf = write_spec(tmp_path, "mine", {"version": 1, "name": "mine", "extends": 3})
    with pytest.raises(SpecError, match="extends must be a string"):
        load_spec(leaf)


def test_partial_override_alone_would_not_validate(tmp_path: Path) -> None:
    """Layers are merged before validation; a child on its own is legal only because
    the base supplies the rest."""
    document, _, chain = flatten(
        write_spec(tmp_path, "leaf", {"version": 1, "name": "leaf", "extends": "car"})
    )
    assert len(chain) == 2
    assert document["speeds"]["highway"]["motorway"] == "110 km/h"


def test_ev_delivery_inherits_through_two_levels() -> None:
    """The bundled three-level chain is the real regression case for merging."""
    spec = load_bundled("ev-delivery")
    assert spec.mode == "van"
    # from ev-delivery
    assert spec.vehicle.weight == 4250
    assert spec.speeds.highway["motorway"] == 90
    # from van-urban
    assert spec.access.allow_through_destination is True
    assert {t.name for t in spec.time_factors} == {"am-peak", "pm-peak", "overnight"}
    # from car
    assert spec.surfaces["cobblestone"] == 0.55
    assert spec.access.barriers["gate"].action == "block"


def test_deep_merge_of_non_dict_override_replaces() -> None:
    assert deep_merge({"a": {"b": 1}}, {"a": 5}) == {"a": 5}


def test_merge_preserves_base_ordering_of_keyed_lists() -> None:
    base: dict[str, Any] = {"zones": [{"id": "z"}, {"id": "a"}]}
    merged = deep_merge(base, {"zones": [{"id": "a", "penalty": 1}]})
    assert [z["id"] for z in merged["zones"]] == ["z", "a"]
