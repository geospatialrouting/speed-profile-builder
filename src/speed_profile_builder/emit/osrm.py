"""Emit an OSRM Lua profile from the normalised IR.

The generated file is a genuine, self-contained OSRM profile: it defines
``setup``, ``process_node``, ``process_way`` and ``process_turn``, returns them
in the table OSRM expects, and deliberately does *not* ``require`` any of
OSRM's ``lib/*`` helpers. Self-containment costs a little duplication and buys
two things worth more: the file can be handed to ``osrm-extract -p`` from any
directory, and its syntax can be checked by any Lua interpreter without OSRM's
source tree on the package path.

Two model mismatches are handled explicitly rather than pretended away:

* OSRM cannot add a fixed number of seconds to an edge inside ``process_way``,
  because the length is not known there. Second-denominated penalties (zones,
  tolls, ferries) become multiplicative *rate* factors — see
  :func:`~speed_profile_builder.emit.common.rate_factor`.
* OSRM's node result exposes only ``barrier`` and ``traffic_lights`` booleans,
  so a barrier with ``action: penalty`` cannot be charged a time cost. It is
  emitted as passable and recorded in the header, so the difference between the
  two engines is visible in the artefact instead of being a surprise later.
"""

from __future__ import annotations

from ..model import Profile
from .common import EmitOptions, num, provenance, rate_factor

#: Mapping from spec transport modes to the OSRM ``mode`` enum members.
_OSRM_MODE = {
    "car": "mode.driving",
    "truck": "mode.driving",
    "van": "mode.driving",
    "motorcycle": "mode.driving",
    "bicycle": "mode.cycling",
    "foot": "mode.walking",
}

#: OSM keys carrying vehicle restrictions, paired with the IR attribute to test.
_DIMENSION_TAGS: tuple[tuple[str, str], ...] = (
    ("maxheight", "height_m"),
    ("maxwidth", "width_m"),
    ("maxlength", "length_m"),
    ("maxweight", "weight_kg"),
    ("maxaxleload", "axle_load_kg"),
)


