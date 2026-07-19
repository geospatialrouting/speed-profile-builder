"""Lint rules: each fires on a crafted bad spec and stays silent on a good one."""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

from speed_profile_builder.errors import LintFinding
from speed_profile_builder.lint import RULES, lint_profile
from speed_profile_builder.model import Profile
from speed_profile_builder.render import render_lint
from speed_profile_builder.spec.loader import bundled_profiles, load_spec

from .conftest import profile_from


def rules_fired(profile: Profile) -> set[str]:
    return {f.rule for f in lint_profile(profile)}


def _mutate(document: dict[str, Any], **changes: Any) -> Profile:
    return profile_from({**document, **changes})


# ------------------------------------------------------------------ clean input


def test_a_good_profile_produces_no_findings(good_profile: Profile) -> None:
    assert lint_profile(good_profile) == []


@pytest.mark.parametrize("name", sorted(bundled_profiles()))
def test_bundled_profiles_have_no_lint_errors(name: str) -> None:
    """Shipping a base profile that trips our own error rules would be indefensible."""
    from speed_profile_builder.model import build_profile

    spec, chain = load_spec(bundled_profiles()[name])
    findings = lint_profile(build_profile(spec, chain))
    assert [f for f in findings if f.severity == "error"] == []


# ------------------------------------------------------------------- each rule


def test_speed_above_legal_max_fires(good_document: dict[str, Any]) -> None:
    good_document["speeds"]["highway"]["motorway"] = 130
    assert "speed-above-legal-max" in rules_fired(profile_from(good_document))


def test_speed_above_legal_max_also_checks_the_default(good_document: dict[str, Any]) -> None:
    good_document["speeds"]["max_legal"] = 20
    findings = [f for f in lint_profile(profile_from(good_document)) if f.path == "speeds.default"]
    assert findings and findings[0].rule == "speed-above-legal-max"


def test_implausible_speed_fires_for_the_mode() -> None:
    profile = profile_from(
        {
            "version": 1,
            "name": "bike",
            "mode": "bicycle",
            "speeds": {"highway": {"cycleway": 90}},
        }
    )
    findings = [f for f in lint_profile(profile) if f.rule == "implausible-speed"]
    assert findings and findings[0].severity == "error"


def test_impassable_zone_penalty_fires(good_document: dict[str, Any]) -> None:
    good_document["zones"] = [{"id": "lez", "tag": "lez", "action": "penalty", "penalty": "2 h"}]
    assert "impassable-penalty" in rules_fired(profile_from(good_document))


def test_impassable_barrier_penalty_fires(good_document: dict[str, Any]) -> None:
    good_document["access"] = {
        "barriers": {"lift_gate": {"action": "penalty", "penalty": "4000 s"}}
    }
    assert "impassable-penalty" in rules_fired(profile_from(good_document))


def test_impassable_u_turn_penalty_fires(good_document: dict[str, Any]) -> None:
    good_document["turn"] = {"u_turn_penalty": "3 h"}
    assert "impassable-penalty" in rules_fired(profile_from(good_document))


def test_contradictory_zones_fire_as_an_error(good_document: dict[str, Any]) -> None:
    good_document["zones"] = [
        {"id": "a", "tag": "lez", "action": "avoid"},
        {"id": "b", "tag": "lez", "action": "penalty", "penalty": "60 s"},
    ]
    findings = [
        f for f in lint_profile(profile_from(good_document)) if f.rule == "contradictory-zones"
    ]
    assert findings and findings[0].severity == "error"


def test_zones_with_the_same_action_only_warn(good_document: dict[str, Any]) -> None:
    good_document["zones"] = [
        {"id": "a", "tag": "lez", "action": "penalty", "penalty": "60 s"},
        {"id": "b", "tag": "lez", "action": "penalty", "penalty": "90 s"},
    ]
    findings = [
        f for f in lint_profile(profile_from(good_document)) if f.rule == "contradictory-zones"
    ]
    assert findings and findings[0].severity == "warning"


def test_unreachable_time_factor_fires_for_unknown_classes(good_document: dict[str, Any]) -> None:
    good_document["time_factors"] = [
        {"name": "peak", "factor": 0.8, "highway": ["motorway_link", "steps"]}
    ]
    assert "unreachable-time-factor" in rules_fired(profile_from(good_document))


def test_neutral_time_factor_is_reported_as_info(good_document: dict[str, Any]) -> None:
    good_document["time_factors"] = [{"name": "noop", "factor": 1.0}]
    findings = [
        f for f in lint_profile(profile_from(good_document)) if f.rule == "unreachable-time-factor"
    ]
    assert findings and findings[0].severity == "info"


