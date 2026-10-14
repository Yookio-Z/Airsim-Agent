"""Perception tools: a thin read-only surface for the Agent.

The product has **no object detection**: YOLO was removed by design decision.
Semantics come from the vision model (``inspect_current_frame``), metric
distance from the depth image (``airsim_get_depth_map``), and world position
from a depth-point projection (``airsim_locate_point``). Nothing in this module
moves the vehicle.
"""

from __future__ import annotations

from typing import Any, Callable

# 相机健康里对 Agent 有意义的字段。检测相关字段（detect_fps / detector /
# detection_age_s / detection_stale）一律不暴露：它们描述的是一个已经不在
# 产品里的子系统，模型看到只会增加困惑。
_CAMERA_HEALTH_KEYS = (
    "online",
    "fps",
    "update_fps_target",
    "capture_age_s",
    "latency_ms",
    "last_update_ts",
    "total_frames",
    "error",
    "source",
    "view",
)


def register_perception_axis_tools(
    mcp,
    axis: Any,
    fmt: Callable[[dict], str],
    vlm: Callable[[str, str], dict] | None = None,
    fallback_capture: Callable[[], bytes | None] | None = None,
    locate: Callable[..., dict] | None = None,
) -> None:
    """Register camera tools against a ToolCollector (mcp.tool decorator).

    ``vlm`` (optional) is a callable (question, image_base64) -> dict that runs
    a multimodal model over the current perception frame; injected by the
    runtime with the planner when a vision-capable model is configured.
    ``fallback_capture`` (optional) grabs one frame directly from the simulator
    when the axis has no cached frame yet.
    ``locate`` (optional) is a callable (x_norm, y_norm, vehicle_name) -> dict
    that projects one normalised image point to a metric distance and world
    position through the depth image and the drone pose.
    """

    @mcp.tool()
    def perception_status(include_events: bool = True, limit: int = 5) -> str:
        """相机/画面健康：感知轴是否在线、帧率、画面多久没更新、当前取景。

        只读工具，不产生任何飞行动作，也**不含目标检测信息**（本产品已无目标
        检测：语义问题问 `inspect_current_frame`，距离问 `airsim_get_depth_map`）。

        判断"相机是不是卡住了"看 `capture_age_s`：它持续增大就是画面不再更新，
        此时任何基于画面的判断（包括视觉模型的回答）都不可信——先悬停并报告
        链路问题，不要继续飞。

        `view` 是感知轴此刻取景的相机与图像类型。感知轴只有一个视角，它就是
        本 Agent 正在看的画面；取景归操作员，Agent 不切相机，需要看别的方向
        时自己转向（`drone_rotate_to`）或移动。

        Args:
            include_events: 是否包含最近感知事件（相机/链路类），默认 true
            limit: 事件条数上限，默认 5
        """
        if axis is None:
            return fmt({"status": "error", "message": "perception axis unavailable", "enabled": False})
        try:
            health = axis.health() or {}
        except Exception as exc:
            return fmt({"status": "error", "message": f"感知状态读取失败: {exc}"})
        payload: dict[str, Any] = {
            "status": "ok",
            "enabled": bool(getattr(axis, "enabled", False)),
        }
        payload.update({key: health[key] for key in _CAMERA_HEALTH_KEYS if key in health})
        # 画面尺寸：把归一化坐标换成像素（或反过来）时要用。
        try:
            snap = axis.snapshot() or {}
        except Exception:
            snap = {}
        for key in ("frame_width", "frame_height"):
            if snap.get(key):
                payload[key] = snap[key]
        if include_events:
            try:
                events = axis.pop_events()
            except Exception:
                events = []
            payload["events"] = list(events)[-max(1, int(limit)) :]
        return fmt(payload)

    @mcp.tool()
    def inspect_current_frame(question: str) -> str:
        """用当前配置的模型分析感知轴当前画面并回答问题。

        读取感知轴的最近一帧交给模型理解,返回模型对画面的描述/判断。适合
        "画面里有什么""目标是什么颜色""它朝哪边""车牌上写着什么"等语义问题,
        以及"画面里有没有<目标>?在哪个位置?"这类确认问题。

        规划与看图是同一个模型:当前配的模型必须支持图像输入,否则本工具直接
        返回错误,不会另找模型代答。

        返回的 `answer.target_candidates[]` 里每个候选带 `bbox_hint`
        ({x, y, w, h}，归一化 0~1 的中心与宽高，**都是粗值**)：把它交给
        `airsim_get_depth_map` 读距离、交给 `airsim_locate_point` 换世界坐标，
        就能把"我看见了"变成可执行的几何量。一次调用只问一次,同一位置不要
        重复问——回答含糊意味着距离或角度不对,应移动或转向后再看。

        Args:
            question: 针对当前画面的问题(中文即可),例如"画面里那辆浅蓝色汽车在什么位置?"
        """
        if vlm is None:
            return fmt({
                "status": "error",
                "message": "未配置可用的模型，无法分析图像。请在模型设置里配置一个支持图像的模型。",
            })
        if axis is None:
            return fmt({"status": "error", "message": "perception axis unavailable"})
        try:
            jpeg, _dets, _ts = axis.annotated_frame()
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
            # 模型侧的错误(未配 key / 不支持图像 / 调用失败)已经带完整说明,
            # 这里原样透出,不要再包一层"视觉模型"——那会让人以为系统另有一个
            # 视觉模型被调挂了。
            return fmt({"status": "error", "message": str(exc)})
        return fmt({"status": "ok", "question": question, "answer": answer})

    @mcp.tool()
    def airsim_locate_point(
        x: float,
        y: float,
        w: float = 0.0,
        h: float = 0.0,
        vehicle_name: str = "",
    ) -> str:
        """把一个归一化图像点换算成公制距离与世界坐标（NED）。

        用法：视觉模型（inspect_current_frame）给出目标的归一化位置
        （`answer.target_candidates[].bbox_hint` 的 x/y，或按画面比例自己量的
        x/y）后调用本工具。它读该点的深度、结合无人机位姿做针孔投影，返回：

        - `distance_m` / `depth_m`：目标点到相机的公制距离；
        - `world_pos`：NED 世界坐标（米）——需要朝目标飞（drone_fly_to）、
          记住目标位置、或有别处坐标要比较时用它；
        - `valid=false` 时 `message` 说明原因（天空/过远/云台稳定/无深度），
          此时该点不可用于规划移动。

        只读工具，不做任何飞行动作。点的准确性由你负责：点错位置得到的是
        那个位置的距离和坐标，不是目标的——先确认目标在画面里的位置再调用。

        Args:
            x: 归一化横向坐标 0~1（0=最左，1=最右）
            y: 归一化纵向坐标 0~1（0=最上，1=最下）
            w: 可选：目标归一化宽度（视觉模型 bbox_hint 的 w）。**强烈建议传**——
               传了之后本工具会在框内/框外各取 4 点做深度交叉验证，回答"这个框里
               是不是一个独立目标"（框内外深度几乎一样 = 你画的是一整片连续表面，
               比如地面/墙面，不是目标），并给出按深度换算的米制尺寸供你把关。
            h: 可选：目标归一化高度（同上）。
            vehicle_name: 无人机名称，留空=默认机
        """
        if locate is None:
            return fmt({
                "status": "error",
                "message": "当前运行时不支持点位定位（未注入深度投影，通常是 AirSim 后端未连接）",
            })
        try:
            data = locate(
                float(x), float(y), vehicle_name=str(vehicle_name or ""),
                hint_w=float(w or 0.0), hint_h=float(h or 0.0),
            )
        except Exception as exc:
            return fmt({"status": "error", "message": f"点位定位失败: {exc}"})
        payload = dict(data or {})
        payload.setdefault("status", "ok" if payload.get("valid") else "error")
        payload["query_point"] = {"x": float(x), "y": float(y)}
        return fmt(payload)