def _lua_string(value: str) -> str:
    """Quote a string for Lua, escaping the few characters that matter."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _lua_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "nil"
    if isinstance(value, (int, float)):
        return num(float(value))
    return _lua_string(str(value))


def _lua_map(items: dict[str, object], indent: str = "  ") -> str:
    """Render a Lua table with bracket-quoted keys, one entry per line.

    Bracket-quoting every key avoids having to reason about which OSM values
    happen to be valid Lua identifiers (``concrete:plates`` is not).
    """
    if not items:
        return "{}"
    body = "\n".join(f"{indent}[{_lua_string(k)}] = {_lua_value(v)}," for k, v in items.items())
    return "{\n" + body + f"\n{indent[:-2]}}}"


def _lua_set(values: tuple[str, ...] | list[str], indent: str = "  ") -> str:
    """Render a set-like Lua table (``{[v] = true}``) in the given order."""
    return _lua_map(dict.fromkeys(values, True), indent)


def _lua_list(values: tuple[str, ...] | list[str]) -> str:
    return "{ " + ", ".join(_lua_string(v) for v in values) + " }"


def _zone_entries(profile: Profile, options: EmitOptions) -> str:
    rows = []
    for zone in profile.zones:
        if zone.tag is None:
            continue  # polygon zones are handled by a preprocessing step
        factor = (
            0.0
            if zone.action in ("avoid", "block")
            else rate_factor(zone.penalty_s, options.penalty_reference_s)
        )
        rows.append(
            "  { "
            f"id = {_lua_string(zone.id)}, "
            f"tag = {_lua_string(zone.tag)}, "
            f"value = {_lua_value(zone.value)}, "
            f"block = {'true' if zone.action == 'block' else 'false'}, "
            f"factor = {num(factor)} "
            "},"
        )
    if not rows:
        return "{}"
    return "{\n" + "\n".join(rows) + "\n}"


def emit_osrm(profile: Profile, options: EmitOptions | None = None) -> str:
    """Render ``profile`` as OSRM Lua source. Deterministic for a given input."""
    options = options or EmitOptions()
    mode_expr = _OSRM_MODE[profile.mode]
    veh = profile.vehicle

    lines: list[str] = []
    if options.header:
        lines += provenance(profile, "--", options)
        lines += [
            "--",
            "-- Penalties written in seconds in the spec are applied as edge rate factors;",
            f"-- a penalty of {num(options.penalty_reference_s)} s halves an edge's rate.",
        ]
        soft = [b.key for b in profile.access.barriers if b.action == "penalty"]
        if soft:
            lines.append(
                "-- Note: OSRM cannot charge time at a node, so these barriers are emitted as"
            )
            lines.append(f"--       passable with no penalty: {', '.join(sorted(soft))}.")
        lines.append("")

    lines += [
        "api_version = 4",
        "",
        f"local PROFILE_NAME = {_lua_string(profile.name)}",
        f"local DEFAULT_SPEED = {num(profile.default_speed_kmh)}",
        f"local GLOBAL_FACTOR = {num(profile.global_factor)}",
        f"local MAX_LEGAL_SPEED = {_lua_value(profile.max_legal_speed_kmh)}",
        "",
        f"local speeds = {_lua_map(dict(profile.speeds_kmh))}",
        "",
        f"local surface_factors = {_lua_map(dict(profile.surfaces))}",
        "",
        f"local tracktype_factors = {_lua_map(dict(profile.tracktypes))}",
        "",
        f"local smoothness_factors = {_lua_map(dict(profile.smoothness))}",
        "",
        f"local access_hierarchy = {_lua_list(profile.access.hierarchy)}",
        f"local access_allow = {_lua_set(profile.access.allowed)}",
        f"local access_deny = {_lua_set(profile.access.blocked)}",
        f"local allow_through_destination = {_lua_value(profile.access.allow_through_destination)}",
        "",
        "local barrier_block = "
        + _lua_set([b.key for b in profile.access.barriers if b.action == "block"]),
        "local barrier_allow = "
        + _lua_set([b.key for b in profile.access.barriers if b.action != "block"]),
        "",
        "local vehicle = {",
        f"  height = {_lua_value(veh.height_m)},",
        f"  width = {_lua_value(veh.width_m)},",
        f"  length = {_lua_value(veh.length_m)},",
        f"  weight = {_lua_value(veh.weight_kg)},",
        f"  axle_load = {_lua_value(veh.axle_load_kg)},",
        f"  hazmat = {_lua_value(veh.hazmat)},",
        "}",
        "",
        f"local zone_rules = {_zone_entries(profile, options)}",
        "",
        f"local FERRY_ALLOWED = {_lua_value(profile.ferry.allowed)}",
        f"local FERRY_SPEED = {num(profile.ferry.speed_kmh)}",
        "local FERRY_FACTOR = "
        + num(rate_factor(profile.ferry.penalty_s, options.penalty_reference_s)),
        f"local TOLL_AVOID = {_lua_value(profile.toll.avoid)}",
        "local TOLL_FACTOR = "
        + num(rate_factor(profile.toll.penalty_s, options.penalty_reference_s)),
        "",
        _RUNTIME,
        "",
        "function setup()",
        "  return {",
        "    properties = {",
        "      max_speed_for_map_matching      = 180 / 3.6,",
        "      weight_name                     = 'routability',",
        "      process_call_tagless_node       = false,",
        f"      u_turn_penalty                  = {num(profile.turn.u_turn_penalty_s)},",
        f"      traffic_light_penalty           = {num(profile.turn.traffic_signal_penalty_s)},",
        "      continue_straight_at_waypoint   = true,",
        f"      use_turn_restrictions           = {_lua_value(profile.turn.obey_restrictions)},",
        "      left_hand_driving               = false,",
        "      weight_precision                = 2,",
        "    },",
        f"    default_mode = {mode_expr},",
        "    default_speed = DEFAULT_SPEED,",
        "    oneway_handling = " + _lua_value(profile.access.respect_oneway) + ",",
        f"    turn_penalty = {num(profile.turn.base_penalty_s)},",
        f"    angle_penalty = {num(profile.turn.angle_penalty_s)},",
        f"    stop_sign_penalty = {num(profile.turn.stop_sign_penalty_s)},",
        f"    crossing_penalty = {num(profile.turn.crossing_penalty_s)},",
        f"    allow_u_turns = {_lua_value(profile.turn.allow_u_turns)},",
        "    name = PROFILE_NAME,",
        "  }",
        "end",
        "",
        _PROCESS_NODE,
        "",
        _process_way(profile, mode_expr),
        "",
        _PROCESS_TURN,
        "",
        "return {",
        "  setup = setup,",
        "  process_way = process_way,",
        "  process_node = process_node,",
        "  process_turn = process_turn,",
        "}",
        "",
    ]
    return "\n".join(lines)


_RUNTIME = """local MAX_TURN_WEIGHT = 100000

