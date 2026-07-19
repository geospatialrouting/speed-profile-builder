# speed-profile-builder

Author OSRM Lua and Valhalla costing profiles from **one declarative YAML spec** —
with validation, stock-profile diffs, linting, and per-way cost simulation before
you rebuild.

---

## The problem

Routing profiles are the highest-leverage and worst-tooled part of a routing stack.

- OSRM profiles are hand-written Lua. A typo in a table key does not fail; it
  silently produces a graph where `residential` is 90 km/h.
- Valhalla costing is a sprawling JSON blob whose knobs are preferences, not
  speeds, and which lives in the request rather than the build.
- Running both engines means maintaining two unrelated artefacts that are
  supposed to describe the same vehicle.
- Above all: there is **no way to see what a change does** short of a multi-hour
  graph rebuild followed by squinting at routes.

This tool takes a single spec and gives you the feedback loop back.

```
                     ┌──────────────┐
                     │  spec (YAML) │   extends: truck
                     └──────┬───────┘
                            │  parse · merge · validate
                     ┌──────▼───────┐
                     │  Profile IR  │   normalised, engine-agnostic
                     └──┬────┬───┬──┘
        emit_osrm ◄─────┘    │   └─────► emit_valhalla
      profile.lua            │             profile.valhalla.json
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
            diff            lint         simulate
     vs stock car/         17 rules    speed + cost per way,
     truck/bike/foot                   before/after, no rebuild
```

Each arrow is a layer boundary the code actually respects: the spec layer knows
nothing about routing engines, the emitters know nothing about YAML, and
`diff` / `lint` / `simulate` read only the intermediate representation.

## What you get

- **One source of truth, two engines.** A real OSRM Lua profile (correct
  `setup`, `process_node`, `process_way`, `process_turn`, and properties table)
  and a Valhalla costing document, generated from the same spec.
- **Strict validation** with line-referenced error messages and did-you-mean
  suggestions — unknown keys are errors, not silent no-ops.
- **Inheritance.** `extends:` a bundled base and override only what differs,
  which is how people actually work.
- **Diffs against the engine's stock profile**, computed on the normalised
  profile rather than on generated code, so the diff is the set of decisions
  that changed.
- **A linter** for the classic footguns: unreachable rules, speeds above a legal
  maximum, access rules that fragment the network, contradictory tag matches,
  penalties large enough to make edges impassable, missing surface handling.
- **Simulation** against sample ways (a fixture or your own CSV of OSM tags),
  showing resulting speed and cost per way type — with a before/after column
  and percentage cost change.
- **Deterministic, byte-stable output** with a provenance header carrying the
  spec chain and a semantic fingerprint. Golden-file tested.

No routing server is needed for anything, including the test suite.

## Install (from a clone)

This project is not published to PyPI. Clone it and install the local checkout:

```bash
git clone https://github.com/geospatialrouting/speed-profile-builder.git
cd speed-profile-builder
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Python 3.11 or newer. Pure-Python dependencies only: `click`, `pydantic`,
`pyyaml`, `rich`.

Run it either way:

```bash
speed-profile --help
python -m speed_profile_builder --help
```

## Worked example

The repository ships `examples/urban-freight.yaml` — an 18 t rigid doing
city-centre delivery work, inheriting the bundled `truck` base.

### 1. Validate it

```console
$ speed-profile validate examples/urban-freight.yaml
valid  urban-freight (mode: truck)
  18 t rigid on city-centre delivery work, LEZ-restricted.
  chain: urban-freight.yaml <- truck.yaml
  highway classes: 16
  surfaces: 15  tracktypes: 5
  zones: 3  time factors: 2
  fingerprint: 2876d2cfe9ae5b27
```

### 2. See what it changes versus the stock truck profile

```console
$ speed-profile diff examples/urban-freight.yaml
speeds
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃   ┃ key                        ┃ stock-truck ┃ urban-freight ┃   delta ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ ~ │ speeds.default             │          10 │            20 │ +100.0% │
│ ~ │ speeds.global_factor       │           1 │          0.95 │   -5.0% │
│ ~ │ speeds.highway.motorway    │          90 │            80 │  -11.1% │
│ ~ │ speeds.highway.primary     │          65 │            45 │  -30.8% │
│ ~ │ speeds.highway.residential │          25 │            18 │  -28.0% │
│ ~ │ speeds.max_legal           │          90 │            80 │  -11.1% │
└───┴────────────────────────────┴─────────────┴───────────────┴─────────┘
+39 added  -0 removed  ~42 changed
```

(Truncated: the real output groups every changed key by section — access,
extras, smoothness, speeds, turn, vehicle, zones.)

### 3. Lint it

```console
$ speed-profile lint examples/urban-freight.yaml
clean - no lint findings for urban-freight
```

### 4. Simulate it on real tag sets before rebuilding anything

```console
$ speed-profile simulate examples/urban-freight.yaml \
      --samples examples/sample-ways.csv --against truck
