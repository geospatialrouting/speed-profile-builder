"""Command-line interface.

The CLI is a thin shell over the library: every command loads a spec, calls one
pure function, and hands the result to :mod:`speed_profile_builder.render`. No
routing logic lives here, which is why the test suite can cover the interesting
behaviour without spawning a process.

Exit codes are chosen for CI use: ``0`` success, ``1`` the command ran but found
something you asked it to find (lint errors, an out-of-date build under
``--check``), ``2`` bad input.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from . import __version__
from .diff import diff_profiles
from .emit import EmitOptions, emit_osrm, emit_valhalla
from .errors import SpecError, SpeedProfileError
from .lint import RULES, lint_profile
from .model import Profile, build_profile
from .render import FORMATS, render_comparison, render_diff, render_lint, render_simulation
from .simulate import compare, load_samples, simulate
from .spec.loader import bundled_profiles, load_spec
from .stock import stock_for_mode, stock_names, stock_profile

#: Sample ways shipped with the package so ``simulate`` works with no arguments.
DEFAULT_SAMPLES = Path(__file__).resolve().parent / "data" / "sample-ways.yaml"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_BAD_INPUT = 2

_EPILOG = (
    "Background on calibrating these numbers for real fleets: "
    "https://www.geospatialrouting.com/osm-graph-architecture-network-modeling/"
    "speed-profile-calibration-for-heavy-vehicles/"
)


def _console(no_color: bool = False) -> Console:
    return Console(color_system=None if no_color else "auto", soft_wrap=False)


def _fail(console: Console, error: SpeedProfileError) -> None:
    """Print a user-facing error and exit with the bad-input code."""
    if isinstance(error, SpecError):
        console.print(f"[bold red]error:[/bold red] {error.summary}")
        for issue in error.issues:
            console.print(f"  {issue.render()}", markup=False, highlight=False)
    else:
        console.print(f"[bold red]error:[/bold red] {error}")
    raise SystemExit(EXIT_BAD_INPUT)


def _load(path: Path, console: Console) -> Profile:
    """Load and lower a spec, exiting cleanly on any user-facing error."""
    try:
        spec, chain = load_spec(path)
    except SpeedProfileError as exc:
        _fail(console, exc)
    return build_profile(spec, chain)


def _resolve_baseline(against: str | None, profile: Profile, console: Console) -> Profile:
    """Interpret ``--against`` as a stock name, then as a path to another spec."""
    if against is None:
        return stock_for_mode(profile.mode)
    if against in stock_names():
        return stock_profile(against)
    path = Path(against)
    if path.exists():
        return _load(path, console)
    console.print(
        f"[bold red]error:[/bold red] --against {against!r} is neither a stock profile "
        f"({', '.join(stock_names())}) nor an existing file"
    )
    raise SystemExit(EXIT_BAD_INPUT)


@click.group(
    context_settings={"help_option_names": ["-h", "--help"], "max_content_width": 100},
    epilog=_EPILOG,
)
@click.version_option(__version__, prog_name="speed-profile")
def main() -> None:
    """Compile one declarative YAML profile spec into OSRM Lua and Valhalla JSON.

    Validate it, diff it against the engine's stock profile, lint it for the
    classic footguns, and simulate its effect on sample ways before committing
    to a graph rebuild.
    """


@main.command()
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "-o",
    "--out",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("build"),
    show_default=True,
    help="Directory to write generated files into.",
)
@click.option(
    "--engine",
    type=click.Choice(["osrm", "valhalla", "both"]),
    default="both",
    show_default=True,
    help="Which engine(s) to generate for.",
)
@click.option(
    "--penalty-reference",
    type=float,
    default=300.0,
    show_default=True,
    help="Seconds of penalty that halve an OSRM edge rate. Tune to typical edge length.",
)
@click.option("--stdout", "to_stdout", is_flag=True, help="Write to stdout instead of files.")
@click.option(
    "--check",
    is_flag=True,
    help="Do not write; exit 1 if the files on disk differ from what would be generated.",
)
@click.option("--no-color", is_flag=True, help="Disable colour output.")
def build(
    spec: Path,
    out: Path,
    engine: str,
    penalty_reference: float,
    to_stdout: bool,
    check: bool,
    no_color: bool,
) -> None:
    """Compile SPEC into engine profiles.

    Output is deterministic: the same spec always produces byte-identical files,
    so `--check` is a reliable CI gate against someone editing generated code.
    """
    console = _console(no_color)
    profile = _load(spec, console)
    if penalty_reference <= 0:
        console.print("[bold red]error:[/bold red] --penalty-reference must be positive")
        raise SystemExit(EXIT_BAD_INPUT)
    options = EmitOptions(penalty_reference_s=penalty_reference, tool_version=__version__)

    artefacts: dict[Path, str] = {}
    if engine in ("osrm", "both"):
        artefacts[out / f"{profile.name}.lua"] = emit_osrm(profile, options)
    if engine in ("valhalla", "both"):
        artefacts[out / f"{profile.name}.valhalla.json"] = emit_valhalla(profile, options)

    if to_stdout:
        for i, (path, text) in enumerate(sorted(artefacts.items())):
            if i:
                click.echo()
            click.echo(f"----- {path.name} -----")
            click.echo(text, nl=False)
        return

    if check:
        stale = [
            path
            for path, text in sorted(artefacts.items())
            if not path.exists() or path.read_text(encoding="utf-8") != text
        ]
        if stale:
            console.print("[bold red]out of date:[/bold red]")
            for path in stale:
                console.print(f"  {path}", markup=False)
            console.print("run 'speed-profile build' and commit the result")
            raise SystemExit(EXIT_FINDINGS)
        console.print(f"[green]up to date[/green] ({len(artefacts)} file(s))")
        return

    out.mkdir(parents=True, exist_ok=True)
    for path, text in sorted(artefacts.items()):
        path.write_text(text, encoding="utf-8")
        console.print(f"[green]wrote[/green] {path}", markup=True, highlight=False)
    console.print(f"fingerprint [bold]{profile.fingerprint()}[/bold]")


@main.command()
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--no-color", is_flag=True, help="Disable colour output.")
def validate(spec: Path, no_color: bool) -> None:
    """Validate SPEC and print the resolved profile summary.

    Resolution includes the full `extends:` chain, so this is also how you check
    what a child spec actually inherited.
    """
    console = _console(no_color)
    profile = _load(spec, console)
    console.print(f"[green]valid[/green]  [bold]{profile.name}[/bold] (mode: {profile.mode})")
    if profile.description:
        console.print(f"  {profile.description}")
    if profile.source_chain:
        console.print(
            f"  chain: {' <- '.join(reversed([Path(p).name for p in profile.source_chain]))}"
        )
    console.print(f"  highway classes: {len(profile.speeds_kmh)}")
    console.print(f"  surfaces: {len(profile.surfaces)}  tracktypes: {len(profile.tracktypes)}")
    console.print(f"  zones: {len(profile.zones)}  time factors: {len(profile.time_factors)}")
    console.print(f"  fingerprint: {profile.fingerprint()}")


@main.command(name="diff")
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--against",
    default=None,
    help=(
        "Baseline: a stock profile name (car, truck, bicycle, foot) or a path to "
        "another spec. Defaults to the stock profile matching the spec's mode."
    ),
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(list(FORMATS)),
    default="table",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--exit-code",
    is_flag=True,
    help="Exit 1 when any difference is found, for use as a CI gate.",
)
@click.option("--no-color", is_flag=True, help="Disable colour output.")
def diff_command(
    spec: Path, against: str | None, fmt: str, exit_code: bool, no_color: bool
) -> None:
    """Diff SPEC against a stock profile or another spec.

    The comparison runs on the normalised profile, not the generated code, so
    what you see is the set of decisions that changed.
    """
    console = _console(no_color)
    profile = _load(spec, console)
    baseline = _resolve_baseline(against, profile, console)
    result = diff_profiles(baseline, profile)
    render_diff(result, fmt, console)
    if exit_code and not result.is_empty:
        raise SystemExit(EXIT_FINDINGS)


@main.command(name="lint")
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--select", multiple=True, help="Only run these rules (repeatable).")
@click.option("--ignore", multiple=True, help="Skip these rules (repeatable).")
@click.option(
    "--min-severity",
    type=click.Choice(["error", "warning", "info"]),
    default="info",
    show_default=True,
    help="Lowest severity to report.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(list(FORMATS)),
    default="table",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--fail-on",
    type=click.Choice(["error", "warning", "info", "never"]),
    default="error",
    show_default=True,
    help="Severity at which to exit non-zero.",
)
@click.option("--list-rules", is_flag=True, help="Print the rule names and exit.")
@click.option("--no-color", is_flag=True, help="Disable colour output.")
def lint_command(
    spec: Path,
    select: tuple[str, ...],
    ignore: tuple[str, ...],
    min_severity: str,
    fmt: str,
    fail_on: str,
    list_rules: bool,
    no_color: bool,
) -> None:
    """Lint SPEC for rules that pass validation but break routing."""
    console = _console(no_color)
    if list_rules:
        for name in sorted(RULES):
            console.print(name)
        return
    profile = _load(spec, console)
    try:
        findings = lint_profile(profile, select or None, ignore or None, min_severity)
    except KeyError as exc:
        console.print(f"[bold red]error:[/bold red] {exc.args[0]}")
        raise SystemExit(EXIT_BAD_INPUT) from exc
    render_lint(findings, fmt, console, profile.name)
    if fail_on == "never":
        return
    order = {"error": 0, "warning": 1, "info": 2}
    if any(order[f.severity] <= order[fail_on] for f in findings):
        raise SystemExit(EXIT_FINDINGS)


@main.command(name="simulate")
@click.argument("spec", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--samples",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="YAML fixture or CSV of OSM tags. Defaults to the bundled sample ways.",
)
@click.option(
    "--against",
    default=None,
    help="Compare against a stock profile name or another spec, showing before/after.",
)
@click.option("--at", "at_time", default=None, help="Time of day as HH:MM for time-of-day factors.")
@click.option(
    "--day",
    default="we",
    show_default=True,
    help="Two-letter day (mo..su) used with --at.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(list(FORMATS)),
    default="table",
    show_default=True,
    help="Output format.",
)
@click.option("--explain", is_flag=True, help="Print the factor chain applied to each way.")
@click.option("--no-color", is_flag=True, help="Disable colour output.")
def simulate_command(
    spec: Path,
    samples: Path | None,
    against: str | None,
    at_time: str | None,
    day: str,
    fmt: str,
    explain: bool,
    no_color: bool,
) -> None:
    """Apply SPEC to sample ways and report speed and cost per way.

    With `--against`, every way is evaluated under both profiles and the cost
    delta is shown — the fastest way to find out whether a change did what you
    intended, without rebuilding a graph.
    """
    console = _console(no_color)
    profile = _load(spec, console)
    try:
        ways = load_samples(samples or DEFAULT_SAMPLES)
    except SpeedProfileError as exc:
        _fail(console, exc)

    minute = None
    if at_time is not None:
        try:
            hours, minutes = at_time.split(":")
            minute = int(hours) * 60 + int(minutes)
            if not 0 <= minute < 1440:
                raise ValueError
        except ValueError:
            console.print(f"[bold red]error:[/bold red] --at {at_time!r} must be HH:MM in 24h time")
            raise SystemExit(EXIT_BAD_INPUT) from None

    if against is not None:
        baseline = _resolve_baseline(against, profile, console)
        comparisons = compare(baseline, profile, ways, day=day, minute=minute)
        render_comparison(comparisons, fmt, console, baseline.name, profile.name)
        return

    results = simulate(profile, ways, day=day, minute=minute)
    render_simulation(results, fmt, console, profile.name)
    if explain and fmt == "table":
        for result in results:
            console.print(f"[bold]{result.sample.name}[/bold]: {result.reason}")
            for step in result.steps:
                console.print(f"    {step}", markup=False, highlight=False)


@main.command(name="init")
@click.argument("name")
@click.option(
    "--extends",
    "base",
    default="car",
    show_default=True,
    help="Bundled base profile to inherit from.",
)
@click.option(
    "-o",
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="File to write. Defaults to <name>.yaml. Use '-' for stdout.",
)
@click.option("--force", is_flag=True, help="Overwrite an existing file.")
def init_command(name: str, base: str, out: Path | None, force: bool) -> None:
    """Scaffold a new spec that extends a bundled base profile."""
    console = _console()
    available = bundled_profiles()
    if base not in available:
        console.print(
            f"[bold red]error:[/bold red] unknown base {base!r}; "
            f"available: {', '.join(sorted(available))}"
        )
        raise SystemExit(EXIT_BAD_INPUT)

    text = _scaffold(name, base)
    if str(out) == "-":
        click.echo(text, nl=False)
        return
    target = out or Path(f"{name}.yaml")
    if target.exists() and not force:
        console.print(f"[bold red]error:[/bold red] {target} exists; pass --force to overwrite")
        raise SystemExit(EXIT_BAD_INPUT)
    target.write_text(text, encoding="utf-8")
    console.print(f"[green]wrote[/green] {target}")
    console.print(f"next: speed-profile validate {target} && speed-profile diff {target}")


def _scaffold(name: str, base: str) -> str:
    """Render the starter spec written by ``init``.

    Deliberately opinionated: it shows the overrides people reach for first
    (a couple of speeds, a zone, a time factor) rather than an empty skeleton
    that teaches nothing.
    """
    return f"""# {name} - override only what differs from the '{base}' base profile.
