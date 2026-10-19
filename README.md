# AirSim Agent

An LLM-driven UAV agent runtime with multi-backend vehicle control
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
  a single plan-execute route (one LLM plan, then either a fixed tool sequence
  or an observe-decide-act loop with on-demand ReAct), task state/events,
  cancel/pause, and human approval flows.
- **Tool registry** - a manifest-driven tool surface (flight, telemetry,
  perception, VLM, memory, sub-agent) served to the LLM through native
  function calling or JSON-schema prompting, over AirSim / PX4 MAVLink /
  PX4 ROS2 backends.
- **Multi-backend control** - unified ground-station services over AirSim RPC,
  MAVLink (UDP/TCP/serial), and a PX4 ROS2 gateway (HTTP bridge on `:8766`).
- **Autonomy & safety** - an execution supervisor (pause / resume / emergency
  stop / approval gates), a runtime envelope watchdog, and task-level guards
  (progress monitoring, approach vetos). Flight safety is enforced in the
  runtime and bridge layers; perception is "vision model for semantics + depth
  for metric distance" (no object detector).
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
uv run python -m src.ui.server --host 127.0.0.1 --port 8765 --backend px4_mavlink

# Or the short form (same defaults)
uv run python -m src
```

Backend options: `px4_mavlink`, `airsim`, `px4_ros2`. Backends are switched
at runtime from the UI's Links panel (or `POST /api/backend`); the switch
replaces the controller, re-registers the backend's tool set, and reconnects
without restarting the process. There are no external MCP servers — the
in-process agent runtime (`src.agent.AgentRuntime`) is the single execution
surface for both the UI and the LLM planner.

## Project Structure

```text
src/            Python package: agent runtime, autonomy (supervisor only), modules, tools, GCS, web UI
.airsim_agent/  Agent home: SYSTEM.md (system prompt) + skills/ (SKILL.md guidance); runtime data stays local
ros2/           ROS2 package with the PX4 ROS2 gateway node (frozen path: not the active backend)
tests/          Pytest suite (local development)
```

Design, planning and experiment notes live in a local `docs/` folder that is
deliberately **not** tracked by git (see `.gitignore`), so they are not part of
this repository and links to them do not resolve here.

## Notes

- Runtime-local state (`src/data/`, `.runtime/`, `captures/`, model weights,
  logs) is gitignored and regenerated or configured locally.
- `third_party/` contains external reference projects for study only and is not
  imported by runtime code.