simulate: stock-truck -> urban-freight
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ way          ┃ highway     ┃ speed before ┃ speed after ┃ cost before ┃ cost after ┃  cost delta ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ A40 dual     │ trunk       │         85.0 │        66.5 │        102s │       130s │      +27.8% │
│ carriageway  │             │              │             │             │            │             │
│ Euston Road  │ primary     │         48.3 │     blocked │         82s │    blocked │ now blocked │
│ Bloomsbury   │ residential │         25.0 │     blocked │         43s │    blocked │ now blocked │
│ side street  │             │              │             │             │            │             │
│ Delivery bay │ service     │         15.0 │     blocked │        619s │    blocked │ now blocked │
│ approach     │             │              │             │             │            │             │
│ Riverside    │ secondary   │      blocked │     blocked │     blocked │    blocked │           - │
│ weight limit │             │              │             │             │            │             │
│ Industrial   │ unclassifi… │         25.0 │        33.2 │        130s │        97s │      -24.8% │
│ estate loop  │             │              │             │             │            │             │
│ Farm access  │ track       │      blocked │         5.0 │     blocked │      1080s │         now │
│ track        │             │              │             │             │            │    routable │
│ M6 Toll      │ motorway    │         90.0 │        76.0 │        160s │       369s │     +130.9% │
└──────────────┴─────────────┴──────────────┴─────────────┴─────────────┴────────────┴─────────────┘
```

Three streets went from routable to blocked — that is the LEZ rule doing its
job, visible in a second rather than after a rebuild. `--explain` prints the
factor chain that produced each number.

### 5. Build both engines

```console
$ speed-profile build examples/urban-freight.yaml -o build/
wrote build/urban-freight.lua
wrote build/urban-freight.valhalla.json
fingerprint 2876d2cfe9ae5b27
```

The Lua is self-contained — no `require` of OSRM's `lib/*` — so you can pass it
straight to `osrm-extract -p build/urban-freight.lua` from any directory.

## Commands

| Command | Purpose |
|---|---|
| `build` | Compile a spec into OSRM Lua and/or Valhalla JSON. |
| `validate` | Validate and print the resolved profile, including inherited values. |
| `diff` | Compare against a stock profile or another spec. |
| `lint` | Run the rule set over the resolved profile. |
| `simulate` | Apply the profile to sample ways and report speed and cost. |
| `init` | Scaffold a new spec that extends a bundled base. |
| `profiles` | List the bundled base profiles and stock baselines. |

### Flag reference

**Global**

| Flag | Meaning |
|---|---|
| `-h`, `--help` | Show help for the group or a command. |
| `--version` | Print the tool version. |

**`build SPEC`**

| Flag | Default | Meaning |
|---|---|---|
| `-o`, `--out DIR` | `build` | Directory to write generated files into. |
| `--engine {osrm,valhalla,both}` | `both` | Which engine(s) to generate. |
| `--penalty-reference SECONDS` | `300` | Seconds of penalty that halve an OSRM edge rate (see *Penalties* below). |
| `--stdout` | off | Write to stdout instead of files. |
| `--check` | off | Write nothing; exit 1 if on-disk files differ from what would be generated. |
| `--no-color` | off | Disable colour. |

**`validate SPEC`** — takes `--no-color`.

**`diff SPEC`**

| Flag | Default | Meaning |
|---|---|---|
| `--against NAME\|PATH` | stock profile for the spec's mode | Baseline: `car`, `truck`, `bicycle`, `foot`, or a path to another spec. |
| `--format {table,json,markdown}` | `table` | Output format. |
| `--exit-code` | off | Exit 1 when any difference is found. |
| `--no-color` | off | Disable colour. |

**`lint SPEC`**

| Flag | Default | Meaning |
|---|---|---|
| `--select RULE` | all | Only run these rules (repeatable). |
| `--ignore RULE` | none | Skip these rules (repeatable). |
| `--min-severity {error,warning,info}` | `info` | Lowest severity to report. |
| `--fail-on {error,warning,info,never}` | `error` | Severity at which to exit non-zero. |
| `--format {table,json,markdown}` | `table` | Output format. |
| `--list-rules` | off | Print rule names and exit. |
| `--no-color` | off | Disable colour. |

**`simulate SPEC`**

| Flag | Default | Meaning |
|---|---|---|
| `--samples FILE` | bundled sample ways | YAML fixture or CSV of OSM tags. |
| `--against NAME\|PATH` | none | Show before/after against a stock profile or another spec. |
| `--at HH:MM` | none | Time of day, activating time-of-day factors. |
| `--day XX` | `we` | Two-letter day (`mo`..`su`) used with `--at`. |
| `--format {table,json,markdown}` | `table` | Output format. |
| `--explain` | off | Print the factor chain applied to each way. |
| `--no-color` | off | Disable colour. |

**`init NAME`**

| Flag | Default | Meaning |
|---|---|---|
| `--extends BASE` | `car` | Bundled base profile to inherit from. |
| `-o`, `--out FILE` | `<name>.yaml` | Output file; `-` for stdout. |
| `--force` | off | Overwrite an existing file. |

**`profiles`** — takes `--no-color`.

### Sample file formats

`--samples` accepts either a YAML fixture:

```yaml
ways:
  - name: Euston Road
    length_m: 1100
    tags: {highway: primary, surface: asphalt, maxspeed: "30 mph"}
```

or a CSV whose columns are OSM keys (`name` and `length_m` are reserved, empty
cells are ignored) — which is what an Overpass export gives you:

```csv
name,highway,surface,maxspeed,maxweight
Riverside weight limit,secondary,asphalt,30 mph,7.5
```

## The spec format

Every quantity accepts a bare number in the canonical unit (km/h, m, kg, s) or a
string with an explicit unit: `"55 mph"`, `"7.5 t"`, `"13 ft"`, `"5 min"`.
Unknown keys are rejected.

```yaml
version: 1                    # required, currently always 1
name: urban-freight           # required; becomes the generated filename
mode: truck                   # car | van | truck | motorcycle | bicycle | foot
extends: truck                # optional: a bundled base or a sibling file
description: Free text.

vehicle:                      # physical envelope; drives access + Valhalla options
  height: 3.9 m
  width: 2.5 m
  length: 10.0 m
  weight: 18 t                # bare numbers here are KILOGRAMS - write the unit
  axle_load: 10 t
  axle_count: 3
  hazmat: false
  trailer: false

speeds:
  default: 20 km/h            # used for classes with no entry (needs an access tag)
  max_legal: 80 km/h          # hard ceiling applied after every other factor
  global_factor: 0.95         # multiplies every speed
  highway:
    motorway: 80 km/h
    residential: 18 km/h
    # ... any OSM highway value

surfaces:                     # multipliers keyed by surface=*
  asphalt: 1.0
  gravel: 0.45
tracktypes:                   # grade1..grade5 only
  grade3: 0.4
smoothness:                   # multipliers keyed by smoothness=*
  bad: 0.5

access:
  hierarchy: [hgv, goods, motor_vehicle, vehicle, access]   # most specific first
  allowed: ["yes", designated, permissive, destination, delivery]
  blocked: ["no", private, agricultural]
  respect_oneway: true
  allow_through_destination: false
  barriers:
    gate: block                                   # block | allow
    lift_gate: {action: penalty, penalty: 25 s}   # or a timed penalty

turn:
  base_penalty: 10 s
  u_turn_penalty: 75 s
  traffic_signal_penalty: 10 s
  stop_sign_penalty: 6 s
  crossing_penalty: 0 s
  angle_penalty: 6 s          # seconds per 90 degrees of heading change
  allow_u_turns: true
  restrictions: obey          # obey | ignore

zones:                        # low-emission / restricted areas
  - id: lez-core
    tag: low_emission_zone    # match by tag (both engines) ...
    value: "yes"              # ... optionally on an exact value
    action: block             # penalty | avoid | block
  - id: charge
    tag: congestion_charge
    action: penalty
    penalty: 300 s
  - id: harbour
    action: avoid
    polygon: [[-0.11, 51.50], [-0.09, 51.50], [-0.09, 51.52]]  # Valhalla only

time_factors:                 # time-of-day speed multipliers
  - name: am-peak
    hours: "07:00-09:59"      # 24h; may wrap past midnight
    days: [mo, tu, we, th, fr]
    factor: 0.65
    highway: [primary, secondary]   # omit to apply everywhere

extras:
  ferry: {allowed: true, speed: 12 km/h, penalty: 900 s}
  toll: {avoid: false, penalty: 180 s}
  cargo_bike:                 # bicycle mode only
    min_width: 1.0 m
    walk_steps: false
    gradient_penalty: 1.8
    max_gradient: 8.0
  charging:                   # electric fleets
    range: 180 km
    reserve_fraction: 0.25
    recharge_penalty: 2700 s

metadata:                     # free-form string map, carried into the output
  owner: city-logistics
```

Note on YAML: unquoted `yes` and `no` are booleans in YAML 1.1, which is exactly
the wrong thing for OSM access values. The tool converts them back, but quoting
them is clearer.

### Inheritance rules

`extends:` resolves to a sibling file first, then to a bundled base profile.
Chains may be any depth up to 16. Merge semantics are explicit:

| In the base | In the child | Result |
|---|---|---|
| mapping | mapping | merged recursively, key by key |
| anything | scalar | replaced |
| anything | `null` | **removed** (the only way to unset an inherited value) |
| list | list | replaced wholesale |
| `zones` / `time_factors` | list | merged by `id` / `name`, base order preserved, new entries appended |
| `zones` / `time_factors` entry | `{id: x, remove: true}` | that entry is deleted |

`name` and `extends` are never inherited — a child always keeps its own.

### Bundled base profiles

| Name | Mode | Extends | Summary |
|---|---|---|---|
| `car` | car | — | General-purpose passenger car, free-flow speeds, no dimension limits. |
| `truck` | truck | — | 40 t articulated HGV at EU maximum dimensions. |
| `van-urban` | van | `car` | 3.5 t urban delivery van with destination access and peak-hour factors. |
| `ev-delivery` | van | `van-urban` | Battery-electric 3.5 t van, LEZ-exempt, range-limited. |
| `cargo-bike` | bicycle | — | Electrically assisted cargo bike, 1.0 m wide, cannot be carried over barriers. |

Start from one with `speed-profile init my-fleet --extends van-urban`.

## Lint rules

| Rule | Severity | Fires when |
|---|---|---|
| `implausible-speed` | error | A speed exceeds what the mode can physically do (usually mph typed as km/h). |
| `contradictory-zones` | error / warning | Two zones match the same tag with different actions, or compound silently. |
| `network-fragmentation` | error / warning | `access=yes` is blocked, `allowed` is empty, or blocking a value that severs last-leg delivery. |
| `suspicious-units` | error | A vehicle mass under 100 kg — tonnes written as a bare number. |
| `no-speeds` | error | `speeds.highway` is empty. |
| `turn-restriction-bypass` | error | `turn.restrictions: ignore` — routes may be illegal to drive. |
| `unreachable-time-factor` | error / warning / info | A time factor with no days, no matching classes, or a factor of 1.0. |
| `speed-above-legal-max` | warning | A class speed above `speeds.max_legal`, which is silently clamped. |
| `impassable-penalty` | warning | A penalty of an hour or more; say `avoid` or `block` instead. |
| `missing-surface-handling` | warning / info | No surface multipliers, or a `track` speed with no tracktype table. |
| `unknown-highway` | warning | A highway class OSM does not use. |
| `missing-vehicle-limits` | warning | A truck or van with no declared dimensions. |
| `extreme-global-factor` | warning | `global_factor` far from 1.0, hiding the real speed table. |
| `default-speed-outlier` | warning | `speeds.default` faster than every declared class. |
| `ferry-sanity` | warning / info | Ferries faster than 40 km/h, or with no boarding penalty. |
| `dead-zone-rule` | info | A zone penalty below one second. |
| `polygon-zone-osrm` | info | A polygon-only zone, which OSRM cannot evaluate. |

`speed-profile lint --list-rules` prints the names; `--select` and `--ignore`
address them individually.

## Using it in CI

```yaml
- run: speed-profile validate profiles/fleet.yaml
- run: speed-profile lint profiles/fleet.yaml --format markdown >> $GITHUB_STEP_SUMMARY
- run: speed-profile build profiles/fleet.yaml -o build/ --check
- run: speed-profile diff profiles/fleet.yaml --format markdown >> $GITHUB_STEP_SUMMARY
```

Exit codes are stable: `0` success, `1` the command found what you asked it to
find (lint errors, a stale build, a non-empty diff under `--exit-code`), `2` bad
input. Codegen is deterministic, so `build --check` is a reliable gate against
someone hand-editing generated Lua.

## How penalties map onto each engine

The spec expresses penalties in seconds because that is how people think about
them. The engines disagree about whether that is expressible.

- **Valhalla** takes them almost directly — `maneuver_penalty`, `gate_penalty`,
  `ferry_cost` and `toll_booth_cost` are all seconds.
- **OSRM** has no way to add a fixed time to an edge inside `process_way`; the
  way's length is not known there. The generated Lua therefore converts a
  penalty into a multiplicative *rate* factor: a penalty equal to
  `--penalty-reference` (default 300 s) halves the edge's rate. The mapping is
  smooth and never reaches zero, so a penalised edge remains usable as a last
  resort rather than silently disconnecting the network. Tune the reference to
  the typical edge length in the region you are routing.

## Limitations

Stated plainly, because a codegen tool that hides its gaps is worse than none.

- **Polygon zones are Valhalla-only.** OSRM's `process_way` cannot evaluate
  geometry, so polygon zones are emitted as `exclude_polygons` for Valhalla and
  reported by the linter as inert for OSRM. Use a tag matcher for OSRM builds.
- **Barrier penalties are Valhalla-only.** OSRM's node result exposes only
  `barrier` and `traffic_lights` booleans, so a barrier with `action: penalty`
  is emitted as passable and noted in the generated file's header.
- **Valhalla has no per-class speed table.** Speeds come from the graph. The
  spec's speeds are carried in a non-standard `_speed_profile` block (Valhalla
  ignores unknown keys) for a graph-build or traffic-injection step to consume,
  and the closest expressible knobs (`top_speed`, `use_highways`, `use_tracks`,
  `use_living_streets`) are derived from them.
- **Time-of-day factors are not applied by either engine at request time.** They
  are honoured by `simulate` and exported in the Valhalla extension block; OSRM
  needs its traffic-update mechanism to consume them.
- **Stock baselines are pinned approximations** of OSRM's `car.lua`,
  `bicycle.lua`, `foot.lua` and Valhalla's truck costing defaults, not live
  imports. That is deliberate — a diff that shifted because upstream edited a
  comment would be useless.
- **The simulator is a model, not the engine.** It mirrors the generated
  `process_way` step for step, but it does not do turn costs, contraction, or
  map matching.
- **Elevation is not modelled.** `cargo_bike.gradient_penalty` and
  `max_gradient` are carried through to Valhalla's `use_hills`; there is no
  terrain input.

## Development

```bash
pip install -e ".[dev]"
pytest -q
ruff check .
ruff format --check .
```

Golden files under `tests/golden/` cover every bundled profile on both engines.
When a codegen change is intentional:

```bash
UPDATE_GOLDEN=1 pytest tests/test_golden.py
```

then read the resulting diff before committing — that diff *is* the review. The
generated Lua is syntax-checked when a Lua interpreter (or `lupa`) is available;
the suite passes without one.

## Further reading

Background on the decisions this tool encodes:

- [Speed profile calibration for heavy vehicles](https://www.geospatialrouting.com/osm-graph-architecture-network-modeling/speed-profile-calibration-for-heavy-vehicles/) — how to derive the numbers that go in the `speeds` block.
- [Calibrating speed profiles for electric delivery fleets](https://www.geospatialrouting.com/osm-graph-architecture-network-modeling/speed-profile-calibration-for-heavy-vehicles/calibrating-speed-profiles-for-electric-delivery-fleets/) — the reasoning behind the bundled `ev-delivery` base.
- [Configuring edge weights for freight logistics](https://www.geospatialrouting.com/osm-graph-architecture-network-modeling/configuring-edge-weights-for-freight-logistics/) — why weight and duration diverge, and what that means for the rate factors this tool emits.
- [Encoding low-emission zone penalties](https://www.geospatialrouting.com/osm-graph-architecture-network-modeling/configuring-edge-weights-for-freight-logistics/encoding-low-emission-zone-penalties/) — the modelling behind the `zones` block.
- [Valhalla costing JSON for cargo bikes](https://www.geospatialrouting.com/python-routing-engines-isochrone-mapping/valhalla-configuration-for-multi-modal-analysis/valhalla-costing-json-for-cargo-bikes/) — what the `cargo-bike` base is trying to express.
- [Setting turn restrictions in GraphHopper vs OSRM](https://www.geospatialrouting.com/osm-graph-architecture-network-modeling/handling-turn-restrictions-in-routing-graphs/setting-turn-restrictions-in-graphhopper-vs-osrm/) — context for the `turn` block and the generated `process_turn`.

## Related tools

- [osrm-quickstart](https://github.com/geospatialrouting/osrm-quickstart) — get an OSRM instance running against a real extract.
- [route-regression-check](https://github.com/geospatialrouting/route-regression-check) — catch route changes between two engine builds.

## Licence

MIT. See [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

Maintained by [geospatialrouting.com](https://www.geospatialrouting.com).
