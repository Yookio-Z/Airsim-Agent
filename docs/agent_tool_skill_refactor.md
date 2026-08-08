# Agent Tool And Skill Refactor

> 文档状态：稳定的工具/Skill/ROS Provider 边界约定。当前阶段和 Jetson 分布式 Agent 方案以 [`system_upgrade_plan.md`](system_upgrade_plan.md) 为准。

This document records the new bottom-layer contract for the UAV agent.
For the full harness architecture, memory policy, and ROS/PX4 integration path,
see `docs/agent_harness_design.md`.

## Goal

The agent should feel like an assistant, not a bag of hardcoded AirSim tasks.
The foundation is a stable, small atomic tool layer plus user-editable skills.

## Layer Model

```text
Operator / UI / Agent
  -> GroundStationServices
      -> Link / Vehicle / Telemetry / Mission / Command / Safety managers
          -> Backend adapters
              -> AirSim RPC
              -> PX4 MAVLink
              -> ROS bridge
              -> Real vehicle link

Agent Loop
  -> SkillRegistry
      -> Markdown skill documents
      -> Hard skill executors
          -> Atomic tools / GCS services / providers
```

## Atomic Tool Rule

An atomic tool performs exactly one of these jobs:

- one link operation
- one telemetry read
- one vehicle command
- one mission service call
- one perception read
- one single-frame inference call
- one async task status/cancel operation

Atomic tools must not contain a full mission strategy, target search policy,
formation controller, tracking loop, or ROS topic choreography.

## Workflow Tool Migration

The following legacy workflow tool names are no longer registered as runtime
tools. They remain only in the manifest as migration records so planner cleanup,
history, and docs can point to the intended skill/provider replacement:

- `airsim_search_target` -> `skill:search`
- `airsim_approach_target` -> `skill:approach_target`
- `airsim_track_object` -> `skill:track_object`
- `airsim_check_obstacle` -> `skill:avoid_obstacle`
- `airsim_formation_mission` -> `skill:formation_mission`
- `airsim_precise_formation` -> `skill:formation_mission`
- `airsim_patrol_area` -> `skill:patrol_area`

The manifest in `src/tools/manifest.py` is the source of truth for this
classification.

Default registration no longer imports the old workflow modules, and the old
`search.py`, `tracking.py`, `formation.py`, and `workflow_shims.py` files have
been removed. Agent planning uses skill cards plus atomic/provider tool cards.

## ROS And Real Vehicle Boundary

ROS should not become another pile of agent-facing tools such as
`ros_publish_topic` or `ros_subscribe_topic`.

Instead, ROS adapts into provider surfaces:

- Image topics -> `image_source`
- Depth image or point cloud topics -> `depth_source` or `mapping_provider`
- Detection topics -> `detection_source`
- Tracking actions -> `tracking_provider`
- Path planning services/actions -> `path_planner`
- Navigation actions -> `vehicle_command` or `mission`
- Telemetry topics -> `GroundStationState`

Skills may use these surfaces, but the Agent Loop should not micromanage ROS
topic timing. This keeps AirSim, PX4 SITL, ROS simulation, and real vehicles on
the same conceptual contract.

The first ROS provider tools are intentionally narrow:

- `provider_bridge_health`
- `provider_obstacle_summary`
- `provider_validate_motion`

See `docs/ros_provider_bridge.md` for the quick PX4 + XRCE-DDS + ROS obstacle
validation path.

## Real Vehicle Safety

Real vehicle profiles must set:

- `real_vehicle=True`
- `simulated_vehicle=False`
- `requires_operator_approval=True`

High-risk commands and skills require approval before execution. The safety
manager remains the final gate even when a skill or planner proposes an action.

## Skill Documents

User-editable skill documents live under `skills/*/SKILL.md`.

Current active hard skills:

- `navigation`
- `search`
- `visual_observe`
- `return_home`

Draft skill contracts:

- `avoid_obstacle`
- `approach_target`
- `track_object`
- `patrol_area`
- `formation_mission`

Draft skills are documentation contracts only. They should not be exposed as
executable actions until a hard executor or provider-backed implementation is
registered.

## Settings UI Contract

The Agent settings UI should reflect the same layer model:

- Tools: show Agent-visible atomic/provider tools separately from legacy
  workflow migration records.
- Skills: show all `skills/*/SKILL.md` documents, including draft provider
  contracts, and edit the markdown file directly.
- Memory: separate current conversation context, runtime working state,
  persistent mission lessons/risk events, skill candidates, and replay records.

The UI is not a second source of truth for skill metadata. Saved skill edits
write back to `SKILL.md`, then the registry reloads the markdown documents.
