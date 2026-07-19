"""Author OSRM and Valhalla routing profiles from one declarative YAML spec.

The pipeline is strictly layered and each layer is independently testable:

``spec`` -> ``model`` -> ``emit_osrm`` / ``emit_valhalla`` -> ``diff`` / ``lint``
/ ``simulate``

Nothing in this package contacts a routing server. Sample ways come from
fixtures, stock baselines are pinned data, and code generation is a pure
function of the spec — which is what makes the whole tool testable offline and
its output reproducible.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    __version__ = _version("speed-profile-builder")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0+local"

from .diff import diff_profiles
from .errors import LintFinding, SpecError, SpecIssue, SpeedProfileError, UnitError
from .lint import lint_profile
from .model import Profile, build_profile
from .simulate import WaySample, compare, evaluate, simulate
from .spec import ProfileSpec, load_bundled, load_spec

__all__ = [
    "LintFinding",
    "Profile",
    "ProfileSpec",
    "SpecError",
    "SpecIssue",
    "SpeedProfileError",
    "UnitError",
    "WaySample",
    "__version__",
    "build_profile",
    "compare",
    "diff_profiles",
    "evaluate",
    "lint_profile",
    "load_bundled",
    "load_spec",
    "simulate",
]
