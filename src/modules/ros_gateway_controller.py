"""FlightController implementation backed by the ROS Provider Gateway."""

from __future__ import annotations

import math
import time
from typing import Any

from src.agent.ros_provider_bridge import RosProviderBridgeClient, RosProviderBridgeConfig
from src.agent.workflow_ports import ProviderResult
from src.config import config
from src.modules.flight_controller import ConnectionInfo, DroneStatus, FlightController
from src.modules.px4_yaw_link import bearing_to_deg, heading_error_deg
from ..logging_config import get_logger

logger = get_logger(__name__)


class RosGatewayController(FlightController):
    """Controls PX4 through a ROS2 gateway running in WSL or onboard."""

    def __init__(
        self,
        base_url: str = "",
        endpoints: dict[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        timeout = float(timeout_sec if timeout_sec is not None else config.ros_bridge_timeout_sec)
        # 多机（多 gateway）：{vehicle_name: base_url}；单端点时用默认名 px4_ros2
        self._endpoints: dict[str, RosProviderBridgeClient] = {}
        self._default_name = "px4_ros2"
        if endpoints:
            self._default_name = next(iter(endpoints))
            for name, url in endpoints.items():
                self._endpoints[str(name)] = RosProviderBridgeClient(
                    RosProviderBridgeConfig(base_url=str(url).strip(), timeout_sec=timeout)
                )
        self.client = RosProviderBridgeClient(
            RosProviderBridgeConfig(
                base_url=(base_url or config.ros_bridge_url or "").strip(),
                timeout_sec=timeout,
            )
        )
        self._connected = False
        self.last_error = ""
        self._last_connection_info: dict[str, Any] = {
            "url": self.client.config.base_url,
            "requested_url": self.client.config.base_url,
            "backend": self.backend_name,
            "connected": False,
            "real_vehicle": False,
        }

    def _resolve_clients(self, vehicle_name: str = "") -> list[RosProviderBridgeClient]:
        """Map vehicle_name onto gateway clients (same semantics as MAVLink multi-system)."""
        if not self._endpoints:
            return [self.client]
        names = list(self._endpoints.keys())
        if not vehicle_name:
            return [self._endpoints[self._default_name]]
        name = str(vehicle_name).strip().lower()
        if name == "all":
            return list(self._endpoints.values())
        for key, client in self._endpoints.items():
            if name in {str(key).lower(), f"px4_ros2_{key}".lower()}:
                return [client]
        return [self._endpoints[self._default_name]]

    def _run_all(self, vehicle_name: str, action) -> bool:
        """Execute action(client) for each resolved gateway; all must succeed."""
        clients = self._resolve_clients(vehicle_name)
        ok = True
        for client in clients:
            if not self._ok(action(client)):
                ok = False
                break
        return ok

    @property
    def backend_name(self) -> str:
        return "px4_ros2"

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self, **kwargs: Any) -> ConnectionInfo:
        base_url = str(kwargs.get("base_url") or kwargs.get("url") or "").strip()
        if base_url:
            self.client = RosProviderBridgeClient(
                RosProviderBridgeConfig(base_url=base_url, timeout_sec=self.client.config.timeout_sec)
            )
        result = self.client.health()
        self._connected = bool(result.ok)
        self.last_error = "" if result.ok else result.message
        px4_status = result.data.get("px4") if isinstance(result.data.get("px4"), dict) else {}
        self._last_connection_info = {
            "url": self.client.config.base_url,
            "requested_url": base_url or self.client.config.base_url,
            "backend": self.backend_name,
            "connected": self._connected,
            "real_vehicle": False,
            "status": result.status,
            "message": result.message,
            "surface": "ROS2 Provider Gateway",
            "updated_at": time.time(),
            "px4_seen": bool(px4_status.get("px4_seen", False)),
            "last_local_position_age_s": px4_status.get("last_local_position_age_s"),
            "last_vehicle_status_age_s": px4_status.get("last_vehicle_status_age_s"),
        }
        return ConnectionInfo(
            backend=self.backend_name,
            connected=self._connected,
            details={
                "status": result.status,
                "message": result.message,
                "ros_bridge_url": self.client.config.base_url,
                **result.data,
            },
        )

    def disconnect(self) -> None:
        self._connected = False
        self._last_connection_info = {
            **self._last_connection_info,
            "connected": False,
            "updated_at": time.time(),
        }

    def arm(self, vehicle_name: str = "") -> bool:
        return self._run_all(vehicle_name, lambda client: client.px4_arm(True))

    def disarm(self, vehicle_name: str = "") -> bool:
        return self._run_all(vehicle_name, lambda client: client.px4_arm(False))

    def takeoff(self, altitude: float = 3.0, vehicle_name: str = "") -> bool:
        altitude_m = abs(float(altitude))
        return self._run_all(vehicle_name, lambda client: self._ok(
            client.px4_takeoff(
                {
                    "altitude_m": altitude_m,
                    "wait": True,
                    "timeout_sec": max(25.0, altitude_m * 8.0),
                }
            )
        ))

    def land(self, vehicle_name: str = "") -> bool:
        return self._run_all(vehicle_name, lambda client: client.px4_land({"wait": False}))

    def hover(self, vehicle_name: str = "") -> bool:
        return self._run_all(vehicle_name, lambda client: client.px4_hold({}))

    def _face_waypoint_before_move(self, x: float, y: float, vehicle_name: str = "") -> None:
        """移动前把机头转向目标方位，对齐 PX4 的 MPC_YAW_MODE=0 语义。

        px4_local_setpoint 不带航向字段，网关侧保持当前机头——与另外两个后端
        修复前的"锁死机头"问题同源。转向走 px4_rotate_to（网关的
        rotate_to_heading 原语），必须与移动串行。
        """
        if (config.yaw_mode or "face_waypoint").strip().lower() != "face_waypoint":
            return
        try:
            status = self.get_status(vehicle_name).to_dict()
            pos = status.get("position_ned") or {}
            cur_north = float(pos.get("x", 0.0) or 0.0)
            cur_east = float(pos.get("y", 0.0) or 0.0)
            # NED：x 是北、y 是东。bearing_to_deg 收的是 (东, 北)。
            delta_east = y - cur_east
            delta_north = x - cur_north
            # 目标就在正上方，没有朝向可言，别去转。
            if math.hypot(delta_east, delta_north) < 0.5:
                return
            target_bearing = bearing_to_deg(delta_east, delta_north)
            current = status.get("heading_deg")
            current = float(current) if isinstance(current, (int, float)) else None
            # 没有航向反馈就别盲转，转错了比不转更难收拾。
            if current is None:
                return
            if abs(heading_error_deg(current, target_bearing)) <= config.yaw_tolerance_deg:
                return
            self.rotate_to_heading(target_bearing, timeout=20.0, vehicle_name=vehicle_name)
        except Exception as exc:
            logger.debug("face-waypoint pre-rotation skipped: %s", exc)

    def move_to_position(
        self,
        x: float,
        y: float,
        z: float,
        velocity: float = 2.0,
        vehicle_name: str = "",
    ) -> bool:
        target = {"x": float(x), "y": float(y), "z": float(z)}
        speed = max(0.1, float(velocity))
        # 先转正机头再走（yaw_mode=face_waypoint 时），与另外两个后端一致
        self._face_waypoint_before_move(float(x), float(y), vehicle_name)
        timeout_sec = 45.0
        try:
            current = self.get_status().position_ned or {}
            distance = math.sqrt(
                (float(current.get("x", 0.0)) - target["x"]) ** 2
                + (float(current.get("y", 0.0)) - target["y"]) ** 2
                + (float(current.get("z", 0.0)) - target["z"]) ** 2
            )
            timeout_sec = max(15.0, min(120.0, distance / speed + 12.0))
        except Exception:
            pass
        return self._run_all(vehicle_name, lambda client: self._ok(
            client.px4_local_setpoint(
                {
                    **target,
                    "velocity": speed,
                    "wait": True,
                    "timeout_sec": timeout_sec,
                }
            )
        ))

    def move_by_velocity(
        self,
        vx: float,
        vy: float,
        vz: float,
        duration: float = 0.0,
        vehicle_name: str = "",
    ) -> bool:
        return self._run_all(vehicle_name, lambda client: self._ok(
            client.px4_velocity(
                {
                    "vx": float(vx),
                    "vy": float(vy),
                    "vz": float(vz),
                    "duration_sec": max(0.0, float(duration)),
                }
            )
        ))

    def move_on_path(self, waypoints: list[dict], velocity: float = 2.0, vehicle_name: str = "") -> bool:
        # px4_path 一次飞完整条路径，中途无法安全改转向，只对正首航点方位
        # （与 airsim 的 moveOnPathAsync 同一限制；多段折线只保证起飞腿）。
        if waypoints:
            first = waypoints[0]
            self._face_waypoint_before_move(
                float(first.get("x", 0) or 0),
                float(first.get("y", 0) or 0),
                vehicle_name,
            )
        return self._run_all(vehicle_name, lambda client: self._ok(
            client.px4_path(
                {
                    "waypoints": list(waypoints),
                    "velocity": max(0.1, float(velocity)),
                    "wait": True,
                }
            )
        ))

    def get_status(self, vehicle_name: str = "") -> DroneStatus:
        client = self._resolve_clients(vehicle_name)[0]
        result = client.px4_status()
        if not result.ok:
            self.last_error = result.message
            return DroneStatus(extra={"status": "error", "message": result.message, **result.data})
        self._connected = True
        data = result.data
        position = self._dict_float(data.get("position_ned"), {"x": 0.0, "y": 0.0, "z": 0.0})
        velocity = self._dict_float(data.get("velocity_ned"), {"vx": 0.0, "vy": 0.0, "vz": 0.0})
        attitude = self._dict_float(data.get("attitude_rad"), {"roll": 0.0, "pitch": 0.0, "yaw": 0.0})
        gps = data.get("gps") if isinstance(data.get("gps"), dict) else None
        battery = data.get("battery_voltage")
        extra = {k: v for k, v in data.items() if k not in {"position_ned", "velocity_ned", "attitude_rad", "gps", "battery_voltage"}}
        if "heading_deg" not in extra:
            yaw = attitude.get("yaw")
            if isinstance(yaw, (int, float)) and math.isfinite(float(yaw)):
                extra["heading_deg"] = round(math.degrees(float(yaw)) % 360.0, 3)
        self._last_connection_info = {
            **self._last_connection_info,
            "url": self.client.config.base_url,
            "requested_url": self._last_connection_info.get("requested_url") or self.client.config.base_url,
            "connected": self._connected,
            "status": result.status,
            "updated_at": time.time(),
            "px4_seen": bool(data.get("px4_seen", False)),
            "last_local_position_age_s": data.get("last_local_position_age_s"),
            "last_vehicle_status_age_s": data.get("last_vehicle_status_age_s"),
        }
        extra["active_link"] = self.get_connection_info()
        return DroneStatus(
            position_ned=position,
            velocity_ned=velocity,
            attitude_rad=attitude,
            armed=bool(data.get("armed", False)),
            flying=bool(data.get("flying", False)),
            mode=str(data.get("mode") or ""),
            gps=gps,
            battery_voltage=float(battery) if isinstance(battery, (int, float)) else None,
            extra=extra,
        )

    def list_vehicles(self) -> list[str]:
        if self._endpoints:
            return [f"px4_ros2_{name}" for name in self._endpoints]
        return ["px4_ros2"]

    def get_connection_info(self) -> dict[str, Any]:
        """Return the active ROS gateway endpoint for the settings UI."""

        return {
            **dict(self._last_connection_info),
            "url": self.client.config.base_url,
            "connected": self._connected,
            "backend": self.backend_name,
        }

    def set_mode(self, mode: str, vehicle_name: str = "") -> bool:
        return self._run_all(vehicle_name, lambda client: client.px4_set_mode({"mode": str(mode)}))

    def rotate_to_heading(self, heading_deg: float, timeout: float = 30.0, vehicle_name: str = "") -> bool:
        return self._run_all(vehicle_name, lambda client: self._ok(
            client.px4_rotate_to(
                {
                    "heading_deg": float(heading_deg) % 360.0,
                    "timeout_sec": max(0.0, float(timeout)),
                    "wait": True,
                }
            )
        ))

    def _ok(self, result: ProviderResult) -> bool:
        self.last_error = "" if result.ok else (result.message or result.status)
        if result.ok:
            self._connected = True
        return bool(result.ok)

    def _dict_float(self, value: Any, default: dict[str, float]) -> dict[str, float]:
        if not isinstance(value, dict):
            return dict(default)
        result = dict(default)
        for key in result:
            try:
                result[key] = float(value.get(key, result[key]))
            except (TypeError, ValueError):
                pass
        return result


def yaw_to_heading_rad(heading_deg: float) -> float:
    """Convert compass heading degrees to PX4 local yaw radians."""

    return math.radians(float(heading_deg) % 360.0)
