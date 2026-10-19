"""Provider 工具组：ROS2/provider 桥（冻结期，只读）。

- 注册入口：``register_provider_tools(collector, controller, fmt)``
- 调用方：``src/agent/tool_executor.py`` 的 ``ToolRuntime.ensure_ready()``
  （仅后端具备 ros2_topics 能力时）。
- 整体可裁：后段若不保留，删本文件 + ``manifest.py`` 的三条 + ``tool_cards.py``
  的三张卡片即可（2026-10-15 操作员：先单独留一个文件，方便整体取舍）。
"""
from __future__ import annotations

from typing import Callable

from src.agent.ros_provider_bridge import RosProviderBridgeClient
from src.modules.backends.base import FlightController


def register_provider_tools(
    mcp,
    _controller: FlightController,
    fmt_result: Callable[[dict], str],
) -> None:
    """Register ROS/provider-backed atomic reads and validations."""

    client = RosProviderBridgeClient.from_env()

    @mcp.tool()
    def provider_bridge_health() -> str:
        """Check whether the external provider bridge is reachable."""

        return fmt_result(client.health().to_dict())

    @mcp.tool()
    def provider_obstacle_summary(max_age_sec: float = 1.0, frame: str = "local_ned") -> str:
        """Read the current local obstacle or costmap summary from a provider."""

        result = client.obstacle_summary(
            {
                "max_age_sec": max(0.0, float(max_age_sec)),
                "frame": frame,
            }
        )
        return fmt_result(result.to_dict())

    @mcp.tool()
    def provider_validate_motion(
        forward_m: float = 0.0,
        right_m: float = 0.0,
        up_m: float = 0.0,
        velocity: float = 1.0,
        max_age_sec: float = 1.0,
    ) -> str:
        """Ask a provider whether a proposed body-frame motion is currently safe."""

        result = client.validate_motion(
            {
                "motion": {
                    "forward_m": float(forward_m),
                    "right_m": float(right_m),
                    "up_m": float(up_m),
                    "velocity": max(0.0, float(velocity)),
                },
                "max_age_sec": max(0.0, float(max_age_sec)),
            }
        )
        return fmt_result(result.to_dict())

