"""Error types shared by every layer of the pipeline.

Routing profiles are edited by hand, often by people who are not Python
developers, so a stack trace is a useless response to a typo. Every failure the
tool can attribute to user input is raised as a :class:`SpecError` carrying the
source file and (where the YAML loader could recover it) the line and column, so
the CLI can print something that points at the offending line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class SpeedProfileError(Exception):
    """Base class for every error this package raises deliberately."""


@dataclass
class SpecIssue:
    """A single validation problem located in a source document.

    ``path`` is the dotted location inside the spec (``speeds.highway.motorway``)
    rather than a pydantic tuple, because that is what the user sees in their
    YAML file and can search for.
    """

    message: str
    path: str = ""
    line: int | None = None
    column: int | None = None
    source: Path | None = None
    hint: str = ""

    def render(self) -> str:
        """Render a single-line, grep-friendly description of the issue."""
        where = str(self.source) if self.source else "<spec>"
        if self.line is not None:
            where = f"{where}:{self.line}"
            if self.column is not None:
                where = f"{where}:{self.column}"
        prefix = f"{where}: " if where else ""
        loc = f"[{self.path}] " if self.path else ""
        text = f"{prefix}{loc}{self.message}"
        if self.hint:
            text = f"{text}\n    hint: {self.hint}"
        return text


class SpecError(SpeedProfileError):
    """Raised when a spec document cannot be parsed or fails validation.

    Carries every issue found rather than only the first, because fixing one
    error at a time across a 200-line profile is miserable.
    """

    def __init__(self, issues: list[SpecIssue] | SpecIssue, summary: str = "") -> None:
        self.issues: list[SpecIssue] = [issues] if isinstance(issues, SpecIssue) else list(issues)
        self.summary = summary or f"{len(self.issues)} problem(s) in spec"
        super().__init__(self.summary + "\n" + "\n".join(i.render() for i in self.issues))


class UnitError(SpeedProfileError):
    """Raised when a quantity string cannot be parsed or has the wrong dimension."""


@dataclass
class LintFinding:
    """One lint rule firing against one location in the resolved profile."""

    rule: str
    severity: str
    message: str
    path: str = ""
    hint: str = ""
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        """Whether this finding should make ``lint`` exit non-zero by default."""
        return self.severity == "error"
