"""Local execution facade over the existing MCP tool registrations."""

from __future__ import annotations

import inspect
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .backends import BackendProfile, BackendRegistry, create_builtin_backend_registry
from .llm_protocol import validate_json_schema
from src.config import config
from src.modules.formation import FLIGHT_ACTIONS, FormationController
from src.modules.safety_validator import FlightConstraint, SafetyValidator
from src.tools.manifest import manifest_metadata, list_tool_manifest

logger = logging.getLogger(__name__)


# Output shape checks for the highest-value tools. Schemas carry no `required`
# fields on purpose: present fields are type-checked, missing fields are left to
# the normalizers, so validation acts as a diagnostic net rather than a gate.
TOOL_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "drone_get_status": {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "connected": {"type": "boolean"},
            "flying": {"type": "boolean"},
            "armed": {"type": "boolean"},
            "has_collided": {"type": "boolean"},
            "position_ned": {"type": "object"},
            "velocity_ned": {"type": "object"},
            "attitude_rad": {"type": "object"},
        },
    },
    "airsim_task_status": {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "task_id": {"type": "string"},
            "terminal": {"type": "boolean"},
        },
    },
    "formation_command": {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "mode": {"type": "string"},
            "stable": {"type": "boolean"},
            "drones": {"type": "array"},
            "progress": {"type": "object"},
        },
    },
}


@dataclass
class ToolCallResult:
    tool: str
    params: dict[str, Any]
    ok: bool
    data: dict[str, Any]
    started_at: float
    finished_at: float
    safety: dict[str, Any] | None = None
    terminal: bool = True
    task_id: str = ""
    # Structured failure classification: "" | BLOCKED | SAFETY_BLOCKED |
    # NOT_CONNECTED | LINK_STALE | TIMEOUT | CONNECTION | INVALID_PARAMS |
    # UNKNOWN_TOOL | INVALID_ASYNC_RESPONSE | CANCELLED | TOOL_ERROR |
    # RUNTIME_UNAVAILABLE | INVALID_TOOL_OUTPUT
    error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "params": self.params,
            "ok": self.ok,
            "data": self.data,
            "duration_ms": round((self.finished_at - self.started_at) * 1000, 1),
            "safety": self.safety,
            "terminal": self.terminal,
            "task_id": self.task_id,
            "error_code": self.error_code,
            "outcome": "succeeded" if self.ok and self.terminal else ("accepted" if self.ok else "failed"),
        }


@dataclass
class ToolSpec:
    name: str
    category: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


def _rtsp_open_timeout(value: Any) -> float:
    """相机面板的 timeout_sec → RTSP 握手超时（秒）。

    面板的超时是给"抓一帧"用的（3~120s），拿它当握手超时会让链路故障时
    白等很久；这里夹到 3~20s，既够慢链路握手，又能在断链时快速失败。
    """
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 10.0
    if seconds <= 0:
        return 10.0
    return max(3.0, min(20.0, seconds))


class ToolCollector:
    """Small FastMCP-compatible collector used to reuse @mcp.tool functions."""

    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., str]] = {}

    def tool(self):
        def decorator(fn: Callable[..., str]) -> Callable[..., str]:
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _agent_subtask_tool_card() -> dict[str, Any]:
    from .tool_cards import ToolCard

    return ToolCard(
        name="agent_subtask",
        purpose="Delegate an open-ended interpretation subtask (multi-target confirmation, ambiguous goal analysis) to a bounded sub-agent that returns a structured report.",
        when_to_use="When the goal needs several rounds of focused analysis that would consume the parent loop's step budget, or when an independent check of an ambiguous requirement is useful.",
        inputs={
            "goal": "one-sentence focused subtask for the sub-agent",
            "constraints": "optional constraints (altitude limits, target list, no-fly hints)",
            "max_steps": "sub-agent step budget (default 6)",
            "model_id": "optional model id for the sub-agent",
        },
        outputs="structured report {status, summary, steps, findings}.",
        risk="low",
        kind="atomic",
        execution_mode="immediate",
    ).to_dict()


def _agent_memory_tool_cards() -> list[dict[str, Any]]:
    """Agent-level memory tool cards appended to the regular tool set so the
    LLM can actively store and recall durable facts (agenticros-style)."""
    from .tool_cards import ToolCard

    return [
        ToolCard(
            name="memory_recall",
            purpose="Recall previously stored facts, missions, lessons, and run transcripts from long-term memory.",
            when_to_use="When the operator refers to an earlier task, fact, or lesson, or when past experience can inform the current decision.",
            inputs={"query": "natural language search text", "limit": "max results (default 5)"},
            outputs="matching memory records with relevance scores.",
            risk="low",
            kind="atomic",
        ).to_dict(),
        ToolCard(
            name="memory_remember",
            purpose="Store a durable fact about the mission or environment for future runs.",
            when_to_use="When the operator states a persistent fact (target area, vehicle id, learned preference) that future tasks should know.",
            inputs={"key": "short fact key", "value": "fact content", "tags": "optional comma-separated tags"},
            outputs="stored confirmation.",
            risk="low",
            kind="atomic",
        ).to_dict(),
    ]


