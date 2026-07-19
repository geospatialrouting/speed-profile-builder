"""End-to-end CLI behaviour, including exit codes used as CI gates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from speed_profile_builder.cli import main

from .conftest import write_spec


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def spec_path(tmp_path: Path, good_document: dict[str, Any]) -> Path:
    return write_spec(tmp_path, "mine", good_document)


def invoke(runner: CliRunner, *args: str) -> Any:
    """Run a command, letting exceptions surface as a non-zero exit code."""
    return runner.invoke(main, list(args))


def test_profiles_lists_the_bundled_bases(runner: CliRunner) -> None:
    result = invoke(runner, "profiles", "--no-color")
    assert result.exit_code == 0
    for name in ("car", "truck", "van-urban", "cargo-bike", "ev-delivery"):
        assert name in result.output


def test_validate_reports_the_resolved_profile(runner: CliRunner, spec_path: Path) -> None:
    result = invoke(runner, "validate", str(spec_path), "--no-color")
    assert result.exit_code == 0
    assert "valid" in result.output
    assert "fingerprint" in result.output


def test_validate_exits_two_on_a_bad_spec(runner: CliRunner, tmp_path: Path) -> None:
    path = write_spec(tmp_path, "bad", {"version": 1, "name": "bad", "speedz": 1})
    result = invoke(runner, "validate", str(path), "--no-color")
    assert result.exit_code == 2
    assert "unknown key 'speedz'" in result.output


def test_build_writes_both_engines(runner: CliRunner, spec_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = invoke(runner, "build", str(spec_path), "-o", str(out), "--no-color")
    assert result.exit_code == 0
    assert (out / "good.lua").exists()
    assert (out / "good.valhalla.json").exists()


def test_build_can_target_one_engine(runner: CliRunner, spec_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    invoke(runner, "build", str(spec_path), "-o", str(out), "--engine", "valhalla", "--no-color")
    assert not (out / "good.lua").exists()
    assert (out / "good.valhalla.json").exists()


def test_build_to_stdout_prints_both_artefacts(runner: CliRunner, spec_path: Path) -> None:
    result = invoke(runner, "build", str(spec_path), "--stdout")
    assert result.exit_code == 0
    assert "----- good.lua -----" in result.output
    assert "----- good.valhalla.json -----" in result.output


def test_build_is_byte_stable_across_invocations(
    runner: CliRunner, spec_path: Path, tmp_path: Path
) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    invoke(runner, "build", str(spec_path), "-o", str(first), "--no-color")
    invoke(runner, "build", str(spec_path), "-o", str(second), "--no-color")
    for name in ("good.lua", "good.valhalla.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_build_check_detects_stale_output(
    runner: CliRunner, spec_path: Path, tmp_path: Path, good_document: dict[str, Any]
) -> None:
    out = tmp_path / "out"
    invoke(runner, "build", str(spec_path), "-o", str(out), "--no-color")
    assert invoke(runner, "build", str(spec_path), "-o", str(out), "--check").exit_code == 0

    good_document["speeds"]["highway"]["primary"] = 55
    write_spec(tmp_path, "mine", good_document)
    stale = invoke(runner, "build", str(spec_path), "-o", str(out), "--check", "--no-color")
    assert stale.exit_code == 1
    assert "out of date" in stale.output


def test_build_rejects_a_non_positive_penalty_reference(runner: CliRunner, spec_path: Path) -> None:
    result = invoke(runner, "build", str(spec_path), "--penalty-reference", "0", "--stdout")
    assert result.exit_code == 2


def test_diff_against_stock_defaults_to_the_matching_mode(
    runner: CliRunner, spec_path: Path
) -> None:
    result = invoke(runner, "diff", str(spec_path), "--format", "json")
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["baseline"] == "stock-car"
    assert payload["summary"]["changed"] > 0


def test_diff_against_another_spec(runner: CliRunner, spec_path: Path) -> None:
    result = invoke(runner, "diff", str(spec_path), "--against", str(spec_path), "--no-color")
    assert result.exit_code == 0
    assert "no differences" in result.output


def test_diff_exit_code_flag_gates_ci(runner: CliRunner, spec_path: Path) -> None:
    assert invoke(runner, "diff", str(spec_path), "--exit-code", "--no-color").exit_code == 1
    assert (
        invoke(
            runner, "diff", str(spec_path), "--against", str(spec_path), "--exit-code", "--no-color"
        ).exit_code
        == 0
    )


def test_diff_rejects_an_unknown_baseline(runner: CliRunner, spec_path: Path) -> None:
    result = invoke(runner, "diff", str(spec_path), "--against", "spaceship", "--no-color")
    assert result.exit_code == 2
    assert "neither a stock profile" in result.output


def test_lint_is_clean_for_a_good_spec(runner: CliRunner, spec_path: Path) -> None:
    result = invoke(runner, "lint", str(spec_path), "--no-color")
    assert result.exit_code == 0
    assert "clean" in result.output


def test_lint_exits_one_on_an_error(
    runner: CliRunner, tmp_path: Path, good_document: dict[str, Any]
) -> None:
    good_document["turn"] = {"restrictions": "ignore"}
    path = write_spec(tmp_path, "bad", good_document)
    result = invoke(runner, "lint", str(path), "--format", "json")
    assert result.exit_code == 1
    assert "turn-restriction-bypass" in result.output


def test_lint_fail_on_never_always_succeeds(
    runner: CliRunner, tmp_path: Path, good_document: dict[str, Any]
) -> None:
    good_document["turn"] = {"restrictions": "ignore"}
    path = write_spec(tmp_path, "bad", good_document)
    assert invoke(runner, "lint", str(path), "--fail-on", "never", "--no-color").exit_code == 0


def test_lint_rejects_an_unknown_rule(runner: CliRunner, spec_path: Path) -> None:
    result = invoke(runner, "lint", str(spec_path), "--select", "nope", "--no-color")
    assert result.exit_code == 2
    assert "unknown lint rule" in result.output


def test_lint_list_rules(runner: CliRunner, spec_path: Path) -> None:
    result = invoke(runner, "lint", str(spec_path), "--list-rules", "--no-color")
    assert "implausible-speed" in result.output


def test_simulate_uses_the_bundled_samples(runner: CliRunner, spec_path: Path) -> None:
    result = invoke(runner, "simulate", str(spec_path), "--format", "json")
    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert len(payload["ways"]) > 15


def test_simulate_against_shows_deltas(runner: CliRunner, spec_path: Path) -> None:
    result = invoke(runner, "simulate", str(spec_path), "--against", "car", "--format", "json")
    payload = json.loads(result.output)
    assert payload["baseline"] == "stock-car"
    assert "cost_delta_pct" in payload["ways"][0]


def test_simulate_rejects_a_bad_time(runner: CliRunner, spec_path: Path) -> None:
    result = invoke(runner, "simulate", str(spec_path), "--at", "25:99", "--no-color")
    assert result.exit_code == 2
    assert "HH:MM" in result.output


def test_simulate_explain_prints_the_factor_chain(runner: CliRunner, spec_path: Path) -> None:
    result = invoke(runner, "simulate", str(spec_path), "--explain", "--no-color")
    assert result.exit_code == 0
    assert "base" in result.output


def test_simulate_accepts_a_csv(runner: CliRunner, spec_path: Path, tmp_path: Path) -> None:
    csv_path = tmp_path / "ways.csv"
    csv_path.write_text("name,highway,surface\nA,primary,gravel\n", encoding="utf-8")
    result = invoke(
        runner, "simulate", str(spec_path), "--samples", str(csv_path), "--format", "json"
    )
    payload = json.loads(result.output)
    assert payload["ways"][0]["speed_kmh"] == pytest.approx(42.0)


def test_init_scaffolds_a_valid_spec(runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "new.yaml"
    result = invoke(runner, "init", "fleet", "--extends", "van-urban", "-o", str(target))
    assert result.exit_code == 0
    validated = invoke(runner, "validate", str(target), "--no-color")
    assert validated.exit_code == 0
    assert "fleet" in validated.output


def test_init_refuses_to_clobber(runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "new.yaml"
    target.write_text("existing", encoding="utf-8")
    result = invoke(runner, "init", "fleet", "-o", str(target))
    assert result.exit_code == 2
    assert target.read_text(encoding="utf-8") == "existing"


def test_init_force_overwrites(runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "new.yaml"
    target.write_text("existing", encoding="utf-8")
    assert invoke(runner, "init", "fleet", "-o", str(target), "--force").exit_code == 0
    assert "extends" in target.read_text(encoding="utf-8")


def test_init_rejects_an_unknown_base(runner: CliRunner, tmp_path: Path) -> None:
    result = invoke(
        runner, "init", "fleet", "--extends", "spaceship", "-o", str(tmp_path / "x.yaml")
    )
    assert result.exit_code == 2


def test_init_to_stdout(runner: CliRunner) -> None:
    result = invoke(runner, "init", "fleet", "-o", "-")
    assert result.exit_code == 0
    assert result.output.startswith("# fleet")


def test_help_mentions_every_command(runner: CliRunner) -> None:
    result = invoke(runner, "--help")
    for command in ("build", "validate", "diff", "lint", "simulate", "init", "profiles"):
        assert command in result.output
