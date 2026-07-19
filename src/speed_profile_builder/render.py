"""Presentation layer: turn results into a table, JSON or Markdown.

Kept separate from ``diff``, ``lint`` and ``simulate`` so those stay pure data
functions that tests can assert on without parsing terminal output. Everything
here takes already-computed results and only decides how they look.

Three formats, because a profile change gets read in three places: a terminal
during development (``table``), a CI job summary (``markdown``), and a script
that gates the merge (``json``).
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .diff import ChangeKind, ProfileDiff
from .errors import LintFinding
from .simulate import Comparison, WayResult

#: Colour per change kind; also used for lint severities.
_KIND_STYLE = {
    ChangeKind.ADDED: "green",
    ChangeKind.REMOVED: "red",
    ChangeKind.CHANGED: "yellow",
}

_SEVERITY_STYLE = {"error": "bold red", "warning": "yellow", "info": "cyan"}

FORMATS = ("table", "json", "markdown")


def _fmt(value: Any) -> str:
    """Render a scalar compactly and identically in every format."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def _pct(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:+.1f}%"


def render_diff(diff: ProfileDiff, fmt: str, console: Console) -> None:
    """Write ``diff`` to ``console`` in the requested format."""
    if fmt == "json":
        console.print_json(json.dumps(diff.to_dict()))
        return
    if fmt == "markdown":
        console.print(_diff_markdown(diff), markup=False, highlight=False)
        return

    if diff.is_empty:
        console.print(
            f"[green]no differences[/green] between "
            f"[bold]{diff.baseline_name}[/bold] and [bold]{diff.candidate_name}[/bold]"
        )
        return

    for section, changes in diff.by_section().items():
        table = Table(title=section, title_justify="left", header_style="bold")
        table.add_column("", width=1)
        table.add_column("key")
        table.add_column(diff.baseline_name, justify="right")
        table.add_column(diff.candidate_name, justify="right")
        table.add_column("delta", justify="right")
        for change in changes:
            style = _KIND_STYLE[change.kind]
            table.add_row(
                Text(change.marker(), style=style),
                change.key,
                _fmt(change.before),
                _fmt(change.after),
                Text(_pct(change.percent), style=style),
            )
        console.print(table)

    summary = diff.summary()
    console.print(
        f"[green]+{summary['added']} added[/green]  "
        f"[red]-{summary['removed']} removed[/red]  "
        f"[yellow]~{summary['changed']} changed[/yellow]"
    )


def _diff_markdown(diff: ProfileDiff) -> str:
    if diff.is_empty:
        return f"No differences between `{diff.baseline_name}` and `{diff.candidate_name}`.\n"
    lines = [
        f"### Profile diff: `{diff.baseline_name}` -> `{diff.candidate_name}`",
        "",
        f"| | key | {diff.baseline_name} | {diff.candidate_name} | delta |",
        "|---|---|---:|---:|---:|",
    ]
    for change in diff.changes:
        lines.append(
            f"| `{change.marker()}` | `{change.key}` | {_fmt(change.before)} | "
            f"{_fmt(change.after)} | {_pct(change.percent)} |"
        )
    summary = diff.summary()
    lines += [
        "",
        f"**{summary['added']} added, {summary['removed']} removed, "
        f"{summary['changed']} changed.**",
        "",
    ]
    return "\n".join(lines)


def render_lint(findings: list[LintFinding], fmt: str, console: Console, profile_name: str) -> None:
    """Write lint findings in the requested format."""
    if fmt == "json":
        payload = {
            "profile": profile_name,
            "findings": [
                {
                    "rule": f.rule,
                    "severity": f.severity,
                    "message": f.message,
                    "path": f.path,
                    "hint": f.hint,
                }
                for f in findings
            ],
            "summary": _lint_summary(findings),
        }
        console.print_json(json.dumps(payload))
        return
    if fmt == "markdown":
        console.print(_lint_markdown(findings, profile_name), markup=False, highlight=False)
        return

    if not findings:
        console.print(f"[green]clean[/green] - no lint findings for [bold]{profile_name}[/bold]")
        return
    table = Table(header_style="bold", title=f"lint: {profile_name}", title_justify="left")
    table.add_column("severity")
    table.add_column("rule")
    table.add_column("path")
    table.add_column("message")
    for finding in findings:
        style = _SEVERITY_STYLE[finding.severity]
        message = finding.message
        if finding.hint:
            message = f"{message}\n[dim]hint: {finding.hint}[/dim]"
        table.add_row(Text(finding.severity, style=style), finding.rule, finding.path, message)
    console.print(table)
    counts = _lint_summary(findings)
    console.print(
        f"[bold red]{counts['error']} error(s)[/bold red]  "
        f"[yellow]{counts['warning']} warning(s)[/yellow]  "
        f"[cyan]{counts['info']} info[/cyan]"
    )


def _lint_summary(findings: list[LintFinding]) -> dict[str, int]:
    return {
        severity: sum(1 for f in findings if f.severity == severity)
        for severity in ("error", "warning", "info")
    }


