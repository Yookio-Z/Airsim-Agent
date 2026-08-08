# Intelligent Ground Control Station Architecture Roadmap

> 文档状态：总体架构背景文档。当前系统状态、阶段优先级和 Jetson/边侧 Agent 路线以 [`system_upgrade_plan.md`](system_upgrade_plan.md) 为准。

本文档保留产品方向、GCS 分层和 Backend 抽象的长期设计；其中早期 Phase 编号是历史路线，不应覆盖当前主线方案。

## 1. Historical baseline and current interpretation

The original Web Agent started as an in-process AirSim command runtime:

```text
Web UI
  -> AgentRuntime
      -> LLMMissionPlanner / MissionPlanner
      -> ToolRuntime
      -> AirSimController
```

That historical baseline explains why the later abstractions were introduced.
The current repository also contains PX4 MAVLink, PX4 ROS2 Gateway, GCS Mission
services, approval gates, and provider contracts. The remaining gaps should be
read from `system_upgrade_plan.md`, not inferred from the original limitations
below.

The original limitations were:

- The planner creates a one-shot mission plan. It does not observe, reason,
  act, and replan in a loop.
- The runtime executes a flat list of tools. It has no goal/subgoal/skill
  hierarchy.
- The Web Agent's `ToolRuntime` is still AirSim-centric, even though lower
  layers already contain a `FlightController` abstraction and a PX4/MAVLink
  controller.
- Perception and search tools are tied to AirSim image APIs.
- Memory is mostly a record source, not an active decision system.
- Safety exists, but it is not yet platform-profiled for PX4 SITL, ROS2, or
  real aircraft.

The immediate priority is not to add a more complex LLM loop on top of this
execution layer. The immediate priority is to decouple execution backends.

## 2. Product Direction

The system should evolve into an AI-augmented ground control station, not an
AirSim-only chat panel.

The product target is:

```text
QGroundControl-style ground station
  + tile map
  + waypoint mission planning
  + PX4/MAVLink connectivity
  + telemetry monitoring
  + mission upload/start/pause/RTL/land
  + Agent copilot for intelligent planning and supervision
```

The ground station is the operational center. The Agent is the intelligent
copilot inside that ground station. This distinction matters because future
PX4 SITL, ROS2, and real aircraft support should use the same vehicle,
telemetry, mission, and safety APIs as the manual UI.

The Agent should not bypass the ground station stack with private control
paths. It should call the same mission and command services that the UI uses.

```text
Manual UI action
  -> Ground Station API
      -> Vehicle / Mission / Command / Safety managers
          -> Backend adapter

Agent action
  -> Ground Station API
      -> Vehicle / Mission / Command / Safety managers
          -> Backend adapter
```

This keeps manual control, automated missions, and Agent decisions coherent.

## 3. Overall System Architecture

The whole system should be organized as one integrated ground station:

```text
Frontend Ground Station
  -> Map / Plan View
      -> tile map
      -> waypoint editing
      -> mission item editing
      -> geofence / rally / home display
  -> Fly View
      -> live vehicle position
      -> telemetry
      -> mode / arm / battery / GPS / link health
      -> quick commands: arm, takeoff, pause, RTL, land
  -> Agent Copilot
      -> natural-language command
      -> plan explanation
      -> safety warnings
      -> mission suggestions

Backend Ground Station Service
  -> LinkManager
      -> MAVLink UDP / TCP / serial links
      -> later: ROS2 bridge links
  -> VehicleManager
      -> vehicle discovery from heartbeat
      -> active vehicle selection
      -> multi-vehicle state
  -> TelemetryManager
      -> position, attitude, velocity, battery, GPS, mode, armed state
  -> MissionManager
      -> mission upload / download / clear / start
      -> waypoint conversion
      -> mission progress
  -> CommandManager
      -> arm, disarm, takeoff, land, RTL, hold, mode change
  -> ParameterManager
      -> PX4 parameter read/write, later UI editing
  -> SafetyManager
      -> geofence, altitude envelope, mode checks, failsafe checks

Agent Runtime
  -> TaskRouter
  -> AgentLoop / ReAct planner
  -> SkillRegistry
  -> WorldState / BeliefState
  -> Verifier
  -> Memory

Backend Adapters
  -> AirSimBackend
  -> PX4MavlinkBackend
  -> ROS2Backend
  -> RealDroneBackend
```

