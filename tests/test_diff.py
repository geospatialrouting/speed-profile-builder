"""Diffing two resolved profiles."""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

from speed_profile_builder.diff import ChangeKind, diff_profiles
from speed_profile_builder.render import render_diff
from speed_profile_builder.stock import stock_for_mode, stock_names, stock_profile

from .conftest import profile_from


def _capture(diff: Any, fmt: str) -> str:
    console = Console(file=io.StringIO(), width=200, color_system=None)
    render_diff(diff, fmt, console)
    return console.file.getvalue()  # type: ignore[union-attr]


def test_identical_profiles_produce_no_changes(minimal_document: dict[str, Any]) -> None:
    profile = profile_from(minimal_document)
    result = diff_profiles(profile, profile_from(minimal_document))
    assert result.is_empty
    assert result.summary() == {"added": 0, "removed": 0, "changed": 0}


def test_no_false_positives_from_reordering(minimal_document: dict[str, Any]) -> None:
    """Two specs listing the same speeds in a different order are the same profile."""
    reordered = {
        **minimal_document,
        "speeds": {"default": 30, "highway": {"primary": 60, "residential": 30}},
    }
    assert diff_profiles(profile_from(minimal_document), profile_from(reordered)).is_empty


def test_changed_value_is_detected_with_a_percentage(minimal_document: dict[str, Any]) -> None:
    after = {
        **minimal_document,
        "speeds": {"default": 30, "highway": {"primary": 30, "residential": 30}},
    }
    result = diff_profiles(profile_from(minimal_document), profile_from(after))
    change = next(c for c in result.changes if c.key == "speeds.highway.primary")
    assert change.kind is ChangeKind.CHANGED
    assert change.before == 60
    assert change.after == 30
    assert change.percent == pytest.approx(-50.0)


def test_added_key_is_detected(minimal_document: dict[str, Any]) -> None:
    after = {**minimal_document, "surfaces": {"gravel": 0.5}}
    result = diff_profiles(profile_from(minimal_document), profile_from(after))
    assert [c.key for c in result.of_kind(ChangeKind.ADDED)] == ["surfaces.gravel"]
    assert result.of_kind(ChangeKind.ADDED)[0].percent is None


def test_removed_key_is_detected(minimal_document: dict[str, Any]) -> None:
    before = {**minimal_document, "surfaces": {"gravel": 0.5}}
    result = diff_profiles(profile_from(before), profile_from(minimal_document))
    assert [c.key for c in result.of_kind(ChangeKind.REMOVED)] == ["surfaces.gravel"]


def test_float_noise_is_not_reported(minimal_document: dict[str, Any]) -> None:
    after = {
        **minimal_document,
        "speeds": {"default": 30, "highway": {"primary": 60.0000000000001, "residential": 30}},
    }
    assert diff_profiles(profile_from(minimal_document), profile_from(after)).is_empty


def test_boolean_change_is_reported_without_a_percentage(
    minimal_document: dict[str, Any],
) -> None:
    after = {**minimal_document, "access": {"respect_oneway": False}}
    result = diff_profiles(profile_from(minimal_document), profile_from(after))
    change = next(c for c in result.changes if c.key == "access.respect_oneway")
    assert change.percent is None
    assert change.before is True


def test_percent_is_none_when_the_baseline_is_zero(minimal_document: dict[str, Any]) -> None:
    before = {**minimal_document, "turn": {"base_penalty": 0}}
    after = {**minimal_document, "turn": {"base_penalty": "10 s"}}
    result = diff_profiles(profile_from(before), profile_from(after))
    change = next(c for c in result.changes if c.key == "turn.base_penalty_s")
    assert change.percent is None


def test_changes_are_grouped_by_section(minimal_document: dict[str, Any]) -> None:
    after = {**minimal_document, "surfaces": {"gravel": 0.5}, "turn": {"base_penalty": "9 s"}}
    sections = diff_profiles(profile_from(minimal_document), profile_from(after)).by_section()
    assert set(sections) == {"surfaces", "turn"}


def test_diff_against_a_stock_profile_finds_real_differences() -> None:
    from speed_profile_builder.model import build_profile
    from speed_profile_builder.spec.loader import load_bundled

    profile = build_profile(load_bundled("truck"))
    result = diff_profiles(stock_for_mode("truck"), profile)
    keys = {c.key for c in result.changes}
    assert "speeds.highway.motorway" in keys
    assert "vehicle.weight_kg" in keys


@pytest.mark.parametrize("name", stock_names())
def test_every_stock_profile_builds(name: str) -> None:
    profile = stock_profile(name)
    assert profile.speeds_kmh
    assert profile.name.startswith("stock-")


def test_unknown_stock_profile_is_reported() -> None:
    from speed_profile_builder.errors import SpecError

    with pytest.raises(SpecError, match="no stock profile named 'lorry'"):
        stock_profile("lorry")


def test_json_render_round_trips(minimal_document: dict[str, Any]) -> None:
    import json

    after = {**minimal_document, "surfaces": {"gravel": 0.5}}
    result = diff_profiles(profile_from(minimal_document), profile_from(after))
    payload = json.loads(_capture(result, "json"))
    assert payload["summary"]["added"] == 1
    assert payload["changes"][0]["key"] == "surfaces.gravel"


def test_markdown_render_is_a_table(minimal_document: dict[str, Any]) -> None:
    after = {**minimal_document, "surfaces": {"gravel": 0.5}}
    text = _capture(diff_profiles(profile_from(minimal_document), profile_from(after)), "markdown")
    assert "| `+` | `surfaces.gravel` |" in text


def test_table_render_says_so_when_nothing_changed(minimal_document: dict[str, Any]) -> None:
    profile = profile_from(minimal_document)
    assert "no differences" in _capture(diff_profiles(profile, profile), "table")


def test_markdown_render_says_so_when_nothing_changed(minimal_document: dict[str, Any]) -> None:
    profile = profile_from(minimal_document)
    assert "No differences" in _capture(diff_profiles(profile, profile), "markdown")
