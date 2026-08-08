# UAV Agent Harness Design

> 文档状态：Agent Runtime、Skill、Provider、安全和验证原则。当前实现状态及仿真优先的后续任务以 [`system_upgrade_plan.md`](system_upgrade_plan.md) 为准。

This document explains the intended Agent architecture for the PX4/AirSim/ROS
UAV assistant.

## Short Answer

The Agent should not be a large list of hardcoded mission tools. It should be:

```text
Operator command
  -> Orchestration loop
  -> Context and memory selection
  -> Skill or plan selection
  -> Atomic tools and provider calls
  -> Safety gate
  -> Execution
  -> Verification
  -> Memory update
```

The current codebase is moving toward that shape. Skills are still early and can
be paused for now. ROS providers and memory design are the next important bottom
layers.

## Tool Categories

The runtime tool panel should show only Agent-visible atomic/provider tools:

- `atomic`: one direct operation, such as connect, read status, arm, takeoff,
  land, fly to a coordinate, upload mission, capture image, read depth, or run
  one single-frame inference.
- `provider`: an adapter surface for data or services that may come from AirSim,
  PX4, ROS 2, a real vehicle, or a perception/planning node. Providers should
  have stable semantic APIs such as `ImageSource`, `DepthSource`,
  `DetectionSource`, `ObstacleProvider`, `PathPlanner`, and `TrackingProvider`.
- Legacy workflow names are migration records only. They are not registered as
  tools and should not appear in the Agent planning surface.

The Agent planner should see only a small action surface. It should not see raw
ROS topics, raw MAVLink messages, or old hardcoded workflow tools.

## ROS And PX4 Path

Your WSL Ubuntu 24.04 setup with PX4 and Micro-XRCE-DDS-Agent is the right
foundation for ROS 2 integration.

Recommended next architecture:

```text
PX4 SITL or PX4 vehicle
  -> uXRCE-DDS client
  -> Micro-XRCE-DDS-Agent in WSL
  -> ROS 2 graph in WSL
  -> ROS provider bridge
  -> src provider ports
  -> Agent runtime
```

The Windows UI/runtime should not need to import `rclpy` directly. The cleaner
bridge is:

- Run ROS 2 nodes in WSL.
- Expose a small local HTTP/WebSocket/gRPC bridge from WSL.
- Let Windows `src` call that bridge through provider classes.

That keeps ROS dependencies, DDS discovery, and PX4 topic timing inside WSL,
while the Agent keeps a stable provider contract.

The implemented ROS path has two pieces:

- Windows backend: `px4_ros2`, backed by `RosGatewayController`.
- WSL/onboard package: `ros2/airsim_agent_ros`, executable `gateway_node`.

The same HTTP gateway also exposes provider tools:

- `provider_bridge_health`
- `provider_obstacle_summary`
- `provider_validate_motion`

Set `AIRSIM_AGENT_ROS_BRIDGE_URL` or `DRONE_ROS_BRIDGE_URL` when the bridge is
running. Select `AIRSIM_AGENT_BACKEND=px4_ros2` to make normal flight tools
execute through ROS2 instead of MAVLink. See `docs/ros_provider_bridge.md` for
the concrete PX4 + XRCE-DDS + ROS control and obstacle validation path.

## Advanced Tasks Through ROS

Advanced tasks should be provider-backed, not hardcoded MCP tools:

- Path planning: ROS node exposes a bounded `PathPlanner.plan_path()` provider.
- Obstacle avoidance: depth/point cloud/mapping node exposes `DepthSource` or
  local costmap summaries.
- Object detection: YOLO/vision node exposes `DetectionSource.detect()`.
- Object tracking: tracker node exposes `TrackingProvider.start/status/cancel`.
- 3D mapping: SLAM/mapping node exposes map summaries, occupancy, or planning
  queries. The Agent should not consume raw point clouds directly.

The Agent can later call a skill such as `search`, `track_object`, or
`avoid_obstacle`, but the heavy continuous loop belongs in ROS/provider code.

## Orchestration Loop

The main loop should be:

1. Observe: read current backend, telemetry, mission state, active task state,
   recent memory, and available tool/provider cards.
2. Route: decide whether the command is direct, planned, supervised, or agentic.
3. Plan: choose a small sequence or one skill-sized action.
4. Approve: request operator approval for real-vehicle or high-risk actions.
5. Execute: call one atomic tool/provider action at a time.
6. Verify: compare telemetry, task status, and perception evidence with the
   intended outcome.