The front end and Agent should both observe the same `WorldState`. The
backend should publish a single source of truth for vehicle state, mission
state, link state, and safety state.

## 4. Map And Mission Planning View

The current middle panel should evolve toward a QGroundControl-style Plan View.
It should not remain a decorative dashboard. It should become the operator's
main mission planning surface.

Expected map features:

- Tile map rendering, preferably with MapLibre GL JS for a modern map layer
  system. Leaflet is also acceptable for a smaller MVP.
- Vehicle position marker with heading.
- Home position marker.
- Editable waypoint list.
- Click-to-add waypoint.
- Drag-to-move waypoint.
- Altitude, speed, hold time, camera action, and acceptance radius per waypoint.
- Polyline route preview.
- Mission distance and estimated duration.
- Geofence display and editing.
- Rally/safe point display later.
- Upload mission to vehicle.
- Download mission from vehicle.
- Clear mission.
- Start/pause/resume mission.
- RTL and land quick actions.

Mission items should be represented in a backend-neutral schema first:

```json
{
  "id": "wp_001",
  "type": "waypoint",
  "frame": "global_relative_alt",
  "lat": 47.397742,
  "lon": 8.545594,
  "alt_m": 30.0,
  "speed_mps": 5.0,
  "hold_s": 0.0,
  "acceptance_radius_m": 2.0,
  "actions": []
}
```

The PX4/MAVLink adapter can convert this schema to MAVLink mission items. The
AirSim adapter can convert it to local NED waypoints. The ROS2 adapter can
convert it to whatever mission/action interface the selected ROS2 stack
provides.

## 5. QGroundControl And PX4 Connection Model

QGroundControl works as a MAVLink ground station. The important architectural
idea is not the UI skin; it is the separation of link, vehicle, telemetry,
mission, command, and parameter responsibilities.

The relevant flow is:

```text
LinkManager
  -> opens UDP / TCP / serial links
  -> receives MAVLink bytes
  -> MAVLinkProtocol parses messages
  -> HEARTBEAT identifies a vehicle
  -> MultiVehicleManager creates/updates a Vehicle object
  -> Vehicle owns telemetry, commands, mission, params, and state
```

For PX4, the ground station should connect through MAVLink:

```text
PX4 SITL / real PX4
  -> MAVLink stream
      -> UDP, TCP, or serial telemetry link
          -> LinkManager
              -> VehicleManager
                  -> TelemetryManager / MissionManager / CommandManager
```

For WSL-based PX4 SITL, the design must account for:

- UDP endpoint routing between WSL and Windows.
- Whether QGroundControl and this system need to connect at the same time.
- MAVLink forwarding or routing if multiple ground stations are active.
- Heartbeat freshness and link timeout detection.
- PX4 mode requirements before accepting position or mission commands.
- Coordinate conversion between NED, ENU, and GPS/global frames.

The mission flow should eventually follow the MAVLink mission protocol shape:

```text
create local mission plan
  -> validate against safety constraints
  -> upload mission to vehicle
  -> confirm mission accepted
  -> start mission
  -> monitor mission current/mission reached messages
  -> verify final state
```

Manual mission editing and Agent-generated missions must use the same
`MissionManager`.

## 6. Agent Target Architecture

The long-term architecture should keep the agent independent from the vehicle
platform:

```text
UI / API
  -> AgentRuntime
      -> TaskRouter
      -> AgentLoop
          -> WorldState / BeliefState
          -> Policy / LLM ReAct Planner
          -> SkillRegistry
              -> NavigationSkill
              -> SearchSkill
              -> TrackingSkill
              -> PerceptionSkill
          -> ToolRuntime
              -> CapabilityRegistry
              -> BackendRegistry
                  -> AirSimBackend
                  -> PX4MavlinkBackend
                  -> ROS2Backend
                  -> RealDroneBackend
          -> SafetyArbiter
          -> Verifier
          -> Memory
```

The key rule is:

```text
Agent depends on skills and capabilities.
Skills depend on abstract providers.
Backends implement platform-specific details.
```

The Agent should not know whether a command is executed through AirSim RPC,
PX4 MAVLink, ROS2 topics/services/actions, or a real vehicle bridge.

