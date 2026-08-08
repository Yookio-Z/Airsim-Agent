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
