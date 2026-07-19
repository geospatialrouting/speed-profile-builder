"""Shared helpers for deterministic code generation.

Both emitters must produce byte-identical output for the same input, on any
machine, on any run. That rules out three things that creep into generated code
by default: timestamps, absolute paths, and unordered iteration. This module
provides the primitives that avoid all three, plus the provenance header that
tells a reviewer which spec produced the file and what its fingerprint was.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model import Profile

#: Seconds of penalty that halve an edge's routing rate in engines that have no
#: native per-edge time penalty (i.e. OSRM). Exposed as a knob because the right
#: value depends on typical edge length in the region being routed.
DEFAULT_PENALTY_REFERENCE_S = 300.0


@dataclass(frozen=True)
class EmitOptions:
    """Knobs that change generated output; part of the golden-file contract."""

    #: Reference used to convert second-denominated penalties into rate factors.
    penalty_reference_s: float = DEFAULT_PENALTY_REFERENCE_S
    #: Emit the provenance header. Only turned off by tests comparing bodies.
    header: bool = True
    #: Tool version recorded in the header.
    tool_version: str = ""


def rate_factor(penalty_s: float, reference_s: float = DEFAULT_PENALTY_REFERENCE_S) -> float:
    """Convert a time penalty into a multiplicative rate factor in ``(0, 1]``.

    OSRM has no way to add a fixed number of seconds to an edge from
    ``process_way`` — the way's length is not known there. The idiomatic
    workaround is to depress the edge's *rate* (the routability weight per
    metre), which makes the edge proportionally less attractive. A penalty equal
    to ``reference_s`` halves the rate; the mapping is smooth and never reaches
    zero, so a penalised edge stays usable as a last resort instead of silently
    disconnecting the network.
    """
    if penalty_s <= 0:
        return 1.0
    if reference_s <= 0:
        raise ValueError("penalty reference must be positive")
    return 1.0 / (1.0 + penalty_s / reference_s)


def num(value: float, digits: int = 4) -> str:
    """Format a float deterministically: fixed precision, trailing zeros trimmed.

    Avoids ``repr`` drift (``0.30000000000000004``) that would otherwise make
    generated files differ between platforms.
    """
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def provenance(profile: Profile, comment: str, options: EmitOptions) -> list[str]:
    """Build the generated-file header as a list of comment lines.

    Deliberately contains no timestamp: a regenerated file that differs only by
    the clock produces noise in review and breaks reproducible builds. Source
    paths are reduced to basenames for the same reason.
    """
    version = options.tool_version or "unknown"
    chain = [p.rsplit("/", 1)[-1] for p in profile.source_chain] or [f"{profile.name}.yaml"]
    lines = [
        f"{comment} GENERATED FILE - DO NOT EDIT.",
        f"{comment} Produced by speed-profile-builder {version} from a declarative spec.",
        f"{comment}",
        f"{comment} profile:     {profile.name} (mode: {profile.mode})",
        f"{comment} spec chain:  {' <- '.join(reversed(chain))}",
        f"{comment} fingerprint: {profile.fingerprint()}",
    ]
    if profile.description:
        lines.append(f"{comment} summary:     {profile.description}")
    lines += [
        f"{comment}",
        f"{comment} Edit the spec and re-run:  speed-profile build <spec>",
    ]
    return lines