def parse_no_fly_zones(raw: str | list[Any] | None) -> list[dict[str, float]]:
    """把配置里的禁飞区解析成 FlightConstraint 需要的圆列表。

    接受 JSON 字符串（便于用 DRONE_SAFETY_NO_FLY_ZONES_JSON 环境变量配置）或
    已经是列表的形式。非法条目跳过而不是抛异常：安全配置写坏了应该让服务起
    得来并留下警告，而不是整个地面站启动失败。
    """
    if raw is None or raw == "":
        return []
    payload: Any = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("no_fly_zones_unparsable", extra={"raw": raw[:200]})
            return []
    if isinstance(payload, dict):
        payload = payload.get("zones") or payload.get("circles") or []
    if not isinstance(payload, list):
        logger.warning("no_fly_zones_wrong_shape", extra={"type": type(payload).__name__})
        return []
    zones: list[dict[str, float]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        x = _finite_or_none(item.get("x", 0.0))
        y = _finite_or_none(item.get("y", 0.0))
        radius = _finite_or_none(item.get("radius"))
        if x is None or y is None or radius is None or radius <= 0.0:
            logger.warning("no_fly_zone_entry_skipped", extra={"index": index, "entry": item})
            continue
        zone: dict[str, Any] = {"x": x, "y": y, "radius": radius}
        name = str(item.get("name") or "").strip()
        if name:
            zone["name"] = name
        zones.append(zone)
    return zones


def _non_finite_params(params: dict[str, Any] | None) -> list[str]:
    """顶层参数里不是有限数值的键名（NaN / ±Inf，含 "nan"/"inf" 这类字符串）。

    每个校验分支都用 float() 取参，而 float("nan") 和 JSON 的 NaN 字面量都能
    转换成功；NaN 之后与任何阈值比较都是 False，于是逐条躲过范围检查。字符串
    "nan"/"inf" 同理也要拦。真正的字符串参数（串口 URL、航点 JSON）解析会失败，
    不会因为这道扫描被误判。
    """
    bad: list[str] = []
    for key, value in (params or {}).items():
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            if not math.isfinite(value):
                bad.append(str(key))
            continue
        if isinstance(value, str):
            try:
                number = float(value.strip())
            except (TypeError, ValueError):
                continue
            if not math.isfinite(number):
                bad.append(str(key))
    return bad


def _finite_or_none(value: Any) -> float | None:
    """float(value)，非数字或非有限值时返回 None。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _gps_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两个经纬度之间的近似水平距离（米）。

    用等距圆柱投影（与 mavlink_controller._gps_offset_m 同一套近似）：在围栏
    量级（几十米~几公里）误差远小于围栏本身，够用来判断"这个航点是不是飞出去
    了"，而且不引入新的依赖。
    """
    meters_per_lat = 111_320.0
    meters_per_lon = meters_per_lat * math.cos(math.radians((lat1 + lat2) / 2.0))
    north = (lat2 - lat1) * meters_per_lat
    east = (lon2 - lon1) * meters_per_lon
    return math.hypot(north, east)


class ToolRuntime:
    """Executes backend tools locally with safety validation."""

    READ_ONLY_TOOLS = {
        "drone_get_status",
        "drone_get_firmware_info",
        "drone_get_parameters",
        "drone_list_vehicles",
        "drone_download_mission",
        "drone_get_mission_progress",
        "airsim_take_photo",
        "airsim_get_sensors",
        "airsim_get_depth_map",
        "airsim_detect_objects",
        "inspect_current_frame",
        "provider_bridge_health",
        "provider_obstacle_summary",
        "provider_validate_motion",
        "perception_status",
        "perception_start",
        "perception_stop",
    }

    CONTROL_TOOLS = {
        "drone_arm",
        "drone_disarm",
        "drone_takeoff",
        "drone_dispatch_takeoff",
        "drone_land",
        "drone_hover",
        "drone_fly_to",
        "drone_fly_velocity",
        "drone_move_relative",
        "drone_approach_target",
        "drone_fly_path",
        "drone_dispatch_path",
        "drone_dispatch_land",
        "drone_dispatch_return_land",
        "drone_upload_mission",
        "drone_clear_mission",
        "drone_start_mission",
        "drone_rotate_to",
        "drone_set_mode",
    }

    # Idempotent read-only tools that may be retried once on a transient
    # TIMEOUT. Flight tools are deliberately excluded: a control call may have
    # partially executed before the timeout, and blind retries could double a
    # move; link-loss is handled by the reconnect path instead.
    _RETRYABLE_READ_TOOLS = READ_ONLY_TOOLS | {"airsim_task_status", "airsim_task_cancel"}

    CONNECTION_ERROR_MARKERS = (
        "not connected",
        "connection refused",
        "connect timed out",
        "connection timed out",
        "connect timeout",
        "unreachable",
        "no backend",
        "broken pipe",
        "refused",
        # 中文侧只保留"链路级"标记：通用的 超时 / 拒绝 会把业务超时（例如
        # "降落超时未确认"）也判成断链，进而对飞行链路做一次 disconnect + 重连。
        # 英文侧同理——曾用 "timeout"/"rpc"/"airsim" 这类宽词，一次相机超时就会
        # 重连飞控链路。改窄之后两端行为一致（见 tests 里的标记用例）。
        "\u65e0\u6cd5\u8fde\u63a5",
        "\u8fde\u63a5\u5931\u8d25",
        "\u672a\u8fde\u63a5",
        "\u8fde\u63a5\u88ab\u62d2",
    )
    CAMERA_SOURCE_TOOLS = {"airsim_take_photo", "airsim_get_depth_map"}

    def __init__(
        self,
        backend_id: str | None = None,
        backend_registry: BackendRegistry | None = None,
        camera_settings_provider: Callable[[], dict[str, Any]] | None = None,
        perception_axis: Any | None = None,
        vlm_provider: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.backend_registry = backend_registry or create_builtin_backend_registry()
        self.backend_id = self.backend_registry.resolve_id(backend_id)
        self.backend_profile: BackendProfile | None = None
        self.controller: Any | None = None
        self.collector: ToolCollector | None = None
        self.camera_settings_provider = camera_settings_provider
        self.perception_axis = perception_axis
        self.vlm_provider = vlm_provider
        self.camera_controller: Any | None = None
        self.camera_collector: ToolCollector | None = None
        self.camera_key = ""
        self.camera_error = ""
        self._camera_lock = threading.RLock()
        self.available = False
        self.init_error = ""
        self._lock = threading.RLock()
        self._last_status_snapshot: dict[str, Any] = {}
        self._last_connect_params: dict[str, Any] = {}
        self._real_vehicle = False
        # Multi-vehicle formation controller (AirSim backend only), created
        # lazily the first time a formation command runs.
        self._formation: FormationController | None = None
        self._formation_stop_provider: Callable[[], bool] | None = None
        # External stop/cancel signal for blocking single-vehicle flight
        # commands (emergency stop / task cancel preemption).
        self._flight_stop_provider: Callable[[], bool] | None = None
        # 飞控指令串行闸门：所有会写 OFFBOARD/位置/速度设定值的执行路径
        # （Agent 飞行工具、算法级视觉伺服）共享同一把锁，避免两路线程同时
        # 向 PX4 推不同目标点导致失控（历史上表现为突然俯冲/乱转/掉高）。
        self._control_gate = threading.RLock()
        self.safety = SafetyValidator(
            FlightConstraint(
                max_altitude=float(config.safety_max_altitude_m),
                min_altitude=float(config.safety_min_altitude_m),
                max_velocity=float(config.safety_max_velocity_mps),
                max_distance_from_home=float(config.safety_geofence_m),
                no_fly_zones=parse_no_fly_zones(config.safety_no_fly_zones_json),
            )
        )

    def ensure_ready(self) -> bool:
        if self.available and self.collector is not None:
            return True
        try:
            from src.tools.core import register_core_tools

            self.backend_profile = self.backend_registry.require(self.backend_id)
            capabilities = self.backend_profile.capabilities
            # 感知能力用"合并后"的能力：px4 后端的原始能力里 image_capture 为
            # False，但感知轴/相机源照样提供图像能力。若这里用原始能力判断，
            # 图像工具会在 px4 链路上被整体跳过（表现为 unknown tool）。
            merged_capabilities = self._camera_capabilities(capabilities.to_dict())
            self.controller = self.backend_profile.create_controller()
            if hasattr(self.controller, "set_stop_provider"):
                self.controller.set_stop_provider(self._flight_stop_provider)
            self.collector = ToolCollector()

            def fmt(data: dict[str, Any]) -> str:
                return json.dumps(data, ensure_ascii=False, indent=2)

            register_core_tools(self.collector, self.controller, fmt)

            # Optional tool groups: failures must not block core tools.
            # 图像工具跟着"相机源"走，不跟飞行后端绑死：
            #   - airsim 后端：相机与飞控同源，直接用飞控控制器注册；
            #   - 其他后端（px4_mavlink/px4_ros2）：用设置里选定的相机源
            #     （AirSim 仿真 / RTSP 图传 / 本机相机）自己的工具集并入。
            # 这样无论飞控走哪条链路，LLM 拿到的拍照工具都从当前相机源取图。
            if merged_capabilities.get("image_capture") or merged_capabilities.get("object_detection"):
                try:
                    if self.backend_id == "airsim":
                        from src.tools.perception import register_perception_tools

                        register_perception_tools(self.collector, self.controller, fmt)
                    else:
                        cam_collector, cam_err = self._ensure_camera_tools()
                        if cam_collector is not None:
                            for tool_name, tool_fn in cam_collector.tools.items():
                                self.collector.tools[tool_name] = self._adapt_camera_tool(tool_fn)
                        elif cam_err:
                            import logging

                            logging.getLogger(__name__).warning(f"camera source tools skipped: {cam_err}")
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(f"perception tools skipped: {exc}")
            if merged_capabilities.get("depth_perception"):
                try:
                    from src.modules.airsim_controller import AirSimController
                    from src.tools.vision import register_vision_tools

                    depth_controller = self.controller if self.backend_id == "airsim" else self.camera_controller
                    if isinstance(depth_controller, AirSimController):
                        # 深度图只来自 AirSim 仿真相机；RTSP/本机相机没有深度。
                        register_vision_tools(self.collector, depth_controller, fmt)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(f"vision tools skipped: {exc}")
            if capabilities.ros2_topics:
                try:
                    from src.tools.providers import register_provider_tools

                    register_provider_tools(self.collector, self.controller, fmt)
                except Exception as exc:
                    import logging

                    logging.getLogger(__name__).warning(f"provider tools skipped: {exc}")

            # Perception axis tools: always register when configured. inspect_current_frame
            # is useful even when the axis has no frames yet (its fallback
            # pulls one frame directly from the camera), so we do not gate on
            # axis.is_online -- the tool itself reports an explanatory error.
            axis_configured = self.perception_axis is not None and getattr(self.perception_axis, "enabled", False)
            if axis_configured or getattr(self, "vlm_provider", None) is not None:
                # Also reached with an unconfigured axis when the active model can
                # see images, so inspect_current_frame still serves the UI camera
                # panel fallback path.
                try:
                    from src.tools.perception_axis import register_perception_axis_tools

                    register_perception_axis_tools(
                        self.collector,
                        self.perception_axis,
                        fmt,
                        vlm=self.vlm_provider,
                        fallback_capture=self._capture_current_frame_jpeg,
                        approach=self.approach_target_step,
                    )
                except Exception as exc:
                    import logging

                    logging.getLogger(__name__).warning(f"perception axis tools skipped: {exc}")

            self._ensure_formation_tools()

            self.available = True
            self.init_error = ""
            return True
        except Exception as e:
            self.available = False
            self.init_error = str(e)
            return False

    def _camera_settings(self) -> dict[str, Any]:
        if self.camera_settings_provider is None:
            return {}
        try:
            settings = self.camera_settings_provider() or {}
            return settings if isinstance(settings, dict) else {}
        except Exception as exc:
            self.camera_error = str(exc)
            return {}

    def _camera_source_enabled(self) -> bool:
        settings = self._camera_settings()
        return str(settings.get("source") or "").lower() in {"airsim", "rtsp", "local"}

    def _perception_camera_controller(self) -> Any | None:
        """感知轴正在用的那个相机控制器（AirSim）。

        必须复用它而不是另开一个连接：AirSim 的 RPC 是单线程的，感知轴本身以
        4~5 FPS 持续取帧，另开一条连接来读深度只会在竞争里超时（实测深度一直
        是 None、步长因此吃满上限）。共用同一个控制器时请求会在它的执行锁上
        排队，而不是互相饿死。
        """
        axis = getattr(self, "perception_axis", None)
        engine = getattr(axis, "_engine", None)
        source = getattr(engine, "_frame_source", None)
        provider = getattr(source, "_controller_provider", None)
        if not callable(provider):
            return None
        try:
            from src.modules.perception_airsim_adapter import _resolve_controller

            return _resolve_controller(provider)
        except Exception:
            return None

    def _perception_health(self) -> dict[str, Any]:
        """感知轴健康：相机链路是否还在出帧。

        相机链路与飞控链路是分开的（AirSim 卡住时 MAVLink 心跳照旧），所以
        "飞控在线"不等于"我看到的画面是新的"。这份健康信息随 status_snapshot
        进入 Agent 的每轮观察，模型才有机会发现画面已经停止更新；Agent Loop
        还会据此在画面长时间不更新时停下任务，避免基于陈旧画面继续动作。
        """
        axis = getattr(self, "perception_axis", None)
        if axis is None:
            return {"enabled": False}
        try:
            snapshot = axis.snapshot() or {}
            health = axis.health() if hasattr(axis, "health") else {}
        except Exception as exc:  # noqa: BLE001 — 健康检查本身不能抛出
            return {"enabled": bool(getattr(axis, "enabled", False)), "error": str(exc)}
        return {
            "enabled": bool(getattr(axis, "enabled", False)),
            "fps": health.get("fps", snapshot.get("fps")),
            # detect_fps 只有 health() 里才有：snapshot() 从不带这个字段，读它会让
            # "检测中"永远显示不出来（实测检测在跑、徽标却一直显示未检测）。
            "detect_fps": health.get("detect_fps"),
            "capture_age_s": snapshot.get("capture_age_s"),
            "detection_age_s": snapshot.get("detection_age_s"),
            "detection_stale": snapshot.get("detection_stale"),
            "targets": len(snapshot.get("targets") or []),
            "locked": bool(snapshot.get("primary")),
        }

    def _camera_tools_ready(self) -> bool:
        """相机工具是否真的注册成功了（而不是"配置里写了相机源"）。

        配置写了 airsim/rtsp 不等于相机源连得上：连接失败时相机工具集为空，
        此时若仍把 airsim_take_photo / airsim_get_depth_map 声明为可用，Agent
        就会规划出根本无法执行的步骤——实测任务正是失败在 "unknown tool" 上。
        判断只看已经注册好的相机收集器，不做任何阻塞式连接。
        """
        collector = getattr(self, "camera_collector", None)
        if collector is None:
            return False
        return bool(self.CAMERA_SOURCE_TOOLS & set(getattr(collector, "tools", {}) or {}))

    def _camera_capabilities(self, capabilities: dict[str, Any]) -> dict[str, Any]:
        merged = dict(capabilities or {})
        settings = self._camera_settings()
        source = str(settings.get("source") or "").lower()
        axis = getattr(self, "perception_axis", None)
        axis_enabled = bool(axis is not None and getattr(axis, "enabled", False))
        # 感知能力跟随感知轴，不跟随飞行后端。px4_mavlink/px4_ros2 下感知轴
        # 仍然提供图像采集/目标检测/搜索/追踪（图像源是与飞控解耦的独立
        # 相机通道），飞行后端只决定飞行指令通道。只有当感知轴未启用时，
        # 才回落到"该后端没有感知能力"。
        if self.backend_id != "airsim":
            merged["camera_source"] = source
            merged["image_capture_via"] = "perception_axis" if axis_enabled else "none"
            merged["image_capture"] = axis_enabled
            merged["object_detection"] = axis_enabled
            merged["target_search"] = axis_enabled
            merged["target_tracking"] = axis_enabled
            # 深度图依赖 AirSim 仿真相机通道；真机形态下由机载感知提供，
            # 这里仅在仿真相机源可用时置位。
            merged["depth_perception"] = bool(axis_enabled and source == "airsim")
            if axis_enabled and source == "airsim":
                merged["camera_host"] = settings.get("host", "127.0.0.1")
                merged["camera_port"] = settings.get("port", 41452)
            return merged
        if source == "airsim":
            merged["image_capture"] = True
            merged["depth_perception"] = True
            merged["camera_source"] = "airsim"
            merged["camera_host"] = settings.get("host", "127.0.0.1")
            merged["camera_port"] = settings.get("port", 41452)
            merged["image_capture_via"] = "airsim_camera_source"
        elif source == "rtsp":
            merged["image_capture"] = True
            merged["depth_perception"] = False
            merged["camera_source"] = "rtsp"
            merged["camera_url"] = settings.get("url", "")
            merged["image_capture_via"] = "rtsp_stream"
        elif source == "local":
            merged["image_capture"] = True
            merged["depth_perception"] = False
            merged["camera_source"] = "local"
            merged["camera_index"] = settings.get("camera_name", "0")
            merged["image_capture_via"] = "local_camera"
        return merged

    def _camera_tool_spec(self, name: str) -> ToolSpec | None:
        settings = self._camera_settings()
        if name == "airsim_take_photo":
            return ToolSpec(
                name="airsim_take_photo",
                category="perception",
                description="Capture a PNG frame from the configured AirSim camera source.",
                parameters={
                    "camera_name": {"default": settings.get("camera_name", "0"), "annotation": "str"},
                    "vehicle_name": {"default": settings.get("vehicle_name", ""), "annotation": "str"},
                    "image_type": {"default": settings.get("image_type", "scene"), "annotation": "str"},
                    "auto_save": {"default": settings.get("auto_save", False), "annotation": "bool"},
                    "timeout_sec": {"default": settings.get("timeout_sec", 30.0), "annotation": "float"},
                },
            )
        if name == "airsim_get_depth_map":
            return ToolSpec(
                name="airsim_get_depth_map",
                category="perception",
                description="Read a depth image from the configured AirSim camera source.",
                parameters={
                    "camera_name": {"default": settings.get("camera_name", "0"), "annotation": "str"},
                    "vehicle_name": {"default": settings.get("vehicle_name", ""), "annotation": "str"},
                    "return_vis": {"default": False, "annotation": "bool"},
                    "query_points": {"default": "", "annotation": "str"},
                },
            )
        return None

    def _ensure_camera_tools(self) -> tuple[ToolCollector | None, str]:
        settings = self._camera_settings()
        source = str(settings.get("source") or "").lower()
        if source == "rtsp":
            return self._ensure_rtsp_camera_tools(settings)
        if source == "local":
            return self._ensure_local_camera_tools(settings)
        if source != "airsim":
            return None, "camera source is not AirSim"

        host = str(settings.get("host") or "127.0.0.1")
        try:
            port = int(settings.get("port") or 41452)
        except (TypeError, ValueError):
            port = 41452
        key = f"airsim:{host}:{port}"

        if self.camera_key != key:
            if self.camera_controller is not None:
                try:
                    self.camera_controller.disconnect()
                except Exception:
                    pass
            self.camera_controller = None
            self.camera_collector = None
            self.camera_key = key

        if self.camera_controller is not None and self.camera_collector is not None:
            if bool(getattr(self.camera_controller, "is_connected", False)):
                return self.camera_collector, ""
            self.camera_controller = None
            self.camera_collector = None

        try:
            from src.modules.airsim_controller import AirSimController
            from src.tools.perception import register_perception_tools
            from src.tools.vision import register_vision_tools

            controller = AirSimController(ip=host, port=port)
            info = controller.connect(ip=host, port=port)
            if not info.connected:
                details = getattr(info, "details", {}) or {}
                message = details.get("message") if isinstance(details, dict) else ""
                self.camera_error = message or "AirSim camera source is not connected"
                return None, self.camera_error

            collector = ToolCollector()

            def fmt(data: dict[str, Any]) -> str:
                return json.dumps(data, ensure_ascii=False, indent=2)

            register_perception_tools(collector, controller, fmt)
            register_vision_tools(collector, controller, fmt)
            if "airsim_take_photo" not in collector.tools:
                self.camera_error = "AirSim camera tool registration failed"
                controller.disconnect()
                return None, self.camera_error

            self.camera_controller = controller
            self.camera_collector = collector
            self.camera_error = ""
            return collector, ""
        except Exception as exc:
            self.camera_error = str(exc)
            return None, self.camera_error

    def _ensure_rtsp_camera_tools(self, settings: dict[str, Any]) -> tuple[ToolCollector | None, str]:
        """Camera tool set for a real onboard camera pushed over RTSP."""
        url = str(settings.get("url") or settings.get("rtsp_url") or "").strip()
        if not url:
            self.camera_error = "rtsp camera source requires a stream URL"
            return None, self.camera_error
        rtsp_transport = str(settings.get("transport") or "").strip().lower()
        rtsp_timeout = _rtsp_open_timeout(settings.get("timeout_sec"))
        key = f"rtsp:{url}|{rtsp_transport}"

        if self.camera_key != key:
            if self.camera_controller is not None:
                try:
                    self.camera_controller.disconnect()
                except Exception:
                    pass
            self.camera_controller = None
            self.camera_collector = None
            self.camera_key = key

        if self.camera_controller is not None and self.camera_collector is not None:
            if bool(getattr(self.camera_controller, "is_connected", False)):
                return self.camera_collector, ""
            self.camera_controller = None
            self.camera_collector = None

        try:
            from src.modules.rtsp_camera_controller import RtspCameraController

            controller = RtspCameraController(
                url,
                transport=rtsp_transport,
                open_timeout_sec=rtsp_timeout,
            )
            info = controller.connect()
            if not info.connected:
                self.camera_error = controller.last_error or "rtsp camera source is not connected"
                return None, self.camera_error

            collector = ToolCollector()
            self._register_rtsp_camera_tools(collector, controller)
            self.camera_controller = controller
            self.camera_collector = collector
            self.camera_error = ""
            return collector, ""
        except Exception as exc:
            self.camera_error = str(exc)
            return None, self.camera_error

    def _ensure_local_camera_tools(self, settings: dict[str, Any]) -> tuple[ToolCollector | None, str]:
        """Camera tool set for a local webcam / USB camera (pipeline testing)."""
        try:
            index = int(settings.get("camera_name") or 0)
        except (TypeError, ValueError):
            index = 0
        key = f"local:{index}"

        if self.camera_key != key:
            if self.camera_controller is not None:
                try:
                    self.camera_controller.disconnect()
                except Exception:
                    pass
            self.camera_controller = None
            self.camera_collector = None
            self.camera_key = key

        if self.camera_controller is not None and self.camera_collector is not None:
            if bool(getattr(self.camera_controller, "is_connected", False)):
                return self.camera_collector, ""
            self.camera_controller = None
            self.camera_collector = None

        try:
            from src.modules.rtsp_camera_controller import LocalCameraController

            controller = LocalCameraController(index)
            info = controller.connect()
            if not info.connected:
                self.camera_error = controller.last_error or "local camera is not available"
                return None, self.camera_error

            collector = ToolCollector()
            self._register_rtsp_camera_tools(collector, controller)
            self.camera_controller = controller
            self.camera_collector = collector
            self.camera_error = ""
            return collector, ""
        except Exception as exc:
            self.camera_error = str(exc)
            return None, self.camera_error

    def _ensure_formation_tools(self) -> tuple[ToolCollector | None, str]:
        """Register the formation_command tool on formation-capable backends.

        Currently AirSim (SimpleFlight multirotor) and PX4 MAVLink (single link
        with multiple systems, via the duck-typed velocity-control protocol).
        The "at least 2 vehicles" requirement is enforced at call time
        (connections may not exist yet during registration).
        """
        if self.backend_id not in {"airsim", "px4_mavlink"} or self.controller is None:
            return None, "formation requires the airsim or px4_mavlink backend"
        external = getattr(self.controller, "_uses_external_px4_controller", None)
        if callable(external):
            try:
                if external(""):
                    return None, "airsim backend uses an external PX4 flight controller; formation unavailable"
            except Exception:
                pass
        collector = self.collector
        if collector is None:
            return None, "tool collector unavailable"
        if "formation_command" in collector.tools:
            return collector, ""

        @collector.tool()
        def formation_command(
            action: str = "status",
            formation_type: str = "line",
            spacing: float = 5.0,
            altitude: float = 10.0,
            x: float = 0.0,
            y: float = 0.0,
            z: float | None = None,
            angle_deg: float = 0.0,
            scale_factor: float = 1.0,
            area_shape: str = "rectangle",
            area_width: float = 100.0,
            area_height: float = 100.0,
            area_radius: float = 25.0,
            area_x: float = 0.0,
            area_y: float = 0.0,
            area_altitude: float = 10.0,
            resolution: float = 5.0,
            partition: str = "balanced",
            path_algo: str = "boustrophedon",
            coverage_speed: float = 3.0,
            vehicle_ids: str = "",
        ) -> str:
            """Multi-vehicle formation and coverage control (AirSim backend).

            The deterministic 10Hz control loop maintains the formation; this
            tool only issues high-level intents. Poll action=status until
            stable=true.
            """
            fc = self._formation_controller()
            if fc is None:
                return json.dumps(
                    {"status": "error", "message": "formation controller unavailable for this backend"},
                    ensure_ascii=False,
                )
            if action in {"takeoff", "coverage_start"} and len(fc._list_vehicles()) < 2:
                return json.dumps(
                    {
                        "status": "error",
                        "message": "formation requires at least 2 vehicles on the airsim or px4_mavlink backend",
                        "vehicles": fc._list_vehicles(),
                    },
                    ensure_ascii=False,
                )
            if action == "status":
                return json.dumps(fc.status(), ensure_ascii=False)
            if action == "set_drones":
                ids = [part.strip() for part in str(vehicle_ids or "").split(",") if part.strip()]
                return json.dumps(fc.set_drones(ids), ensure_ascii=False)
            if action == "set_formation":
                return json.dumps(fc.set_formation(formation_type, spacing), ensure_ascii=False)
            if action == "takeoff":
                return json.dumps(fc.takeoff(altitude), ensure_ascii=False)
            if action == "move_center":
                return json.dumps(fc.move_center(x, y, z), ensure_ascii=False)
            if action == "rotate":
                return json.dumps(fc.rotate(angle_deg), ensure_ascii=False)
            if action == "scale":
                return json.dumps(fc.scale(scale_factor), ensure_ascii=False)
            if action == "coverage_plan":
                area: dict[str, Any] = {"shape": area_shape, "altitude": area_altitude}
                if area_shape == "circle":
                    area.update({"radius": area_radius, "x": area_x, "y": area_y})
                else:
                    area.update({"width": area_width, "height": area_height, "x": area_x, "y": area_y})
                return json.dumps(fc.coverage_plan(area, resolution, partition, path_algo, coverage_speed), ensure_ascii=False)
            if action == "coverage_start":
                return json.dumps(fc.coverage_start(), ensure_ascii=False)
            if action == "hover_all":
                return json.dumps(fc.hover_all(), ensure_ascii=False)
            if action == "land_all":
                return json.dumps(fc.land_all(), ensure_ascii=False)
            if action == "stop":
                fc.shutdown("operator_stop")
                return json.dumps(fc.status(), ensure_ascii=False)
            return json.dumps(
                {
                    "status": "error",
                    "message": f"unknown action: {action}",
                    "valid_actions": [
                        "set_drones", "set_formation", "takeoff", "move_center", "rotate", "scale",
                        "coverage_plan", "coverage_start", "hover_all", "land_all", "stop", "status",
                    ],
                },
                ensure_ascii=False,
            )

        return collector, ""

    def _formation_controller(self) -> FormationController | None:
        if self._formation is None and self.controller is not None:
            self._formation = FormationController(self.controller)
            self._formation.should_stop = self._formation_stop_provider
        return self._formation

    def formation_active(self) -> bool:
        """True while a formation/coverage control loop is commanding vehicles."""
        formation = getattr(self, "_formation", None)
        return bool(formation and formation.mode != "idle")

    def formation_shutdown(self, reason: str) -> bool:
        """Hover all formation drones and stop the control thread.

        Called on run end, backend switches, and emergency stop. Returns True
        when a mission was actually active.
        """
        formation = getattr(self, "_formation", None)
        if formation is None:
            return False
        return formation.shutdown(reason)

    def formation_set_stop_provider(self, provider: Callable[[], bool] | None) -> None:
        self._formation_stop_provider = provider
        if self._formation is not None:
            self._formation.should_stop = provider

    def set_flight_stop_provider(self, provider: Callable[[], bool] | None) -> None:
        """Wire an external stop/cancel signal into blocking flight commands.

        Emergency stop / task cancel must preempt an in-flight single-vehicle
        move (the blocking loops poll this provider and exit cleanly)."""
        self._flight_stop_provider = provider
        controller = self.controller
        if controller is not None and hasattr(controller, "set_stop_provider"):
            try:
                controller.set_stop_provider(provider)
            except Exception:
                pass

    @staticmethod
    def _register_rtsp_camera_tools(collector: ToolCollector, controller: Any) -> None:
        """Single photo tool for RTSP sources (real cameras: scene only)."""

        @collector.tool()
        def airsim_take_photo(
            camera_name: str = "0",
            image_type: str = "scene",
            vehicle_name: str = "",
            auto_save: bool = False,
            timeout_sec: float = 30.0,
        ) -> str:
            """Capture a JPEG frame from the RTSP camera source."""
            import base64

            raw = controller.capture_image(
                camera_name=camera_name,
                image_type=image_type,
                vehicle_name=vehicle_name,
                timeout=float(timeout_sec or 30.0),
            )
            if raw is None:
                return json.dumps(
                    {
                        "status": "error",
                        "backend": controller.backend_name,
                        "message": controller.last_error or "frame capture failed",
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "status": "ok",
                    "backend": controller.backend_name,
                    "image_base64": base64.b64encode(raw).decode("ascii"),
                    "format": "jpeg",
                    "source": "rtsp",
                    "message": "frame captured from RTSP camera",
                },
                ensure_ascii=False,
            )

    @staticmethod
    def _adapt_camera_tool(fn: Callable[..., str]) -> Callable[..., str]:
        """按函数签名过滤入参：不同相机源（AirSim/RTSP/本机）的拍照工具
        参数集不同（例如 RTSP 版没有 verify_target_class）。LLM 按统一卡片
        传参时，多出来的键不应变成 TypeError。"""
        import functools
        import inspect

        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return fn
        accepted = set(sig.parameters)

        @functools.wraps(fn)
        def _wrapped(**kwargs: Any) -> str:
            return fn(**{k: v for k, v in kwargs.items() if k in accepted})

        return _wrapped

    def _camera_params(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        settings = self._camera_settings()
        merged = dict(params or {})
        if name == "airsim_take_photo":
            merged["camera_name"] = str(merged.get("camera_name") or settings.get("camera_name") or "0")
            merged["vehicle_name"] = str(merged.get("vehicle_name") or settings.get("vehicle_name") or "")
            merged["image_type"] = str(merged.get("image_type") or settings.get("image_type") or "scene")
            if "auto_save" not in merged:
                merged["auto_save"] = bool(settings.get("auto_save", False))
            if "timeout_sec" not in merged:
                merged["timeout_sec"] = float(settings.get("timeout_sec") or 30.0)
        elif name == "airsim_get_depth_map":
            merged["camera_name"] = str(merged.get("camera_name") or settings.get("camera_name") or "0")
            merged["vehicle_name"] = str(merged.get("vehicle_name") or settings.get("vehicle_name") or "")
        return merged

    def _execute_camera_tool(self, name: str, params: dict[str, Any], started: float) -> ToolCallResult:
        if name not in self.CAMERA_SOURCE_TOOLS:
            return ToolCallResult(
                name,
                params,
                False,
                {"status": "error", "message": f"unknown camera tool: {name}"},
                started,
                time.time(),
            )

        collector, error = self._ensure_camera_tools()
        if collector is None:
            return ToolCallResult(
                name,
                params,
                False,
                {
                    "status": "error",
                    "message": f"AirSim camera source unavailable: {error or 'unknown error'}",
                    "camera_source": self._camera_settings(),
                },
                started,
                time.time(),
            )

        fn = collector.tools.get(name)
        if not fn:
            return ToolCallResult(
                name,
                params,
                False,
                {"status": "error", "message": f"camera tool not registered: {name}"},
                started,
                time.time(),
            )

        safe_params = self._camera_params(name, params)
        try:
            safety = self.validate(name, safe_params)
            with self._camera_lock:
                raw = fn(**safe_params)
            data = json.loads(raw) if isinstance(raw, str) else {"status": "ok", "result": raw}
            status = str(data.get("status", "ok")).strip().lower()
            ok = status not in {"error", "blocked", "failed", "cancelled", "canceled"}
            return ToolCallResult(
                name,
                safe_params,
                ok,
                data,
                started,
                time.time(),
                safety=safety,
            )
        except Exception as exc:
            return ToolCallResult(
                name,
                safe_params,
                False,
                {"status": "error", "message": str(exc), "camera_source": self._camera_settings()},
                started,
                time.time(),
            )

    # Real devices (local webcam / RTSP) must NOT stay opened when no preview
    # request arrives for a while — otherwise e.g. a laptop webcam "in use" LED
    # stays lit forever even though nobody is viewing it. Release idle preview
    # controllers after this many seconds of no use.
    PREVIEW_IDLE_RELEASE_SEC = 3.0

    def _ensure_preview_controller(self, params: dict[str, Any] | None = None) -> tuple[Any | None, str]:
        """Build (or reuse) a camera controller for a single UI preview frame.

        Unlike ``_ensure_camera_tools`` (which keeps ONE global controller for the
        agent's configured source), this honours an optional per-request
        ``source``/``url`` override so multiple viewer windows can display
        *different* sources at the same time — e.g. one window on the AirSim
        drone, another on a real drone's RTSP stream for digital-twin work.

        Controllers are cached by a source-specific key and reused across
        requests to avoid reconnecting on every frame.
        """
        raw = dict(params or {})
        settings = self._camera_settings()
        source = str(raw.get("source") or settings.get("source") or "airsim").strip().lower()
        if source not in {"airsim", "rtsp", "local"}:
            return None, f"unsupported camera source: {source}"

        if source == "rtsp":
            url = str(raw.get("url") or settings.get("url") or settings.get("rtsp_url") or "").strip()
            if not url:
                return None, "rtsp camera source requires a stream URL"
            # 传输协议进缓存键：改了 TCP/UDP 要与旧连接区分开，否则会一直
            # 复用按旧协议打开的 capture。
            rtsp_transport = str(raw.get("transport") or settings.get("transport") or "").strip().lower()
            rtsp_timeout = _rtsp_open_timeout(raw.get("timeout_sec") or settings.get("timeout_sec"))
            key = f"rtsp:{url}|{rtsp_transport}"
        elif source == "local":
            try:
                index = int(raw.get("camera_name") or settings.get("camera_name") or 0)
            except (TypeError, ValueError):
                index = 0
            key = f"local:{index}"
        else:
            host = str(raw.get("host") or settings.get("host") or "127.0.0.1")
            try:
                port = int(raw.get("port") or settings.get("port") or 41452)
            except (TypeError, ValueError):
                port = 41452
            key = f"airsim:{host}:{port}"

        cache = getattr(self, "_preview_controllers", None)
        if cache is None:
            cache = {}
            self._preview_controllers = cache

        with self._camera_lock:
            controller = cache.get(key)
            if controller is not None and bool(getattr(controller, "is_connected", False)):
                controller._last_preview_used = time.time()
                return controller, ""
            if controller is not None:
                try:
                    controller.disconnect()
                except Exception:
                    pass
            try:
                if source == "airsim":
                    from src.modules.airsim_controller import AirSimController

                    controller = AirSimController(ip=host, port=port)
                    info = controller.connect(ip=host, port=port)
                elif source == "rtsp":
                    from src.modules.rtsp_camera_controller import RtspCameraController

                    controller = RtspCameraController(
                        url,
                        transport=rtsp_transport,
                        open_timeout_sec=rtsp_timeout,
                    )
                    info = controller.connect()
                else:
                    from src.modules.rtsp_camera_controller import LocalCameraController

                    controller = LocalCameraController(index)
                    info = controller.connect()
            except Exception as exc:
                return None, str(exc)
            if not getattr(info, "connected", False):
                details = getattr(info, "details", {}) or {}
                message = details.get("message") if isinstance(details, dict) else ""
                return None, (message or f"{source} camera source is not connected")
            cache[key] = controller
            controller._last_preview_used = time.time()
            return controller, ""

    # 抵近回退窗口：显示层的新鲜度 TTL 只有 1.5s 左右，检测循环偶发慢一帧
    # （YOLO 在 1280 分辨率下单帧就可能 300ms+）时 primary 会瞬间为空，抵近被
    # 反复拒绝、模型只能原地回读状态。这里允许用"最近一次真实检测"发起一次
    # 有界抵近——预测框不算，飞机只朝真实看到过的方向走。
    _APPROACH_FALLBACK_WINDOW_S = 4.0

    @classmethod
    def _fallback_approach_target(cls, snap: dict[str, Any]) -> dict[str, Any] | None:
        candidates: list[tuple[float, float, dict[str, Any]]] = []
        for target in snap.get("targets") or []:
            if not isinstance(target, dict) or target.get("predicted"):
                continue
            try:
                age = float(target.get("age_s"))
            except (TypeError, ValueError):
                continue
            if age < 0 or age > cls._APPROACH_FALLBACK_WINDOW_S:
                continue
            bbox = target.get("bbox") or []
            if len(bbox) != 4:
                continue
            try:
                area = max(0.0, (float(bbox[2]) - float(bbox[0])) * (float(bbox[3]) - float(bbox[1])))
            except (TypeError, ValueError):
                continue
            candidates.append((age, -area, target))
        if not candidates:
            return None
        # 最新的一帧检测优先；同一帧里取最大的框（近处目标更大）
        candidates.sort(key=lambda item: (item[0], item[1]))
        return dict(candidates[0][2])

    def _target_distance_from_depth(
        self, target: dict[str, Any], snap: dict[str, Any], vehicle_name: str = ""
    ) -> float | None:
        """用深度图估算目标距离（米）；拿不到返回 None。

        AirSim 的 DepthPlanar 是相机坐标下的深度值，取目标框中心一小块区域的中
        位数比取单点稳。深度图与彩图分辨率不同（256x144 vs 800x600），坐标按快照
        里的画幅比例映射。距离只用来决定"这一步该走多远"，失败不影响抵近可行性。
        """
        controller = self._perception_camera_controller()
        if controller is None:
            return None
        profile = getattr(getattr(self, "perception_axis", None), "profile", None)
        camera_name = str(getattr(profile, "camera_name", "") or "CameraImage")
        bbox = [float(v) for v in (target.get("bbox") or [])]
        if len(bbox) != 4:
            return None
        try:
            import numpy as np

            from src.modules.perception_airsim_adapter import get_depth_image

            depth = get_depth_image(controller, camera_name, vehicle_name, timeout_sec=3.0)
            if depth is None:
                return None
            height, width = int(depth.shape[0]), int(depth.shape[1])
            frame_w = float(snap.get("frame_width") or 0.0) or float(width)
            frame_h = float(snap.get("frame_height") or 0.0) or float(height)
            cx = (bbox[0] + bbox[2]) / 2.0
            cy = (bbox[1] + bbox[3]) / 2.0
            dx = int(max(0.0, min(width - 1.0, cx / max(frame_w, 1.0) * width)))
            dy = int(max(0.0, min(height - 1.0, cy / max(frame_h, 1.0) * height)))
            patch = depth[max(0, dy - 2) : dy + 3, max(0, dx - 2) : dx + 3]
            values = patch[np.isfinite(patch) & (patch > 0.1)]
            if values.size == 0:
                return None
            return float(np.median(values))
        except Exception:  # noqa: BLE001 — 距离估计失败不能阻断抵近
            return None

    def approach_target_step(
        self,
        step_m: float = 12.0,
        vehicle_name: str = "",
        standoff_m: float = 0.0,
        min_fill: float = 0.25,
    ) -> dict[str, Any]:
        """向画面中央锁定的目标做一次有界前向抵近（视觉伺服式靠近）。

        只走机体前方；要求感知轴当前有目标且横向足够居中，否则拒绝（先转向
        对准）。**"够近"由目标在画面里的大小决定**（``min_fill``，默认占画面
        高度 25%），不是某个写死的米数——目标高度占比达标就返回 reached，
        让调用方改用视觉分析去看细节。前进量用深度图换算（表观大小与距离成
        反比），一步走到阈值距离而不冲过头；没有深度就退到 ``step_m`` 上限。

        Args:
            step_m: 单步最大前进距离（米），1~12
            standoff_m: 可选的绝对距离约束（米），0 = 不启用
            min_fill: 目标高度占画面比例达到多少算"够近可辨认"，默认 0.25
        """
        axis = self.perception_axis
        if axis is None or not getattr(axis, "enabled", False):
            return {"status": "error", "message": "perception axis unavailable"}
        controller = self.controller
        if controller is None or not bool(getattr(controller, "is_connected", False)):
            return {"status": "error", "message": "flight controller not connected"}
        try:
            snap = axis.snapshot() or {}
        except Exception as exc:
            return {"status": "error", "message": f"感知快照读取失败: {exc}"}
        primary = snap.get("primary") or self._fallback_approach_target(snap)
        if not primary:
            return {"status": "error", "message": "画面中当前没有锁定目标，先检测/对准再抵近"}
        bbox = primary.get("bbox") or []
        if len(bbox) != 4:
            return {"status": "error", "message": "目标像素框不可用，无法安全抵近"}
        frame_w = float(snap.get("frame_width") or 0.0)
        if frame_w <= 0.0:
            # The axis has not reported a frame size yet (just started); decode
            # the annotated JPEG as a one-off fallback.
            frame_w = 640.0
            try:
                annotated = axis.annotated_frame()
                jpeg = annotated[0] if isinstance(annotated, (tuple, list)) else None
                if jpeg:
                    import cv2
                    import numpy as np

                    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_UNCHANGED)
                    if img is not None:
                        frame_w = float(img.shape[1])
            except Exception:
                pass
        cx = (float(bbox[0]) + float(bbox[2])) / 2.0
        ex = (cx - frame_w / 2.0) / (frame_w / 2.0)
        if abs(ex) > 0.35:
            # 目标偏得多：先自己对准再前进。像素偏差→偏航角是几何换算，模型算不
            # 出来；但"要不要靠近"才是模型的判断，所以对准属于工具的职责，不该
            # 变成要模型自己拼的额外步骤。
            aligned = self._align_to_target(vehicle_name=vehicle_name)
            if abs(float(aligned.get("ex") or 1.0)) > 0.35:
                return {
                    "status": "error",
                    "message": "目标偏离画面中央且自动对准未收敛，请先手动转向对准再抵近",
                    "ex": aligned.get("ex", round(ex, 3)),
                }
            snap = axis.snapshot() or {}
            primary = snap.get("primary") or self._fallback_approach_target(snap) or primary
            bbox = primary.get("bbox") or bbox
            frame_w = float(snap.get("frame_width") or 0.0) or frame_w
            cx = (float(bbox[0]) + float(bbox[2])) / 2.0
            ex = (cx - frame_w / 2.0) / (frame_w / 2.0)
            if abs(ex) > 0.35:
                return {"status": "error", "message": f"目标仍未居中(偏差 {ex:+.2f})，已停止抵近", "ex": round(ex, 3)}
        try:
            max_step = abs(float(step_m if step_m is not None else 12.0))
        except (TypeError, ValueError):
            max_step = 12.0
        max_step = max(1.0, min(12.0, max_step))
        # "够近"的判据是目标在画面里的大小，不是某个写死的米数：面积随距离平方
        # 增长，目标高度占到画面 min_fill 时，特征就足够辨认了（默认 0.25）。
        # 这样"靠近它"由意图（看清特征）驱动，而不是由配置里的数字驱动。
        try:
            min_fill = abs(float(min_fill if min_fill is not None else 0.25))
        except (TypeError, ValueError):
            min_fill = 0.25
        min_fill = max(0.05, min(0.9, min_fill))
        try:
            standoff = abs(float(standoff_m or 0.0))
        except (TypeError, ValueError):
            standoff = 0.0
        standoff = max(0.0, min(30.0, standoff))  # 0 = 不启用绝对距离约束

        frame_h = float(snap.get("frame_height") or 0.0) or 480.0
        bbox_h = abs(float(bbox[3]) - float(bbox[1]))
        fill = bbox_h / frame_h if frame_h > 0 else 0.0
        if fill >= min_fill:
            return {
                "status": "ok",
                "reached": True,
                "target_fill": round(fill, 3),
                "min_fill": round(min_fill, 2),
                "target_track_id": primary.get("track_id"),
                "message": (
                    f"目标已占画面高度 {fill * 100:.0f}%（阈值 {min_fill * 100:.0f}%），"
                    "距离足够辨认特征，不需要继续抵近；要看清细节请改用视觉分析动作。"
                ),
            }

        distance_m = self._target_distance_from_depth(primary, snap, vehicle_name)
        if distance_m is not None and fill > 0.0:
            # 小孔成像：表观大小与距离成反比，所以"要达到 min_fill 还需要走多远"
            # 可以直接从当前距离换算，一步到位而不冲过头。
            target_distance = distance_m * (fill / min_fill)
            remaining = distance_m - target_distance
            if standoff > 0.0:
                remaining = min(remaining, distance_m - standoff)
            if remaining <= 0.5:
                return {
                    "status": "ok",
                    "reached": True,
                    "distance_m": round(distance_m, 2),
                    "message": f"目标距离约 {distance_m:.1f} 米，已到达期望的观察距离，不需要继续抵近。",
                }
            step = max(1.0, min(max_step, remaining))
        else:
            step = max_step
        # 前方避障：深度图里"目标框以外、画面中央"最近的像素就是挡在路上的东西。
        # 太近就停住并报告，而不是继续盲目前进（实测操作员看到飞机一路顶到障碍物）。
        obstacle_m = self._forward_obstacle_distance(primary, snap, vehicle_name)
        if obstacle_m is not None:
            if obstacle_m < 1.5:
                return {
                    "status": "error",
                    "blocked": True,
                    "obstacle_m": round(obstacle_m, 2),
                    "message": f"前方约 {obstacle_m:.1f} 米处有障碍物，已停止抵近（不会盲目前进）。",
                }
            step = max(0.5, min(step, obstacle_m - self._OBSTACLE_MARGIN_M))
        result = self.execute(
            "drone_move_relative",
            {"forward_m": step, "velocity": 1.0, "vehicle_name": vehicle_name} if vehicle_name
            else {"forward_m": step, "velocity": 1.0},
            dry_run=False,
            blocked_by_supervisor=False,
        )
        data = dict(result.data or {})
        data["approach_step_m"] = step
        data["ex"] = round(ex, 3)
        data["target_fill"] = round(fill, 3)
        data["min_fill"] = round(min_fill, 2)
        if distance_m is not None:
            data["distance_m"] = round(distance_m, 2)
        data["target_track_id"] = primary.get("track_id")
        if not result.ok:
            data.setdefault("status", "error")
        return data

    # 机载相机水平视场（settings.json 里 CameraImage 的 FOV_Degrees）。像素偏差
    # 换算成偏航角必须用它，模型自己算不出"该转多少度"。
    _CAMERA_FOV_H_DEG = 90.0
    # 障碍物前保留的安全余量（米）：一步最多走到"障碍距离 - 余量"。
    _OBSTACLE_MARGIN_M = 0.8

    def _forward_obstacle_distance(
        self, target: dict[str, Any], snap: dict[str, Any], vehicle_name: str = ""
    ) -> float | None:
        """前方（目标框以外）最近的障碍距离（米）；拿不到深度返回 None。

        深度图是逐像素的相机距离：画面中央区域里，除了被观察目标本身，任何很近
        的像素都意味着有东西挡在路上（墙、树、路障、别的车）。取 5 分位而不是
        最小值，避免单个噪点让抵近彻底停摆。
        """
        controller = self._perception_camera_controller()
        if controller is None:
            return None
        profile = getattr(getattr(self, "perception_axis", None), "profile", None)
        camera_name = str(getattr(profile, "camera_name", "") or "CameraImage")
        try:
            import numpy as np

            from src.modules.perception_airsim_adapter import get_depth_image

            depth = get_depth_image(controller, camera_name, vehicle_name, timeout_sec=3.0)
            if depth is None:
                return None
            height, width = int(depth.shape[0]), int(depth.shape[1])
            x0, x1 = int(width * 0.25), max(int(width * 0.75), int(width * 0.25) + 1)
            y0, y1 = int(height * 0.17), max(int(height * 0.83), int(height * 0.17) + 1)
            region = np.array(depth[y0:y1, x0:x1], dtype=np.float32, copy=True)
            valid = np.isfinite(region) & (region > 0.2)
            bbox = [float(v) for v in (target.get("bbox") or [])]
            if len(bbox) == 4:
                frame_w = float(snap.get("frame_width") or 0.0) or float(width)
                frame_h = float(snap.get("frame_height") or 0.0) or float(height)
                bx0 = int(max(0.0, min(float(width - 1), bbox[0] / max(frame_w, 1.0) * width)))
                bx1 = int(max(0.0, min(float(width), bbox[2] / max(frame_w, 1.0) * width)))
                by0 = int(max(0.0, min(float(height - 1), bbox[1] / max(frame_h, 1.0) * height)))
                by1 = int(max(0.0, min(float(height), bbox[3] / max(frame_h, 1.0) * height)))
                rx0, rx1 = max(bx0 - x0, 0), min(bx1 - x0, region.shape[1])
                ry0, ry1 = max(by0 - y0, 0), min(by1 - y0, region.shape[0])
                if rx1 > rx0 and ry1 > ry0:
                    valid[ry0:ry1, rx0:rx1] = False  # 被观察对象不是障碍
            values = region[valid]
            if values.size < 8:
                return None
            return float(np.percentile(values, 5))
        except Exception:  # noqa: BLE001 — 避障读数失败不能阻断抵近
            return None

    def _align_to_target(self, tolerance: float = 0.12, vehicle_name: str = "") -> dict[str, Any]:
        """把机头转向让画面里的目标居中（视觉对准）。

        绕目标移动、飞到它某一侧、抵近之前都要先对准：抵近工具只接受居中目标，
        而"目标偏在画面右边 → 该往右转多少度"这种换算必须由工具来做。
        用相机水平 FOV 把归一化像素偏差换成偏航增量，迭代最多三次收敛。
        """
        axis = self.perception_axis
        if axis is None or not getattr(axis, "enabled", False):
            return {"status": "error", "message": "perception axis unavailable"}
        controller = self.controller
        if controller is None or not bool(getattr(controller, "is_connected", False)):
            return {"status": "error", "message": "flight controller not connected"}
        try:
            limit = max(0.02, min(0.5, float(tolerance)))
        except (TypeError, ValueError):
            limit = 0.12
        attempts: list[float] = []
        # 侧移之后目标往往偏得较多（一次绕行就换了半张画面），迭代次数要够；
        # 每一轮都要等检测更新，否则拿旧框算出来的角度是错的。
        for _ in range(5):
            try:
                snap = axis.snapshot() or {}
            except Exception as exc:
                return {"status": "error", "message": f"感知快照读取失败: {exc}"}
            primary = snap.get("primary") or self._fallback_approach_target(snap)
            if not primary:
                return {"status": "error", "message": "画面中没有锁定目标，无法对准", "attempts": attempts}
            bbox = [float(v) for v in (primary.get("bbox") or [])]
            if len(bbox) != 4:
                return {"status": "error", "message": "目标像素框不可用，无法对准", "attempts": attempts}
            frame_w = float(snap.get("frame_width") or 0.0) or 640.0
            ex = ((bbox[0] + bbox[2]) / 2.0 - frame_w / 2.0) / (frame_w / 2.0)
            attempts.append(round(ex, 3))
            if abs(ex) <= limit:
                return {
                    "status": "ok",
                    "aligned": True,
                    "ex": round(ex, 3),
                    "attempts": attempts,
                    "target_track_id": primary.get("track_id"),
                    "message": "目标已在画面中央。",
                }
            try:
                status = controller.get_status(vehicle_name) if vehicle_name else controller.get_status()
                heading = float(getattr(status, "heading_deg", 0.0) or 0.0)
            except Exception as exc:
                return {"status": "error", "message": f"航向读取失败: {exc}", "attempts": attempts}
            target_heading = (heading + ex * (self._CAMERA_FOV_H_DEG / 2.0)) % 360.0
            try:
                ok = controller.rotate_to_heading(target_heading, vehicle_name=vehicle_name)
            except Exception as exc:
                return {"status": "error", "message": f"转向失败: {exc}", "attempts": attempts}
            if not ok:
                return {"status": "error", "message": "转向指令未完成", "attempts": attempts}
            time.sleep(0.9)  # 等检测跟上这次转动（检测约 2~4 Hz，太短会拿旧框再算）
        return {
            "status": "ok" if abs(attempts[-1]) <= max(limit * 2, 0.2) else "error",
            "aligned": abs(attempts[-1]) <= max(limit * 2, 0.2),
            "ex": attempts[-1],
            "attempts": attempts,
            "message": "目标接近居中。" if abs(attempts[-1]) <= max(limit * 2, 0.2) else "对准未收敛，请再试一次或手动转向。",
        }

    def acquire_control_gate(self, blocking: bool = False, timeout: float = -1.0) -> bool:
        """获取飞控指令串行闸门（Agent 飞行工具与视觉伺服共用）。"""
        if blocking and timeout >= 0:
            return self._control_gate.acquire(timeout=timeout)
        return self._control_gate.acquire(blocking=blocking)

    def release_control_gate(self) -> None:
        try:
            self._control_gate.release()
        except RuntimeError:
            pass

    def servo_step(self, primary: dict[str, Any], frame_size: tuple[int, int] | None = None,
                   max_yaw_rate_deg: float = 25.0, vehicle_name: str = "") -> dict[str, Any]:
        """单步视觉伺服：按目标像素横向偏差做一次轻量 yaw 修正。

        只用"一个 yaw-rate 设定值"这一种原语，不做模式切换、不推位置目标，
        也不发升降速度——历史故障正是多路线程同时向 PX4 推不同目标点/速度导致
        的失控。高度由 OFFBOARD 零速度保持，纵向不主动干预。

        调用方必须先持有 control gate（``acquire_control_gate``）；本方法不再
        自行进入/退出 OFFBOARD，模式由调用方统一 prepare/release。
        """
        result: dict[str, Any] = {"corrected": False}
        controller = self.controller
        if controller is None or not bool(getattr(controller, "is_connected", False)):
            return {"error": "flight controller not connected"}
        bbox = (primary or {}).get("bbox") or []
        if len(bbox) != 4:
            return {"error": "no target bbox"}
        try:
            status = controller.get_status().to_dict()
        except Exception as exc:
            return {"error": str(exc)}
        if not bool(status.get("flying")):
            return {"error": "not airborne"}
        frame_w, frame_h = frame_size or (640, 480)
        cx = (float(bbox[0]) + float(bbox[2])) / 2.0
        ex = (cx - frame_w / 2.0) / (frame_w / 2.0)
        result.update({"ex": round(ex, 3), "track_id": (primary or {}).get("track_id")})
        # ex>0 = 目标在画面右侧 → 需要右转（NED yaw 正方向为右转）。
        if abs(ex) < 0.08:
            yaw_rate_deg = 0.0
        else:
            yaw_rate_deg = max(-max_yaw_rate_deg, min(max_yaw_rate_deg, ex * max_yaw_rate_deg * 1.5))
        # 每个 tick 都必须发一次设定值：OFFBOARD 需要持续流，中心附近停发会让
        # PX4 触发 OFFBOARD 超时 failsafe。对准后发 0 偏航率即可保持。
        try:
            controller.send_yaw_rate_setpoint(math.radians(yaw_rate_deg), vehicle_name)
            result["yaw_rate_deg_s"] = round(yaw_rate_deg, 1)
            result["corrected"] = abs(yaw_rate_deg) >= 1e-6
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def center_target_on_screen(
        self,
        seconds: float = 4.0,
        max_yaw_step_deg: float = 25.0,
        vertical_gain: float = 0.45,
        vehicle_name: str = "",
    ) -> ToolCallResult:
        """视觉伺服：把感知轴锁定的目标拉回画面中央（算法闭环，不经 LLM）。

        按像素偏差做小步闭环：横向用 yaw 对准中线，纵向用升降把目标拉回水平中线。
        这是"追踪时目标始终保持在画面中央"的确定性实现，LLM 不参与高频控制。
        """
        started = time.time()
        axis = self.perception_axis
        controller = self.controller
        if axis is None or not getattr(axis, "enabled", False):
            return ToolCallResult("tracking_servo", {"seconds": seconds}, False,
                                  {"status": "error", "message": "perception axis unavailable"}, started, time.time())
        if controller is None or not bool(getattr(controller, "is_connected", False)):
            return ToolCallResult("tracking_servo", {"seconds": seconds}, False,
                                  {"status": "error", "message": "flight controller not connected"}, started, time.time())
        try:
            duration = max(1.0, min(15.0, float(seconds)))
        except (TypeError, ValueError):
            duration = 4.0

        deadline = started + duration
        iterations = 0
        yaw_cmds = 0
        vert_cmds = 0
        last_err = None
        while time.time() < deadline:
            snap = axis.snapshot()
            primary = snap.get("primary") if isinstance(snap, dict) else None
            if not primary:
                break
            bbox = primary.get("bbox") or []
            if len(bbox) != 4:
                break
            frame_w = int(snap.get("frame_width") or 0)
            frame_h = int(snap.get("frame_height") or 0)
            if frame_w <= 0 or frame_h <= 0:
                # One-off fallback until the axis reports a frame size.
                frame_w, frame_h = 640, 480
                try:
                    import cv2
                    annotated = axis.annotated_frame()
                    if annotated and annotated[0]:
                        img = cv2.imdecode(np.frombuffer(annotated[0], np.uint8), cv2.IMREAD_UNCHANGED)
                        if img is not None:
                            frame_h, frame_w = img.shape[:2]
                except Exception:
                    pass
            cx = (float(bbox[0]) + float(bbox[2])) / 2.0
            cy = (float(bbox[1]) + float(bbox[3])) / 2.0
            ex = (cx - frame_w / 2.0) / (frame_w / 2.0)   # +1 目标在画面右侧
            ey = (cy - frame_h / 2.0) / (frame_h / 2.0)   # +1 目标在画面下方
            last_err = {"ex": round(ex, 3), "ey": round(ey, 3), "track_id": primary.get("track_id")}
            if abs(ex) < 0.08 and abs(ey) < 0.12:
                break
            if abs(ex) >= 0.08:
                try:
                    status = controller.get_status().to_dict()
                    heading = float(status.get("heading_deg") or 0.0)
                except Exception:
                    heading = 0.0
                step = max(-max_yaw_step_deg, min(max_yaw_step_deg, ex * max_yaw_step_deg * 1.6))
                try:
                    controller.rotate_to_heading((heading + step) % 360.0, timeout=6.0, vehicle_name=vehicle_name)
                    yaw_cmds += 1
                except Exception:
                    pass
            if abs(ey) >= 0.12:
                try:
                    controller.move_by_velocity(0.0, 0.0, ey * vertical_gain, 0.3, vehicle_name)
                    vert_cmds += 1
                except Exception:
                    pass
            iterations += 1
            time.sleep(0.35)
        data = {
            "status": "ok",
            "message": f"居中伺服完成（{iterations} 轮，偏航 {yaw_cmds} 次，升降 {vert_cmds} 次）",
            "iterations": iterations,
            "yaw_commands": yaw_cmds,
            "vertical_commands": vert_cmds,
            "last_error": last_err,
            "target_visible": last_err is not None,
        }
        return ToolCallResult("tracking_servo", {"seconds": seconds}, True, data, started, time.time())

    def capture_camera_preview(self, params: dict[str, Any] | None = None) -> tuple[bool, bytes, str, dict[str, Any]]:
        """Return one lightweight preview frame for the UI.

        Preview frames intentionally bypass the full airsim_take_photo tool
        path: no stationary check, no retry/cooldown loop, no base64 JSON.
        Agent visual reasoning still uses governed tools.
        """
        raw_params = dict(params or {})
        settings = self._camera_settings()
        source = str(raw_params.get("source") or settings.get("source") or "airsim").strip().lower()
        want_detect = str(raw_params.get("detect") or "0").strip().lower() in {"1", "true", "yes", "on"}
        req_camera = str(raw_params.get("camera_name") or settings.get("camera_name") or "0").strip()
        req_image_type = str(raw_params.get("image_type") or settings.get("image_type") or "scene").strip().lower()
        # 感知轴标注帧只对应"相机0 / Scene"。面板切到 Depth/Segmentation/
        # Infrared 或别的相机时，必须走下面的真实抓帧路径，否则选项形同虚设。
        axis_view_matches = req_camera in {"", "0"} and req_image_type in {"", "scene"}
        if want_detect and source == "airsim" and axis_view_matches:
            axis = getattr(self, "perception_axis", None)
            if axis is not None:
                try:
                    jpeg, dets, ts = axis.annotated_frame()
                except Exception:
                    jpeg, dets, ts = None, [], 0.0
                if jpeg:
                    # 采集偶发卡顿不该让画面和锁定整个消失。这里优先回放感知轴
                    # 缓存，并把新鲜度如实报给前端；不再因为 is_online 一秒内为
                    # 假就回退到阻塞式的重新抓帧——那条路每次要几秒，表现出来
                    # 正是"标注一会有一会没有"。只有缓存完全为空时才回退。
                    age_s = max(0.0, time.time() - float(ts or 0.0))
                    try:
                        online = bool(axis.is_online())
                    except Exception:
                        online = False
                    # 帧率不再烧录进 JPEG：画面用 object-fit: cover 铺满画面区，
                    # 烧在图片左上角的字在小面板上会被裁掉（要放大才看得到）。
                    # 这里随 meta 报给前端，由面板固定位置的徽标显示。
                    try:
                        health = axis.health()
                    except Exception:
                        health = {}
                    max_width = 560
                    quality = 54
                    body, mime = self._encode_preview_frame(jpeg, max_width=max_width, quality=quality)
                    meta: dict[str, Any] = {
                        "status": "ok",
                        "vehicle": "perception-axis",
                        "camera": req_camera or "0",
                        "image_type": "scene",
                        "size_kb": round(len(body) / 1024, 1),
                        "source": "airsim",
                        "detections": list(dets),
                        "axis_online": online,
                        "axis_frame_age_s": round(age_s, 2),
                        "frame_timestamp": float(ts or 0.0),
                        "preview_cached": True,
                        "fps": float(health.get("fps") or 0.0),
                    }
                    # 远端感知服务不报检测率，缺键时不要补 0.0：前端会把它当成
                    # "检测确实停了"显示出来。
                    if health.get("detect_fps") is not None:
                        meta["detect_fps"] = float(health.get("detect_fps") or 0.0)
                    return True, body, mime, meta
        self._start_preview_reaper()
        raw_params = dict(params or {})
        controller, error = self._ensure_preview_controller(raw_params)
        if controller is None:
            return False, b"", "text/plain; charset=utf-8", {
                "status": "error",
                "message": f"camera source unavailable: {error or 'unknown error'}",
            }

        settings = self._camera_settings()
        source = str(raw_params.get("source") or settings.get("source") or "airsim").strip().lower()
        camera_name = str(raw_params.get("camera_name") or settings.get("camera_name") or "0")
        vehicle_name = str(raw_params.get("vehicle_name") or settings.get("vehicle_name") or "")
        image_type_name = str(raw_params.get("image_type") or settings.get("image_type") or "scene").lower()
        detect = str(raw_params.get("detect") or "0").strip().lower() in {"1", "true", "yes", "on"}
        try:
            timeout_sec = float(raw_params.get("timeout_sec") or 2.0)
        except (TypeError, ValueError):
            timeout_sec = 2.0
        timeout_sec = max(0.4, min(4.0, timeout_sec))
        try:
            max_width = int(raw_params.get("max_width") or 640)
        except (TypeError, ValueError):
            max_width = 640
        max_width = max(240, min(1280, max_width))
        try:
            quality = int(raw_params.get("quality") or 62)
        except (TypeError, ValueError):
            quality = 62
        quality = max(35, min(90, quality))

        try:
            if source in {"rtsp", "local"}:
                image_type = 0  # real cameras: scene only
            else:
                import airsim

                type_map = {
                    "scene": airsim.ImageType.Scene,
                    "depth": airsim.ImageType.DepthVis,
                    "segmentation": airsim.ImageType.Segmentation,
                    "infrared": airsim.ImageType.Infrared,
                    "depth_planar": airsim.ImageType.DepthPlanar,
                    "depth_perspective": airsim.ImageType.DepthPerspective,
                    "surface_normals": airsim.ImageType.SurfaceNormals,
                }
                image_type = type_map.get(image_type_name, airsim.ImageType.Scene)
            # AirSim vehicles are named in the simulator (e.g. "Drone1"); the
            # settings panel may carry a MAVLink system name ("px4_sys1") that
            # is not a valid simulator vehicle -> fall back to the default.
            valid_vehicles = list(getattr(controller, "_vehicles", []) or [])
            if vehicle_name and vehicle_name in valid_vehicles:
                names = [vehicle_name]
            elif valid_vehicles:
                names = valid_vehicles
            else:
                names = [""]
            if not names and source in {"rtsp", "local"}:
                names = [""]
            if not names:
                return False, b"", "text/plain; charset=utf-8", {
                    "status": "error",
                    "message": "no AirSim vehicle is available for camera preview",
                }
            vehicle = names[0]
            with self._camera_lock:
                raw = controller.capture_image(
                    camera_name=camera_name,
                    image_type=image_type,
                    vehicle_name=vehicle,
                    timeout=timeout_sec,
                )
            if not raw:
                return False, b"", "text/plain; charset=utf-8", {
                    "status": "error",
                    "message": "AirSim returned an empty camera preview frame",
                }
            detections: list[dict[str, Any]] = []
            preview_bytes = bytes(raw)
            # 只在 Scene 图上做检测标注：Depth/Segmentation/Infrared 是有专门
            # 用途的通道，叠加 YOLO 框没有意义，还会白占模型推理锁（拖慢画面）。
            if detect and image_type_name in {"scene", ""} and source in {"airsim", "rtsp", "local"}:
                preview_bytes, detections = self._detect_and_annotate(preview_bytes)
            body, mime_type = self._encode_preview_frame(preview_bytes, max_width=max_width, quality=quality)
            meta: dict[str, Any] = {
                "status": "ok",
                "vehicle": vehicle,
                "camera": camera_name,
                "image_type": image_type_name,
                "size_kb": round(len(body) / 1024, 1),
                "source": source,
            }
            if detections:
                meta["detections"] = detections
            return True, body, mime_type, meta
        except Exception as exc:
            self.camera_error = str(exc)
            return False, b"", "text/plain; charset=utf-8", {
                "status": "error",
                "message": str(exc),
            }

    # -- preview controller idle reaper ------------------------------------

    def _start_preview_reaper(self) -> None:
        """Lazily start a daemon thread that releases idle real-device controllers.

        Real cameras/streams (local webcam, RTSP) must be closed when no preview
        request arrives for a while, otherwise e.g. a laptop webcam LED stays on
        forever. The reaper disconnects cached local/rtsp controllers once they
        have been idle longer than ``PREVIEW_IDLE_RELEASE_SEC``. AirSim RPC
        clients are left connected (no physical device / cheap to reconnect).
        """
        if getattr(self, "_preview_reaper_started", False):
            return
        self._preview_reaper_started = True
        thread = threading.Thread(target=self._preview_reaper_loop, name="preview-reaper", daemon=True)
        thread.start()

    def _preview_reaper_loop(self) -> None:
        while True:
            time.sleep(1.0)
            try:
                self._reap_idle_preview_controllers()
            except Exception:
                pass

    def _reap_idle_preview_controllers(self) -> None:
        cache = getattr(self, "_preview_controllers", None)
        if not cache:
            return
        now = time.time()
        with self._camera_lock:
            for key, controller in list(cache.items()):
                if not (key.startswith("local:") or key.startswith("rtsp:")):
                    continue
                last = float(getattr(controller, "_last_preview_used", 0.0) or 0.0)
                if last and (now - last) > self.PREVIEW_IDLE_RELEASE_SEC:
                    try:
                        controller.disconnect()
                    except Exception:
                        pass
                    controller._last_preview_used = 0.0

    def _capture_current_frame_jpeg(self) -> bytes | None:
        """Grab one AirSim scene frame directly and return it as JPEG bytes."""
        try:
            import cv2
            import numpy as np

            controller, error = self._ensure_preview_controller({"source": "airsim"})
            if controller is None:
                return None
            raw = controller.capture_image(camera_name="0", image_type=0, vehicle_name="", timeout=3.0)
            if not raw:
                return None
            img = cv2.imdecode(np.frombuffer(bytes(raw), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            if img is None:
                return None
            if img.ndim == 3 and img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            return buf.tobytes() if ok else None
        except Exception:
            return None

    def _detection_confidence(self, fallback: float = 0.35) -> float:
        """Confidence threshold from the perception profile, so both paths agree.

        The UI preview used to hardcode 0.20 while the perception axis used the
        configured value. Different thresholds mean the UI and the Agent are not
        looking at the same detections, which makes the UI an unreliable
        reference for judging whether recognition is stable.
        """
        profile = getattr(getattr(self, "perception_axis", None), "profile", None)
        try:
            value = float(getattr(profile, "confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        return value if value > 0.0 else fallback

    def _detect_and_annotate(self, raw: bytes, threshold: float | None = None) -> tuple[bytes, list[dict[str, Any]]]:
        """Run YOLO on one preview frame and overlay detection boxes.

        One-shot path (not a continuous stream): a single AirSim frame is
        captured, annotated with detection boxes/labels, and returned for the
        UI. Serialized through the shared model inference lock.
        """
        if threshold is None:
            threshold = self._detection_confidence()
        try:
            import cv2
            import numpy as np

            from src.modules.yolo_detection import detect_objects_stateless

            img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            if img is None:
                return raw, []
            if img.ndim == 3 and img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            if img.ndim != 3:
                return raw, []
            # 标准类别走 COCO 固定类别模型（比 YOLO-World 更准更稳）
            dets = detect_objects_stateless(img, "car", threshold)
            labeled: list[dict[str, Any]] = []
            for det in dets:
                x1, y1, x2, y2 = [int(v) for v in det["bbox"]]
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 255), 2)
                label = f"{det['class']} {det['confidence']:.2f}"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                bx1, by1 = x1, max(0, y1 - th - 8)
                bx2, by2 = min(img.shape[1], x1 + tw + 8), max(0, y1 - 2)
                cv2.rectangle(img, (bx1, by1), (bx2, by2), (0, 0, 0), -1)
                cv2.putText(img, label, (bx1 + 4, by2 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                labeled.append({"class": det["class"], "confidence": det["confidence"], "bbox": det["bbox"]})
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not ok:
                return raw, labeled
            return buf.tobytes(), labeled
        except Exception as exc:
            self.camera_error = str(exc)
            return raw, []

    @staticmethod
    def _encode_preview_frame(raw: bytes, max_width: int = 640, quality: int = 62) -> tuple[bytes, str]:
        try:
            import cv2
            import numpy as np

            arr = np.frombuffer(raw, np.uint8)
            image = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
            if image is None:
                return raw, "image/png"
            if len(image.shape) == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            elif image.shape[2] == 4:
                image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
            height, width = image.shape[:2]
            if width > max_width:
                scale = max_width / float(width)
                image = cv2.resize(image, (max_width, max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
            if ok:
                return encoded.tobytes(), "image/jpeg"
        except Exception:
            pass
        return raw, "image/png"

    def list_tools(self) -> list[dict[str, Any]]:
        self.ensure_ready()
        collector = self.collector
        backend_profile = self.backend_profile
        if not collector:
            return []
        specs = []
        for name, fn in sorted(collector.tools.items()):
            specs.append(self._spec_for(name, fn).__dict__)
        if self._camera_tools_ready() and self._camera_capabilities({}).get("image_capture"):
            for camera_tool in sorted(self.CAMERA_SOURCE_TOOLS):
                if camera_tool not in collector.tools:
                    camera_spec = self._camera_tool_spec(camera_tool)
                    if camera_spec is not None:
                        specs.append(camera_spec.__dict__)
        specs.append(
            ToolSpec(
                name="memory_store",
                category="memory",
                description="Write the current mission summary into long-term memory.",
                parameters={"source": {"default": "mission", "annotation": "str"}},
            ).__dict__
        )
        specs.append(
            ToolSpec(
                name="memory_recall",
                category="memory",
                description="Recall previously stored facts, missions, lessons, and run transcripts from long-term memory.",
                parameters={
                    "query": {"default": None, "annotation": "str", "required": True},
                    "limit": {"default": 5, "annotation": "int"},
                },
            ).__dict__
        )
        specs.append(
            ToolSpec(
                name="memory_remember",
                category="memory",
                description="Store a durable fact about the mission or environment for future runs.",
                parameters={
                    "key": {"default": None, "annotation": "str", "required": True},
                    "value": {"default": None, "annotation": "str", "required": True},
                    "tags": {"default": None, "annotation": "str"},
                },
            ).__dict__
        )
        specs.append(
            ToolSpec(
                name="agent_subtask",
                category="agent",
                description="Delegate an open-ended interpretation subtask to a bounded sub-agent that returns a structured report.",
                parameters={
                    "goal": {"default": None, "annotation": "str", "required": True},
                    "constraints": {"default": None, "annotation": "str"},
                    "max_steps": {"default": 6, "annotation": "int"},
                    "model_id": {"default": None, "annotation": "str"},
                },
            ).__dict__
        )
        for spec in specs:
            if isinstance(spec, dict):
                spec["manifest"] = manifest_metadata(str(spec.get("name") or ""))
        return specs

    def list_tool_manifest(self) -> list[dict[str, Any]]:
        return list_tool_manifest()

    def list_tool_cards(self) -> list[dict[str, Any]]:
        self.ensure_ready()
        collector = self.collector
        backend_profile = self.backend_profile
        if not collector or not backend_profile:
            return []
        available = set(collector.tools)
        available.add("memory_store")
        # runtime 直接分派、不注册到 collector 的工具，也要让卡片可见
        available.update({"memory_recall", "memory_remember", "agent_subtask"})
        if "formation_command" in collector.tools:
            available.add("formation_command")
        capabilities = self._camera_capabilities(backend_profile.capabilities.to_dict())
        if self._camera_tools_ready():
            available.update(self.CAMERA_SOURCE_TOOLS)
        from .tool_cards import cards_for_capabilities

        cards = cards_for_capabilities(capabilities, available)
        cards.extend(_agent_memory_tool_cards())
        cards.append(_agent_subtask_tool_card())
        return cards

    def reset_connection(self) -> None:
        """Drop the current controller/tool registry so the next call starts fresh."""
        # stop the formation control loop first: it holds a reference to the
        # controller being dropped and must never keep commanding it
        self.formation_shutdown("reset_connection")
        self._formation = None
        controller = self.controller
        if controller is not None:
            # Generic disconnect: close MAVLink sockets or AirSim links and release session resources.
            try:
                controller.disconnect()
            except Exception:
                pass
            # AirSim also needs its RPC runtime reset to release stuck locks.
            if hasattr(controller, "_reset_rpc_runtime"):
                try:
                    controller._reset_rpc_runtime()
                except Exception:
                    pass
        self.controller = None
        self.collector = None
        self.available = False
        self._last_status_snapshot = {}

    def set_backend(self, backend_id: str | None) -> ToolCallResult:
        """Switch to another registered backend without restarting the process."""
        with self._lock:
            new_id = self.backend_registry.resolve_id(backend_id)
            if new_id == self.backend_id and self.backend_profile is not None:
                return ToolCallResult(
                    "set_backend",
                    {"backend": new_id},
                    True,
                    {"status": "ok", "message": f"already on {new_id}"},
                    time.time(),
                    time.time(),
                )
            self.reset_connection()
            self.backend_id = new_id
            self._last_connect_params = {}
            self._real_vehicle = False
            try:
                self.backend_profile = self.backend_registry.require(new_id)
            except Exception as e:
                return ToolCallResult(
                    "set_backend",
                    {"backend": new_id},
                    False,
                    {"status": "error", "message": str(e)},
                    time.time(),
                    time.time(),
                )
            return ToolCallResult(
                "set_backend",
                {"backend": new_id},
                True,
                {"status": "ok", "message": f"switched to {new_id}"},
                time.time(),
                time.time(),
            )

    def reconnect(
        self,
        ip: str | None = None,
        port: int | None = None,
        url: str = "",
        fallback_url: str = "",
        remote_host: str = "",
        remote_port: int = 0,
        real_vehicle: bool = False,
    ) -> ToolCallResult:
        with self._lock:
            self.reset_connection()
            profile = self.backend_profile or self.backend_registry.require(self.backend_id)
            params = dict(profile.default_connect_params)
            if self.backend_id in {"px4_mavlink", "px4_ros2"}:
                if url:
                    params["url"] = url
                    if fallback_url:
                        params["fallback_url"] = fallback_url
                    if remote_host:
                        params["remote_host"] = remote_host
                    if remote_port:
                        params["remote_port"] = int(remote_port)
                    params["real_vehicle"] = bool(real_vehicle)
                elif self._last_connect_params:
                    params = dict(self._last_connect_params)
            else:
                if ip is not None or port is not None:
                    params.update({
                        "ip": ip or params.get("ip", "127.0.0.1"),
                        "port": int(port or params.get("port", 41452)),
                    })
                elif self._last_connect_params:
                    params = dict(self._last_connect_params)
            self._last_connect_params = dict(params)
            self._real_vehicle = bool(params.get("real_vehicle", False))
            return self.execute(
                "drone_connect",
                params,
                dry_run=False,
                allow_reconnect=False,
            )

    def execute(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        dry_run: bool = False,
        blocked_by_supervisor: bool = False,
        allow_reconnect: bool = True,
    ) -> ToolCallResult:
        params = dict(params or {})
        started = time.time()

        if name == "memory_store":
            return ToolCallResult(
                name,
                params,
                True,
                {"status": "ok", "message": "memory handled by runtime"},
                started,
                time.time(),
            )

        if dry_run:
            try:
                safety = self.validate(name, params)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                return ToolCallResult(
                    name,
                    params,
                    False,
                    {"status": "error", "message": f"invalid tool parameters: {exc}"},
                    started,
                    time.time(),
                    error_code="INVALID_PARAMS",
                )
            return ToolCallResult(
                name,
                params,
                safety.get("level") != "danger",
                {"status": "planned", "message": "dry run only"},
                started,
                time.time(),
                safety=safety,
            )

        if name in self.CAMERA_SOURCE_TOOLS and self.backend_id != "airsim":
            # 拒绝条件看"相机工具是否真的注册成功"，而不是"设置里写了相机源"：
            # 配置了源但注册失败（源不可达）时，旧条件判为"允许执行"，然后静默
            # 落到工具查找失败，报出含糊的 unknown tool。这台情况下给出明确的
            # 相机源提示。_camera_tools_ready() 的注释记录过同一个故障。
            if not self._camera_tools_ready():
                return ToolCallResult(
                    name,
                    params,
                    False,
                    {
                        "status": "error",
                        "message": (
                            f"{name} needs a camera source. On the current "
                            f"{self.backend_id} backend configure a camera source "
                            "(AirSim/RTSP/local) or use perception_status and "
                            "inspect_current_frame for the perception axis stream."
                        ),
                    },
                    started,
                    time.time(),
                    error_code="BLOCKED",
                )

        if blocked_by_supervisor and name not in {"drone_hover", "drone_land", "drone_get_status"}:
            return ToolCallResult(
                name,
                params,
                False,
                {"status": "blocked", "message": "supervisor emergency stop is active"},
                started,
                time.time(),
                error_code="BLOCKED",
            )

        # Formation conflict guard lives at the executor level so EVERY caller
        # (agent loop, skills, GCS panel) is covered: while the deterministic
        # formation/coverage loop commands vehicles, single-vehicle flight tools
        # must not fight it for the same drone. Hover/land/status/connect stay
        # available as safe recovery actions.
        if (
            self.formation_active()
            and name in self.CONTROL_TOOLS
            and name not in {"drone_hover", "drone_land", "drone_get_status", "drone_disconnect", "drone_connect", "airsim_task_cancel"}
        ):
            return ToolCallResult(
                name,
                params,
                False,
                {"status": "blocked", "message": "a formation/coverage mission is active; use formation_command(action=hover_all) or land_all before single-vehicle control"},
                started,
                time.time(),
                error_code="BLOCKED",
            )

        if not self.ensure_ready() or self.collector is None:
            return ToolCallResult(
                name,
                params,
                False,
                {"status": "error", "message": self.init_error or "tool runtime unavailable"},
                started,
                time.time(),
                error_code="RUNTIME_UNAVAILABLE",
            )

        self._lock.acquire()
        try:
            if (
                (name in self.CONTROL_TOOLS or name == "formation_command")
                and self.controller is not None
                and not bool(getattr(self.controller, "is_connected", False))
            ):
                result = ToolCallResult(
                    name,
                    params,
                    False,
                    {
                        "status": "error",
                        "message": "not connected",
                        "backend": getattr(self.controller, "backend_name", self.backend_id),
                    },
                    started,
                    time.time(),
                    error_code="NOT_CONNECTED",
                )
                if allow_reconnect:
                    return self._retry_after_reconnect(name, params, blocked_by_supervisor, None, result)
                return result

            if name in self.CONTROL_TOOLS and self.backend_id == "px4_mavlink" and self.controller is not None:
                try:
                    get_cached_status = getattr(self.controller, "get_cached_status", None)
                    status = get_cached_status() if callable(get_cached_status) else self.controller.get_status()
                    status_data = status.to_dict()
                except Exception as exc:
                    status_data = {"link_stale": True, "connection_error": str(exc)}
                if self._status_is_stale(status_data):
                    heartbeat_age = status_data.get("heartbeat_age_s")
                    age_text = f" (last heartbeat {heartbeat_age}s ago)" if heartbeat_age is not None else ""
                    result = ToolCallResult(
                        name,
                        params,
                        False,
                        {
                            "status": "error",
                            "message": f"PX4 MAVLink heartbeat is lost{age_text}; reconnect the flight controller before running {name}.",
                            "connection_error": "stale MAVLink heartbeat",
                            "heartbeat_age_s": heartbeat_age,
                        },
                        started,
                        time.time(),
                        error_code="LINK_STALE",
                    )
                    if allow_reconnect:
                        return self._retry_after_reconnect(name, params, blocked_by_supervisor, None, result)
                    return result

            try:
                safety = self.validate(name, params)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                return ToolCallResult(
                    name,
                    params,
                    False,
                    {"status": "error", "message": f"invalid tool parameters: {exc}"},
                    started,
                    time.time(),
                    error_code="INVALID_PARAMS",
                )
            if safety.get("level") == "danger":
                # 安全层是闸门，不是夹紧器：danger 一律拒绝执行。
                # 修正值只作为"建议的安全参数"回给模型/操作员，让他们带正确
                # 参数重试；静默套用修正值曾把「10 米高空」的意图变成 0.5 米
                # 贴地飞（z>=0 的修正值是 -min_altitude），把超围栏目标夹到
                # 围栏边界后照飞。warning 级（如高度超过上限、速度偏快）仍然
                # 走下面的自动夹紧/降速，那些是收敛到安全值而不是改换目标。
                suggested = dict(safety.get("corrected_params") or {})
                message = "flight command blocked by safety layer"
                if suggested:
                    message += "；suggested_params 是安全参数，请带它重试"
                return ToolCallResult(
                    name, params, False,
                    {
                        "status": "blocked",
                        "message": message,
                        "violations": safety.get("violations", []),
                        "suggested_params": suggested,
                    },
                    started, time.time(), safety=safety, error_code="SAFETY_BLOCKED",
                )
            if safety.get("corrected_params"):
                params.update(safety["corrected_params"])

            fn = self.collector.tools.get(name)
            if not fn:
                return ToolCallResult(
                    name, params, False,
                    {"status": "error", "message": f"unknown tool: {name}"},
                    started, time.time(), safety=safety, error_code="UNKNOWN_TOOL",
                )

            # Bounded retry for transient timeouts on idempotent read-only tools.
            # Flight-control tools are never retried here (a move may have
            # partially executed; the reconnect path handles link loss instead).
            max_attempts = 2 if name in self._RETRYABLE_READ_TOOLS else 1
            attempts = 0
            while True:
                attempts += 1
                try:
                    raw = fn(**params)
                    data = json.loads(raw) if isinstance(raw, str) else {"status": "ok", "result": raw}
                    status = str(data.get("status", "ok")).strip().lower()
                    ok = status not in {"error", "blocked", "failed", "cancelled", "canceled"}
                    if name == "drone_connect" and data.get("connected") is False:
                        ok = False
                    if name == "drone_connect" and ok:
                        # 记录"这条链路是不是真机"，并把它写回 _last_connect_params
                        # 供自动重连复用（重连若拿不到这两个信息，会退回 default_
                        # connect_params，即配置里的默认端点，而不是操作员实际连的
                        # 那个）。以前这段只覆盖 px4_mavlink，ros2 网关永远记不上
                        # 真机标记。
                        requested_real = params.get("real_vehicle")
                        self._real_vehicle = bool(
                            self._real_vehicle
                            if requested_real is None
                            else requested_real
                        ) or bool(data.get("real_vehicle")) or str(
                            data.get("url") or params.get("url") or ""
                        ).startswith("serial:")
                        self._last_connect_params.update(dict(params))
                        self._last_connect_params["real_vehicle"] = self._real_vehicle
                    task_id = str(data.get("task_id") or "")
                    terminal = status not in {"accepted", "started", "pending", "queued", "running", "in_progress"}
                    async_invalid = False
                    if not terminal and not task_id:
                        ok = False
                        terminal = True
                        async_invalid = True
                        data = {
                            **data,
                            "status": "error",
                            "message": "async tool returned a non-terminal status without task_id",
                        }
                    error_code = "INVALID_ASYNC_RESPONSE" if async_invalid else self._error_code_for(name, data, ok)
                    result = ToolCallResult(
                        name,
                        params,
                        ok,
                        data,
                        started,
                        time.time(),
                        safety=safety,
                        terminal=terminal,
                        task_id=task_id,
                        error_code=error_code,
                    )
                    if allow_reconnect and self._should_retry_after_reconnect(name, result):
                        return self._retry_after_reconnect(name, params, blocked_by_supervisor, safety, result)
                    if attempts < max_attempts and error_code == "TIMEOUT":
                        time.sleep(0.4 * attempts)
                        continue
                    if name in TOOL_OUTPUT_SCHEMAS:
                        violations = validate_json_schema(data, TOOL_OUTPUT_SCHEMAS[name])
                        if violations:
                            # 只标注、不改判：schema 有遗漏（例如 AirSim 在没有
                            # 碰撞体时把 has_collided 写成 None，而 schema 声明
                            # boolean）不该把一次成功的状态读取翻成失败——那会消耗
                            # AgentLoop 的失败预算，3 次就把任务判失败，而 data 里
                            # 的 status 明明还是 ok。与本文件顶部的注释保持一致。
                            result.data = {**data, "validation_errors": violations}
                    return result
                except Exception as e:
                    message = str(e)
                    error_code = self._classify_exception(name, message)
                    result = ToolCallResult(
                        name, params, False,
                        {"status": "error", "message": message},
                        started, time.time(), safety=safety, error_code=error_code,
                    )
                    if allow_reconnect and self._should_retry_after_reconnect(name, result):
                        return self._retry_after_reconnect(name, params, blocked_by_supervisor, safety, result)
                    if attempts < max_attempts and error_code == "TIMEOUT":
                        time.sleep(0.4 * attempts)
                        continue
                    return result
        finally:
            self._lock.release()

    def _current_position(self) -> tuple[float, float, float] | None:
        """当前 NED 位置；链路不可用/读数非法时返回 None。

        validate() 之外的调用方都不在 try 里，控制器抛出的异常会直接穿出
        execute()（它只接 TypeError/ValueError/JSONDecodeError），所以这里
        必须自己兜住，读不到就当作"位置未知"由调用方决定是拒绝还是退化。
        """
        controller = self.controller
        if controller is None or not getattr(controller, "is_connected", False):
            return None
        try:
            status = controller.get_status()
        except Exception:
            return None
        position = getattr(status, "position_ned", None) or {}
        try:
            x = float(position.get("x", 0.0) or 0.0)
            y = float(position.get("y", 0.0) or 0.0)
            z = float(position.get("z", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            return None
        return (x, y, z)

    def _current_global_position(self) -> tuple[float, float] | None:
        """当前 GPS 经纬度；无定位/读数非法/未连接时返回 None。"""
        controller = self.controller
        if controller is None or not getattr(controller, "is_connected", False):
            return None
        try:
            status = controller.get_status()
        except Exception:
            return None
        gps = getattr(status, "gps", None)
        if not isinstance(gps, dict):
            return None
        lat = _finite_or_none(gps.get("lat"))
        lon = _finite_or_none(gps.get("lon"))
        if lat is None or lon is None:
            return None
        # (0,0) 是"没有定位"的占位值，不是几内亚湾
        if abs(lat) <= 0.001 and abs(lon) <= 0.001:
            return None
        return (lat, lon)

    def _no_fly_zone_violations(
        self,
        from_pos: tuple[float, float, float] | None,
        to_pos: tuple[float, float, float],
    ) -> list[str]:
        """返回"从当前位置飞到目标点"这段路径上的禁飞区违规。

        validate_position 只看落点，判断不出路径穿不穿过禁飞区。SafetyValidator
        .validate_move 里的线段-圆相交检测（_segment_crosses_circle）写好了却一直
        没有生产调用点，这里把它接到真正会横穿一段空间的动作上。

        只取禁飞区相关条目：起点/终点的高度与围栏问题由 validate_position 负责，
        不在这里重复报告（起点是飞机当前的既成事实，不是这条指令选的）。

        未配置禁飞区时直接返回、不读遥测——否则每个位置指令都要多一次 RPC，
        而未连接时那次读还会触发数秒的连接尝试。
        """
        return self._leg_zone_check(from_pos, to_pos)[0]

    def _leg_zone_check(
        self,
        from_pos: tuple[float, float, float] | None,
        to_pos: tuple[float, float, float],
    ) -> tuple[list[str], str]:
        """(带前缀的违规列表, 该段判定的级别)。

        配了禁飞区却读不到当前位置时，这一段是无法校验的：不能静默放行（那和
        没接线的旧状态一样），也不该直接判 danger（会拦住合法的离线规划），
        所以给一条 warning，让操作员知道"这一段没查过"。
        """
        if not self.safety.constraints.no_fly_zones:
            return [], "safe"
        if from_pos is None:
            return (
                [
                    "无法读取当前位置，本段航线的禁飞区穿越检查已跳过"
                    "（连接或遥测恢复后重新下发可完成校验）"
                ],
                "warning",
            )
        try:
            result = self.safety.validate_move(from_pos, to_pos)
        except Exception:
            return [], "safe"
        hits = [item for item in result.violations if "禁飞区" in item]
        return ([f"航线{item}" for item in hits], "danger" if hits else "safe")

    def _apply_leg_zone_check(
        self,
        from_pos: tuple[float, float, float] | None,
        to_pos: tuple[float, float, float],
        violations: list[str],
        level: str,
    ) -> str:
        """把一段航线的禁飞区结论并进 (violations, level)。"""
        hits, leg_level = self._leg_zone_check(from_pos, to_pos)
        violations.extend(hits)
        if leg_level == "danger":
            return "danger"
        if leg_level == "warning" and level == "safe":
            return "warning"
        return level

    def _safety_constraints(self) -> dict[str, Any]:
        constraints = self.safety.constraints
        return {
            "max_altitude": constraints.max_altitude,
            "min_altitude": constraints.min_altitude,
            "max_velocity": constraints.max_velocity,
            "geofence_radius": constraints.max_distance_from_home,
        }

    def validate(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        corrected: dict[str, Any] = {}
        violations: list[str] = []
        level = "safe"

        def merge(result) -> None:
            nonlocal level
            if result.violations:
                violations.extend(result.violations)
            if result.level == "danger":
                level = "danger"
            elif result.level == "warning" and level != "danger":
                level = "warning"

        # 每个分支都用 float() 取参，而 float("nan")/JSON 的 NaN 字面量都能成功
        # 转换；NaN 之后与任何阈值比较都是 False，会逐条躲过下面的范围检查。
        # 所以先扫一遍原始参数，在任何按名分支之前就拒掉非有限值。
        bad_params = _non_finite_params(params)
        if bad_params:
            return {
                "level": "danger",
                "violations": [
                    f"参数不是有限数值（NaN/Inf），拒绝执行: {', '.join(bad_params)}"
                ],
                "corrected_params": {},
                "constraints": self._safety_constraints(),
            }

        if name == "drone_takeoff":
            altitude = abs(float(params.get("altitude", 3.0)))
            result = self.safety.validate_position(0.0, 0.0, -altitude)
            merge(result)
            if result.corrected and "z" in result.corrected:
                corrected["altitude"] = abs(float(result.corrected["z"]))

        elif name == "drone_dispatch_takeoff":
            altitude = abs(float(params.get("altitude", 3.0)))
            result = self.safety.validate_position(0.0, 0.0, -altitude)
            merge(result)
            if result.corrected and "z" in result.corrected:
                corrected["altitude"] = abs(float(result.corrected["z"]))

        elif name == "drone_fly_to":
            x = float(params.get("x", 0.0))
            y = float(params.get("y", 0.0))
            z = float(params.get("z", -3.0))
            result = self.safety.validate_position(x, y, z)
            merge(result)
            if result.corrected:
                for key in ("x", "y", "z"):
                    if key in result.corrected:
                        corrected[key] = result.corrected[key]
            level = self._apply_leg_zone_check(self._current_position(), (x, y, z), violations, level)
            velocity = float(params.get("velocity", 2.0))
            vel = self.safety.validate_velocity(velocity, 0.0, 0.0)
            merge(vel)
            if vel.corrected:
                corrected["velocity"] = abs(float(vel.corrected["vx"]))

        elif name == "drone_fly_velocity":
            vx = float(params.get("vx", 0.0))
            vy = float(params.get("vy", 0.0))
            vz = float(params.get("vz", 0.0))
            result = self.safety.validate_velocity(vx, vy, vz)
            merge(result)
            if result.corrected:
                corrected.update(result.corrected)
            # 速度校验只看瞬时矢量，看不出 duration 蕴含的位移：5m/s 下降 60 秒
            # 会从 3 米高度扎进地面，8m/s 飞 300 秒会飞出 100 米围栏 2.4 公里。
            # 所以按 duration 推算落点，走与 drone_fly_to 相同的检查；读不到
            # 当前位置时退化为"整段位移必须放得进围栏"这个不需要遥测的界。
            duration = float(params.get("duration", 0.0) or 0.0)
            if duration > 0.0:
                speed = math.sqrt(vx * vx + vy * vy + vz * vz)
                displacement = speed * duration
                position = self._current_position()
                if position is not None:
                    end = self.safety.validate_position(
                        position[0] + vx * duration,
                        position[1] + vy * duration,
                        position[2] + vz * duration,
                    )
                    merge(end)
                elif displacement > self.safety.constraints.max_distance_from_home:
                    level = "danger"
                    violations.append(
                        f"速度指令位移 {displacement:.1f}m 超过围栏半径 "
                        f"{self.safety.constraints.max_distance_from_home:.0f}m，"
                        "且当前无法读取位置以确认落点"
                    )

        elif name == "drone_move_relative":
            forward_m = float(params.get("forward_m", 0.0))
            right_m = float(params.get("right_m", 0.0))
            up_m = float(params.get("up_m", 0.0))
            velocity = float(params.get("velocity", 2.0))
            if self.controller is not None and getattr(self.controller, "is_connected", False):
                status = self.controller.get_status()
                pos = status.position_ned or {"x": 0.0, "y": 0.0, "z": 0.0}
                heading_value = getattr(status, "extra", {}).get("heading_deg")
                try:
                    heading_deg = float(heading_value)
                except (TypeError, ValueError):
                    heading_deg = math.nan
                if not math.isfinite(heading_deg):
                    attitude = getattr(status, "attitude_rad", None)
                    yaw_value = attitude.get("yaw") if isinstance(attitude, dict) else None
                    try:
                        yaw = float(yaw_value)
                    except (TypeError, ValueError):
                        yaw = 0.0
                    heading_deg = math.degrees(yaw) if math.isfinite(yaw) else 0.0
                heading_deg %= 360.0
                heading_rad = math.radians(heading_deg)
                x = float(pos.get("x", 0.0)) + math.cos(heading_rad) * forward_m + math.cos(heading_rad + math.pi / 2) * right_m
                y = float(pos.get("y", 0.0)) + math.sin(heading_rad) * forward_m + math.sin(heading_rad + math.pi / 2) * right_m
                z = float(pos.get("z", 0.0)) - up_m
                result = self.safety.validate_position(x, y, z)
                merge(result)
                start = (
                    float(pos.get("x", 0.0)),
                    float(pos.get("y", 0.0)),
                    float(pos.get("z", 0.0)),
                )
                level = self._apply_leg_zone_check(start, (x, y, z), violations, level)
            else:
                violations.append("relative movement requires a connection and a current position readback")
                if level == "safe":
                    level = "warning"
            vel = self.safety.validate_velocity(velocity, 0.0, 0.0)
            merge(vel)
            if vel.corrected:
                corrected["velocity"] = abs(float(vel.corrected["vx"]))

        elif name == "drone_fly_path":
            try:
                waypoints = json.loads(str(params.get("waypoints_json", "[]")))
                changed = False
                safe_waypoints = []
                # 航点是折线，逐段检查是否穿越禁飞区（落点检查看不出穿越）。
                previous: tuple[float, float, float] | None = self._current_position()
                for wp in waypoints:
                    x = float(wp.get("x", 0.0))
                    y = float(wp.get("y", 0.0))
                    z = float(wp.get("z", -3.0))
                    result = self.safety.validate_position(x, y, z)
                    merge(result)
                    if result.corrected:
                        x = float(result.corrected.get("x", x))
                        y = float(result.corrected.get("y", y))
                        z = float(result.corrected.get("z", z))
                        changed = True
                    level = self._apply_leg_zone_check(previous, (x, y, z), violations, level)
                    previous = (x, y, z)
                    safe_waypoints.append({"x": x, "y": y, "z": z})
                if changed:
                    corrected["waypoints_json"] = json.dumps(safe_waypoints, ensure_ascii=False)
            except Exception as e:
                level = "danger"
                violations.append(f"waypoint JSON could not be parsed: {e}")

        elif name == "drone_dispatch_path":
            try:
                waypoints = json.loads(str(params.get("waypoints_json", "[]")))
                changed = False
                safe_waypoints = []
                # 航点是折线，逐段检查是否穿越禁飞区（落点检查看不出穿越）。
                previous: tuple[float, float, float] | None = self._current_position()
                for wp in waypoints:
                    x = float(wp.get("x", 0.0))
                    y = float(wp.get("y", 0.0))
                    z = float(wp.get("z", -3.0))
                    result = self.safety.validate_position(x, y, z)
                    merge(result)
                    if result.corrected:
                        x = float(result.corrected.get("x", x))
                        y = float(result.corrected.get("y", y))
                        z = float(result.corrected.get("z", z))
                        changed = True
                    level = self._apply_leg_zone_check(previous, (x, y, z), violations, level)
                    previous = (x, y, z)
                    safe_waypoints.append({"x": x, "y": y, "z": z})
                if changed:
                    corrected["waypoints_json"] = json.dumps(safe_waypoints, ensure_ascii=False)
            except Exception as e:
                level = "danger"
                violations.append(f"waypoint JSON could not be parsed: {e}")

        elif name == "drone_dispatch_return_land":
            x = float(params.get("x", 0.0))
            y = float(params.get("y", 0.0))
            z = float(params.get("z", -3.0))
            result = self.safety.validate_position(x, y, z)
            merge(result)
            if result.corrected:
                for key in ("x", "y", "z"):
                    if key in result.corrected:
                        corrected[key] = result.corrected[key]

        elif name == "drone_upload_mission":
            try:
                payload = json.loads(str(params.get("waypoints_json", "[]")))
                if isinstance(payload, dict):
                    raw_items = payload.get("items") or payload.get("waypoints") or []
                else:
                    raw_items = payload
                if not isinstance(raw_items, list):
                    raise ValueError("mission items must be a list")
                changed = False
                safe_items = []
                # 航点任务是一段折线：逐段检查穿越。全球坐标条目算不出 NED 线段，
                # 遇到它就把 previous 清空（不能跨着它推断下一段）。
                previous: tuple[float, float, float] | None = self._current_position()
                for item in raw_items:
                    if not isinstance(item, dict):
                        continue
                    safe_item = dict(item)
                    # 优先级必须与上传器一致：mavlink_controller 的归一化先看
                    # lat/lon，只有它们缺失时才用 x/y/z。以前这里先判 x/y/z，
                    # 于是一个同时带两种坐标的条目（MissionItem.to_dict() 就会
                    # 输出全部六个键）会被按"家附近那个本地点"校验，而自驾仪
                    # 实际飞的是 lat/lon —— 围栏检查被整个绕过。
                    has_global = (
                        _finite_or_none(safe_item.get("lat")) is not None
                        and _finite_or_none(safe_item.get("lon")) is not None
                    )
                    lat_raw = safe_item.get("lat")
                    lon_raw = safe_item.get("lon")
                    latlon_present = lat_raw is not None and lon_raw is not None
                    if latlon_present and not has_global:
                        # 有 lat/lon 但不是有限数值：上传器会拿它当地理坐标用，
                        # 距离比较对 NaN 恒为 False，于是"检查通过"。必须拒绝。
                        level = "danger"
                        violations.append(
                            f"全球航点坐标非法: lat={lat_raw!r} lon={lon_raw!r} "
                            "（必须是有限数值）"
                        )
                    has_local = all(safe_item.get(axis) is not None for axis in ("x", "y", "z"))
                    if latlon_present:
                        if has_local:
                            violations.append(
                                "该航点同时带 lat/lon 与 x/y/z，上传器以 lat/lon 为准"
                                "（x/y/z 被忽略）"
                            )
                    if has_local and not latlon_present:
                        x = float(safe_item.get("x", 0.0))
                        y = float(safe_item.get("y", 0.0))
                        z = float(safe_item.get("z", -3.0))
                        result = self.safety.validate_position(x, y, z)
                        merge(result)
                        level = self._apply_leg_zone_check(previous, (x, y, z), violations, level)
                        previous = (x, y, z)
                        if result.corrected:
                            x = float(result.corrected.get("x", x))
                            y = float(result.corrected.get("y", y))
                            z = float(result.corrected.get("z", z))
                            safe_item.update({"x": x, "y": y, "z": z, "alt_m": abs(z)})
                            changed = True
                    elif latlon_present:
                        previous = None
                        # 全球坐标航点：以前只处理"含 alt_m"的条目，lat/lon 条目
                        # 既没有 x/y/z 也不含 alt_m 时直接落进 safe_items，一路
                        # 不做任何检查。上传后 drone_start_mission 会把飞机交给
                        # 自驾仪，自动飞行脱离 agent 循环和包线看门狗，所以围栏
                        # 必须在这里守住；无法定位时宁可拒绝也不放行。
                        altitude = _finite_or_none(
                            safe_item.get("alt_m", safe_item.get("alt"))
                        )
                        if altitude is None:
                            level = "danger"
                            violations.append(
                                "全球航点缺少合法高度（alt_m / alt 必须是有限数值）"
                            )
                        result = self.safety.validate_position(0.0, 0.0, -abs(altitude or 3.0))
                        merge(result)
                        origin = self._current_global_position()
                        lat_value = _finite_or_none(lat_raw)
                        lon_value = _finite_or_none(lon_raw)
                        if origin is None:
                            level = "danger"
                            violations.append(
                                "无法读取当前 GPS 位置，不能核对全球航点是否在围栏内"
                                "（远程航线请先提高配置项 safety_geofence_m）"
                            )
                        elif lat_value is None or lon_value is None:
                            # 上面已经记了一条"坐标非法"的 danger；这里不再算距离
                            # ——对 NaN 的距离比较恒为 False，算了也只会"通过"。
                            pass
                        else:
                            distance = _gps_distance_m(origin[0], origin[1], lat_value, lon_value)
                            if distance > self.safety.constraints.max_distance_from_home:
                                level = "danger"
                                violations.append(
                                    f"全球航点距离当前载具 {distance:.0f}m，超出围栏半径 "
                                    f"{self.safety.constraints.max_distance_from_home:.0f}m"
                                )
                        # alt 别名统一成 alt_m：控制器两种都收，不统一的话夹紧值
                        # 会被别名覆盖。只有真的改动了内容才标记 changed，否则会把
                        # 一条未修改的航线也重写成"修正版"。
                        if altitude is not None:
                            normalized = abs(float(altitude))
                            if "alt" in safe_item or safe_item.get("alt_m") != normalized:
                                safe_item.pop("alt", None)
                                safe_item["alt_m"] = normalized
                                changed = True
                    elif "alt_m" in safe_item or "alt" in safe_item:
                        altitude = abs(
                            _finite_or_none(safe_item.get("alt_m", safe_item.get("alt"))) or 3.0
                        )
                        result = self.safety.validate_position(0.0, 0.0, -altitude)
                        merge(result)
                        if result.corrected and "z" in result.corrected:
                            safe_item["alt_m"] = abs(float(result.corrected["z"]))
                            safe_item.pop("alt", None)
                            changed = True
                        elif "alt" in safe_item:
                            # 别名统一成 alt_m，避免夹紧值被 alt 覆盖。
                            safe_item["alt_m"] = altitude
                            safe_item.pop("alt", None)
                            changed = True
                    safe_items.append(safe_item)
                if changed:
                    if isinstance(payload, dict):
                        payload["items"] = safe_items
                        corrected["waypoints_json"] = json.dumps(payload, ensure_ascii=False)
                    else:
                        corrected["waypoints_json"] = json.dumps(safe_items, ensure_ascii=False)
            except Exception as e:
                level = "danger"
                violations.append(f"mission JSON could not be parsed: {e}")

        elif name == "formation_command":
            action = str(params.get("action") or "status")
            if action == "takeoff":
                altitude = abs(float(params.get("altitude", 10.0)))
                result = self.safety.validate_position(0.0, 0.0, -altitude)
                merge(result)
                if result.corrected and "z" in result.corrected:
                    corrected["altitude"] = abs(float(result.corrected["z"]))
            elif action == "move_center":
                x = float(params.get("x", 0.0))
                y = float(params.get("y", 0.0))
                z = float(params.get("z", -10.0)) if params.get("z") is not None else -10.0
                result = self.safety.validate_position(x, y, z)
                merge(result)
                if result.corrected:
                    for key in ("x", "y", "z"):
                        if key in result.corrected:
                            corrected[key] = result.corrected[key]
            elif action == "coverage_plan":
                shape = str(params.get("area_shape") or "rectangle")
                area_x = float(params.get("area_x", 0.0))
                area_y = float(params.get("area_y", 0.0))
                area_altitude = abs(float(params.get("area_altitude", 10.0)))
                if shape == "circle":
                    radius = abs(float(params.get("area_radius", 25.0)))
                    if radius > 500.0:
                        level = "danger"
                        violations.append(f"coverage area radius {radius:.0f}m exceeds 500m limit")
                    # geofence: the farthest points of the circle must stay inside
                    for dx, dy in ((radius, 0.0), (-radius, 0.0), (0.0, radius), (0.0, -radius)):
                        result = self.safety.validate_position(area_x + dx, area_y + dy, -area_altitude)
                        merge(result)
                else:
                    width = abs(float(params.get("area_width", 100.0)))
                    height = abs(float(params.get("area_height", 100.0)))
                    if width > 500.0 or height > 500.0:
                        level = "danger"
                        violations.append(f"coverage area {width:.0f}x{height:.0f}m exceeds 500m limit")
                    # geofence: every corner of the rectangle must stay inside
                    half_w, half_h = width / 2.0, height / 2.0
                    for cx, cy in (
                        (area_x + half_w, area_y + half_h),
                        (area_x - half_w, area_y + half_h),
                        (area_x - half_w, area_y - half_h),
                        (area_x + half_w, area_y - half_h),
                    ):
                        result = self.safety.validate_position(cx, cy, -area_altitude)
                        merge(result)
                result = self.safety.validate_position(0.0, 0.0, -area_altitude)
                merge(result)
                if result.corrected and "z" in result.corrected:
                    corrected["area_altitude"] = abs(float(result.corrected["z"]))
                # 禁飞区：四条角点不够——100x100 的区域可以把一个禁飞区整个罩在
                # 里面而四个角都在区外，覆盖任务会直接飞过去。按"区域外接圆与
                # 禁飞区圆相交"判定（保守：宁可报危险也不放过）。
                if self.safety.constraints.no_fly_zones:
                    if shape == "circle":
                        area_extent = abs(float(params.get("area_radius", 25.0)))
                    else:
                        half_w = abs(float(params.get("area_width", 100.0))) / 2.0
                        half_h = abs(float(params.get("area_height", 100.0))) / 2.0
                        area_extent = math.hypot(half_w, half_h)
                    for zone in self.safety.constraints.no_fly_zones:
                        zone_x = float(zone.get("x", 0.0))
                        zone_y = float(zone.get("y", 0.0))
                        zone_r = float(zone.get("radius", 0.0))
                        gap = math.hypot(area_x - zone_x, area_y - zone_y)
                        if gap <= zone_r + area_extent:
                            level = "danger"
                            violations.append(
                                f"覆盖区域与禁飞区相交: 区域中心距禁飞区中心 {gap:.0f}m，"
                                f"禁飞区半径 {zone_r:.0f}m + 区域外接半径 {area_extent:.0f}m"
                            )
            elif action == "coverage_start" and "coverage_speed" in params:
                speed = float(params.get("coverage_speed", 3.0))
                vel = self.safety.validate_velocity(speed, 0.0, 0.0)
                merge(vel)
                if vel.corrected and "vx" in vel.corrected:
                    corrected["coverage_speed"] = abs(float(vel.corrected["vx"]))

        return {
            "level": level,
            "violations": violations,
            "corrected_params": corrected,
            "constraints": self._safety_constraints(),
        }

    def _public_backend_profile(self) -> dict[str, Any] | None:
        if self.backend_profile is None:
            return None
        profile = self.backend_profile.to_public_dict()
        profile["capabilities"] = self._camera_capabilities(profile.get("capabilities") or {})
        # 只看"这条链路是不是真机"，不看是哪个后端：以前这里额外要求
        # backend_id == "px4_mavlink"，而 px4_ros2 的 profile 又写死
        # real_vehicle=False、连接参数也从不携带该标记，于是真机走 ROS2 网关时
        # 审批门（依赖 requires_operator_approval）永不生效。
        if self._real_vehicle:
            capabilities = dict(profile.get("capabilities") or {})
            capabilities.update({
                "real_vehicle": True,
                "simulated_vehicle": False,
                "requires_operator_approval": True,
            })
            profile["capabilities"] = capabilities
        return profile

    def _operation_contract(self, drone_status: dict[str, Any] | None = None) -> dict[str, Any]:
        """Describe the exact command and mission channel selected for this backend."""
        drone = drone_status or {}
        connected = bool(getattr(self.controller, "is_connected", False)) if self.controller is not None else False
        real_vehicle = bool((self._public_backend_profile() or {}).get("capabilities", {}).get("real_vehicle"))
        map_position_valid = bool(drone.get("map_position_valid", not real_vehicle))
        if self.backend_id == "px4_mavlink":
            return {
                "backend": self.backend_id,
                "vehicle_kind": "real_px4" if real_vehicle else "px4_sitl",
                "command_channel": "MAVLink",
                "mission_channel": "PX4 native mission protocol",
                "mission_frame": "global_relative_alt",
                "return_channel": "PX4 native RTL mode",
                "position_source": drone.get("position_source") or "MAVLink telemetry",
                "map_position_valid": map_position_valid,
                "global_mission_ready": connected and map_position_valid,
            }
        if self.backend_id == "px4_ros2":
            return {
                "backend": self.backend_id,
                "vehicle_kind": "px4_via_ros2",
                "command_channel": "ROS2 Provider Gateway",
                "mission_channel": "ROS2 offboard local path",
                "mission_frame": "local_ned",
                "return_channel": "ROS2 gateway PX4 RTL mode",
                "position_source": drone.get("position_source") or "PX4 ROS2 odometry",
                "map_position_valid": bool(drone.get("map_position_valid", False)),
                "global_mission_ready": False,
            }
        return {
            "backend": self.backend_id,
            "vehicle_kind": "simulation",
            "command_channel": "AirSim RPC",
            "mission_channel": "AirSim local path",
            "mission_frame": "local_ned",
            "return_channel": "AirSim local home path",
            "position_source": "AirSim NED + configured geodetic origin",
            "map_position_valid": connected,
            "global_mission_ready": connected,
        }

    def status_snapshot(self) -> dict[str, Any]:
        # Try a non-blocking lock so long connect/reconnect operations do not block status polling.
        if not self._lock.acquire(blocking=False):
            live_snapshot = self._busy_status_snapshot()
            if live_snapshot is not None:
                return live_snapshot
            if self._last_status_snapshot:
                cached = dict(self._last_status_snapshot)
                cached["busy"] = True
                # 执行锁被占用（正在跑工具/重连）≠ 链路断开。以前这里直接把
                # connected 置 False 并清空车辆数据，于是每次长工具（VLM/移动）
                # 执行期间 UI 都会闪一下 "PX4 OFFLINE"，工具结束又变回 ONLINE——
                # 表现为"连接时不时断开又重连"。只有当缓存来自另一个后端时，
                # 其车辆数据才不可信，需要丢弃。
                same_backend = str(cached.get("backend") or "") == str(self.backend_id or "")
                if not same_backend:
                    cached["connected"] = False
                    cached["stale_connection"] = True
                    cached["drone"] = None
                    cached["vehicles"] = []
                    cached["flight_tasks"] = {}
                return cached
            return {
                "ready": self.available,
                "init_error": self.init_error,
                "connected": False,
                "stale_connection": True,
                "busy": True,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "tool_cards": [],
                "backends": self.backend_registry.list_public(),
                "drone": None,
                "operation_contract": self._operation_contract(),
            }
        try:
            ready = self.ensure_ready()
            connected = False
            drone_status: dict[str, Any] | None = None
            stale_connection = False
            if ready and self.controller is not None:
                connected = bool(getattr(self.controller, "is_connected", False))
                if connected:
                    try:
                        drone_status = self.controller.get_status().to_dict()
                    except Exception as e:
                        drone_status = {"error": str(e)}
                        stale_connection = True
                    connected = bool(getattr(self.controller, "is_connected", False))
                    if self._status_is_stale(drone_status):
                        stale_connection = True
                        connected = False
            vehicles_status = self._vehicles_status(connected)
            # 已派发航线（fire-and-forget）的完成跟踪，供 UI 提示"飞行结束"
            flight_tasks: dict[str, Any] = {}
            update_tasks = getattr(self.controller, "update_flight_task_progress", None)
            if callable(update_tasks):
                try:
                    flight_tasks = update_tasks(vehicles_status) or {}
                except Exception:
                    flight_tasks = {}
            snapshot = {
                "ready": ready,
                "init_error": self.init_error,
                "connected": connected,
                "stale_connection": stale_connection,
                "busy": False,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "tool_cards": self.list_tool_cards() if ready else [],
                "backends": self.backend_registry.list_public(),
                "drone": drone_status,
                "vehicles": vehicles_status,
                "flight_tasks": flight_tasks,
                "perception": self._perception_health(),
                "operation_contract": self._operation_contract(drone_status),
                "map_origin": self._map_origin(),
            }
            self._last_status_snapshot = snapshot
            return dict(snapshot)
        finally:
            self._lock.release()

    def _map_origin(self) -> dict[str, float] | None:
        """NED 原点经纬度（仅 AirSim 有），供 UI 地图对齐场景所在城市。

        优先问已连接的 controller；没连上仿真器时回退到 backend profile 的
        提供者，这样地图在链路建立之前就落在正确的城市。
        """
        getter = getattr(self.controller, "map_origin", None)
        if callable(getter):
            try:
                origin = getter()
            except Exception:
                origin = None
            if isinstance(origin, dict):
                return origin
        provider = getattr(self.backend_profile, "map_origin_provider", None)
        if not callable(provider):
            return None
        try:
            origin = provider()
        except Exception:
            return None
        return origin if isinstance(origin, dict) else None

    def _vehicles_status(self, connected: bool) -> list[dict[str, Any]]:
        """Per-vehicle compact status for multi-vehicle backends (AirSim).

        Single-vehicle backends report one entry matching ``drone``; backends
        without per-vehicle status fall back to the default drone payload.
        """
        controller = self.controller
        if not connected or controller is None:
            return []
        try:
            names = list(controller.list_vehicles() or [])
        except Exception:
            names = []
        if not names:
            return []
        vehicles: list[dict[str, Any]] = []
        for name in names:
            try:
                status = controller.get_status(name).to_dict()
            except Exception as exc:
                status = {"vehicle_name": name, "error": str(exc)}
            status.setdefault("vehicle_name", name)
            vehicles.append(status)
        return vehicles

    def vehicle_info(self, refresh: bool = False) -> dict[str, Any]:
        """Return active vehicle link and firmware metadata for settings UI."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready else "error",
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            connection_info = getattr(controller, "get_connection_info", None)
            if callable(connection_info):
                payload["connection"] = connection_info()
            if connected:
                firmware_info = getattr(controller, "get_firmware_info", None)
                if callable(firmware_info):
                    payload["firmware"] = firmware_info(force=bool(refresh))
                parameter_status = getattr(controller, "get_parameter_status", None)
                if callable(parameter_status):
                    payload["parameters"] = parameter_status()
            return payload
        finally:
            self._lock.release()

    def vehicle_parameters(
        self,
        refresh: bool = False,
        query: str = "",
        limit: int = 200,
        offset: int = 0,
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Return active vehicle parameters for the settings UI and Agent."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
                "parameters": [],
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready else "error",
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "parameters": [],
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            get_parameters = getattr(controller, "get_parameters", None)
            if not callable(get_parameters):
                payload["status"] = "error"
                payload["message"] = "parameter download is not supported by this backend"
                return payload
            data = get_parameters(
                refresh=bool(refresh),
                timeout=float(timeout),
                query=str(query or ""),
                limit=int(limit),
                offset=int(offset),
            )
            if isinstance(data, dict):
                payload.update(data)
            return payload
        finally:
            self._lock.release()

    def set_vehicle_parameter(
        self,
        name: str,
        value: Any,
        component_id: int | None = None,
        param_type: int | None = None,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        """Set one PX4 parameter through the active MAVLink backend."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready and connected else ("disconnected" if ready else "error"),
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            set_parameter = getattr(controller, "set_parameter", None)
            if not callable(set_parameter):
                payload["status"] = "error"
                payload["message"] = "parameter write is not supported by this backend"
                return payload
            data = set_parameter(
                name=str(name or ""),
                value=value,
                component_id=component_id,
                param_type=param_type,
                timeout=float(timeout or 3.0),
            )
            if isinstance(data, dict):
                payload.update(data)
            return payload
        finally:
            self._lock.release()

    def vehicle_setup_snapshot(self, include_history: bool = True, history_limit: int = 240) -> dict[str, Any]:
        """Return QGC-style read-only PX4 setup diagnostics for the settings UI."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
                "history": {},
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready and connected else ("disconnected" if ready else "error"),
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "history": {},
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            get_snapshot = getattr(controller, "get_vehicle_setup_snapshot", None)
            if not callable(get_snapshot):
                payload["status"] = "error"
                payload["message"] = "vehicle setup diagnostics are not supported by this backend"
                return payload
            data = get_snapshot(include_history=include_history, history_limit=history_limit)
            if isinstance(data, dict):
                payload.update(data)
            return payload
        finally:
            self._lock.release()

    def vehicle_telemetry_snapshot(
        self,
        include_history: bool = True,
        history_limit: int = 240,
        history_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return lightweight live vehicle telemetry for high-frequency settings UI updates."""
        if not self._lock.acquire(blocking=False):
            return {
                "status": "busy",
                "connected": bool((self._last_status_snapshot or {}).get("connected")),
                "message": "vehicle runtime is busy",
                "backend": self.backend_id,
                "history": {},
            }
        try:
            ready = self.ensure_ready()
            controller = self.controller
            connected = bool(controller is not None and getattr(controller, "is_connected", False))
            payload: dict[str, Any] = {
                "status": "ok" if ready and connected else ("disconnected" if ready else "error"),
                "ready": ready,
                "connected": connected,
                "backend": self.backend_id,
                "backend_profile": self._public_backend_profile(),
                "history": {},
            }
            if not ready:
                payload["message"] = self.init_error or "tool runtime unavailable"
                return payload
            if controller is None:
                payload["status"] = "error"
                payload["message"] = "controller is not initialized"
                return payload
            get_snapshot = getattr(controller, "get_vehicle_telemetry_snapshot", None)
            if not callable(get_snapshot):
                payload["status"] = "error"
                payload["message"] = "vehicle live telemetry is not supported by this backend"
                return payload
            data = get_snapshot(
                include_history=include_history,
                history_limit=history_limit,
                history_keys=history_keys,
            )
            if isinstance(data, dict):
                payload.update(data)
            return payload
        finally:
            self._lock.release()

    def _busy_status_snapshot(self) -> dict[str, Any] | None:
        """Best-effort live telemetry while a long-running control tool owns the runtime lock.

        Long local waypoint execution can hold ToolRuntime._lock for many seconds. Returning
        only the last cached snapshot during that window makes the frontend marker jump from
        the initial frame to the final frame. This method reads telemetry only; it never sends
        flight-control commands. MAVLink backends can expose get_cached_status() so this read
        does not consume command ACK / mission-transfer messages while control is active.
        """
        controller = self.controller
        if controller is None:
            return None

        ready = self.available
        connected = bool(getattr(controller, "is_connected", False))
        if not ready or not connected:
            return None

        stale_connection = False
        try:
            get_cached_status = getattr(controller, "get_cached_status", None)
            if callable(get_cached_status):
                drone_status = get_cached_status().to_dict()
            else:
                drone_status = controller.get_status().to_dict()
        except Exception as exc:
            drone_status = {"error": str(exc)}
            stale_connection = True

        connected = bool(getattr(controller, "is_connected", False))
        # 忙碌期间不要用"心跳年龄"判链路：遥测只在有人读 socket 时才刷新，
        # 一个跑 20 多秒的视觉工具期间没人刷新，心跳年龄自然变大，会被误判成
        # OFFLINE（工具一结束又变回 ONLINE，UI 上就是连接反复抖动）。这里只在
        # 明确的连接错误时才降级；真正的断链由非忙碌路径和重连逻辑判定。
        if isinstance(drone_status, dict) and drone_status.get("connection_error"):
            stale_connection = True
            connected = False

        cached = self._last_status_snapshot or {}
        # vehicles: reuse the cached list only — a fresh per-vehicle status
        # read is an RPC that would queue behind the tool call currently
        # holding the lock. Without the cached list the frontend vehicle
        # panel empties whenever any long tool call runs, flickering between
        # "3 drones" and "none".
        vehicles_cached = cached.get("vehicles") or []
        flight_tasks_busy: dict[str, Any] = {}
        update_tasks_busy = getattr(controller, "update_flight_task_progress", None)
        if callable(update_tasks_busy):
            try:
                flight_tasks_busy = update_tasks_busy(vehicles_cached) or {}
            except Exception:
                flight_tasks_busy = {}
        snapshot = {
            "ready": ready,
            "init_error": self.init_error,
            "connected": connected,
            "stale_connection": stale_connection,
            "busy": True,
            "backend": self.backend_id,
            "backend_profile": cached.get("backend_profile") or self._public_backend_profile(),
            # 这条"工具锁被占"的快照同样要带感知健康：不带的话前端在 Agent 跑工具
            # 期间读不到 detect_fps，检测明明在跑（画面里还有检测框）却显示"未检测"，
            # 按钮也跟着变成"开启目标检测"。
            "perception": self._perception_health(),
            "tool_cards": cached.get("tool_cards") or [],
            "backends": cached.get("backends") or self.backend_registry.list_public(),
            "drone": drone_status,
            "vehicles": vehicles_cached,
            "flight_tasks": flight_tasks_busy,
        }
        self._last_status_snapshot = snapshot
        return dict(snapshot)

    @staticmethod
    def _status_is_stale(drone_status: dict[str, Any] | None) -> bool:
        if not isinstance(drone_status, dict):
            return False
        return bool(drone_status.get("connection_error") or drone_status.get("link_stale"))

    def _should_retry_after_reconnect(self, name: str, result: ToolCallResult) -> bool:
        if result.ok or not self._requires_vehicle_connection(name):
            return False
        return self._is_connection_error(result.data)

    def _retry_after_reconnect(
        self,
        name: str,
        params: dict[str, Any],
        blocked_by_supervisor: bool,
        safety: dict[str, Any] | None,
        failed_result: ToolCallResult,
    ) -> ToolCallResult:
        reconnect = self.reconnect()
        if not reconnect.ok:
            failed_result.data["reconnect"] = reconnect.to_dict()
            return failed_result

        if name in self.CONTROL_TOOLS or name == "formation_command":
            # Safety: never blindly re-dispatch a flight-control command after a
            # link loss. The command may have partially executed before the
            # connection dropped, and re-sending could double a move. Return the
            # failure with a clear recovery hint instead.
            failed_result.data["auto_reconnect"] = {
                "attempted": True,
                "ok": reconnect.ok,
                "redispatched": False,
                "reason": "flight-control command not auto-redispatched after reconnect; operator must confirm state before re-issuing",
                "before_retry": failed_result.to_dict(),
            }
            return failed_result

        retry = self.execute(
            name,
            params,
            dry_run=False,
            blocked_by_supervisor=blocked_by_supervisor,
            allow_reconnect=False,
        )
        retry.safety = retry.safety or safety
        retry.data["auto_reconnect"] = {
            "attempted": True,
            "ok": reconnect.ok,
            "redispatched": True,
            "before_retry": failed_result.to_dict(),
        }
        return retry

    def _requires_vehicle_connection(self, name: str) -> bool:
        if name in {"memory_store", "drone_connect", "drone_disconnect"}:
            return False
        # 相机类工具的超时/失败是相机链路的问题，不代表飞控链路断开。以前它同样
        # 命中连接类标记，于是一次 airsim_take_photo 超时就会 disconnect 并重连
        # 飞控链路——飞机可能正在空中，而且真机上相机往往根本不在飞控链路上。
        if name in self.CAMERA_SOURCE_TOOLS:
            return False
        return name.startswith("drone_") or name.startswith("airsim_")

    def _is_connection_error(self, data: dict[str, Any]) -> bool:
        # 只扫错误语义字段。payload 里恒有的结构化字段（backend="AirSim"、
        # 载具名等）会让 "airsim" 这类标记误命中——例如后端不支持某工具时
        # 返回的 "not supported" 也带 backend 字段，曾被误判成连接错误，
        # 触发不必要的断开重连（UI 表现为 AirSim OFFLINE 一下又重连）。
        fields: list[str] = []
        for key in ("message", "error", "error_detail", "path_error"):
            value = data.get(key)
            if isinstance(value, str):
                fields.append(value)
            elif isinstance(value, dict):
                fields.append(json.dumps(value, ensure_ascii=False, default=str))
        text = " ".join(fields).lower()
        return any(marker in text for marker in self.CONNECTION_ERROR_MARKERS)

    @staticmethod
    def _error_code_for(name: str, data: dict[str, Any], ok: bool) -> str:
        """Classify a failed tool result into a structured error code."""
        if ok:
            return ""
        status = str(data.get("status") or "").strip().lower()
        message = str(data.get("message") or "").lower()
        if status == "blocked":
            return "BLOCKED"
        if status in {"cancelled", "canceled"}:
            return "CANCELLED"
        if "timeout" in message or "timed out" in message or "超时" in message:
            return "TIMEOUT"
        if any(marker in message for marker in ("not connected", "连接失败", "未连接")):
            return "NOT_CONNECTED"
        return "TOOL_ERROR"

    @staticmethod
    def _classify_exception(name: str, message: str) -> str:
        """Classify a raised exception from a tool call."""
        lowered = str(message or "").lower()
        if any(marker in lowered for marker in ("timeout", "timed out", "超时")):
            return "TIMEOUT"
        if any(marker in lowered for marker in ("connect", "connection", "refused", "reset", "broken pipe", "winerror", "未连接", "连接")):
            return "CONNECTION"
        return "TOOL_ERROR"

    def _spec_for(self, name: str, fn: Callable[..., str]) -> ToolSpec:
        doc = inspect.getdoc(fn) or ""
        first_line = doc.splitlines()[0] if doc else name
        params: dict[str, Any] = {}
        signature = inspect.signature(fn)
        for key, param in signature.parameters.items():
            if param.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
                continue
            default = None if param.default is inspect._empty else param.default
            annotation = ""
            if param.annotation is not inspect._empty:
                annotation = getattr(param.annotation, "__name__", str(param.annotation))
            params[key] = {
                "default": default,
                "annotation": annotation,
                "required": param.default is inspect._empty,
            }
        return ToolSpec(
            name=name,
            category=self._category_for(name),
            description=first_line,
            parameters=params,
        )

    def _category_for(self, name: str) -> str:
        if name.startswith("drone_"):
            if "mission" in name:
                return "mission"
            if name in self.READ_ONLY_TOOLS:
                return "state"
            return "flight"
        if "photo" in name or "sensor" in name or "depth" in name or "detect" in name:
            return "perception"
        if "search" in name or "track" in name or "approach" in name or "task" in name:
            return "mission"
        return "tool"
