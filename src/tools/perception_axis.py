"""Perception axis tools: a thin read-only surface for the Agent.

These tools expose the perception axis state (health / snapshot / events)
regardless of the flight backend. The Agent consumes state; the detection and
tracking algorithms live inside the axis engines and never enter the LLM loop.
"""

from __future__ import annotations

from typing import Any, Callable


def register_perception_axis_tools(mcp, axis: Any, fmt: Callable[[dict], str]) -> None:
    """Register axis tools against a ToolCollector (mcp.tool decorator)."""

    @mcp.tool()
    def perception_status(include_snapshot: bool = True, include_events: bool = True, limit: int = 5) -> str:
        """感知轴状态：感知服务健康、当前检测到的目标快照与最近事件。

        只读工具，不产生任何飞行动作。感知离线时同样可调用（用于判断
        为何检测不到目标）。目标状态由底层感知服务（本机进程或 Jetson
        机载）持续维护，Agent 只消费结果、不参与检测循环。

        Args:
            include_snapshot: 是否包含当前目标检测快照，默认 true
            include_events: 是否包含最近感知事件（目标发现/丢失/恢复），默认 true
            limit: 事件条数上限，默认 5
        """
        if axis is None:
            return fmt({"status": "error", "message": "perception axis unavailable", "enabled": False})

        payload: dict[str, Any] = {
            "status": "ok",
            "health": axis.health(),
        }
        if include_snapshot:
            payload["snapshot"] = axis.snapshot()
        if include_events:
            events = axis.pop_events()
            payload["events"] = events[-max(1, int(limit)) :]
        return fmt(payload)