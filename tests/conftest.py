"""Shared fixtures.

Every fixture here is offline by construction: specs are written to ``tmp_path``
and sample ways come from literals. No test in this suite may require a routing
engine, a network, or a Lua interpreter.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from speed_profile_builder.model import Profile, build_profile
from speed_profile_builder.spec.loader import bundled_profiles, load_spec, validate_document

GOLDEN_DIR = Path(__file__).parent / "golden"


def has_lua() -> bool:
    """Whether a Lua parser is reachable, either as a binary or via ``lupa``.

    The suite must pass without one, so this only ever gates extra assertions.
    """
    if any(shutil.which(name) for name in ("luac", "lua", "luajit")):
        return True
    try:
        import lupa  # noqa: F401
    except ImportError:
        return False
    return True


requires_lua = pytest.mark.skipif(not has_lua(), reason="no Lua interpreter available")


def write_spec(directory: Path, name: str, document: dict[str, Any]) -> Path:
    """Write ``document`` as YAML and return its path."""
    path = directory / f"{name}.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def profile_from(document: dict[str, Any]) -> Profile:
    """Validate and lower an in-memory spec document, skipping the filesystem."""
    return build_profile(validate_document(dict(document)))


@pytest.fixture
def minimal_document() -> dict[str, Any]:
    """The smallest spec that validates, used as a base for mutation in tests."""
    return {
        "version": 1,
        "name": "minimal",
        "mode": "car",
        "speeds": {"default": 30, "highway": {"residential": 30, "primary": 60}},
    }


@pytest.fixture
def good_document() -> dict[str, Any]:
    """A realistic spec that should produce no lint findings at all."""
    return {
        "version": 1,
        "name": "good",
        "mode": "car",
        "description": "A profile that passes every lint rule.",
        "speeds": {
            "default": 30,
            "max_legal": 120,
            "highway": {
                "motorway": 110,
                "trunk": 90,
                "primary": 70,
                "secondary": 60,
                "tertiary": 50,
                "residential": 30,
                "track": 15,
            },
        },
        "surfaces": {"asphalt": 1.0, "unpaved": 0.6, "gravel": 0.6, "ground": 0.5, "dirt": 0.45},
        "tracktypes": {"grade1": 1.0, "grade3": 0.6, "grade5": 0.3},
        "turn": {"base_penalty": 7, "u_turn_penalty": 30},
        "extras": {"ferry": {"allowed": True, "speed": 12, "penalty": 300}},
    }


@pytest.fixture
def good_profile(good_document: dict[str, Any]) -> Profile:
    """The lint-clean profile, already lowered to the IR."""
    return profile_from(good_document)


@pytest.fixture(params=sorted(bundled_profiles()))
def bundled_profile(request: pytest.FixtureRequest) -> Profile:
    """Each bundled base profile in turn, for parametrised golden tests."""
    path = bundled_profiles()[request.param]
    spec, chain = load_spec(path)
    return build_profile(spec, chain)
