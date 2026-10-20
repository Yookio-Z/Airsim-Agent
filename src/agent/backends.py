"""Execution backend registry for the local agent runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from src.config import config
from src.modules.backends.base import FlightController


@dataclass(frozen=True)
class BackendCapabilities:
    """Capability flags exposed to the agent and future planners."""

    flight_control: bool = True
    telemetry: bool = True
    mode_control: bool = False
    gps: bool = False
    image_capture: bool = False
    depth_perception: bool = False
    # 注意：这里**没有** object_detection —— 产品已放弃目标检测（2026-10-13 决议）。
    # "能不能搜/盯"由 target_search / target_tracking 表达，语义由视觉模型提供，
    # 不再隐含"有检测器"。旧键若被重新加回来，会让人以为检测还在。
    target_search: bool = False
    target_tracking: bool = False
    obstacle_avoidance: bool = False
    multi_vehicle: bool = False
    ros2_topics: bool = False
    real_vehicle: bool = False
    # P5 safety fields:
    # simulated_vehicle: True for AirSim / SITL (no real hardware at risk).
    # requires_operator_approval: True when high-risk tools must wait for human
    #   confirmation before execution. Real-vehicle profiles should set this True;
    #   simulation profiles may leave it False for fast iteration but can opt in.
    simulated_vehicle: bool = True
    requires_operator_approval: bool = False
    custom: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "flight_control": self.flight_control,
            "telemetry": self.telemetry,
            "mode_control": self.mode_control,
            "gps": self.gps,
            "image_capture": self.image_capture,
            "depth_perception": self.depth_perception,
            "target_search": self.target_search,
            "target_tracking": self.target_tracking,
            "obstacle_avoidance": self.obstacle_avoidance,
            "multi_vehicle": self.multi_vehicle,
            "ros2_topics": self.ros2_topics,
            "real_vehicle": self.real_vehicle,
            "simulated_vehicle": self.simulated_vehicle,
            "requires_operator_approval": self.requires_operator_approval,
        }
        data.update(self.custom)
        return data

    def supported_tool_cards(self, available_tool_names: set[str] | None = None) -> list[dict[str, Any]]:
        from .tool_cards import cards_for_capabilities

        return cards_for_capabilities(self.to_dict(), available_tool_names)


ControllerFactory = Callable[[], FlightController]
MapOriginProvider = Callable[[], dict[str, float] | None]


@dataclass(frozen=True)
class BackendProfile:
    """A runnable backend plus its declared capabilities."""

    id: str
    name: str
    description: str
    controller_factory: ControllerFactory
    capabilities: BackendCapabilities
    default_connect_params: dict[str, Any] = field(default_factory=dict)
    mode: str = "simulation"
    control_path: str = ""
    requires_ros_gateway: bool = False
    agent_settings: dict[str, Any] = field(default_factory=dict)
    # NED 原点经纬度：仿真后端用它告诉 UI 地图场景所在城市。刻意不要求链路已
    # 连接——没连上仿真器时地图也该落在正确的城市，而不是默认的北京。
    map_origin_provider: MapOriginProvider | None = None

    def create_controller(self) -> FlightController:
        return self.controller_factory()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "capabilities": self.capabilities.to_dict(),
            "default_connect_params": dict(self.default_connect_params),
            "mode": self.mode,
            "control_path": self.control_path,
            "requires_ros_gateway": self.requires_ros_gateway,
            "agent_settings": dict(self.agent_settings),
        }


class BackendRegistry:
    """Registry of available execution backends."""

    def __init__(self) -> None:
        self._profiles: dict[str, BackendProfile] = {}

    def register(self, profile: BackendProfile) -> None:
        self._profiles[profile.id] = profile

    def get(self, backend_id: str) -> BackendProfile | None:
        return self._profiles.get(backend_id)

    def require(self, backend_id: str) -> BackendProfile:
        profile = self.get(backend_id)
        if profile is None:
            known = ", ".join(sorted(self._profiles)) or "none"
            raise ValueError(f"unknown backend '{backend_id}'. Available backends: {known}")
        return profile

    def resolve_id(self, backend_id: str | None = None) -> str:
        requested = (
            backend_id
            or os.environ.get("AIRSIM_AGENT_BACKEND")
            or os.environ.get("DRONE_AGENT_BACKEND")
            or "airsim"
        )
        normalized = requested.strip().lower().replace("-", "_")
        aliases = {
            "px4": "px4_mavlink",
            "mavlink": "px4_mavlink",
            "sitl": "px4_mavlink",
            "ros": "px4_ros2",
            "ros2": "px4_ros2",
            "px4_ros": "px4_ros2",
        }
        return aliases.get(normalized, normalized)

    def list_public(self) -> list[dict[str, Any]]:
        return [p.to_public_dict() for p in self._profiles.values()]


def _create_airsim_controller() -> FlightController:
    from src.modules.backends.airsim import AirSimController

    return AirSimController()


def _airsim_settings_path():
    """AirSim settings.json 位置（与 AirSimController 同一套规则）。"""
    from pathlib import Path

    explicit = os.environ.get("AIRSIM_SETTINGS_PATH")
    if explicit:
        return Path(explicit)
    home = Path(os.environ.get("USERPROFILE") or str(Path.home()))
    return home / "Documents" / "AirSim" / "settings.json"


def read_airsim_origin_geopoint() -> dict[str, float] | None:
    """从 AirSim settings.json 读 OriginGeopoint，不导入 airsim 包。

    刻意独立于 AirSimController：UI 要在仿真器还没起来时就知道地图该落在哪个
    城市，而 airsim 控制器模块顶层就 import airsim，没装仿真环境时导入即失败。
    """
    try:
        import json

        origin = json.loads(_airsim_settings_path().read_text(encoding="utf-8")).get("OriginGeopoint")
        if not isinstance(origin, dict):
            return None
        lat = float(origin.get("Latitude"))
        lon = float(origin.get("Longitude"))
    except Exception:
        return None
    if abs(lat) < 0.001 or abs(lon) < 0.001:
        return None
    try:
        alt = float(origin.get("Altitude", 0.0) or 0.0)
    except (TypeError, ValueError):
        alt = 0.0
    return {"lat": lat, "lon": lon, "alt": alt}


# AirSim 没有"列出相机"的 RPC；settings.json 里没写 Cameras 时，模拟器建的是
# 这五个默认相机（前/前右/前左/下/后）。
DEFAULT_AIRSIM_CAMERAS: tuple[str, ...] = ("0", "1", "2", "3", "4")


def read_airsim_camera_catalog() -> dict[str, Any]:
    """settings.json 里每架载具实际声明的相机名。

    事实来源是配置文件，不是面板里写死的一份列表：
      - 载具写了 ``Cameras`` 块 → 只有这些相机存在；
      - 没写 → AirSim 建默认相机 ``0``..``4``。
    ``CaptureSettings`` 显式为空的相机不会出图（例如只留位不采集的
    ``CameraDepth``），不列入可选列表；若整个 Cameras 块都不能采集，则保留
    全部名字，让面板能如实展示配置而不是给出空列表。
    """
    import json

    fallback = {"by_vehicle": {}, "default": list(DEFAULT_AIRSIM_CAMERAS)}
    try:
        data = json.loads(_airsim_settings_path().read_text(encoding="utf-8"))
    except Exception:
        return fallback
    vehicles = data.get("Vehicles")
    if not isinstance(vehicles, dict):
        return fallback

    by_vehicle: dict[str, list[str]] = {}
    for vehicle_name, spec in vehicles.items():
        if not isinstance(spec, dict):
            continue
        cameras = spec.get("Cameras")
        if isinstance(cameras, dict) and cameras:
            names = [
                str(camera_name)
                for camera_name, camera_spec in cameras.items()
                if not (
                    isinstance(camera_spec, dict)
                    and isinstance(camera_spec.get("CaptureSettings"), list)
                    and not camera_spec.get("CaptureSettings")
                )
            ]
            by_vehicle[str(vehicle_name)] = names or [str(name) for name in cameras]
        else:
            by_vehicle[str(vehicle_name)] = list(DEFAULT_AIRSIM_CAMERAS)
    first = next(iter(by_vehicle.values()), None)
    return {"by_vehicle": by_vehicle, "default": list(first) if first else list(DEFAULT_AIRSIM_CAMERAS)}


def _create_px4_mavlink_controller() -> FlightController:
    from src.modules.backends.mavlink import MavlinkController

    return MavlinkController(
        connection_string=config.px4_connection_string,
        outdoor=config.outdoor_mode,
        max_velocity=config.max_velocity,
    )


def _create_px4_ros2_controller() -> FlightController:
    from src.modules.backends.ros_gateway import RosGatewayController

    return RosGatewayController(base_url=_ros_bridge_url())


def _ros_bridge_url() -> str:
    return (
        os.environ.get("AIRSIM_AGENT_ROS_BRIDGE_URL")
        or os.environ.get("DRONE_ROS_BRIDGE_URL")
        or config.ros_bridge_url
        or ""
    ).strip()


def create_builtin_backend_registry() -> BackendRegistry:
    registry = BackendRegistry()
    ros_bridge_url = _ros_bridge_url()
    ros_bridge_enabled = bool(ros_bridge_url)
    ros_custom = {"ros_bridge_url": ros_bridge_url} if ros_bridge_enabled else {}
    registry.register(
        BackendProfile(
            id="airsim",
            name="AirSim",
            description="AirSim RPC simulation backend with flight and perception tools.",
            controller_factory=_create_airsim_controller,
            default_connect_params={"ip": "127.0.0.1", "port": config.airsim_port},
            map_origin_provider=read_airsim_origin_geopoint,
            mode="airsim",
            control_path="AirSim RPC",
            requires_ros_gateway=False,
            agent_settings={
                "tool_scope": "AirSim flight, camera, depth, and perception tools.",
                "memory_scope": "Simulation task memory and visual observations.",
                "skill_scope": "Navigation, visual observe, search, and return-home skills.",
            },
            capabilities=BackendCapabilities(
                flight_control=True,
                telemetry=True,
                mode_control=False,
                gps=False,
                image_capture=True,
                depth_perception=True,
                target_search=True,
                target_tracking=True,
                obstacle_avoidance=True,
                multi_vehicle=True,
                ros2_topics=False,
                real_vehicle=False,
            ),
        )
    )
    registry.register(
        BackendProfile(
            id="px4_mavlink",
            name="PX4 MAVLink",
            description="PX4 SITL or vehicle backend through pymavlink.",
            controller_factory=_create_px4_mavlink_controller,
            default_connect_params={"url": config.px4_connection_string},
            mode="px4_mavlink",
            control_path="MAVLink",
            requires_ros_gateway=False,
            agent_settings={
                "tool_scope": "PX4 link, telemetry, modes, commands, and missions through MAVLink.",
                "memory_scope": "Flight command memory, mission uploads, and PX4 link diagnostics.",
                "skill_scope": "Navigation and return-home only; visual and ROS providers are not active.",
            },
            capabilities=BackendCapabilities(
                flight_control=True,
                telemetry=True,
                mode_control=True,
                gps=True,
                image_capture=False,
                depth_perception=False,
                target_search=False,
                target_tracking=False,
                obstacle_avoidance=False,
                ros2_topics=False,
                real_vehicle=False,
            ),
        )
    )
    registry.register(
        BackendProfile(
            id="px4_ros2",
            name="PX4 ROS2 Gateway",
            description="PX4 control through a ROS2 Provider Gateway running in WSL or onboard.",
            controller_factory=_create_px4_ros2_controller,
            default_connect_params={"url": ros_bridge_url},
            mode="px4_ros2",
            control_path="ROS2 Provider Gateway -> PX4 /fmu topics",
            requires_ros_gateway=True,
            agent_settings={
                "tool_scope": "PX4 ROS2 offboard control plus provider diagnostics and validation.",
                "memory_scope": "ROS provider health, PX4 topic status, and closed-loop verification.",
                "skill_scope": "Navigation can execute through ROS2; obstacle/planning skills use providers when adapters are configured.",
                "ros_workspace": config.ros_workspace_path,
                "ros_gateway_url": ros_bridge_url,
            },
            capabilities=BackendCapabilities(
                flight_control=True,
                telemetry=True,
                mode_control=True,
                gps=True,
                image_capture=False,
                depth_perception=True,
                target_search=False,
                target_tracking=False,
                obstacle_avoidance=True,
                ros2_topics=True,
                real_vehicle=False,
                custom=ros_custom,
            ),
        )
    )
    return registry
