# AirSim Agent

An LLM-driven UAV agent runtime with MCP servers, multi-backend vehicle control
(AirSim / PX4 MAVLink / PX4 + ROS2), and a web ground-control station (GCS).

## Overview

This project is a smart ground station rather than a chat-controlled simulation
demo. The web UI and the LLM agent share the same Ground Station services and
mission data model, and every high-risk action passes through a safety layer and
human approval gate before reaching the flight controller.

```text
UI / Ground Agent
  -> Ground Station Services (Link / Vehicle / Telemetry / Mission / Command / Safety)
      -> AirSim / PX4 MAVLink / PX4 ROS2 / Real Vehicle
```

The LLM is used for task-level understanding and decision-making only; it never
enters the high-frequency flight control loop.

## Features

- **Agent runtime** - LLM-driven task understanding, capability-aware planning,
  L0-L4 task routing, task state/events, cancel/pause, and human approval flows.
- **MCP servers** - Model Context Protocol servers for AirSim, PX4/MAVLink, and
  PX4/ROS2 backends.
- **Multi-backend control** - unified ground-station services over AirSim RPC,
  MAVLink (UDP/TCP/serial), and a PX4 ROS2 gateway (HTTP bridge on `:8766`).
- **Autonomy & safety** - supervisor, safety arbiter, policy engine, obstacle
  avoidance, visual servoing, target lock, and YOLO-based perception.
- **Web ground station** - MapLibre-based UI with mission management,
  telemetry, captures, approval gates, and a SKILL.md editor.
- **Skills system** - editable `SKILL.md` documents loaded at runtime as LLM
  guidance (not hard-coded tools).
- **ROS2 gateway** - PX4 state/control/obstacle providers, async tasks, and
  offboard watchdog via an HTTP-to-ROS bridge, with a roadmap for Jetson
  edge-side agents.

## Quick Start (Windows)

Requires Python >= 3.10 and [uv](https://docs.astral.sh/uv/).

```powershell
# Create the environment and install dependencies
uv sync

# Start the web ground station (default backend: px4_mavlink)
.\scripts\start_ui.ps1 -Backend px4_mavlink

# Or start it directly
uv run python -m src.ui.server --host 127.0.0.1 --port 8765 --backend px4_mavlink
```

Backend options: `px4_mavlink`, `airsim`, `px4_ros2`. Backends are switched
at runtime from the UI's Links panel (or `POST /api/backend`); the switch
replaces the controller, re-registers the backend's tool set, and reconnects
without restarting the process. There are no external MCP servers — the
in-process agent runtime (`src.agent.AgentRuntime`) is the single execution
surface for both the UI and the LLM planner.

## Project Structure

```text
src/          Python package: agent runtime, autonomy, modules, tools, GCS, web UI
ros2/         ROS2 package with the PX4 ROS2 gateway node
skills/       Runtime-loaded SKILL.md guidance documents
scripts/      Development launchers and smoke-test scripts
docs/         Architecture, design, and upgrade-plan documentation
tests/        Pytest suite (local development)
```

See [`docs/project_structure.md`](docs/project_structure.md) for the detailed
layout and [`docs/README.md`](docs/README.md) for the documentation index.

## Documentation

- [`docs/system_upgrade_plan.md`](docs/system_upgrade_plan.md) - current system
  baseline and upgrade roadmap (authoritative).
- [`docs/ros_provider_bridge.md`](docs/ros_provider_bridge.md) - PX4 ROS2
  gateway operation and ROS provider integration.
- [`docs/agent_harness_design.md`](docs/agent_harness_design.md) - agent loop,
  skills, providers, state, safety, and verification principles.

## Notes

- Runtime-local state (`src/data/`, `.runtime/`, `captures/`, model weights,
  logs) is gitignored and regenerated or configured locally.
- `third_party/` contains external reference projects for study only and is not
  imported by runtime code.
