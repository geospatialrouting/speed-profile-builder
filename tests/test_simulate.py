"""Simulating a profile against sample ways."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from speed_profile_builder.errors import SpecError
from speed_profile_builder.model import Profile, build_profile
from speed_profile_builder.render import render_comparison, render_simulation
from speed_profile_builder.simulate import (
    WaySample,
    compare,
    evaluate,
    load_samples,
    parse_samples_csv,
    simulate,
)
from speed_profile_builder.spec.loader import load_bundled

from .conftest import profile_from


@pytest.fixture
def profile(good_document: dict[str, Any]) -> Profile:
    return profile_from(good_document)


def way(**tags: str) -> WaySample:
    return WaySample(name="w", tags=dict(tags))


# ------------------------------------------------------------------- speeds


def test_class_speed_is_used(profile: Profile) -> None:
    result = evaluate(profile, way(highway="primary"))
    assert result.routable
    assert result.speed_kmh == 70


def test_unlisted_class_falls_back_to_the_default_only_with_access(profile: Profile) -> None:
    """Mirrors OSRM: an unknown class without an access tag is not routable."""
    assert not evaluate(profile, way(highway="bridleway")).routable
    allowed = evaluate(profile, way(highway="bridleway", access="yes"))
    assert allowed.routable
    assert allowed.speed_kmh == profile.default_speed_kmh


def test_surface_multiplier_applies(profile: Profile) -> None:
    result = evaluate(profile, way(highway="primary", surface="gravel"))
    assert result.speed_kmh == pytest.approx(70 * 0.6)


def test_unknown_surface_is_neutral(profile: Profile) -> None:
    assert evaluate(profile, way(highway="primary", surface="regolith")).speed_kmh == 70


def test_tracktype_and_smoothness_compound(good_document: dict[str, Any]) -> None:
    good_document["smoothness"] = {"bad": 0.5}
    profile = profile_from(good_document)
    result = evaluate(profile, way(highway="primary", tracktype="grade3", smoothness="bad"))
    assert result.speed_kmh == pytest.approx(70 * 0.6 * 0.5)


def test_global_factor_scales_every_speed(good_document: dict[str, Any]) -> None:
    good_document["speeds"]["global_factor"] = 0.5
    result = evaluate(profile_from(good_document), way(highway="primary"))
    assert result.speed_kmh == pytest.approx(35)


def test_posted_maxspeed_caps_but_never_raises(profile: Profile) -> None:
    slower = evaluate(profile, way(highway="primary", maxspeed="40"))
    assert slower.speed_kmh == 40
    faster = evaluate(profile, way(highway="primary", maxspeed="120"))
    assert faster.speed_kmh == 70


def test_maxspeed_in_mph_is_converted(profile: Profile) -> None:
    result = evaluate(profile, way(highway="primary", maxspeed="30 mph"))
    assert result.speed_kmh == pytest.approx(48.28032)


@pytest.mark.parametrize("value", ["none", "signals", "variable", "not a speed"])
def test_unusable_maxspeed_values_are_ignored(profile: Profile, value: str) -> None:
    assert evaluate(profile, way(highway="primary", maxspeed=value)).speed_kmh == 70


def test_walk_maxspeed_is_interpreted(profile: Profile) -> None:
    assert evaluate(profile, way(highway="primary", maxspeed="walk")).speed_kmh == 7


def test_legal_ceiling_clamps_the_result(good_document: dict[str, Any]) -> None:
    good_document["speeds"]["max_legal"] = 50
    result = evaluate(profile_from(good_document), way(highway="motorway"))
    assert result.speed_kmh == 50
    assert any("clamped" in step for step in result.steps)


# ------------------------------------------------------------------- access


def test_access_denial_blocks_the_way(profile: Profile) -> None:
    result = evaluate(profile, way(highway="primary", access="private"))
    assert not result.routable
    assert "access=private" in result.reason


def test_more_specific_access_tag_wins() -> None:
    """hgv=no on an otherwise open road must block a lorry."""
    profile = build_profile(load_bundled("truck"))
    assert not evaluate(profile, way(highway="primary", access="yes", hgv="no")).routable
    assert evaluate(profile, way(highway="primary", access="no", hgv="yes")).routable


def test_unrecognised_access_value_falls_through(profile: Profile) -> None:
    result = evaluate(profile, way(highway="primary", access="discouraged"))
    assert result.routable
    assert result.speed_kmh == 70


def test_destination_only_adds_a_penalty(profile: Profile) -> None:
    result = evaluate(profile, way(highway="primary", access="destination"))
    assert result.routable
    assert result.penalty_s == 600


def test_through_destination_removes_the_penalty(good_document: dict[str, Any]) -> None:
    good_document["access"] = {"allow_through_destination": True}
    result = evaluate(profile_from(good_document), way(highway="primary", access="destination"))
    assert result.penalty_s == 0


# --------------------------------------------------------------- dimensions


def test_low_bridge_blocks_a_tall_vehicle() -> None:
    profile = build_profile(load_bundled("truck"))
    blocked = evaluate(profile, way(highway="primary", maxheight="3.2"))
    assert not blocked.routable
    assert "maxheight" in blocked.reason
    assert evaluate(profile, way(highway="primary", maxheight="4.5")).routable


def test_weight_limit_is_read_as_tonnes() -> None:
    """OSM maxweight is tonnes; reading it as kilograms would open every bridge."""
    profile = build_profile(load_bundled("truck"))
    assert not evaluate(profile, way(highway="secondary", maxweight="7.5")).routable
    assert evaluate(profile, way(highway="secondary", maxweight="44")).routable


def test_weight_limit_with_an_explicit_kg_unit() -> None:
    profile = build_profile(load_bundled("truck"))
    assert not evaluate(profile, way(highway="secondary", maxweight="7500 kg")).routable


def test_unparseable_restriction_does_not_block(profile: Profile) -> None:
    truck = build_profile(load_bundled("truck"))
    assert evaluate(truck, way(highway="primary", maxheight="default")).routable


def test_axle_load_limit_is_enforced() -> None:
    profile = build_profile(load_bundled("truck"))
    assert not evaluate(profile, way(highway="primary", maxaxleload="8")).routable


# ------------------------------------------------------------ zones and tolls


def test_zone_penalty_is_added(good_document: dict[str, Any]) -> None:
    good_document["zones"] = [
        {"id": "lez", "tag": "low_emission_zone", "action": "penalty", "penalty": "120 s"}
    ]
    result = evaluate(profile_from(good_document), way(highway="primary", low_emission_zone="yes"))
    assert result.penalty_s == 120
    assert result.cost_s == pytest.approx(result.duration_s + 120)


def test_zone_avoid_blocks_the_way(good_document: dict[str, Any]) -> None:
    good_document["zones"] = [{"id": "lez", "tag": "low_emission_zone", "action": "avoid"}]
    result = evaluate(profile_from(good_document), way(highway="primary", low_emission_zone="yes"))
    assert not result.routable
    assert "lez" in result.reason


def test_zone_value_match_is_exact(good_document: dict[str, Any]) -> None:
    good_document["zones"] = [
        {"id": "z", "tag": "zone", "value": "core", "action": "avoid"},
    ]
    profile = profile_from(good_document)
    assert not evaluate(profile, way(highway="primary", zone="core")).routable
    assert evaluate(profile, way(highway="primary", zone="fringe")).routable


def test_toll_penalty_is_added(good_document: dict[str, Any]) -> None:
    good_document["extras"]["toll"] = {"avoid": False, "penalty": "90 s"}
    result = evaluate(profile_from(good_document), way(highway="motorway", toll="yes"))
    assert result.penalty_s == 90


def test_toll_avoidance_blocks_the_way(good_document: dict[str, Any]) -> None:
    good_document["extras"]["toll"] = {"avoid": True}
    assert not evaluate(profile_from(good_document), way(highway="motorway", toll="yes")).routable


# ------------------------------------------------------------------ ferries


def test_ferry_uses_the_ferry_speed_and_penalty(profile: Profile) -> None:
    result = evaluate(profile, WaySample("f", {"route": "ferry"}, length_m=6000))
    assert result.speed_kmh == 12
    assert result.penalty_s == 300
    assert result.duration_s == pytest.approx(6 / 12 * 3600)


def test_disallowed_ferry_is_not_routable(good_document: dict[str, Any]) -> None:
    good_document["extras"]["ferry"]["allowed"] = False
    result = evaluate(profile_from(good_document), WaySample("f", {"route": "ferry"}))
    assert not result.routable


def test_a_way_with_no_highway_and_no_route_is_not_routable(profile: Profile) -> None:
    assert not evaluate(profile, WaySample("x", {"name": "nowhere"})).routable


# --------------------------------------------------------------- time factors


def test_time_factor_applies_inside_its_window(good_document: dict[str, Any]) -> None:
    good_document["time_factors"] = [
        {"name": "am", "factor": 0.5, "hours": "07:00-09:59", "days": ["mo"]}
    ]
    profile = profile_from(good_document)
    peak = evaluate(profile, way(highway="primary"), day="mo", minute=8 * 60)
    off = evaluate(profile, way(highway="primary"), day="mo", minute=13 * 60)
    assert peak.speed_kmh == pytest.approx(35)
    assert off.speed_kmh == 70


def test_time_factors_are_skipped_without_a_minute(good_document: dict[str, Any]) -> None:
    good_document["time_factors"] = [{"name": "am", "factor": 0.5}]
    assert evaluate(profile_from(good_document), way(highway="primary")).speed_kmh == 70


# ------------------------------------------------------------------ compare


def test_compare_reports_cost_and_speed_deltas(good_document: dict[str, Any]) -> None:
    slower = {**good_document, "speeds": {**good_document["speeds"], "global_factor": 0.5}}
    comparisons = compare(
        profile_from(good_document), profile_from(slower), [way(highway="primary")]
    )
    only = comparisons[0]
    assert only.speed_delta_pct == pytest.approx(-50.0)
    assert only.cost_delta_pct == pytest.approx(100.0)
    assert not only.routability_changed


def test_compare_flags_a_way_that_became_blocked(good_document: dict[str, Any]) -> None:
    stricter = {
        **good_document,
        "access": {"allowed": ["yes", "designated"], "blocked": ["no", "private", "permissive"]},
    }
    comparisons = compare(
        profile_from(good_document),
        profile_from(stricter),
        [way(highway="primary", access="permissive")],
    )
    assert comparisons[0].routability_changed
    assert comparisons[0].cost_delta_pct is None


def test_simulate_preserves_input_order(profile: Profile) -> None:
    samples = [way(highway=h) for h in ("primary", "residential", "motorway")]
    results = simulate(profile, samples)
    assert [r.sample.tags["highway"] for r in results] == ["primary", "residential", "motorway"]


# ------------------------------------------------------------------- loading


def test_yaml_samples_load(tmp_path: Path) -> None:
    path = tmp_path / "ways.yaml"
    path.write_text(
        "ways:\n  - name: a\n    tags: {highway: primary}\n"
        "  - name: b\n    length_m: 500\n    tags: {highway: track, tracktype: grade5}\n",
        encoding="utf-8",
    )
    samples = load_samples(path)
    assert [s.name for s in samples] == ["a", "b"]
    assert samples[1].length_m == 500


def test_yaml_samples_accept_a_bare_list(tmp_path: Path) -> None:
    path = tmp_path / "ways.yaml"
    path.write_text("- tags: {highway: primary}\n", encoding="utf-8")
    assert load_samples(path)[0].name == "way-0"


def test_yaml_boolean_tag_values_become_osm_strings(tmp_path: Path) -> None:
    path = tmp_path / "ways.yaml"
    path.write_text("- tags: {highway: primary, toll: yes}\n", encoding="utf-8")
    assert load_samples(path)[0].tags["toll"] == "yes"


def test_sample_without_tags_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ways.yaml"
    path.write_text("- name: a\n", encoding="utf-8")
    with pytest.raises(SpecError, match="no 'tags' mapping"):
        load_samples(path)


def test_empty_sample_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ways.yaml"
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(SpecError, match="non-empty list"):
        load_samples(path)


def test_missing_sample_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match="cannot read samples"):
        load_samples(tmp_path / "nope.yaml")


def test_csv_samples_load_with_sparse_columns() -> None:
    text = "name,highway,surface,maxspeed\nA road,primary,asphalt,50\nB track,track,,\n"
    samples = parse_samples_csv(text)
    assert samples[0].tags == {"highway": "primary", "surface": "asphalt", "maxspeed": "50"}
    assert samples[1].tags == {"highway": "track"}


def test_csv_length_column_is_reserved() -> None:
    samples = parse_samples_csv("name,length_m,highway\nA,250,primary\n")
    assert samples[0].length_m == 250
    assert "length_m" not in samples[0].tags


def test_csv_row_without_tags_is_rejected() -> None:
    with pytest.raises(SpecError, match="no tag columns"):
        parse_samples_csv("name,highway\nA,\n")


def test_bundled_sample_ways_all_parse() -> None:
    from speed_profile_builder.cli import DEFAULT_SAMPLES

    samples = load_samples(DEFAULT_SAMPLES)
    assert len(samples) > 15
    assert any(s.tags.get("route") == "ferry" for s in samples)


# -------------------------------------------------------------------- render


def _capture(render: Any, *args: Any) -> str:
    console = Console(file=io.StringIO(), width=240, color_system=None)
    render(*args[:-1], console, *args[-1:])
    return console.file.getvalue()  # type: ignore[union-attr]


def test_simulation_table_marks_blocked_ways(profile: Profile) -> None:
    results = simulate(profile, [way(highway="primary", access="private")])
    text = _capture(render_simulation, results, "table", profile.name)
    assert "blocked" in text


def test_simulation_json_is_machine_readable(profile: Profile) -> None:
    import json

    results = simulate(profile, [way(highway="primary")])
    payload = json.loads(_capture(render_simulation, results, "json", profile.name))
    assert payload["ways"][0]["speed_kmh"] == 70


def test_comparison_markdown_has_a_delta_column(good_document: dict[str, Any]) -> None:
    slower = {**good_document, "speeds": {**good_document["speeds"], "global_factor": 0.5}}
    comparisons = compare(
        profile_from(good_document), profile_from(slower), [way(highway="primary")]
    )
    console = Console(file=io.StringIO(), width=240, color_system=None)
    render_comparison(comparisons, "markdown", console, "before", "after")
    text = console.file.getvalue()  # type: ignore[union-attr]
    assert "cost delta" in text
    assert "+100.0%" in text
