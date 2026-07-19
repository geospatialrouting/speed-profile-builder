"""Golden-file tests for every bundled profile on both engines.

Codegen changes are the ones most likely to be invisible in review, so the
generated output for all five bundled profiles is committed and compared byte
for byte. When a change is intentional, regenerate with::

    UPDATE_GOLDEN=1 pytest tests/test_golden.py

and read the resulting diff before committing it — that diff is the review.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from speed_profile_builder.emit import EmitOptions, emit_osrm, emit_valhalla
from speed_profile_builder.model import Profile

from .conftest import GOLDEN_DIR

#: A pinned tool version keeps goldens stable across releases; the version
#: string is provenance, not behaviour.
OPTIONS = EmitOptions(tool_version="golden")

UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


def _check(path: Path, produced: str) -> None:
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(produced, encoding="utf-8")
        pytest.skip(f"updated golden {path.name}")
    if not path.exists():
        pytest.fail(f"missing golden {path}; run UPDATE_GOLDEN=1 pytest to create it")
    expected = path.read_text(encoding="utf-8")
    assert produced == expected, (
        f"{path.name} differs from the committed golden; "
        "rerun with UPDATE_GOLDEN=1 if the change is intended"
    )


def test_osrm_golden(bundled_profile: Profile) -> None:
    _check(GOLDEN_DIR / f"{bundled_profile.name}.lua", emit_osrm(bundled_profile, OPTIONS))


def test_valhalla_golden(bundled_profile: Profile) -> None:
    _check(
        GOLDEN_DIR / f"{bundled_profile.name}.valhalla.json",
        emit_valhalla(bundled_profile, OPTIONS),
    )


def test_goldens_exist_for_every_bundled_profile() -> None:
    """Guards against a new bundled profile shipping without golden coverage."""
    from speed_profile_builder.spec.loader import bundled_profiles

    if UPDATE:
        pytest.skip("golden files are being regenerated")
    for name in bundled_profiles():
        assert (GOLDEN_DIR / f"{name}.lua").exists()
        assert (GOLDEN_DIR / f"{name}.valhalla.json").exists()
