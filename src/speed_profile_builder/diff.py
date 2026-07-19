"""Structural diff between two resolved profiles.

Reviewing a routing profile change by reading a Lua diff is hopeless: a one-line
speed edit can move fifty generated lines. Diffing the *IR* instead gives a
review artefact that maps one-to-one onto decisions somebody made — this speed
went up, that barrier became passable — and nothing else.

The diff is computed over :meth:`~speed_profile_builder.model.Profile.flatten`,
so it inherits that function's stable ordering and never reports a change that
is only a reordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .model import Profile


class ChangeKind(StrEnum):
    """Classification of a single key-level difference."""

    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


@dataclass(frozen=True)
class Change:
    """One key that differs between the baseline and the candidate profile."""

    key: str
    kind: ChangeKind
    before: Any = None
    after: Any = None

    @property
    def percent(self) -> float | None:
        """Relative change, when both sides are non-zero numbers.

        Percentages are the unit people actually reason about for speeds and
        penalties ("this profile is 12% slower on residential streets"), so the
        renderers show them wherever they are meaningful.
        """
        if self.kind is not ChangeKind.CHANGED:
            return None
        if not _numeric(self.before) or not _numeric(self.after):
            return None
        if float(self.before) == 0:
            return None
        return (float(self.after) - float(self.before)) / abs(float(self.before)) * 100.0

    def marker(self) -> str:
        """The ``+``/``-``/``~`` marker used in text output."""
        return {ChangeKind.ADDED: "+", ChangeKind.REMOVED: "-", ChangeKind.CHANGED: "~"}[self.kind]


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True)
class ProfileDiff:
    """The full set of differences, plus the names of the two sides."""

    baseline_name: str
    candidate_name: str
    changes: tuple[Change, ...]

    @property
    def is_empty(self) -> bool:
        """Whether the two profiles are semantically identical."""
        return not self.changes

    def of_kind(self, kind: ChangeKind) -> tuple[Change, ...]:
        """All changes of one kind, in key order."""
        return tuple(c for c in self.changes if c.kind is kind)

    def by_section(self) -> dict[str, tuple[Change, ...]]:
        """Group changes by their top-level section (``speeds``, ``turn``, ...)."""
        sections: dict[str, list[Change]] = {}
        for change in self.changes:
            sections.setdefault(change.key.split(".", 1)[0], []).append(change)
        return {k: tuple(v) for k, v in sorted(sections.items())}

    def summary(self) -> dict[str, int]:
        """Counts by kind, for one-line CI output."""
        return {
            "added": len(self.of_kind(ChangeKind.ADDED)),
            "removed": len(self.of_kind(ChangeKind.REMOVED)),
            "changed": len(self.of_kind(ChangeKind.CHANGED)),
        }

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready representation, used by ``--format json``."""
        return {
            "baseline": self.baseline_name,
            "candidate": self.candidate_name,
            "summary": self.summary(),
            "changes": [
                {
                    "key": c.key,
                    "kind": c.kind.value,
                    "before": c.before,
                    "after": c.after,
                    **({"percent": round(c.percent, 2)} if c.percent is not None else {}),
                }
                for c in self.changes
            ],
        }


def diff_profiles(baseline: Profile, candidate: Profile, epsilon: float = 1e-9) -> ProfileDiff:
    """Compare two profiles key by key.

    ``epsilon`` guards against float noise: two speeds that differ only in the
    fifteenth decimal are the same speed, and reporting them would train users
    to ignore the diff.
    """
    before = baseline.flatten()
    after = candidate.flatten()
    changes: list[Change] = []

    for key in sorted(set(before) | set(after)):
        has_before, has_after = key in before, key in after
        old, new = before.get(key), after.get(key)
        if has_before and not has_after:
            changes.append(Change(key, ChangeKind.REMOVED, before=old))
        elif has_after and not has_before:
            changes.append(Change(key, ChangeKind.ADDED, after=new))
        elif _numeric(old) and _numeric(new):
            if abs(float(old) - float(new)) > epsilon:
                changes.append(Change(key, ChangeKind.CHANGED, before=old, after=new))
        elif old != new:
            changes.append(Change(key, ChangeKind.CHANGED, before=old, after=new))

    return ProfileDiff(baseline.name, candidate.name, tuple(changes))