## 7. Backend And Capability Model

Each backend should declare:

- Backend identity: `airsim`, `px4_mavlink`, `ros2`, `real_drone`.
- Connection model: AirSim RPC endpoint, MAVLink URL, ROS2 domain/node config,
  or real vehicle bridge config.
- Supported capabilities.
- Safety profile.
- Coordinate frame expectations.

Example capabilities:

```text
flight_control
mode_control
telemetry
gps
image_capture
depth_perception
object_detection
target_search
target_tracking
obstacle_avoidance
multi_vehicle
ros2_topics
real_vehicle
```

Backend examples:

```text
AirSimBackend
  flight_control: yes
  mode_control: limited
  telemetry: yes
  image_capture: yes
  depth_perception: yes
  object_detection: yes
  target_search: yes
  target_tracking: yes
  real_vehicle: no

PX4MavlinkBackend
  flight_control: yes
  mode_control: yes
  telemetry: yes
  gps: yes
  image_capture: no
  target_search: no
  real_vehicle: optional later

ROS2Backend
  flight_control: optional
  telemetry: yes
  image_capture: optional
  object_detection: optional
  ros2_topics: yes

RealDroneBackend
  flight_control: yes
  telemetry: yes
  gps: yes
  safety profile: strict
  human confirmation: required for high-risk actions
```

The planner should receive the current capability list. It should never plan a
camera task on a backend that cannot capture images, and it should never plan
PX4 mode changes on an AirSim-only backend unless the backend declares support.

## 8. Task Difficulty Routing

Not every command should run through a full ReAct loop. The runtime should first
classify task difficulty:

```text
L0: Direct tool command
    Examples: get status, connect, hover, land.
    Execution: no LLM needed.

L1: Template skill command
    Examples: take off to 5m, move forward 10m, take a photo.
    Execution: rule extraction or lightweight planner.

L2: One-shot planned mission
    Examples: take off, fly to a point, take a photo, report status.
    Execution: plan -> execute -> verify.

L3: Agent loop mission
    Examples: search for a vehicle, approach and confirm it, track a target.
    Execution: observe -> decide -> validate -> act -> update belief -> repeat.

L4: High-risk mission
    Examples: real aircraft flight, low-altitude obstacle traversal,
    multi-vehicle coordination, long-range flight.
    Execution: strict safety profile and human-in-the-loop approval.
```

This avoids slowing down simple control actions with unnecessary LLM calls while
allowing complex missions to use ReAct-style reasoning and replanning.

## 9. ReAct And Control Latency

LLM-based ReAct must not sit in the low-level flight control loop.

Bad design:

```text
LLM decides every 0.5m movement.
LLM decides every yaw adjustment.
LLM decides every control tick.
```

Better design:

```text
LLM chooses a macro action or skill.
Skill performs local closed-loop control.
Skill returns structured observations.
Agent decides whether to continue, replan, or stop.
```

For example:

```text
Agent action:
  run SearchTargetSkill(target="car", radius=30, altitude=5)

Skill internals:
  generate waypoints
  capture frames
  run detection
  update belief
  stop when target confidence is high or timeout is reached

Agent receives:
  target_found=true
  confidence=0.82
  image_path=...
  target_position_ned=...
```

This keeps the vehicle responsive and keeps LLM reasoning at mission scale.

## 10. Skill Layer

Skills should be backend-aware but not backend-bound. A skill should depend on
abstract providers:

```text
NavigationProvider
  takeoff
  land
  hover
  move_to
  move_relative
  fly_path
  set_mode

TelemetryProvider
  get_status
  get_gps
  get_attitude
  get_battery

PerceptionProvider
  capture_image
  get_depth
  detect_objects
  subscribe_frames

TrackingProvider
  lock_target
  track_target
  stop_tracking
```

AirSim can implement all of these in-process. PX4 MAVLink initially implements
only flight and telemetry. ROS2 can provide camera/perception through topics and
flight control through MAVROS, PX4 ROS2 bridge, or a custom adapter.

## 11. Safety And Verification

Safety must be platform-specific:

