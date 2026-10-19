"""Camera tools must never run against a PX4 flight backend.

Issue: on the px4 backend the standalone AirSim camera sink issued simGetImage
against a PX4-managed vehicle and crashed AirSim. Camera capture is now gated
to the airsim backend: px4 surfaces no camera tools, no camera cards, and no
camera-source execution path at all.

The old per-source camera tools (airsim_take_photo / airsim_get_depth_map) are
gone: seeing is inspect_current_frame, geometry is locate_image_point, and
both come from the perception-axis surface — which is keyed to the camera
chain, not the flight backend.
"""

from __future__ import annotations

from src.agent.tool_executor import ToolRuntime


def _camera_settings() -> dict:
    return {"source": "airsim", "host": "127.0.0.1", "port": 41452}


def _px4_runtime() -> ToolRuntime:
    rt = ToolRuntime(backend_id="px4_mavlink", camera_settings_provider=_camera_settings)
    assert rt.ensure_ready(), rt.init_error
    return rt


class _StubAxis:
    enabled = True

    def health(self):
        return {"enabled": True, "online": False}

    def snapshot(self):
        return {"targets": [], "primary": None}

    def pop_events(self):
        return []


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


def test_deleted_camera_tools_are_unknown_now():
    """相机源工具层随深度工具一起删除：旧名字再来就是 unknown tool，
    而不再是含糊的"needs a camera source"拒绝。"""
    rt = _px4_runtime()
    result = rt.execute("airsim_get_depth_map", {}, dry_run=False, blocked_by_supervisor=False)
    assert result.ok is False
    assert result.error_code == "UNKNOWN_TOOL"


def test_airsim_backend_geometry_tools_come_from_the_perception_axis():
    # 几何/看图工具随感知轴注册（与飞行后端解耦）；否则整个几何面不存在。
    rt = ToolRuntime(
        backend_id="airsim",
        camera_settings_provider=_camera_settings,
        perception_axis=_StubAxis(),
    )
    assert rt.ensure_ready(), rt.init_error
    names = {s.get("name") for s in rt.list_tools()}
    assert "locate_image_point" in names
    assert "inspect_current_frame" in names
    assert "airsim_get_depth_map" not in names


def test_real_vehicle_hides_perception_tools_and_capabilities():
    """真机（real_vehicle）只暴露基本控制工具：感知/相机类工具不注册、卡片不出现、
    能力全部置否。真机上没有仿真相机链路，"Agent 有相机能力"必须是假命题。"""
    rt = ToolRuntime(
        backend_id="px4_mavlink",
        camera_settings_provider=_camera_settings,
        perception_axis=_StubAxis(),
    )
    rt._real_vehicle = True
    assert rt.ensure_ready(), rt.init_error
    names = {s.get("name") for s in rt.list_tools()}
    cards = {c["name"] for c in rt.list_tool_cards()}
    for gone in ("inspect_current_frame", "perception_status", "locate_image_point"):
        assert gone not in names, f"{gone} 不该出现在真机工具面"
        assert gone not in cards, f"{gone} 不该出现在真机卡片面"
    # 基本控制工具照常（PX4 对接的那套）
    assert {"drone_takeoff", "drone_land", "drone_fly_to", "drone_get_status"} <= names
    caps = rt._camera_capabilities({})
    assert caps["image_capture"] is False
    assert caps["depth_perception"] is False
    assert caps["image_capture_via"] == "none"


def test_px4_sitl_keeps_the_perception_surface():
    """SITL（非真机）保留感知工具面：仿真相机链路本来就存在。"""
    rt = ToolRuntime(
        backend_id="px4_mavlink",
        camera_settings_provider=_camera_settings,
        perception_axis=_StubAxis(),
    )
    assert rt.ensure_ready(), rt.init_error
    names = {s.get("name") for s in rt.list_tools()}
    assert {"inspect_current_frame", "perception_status", "locate_image_point"} <= names
