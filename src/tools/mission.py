"""任务工具组（GCS 专用）：航点任务的上传/下载/清空/启动/进度。

- 注册入口：``register_mission_tools(collector, controller, fmt)``
- 调用方：``src/agent/tool_executor.py`` 的 ``ToolRuntime.ensure_ready()``；
  实际使用者是地面站界面与 GCS 服务（``src/gcs/``），不是模型。

（原文如下）任务工具（GCS 专用）：航点任务的上传/下载/清空/启动/进度。

这五个工具只由地面站界面使用（任务面板、地图与多机任务编排），**不在模型的
决策面里**——它们没有卡片，模型看不到也调不到。Agent 自己飞航线用
`drone_fly_path`；要在界面上知道 Agent 飞到哪儿，看的是地图叠加层画的运行轨迹，
不需要碰 Mission 协议。
"""

from __future__ import annotations

import json
from typing import Callable

from src.modules.backends.base import FlightController


def register_mission_tools(
    mcp,
    controller: FlightController,
    fmt_result: Callable[[dict], str],
) -> None:
    """Register the mission工具 group (upload/download/clear/start/progress)."""
    @mcp.tool()
    def drone_upload_mission(waypoints_json: str, vehicle_name: str = "") -> str:
        """Upload a backend mission (vehicle_name: 空=默认机, 或具体载具名).

        The payload can be an array of MissionItem objects or a MissionPlanDraft
        object with `items` or `waypoints`.
        """
        method = getattr(controller, "upload_mission", None)
        if not callable(method):
            return fmt_result(
                {
                    "status": "error",
                    "backend": controller.backend_name,
                    "message": "mission upload is not supported by this backend",
                }
            )
        try:
            payload = json.loads(waypoints_json)
        except json.JSONDecodeError as exc:
            return fmt_result({"status": "error", "message": f"invalid mission JSON: {exc}"})
        if isinstance(payload, dict):
            waypoints = payload.get("items") or payload.get("waypoints") or []
        else:
            waypoints = payload
        if not isinstance(waypoints, list):
            return fmt_result({"status": "error", "message": "mission items must be a list"})
        clean = [item for item in waypoints if isinstance(item, dict)]
        try:
            result = method(clean, vehicle_name=vehicle_name)
        except TypeError:
            # 旧控制器签名不支持按车上传
            result = method(clean)
        return fmt_result(dict(result))

    @mcp.tool()
    def drone_download_mission() -> str:
        """Download the current backend mission."""
        method = getattr(controller, "download_mission", None)
        if not callable(method):
            return fmt_result(
                {
                    "status": "error",
                    "backend": controller.backend_name,
                    "message": "mission download is not supported by this backend",
                }
            )
        return fmt_result(dict(method()))

    @mcp.tool()
    def drone_clear_mission(vehicle_name: str = "") -> str:
        """Clear the current backend mission (vehicle_name: 空=默认机, 或具体载具名)."""
        method = getattr(controller, "clear_mission", None)
        if not callable(method):
            return fmt_result(
                {
                    "status": "error",
                    "backend": controller.backend_name,
                    "message": "mission clear is not supported by this backend",
                }
            )
        try:
            result = method(vehicle_name=vehicle_name)
        except TypeError:
            result = method()
        return fmt_result(dict(result))

    @mcp.tool()
    def drone_start_mission(vehicle_name: str = "") -> str:
        """Start the mission already uploaded to the backend (vehicle_name: 空=默认机, 或具体载具名)."""
        method = getattr(controller, "start_mission", None)
        if not callable(method):
            return fmt_result(
                {
                    "status": "error",
                    "backend": controller.backend_name,
                    "message": "mission start is not supported by this backend",
                }
            )
        try:
            result = method(vehicle_name=vehicle_name)
        except TypeError:
            result = method()
        return fmt_result(dict(result))

    @mcp.tool()
    def drone_get_mission_progress() -> str:
        """Read current mission progress from the backend."""
        method = getattr(controller, "get_mission_progress", None)
        if not callable(method):
            return fmt_result(
                {
                    "status": "error",
                    "backend": controller.backend_name,
                    "message": "mission progress is not supported by this backend",
                }
            )
        return fmt_result(dict(method()))
