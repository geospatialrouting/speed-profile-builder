"""Schema validation: success paths and every failure path we promise to catch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from speed_profile_builder.errors import SpecError
from speed_profile_builder.spec.loader import LineIndex, load_spec, validate_document

from .conftest import write_spec


def _expect_error(document: dict[str, Any], fragment: str) -> SpecError:
    with pytest.raises(SpecError) as excinfo:
        validate_document(document)
    rendered = "\n".join(i.render() for i in excinfo.value.issues)
    assert fragment in rendered, rendered
    return excinfo.value


def test_minimal_document_validates(minimal_document: dict[str, Any]) -> None:
    spec = validate_document(minimal_document)
    assert spec.name == "minimal"
    assert spec.speeds.highway["primary"] == 60


def test_units_are_normalised_on_parse(minimal_document: dict[str, Any]) -> None:
    minimal_document["speeds"]["highway"]["primary"] = "50 mph"
    spec = validate_document(minimal_document)
    assert spec.speeds.highway["primary"] == pytest.approx(80.4672)


def test_unknown_key_is_rejected_with_a_suggestion(minimal_document: dict[str, Any]) -> None:
    minimal_document["speedz"] = {}
    error = _expect_error(minimal_document, "unknown key 'speedz'")
    assert "did you mean 'speeds'?" in error.issues[0].hint


def test_unknown_nested_key_is_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["turn"] = {"u_turn_penality": 30}
    _expect_error(minimal_document, "unknown key 'u_turn_penality'")


def test_missing_name_is_reported_as_missing(minimal_document: dict[str, Any]) -> None:
    del minimal_document["name"]
    _expect_error(minimal_document, "required key is missing")


def test_invalid_name_explains_why_it_matters(minimal_document: dict[str, Any]) -> None:
    minimal_document["name"] = "my profile!"
    _expect_error(minimal_document, "becomes the generated filename")


def test_unknown_mode_is_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["mode"] = "hovercraft"
    _expect_error(minimal_document, "hovercraft")


def test_negative_speed_is_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["speeds"]["highway"]["primary"] = -10
    _expect_error(minimal_document, "greater than 0")


def test_absurd_speed_is_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["speeds"]["highway"]["primary"] = "5000 km/h"
    _expect_error(minimal_document, "less than or equal to 400")


def test_axle_load_above_total_weight_is_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["vehicle"] = {"weight": "3 t", "axle_load": "9 t"}
    _expect_error(minimal_document, "exceeds total weight")


def test_barrier_penalty_without_penalty_action_is_rejected(
    minimal_document: dict[str, Any],
) -> None:
    minimal_document["access"] = {"barriers": {"gate": {"action": "block", "penalty": "30 s"}}}
    _expect_error(minimal_document, "cannot carry a penalty")


def test_penalty_action_without_a_penalty_is_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["access"] = {"barriers": {"gate": {"action": "penalty"}}}
    _expect_error(minimal_document, "requires a penalty greater than zero")


def test_barrier_shorthand_is_accepted(minimal_document: dict[str, Any]) -> None:
    minimal_document["access"] = {"barriers": {"gate": "block"}}
    spec = validate_document(minimal_document)
    assert spec.access.barriers["gate"].action == "block"


def test_yaml_boolean_access_values_are_restored(minimal_document: dict[str, Any]) -> None:
    """Unquoted ``yes``/``no`` in YAML arrive as booleans and must survive."""
    minimal_document["access"] = {"allowed": [True, "designated"], "blocked": [False, "private"]}
    spec = validate_document(minimal_document)
    assert spec.access.allowed == ["yes", "designated"]
    assert spec.access.blocked == ["no", "private"]


def test_empty_access_hierarchy_is_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["access"] = {"hierarchy": []}
    _expect_error(minimal_document, "at least one tag")


def test_duplicate_access_hierarchy_entry_is_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["access"] = {"hierarchy": ["access", "vehicle", "access"]}
    _expect_error(minimal_document, "duplicate tags: access")


def test_access_value_in_both_lists_is_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["access"] = {"allowed": ["yes", "private"], "blocked": ["no", "private"]}
    _expect_error(minimal_document, "both allowed and blocked: private")


def test_zone_without_a_matcher_is_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["zones"] = [{"id": "lez", "action": "penalty", "penalty": "60 s"}]
    _expect_error(minimal_document, "must define either 'tag' or 'polygon'")


def test_zone_penalty_action_needs_a_penalty(minimal_document: dict[str, Any]) -> None:
    minimal_document["zones"] = [{"id": "lez", "tag": "lez", "action": "penalty"}]
    _expect_error(minimal_document, "action 'penalty' but no penalty")


def test_zone_avoid_action_rejects_a_penalty(minimal_document: dict[str, Any]) -> None:
    minimal_document["zones"] = [{"id": "lez", "tag": "lez", "action": "avoid", "penalty": "60 s"}]
    _expect_error(minimal_document, "cannot take penalty")


def test_zone_polygon_needs_three_vertices(minimal_document: dict[str, Any]) -> None:
    minimal_document["zones"] = [
        {"id": "lez", "action": "avoid", "polygon": [[0.0, 0.0], [1.0, 1.0]]}
    ]
    _expect_error(minimal_document, "at least 3 vertices")


def test_zone_polygon_rejects_swapped_coordinates(minimal_document: dict[str, Any]) -> None:
    minimal_document["zones"] = [
        {
            "id": "lez",
            "action": "avoid",
            "polygon": [[-0.1, 151.5], [-0.1, 151.6], [-0.2, 151.6]],
        }
    ]
    _expect_error(minimal_document, "[longitude, latitude] pairs")


def test_duplicate_zone_ids_are_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["zones"] = [
        {"id": "lez", "tag": "a", "action": "avoid"},
        {"id": "lez", "tag": "b", "action": "avoid"},
    ]
    _expect_error(minimal_document, "duplicate zones id(s): lez")


def test_bad_time_range_is_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["time_factors"] = [{"name": "peak", "factor": 0.8, "hours": "7-9"}]
    _expect_error(minimal_document, "HH:MM-HH:MM")


def test_unknown_day_is_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["time_factors"] = [{"name": "peak", "factor": 0.8, "days": ["funday"]}]
    _expect_error(minimal_document, "unknown day 'funday'")


def test_cargo_bike_extras_require_bicycle_mode(minimal_document: dict[str, Any]) -> None:
    minimal_document["extras"] = {"cargo_bike": {"gradient_penalty": 2.0}}
    _expect_error(minimal_document, "only valid for mode 'bicycle'")


def test_vehicle_weight_is_rejected_for_bicycle(minimal_document: dict[str, Any]) -> None:
    minimal_document["mode"] = "bicycle"
    minimal_document["vehicle"] = {"weight": "100 kg"}
    _expect_error(minimal_document, "not meaningful for mode 'bicycle'")


def test_u_turn_penalty_with_u_turns_disabled_is_rejected(
    minimal_document: dict[str, Any],
) -> None:
    minimal_document["turn"] = {"allow_u_turns": False, "u_turn_penalty": "60 s"}
    _expect_error(minimal_document, "meaningless when allow_u_turns is false")


def test_bad_tracktype_key_is_rejected(minimal_document: dict[str, Any]) -> None:
    minimal_document["tracktypes"] = {"grade9": 0.2}
    _expect_error(minimal_document, "grade1..grade5")


def test_every_issue_is_reported_not_just_the_first(minimal_document: dict[str, Any]) -> None:
    minimal_document["speedz"] = 1
    minimal_document["turnz"] = 2
    with pytest.raises(SpecError) as excinfo:
        validate_document(minimal_document)
    assert len(excinfo.value.issues) == 2


def test_errors_carry_line_numbers_from_the_source(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "version: 1\nname: bad\nmode: car\nspeeds:\n  highway:\n    primary: -5\n",
        encoding="utf-8",
    )
    with pytest.raises(SpecError) as excinfo:
        load_spec(path)
    issue = excinfo.value.issues[0]
    assert issue.line == 6
    assert issue.path == "speeds.highway.primary"
    assert "bad.yaml:6" in issue.render()


def test_invalid_yaml_reports_the_mark(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("name: x\n  bad indent: 1\n", encoding="utf-8")
    with pytest.raises(SpecError, match="invalid YAML"):
        load_spec(path)


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(SpecError, match="empty"):
        load_spec(path)


def test_non_mapping_document_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(SpecError, match="must be a YAML mapping"):
        load_spec(path)


def test_missing_file_is_reported_cleanly(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match="cannot read spec"):
        load_spec(tmp_path / "nope.yaml")


def test_line_index_falls_back_to_the_nearest_parent(tmp_path: Path) -> None:
    text = "version: 1\nname: x\nspeeds:\n  highway:\n    primary: 60\n"
    index = LineIndex.from_text(text, tmp_path / "x.yaml")
    assert index.locate("speeds.highway.primary") == (5, 5)
    assert index.locate("speeds.highway.missing") == (4, 3)
    assert index.locate("totally.unknown") == (None, None)


def test_valid_spec_from_disk_round_trips(tmp_path: Path, good_document: dict[str, Any]) -> None:
    path = write_spec(tmp_path, "good", good_document)
    spec, chain = load_spec(path)
    assert spec.name == "good"
    assert chain == [path.resolve()]