-- Parse a numeric OSM restriction value such as "4.5", "4.5 m" or "12'6\\"".
-- Returns nil for values that carry no usable number, which callers must treat
-- as "unrestricted" rather than "zero".
local function parse_measure(value)
  if not value then return nil end
  local feet, inches = value:match("^(%d+)'%s*(%d*)")
  if feet then
    return tonumber(feet) * 0.3048 + (tonumber(inches) or 0) * 0.0254
  end
  local number = value:match("^%s*(%-?%d+%.?%d*)")
  if not number then return nil end
  local scale = 1.0
  if value:find("ft") then scale = 0.3048 end
  return tonumber(number) * scale
end

-- OSM writes maxweight and maxaxleload in tonnes unless a unit says otherwise.
local function parse_mass_kg(value)
  local base = parse_measure(value)
  if not base then return nil end
  if value:find("kg") then return base end
  return base * 1000.0
end

-- Walk the access hierarchy most-specific-first. The first tag that carries a
-- recognised value decides; an unrecognised value falls through so that, for
-- example, hgv=discouraged does not silently block the way.
local function access_verdict(way)
  for i = 1, #access_hierarchy do
    local value = way:get_value_by_key(access_hierarchy[i])
    if value then
      if access_deny[value] then return false end
      if access_allow[value] then
        if value == "destination" and not allow_through_destination then
          return true, true
        end
        return true
      end
    end
  end
  return nil
end

local function dimension_blocked(way)
  if vehicle.height and (parse_measure(way:get_value_by_key("maxheight")) or math.huge)
      < vehicle.height then return "maxheight" end
  if vehicle.width and (parse_measure(way:get_value_by_key("maxwidth")) or math.huge)
      < vehicle.width then return "maxwidth" end
  if vehicle.length and (parse_measure(way:get_value_by_key("maxlength")) or math.huge)
      < vehicle.length then return "maxlength" end
  if vehicle.weight and (parse_mass_kg(way:get_value_by_key("maxweight")) or math.huge)
      < vehicle.weight then return "maxweight" end
  if vehicle.axle_load and (parse_mass_kg(way:get_value_by_key("maxaxleload")) or math.huge)
      < vehicle.axle_load then return "maxaxleload" end
  if vehicle.hazmat and way:get_value_by_key("hazmat") == "no" then return "hazmat" end
  return nil
end

local function apply_cap(speed)
  if MAX_LEGAL_SPEED and speed > MAX_LEGAL_SPEED then speed = MAX_LEGAL_SPEED end
  if speed < 5 then speed = 5 end
  return speed
end

-- Combined rate factor from every zone rule that matches this way. Returns 0
-- when a rule blocks the way outright.
local function zone_factor(way)
  local factor = 1.0
  for i = 1, #zone_rules do
    local rule = zone_rules[i]
    local value = way:get_value_by_key(rule.tag)
    if value and value ~= "no" and (rule.value == nil or value == rule.value) then
      if rule.block or rule.factor == 0 then return 0.0 end
      factor = factor * rule.factor
    end
  end
  return factor
end"""


_PROCESS_NODE = """function process_node(profile, node, result, relations)
  local barrier = node:get_value_by_key("barrier")
  if barrier and not barrier_allow[barrier] then
    if barrier_block[barrier] then
      result.barrier = true
    else
      -- Unlisted barriers are treated as impassable unless an access tag on the
      -- node explicitly opens them; this is the conservative reading and avoids
      -- routing lorries through unmapped obstacles.
      local access = nil
      for i = 1, #access_hierarchy do
        local value = node:get_value_by_key(access_hierarchy[i])
        if value then access = value break end
      end
      result.barrier = not (access and access_allow[access])
    end
  end

  if node:get_value_by_key("highway") == "traffic_signals" then
    result.traffic_lights = true
  end
