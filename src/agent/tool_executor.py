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
from src.modules.formation.controller import FLIGHT_ACTIONS, FormationController
from src.modules.safety_validator import FlightConstraint, SafetyValidator, ValidationResult
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
        "drone_download_mission",
        "drone_get_mission_progress",
        "airsim_get_sensors",
        "locate_image_point",
        "inspect_current_frame",
        "provider_bridge_health",
        "provider_obstacle_summary",
        "provider_validate_motion",
        "perception_status",
    }

    CONTROL_TOOLS = {
        "drone_arm",
        "drone_disarm",
        "drone_takeoff",
        "drone_land",
        "drone_hover",
        "drone_fly_to",
        "drone_move_relative",
        "drone_fly_path",
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
    _RETRYABLE_READ_TOOLS = READ_ONLY_TOOLS

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
    # 相机链路工具：失败是相机的问题，不代表飞控链路断开（见
    # _requires_vehicle_connection）。名字带 airsim_ 前缀，但读的是深度/几何，
    # 不依赖飞控指令通道；真机上相机往往根本不在飞控链路上。
    CAMERA_CHAIN_TOOLS = {"locate_image_point"}

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
        self.camera_key = ""
        self.camera_error = ""
        self._camera_lock = threading.RLock()
        # 预览编码记忆：同一帧只编码一次。面板在缓存路径下 100ms 轮询一次、
        # 感知轴只出 5Hz，实测一半以上请求拿到的是同一帧却各自重编码。
        self._preview_encode_cache: dict[str, Any] | None = None
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
            from src.tools.flight import register_flight_tools

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

            register_flight_tools(self.collector, self.controller, fmt)
            # 任务组单独一个文件（mission.py）：GCS 专用，模型面没有卡片。
            try:
                from src.tools.mission import register_mission_tools

                register_mission_tools(self.collector, self.controller, fmt)
            except Exception as exc:
                import logging

                logging.getLogger(__name__).warning(f"mission tools skipped: {exc}")

            # Optional tool groups: failures must not block core tools.
            # 图像工具跟着"相机源"走，不跟飞行后端绑死：
            #   - airsim 后端：相机与飞控同源，直接用飞控控制器注册；
            #   - 其他后端（px4_mavlink/px4_ros2）：用设置里选定的相机源
            #     （AirSim 仿真 / RTSP 图传 / 本机相机）自己的工具集并入。
            # 这样无论飞控走哪条链路，LLM 拿到的拍照工具都从当前相机源取图。
            # 只有"有相机源"才注册图像工具。旧写法还 OR 了 object_detection，
            # 那是检测时代的能力键，已随 YOLO 一起移除（见 backends.py 的说明）。
            if merged_capabilities.get("image_capture") and self.backend_id == "airsim":
                # 相机源不再注册模型面工具：看画面走感知轴的 inspect_current_frame，
                # 测距/算坐标走 locate_image_point，两者都与飞行后端解耦。
                try:
                    from src.tools.sensors import register_sensor_tools

                    register_sensor_tools(self.collector, self.controller, fmt)
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(f"perception tools skipped: {exc}")
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
            # 真机只暴露基本控制工具：感知/相机类工具一律不注册（卡片随之消失）。
            # 真机上没有仿真相机链路，"Agent 有相机能力"必须是假命题；操作员仍能
            # 在界面上看相机预览——那是感知轴自己的事，不是模型的工具面。
            perception_tools_allowed = not self._real_vehicle
            axis_configured = (
                perception_tools_allowed
                and self.perception_axis is not None
                and getattr(self.perception_axis, "enabled", False)
            )
            if perception_tools_allowed and (
                axis_configured or getattr(self, "vlm_provider", None) is not None
            ):
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
                        locate=self.locate_image_point,
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
            from src.modules.perception.airsim_adapter import _resolve_controller

            return _resolve_controller(provider)
        except Exception:
            return None

    def locate_image_point(
        self,
        x_norm: float,
        y_norm: float,
        vehicle_name: str = "",
        hint_w: float = 0.0,
        hint_h: float = 0.0,
        extra_points: list[tuple[float, float]] | None = None,
    ) -> dict[str, Any]:
        """归一化图像点 → 距离 + 世界坐标（``locate_image_point`` 的实现）。

        纯读取：深度图 + 无人机位姿 → NED。几何只有一份实现在
        ``perception_airsim_adapter.project_point_to_world`` 里；这里只负责解析
        相机名，以及"视觉模型归一化坐标的基准画幅"——视觉模型看到的是感知轴的
        彩色帧，深度图分辨率/纵向视场与它不同，纵向必须按角度映射，所以基准画幅
        取自感知轴快照（frame_width/frame_height）。
        """
        controller = self._perception_camera_controller()
        if controller is None:
            return {
                "valid": False,
                "reason": "no_controller",
                "message": "当前后端没有可用的深度相机（本工具依赖 AirSim 深度链路）。",
            }
        axis = getattr(self, "perception_axis", None)
        profile = getattr(axis, "profile", None)
        camera_name = str(getattr(profile, "camera_name", "") or "0")
        frame_shape: tuple[int, int] | None = None
        try:
            snap = axis.snapshot() or {} if axis is not None else {}
        except Exception:
            snap = {}
        frame_w = int(snap.get("frame_width") or 0)
        frame_h = int(snap.get("frame_height") or 0)
        if frame_w > 0 and frame_h > 0:
            frame_shape = (frame_h, frame_w)
        try:
            from src.modules.perception.airsim_adapter import project_point_to_world

            return project_point_to_world(
                frame_provider=controller,
                x_norm=float(x_norm),
                y_norm=float(y_norm),
                camera_name=camera_name,
                vehicle_name=str(vehicle_name or ""),
                frame_shape=frame_shape,
                hint_w=float(hint_w or 0.0),
                hint_h=float(hint_h or 0.0),
                extra_points=list(extra_points or []),
            )
        except Exception as exc:  # noqa: BLE001 — 给模型一句可执行的话，不抛穿
            return {"valid": False, "reason": "projection_error", "message": f"点位投影失败: {exc}"}

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
            # 取景随遥测下发：相机面板据此把下拉框默认成"轴正在看的那一路"，
            # 而不是各自默认 0 号相机，一开就分叉。
            "view": health.get("view") or snapshot.get("view") or {},
            "preview": snapshot.get("preview") or {},
        }

    def _camera_capabilities(self, capabilities: dict[str, Any]) -> dict[str, Any]:
        merged = dict(capabilities or {})
        settings = self._camera_settings()
        source = str(settings.get("source") or "").lower()
        # 真机（real_vehicle）不向模型暴露任何感知/相机能力：工具不注册、卡片不出现。
        if self._real_vehicle:
            merged["camera_source"] = source
            merged["image_capture"] = False
            merged["target_search"] = False
            merged["target_tracking"] = False
            merged["depth_perception"] = False
            merged["image_capture_via"] = "none"
            return merged
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
            merged["target_search"] = axis_enabled
            merged["target_tracking"] = axis_enabled
            # 深度图依赖 AirSim 仿真相机通道；真机形态下由机载感知提供，
            # 这里仅在仿真相机源可用时置位。
            merged["depth_perception"] = bool(axis_enabled and source == "airsim")
            if axis_enabled and source == "airsim":
                merged["camera_host"] = settings.get("host", "127.0.0.1")
                merged["camera_port"] = settings.get("port", config.airsim_port)
            return merged
        if source == "airsim":
            merged["image_capture"] = True
            merged["depth_perception"] = True
            merged["camera_source"] = "airsim"
            merged["camera_host"] = settings.get("host", "127.0.0.1")
            merged["camera_port"] = settings.get("port", config.airsim_port)
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

    def _ensure_preview_controller(self, params: dict[str, Any] | None = None) -> tuple[Any | None, str]:
        """Build (or reuse) a camera controller for a single UI preview frame.

        It honours an optional per-request ``source``/``url`` override so
        multiple viewer windows can display *different* sources at the same
        time — e.g. one window on the AirSim drone, another on a real drone's
        RTSP stream for digital-twin work.

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
                port = int(raw.get("port") or settings.get("port") or config.airsim_port)
            except (TypeError, ValueError):
                port = config.airsim_port
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
                    from src.modules.backends.airsim import AirSimController

                    controller = AirSimController(ip=host, port=port)
                    info = controller.connect(ip=host, port=port)
                elif source == "rtsp":
                    from src.modules.perception.rtsp_camera import RtspCameraController

                    controller = RtspCameraController(
                        url,
                        transport=rtsp_transport,
                        open_timeout_sec=rtsp_timeout,
                    )
                    info = controller.connect()
                else:
                    from src.modules.perception.rtsp_camera import LocalCameraController

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

    # 视觉抵近武器库（fallback target / 目标距离估计 / 前向避障 / 自动对准 /
    # approach_target_step）随 drone_approach_target 一起删除：运行时不再持有任何
    # 抵近策略。靠近由模型按技能用"VLM 取点 + locate_image_point（多点测距）+
    # 有界 drone_move_relative"完成；净空判断写进技能，用多点距离读数做。


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

        Preview frames are the UI's own fast path (shared cached frame or a
        direct grab) — no still-frame stationary check, no retry/cooldown loop,
        no base64 JSON. Agent visual reasoning still uses governed tools.
        """
        raw_params = dict(params or {})
        settings = self._camera_settings()
        source = str(raw_params.get("source") or settings.get("source") or "airsim").strip().lower()
        req_camera = str(raw_params.get("camera_name") or settings.get("camera_name") or "0").strip()
        req_image_type = str(raw_params.get("image_type") or settings.get("image_type") or "scene").strip().lower()
        axis = getattr(self, "perception_axis", None)
        axis_view: dict[str, Any] = {}
        if axis is not None:
            try:
                axis_view = axis.view() or {}
            except Exception:
                axis_view = {}
        # 判断"面板要的是不是感知轴正在看的那一路"要跟轴自己报的相机名比，不能
        # 再拿 {"", "0"} 猜：配置里相机叫 CameraImage，猜法的结果是"0"被当成轴
        # 那一路（于是显示的是轴的画面、报的名字却是 0），而显式填 CameraImage
        # 反倒被判成另一路、掉进直连抓帧。空串仍表示"按轴的当前相机走"。
        axis_camera = str(axis_view.get("camera_name") or "")
        # 轴没报相机名时无法与它比对，就按"面板要的就是轴那一路"处理：轴的帧本来
        # 就是 Agent 在看的那份，宁可回放它也不要无谓地去开第二条取流。
        camera_matches = (not axis_camera) or req_camera in {"", axis_camera}
        axis_view_matches = camera_matches and req_image_type in {"", "scene"}
        # 面板看的就是轴上那一路时，预览**一律**回放轴的缓存：感知轴是唯一的 AirSim
        # 帧源（UI 与 Agent 消费同一份），再拉一次模拟器就是和轴的采集线程抢单线程
        # RPC。走直连抓帧那条路每次要几秒，表现出来正是"出图率和面板 FPS 一起塌到
        # ~1 FPS"。
        #
        # 这里以前额外要求 `detect=1`——那是"面板要带检测框的标注帧"的年代留下的
        # 开关（面板为了画框才传它）。检测已从产品中移除、标注框也不存在了，那个门
        # 就退化成了纯粹的性能回归开关：前端一停止传它，预览立刻退回阻塞直连。护栏
        # 现在落在行为上（默认请求就必须走缓存），见
        # tests/test_perception_axis.py::test_preview_uses_axis_cache_without_detect_param。
        if source == "airsim" and axis_view_matches and axis is not None:
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
                body, mime = self._encoded_preview_for_frame(
                    jpeg, ts, max_width=max_width, quality=quality
                )
                meta: dict[str, Any] = {
                    "status": "ok",
                    "vehicle": "perception-axis",
                    # 报轴实际在用的相机名，而不是请求里那个字符串：这一帧就是从
                    # 那路相机来的，meta 必须说实话（此前恒等于请求值，出现过
                    # "画面来自 CameraImage、meta 写 0"）。
                    "camera": axis_camera or req_camera or "0",
                    "image_type": "scene",
                    "size_kb": round(len(body) / 1024, 1),
                    "source": "airsim",
                    "detections": list(dets),
                    # 框的来源：感知轴的连续检测（有 track_id、会做时序关联）还是
                    # 面板直连抓帧时的一次性检测。两者可信度不同，前端和 Agent
                    # 都要能区分。
                    "detection_source": "axis",
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
                try:
                    axis.note_preview(
                        camera_name=axis_camera or req_camera,
                        image_type="scene",
                        source="airsim",
                        detection_source="axis",
                        detections=list(dets),
                        target_class=str(getattr(axis.profile, "target_class", "") or ""),
                    )
                except Exception:
                    pass
                return True, body, mime, meta
        self._start_preview_reaper()
        raw_params = dict(params or {})
        # AirSim 上优先复用感知轴那个控制器：另开一条连接会和轴抢仿真器单线程
        # 的 RPC（实测表现为深度一直读不到、出图率塌陷），共用一个控制器时请求
        # 只是在它的执行锁上排队。RTSP/本机相机不存在这个竞争，各走各的。
        controller = self._perception_camera_controller() if source == "airsim" else None
        error = ""
        if controller is None:
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
            preview_bytes = bytes(raw)
            # 这条路**不做检测**：框只有感知轴一个来源。
            #
            # 这里原本会跑一次性单帧检测，代价是把"检测有几处事实来源"变成两处，
            # 而且很贵——实测一次单帧检测 1.3s（抖到 14s），叠上抓帧 0.4~2s，再
            # 和感知轴自己的 5fps 抓帧、2Hz 推理抢同一台机器，整帧能拖到十几到
            # 四十几秒（前端每帧只等 3.6s，于是画面永远刷不出来）。
            #
            # 没有它也不会丢能力：要看这一路的框，把感知轴切到这台相机即可（面板
            # 换一下相机），那条路是 13ms 的缓存回放，还带 track_id 和世界坐标。
            body, mime_type = self._encode_preview_frame(preview_bytes, max_width=max_width, quality=quality)
            meta: dict[str, Any] = {
                "status": "ok",
                "vehicle": vehicle,
                "camera": camera_name,
                "image_type": image_type_name,
                "size_kb": round(len(body) / 1024, 1),
                "source": source,
                "detection_source": "none",
                "detections": [],
                # 这条路的框永远为空，得让面板说清原因：false = 这一路不是感知轴
                # 在看的那台相机（要看它的框就把感知轴切过去）。RTSP/USB 源上相机
                # 名不构成"另一路"，那种情况不报 false。
                "matches_axis_view": bool(axis_view_matches),
            }
            # 记下"操作员在看哪一路"：不记的话 Agent 完全不知道面板已经切走了。
            self._note_preview_observation(
                camera_name=camera_name,
                image_type=image_type_name,
                source=source,
                detections=[],
                detection_source="",
            )
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

    def _encoded_preview_for_frame(
        self, jpeg: bytes, frame_ts: Any, *, max_width: int, quality: int
    ) -> tuple[bytes, str]:
        """同一帧的预览编码只做一次，重复请求直接复用字节。

        面板在缓存路径下 100ms 轮询一次（10Hz），而感知轴只出 5Hz——实测 83 次
        请求里 44 次拿到的是同一帧，却各自重新编码一遍（20.5ms/次，合计约 0.17
        颗核）。这些 CPU 和采集、YOLO 推理抢的是同一台机器，直接放大出帧抖动，
        所以必须复用。键里带上尺寸参数：两个面板用不同尺寸时各自算各自的。
        """
        key = (round(float(frame_ts or 0.0), 6), len(jpeg), int(max_width), int(quality))
        cached = getattr(self, "_preview_encode_cache", None)
        if isinstance(cached, dict) and cached.get("key") == key:
            return cached["body"], cached["mime"]
        body, mime = self._encode_preview_frame(jpeg, max_width=max_width, quality=quality)
        self._preview_encode_cache = {"key": key, "body": body, "mime": mime}
        return body, mime

    def _note_preview_observation(
        self,
        *,
        camera_name: str,
        image_type: str,
        source: str,
        detections: list[dict[str, Any]],
        detection_source: str,
    ) -> None:
        """把"面板此刻在看什么"写进感知轴，让 Agent 也能看到。

        只记录事实，不碰 targets：面板旁看的画面没有 track_id、没有世界坐标、
        没有经过轴的时序关联，混进去会让模型把它当成自己检测到的目标。
        """
        note = getattr(getattr(self, "perception_axis", None), "note_preview", None)
        if not callable(note):
            return
        try:
            note(
                camera_name=camera_name,
                image_type=image_type,
                source=source,
                detection_source=detection_source,
                detections=list(detections or []),
                target_class="",
            )
        except Exception:
            pass  # 记录观测失败不能拖垮取景

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
        if "formation_command" in collector.tools:
            available.add("formation_command")
        capabilities = self._camera_capabilities(backend_profile.capabilities.to_dict())
        from .tool_cards import cards_for_capabilities

        return cards_for_capabilities(capabilities, available)

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
        real_vehicle: bool | None = None,
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
                elif self._last_connect_params:
                    params = dict(self._last_connect_params)
            else:
                if ip is not None or port is not None:
                    params.update({
                        "ip": ip or params.get("ip", "127.0.0.1"),
                        "port": int(port or params.get("port", config.airsim_port)),
                    })
                elif self._last_connect_params:
                    params = dict(self._last_connect_params)
            # real_vehicle 必须始终落在 params 里。原实现只在传了 url 的分支
            # 写入，其余路径（无参重连、只给 ip/port）会退回 default_connect_params
            # 里的缺省 False——真机上这等于**悄悄关掉审批门**，模型从此可以不
            # 经确认直接挪飞机。调用方没显式说明时沿用当前值，而不是清零。
            if real_vehicle is None:
                resolved = bool(params.get("real_vehicle", self._real_vehicle))
            else:
                resolved = bool(real_vehicle)
            params["real_vehicle"] = resolved
            self._last_connect_params = dict(params)
            self._real_vehicle = resolved
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
            and name not in {"drone_hover", "drone_land", "drone_get_status", "drone_disconnect", "drone_connect"}
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

        if name == "drone_disarm":
            # 空中解锁等于掉机。审批门只能让人点"确认"，拦不住模型误判状态；
            # 真正的兜底得看飞机自己是不是在地上。落地判定不确定时按"不安全"
            # 处理——宁可多要一次确认，也不要在 20m 高空解锁。
            airborne = self._is_vehicle_airborne()
            if airborne is not False:
                merge(ValidationResult(
                    is_safe=False,
                    violations=[
                        "真机空中禁止解锁：解锁会导致自由落体"
                        + ("" if airborne is None else f"（当前高度 {airborne:.1f}m）")
                        + "。请先降落，确认 landed 后再解锁。"
                    ],
                    level="danger",
                ))

        elif name == "drone_takeoff":
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
                waypoints = json.loads(str(params.get("waypoints", "[]")))
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
                    corrected["waypoints"] = json.dumps(safe_waypoints, ensure_ascii=False)
            except Exception as e:
                level = "danger"
                violations.append(f"waypoint JSON could not be parsed: {e}")

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
            # 围栏圆心必须跟着飞控的真实 home 走，否则真机上围的是 EKF 原点。
            self._sync_geofence_home()
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
                "flight_tasks": {},
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

    def _is_vehicle_airborne(self) -> bool | None:
        """飞机是否在空中。

        True=在空中, False=确认在地面, None=**判不出来**（无遥测或字段缺失）。
        返回 None 而不是猜 False，是因为"判不出来就当安全"正好把守卫变成装饰。
        高度和 landed_state 两路独立信号交叉判断：只信 armed 会漏掉
        "已解锁但仍停在地上"和"解锁状态丢失但实际在飞"两种情况。
        """
        controller = self.controller
        if controller is None:
            return None
        try:
            status = controller.get_status()
        except Exception:
            return None
        data = status.to_dict() if hasattr(status, "to_dict") else {}
        if not isinstance(data, dict) or not data:
            return None

        # 明确的落地状态最可信，飞控自己说 landed 就认
        landed = data.get("landed_state")
        if isinstance(landed, str) and landed.strip().lower() in {"landed", "on_ground", "ground"}:
            return False
        if isinstance(landed, int) and landed == 2:  # MAV_LANDED_STATE_LANDED
            return False

        pos = data.get("position_ned") or {}
        try:
            altitude = abs(float(pos.get("z", 0.0) or 0.0))
        except (TypeError, ValueError):
            altitude = None
        if altitude is not None and altitude > 0.5:
            return True

        # 高度贴着地，但飞行标志还亮着——正在起降途中。此时解锁同样危险
        #（下一条指令就可能拉起来），所以按 fail-safe 判成空中。
        flying = data.get("flying")
        if flying is True:
            return True

        if altitude is not None:
            return False
        return None

    def _sync_geofence_home(self) -> None:
        """把飞控的真实 home 喂给安全约束，让围栏以 home 为圆心。

        FlightConstraint.home_position 原本固定在 (0,0)——那是 EKF 本地原点，不
        是操作员的家。仿真里两者常常重合，看不出问题；真机上 home 往往离 EKF
        原点几百米，于是"离 home 100m"的围栏实际上圈在另一个地方，飞机飞出
        作业区也不会报警。

        取不到 home 时保持原值不动：宁可沿用上一次的有效圆心，也不要退回
        EKF 原点假装一切正常。
        """
        controller = self.controller
        if controller is None:
            return
        for name in ("home_position_ned", "home_position"):
            getter = getattr(controller, name, None)
            if not callable(getter):
                continue
            try:
                home = getter()
            except Exception:
                continue
            if not isinstance(home, dict):
                continue
            try:
                x = float(home.get("x", 0.0) or 0.0)
                y = float(home.get("y", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            current = self.safety.constraints.home_position
            if (x, y) != tuple(current):
                self.safety.constraints.home_position = (x, y)
            return

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
            "flight_tasks": {},
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
        if name in {"drone_connect", "drone_disconnect"}:
            return False
        if name in self.CAMERA_CHAIN_TOOLS:
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