7. Recover or stop: hover, re-read state, retry bounded operations, or block.
8. Remember: save only useful summaries, not raw noisy data.

Use plan-and-execute for deterministic flight and mission commands. Use ReAct
only when the world must be observed between steps, such as visual search,
uncertain state, perception failure, or recovery.

## Memory Design

The Agent should have five memory scopes.

### 1. Conversation Context

Per-session chat messages. This is for resolving references like "that target"
or "the previous command." The model should receive only a recent window plus a
summary, not the entire chat forever.

### 2. Working State

Short-lived runtime facts:

- active backend and vehicle
- current connection health
- last telemetry
- last task start position
- last image metadata
- active mission/task id

This should expire or be overwritten. It is not long-term knowledge.

### 3. Episodic Task Memory

Auditable records of completed tasks:

- command
- selected route
- tool sequence
- step results
- final telemetry
- verification result
- failure reason

This supports replay, debugging, and explaining why the Agent behaved a certain
way.

### 4. Semantic Lessons

Small durable lessons extracted from repeated task outcomes:

- which backend settings work
- repeated failure patterns
- preferred safe altitudes or speeds when explicitly established
- known camera/provider availability
- common recovery strategies that worked

This should be concise and reviewed. It should not store raw logs or huge tool
outputs.

### 5. Skill Candidates

Repeated successful tool sequences may become a candidate skill, but never an
automatic skill. A human should review and promote it.

Good memory panel grouping:

- What the model can use now
- Persistent mission memory
- Skill candidates
- Replay records

## Context Management

The prompt should be assembled from:

- system safety policy
- current command
- current telemetry/backend state
- small list of available skills/tools/providers
- relevant memory guidance
- recent conversation summary
- active run state

It should not include raw ROS topics, full telemetry streams, full image
payloads, or all old tool results unless specifically needed.

## Prompt Construction

Prompt construction should be layered:

1. System: role, safety, tool-use constraints.
2. Developer/runtime: backend capabilities, real/sim mode, approval policy.
3. Tools: small cards for currently available actions only.
4. Memory: concise guidance and relevant prior failures.
5. User: current command and attachments.

The model should return structured JSON. Free text is for explanations after
execution, not for choosing tools.

## Output Parsing

The runtime must validate every model decision:

- action is in the allowed tool/skill set
- parameters match expected schema
- safety validator accepts the motion envelope
- high-risk real-vehicle actions have approval
- async tools return task ids and terminal status

Invalid output becomes an observation/replan event, not an unsafe action.

## State Management

State belongs outside the LLM:

- `RunState`: current task lifecycle
- `AgentLoop` state: observations, decisions, results
- `ExecutionSupervisor`: pause, emergency stop, approvals
- `ToolRuntime`: backend, connection health, safety validation
- `TaskRunStore`: replay/audit records
- `AgentMemory`: summaries and lessons

The LLM proposes. The runtime owns state.

## Error Handling

Errors should be typed by recovery strategy:

- connection error: reconnect or block
- stale PX4 heartbeat: block control commands
- invalid parameters: correct within safety bounds or ask
- tool timeout: cancel async task and record failure
- perception unavailable: degrade to image capture or report limitation
- safety violation: block and optionally hover

Retries must be bounded. Real-vehicle failures should bias toward hold, land, or
operator approval.

## Guardrails And Safety

Safety should be layered:

- static constraints: altitude, velocity, geofence
- backend constraints: PX4 mode, heartbeat, arm/takeoff state
- runtime constraints: one active task, emergency stop, approval gate
- provider constraints: obstacle/path-planner checks when available
- verification: post-action telemetry and mission state

Raw model reasoning never bypasses the safety manager.

## Verification Loops

Use deterministic checks first:

- takeoff: armed/flying/altitude
- move: position delta or waypoint progress
- land: flying false or landed state
- mission: upload/start/progress state
- connection: heartbeat and stale status

Use inference only for semantic questions:

- target visible
- object class match
- scene description

For ROS tasks, verification should combine provider confidence, telemetry, and
bounded task status.

## Subagent Orchestration

Do not use subagents for direct vehicle control. A single primary runtime should
own flight commands and safety state.

Subagents can be useful later for non-control work:

- offline map analysis
- long log summarization
- code generation for new providers
- dataset inspection
- mission report drafting

They may propose, but the primary runtime must approve and execute.