end"""


def _process_way(profile: Profile, mode_expr: str) -> str:
    """Render ``process_way``; the branches vary a little by transport mode."""
    steps_line = (
        '  if data.highway == "steps" then return end'
        if profile.mode != "foot"
        else "  -- steps are usable on foot"
    )
    return f"""function process_way(profile, way, result, relations)
  local data = {{
    highway = way:get_value_by_key("highway"),
    route   = way:get_value_by_key("route"),
  }}
  if not data.highway and not data.route then return end

  -- Ferries first: they are not highways and must not inherit road speeds.
  if data.route == "ferry" then
    if not FERRY_ALLOWED then return end
    result.forward_mode = mode.ferry
    result.backward_mode = mode.ferry
    result.forward_speed = FERRY_SPEED
    result.backward_speed = FERRY_SPEED
    result.forward_rate = FERRY_SPEED * FERRY_FACTOR / 3.6
    result.backward_rate = FERRY_SPEED * FERRY_FACTOR / 3.6
    result.name = way:get_value_by_key("name") or "ferry"
    return
  end
  if not data.highway then return end
{steps_line}

  local allowed, destination_only = access_verdict(way)
  if allowed == false then return end
  if dimension_blocked(way) then return end

  local speed = speeds[data.highway]
  if not speed then
    if allowed ~= true then return end
    speed = DEFAULT_SPEED
  end

  local surface = way:get_value_by_key("surface")
  if surface and surface_factors[surface] then
    speed = speed * surface_factors[surface]
  end
  local tracktype = way:get_value_by_key("tracktype")
  if tracktype and tracktype_factors[tracktype] then
    speed = speed * tracktype_factors[tracktype]
  end
  local smoothness = way:get_value_by_key("smoothness")
  if smoothness and smoothness_factors[smoothness] then
    speed = speed * smoothness_factors[smoothness]
  end

  speed = speed * GLOBAL_FACTOR

  local posted = parse_measure(way:get_value_by_key("maxspeed"))
  if posted and posted > 0 then
    local unit_mph = (way:get_value_by_key("maxspeed") or ""):find("mph")
    if unit_mph then posted = posted * 1.609344 end
    if posted < speed then speed = posted end
  end
  speed = apply_cap(speed)

  local factor = zone_factor(way)
  if factor == 0 then return end
  if TOLL_AVOID and way:get_value_by_key("toll") == "yes" then return end
  if way:get_value_by_key("toll") == "yes" then factor = factor * TOLL_FACTOR end
  if destination_only then factor = factor * 0.1 end

  result.forward_speed = speed
  result.backward_speed = speed
  result.forward_rate = speed * factor / 3.6
  result.backward_rate = speed * factor / 3.6
  result.forward_mode = {mode_expr}
  result.backward_mode = {mode_expr}
  result.name = way:get_value_by_key("name") or way:get_value_by_key("ref") or ""

  if profile.oneway_handling then
    local oneway = way:get_value_by_key("oneway")
    if oneway == "yes" or oneway == "1" or oneway == "true" then
      result.backward_mode = mode.inaccessible
    elseif oneway == "-1" then
      result.forward_mode = mode.inaccessible
    elseif way:get_value_by_key("junction") == "roundabout" then
      result.backward_mode = mode.inaccessible
    end
  end

  if way:get_value_by_key("junction") == "roundabout" then
    result.roundabout = true
  end
end"""


_PROCESS_TURN = """function process_turn(profile, turn)
  if turn.has_traffic_light then
    turn.duration = turn.duration + profile.properties.traffic_light_penalty
  end

  if turn.number_of_roads > 2 or turn.source_mode ~= turn.target_mode or turn.is_u_turn then
    turn.duration = turn.duration + profile.turn_penalty
    if profile.angle_penalty > 0 then
      turn.duration = turn.duration + profile.angle_penalty * math.abs(turn.angle) / 90.0
    end
    if turn.is_u_turn then
      if not profile.allow_u_turns then
        -- OSRM has no "forbid" flag on a turn; a very large weight is the
        -- documented way to make one effectively unusable.
        turn.weight = MAX_TURN_WEIGHT
        return
      end
      turn.duration = turn.duration + profile.properties.u_turn_penalty
    end
  end

  if profile.properties.weight_name == 'routability' then
    -- Discourage entering restricted (destination-only) road segments from
    -- unrestricted ones; matches the stock OSRM behaviour.
    if not turn.source_restricted and turn.target_restricted then
      turn.weight = turn.weight + 3000
    end
  end
end"""