# Inherited values are merged key by key; write null to remove one.
version: 1
name: {name}
extends: {base}
description: TODO describe the fleet or use case this profile models.

speeds:
  # Only the classes you change need listing.
  highway:
    residential: 25 km/h
    living_street: 8 km/h

# Restricted or low-emission zones, matched by tag (works in both engines) or
# by polygon (Valhalla only).
zones: []

# Time-of-day speed factors, applied on top of the class speed.
time_factors: []

metadata:
  owner: TODO
"""


@main.command(name="profiles")
@click.option("--no-color", is_flag=True, help="Disable colour output.")
def profiles_command(no_color: bool) -> None:
    """List the bundled base profiles available to `extends:`."""
    from rich.table import Table

    console = _console(no_color)
    table = Table(header_style="bold", title="bundled base profiles", title_justify="left")
    table.add_column("name")
    table.add_column("mode")
    table.add_column("extends")
    table.add_column("description")
    for name, path in sorted(bundled_profiles().items()):
        try:
            spec, _ = load_spec(path)
        except SpeedProfileError as exc:  # pragma: no cover - packaging failure
            _fail(console, exc)
        table.add_row(name, spec.mode, spec.extends or "-", spec.description)
    console.print(table)
    console.print("stock baselines for --against: " + ", ".join(stock_names()))


def run() -> None:  # pragma: no cover - exercised via the console script
    """Entry point that keeps tracebacks out of ordinary user errors."""
    try:
        main.main(standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        sys.exit(EXIT_BAD_INPUT)
    except click.Abort:
        sys.exit(EXIT_BAD_INPUT)
    except SpeedProfileError as exc:
        _console().print(f"[bold red]error:[/bold red] {exc}")
        sys.exit(EXIT_BAD_INPUT)
    except SystemExit:
        raise
