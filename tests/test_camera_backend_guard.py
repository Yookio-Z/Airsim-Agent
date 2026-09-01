"""Camera tools must never run against a PX4 flight backend.

Issue: on the px4 backend the standalone AirSim camera sink issued simGetImage
against a PX4-managed vehicle and crashed AirSim. Camera capture is now gated
to the airsim backend: px4 surfaces no camera tools, no camera cards, and
refuses execution with a clear error pointing at the perception axis.
"""

from __future__ import annotations

import json

from src.agent.tool_executor import ToolRuntime


def _camera_settings() -> dict:
    return {"source": "airsim", "host": "127.0.0.1", "port": 41452}


def _px4_runtime() -> ToolRuntime:
    rt = ToolRuntime(backend_id="px4_mavlink", camera_settings_provider=_camera_settings)
    assert rt.ensure_ready(), rt.init_error
    return rt


def test_px4_backend_exposes_no_camera_tools():
    rt = _px4_runtime()
    names = {s.get("name") for s in rt.list_tools()}
    assert "airsim_take_photo" not in names
    assert "airsim_get_depth_map" not in names


def test_px4_backend_camera_capabilities_are_not_merged():
    rt = _px4_runtime()
    caps = rt._camera_capabilities({"flight_control": True})  # noqa: SLF001
    assert caps.get("image_capture") is False
    assert caps.get("depth_perception") is False
    assert caps.get("image_capture_via") != "airsim_camera_source"


def test_px4_backend_refuses_camera_execution():
    rt = _px4_runtime()
    result = rt.execute("airsim_take_photo", {}, dry_run=False, blocked_by_supervisor=False)
    assert result.ok is False
    assert result.error_code == "BLOCKED"
    payload = json.loads(result.data["message"]) if isinstance(result.data, str) else result.data
    assert "airsim backend" in str(payload)


def test_airsim_backend_still_lists_camera_tools():
    # The airsim backend keeps the camera tool surface (registration may fail
    # without a live controller, but the tool list must include the specs).
    rt = ToolRuntime(backend_id="airsim", camera_settings_provider=_camera_settings)
    assert rt.ensure_ready(), rt.init_error
    names = {s.get("name") for s in rt.list_tools()}
    assert "airsim_take_photo" in names