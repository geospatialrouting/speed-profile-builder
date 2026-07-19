"""Spec layer: parse, merge and validate profile documents."""

from .loader import (
    LineIndex,
    bundled_profiles,
    deep_merge,
    flatten,
    load_bundled,
    load_spec,
    validate_document,
)
from .schema import KNOWN_HIGHWAYS, ProfileSpec

__all__ = [
    "KNOWN_HIGHWAYS",
    "LineIndex",
    "ProfileSpec",
    "bundled_profiles",
    "deep_merge",
    "flatten",
    "load_bundled",
    "load_spec",
    "validate_document",
]
