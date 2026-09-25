"""Execution backend registry for the local agent runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from src.config import config
from src.modules.flight_controller import FlightController


@dataclass(frozen=True)
class BackendCapabilities:
    """Capability flags exposed to the agent and future planners."""

    flight_control: bool = True
    telemetry: bool = True
    mode_control: bool = False
    gps: bool = False
    image_capture: bool = False
    depth_perception: bool = False
    object_detection: bool = False
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
            "object_detection": self.object_detection,
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
    from src.modules.airsim_controller import AirSimController

    return AirSimController()


def read_airsim_origin_geopoint() -> dict[str, float] | None:
    """从 AirSim settings.json 读 OriginGeopoint，不导入 airsim 包。

    刻意独立于 AirSimController：UI 要在仿真器还没起来时就知道地图该落在哪个
    城市，而 airsim 控制器模块顶层就 import airsim，没装仿真环境时导入即失败。
    """
    from pathlib import Path

    explicit = os.environ.get("AIRSIM_SETTINGS_PATH")
    if explicit:
        path = Path(explicit)
    else:
        home = Path(os.environ.get("USERPROFILE") or str(Path.home()))
        path = home / "Documents" / "AirSim" / "settings.json"
    try:
        import json

        origin = json.loads(path.read_text(encoding="utf-8")).get("OriginGeopoint")
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


def _create_px4_mavlink_controller() -> FlightController:
    from src.modules.mavlink_controller import MavlinkController

    return MavlinkController(
        connection_string=config.px4_connection_string,
        outdoor=config.outdoor_mode,
        max_velocity=config.max_velocity,
    )


def _create_px4_ros2_controller() -> FlightController:
    from src.modules.ros_gateway_controller import RosGatewayController

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
            default_connect_params={"ip": "127.0.0.1", "port": 41452},
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
                object_detection=True,
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
                object_detection=False,
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
                object_detection=False,
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