```text
AirSim profile:
  permissive sandbox
  default geofence
  simulation collision checks

PX4 SITL profile:
  mode checks
  offboard/guided prerequisites
  heartbeat freshness
  failsafe state checks

Real aircraft profile:
  strict geofence
  altitude envelope
  battery threshold
  GPS quality threshold
  human approval for risky tasks
  mandatory RTL/land fallback
```

Verification should not trust successful tool calls alone. It should compare
final state with mission intent:

- Did the vehicle actually take off?
- Did it reach the target position?
- Did it land?
- Was the target actually detected?
- Was the image saved?
- Did a safety constraint modify or block the action?

## 12. Recommended Implementation Phases

### Phase 0: Ground Station Product Baseline

- Define the system as an AI-augmented ground control station.
- Treat the map/plan view as a first-class mission planning surface.
- Introduce backend-neutral mission item schemas.
- Define the backend ground station service boundary:
  `LinkManager`, `VehicleManager`, `TelemetryManager`, `MissionManager`,
  `CommandManager`, `ParameterManager`, and `SafetyManager`.
- Ensure manual UI actions and Agent actions call the same backend services.

### Phase 1: Execution Backend Decoupling

- Add backend and capability definitions.
- Register AirSim and PX4/MAVLink backends.
- Make `ToolRuntime` select a backend instead of directly creating
  `AirSimController`.
- Dynamically register tools based on backend capabilities.
- Keep current UI and AirSim behavior unchanged.

Initial backend selection can be controlled with:

```powershell
$env:AIRSIM_AGENT_BACKEND = "airsim"
# or
$env:AIRSIM_AGENT_BACKEND = "px4_mavlink"
```

Aliases such as `px4`, `mavlink`, and `sitl` should resolve to
`px4_mavlink`.

### Phase 2: Capability-Aware Planning

- Pass backend capabilities into planner prompts and rule planner.
- Prevent unsupported tool plans before execution.
- Add user-visible backend status.

### Phase 3: Skill Registry

- Add `SkillRegistry`.
- Convert common tasks into skills: navigation, search, tracking, capture,
  return home.
- Let simple tasks bypass LLM where possible.

### Phase 4: Task Router

- Classify commands into L0-L4 difficulty levels.
- Route simple tasks to direct tools/templates.
- Route complex tasks to Agent Loop.
- Route high-risk tasks through supervisor approval.

### Phase 5: Agent Loop / ReAct

- Add structured loop state:
  `Observation -> Decision -> Action -> Result -> ReflectionSummary`.
- Use LLM only at mission-level decision points.
- Support stop conditions, retries, and replanning.
- Store replayable event logs.

### Phase 6: ROS2 And Real Vehicle Readiness

- Add ROS2 backend adapter.
- Add coordinate-frame provider using NED/ENU/FLU conversions.
- Add real vehicle safety profile.
- Add hardware readiness checks and human approval gates.

### Phase 7: Ground Station Mission Layer

- Add `MissionManager` for local mission plans.
- Add mission upload/download/clear/start APIs for PX4 MAVLink.
- Render mission plans on the map.
- Allow Agent to generate or modify mission plans through the same API.

### Phase 8: Full GCS/Agent Integration

- Unify manual mission execution, Agent-generated mission execution, and
  real-time safety monitoring.
- Add mission replay and event logs.
- Support QGC-like multi-vehicle state later.
- Prepare strict real-drone safety profiles and approval gates.

## 13. Near-Term Code Direction

The first code change should create this shape:

```text
ToolRuntime
  -> BackendRegistry
      -> BackendProfile
          -> controller_factory
          -> capabilities
```

This is intentionally small. It gives the Agent execution layer room to support
PX4 and later ROS2 without changing the planner or UI first.

The next design/code step should define the ground station service interfaces:

```text
LinkManager
VehicleManager
TelemetryManager
MissionManager
CommandManager
SafetyManager
```

After that, the middle panel can be converted into a map-first Plan View that
edits a backend-neutral mission model.

## 14. References

- QGroundControl developer guide, communication flow:
  https://docs.qgroundcontrol.com/master/en/qgc-dev-guide/communication_flow.html
- PX4 MAVLink messaging overview:
  https://docs.px4.io/main/en/mavlink/index.html
- MAVLink mission protocol:
  https://mavlink.io/en/services/mission.html
- MAVLink common messages:
  https://mavlink.io/en/messages/common.html
