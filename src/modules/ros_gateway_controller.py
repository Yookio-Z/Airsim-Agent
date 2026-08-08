"""FlightController implementation backed by the ROS Provider Gateway."""

from __future__ import annotations

import math
import time
from typing import Any

from src.agent.ros_provider_bridge import RosProviderBridgeClient, RosProviderBridgeConfig
from src.agent.workflow_ports import ProviderResult
from src.config import config
from src.modules.flight_controller import ConnectionInfo, DroneStatus, FlightController


class RosGatewayController(FlightController):
    """Controls PX4 through a ROS2 gateway running in WSL or onboard."""

    def __init__(self, base_url: str = "", timeout_sec: float | None = None) -> None:
        timeout = float(timeout_sec if timeout_sec is not None else config.ros_bridge_timeout_sec)
        bridge_config = RosProviderBridgeConfig(
            base_url=(base_url or config.ros_bridge_url or "").strip(),
            timeout_sec=timeout,
        )
        self.client = RosProviderBridgeClient(bridge_config)
        self._connected = False
        self.last_error = ""
        self._last_connection_info: dict[str, Any] = {
            "url": self.client.config.base_url,
            "requested_url": self.client.config.base_url,
            "backend": self.backend_name,
            "connected": False,
            "real_vehicle": False,
        }

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
        return self._ok(self.client.px4_arm(True))

    def disarm(self, vehicle_name: str = "") -> bool:
        return self._ok(self.client.px4_arm(False))

    def takeoff(self, altitude: float = 3.0, vehicle_name: str = "") -> bool:
        altitude_m = abs(float(altitude))
        return self._ok(
            self.client.px4_takeoff(
                {
                    "altitude_m": altitude_m,
                    "wait": True,
                    "timeout_sec": max(25.0, altitude_m * 8.0),
                }
            )
        )

    def land(self, vehicle_name: str = "") -> bool:
        return self._ok(self.client.px4_land({"wait": False}))

    def hover(self, vehicle_name: str = "") -> bool:
        return self._ok(self.client.px4_hold({}))

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
        return self._ok(
            self.client.px4_local_setpoint(
                {
                    **target,
                    "velocity": speed,
                    "wait": True,
                    "timeout_sec": timeout_sec,
                }
            )
        )

    def move_by_velocity(
        self,
        vx: float,
        vy: float,
        vz: float,
        duration: float = 0.0,
        vehicle_name: str = "",
    ) -> bool:
        return self._ok(
            self.client.px4_velocity(
                {
                    "vx": float(vx),
                    "vy": float(vy),
                    "vz": float(vz),
                    "duration_sec": max(0.0, float(duration)),
                }
            )
        )

    def move_on_path(self, waypoints: list[dict], velocity: float = 2.0, vehicle_name: str = "") -> bool:
        return self._ok(
            self.client.px4_path(
                {
                    "waypoints": list(waypoints),
                    "velocity": max(0.1, float(velocity)),
                    "wait": True,
                }
            )
        )

    def get_status(self, vehicle_name: str = "") -> DroneStatus:
        result = self.client.px4_status()
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
        return ["px4_ros2"]

    def get_connection_info(self) -> dict[str, Any]:
        """Return the active ROS gateway endpoint for the settings UI."""

        return {
            **dict(self._last_connection_info),
            "url": self.client.config.base_url,
            "connected": self._connected,
            "backend": self.backend_name,
        }

    def set_mode(self, mode: str) -> bool:
        return self._ok(self.client.px4_set_mode({"mode": str(mode)}))

    def rotate_to_heading(self, heading_deg: float, timeout: float = 30.0) -> bool:
        return self._ok(
            self.client.px4_rotate_to(
                {
                    "heading_deg": float(heading_deg) % 360.0,
                    "timeout_sec": max(0.0, float(timeout)),
                    "wait": True,
                }
            )
        )

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