def _lint_markdown(findings: list[LintFinding], profile_name: str) -> str:
    if not findings:
        return f"No lint findings for `{profile_name}`.\n"
    lines = [
        f"### Lint: `{profile_name}`",
        "",
        "| severity | rule | path | message |",
        "|---|---|---|---|",
    ]
    for f in findings:
        message = f.message.replace("|", "\\|")
        if f.hint:
            message = f"{message}<br>_hint: {f.hint}_"
        lines.append(f"| {f.severity} | `{f.rule}` | `{f.path}` | {message} |")
    counts = _lint_summary(findings)
    lines += [
        "",
        f"**{counts['error']} error(s), {counts['warning']} warning(s), {counts['info']} info.**",
        "",
    ]
    return "\n".join(lines)


def render_simulation(results: list[WayResult], fmt: str, console: Console, title: str) -> None:
    """Write single-profile simulation results."""
    if fmt == "json":
        console.print_json(json.dumps({"profile": title, "ways": [r.to_dict() for r in results]}))
        return
    if fmt == "markdown":
        lines = [
            f"### Simulation: `{title}`",
            "",
            "| way | highway | routable | speed (km/h) | duration (s) | penalty (s) | cost (s) |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
        for r in results:
            lines.append(
                f"| {r.sample.name} | `{r.sample.highway or '-'}` | "
                f"{'yes' if r.routable else 'no'} | {_fmt(r.speed_kmh)} | "
                f"{_fmt(round(r.duration_s, 1))} | {_fmt(round(r.penalty_s, 1))} | "
                f"{_fmt(round(r.cost_s, 1))} |"
            )
        console.print("\n".join(lines) + "\n", markup=False, highlight=False)
        return

    table = Table(header_style="bold", title=f"simulate: {title}", title_justify="left")
    table.add_column("way")
    table.add_column("highway")
    table.add_column("speed", justify="right")
    table.add_column("duration", justify="right")
    table.add_column("penalty", justify="right")
    table.add_column("cost", justify="right")
    table.add_column("note")
    for r in results:
        if not r.routable:
            table.add_row(
                r.sample.name,
                r.sample.highway or "-",
                Text("blocked", style="red"),
                "-",
                "-",
                "-",
                Text(r.reason, style="red"),
            )
            continue
        table.add_row(
            r.sample.name,
            r.sample.highway or "-",
            f"{r.speed_kmh:.1f}",
            f"{r.duration_s:.0f}s",
            f"{r.penalty_s:.0f}s",
            f"{r.cost_s:.0f}s",
            "",
        )
    console.print(table)


def render_comparison(
    comparisons: list[Comparison], fmt: str, console: Console, baseline: str, candidate: str
) -> None:
    """Write before/after simulation results for two profiles."""
    if fmt == "json":
        console.print_json(
            json.dumps(
                {
                    "baseline": baseline,
                    "candidate": candidate,
                    "ways": [c.to_dict() for c in comparisons],
                }
            )
        )
        return
    if fmt == "markdown":
        lines = [
            f"### Simulation: `{baseline}` -> `{candidate}`",
            "",
            "| way | highway | speed before | speed after | cost before | cost after | cost delta |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for c in comparisons:
            lines.append(
                f"| {c.after.sample.name} | `{c.after.sample.highway or '-'}` | "
                f"{_state(c.before, 'speed')} | {_state(c.after, 'speed')} | "
                f"{_state(c.before, 'cost')} | {_state(c.after, 'cost')} | "
                f"{_pct(c.cost_delta_pct)} |"
            )
        console.print("\n".join(lines) + "\n", markup=False, highlight=False)
        return

    table = Table(
        header_style="bold",
        title=f"simulate: {baseline} -> {candidate}",
        title_justify="left",
    )
    table.add_column("way")
    table.add_column("highway")
    table.add_column("speed before", justify="right")
    table.add_column("speed after", justify="right")
    table.add_column("cost before", justify="right")
    table.add_column("cost after", justify="right")
    table.add_column("cost delta", justify="right")
    for c in comparisons:
        delta = c.cost_delta_pct
        if c.routability_changed:
            style = "red" if not c.after.routable else "green"
            delta_cell = Text(
                "now blocked" if not c.after.routable else "now routable", style=style
            )
        elif delta is None:
            delta_cell = Text("-", style="dim")
        else:
            style = "red" if delta > 0 else "green" if delta < 0 else "dim"
            delta_cell = Text(_pct(delta), style=style)
        table.add_row(
            c.after.sample.name,
            c.after.sample.highway or "-",
            _state(c.before, "speed"),
            _state(c.after, "speed"),
            _state(c.before, "cost"),
            _state(c.after, "cost"),
            delta_cell,
        )
    console.print(table)


def _state(result: WayResult, what: str) -> str:
    """Cell text for a way that may be blocked under one of the two profiles."""
    if not result.routable:
        return "blocked"
    return f"{result.speed_kmh:.1f}" if what == "speed" else f"{result.cost_s:.0f}s"
