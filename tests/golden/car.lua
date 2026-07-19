-- GENERATED FILE - DO NOT EDIT.
-- Produced by speed-profile-builder golden from a declarative spec.
--
-- profile:     car (mode: car)
-- spec chain:  car.yaml
-- fingerprint: 33652ecb53c4648d
-- summary:     General-purpose passenger car, free-flow speeds, no dimension limits.
--
-- Edit the spec and re-run:  speed-profile build <spec>
--
-- Penalties written in seconds in the spec are applied as edge rate factors;
-- a penalty of 300 s halves an edge's rate.
-- Note: OSRM cannot charge time at a node, so these barriers are emitted as
--       passable with no penalty: lift_gate, toll_booth.

api_version = 4

local PROFILE_NAME = "car"
local DEFAULT_SPEED = 30
local GLOBAL_FACTOR = 1
local MAX_LEGAL_SPEED = 140

local speeds = {
  ["living_street"] = 10,
  ["motorway"] = 110,
  ["motorway_link"] = 60,
  ["primary"] = 80,
  ["primary_link"] = 50,
  ["residential"] = 30,
  ["road"] = 30,
  ["secondary"] = 70,
  ["secondary_link"] = 45,
  ["service"] = 20,
  ["tertiary"] = 60,
  ["tertiary_link"] = 40,
  ["track"] = 15,
  ["trunk"] = 95,
  ["trunk_link"] = 55,
  ["unclassified"] = 45,
}

local surface_factors = {
  ["asphalt"] = 1,
  ["cobblestone"] = 0.55,
  ["compacted"] = 0.8,
  ["concrete"] = 1,
  ["dirt"] = 0.45,
  ["fine_gravel"] = 0.7,
  ["grass"] = 0.4,
  ["gravel"] = 0.6,
  ["ground"] = 0.5,
  ["mud"] = 0.3,
  ["paved"] = 1,
  ["paving_stones"] = 0.9,
  ["sand"] = 0.35,
  ["sett"] = 0.6,
  ["unpaved"] = 0.6,
}

local tracktype_factors = {
  ["grade1"] = 1,
  ["grade2"] = 0.8,
  ["grade3"] = 0.6,
  ["grade4"] = 0.45,
  ["grade5"] = 0.3,
}

local smoothness_factors = {
  ["bad"] = 0.65,
  ["excellent"] = 1,
  ["good"] = 1,
  ["horrible"] = 0.3,
  ["intermediate"] = 0.9,
  ["very_bad"] = 0.45,
}

local access_hierarchy = { "motorcar", "motor_vehicle", "vehicle", "access" }
local access_allow = {
  ["yes"] = true,
  ["designated"] = true,
  ["permissive"] = true,
  ["destination"] = true,
  ["delivery"] = true,
}
local access_deny = {
  ["no"] = true,
  ["private"] = true,
  ["agricultural"] = true,
  ["forestry"] = true,
  ["military"] = true,
}
local allow_through_destination = false

local barrier_block = {
  ["block"] = true,
  ["bollard"] = true,
  ["cycle_barrier"] = true,
  ["gate"] = true,
  ["kissing_gate"] = true,
  ["stile"] = true,
  ["yes"] = true,
}
local barrier_allow = {
  ["lift_gate"] = true,
  ["toll_booth"] = true,
}

local vehicle = {
  height = nil,
  width = nil,
  length = nil,
  weight = nil,
  axle_load = nil,
  hazmat = false,
}

local zone_rules = {}

local FERRY_ALLOWED = true
local FERRY_SPEED = 12
local FERRY_FACTOR = 0.5
local TOLL_AVOID = false
local TOLL_FACTOR = 1

local MAX_TURN_WEIGHT = 100000

-- Parse a numeric OSM restriction value such as "4.5", "4.5 m" or "12'6\"".
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
end

function setup()
  return {
    properties = {
      max_speed_for_map_matching      = 180 / 3.6,
      weight_name                     = 'routability',
      process_call_tagless_node       = false,
      u_turn_penalty                  = 20,
      traffic_light_penalty           = 2,
      continue_straight_at_waypoint   = true,
      use_turn_restrictions           = true,
      left_hand_driving               = false,
      weight_precision                = 2,
    },
    default_mode = mode.driving,
    default_speed = DEFAULT_SPEED,
    oneway_handling = true,
    turn_penalty = 7.5,
    angle_penalty = 3,
    stop_sign_penalty = 2,
    crossing_penalty = 0,
    allow_u_turns = true,
    name = PROFILE_NAME,
  }
end

function process_node(profile, node, result, relations)
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
end

function process_way(profile, way, result, relations)
  local data = {
    highway = way:get_value_by_key("highway"),
    route   = way:get_value_by_key("route"),
  }
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
  if data.highway == "steps" then return end

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
  result.forward_mode = mode.driving
  result.backward_mode = mode.driving
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
end

function process_turn(profile, turn)
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
end

return {
  setup = setup,
  process_way = process_way,
  process_node = process_node,
  process_turn = process_turn,
}
