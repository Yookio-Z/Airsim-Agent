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
    approach: Callable[[float], dict] | None = None,
) -> None:
    """Register axis tools against a ToolCollector (mcp.tool decorator).

    ``vlm`` (optional) is a callable (question, image_base64) -> dict that runs
    a multimodal model over the current perception frame; injected by the
    runtime with the planner when a vision-capable model is configured.
    ``approach`` (optional) is a callable (step_m) -> dict that performs ONE
    bounded body-frame forward step toward the currently centered/locked
    target. It is the "approved visual approach tool" the loop's safety guard
    looks for; without it the Agent must not fly toward a 2D image target.
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
    def perception_start(target_class: str = "") -> str:
        """启动/确保感知轴的目标检测服务在后台运行。

        感知服务持续在后台做 YOLO 检测（含跟踪保持），Agent 只消费其状态。
        若服务已在线则返回 already_online；需要切换检测类别时传 target_class。

        Args:
            target_class: 可选，目标类别（如 car/person/truck）；留空保持当前配置
        """
        if axis is None:
            return fmt({"status": "error", "message": "perception axis unavailable", "enabled": False})
        started = False
        # 切换检测类别：写入 profile 后 start() 会用新类别重建引擎
        if target_class and getattr(axis, "profile", None) is not None:
            try:
                axis.profile.target_class = str(target_class).strip()
            except Exception:
                pass
        if hasattr(axis, "start"):
            try:
                started = bool(axis.start())
            except Exception as exc:
                return fmt({"status": "error", "message": f"感知启动失败: {exc}"})
        online = bool(getattr(axis, "is_online", lambda: False)())
        return fmt({
            "status": "ok",
            "started": started,
            "online": online,
            "message": "检测服务运行中" if online else "检测服务已启动，正在初始化（模型加载可能需要十几秒）",
            "health": axis.health() if hasattr(axis, "health") else {},
        })

    @mcp.tool()
    def perception_stop() -> str:
        """停止后台目标检测服务（释放模型与相机通道）。"""
        if axis is None:
            return fmt({"status": "error", "message": "perception axis unavailable", "enabled": False})
        try:
            axis.stop()
        except Exception as exc:
            return fmt({"status": "error", "message": f"感知停止失败: {exc}"})
        return fmt({"status": "ok", "online": False, "message": "检测服务已停止"})

    @mcp.tool()
    def airsim_detect_objects(target_class: str = "", confidence: float = 0.25) -> str:
        """单帧目标检测：返回感知轴最近一帧里检测到的目标（类别/置信度/像素框）。

        感知轴在底层持续做 YOLO 检测，本工具只读取最新一帧的检测结果，
        不产生飞行动作。目标不在画面里时返回空的 detections 列表。

        Args:
            target_class: 可选，过滤类别（如 car/person/truck）；留空返回全部
            confidence: 可选，最低置信度过滤，默认 0.25
        """
        if axis is None:
            return fmt({"status": "error", "message": "perception axis unavailable", "enabled": False})
        try:
            snap = axis.snapshot()
        except Exception as exc:
            return fmt({"status": "error", "message": f"感知快照读取失败: {exc}"})
        targets = list(snap.get("targets") or [])
        try:
            requested = float(confidence)
        except (TypeError, ValueError):
            requested = 0.25
        # 复检阈值不得比探测器本身更严格：夜间/远距离小目标的原始置信度常常
        # 只有 0.1 左右，调用方习惯性传 0.25 会把真实检出全部滤掉（表现为
        # "画面里明明有车却检测不到"）。这里以探测器灵敏度为上限收口。
        min_conf = min(requested, 0.08)
        wanted = str(target_class or "").strip().lower()
        detections = [
            t
            for t in targets
            if float(t.get("confidence") or 0.0) >= min_conf
            and (not wanted or wanted in str(t.get("class") or "").lower())
        ]
        return fmt(
            {
                "status": "ok",
                "target_class": wanted,
                "confidence": min_conf,
                "detections": detections,
                "count": len(detections),
                "total_frames": snap.get("total_frames", 0),
                "source": snap.get("source", ""),
            }
        )

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

    @mcp.tool()
    def drone_approach_target(step_m: float = 2.0) -> str:
        """向当前锁定/居中的目标做一次有界的前向抵近（视觉伺服式靠近）。

        单步、有界（1~3m）地向机体前方推进，用于"目标已在画面中央"时缩短
        距离；每次调用后必须重新检测/确认再决定下一步，避免盲飞。目标不在
        画面中央、或感知无目标时拒绝执行——先转向对准再靠近。

        Args:
            step_m: 本次前向推进距离（米），会被限制在 1~3m
        """
        if approach is None:
            return fmt({"status": "error", "message": "当前运行时不支持视觉抵近(未注入 approach 回调)"})
        try:
            data = approach(float(step_m))
        except Exception as exc:
            return fmt({"status": "error", "message": f"抵近执行失败: {exc}"})
        return fmt(data)