def test_dead_zone_rule_fires_for_a_sub_second_penalty(good_document: dict[str, Any]) -> None:
    good_document["zones"] = [{"id": "lez", "tag": "lez", "action": "penalty", "penalty": 0.5}]
    assert "dead-zone-rule" in rules_fired(profile_from(good_document))


def test_polygon_zone_is_flagged_for_osrm(good_document: dict[str, Any]) -> None:
    good_document["zones"] = [
        {"id": "core", "action": "avoid", "polygon": [[-0.1, 51.5], [-0.1, 51.6], [-0.2, 51.6]]}
    ]
    assert "polygon-zone-osrm" in rules_fired(profile_from(good_document))


def test_network_fragmentation_fires_for_blocked_destination(good_document: dict[str, Any]) -> None:
    good_document["access"] = {
        "allowed": ["yes", "designated"],
        "blocked": ["no", "private", "destination"],
    }
    assert "network-fragmentation" in rules_fired(profile_from(good_document))


def test_blocking_yes_is_an_error(good_document: dict[str, Any]) -> None:
    good_document["access"] = {"allowed": ["designated"], "blocked": ["no", "yes"]}
    findings = [
        f for f in lint_profile(profile_from(good_document)) if f.rule == "network-fragmentation"
    ]
    assert any(f.severity == "error" for f in findings)


def test_gate_blocking_a_bicycle_profile_warns() -> None:
    profile = profile_from(
        {
            "version": 1,
            "name": "bike",
            "mode": "bicycle",
            "speeds": {"highway": {"cycleway": 18}},
            "access": {"barriers": {"gate": "block"}},
        }
    )
    assert "network-fragmentation" in rules_fired(profile)


def test_missing_surface_handling_fires_when_absent(good_document: dict[str, Any]) -> None:
    good_document.pop("surfaces")
    assert "missing-surface-handling" in rules_fired(profile_from(good_document))


def test_missing_tracktypes_with_a_track_speed_fires(good_document: dict[str, Any]) -> None:
    good_document.pop("tracktypes")
    findings = [
        f
        for f in lint_profile(profile_from(good_document))
        if f.rule == "missing-surface-handling" and f.path == "tracktypes"
    ]
    assert findings


def test_foot_profiles_are_exempt_from_surface_rules() -> None:
    profile = profile_from(
        {"version": 1, "name": "walk", "mode": "foot", "speeds": {"highway": {"footway": 5}}}
    )
    assert "missing-surface-handling" not in rules_fired(profile)


def test_unknown_highway_class_fires(good_document: dict[str, Any]) -> None:
    good_document["speeds"]["highway"]["motorwya"] = 100
    assert "unknown-highway" in rules_fired(profile_from(good_document))


def test_suspicious_mass_fires_for_tonnes_written_as_kilograms(
    good_document: dict[str, Any],
) -> None:
    good_document["vehicle"] = {"weight": 18}
    findings = [
        f for f in lint_profile(profile_from(good_document)) if f.rule == "suspicious-units"
    ]
    assert findings and findings[0].severity == "error"


def test_missing_vehicle_limits_fires_for_trucks() -> None:
    profile = profile_from(
        {"version": 1, "name": "t", "mode": "truck", "speeds": {"highway": {"primary": 60}}}
    )
    assert "missing-vehicle-limits" in rules_fired(profile)


def test_no_speeds_fires_on_an_empty_table() -> None:
    profile = profile_from({"version": 1, "name": "empty", "mode": "car"})
    findings = [f for f in lint_profile(profile) if f.rule == "no-speeds"]
    assert findings and findings[0].severity == "error"


def test_extreme_global_factor_fires(good_document: dict[str, Any]) -> None:
    good_document["speeds"]["global_factor"] = 0.2
    assert "extreme-global-factor" in rules_fired(profile_from(good_document))


def test_default_speed_outlier_fires(good_document: dict[str, Any]) -> None:
    good_document["speeds"]["default"] = 115
    assert "default-speed-outlier" in rules_fired(profile_from(good_document))


def test_ferry_faster_than_a_road_fires(good_document: dict[str, Any]) -> None:
    good_document["extras"]["ferry"]["speed"] = 60
    assert "ferry-sanity" in rules_fired(profile_from(good_document))


def test_ferry_without_a_penalty_is_info(good_document: dict[str, Any]) -> None:
    good_document["extras"]["ferry"]["penalty"] = 0
    findings = [f for f in lint_profile(profile_from(good_document)) if f.rule == "ferry-sanity"]
    assert findings and findings[0].severity == "info"


