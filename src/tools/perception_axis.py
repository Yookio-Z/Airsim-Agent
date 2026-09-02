"""Perception axis tools: a thin read-only surface for the Agent.

These tools expose the perception axis state (health / snapshot / events)
regardless of the flight backend. The Agent consumes state; the detection and
tracking algorithms live inside the axis engines and never enter the LLM loop.
"""

from __future__ import annotations

from typing import Any, Callable


def register_perception_axis_tools(
    mcp,
    axis: Any,
    fmt: Callable[[dict], str],
    vlm: Callable[[str, str], dict] | None = None,
    fallback_capture: Callable[[], bytes | None] | None = None,
) -> None:
    """Register axis tools against a ToolCollector (mcp.tool decorator).

    ``vlm`` (optional) is a callable (question, image_base64) -> dict that runs
    a multimodal model over the current perception frame; injected by the
    runtime with the planner when a vision-capable model is configured.
    """

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

    @mcp.tool()
    def inspect_current_frame(question: str) -> str:
        """用多模态模型分析感知轴当前画面并回答问题。

        读取感知轴的最近一帧(标注后的画面)交给视觉模型理解,返回模型对
        画面的描述/判断。适合"画面里有什么""目标是什么颜色"等开放问题;
        若当前没有可用画面(感知离线/未取帧)或模型不支持视觉则返回错误。

        Args:
            question: 针对当前画面的问题(中文即可),例如"画面里有什么目标"
        """
        if vlm is None:
            return fmt({"status": "error", "message": "当前模型不支持图像分析(多模态未启用或未配置视觉模型)"})
        if axis is None:
            return fmt({"status": "error", "message": "perception axis unavailable"})
        try:
            jpeg, dets, _ts = axis.annotated_frame()
        except Exception as exc:
            return fmt({"status": "error", "message": f"画面读取失败: {exc}"})
        if not jpeg and fallback_capture is not None:
            # The perception axis may not have frames yet in some runtimes
            # (UI-process RPC hang); grab one frame directly from the
            # simulator so the visual question still works.
            try:
                jpeg = fallback_capture()
            except Exception as exc:
                return fmt({"status": "error", "message": f"直连取帧失败: {exc}"})
        if not jpeg:
            return fmt({"status": "error", "message": "当前无感知画面(感知轴离线或尚未取到帧)"})
        import base64

        image_b64 = base64.b64encode(jpeg).decode("ascii")
        try:
            answer = vlm(question, image_b64)
        except Exception as exc:
            return fmt({"status": "error", "message": f"视觉模型调用失败: {exc}"})
        payload: dict[str, Any] = {"status": "ok", "question": question, "answer": answer}
        if dets:
            payload["detections"] = dets
        return fmt(payload)