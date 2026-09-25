# Project Structure

This project keeps runtime code, verification code, local state, and external
reference code separate. Keep new files in the matching area instead of placing
temporary scripts in the repository root.

## Runtime Code

- `src/` - Python package for controllers, tools, GCS services, agent
  runtime, and the web UI server.
- `src/ui/static/` - browser UI assets.
- `src/tools/` - backend-facing atomic tools used by the agent and UI.
- `src/gcs/` - ground-station service boundary for mission, command,
  link, telemetry, and safety abstractions.

### `src/agent/` layout

`runtime.py` used to be an 8,000-line module holding 212 methods. It is now an
assembly point (~700 lines: dependency injection, lifecycle, mixin wiring), and
the rest lives in domain modules that `AgentRuntime` inherits from:

| Module | Holds |
|---|---|
| `runtime.py` | assembly: DI, `_init_runtime_state`, `_wire_callbacks`, lifecycle |
| `run_state.py` | `RunState` / `ChatMessage` / `RuntimeEvent` / `ToolApprovalRequest` + thresholds |
| `settings_store.py` | settings.json schema, camera settings, QGC-style link presets |
| `session_store.py` | on-disk session layout, trimming, readers |
| `execution.py` | plan-execute path: submit -> plan -> run -> verify, plus the chat path |
| `agent_bridge.py` | Agent-loop callbacks, governed tool entry, result rendering |
| `events.py` | run events, SSE subscribers, message timeline, `state()` snapshot |
| `cancellation.py` | cancel/pause/steer predicates and run ownership |
| `vehicle_api.py` | link/backend switching, vehicle + camera settings, manual overrides |
| `approval_gate.py` | operator approval gate and tool risk levels |
| `guardians.py` | envelope watchdog and tracking assist threads |
| `session_api.py` | session CRUD, attachments, agent instructions |
| `replay_api.py` | replay sessions and the run audit log |
| `gcs_api.py` | mission facade over `src/gcs` |

Two consequences worth knowing when writing tests:

- **Patch where the name is looked up.** A moved function resolves its own
  module-level names, so `_load_settings` now lives in `settings_store`: a test
  that patches it must target `src.agent.settings_store`. Same for
  `session_store.read_session_file` and `session_store.SESSIONS_DIR`.
- `runtime.py` re-exports the names outside callers import from it (declared in
  `__all__`), so `from src.agent.runtime import RunState, _build_connect_params`
  keeps working.


## Verification And Development

- `tests/` - maintained pytest tests. These are the default tests selected by
  `pyproject.toml`.
- `scripts/` - repeatable development and smoke-test scripts.
- `scripts/manual/` - manual probes and experiments. These are not part of the
  normal pytest suite.

## Documentation

- `docs/README.md` - 文档导航、职责和维护规则。
- `docs/system_upgrade_plan.md` - 当前系统基线和升级路线的唯一权威文档。
- `docs/` 其他 Markdown - Agent、ROS2 Gateway、QGC 设置和历史任务的专题文档。

## Runtime Data

- `.airsim_agent/` - local agent memory, secrets, logs, and process metadata.
- `.runtime/` - temporary debug captures, preview frames, and local service logs.
- `captures/` - image captures served by the UI through `/captures/...`.

These directories are local runtime state and should not be treated as source
code.

## External References

- `third_party/` - external reference projects and copied upstream code used for
  study or integration reference. Do not import from this directory in runtime
  code unless the dependency is intentionally vendored and documented.

## Root Files

The repository root should stay small:

- `pyproject.toml` - package metadata and tool configuration.
- `uv.lock` - uv dependency lock file.
- `.gitignore` - ignored local state and generated artifacts.
- `yolov8s-worldv2.pt` - current legacy YOLO weight path used by runtime
  tracking code. Keep it here until model path loading is made configurable and
  the running service is restarted with the new location.