def test_ignoring_turn_restrictions_is_an_error(good_document: dict[str, Any]) -> None:
    good_document["turn"]["restrictions"] = "ignore"
    findings = [
        f for f in lint_profile(profile_from(good_document)) if f.rule == "turn-restriction-bypass"
    ]
    assert findings and findings[0].severity == "error"


# ------------------------------------------------------------------- selection


def test_every_registered_rule_has_a_matching_finding_name(good_document: dict[str, Any]) -> None:
    """A rule whose findings carry another name could never be selected or ignored."""
    hostile = profile_from(
        {
            "version": 1,
            "name": "hostile",
            "mode": "truck",
            "speeds": {
                "default": 390,
                "max_legal": 60,
                "highway": {"motorwya": 300, "track": 20},
                "global_factor": 5,
            },
            "access": {"allowed": ["designated"], "blocked": ["no", "yes", "destination"]},
            "turn": {"u_turn_penalty": "5 h", "restrictions": "ignore"},
            "zones": [
                {"id": "a", "tag": "lez", "action": "avoid"},
                {"id": "b", "tag": "lez", "action": "penalty", "penalty": "9000 s"},
                {"id": "c", "action": "avoid", "polygon": [[0, 0], [1, 0], [1, 1]]},
                {"id": "d", "tag": "tiny", "action": "penalty", "penalty": 0.5},
            ],
            "time_factors": [{"name": "noop", "factor": 1.0}],
            "extras": {"ferry": {"allowed": True, "speed": 90, "penalty": 0}},
        }
    )
    fired = rules_fired(hostile) | rules_fired(
        profile_from({**good_document, "vehicle": {"weight": 18}})
    )
    fired |= rules_fired(profile_from({"version": 1, "name": "e", "mode": "car"}))
    missing = set(RULES) - fired
    assert missing == set(), f"rules that never fired: {sorted(missing)}"


def test_select_restricts_to_named_rules(good_document: dict[str, Any]) -> None:
    good_document["speeds"]["highway"]["motorway"] = 130
    good_document["speeds"]["global_factor"] = 0.2
    findings = lint_profile(profile_from(good_document), select=["extreme-global-factor"])
    assert {f.rule for f in findings} == {"extreme-global-factor"}


def test_ignore_removes_named_rules(good_document: dict[str, Any]) -> None:
    good_document["speeds"]["global_factor"] = 0.2
    findings = lint_profile(profile_from(good_document), ignore=["extreme-global-factor"])
    assert findings == []


def test_unknown_rule_name_is_rejected(good_profile: Profile) -> None:
    with pytest.raises(KeyError, match="unknown lint rule 'nope'"):
        lint_profile(good_profile, select=["nope"])


def test_min_severity_filters_out_info(good_document: dict[str, Any]) -> None:
    good_document["extras"]["ferry"]["penalty"] = 0
    assert lint_profile(profile_from(good_document), min_severity="warning") == []
    assert lint_profile(profile_from(good_document), min_severity="info")


def test_findings_are_sorted_by_severity(good_document: dict[str, Any]) -> None:
    good_document["speeds"]["highway"]["motorwya"] = 100
    good_document["turn"]["restrictions"] = "ignore"
    severities = [f.severity for f in lint_profile(profile_from(good_document))]
    assert severities == sorted(severities, key=lambda s: {"error": 0, "warning": 1, "info": 2}[s])


def test_is_error_helper() -> None:
    assert LintFinding("r", "error", "m").is_error
    assert not LintFinding("r", "warning", "m").is_error


# --------------------------------------------------------------------- render


def _capture(findings: list[LintFinding], fmt: str) -> str:
    console = Console(file=io.StringIO(), width=200, color_system=None)
    render_lint(findings, fmt, console, "p")
    return console.file.getvalue()  # type: ignore[union-attr]


def test_render_reports_a_clean_profile(good_profile: Profile) -> None:
    assert "clean" in _capture(lint_profile(good_profile), "table")
    assert "No lint findings" in _capture(lint_profile(good_profile), "markdown")


def test_render_json_carries_the_summary(good_document: dict[str, Any]) -> None:
    import json

    good_document["turn"]["restrictions"] = "ignore"
    payload = json.loads(_capture(lint_profile(profile_from(good_document)), "json"))
    assert payload["summary"]["error"] == 1
    assert payload["findings"][0]["rule"] == "turn-restriction-bypass"


def test_render_markdown_escapes_pipes(good_document: dict[str, Any]) -> None:
    findings = [LintFinding("r", "warning", "a | b", "path", "hint")]
    text = _capture(findings, "markdown")
    assert "a \\| b" in text
