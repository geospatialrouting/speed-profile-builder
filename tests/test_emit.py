"""Code generation: structure, determinism, and the OSRM/Valhalla contracts."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

import pytest

from speed_profile_builder.emit import EmitOptions, emit_osrm, emit_valhalla, rate_factor
from speed_profile_builder.emit.common import num, provenance
from speed_profile_builder.emit.valhalla import build_document
from speed_profile_builder.model import Profile

from .conftest import profile_from, requires_lua

OPTIONS = EmitOptions(tool_version="test")


def lua_syntax_error(source: str) -> str | None:
    """Return a syntax error message for ``source``, or ``None`` if it parses.

    Prefers a real interpreter on PATH and falls back to ``lupa``; callers must
    already have skipped when neither is present.
    """
    binary = shutil.which("luac") or shutil.which("lua") or shutil.which("luajit")
    if binary:
        flag = "-p" if binary.endswith("luac") else "-e"
        if flag == "-p":
            result = subprocess.run(
                [binary, "-p", "-"], input=source, capture_output=True, text=True, check=False
            )
        else:
            result = subprocess.run(
                [binary, "-e", "local f, err = load(io.read('a')); if not f then error(err) end"],
                input=source,
                capture_output=True,
                text=True,
                check=False,
            )
        return None if result.returncode == 0 else (result.stderr or result.stdout)
    import lupa

    runtime = lupa.LuaRuntime()
    try:
        runtime.execute("local f, err = load(...); if not f then error(err) end", source)
    except lupa.LuaError as exc:
        return str(exc)
    return None


# --------------------------------------------------------------------------- OSRM


def test_osrm_output_has_the_required_entry_points(bundled_profile: Profile) -> None:
    source = emit_osrm(bundled_profile, OPTIONS)
    for symbol in ("function setup()", "function process_node(", "function process_way("):
        assert symbol in source
    assert "function process_turn(profile, turn)" in source
    assert source.rstrip().endswith("}")
    assert "api_version = 4" in source


def test_osrm_return_table_wires_up_every_callback(bundled_profile: Profile) -> None:
    source = emit_osrm(bundled_profile, OPTIONS)
    tail = source[source.rindex("return {") :]
    for key in ("setup", "process_way", "process_node", "process_turn"):
        assert f"{key} = {key}" in tail


def test_osrm_header_records_provenance_without_a_timestamp(bundled_profile: Profile) -> None:
    source = emit_osrm(bundled_profile, OPTIONS)
    assert "GENERATED FILE - DO NOT EDIT." in source
    assert bundled_profile.fingerprint() in source
    assert f"{bundled_profile.name}.yaml" in source


def test_osrm_is_self_contained(bundled_profile: Profile) -> None:
    """No ``require`` means the profile works from any working directory."""
    assert "require(" not in emit_osrm(bundled_profile, OPTIONS)


def test_osrm_quotes_keys_that_are_not_lua_identifiers() -> None:
    profile = profile_from(
        {
            "version": 1,
            "name": "odd",
            "mode": "car",
            "speeds": {"highway": {"residential": 30}},
            "surfaces": {"concrete:plates": 0.8},
        }
    )
    assert '["concrete:plates"] = 0.8' in emit_osrm(profile, OPTIONS)


def test_osrm_notes_barriers_it_cannot_represent() -> None:
    profile = profile_from(
        {
            "version": 1,
            "name": "soft",
            "mode": "car",
            "speeds": {"highway": {"residential": 30}},
            "access": {"barriers": {"lift_gate": {"action": "penalty", "penalty": "30 s"}}},
        }
    )
    source = emit_osrm(profile, OPTIONS)
    assert "OSRM cannot charge time at a node" in source
    assert "lift_gate" in source


def test_osrm_maps_mode_to_the_right_enum() -> None:
    bike = profile_from(
        {"version": 1, "name": "b", "mode": "bicycle", "speeds": {"highway": {"cycleway": 18}}}
    )
    assert "default_mode = mode.cycling" in emit_osrm(bike, OPTIONS)
    car = profile_from(
        {"version": 1, "name": "c", "mode": "car", "speeds": {"highway": {"primary": 60}}}
    )
    assert "default_mode = mode.driving" in emit_osrm(car, OPTIONS)


def test_osrm_blocking_zone_emits_a_zero_factor() -> None:
    profile = profile_from(
        {
            "version": 1,
            "name": "z",
            "mode": "car",
            "speeds": {"highway": {"primary": 60}},
            "zones": [{"id": "core", "tag": "lez", "action": "block"}],
        }
    )
    source = emit_osrm(profile, OPTIONS)
    assert 'id = "core"' in source
    assert "block = true" in source


def test_osrm_penalty_reference_changes_the_emitted_factor() -> None:
    profile = profile_from(
        {
            "version": 1,
            "name": "z",
            "mode": "car",
            "speeds": {"highway": {"primary": 60}},
            "zones": [{"id": "lez", "tag": "lez", "action": "penalty", "penalty": "300 s"}],
        }
    )
    tight = emit_osrm(profile, EmitOptions(penalty_reference_s=300, tool_version="test"))
    loose = emit_osrm(profile, EmitOptions(penalty_reference_s=3000, tool_version="test"))
    assert "factor = 0.5 " in tight
    assert tight != loose


@requires_lua
def test_generated_lua_parses(bundled_profile: Profile) -> None:
    """The strongest available check that the output is real OSRM Lua."""
    error = lua_syntax_error(emit_osrm(bundled_profile, OPTIONS))
    assert error is None, error


# ----------------------------------------------------------------------- Valhalla


def test_valhalla_output_is_valid_json_with_a_costing_block(bundled_profile: Profile) -> None:
    document = json.loads(emit_valhalla(bundled_profile, OPTIONS))
    costing = document["costing"]
    assert costing in ("auto", "truck", "bicycle", "pedestrian", "motorcycle")
    assert costing in document["costing_options"]


def test_valhalla_records_provenance(bundled_profile: Profile) -> None:
    document = json.loads(emit_valhalla(bundled_profile, OPTIONS))
    assert document["_generated"]["fingerprint"] == bundled_profile.fingerprint()
    assert document["_generated"]["profile"] == bundled_profile.name


def test_valhalla_truck_carries_dimensions_in_tonnes() -> None:
    from speed_profile_builder.model import build_profile
    from speed_profile_builder.spec.loader import load_bundled

    profile = build_profile(load_bundled("truck"))
    options = build_document(profile, OPTIONS)["costing_options"]["truck"]
    assert options["weight"] == 40
    assert options["axle_load"] == 11.5
    assert options["height"] == 4
    assert options["axle_count"] == 5


def test_valhalla_disallowed_ferry_sets_use_ferry_to_zero() -> None:
    profile = profile_from(
        {
            "version": 1,
            "name": "noferry",
            "mode": "car",
            "speeds": {"highway": {"primary": 60}},
            "extras": {"ferry": {"allowed": False}},
        }
    )
    assert build_document(profile, OPTIONS)["costing_options"]["auto"]["use_ferry"] == 0


def test_valhalla_toll_avoidance_sets_use_tolls_to_zero() -> None:
    profile = profile_from(
        {
            "version": 1,
            "name": "notoll",
            "mode": "car",
            "speeds": {"highway": {"primary": 60}},
            "extras": {"toll": {"avoid": True}},
        }
    )
    assert build_document(profile, OPTIONS)["costing_options"]["auto"]["use_tolls"] == 0


def test_valhalla_polygon_zones_become_exclude_polygons() -> None:
    profile = profile_from(
        {
            "version": 1,
            "name": "poly",
            "mode": "car",
            "speeds": {"highway": {"primary": 60}},
            "zones": [
                {
                    "id": "core",
                    "action": "avoid",
                    "polygon": [[-0.1, 51.5], [-0.1, 51.6], [-0.2, 51.6]],
                }
            ],
        }
    )
    document = build_document(profile, OPTIONS)
    assert document["exclude_polygons"] == [[[-0.1, 51.5], [-0.1, 51.6], [-0.2, 51.6]]]


def test_valhalla_extension_block_carries_what_costing_cannot(bundled_profile: Profile) -> None:
    document = build_document(bundled_profile, OPTIONS)
    extension = document["_speed_profile"]
    assert extension["speeds_kmh"] == bundled_profile.speeds_kmh
    assert extension["global_factor"] == pytest.approx(bundled_profile.global_factor)


def test_valhalla_blocking_barrier_uses_a_finite_but_huge_penalty() -> None:
    profile = profile_from(
        {
            "version": 1,
            "name": "b",
            "mode": "car",
            "speeds": {"highway": {"primary": 60}},
            "access": {"barriers": {"gate": "block"}},
        }
    )
    options = build_document(profile, OPTIONS)["costing_options"]["auto"]
    assert options["gate_penalty"] == 43200


def test_valhalla_cargo_bike_selects_the_cargo_bicycle_type() -> None:
    from speed_profile_builder.model import build_profile
    from speed_profile_builder.spec.loader import load_bundled

    profile = build_profile(load_bundled("cargo-bike"))
    options = build_document(profile, OPTIONS)["costing_options"]["bicycle"]
    assert options["bicycle_type"] == "Cargo"
    assert 0.0 <= options["avoid_bad_surfaces"] <= 1.0


# ------------------------------------------------------------------ determinism


def test_building_twice_produces_identical_bytes(bundled_profile: Profile) -> None:
    assert emit_osrm(bundled_profile, OPTIONS) == emit_osrm(bundled_profile, OPTIONS)
    assert emit_valhalla(bundled_profile, OPTIONS) == emit_valhalla(bundled_profile, OPTIONS)


def test_reloading_the_spec_produces_identical_bytes() -> None:
    """Determinism must survive a fresh parse, not just a repeated call."""
    from speed_profile_builder.model import build_profile
    from speed_profile_builder.spec.loader import bundled_profiles, load_spec

    path = bundled_profiles()["van-urban"]
    spec_a, chain_a = load_spec(path)
    spec_b, chain_b = load_spec(path)
    assert emit_osrm(build_profile(spec_a, chain_a), OPTIONS) == emit_osrm(
        build_profile(spec_b, chain_b), OPTIONS
    )


def test_header_can_be_suppressed_for_body_comparisons(bundled_profile: Profile) -> None:
    body = emit_osrm(bundled_profile, EmitOptions(header=False, tool_version="test"))
    assert "GENERATED FILE" not in body
    document = json.loads(emit_valhalla(bundled_profile, EmitOptions(header=False)))
    assert "_generated" not in document


def test_num_formats_without_float_noise() -> None:
    assert num(0.1 + 0.2) == "0.3"
    assert num(50.0) == "50"
    assert num(1 / 3) == "0.3333"


def test_rate_factor_is_monotonic_and_bounded() -> None:
    assert rate_factor(0) == 1.0
    assert rate_factor(300, 300) == pytest.approx(0.5)
    assert 0 < rate_factor(100000, 300) < rate_factor(600, 300) < 1.0


def test_rate_factor_rejects_a_zero_reference() -> None:
    with pytest.raises(ValueError, match="reference must be positive"):
        rate_factor(60, 0)


def test_provenance_contains_no_absolute_paths() -> None:
    profile = profile_from(
        {"version": 1, "name": "p", "mode": "car", "speeds": {"highway": {"primary": 60}}}
    )
    lines = provenance(profile, "--", OPTIONS)
    assert not any("/" in line.split("spec chain:")[-1] for line in lines if "spec chain" in line)


@pytest.mark.parametrize("engine", ["osrm", "valhalla"])
def test_a_changed_speed_changes_the_output(engine: str, minimal_document: dict[str, Any]) -> None:
    emit = emit_osrm if engine == "osrm" else emit_valhalla
    before = emit(profile_from(minimal_document), OPTIONS)
    minimal_document["speeds"]["highway"]["primary"] = 61
    after = emit(profile_from(minimal_document), OPTIONS)
    assert before != after
