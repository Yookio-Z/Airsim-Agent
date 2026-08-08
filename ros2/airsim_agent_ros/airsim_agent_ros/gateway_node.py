"""ROS2 Provider Gateway for PX4 and algorithm adapters.

Run in WSL or on an onboard companion computer. The Windows ground station talks
to this process over HTTP, while this node talks to PX4 and ROS packages through
normal ROS2 topics.
"""

from __future__ import annotations

import argparse
from collections import deque
import json
import math
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

try:  # pragma: no cover - imported in ROS environments.
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
except ModuleNotFoundError:  # pragma: no cover - allows syntax checks outside ROS.
    rclpy = None
    Node = object  # type: ignore[assignment]
    DurabilityPolicy = None  # type: ignore[assignment]
    HistoryPolicy = None  # type: ignore[assignment]
    QoSProfile = None  # type: ignore[assignment]
    ReliabilityPolicy = None  # type: ignore[assignment]

try:  # pragma: no cover - imported in ROS environments.
    from px4_msgs.msg import (
        OffboardControlMode,
        TrajectorySetpoint,
        VehicleAttitude,
        VehicleCommand,
        VehicleLocalPosition,
        VehicleStatus,
    )
except ModuleNotFoundError:  # pragma: no cover
    OffboardControlMode = None  # type: ignore[assignment]
    TrajectorySetpoint = None  # type: ignore[assignment]
    VehicleAttitude = None  # type: ignore[assignment]
    VehicleCommand = None  # type: ignore[assignment]
    VehicleLocalPosition = None  # type: ignore[assignment]
    VehicleStatus = None  # type: ignore[assignment]

try:  # pragma: no cover - optional telemetry messages vary by PX4 release.
    from px4_msgs.msg import BatteryStatus, VehicleGlobalPosition
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    BatteryStatus = None  # type: ignore[assignment]
    VehicleGlobalPosition = None  # type: ignore[assignment]

try:  # pragma: no cover
    from sensor_msgs.msg import LaserScan
except ModuleNotFoundError:  # pragma: no cover
    LaserScan = None  # type: ignore[assignment]


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _now() -> float:
    return time.time()


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


class Px4RosGatewayNode(Node):  # type: ignore[misc]
    """ROS node plus provider registry used by the HTTP gateway."""

    def __init__(self) -> None:
        super().__init__("airsim_agent_ros_gateway")

        self.declare_parameter("setpoint_hz", 10.0)
        self.declare_parameter("obstacle_scan_topic", "")
        self.declare_parameter("obstacle_front_angle_deg", 60.0)
        self.declare_parameter("obstacle_safety_margin_m", 1.0)
        self.declare_parameter("arrival_threshold_m", 0.8)
        self.declare_parameter("offboard_watchdog_sec", 1.5)
        self.declare_parameter("telemetry_stream_hz", 10.0)
        self.declare_parameter("px4_topic_stale_sec", 5.0)
        self.declare_parameter("px4_status_stale_sec", 30.0)
        self.declare_parameter("require_preflight_for_arm", False)
        self.declare_parameter("min_battery_remaining", -1.0)
        self.declare_parameter("min_battery_voltage_v", 0.0)
        self.declare_parameter("no_fly_zones_json", "[]")

        self.setpoint_hz = max(2.0, float(self.get_parameter("setpoint_hz").value))
        self.arrival_threshold_m = max(0.1, float(self.get_parameter("arrival_threshold_m").value))
        self.obstacle_front_angle_deg = max(1.0, float(self.get_parameter("obstacle_front_angle_deg").value))
        self.obstacle_safety_margin_m = max(0.0, float(self.get_parameter("obstacle_safety_margin_m").value))
        self.offboard_watchdog_sec = max(0.2, float(self.get_parameter("offboard_watchdog_sec").value))
        self.telemetry_stream_hz = min(30.0, max(1.0, float(self.get_parameter("telemetry_stream_hz").value)))
        self.px4_topic_stale_sec = max(0.5, float(self.get_parameter("px4_topic_stale_sec").value))
        self.px4_status_stale_sec = max(
            self.px4_topic_stale_sec,
            float(self.get_parameter("px4_status_stale_sec").value),
        )
        self.require_preflight_for_arm = _bool_value(self.get_parameter("require_preflight_for_arm").value)
        self.min_battery_remaining = float(self.get_parameter("min_battery_remaining").value)
        self.min_battery_voltage_v = float(self.get_parameter("min_battery_voltage_v").value)

        self._lock = threading.RLock()
        self._local_position: dict[str, Any] = {}
        self._vehicle_status: dict[str, Any] = {}
        self._attitude: dict[str, Any] = {}
        self._battery_status: dict[str, Any] = {}
        self._global_position: dict[str, Any] = {}
        self._last_local_position_at = 0.0
        self._last_vehicle_status_at = 0.0
        self._last_attitude_at = 0.0
        self._last_battery_status_at = 0.0
        self._last_global_position_at = 0.0
        self._last_scan_at = 0.0
        self._scan_summary: dict[str, Any] = {}

        self._offboard_stream_enabled = False
        self._offboard_deadline_at: float | None = None
        self._last_setpoint_command_at = 0.0
        self._last_offboard_tick_at = 0.0
        self._last_offboard_watchdog: dict[str, Any] | None = None
        self._offboard_tick_times: deque[float] = deque(maxlen=100)
        self._setpoint_mode = "position"
        self._position_sp = [math.nan, math.nan, math.nan]
        self._velocity_sp = [math.nan, math.nan, math.nan]
        self._yaw_sp = math.nan
        self._no_fly_zones = self._parse_no_fly_zones(str(self.get_parameter("no_fly_zones_json").value or "[]"))
        self._task_lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._task_queue: deque[str] = deque()
        self._task_counter = 0
        self._task_worker_running = False

        px4_qos = self._px4_qos()

        self.offboard_pub = self.create_publisher(OffboardControlMode, "/fmu/in/offboard_control_mode", px4_qos)
        self.trajectory_pub = self.create_publisher(TrajectorySetpoint, "/fmu/in/trajectory_setpoint", px4_qos)
        self.command_pub = self.create_publisher(VehicleCommand, "/fmu/in/vehicle_command", px4_qos)

        self.create_subscription(VehicleLocalPosition, "/fmu/out/vehicle_local_position", self._on_local_position, px4_qos)
        self.create_subscription(VehicleStatus, "/fmu/out/vehicle_status", self._on_vehicle_status, px4_qos)
        self.create_subscription(VehicleAttitude, "/fmu/out/vehicle_attitude", self._on_vehicle_attitude, px4_qos)
        if BatteryStatus is not None:
            self.create_subscription(BatteryStatus, "/fmu/out/battery_status", self._on_battery_status, px4_qos)
        if VehicleGlobalPosition is not None:
            self.create_subscription(
                VehicleGlobalPosition,
                "/fmu/out/vehicle_global_position",
                self._on_global_position,
                px4_qos,
            )

        scan_topic = str(self.get_parameter("obstacle_scan_topic").value or "").strip()
        if scan_topic and LaserScan is not None:
            self.create_subscription(LaserScan, scan_topic, self._on_laserscan, 10)
            self.get_logger().info(f"LaserScan obstacle adapter enabled on {scan_topic}")

        self.create_timer(1.0 / self.setpoint_hz, self._publish_offboard_tick)

    # ------------------------------------------------------------------
    # ROS callbacks
    # ------------------------------------------------------------------

    def _px4_qos(self) -> Any:
        """QoS compatible with PX4 uXRCE-DDS /fmu topics."""

        if QoSProfile is None:
            return 10
        return QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

    def _on_local_position(self, msg: Any) -> None:
        with self._lock:
            self._local_position = {
                "x": float(getattr(msg, "x", 0.0)),
                "y": float(getattr(msg, "y", 0.0)),
                "z": float(getattr(msg, "z", 0.0)),
                "vx": float(getattr(msg, "vx", 0.0)),
                "vy": float(getattr(msg, "vy", 0.0)),
                "vz": float(getattr(msg, "vz", 0.0)),
                "heading": float(getattr(msg, "heading", 0.0)),
                "xy_valid": bool(getattr(msg, "xy_valid", True)),
                "z_valid": bool(getattr(msg, "z_valid", True)),
                "timestamp": int(getattr(msg, "timestamp", 0)),
            }
            self._last_local_position_at = _now()

    def _on_vehicle_status(self, msg: Any) -> None:
        nav_state = int(getattr(msg, "nav_state", -1))
        arming_state = int(getattr(msg, "arming_state", -1))
        with self._lock:
            self._vehicle_status = {
                "nav_state": nav_state,
                "nav_state_name": self._constant_name(VehicleStatus, "NAVIGATION_STATE_", nav_state),
                "arming_state": arming_state,
                "arming_state_name": self._constant_name(VehicleStatus, "ARMING_STATE_", arming_state),
                "failsafe": bool(getattr(msg, "failsafe", False)),
                "pre_flight_checks_pass": bool(getattr(msg, "pre_flight_checks_pass", False)),
                "timestamp": int(getattr(msg, "timestamp", 0)),
            }
            self._last_vehicle_status_at = _now()

    def _on_vehicle_attitude(self, msg: Any) -> None:
        q = list(getattr(msg, "q", [1.0, 0.0, 0.0, 0.0]))
        roll, pitch, yaw = self._quat_to_euler(q)
        with self._lock:
            self._attitude = {
                "roll": roll,
                "pitch": pitch,
                "yaw": yaw,
                "timestamp": int(getattr(msg, "timestamp", 0)),
            }
            self._last_attitude_at = _now()

    def _on_battery_status(self, msg: Any) -> None:
        warning = int(getattr(msg, "warning", 0))
        with self._lock:
            self._battery_status = {
                "connected": bool(getattr(msg, "connected", True)),
                "voltage_v": self._float_or_none(getattr(msg, "voltage_v", None)),
                "voltage_filtered_v": self._float_or_none(getattr(msg, "voltage_filtered_v", None)),
                "current_a": self._float_or_none(getattr(msg, "current_a", None)),
                "remaining": self._float_or_none(getattr(msg, "remaining", None)),
                "warning": warning,
                "warning_name": self._constant_name(BatteryStatus, "BATTERY_WARNING_", warning),
                "timestamp": int(getattr(msg, "timestamp", 0)),
            }
            self._last_battery_status_at = _now()

    def _on_global_position(self, msg: Any) -> None:
        with self._lock:
            self._global_position = {
                "lat": self._float_or_none(getattr(msg, "lat", None)),
                "lon": self._float_or_none(getattr(msg, "lon", None)),
                "alt": self._float_or_none(getattr(msg, "alt", None)),
                "lat_lon_valid": bool(getattr(msg, "lat_lon_valid", True)),
                "alt_valid": bool(getattr(msg, "alt_valid", True)),
                "timestamp": int(getattr(msg, "timestamp", 0)),
            }
            self._last_global_position_at = _now()

    def _on_laserscan(self, msg: Any) -> None:
        front_half = math.radians(self.obstacle_front_angle_deg) / 2.0
        nearest = math.inf
        nearest_angle = 0.0
        for index, value in enumerate(getattr(msg, "ranges", [])):
            if not _finite(value):
                continue
            angle = float(getattr(msg, "angle_min", 0.0)) + index * float(getattr(msg, "angle_increment", 0.0))
            if abs(angle) <= front_half and float(value) < nearest:
                nearest = float(value)
                nearest_angle = angle
        if math.isfinite(nearest):
            level = "clear" if nearest >= 5.0 else ("caution" if nearest >= 2.0 else "blocked")
            summary = {
                "level": level,
                "nearest_distance_m": nearest,
                "nearest_angle_rad": nearest_angle,
                "direction": "front",
                "source": str(getattr(msg, "header", None).frame_id if getattr(msg, "header", None) else "laserscan"),
                "timestamp": _now(),
            }
        else:
            summary = {
                "level": "unknown",
                "nearest_distance_m": None,
                "direction": "front",
                "source": "laserscan",
                "timestamp": _now(),
            }
        with self._lock:
            self._scan_summary = summary
            self._last_scan_at = _now()

    # ------------------------------------------------------------------
    # Provider API methods
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        status = self.status_payload()
        return {
            "ok": True,
            "status": "ready" if status["data"].get("px4_seen") else "waiting_for_px4",
            "message": "ROS gateway is running",
            "data": {
                "providers": self.provider_list(),
                "px4": status["data"],
            },
        }

    def provider_list(self) -> list[dict[str, Any]]:
        with self._lock:
            obstacle_configured = bool(self._scan_summary)
        return [
            {"name": "px4_control", "status": "ready", "surface": "vehicle_command"},
            {"name": "px4_telemetry", "status": "ready", "surface": "telemetry"},
            {"name": "safety", "status": "ready", "surface": "safety_interlock"},
            {
                "name": "obstacle",
                "status": "ready" if obstacle_configured else "not_configured",
                "surface": "obstacle_provider",
            },
        ]

    def status_payload(self) -> dict[str, Any]:
        now = _now()
        with self._lock:
            local = dict(self._local_position)
            status = dict(self._vehicle_status)
            attitude = dict(self._attitude)
            battery = dict(self._battery_status)
            global_position = dict(self._global_position)
            last_local_age = now - self._last_local_position_at if self._last_local_position_at else None
            last_status_age = now - self._last_vehicle_status_at if self._last_vehicle_status_at else None
            last_attitude_age = now - self._last_attitude_at if self._last_attitude_at else None
            last_battery_age = now - self._last_battery_status_at if self._last_battery_status_at else None
            last_global_age = now - self._last_global_position_at if self._last_global_position_at else None
            offboard_rate_hz = self._offboard_publish_rate_locked(now)
            last_setpoint_age = now - self._last_setpoint_command_at if self._last_setpoint_command_at else None
            offboard_watchdog = dict(self._last_offboard_watchdog or {})
            no_fly_zones = [dict(zone) for zone in self._no_fly_zones]
        active_task = self._active_task_snapshot()

        local_position_fresh = self._age_is_fresh(last_local_age, self.px4_topic_stale_sec)
        attitude_fresh = self._age_is_fresh(last_attitude_age, self.px4_topic_stale_sec)
        vehicle_status_fresh = self._age_is_fresh(last_status_age, self.px4_status_stale_sec)
        px4_seen = bool(local_position_fresh or attitude_fresh or vehicle_status_fresh)
        armed = status.get("arming_state_name") == "ARMING_STATE_ARMED"
        position_ned = {
            "x": self._float_or_default(local.get("x"), 0.0),
            "y": self._float_or_default(local.get("y"), 0.0),
            "z": self._float_or_default(local.get("z"), 0.0),
        }
        velocity_ned = {
            "vx": self._float_or_default(local.get("vx"), 0.0),
            "vy": self._float_or_default(local.get("vy"), 0.0),
            "vz": self._float_or_default(local.get("vz"), 0.0),
        }
        yaw = self._float_or_default(attitude.get("yaw", local.get("heading")), 0.0)
        heading_deg = math.degrees(yaw) % 360.0
        flying = bool(armed and abs(position_ned["z"]) > 0.5)
        data = {
            "px4_seen": px4_seen,
            "control_ready": local_position_fresh,
            "position_ned": position_ned,
            "velocity_ned": velocity_ned,
            "attitude_rad": {
                "roll": self._float_or_default(attitude.get("roll"), 0.0),
                "pitch": self._float_or_default(attitude.get("pitch"), 0.0),
                "yaw": yaw,
            },
            "heading_deg": round(heading_deg, 3),
            "armed": armed,
            "flying": flying,
            "mode": str(status.get("nav_state_name") or ""),
            "nav_state": status.get("nav_state"),
            "arming_state": status.get("arming_state"),
            "failsafe": bool(status.get("failsafe", False)),
            "pre_flight_checks_pass": bool(status.get("pre_flight_checks_pass", False)),
            "preflight_required_for_arm": self.require_preflight_for_arm,
            "last_local_position_age_s": round(last_local_age, 3) if last_local_age is not None else None,
            "last_vehicle_status_age_s": round(last_status_age, 3) if last_status_age is not None else None,
            "last_attitude_age_s": round(last_attitude_age, 3) if last_attitude_age is not None else None,
            "topic_freshness": {
                "vehicle_local_position": self._topic_freshness(last_local_age, self.px4_topic_stale_sec),
                "vehicle_attitude": self._topic_freshness(last_attitude_age, self.px4_topic_stale_sec),
                "vehicle_status": self._topic_freshness(last_status_age, self.px4_status_stale_sec),
                "battery_status": self._topic_freshness(last_battery_age, self.px4_status_stale_sec),
                "vehicle_global_position": self._topic_freshness(last_global_age, self.px4_status_stale_sec),
            },
            "local_position_fresh": local_position_fresh,
            "attitude_fresh": attitude_fresh,
            "vehicle_status_fresh": vehicle_status_fresh,
            "offboard_streaming": self._offboard_stream_enabled,
            "offboard": {
                "streaming": self._offboard_stream_enabled,
                "setpoint_hz_configured": self.setpoint_hz,
                "publish_rate_hz": offboard_rate_hz,
                "last_setpoint_command_age_s": round(last_setpoint_age, 3) if last_setpoint_age is not None else None,
                "watchdog_sec": self.offboard_watchdog_sec,
                "deadline_remaining_s": self._deadline_remaining_s(),
                "last_watchdog_action": offboard_watchdog or None,
            },
            "battery": {
                **battery,
                "age_s": round(last_battery_age, 3) if last_battery_age is not None else None,
            },
            "battery_voltage": battery.get("voltage_v"),
            "battery_remaining": battery.get("remaining"),
            "gps": {
                **global_position,
                "age_s": round(last_global_age, 3) if last_global_age is not None else None,
            },
            "global_position": {
                **global_position,
                "age_s": round(last_global_age, 3) if last_global_age is not None else None,
            },
            "geofence": {
                "no_fly_zones": no_fly_zones,
                "zone_count": len(no_fly_zones),
            },
            "active_task": active_task,
        }
        data["safety"] = self._safety_snapshot(data)
        return {
            "ok": px4_seen,
            "status": "ok" if px4_seen else "waiting_for_px4",
            "message": "" if px4_seen else "PX4 ROS topics have not been observed recently.",
            "data": data,
        }

    def arm(self, arm: bool = True, wait: bool = True, require_preflight: bool | None = None) -> dict[str, Any]:
        if arm:
            safety = self._check_command_safety(
                action="arm",
                require_preflight=self._preflight_required(require_preflight),
            )
            if not safety.get("ok"):
                return safety
        self._publish_vehicle_command(
            "VEHICLE_CMD_COMPONENT_ARM_DISARM",
            400,
            param1=1.0 if arm else 0.0,
        )
        if wait:
            expected = "ARMING_STATE_ARMED" if arm else "ARMING_STATE_STANDBY"
            self._wait_until(lambda: self.status_payload()["data"].get("armed") is arm, 4.0)
            current = self._vehicle_status.get("arming_state_name", "")
            ok = bool((arm and current == expected) or (not arm and current != "ARMING_STATE_ARMED"))
        else:
            ok = True
        return {"ok": ok, "status": "ok" if ok else "timeout", "message": "arm command sent" if arm else "disarm command sent"}

    def takeoff(self, params: dict[str, Any]) -> dict[str, Any]:
        altitude = abs(float(params.get("altitude_m") or params.get("altitude") or 3.0))
        timeout = float(params.get("timeout_sec") or max(15.0, altitude * 6.0))
        wait = _bool_value(params.get("wait"), True)
        require_preflight = self._preflight_required(params.get("require_preflight"))
        current = self._current_position()
        if current is None:
            return self._error("no_local_position", "No PX4 local position has been received.")
        target = [current["x"], current["y"], -altitude]
        safety = self._check_command_safety(target, action="takeoff", require_preflight=require_preflight)
        if not safety.get("ok"):
            return safety
        self._prepare_for_offboard_takeoff()

        self._set_position_setpoint(target[0], target[1], target[2], self._current_yaw(), max_age_sec=timeout + 2.0)
        self._warmup_offboard_stream()
        self._set_offboard_mode()
        arm_result = self.arm(True, wait=True, require_preflight=require_preflight)
        if not arm_result.get("ok"):
            with self._lock:
                self._offboard_stream_enabled = False
            return {
                "ok": False,
                "status": "arm_failed",
                "message": arm_result.get("message", "PX4 arm command failed"),
                "data": {"arm_result": arm_result, "target_position_ned": {"x": target[0], "y": target[1], "z": target[2]}},
            }
        offboard_ok = self._ensure_offboard_mode()

        ok = True
        if wait:
            ok = self._wait_until(lambda: self._distance_to(target) <= self.arrival_threshold_m, timeout)
        if ok:
            self._set_position_setpoint(target[0], target[1], target[2], self._current_yaw())
            offboard_ok = self._ensure_offboard_mode()
        return {
            "ok": bool(ok and offboard_ok),
            "status": "ok" if ok and offboard_ok else ("offboard_not_ready" if ok else "timeout"),
            "message": f"takeoff target set to {altitude:.1f}m",
            "data": {
                "target_position_ned": {"x": target[0], "y": target[1], "z": target[2]},
                "offboard_mode": offboard_ok,
                "mode": self._nav_state_name(),
            },
        }

    def land(self, params: dict[str, Any]) -> dict[str, Any]:
        self._publish_vehicle_command("VEHICLE_CMD_NAV_LAND", 21)
        with self._lock:
            self._offboard_stream_enabled = False
        return {"ok": True, "status": "ok", "message": "land command sent"}

    def hold(self) -> dict[str, Any]:
        current = self._current_position()
        if current is None:
            return self._error("no_local_position", "No PX4 local position has been received.")
        self._set_position_setpoint(current["x"], current["y"], current["z"], self._current_yaw())
        offboard_ok = self._ensure_offboard_mode()
        return {
            "ok": offboard_ok,
            "status": "ok" if offboard_ok else "offboard_not_ready",
            "message": "holding current local position",
            "data": {"position_ned": current, "mode": self._nav_state_name()},
        }

    def set_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        mode = str(params.get("mode") or "").strip().upper()
        if mode == "OFFBOARD":
            self._warmup_offboard_stream()
            self._set_offboard_mode()
            return {"ok": True, "status": "ok", "message": "offboard mode command sent"}
        if mode in {"POSCTL", "POSITION", "POSITION_HOLD"}:
            return self._set_main_mode("POSCTL", 3)
        if mode == "ALTCTL":
            return self._set_main_mode("ALTCTL", 2)
        if mode == "MANUAL":
            return self._set_main_mode("MANUAL", 1)
        if mode in {"LAND", "AUTO.LAND"}:
            return self.land({})
        if mode in {"RTL", "RETURN", "RETURN_TO_LAUNCH"}:
            self._publish_vehicle_command("VEHICLE_CMD_NAV_RETURN_TO_LAUNCH", 20)
            return {"ok": True, "status": "ok", "message": "return-to-launch command sent"}
        if mode in {"HOLD", "LOITER"}:
            return self._set_auto_loiter_mode()
        return self._error("unsupported_mode", f"Unsupported ROS gateway mode: {mode}")

    def local_setpoint(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            target = [float(params["x"]), float(params["y"]), float(params["z"])]
        except (KeyError, TypeError, ValueError):
            return self._error("invalid_setpoint", "x, y, and z are required local NED coordinates.")
        wait = _bool_value(params.get("wait"), True)
        velocity = max(0.1, float(params.get("velocity") or 2.0))
        timeout = float(params.get("timeout_sec") or self._estimated_timeout(target, velocity))
        yaw = self._yaw_from_params(params)
        safety = self._check_command_safety(target, action="local_setpoint")
        if not safety.get("ok"):
            return safety
        self._set_position_setpoint(target[0], target[1], target[2], yaw, max_age_sec=timeout + 2.0)
        offboard_ok = self._ensure_offboard_mode()
        if not offboard_ok:
            return {
                "ok": False,
                "status": "offboard_not_ready",
                "message": "PX4 did not enter OFFBOARD mode for local setpoint control.",
                "data": {"target_position_ned": {"x": target[0], "y": target[1], "z": target[2]}, "mode": self._nav_state_name()},
            }
        ok = True
        if wait:
            ok = self._wait_until(lambda: self._distance_to(target) <= self.arrival_threshold_m, timeout)
        return {
            "ok": ok,
            "status": "ok" if ok else "timeout",
            "message": "local NED setpoint accepted",
            "data": {"target_position_ned": {"x": target[0], "y": target[1], "z": target[2]}},
        }

    def move_relative(self, params: dict[str, Any]) -> dict[str, Any]:
        current = self._current_position()
        if current is None:
            return self._error("no_local_position", "No PX4 local position has been received.")
        forward = float(params.get("forward_m") or 0.0)
        right = float(params.get("right_m") or 0.0)
        up = float(params.get("up_m") or 0.0)
        yaw = self._current_yaw()
        dx = math.cos(yaw) * forward + math.cos(yaw + math.pi / 2.0) * right
        dy = math.sin(yaw) * forward + math.sin(yaw + math.pi / 2.0) * right
        target = {
            "x": current["x"] + dx,
            "y": current["y"] + dy,
            "z": current["z"] - up,
        }
        payload = dict(params)
        payload.update(target)
        return self.local_setpoint(payload)

    def velocity(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            vx = float(params.get("vx") or 0.0)
            vy = float(params.get("vy") or 0.0)
            vz = float(params.get("vz") or 0.0)
        except (TypeError, ValueError):
            return self._error("invalid_velocity", "vx, vy, and vz must be numeric NED velocities.")
        if not all(_finite(item) for item in [vx, vy, vz]):
            return self._error("invalid_velocity", "vx, vy, and vz must be finite NED velocities.")
        safety = self._check_command_safety(action="velocity")
        if not safety.get("ok"):
            return safety
        duration = max(0.0, float(params.get("duration_sec") or params.get("duration") or 0.0))
        with self._lock:
            self._setpoint_mode = "velocity"
            self._position_sp = [math.nan, math.nan, math.nan]
            self._velocity_sp = [vx, vy, vz]
            self._yaw_sp = self._current_yaw()
            self._offboard_stream_enabled = True
            self._touch_offboard_command_locked(max_age_sec=(duration + 2.0) if duration > 0 else self.offboard_watchdog_sec)
        if not self._ensure_offboard_mode():
            return {"ok": False, "status": "offboard_not_ready", "message": "PX4 did not enter OFFBOARD mode for velocity control."}
        if duration > 0:
            time.sleep(duration)
            self.hold()
        return {"ok": True, "status": "ok", "message": "velocity setpoint streamed"}

    def path(self, params: dict[str, Any]) -> dict[str, Any]:
        waypoints = params.get("waypoints")
        if not isinstance(waypoints, list) or not waypoints:
            return self._error("invalid_path", "waypoints must be a non-empty list")
        velocity = max(0.1, float(params.get("velocity") or 2.0))
        for index, waypoint in enumerate(waypoints):
            if not isinstance(waypoint, dict):
                return self._error("invalid_waypoint", f"waypoint {index} is not an object")
            try:
                target = [float(waypoint["x"]), float(waypoint["y"]), float(waypoint["z"])]
            except (KeyError, TypeError, ValueError):
                return self._error("invalid_waypoint", f"waypoint {index} must include finite x, y, and z")
            safety = self._check_command_safety(target, action=f"path waypoint {index}")
            if not safety.get("ok"):
                safety.setdefault("data", {})["waypoint_index"] = index
                return safety
        results = []
        for index, waypoint in enumerate(waypoints):
            step = self.local_setpoint({**waypoint, "velocity": velocity, "wait": True})
            results.append(step)
            if not step.get("ok"):
                return {"ok": False, "status": "failed", "message": f"path stopped at waypoint {index}", "data": {"steps": results}}
        return {"ok": True, "status": "ok", "message": f"path complete ({len(waypoints)} waypoints)", "data": {"steps": results}}

    def rotate_to(self, params: dict[str, Any]) -> dict[str, Any]:
        current = self._current_position()
        if current is None:
            return self._error("no_local_position", "No PX4 local position has been received.")
        heading_deg = float(params.get("heading_deg") or 0.0) % 360.0
        yaw = math.radians(heading_deg)
        timeout = max(0.0, float(params.get("timeout_sec") or 30.0))
        wait = _bool_value(params.get("wait"), True)
        target = [current["x"], current["y"], current["z"]]
        safety = self._check_command_safety(target, action="rotate_to")
        if not safety.get("ok"):
            return safety
        self._set_position_setpoint(current["x"], current["y"], current["z"], yaw, max_age_sec=timeout + 2.0)
        offboard_ok = self._ensure_offboard_mode()
        if not offboard_ok:
            return {
                "ok": False,
                "status": "offboard_not_ready",
                "message": "PX4 did not enter OFFBOARD mode for yaw control.",
                "data": {"target_heading_deg": heading_deg, "mode": self._nav_state_name()},
            }
        ok = True
        if wait:
            ok = self._wait_until(lambda: self._yaw_error_abs(self._current_yaw(), yaw) <= math.radians(5.0), timeout)
        return {
            "ok": ok,
            "status": "ok" if ok else "timeout",
            "message": "yaw target reached" if ok else "yaw target timed out",
            "data": {
                "target_heading_deg": heading_deg,
                "heading_deg": round(math.degrees(self._current_yaw()) % 360.0, 1),
            },
        }

    def obstacle_summary(self, params: dict[str, Any]) -> dict[str, Any]:
        max_age = max(0.0, float(params.get("max_age_sec") or 1.0))
        with self._lock:
            summary = dict(self._scan_summary)
            age = _now() - self._last_scan_at if self._last_scan_at else None
        if not summary:
            return {
                "ok": True,
                "status": "not_configured",
                "message": "No obstacle adapter is configured.",
                "data": {"level": "unknown", "source": "none"},
            }
        stale = age is None or age > max_age
        return {
            "ok": not stale,
            "status": "stale" if stale else "ok",
            "message": "obstacle data is stale" if stale else "",
            "data": {**summary, "age_s": round(age, 3) if age is not None else None},
        }

    def validate_motion(self, params: dict[str, Any]) -> dict[str, Any]:
        summary = self.obstacle_summary({"max_age_sec": params.get("max_age_sec", 1.0)})
        if summary["status"] == "not_configured":
            return {"ok": True, "status": "safe", "message": "No obstacle provider configured.", "data": summary["data"]}
        if not summary.get("ok"):
            return {"ok": False, "status": "blocked", "message": summary.get("message", "obstacle data invalid"), "data": summary["data"]}
        data = dict(summary["data"])
        nearest = data.get("nearest_distance_m")
        motion = params.get("motion") if isinstance(params.get("motion"), dict) else params
        forward = max(0.0, float(motion.get("forward_m") or 0.0))
        safe = not isinstance(nearest, (int, float)) or nearest >= forward + self.obstacle_safety_margin_m
        return {
            "ok": safe,
            "status": "safe" if safe else "blocked",
            "message": "motion accepted" if safe else "front obstacle inside safety margin",
            "data": {
                **data,
                "safety_margin_m": self.obstacle_safety_margin_m,
                "requested_forward_m": forward,
            },
        }

    def safety_status(self) -> dict[str, Any]:
        status = self.status_payload()
        return {
            "ok": True,
            "status": "ok" if status["data"]["safety"]["allow_control"] else "blocked",
            "message": "",
            "data": status["data"]["safety"],
        }

    def configure_no_fly_zones(self, params: dict[str, Any]) -> dict[str, Any]:
        raw_zones = params.get("zones", params.get("no_fly_zones", []))
        try:
            zones = self._parse_no_fly_zones(raw_zones)
        except ValueError as exc:
            return self._error("invalid_geofence", str(exc))
        with self._lock:
            self._no_fly_zones = zones
        return {
            "ok": True,
            "status": "ok",
            "message": f"{len(zones)} no-fly zone(s) configured",
            "data": {"no_fly_zones": [dict(zone) for zone in zones], "zone_count": len(zones)},
        }

    def telemetry_payload(self) -> dict[str, Any]:
        payload = self.status_payload()
        payload["event"] = "px4.telemetry"
        payload["timestamp"] = _now()
        return payload

    def start_task(self, params: dict[str, Any]) -> dict[str, Any]:
        steps = params.get("steps") or params.get("plan")
        if not isinstance(steps, list) or not steps:
            return self._error("invalid_task", "steps must be a non-empty list")
        normalized_steps = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                return self._error("invalid_task_step", f"step {index} must be an object")
            action = str(step.get("action") or step.get("type") or "").strip().lower()
            if not action:
                return self._error("invalid_task_step", f"step {index} must include action")
            normalized_steps.append({**step, "action": action})

        with self._task_lock:
            self._task_counter += 1
            task_id = f"task_{self._task_counter}"
            task = {
                "task_id": task_id,
                "status": "queued",
                "progress": 0.0,
                "current_step": "",
                "current_step_index": -1,
                "steps_total": len(normalized_steps),
                "steps": normalized_steps,
                "results": [],
                "cancel_requested": False,
                "created_at": _now(),
                "updated_at": _now(),
            }
            self._tasks[task_id] = task
            self._task_queue.append(task_id)
            if not self._task_worker_running:
                self._task_worker_running = True
                threading.Thread(target=self._task_worker_loop, daemon=True).start()
        return {
            "ok": True,
            "status": "queued",
            "message": "task queued",
            "data": self._task_public(task),
        }

    def task_status(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = str(params.get("task_id") or params.get("id") or "").strip()
        with self._task_lock:
            if not task_id:
                active = self._active_task_locked()
                task = active or (next(reversed(self._tasks.values())) if self._tasks else None)
            else:
                task = self._tasks.get(task_id)
            if task is None:
                return self._error("task_not_found", "No matching task exists.")
            return {"ok": True, "status": str(task.get("status") or "unknown"), "message": "", "data": self._task_public(task)}

    def cancel_task(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = str(params.get("task_id") or params.get("id") or "").strip()
        with self._task_lock:
            task = self._tasks.get(task_id) if task_id else self._active_task_locked()
            if task is None:
                return self._error("task_not_found", "No matching task exists.")
            task["cancel_requested"] = True
            if task.get("status") == "queued":
                task["status"] = "cancelled"
                task["updated_at"] = _now()
                self._task_queue = deque(item for item in self._task_queue if item != task.get("task_id"))
        self._set_auto_loiter_mode()
        return {"ok": True, "status": "cancel_requested", "message": "task cancellation requested", "data": self._task_public(task)}

    # ------------------------------------------------------------------
    # PX4 publishing helpers
    # ------------------------------------------------------------------

    def _publish_offboard_tick(self) -> None:
        watchdog_action: dict[str, Any] | None = None
        with self._lock:
            if not self._offboard_stream_enabled:
                return
            now = _now()
            if self._offboard_deadline_at is not None and now > self._offboard_deadline_at:
                watchdog_action = {
                    "reason": "offboard_command_timeout",
                    "deadline_at": self._offboard_deadline_at,
                    "triggered_at": now,
                    "action": "AUTO_LOITER",
                }
                self._offboard_stream_enabled = False
                self._offboard_deadline_at = None
                self._last_offboard_watchdog = watchdog_action
                self.get_logger().warn("Offboard command timeout; switching PX4 to AUTO_LOITER.")
            else:
                mode = self._setpoint_mode
                position = list(self._position_sp)
                velocity = list(self._velocity_sp)
                yaw = float(self._yaw_sp)

        if watchdog_action is not None:
            self._publish_auto_loiter_command()
            return

        ctrl = OffboardControlMode()
        ctrl.timestamp = self._timestamp_us()
        ctrl.position = mode == "position"
        ctrl.velocity = mode == "velocity"
        self._assign_if_exists(ctrl, "acceleration", False)
        self._assign_if_exists(ctrl, "attitude", False)
        self._assign_if_exists(ctrl, "body_rate", False)
        self._assign_if_exists(ctrl, "thrust_and_torque", False)
        self._assign_if_exists(ctrl, "direct_actuator", False)
        self.offboard_pub.publish(ctrl)

        sp = TrajectorySetpoint()
        sp.timestamp = self._timestamp_us()
        sp.position = position
        sp.velocity = velocity
        if _finite(yaw):
            sp.yaw = yaw
        self.trajectory_pub.publish(sp)

        with self._lock:
            published_at = _now()
            self._last_offboard_tick_at = published_at
            self._offboard_tick_times.append(published_at)

    def _publish_auto_loiter_command(self) -> None:
        self._publish_vehicle_command(
            "VEHICLE_CMD_DO_SET_MODE",
            176,
            param1=1.0,
            param2=4.0,
            param3=3.0,
        )

    def _publish_vehicle_command(self, constant_name: str, fallback: int, **params: float) -> None:
        msg = VehicleCommand()
        msg.timestamp = self._timestamp_us()
        msg.param1 = float(params.get("param1", 0.0))
        msg.param2 = float(params.get("param2", 0.0))
        msg.param3 = float(params.get("param3", 0.0))
        msg.param4 = float(params.get("param4", 0.0))
        msg.param5 = float(params.get("param5", 0.0))
        msg.param6 = float(params.get("param6", 0.0))
        msg.param7 = float(params.get("param7", 0.0))
        msg.command = int(getattr(VehicleCommand, constant_name, fallback))
        msg.target_system = 1
        msg.target_component = 1
        msg.source_system = 1
        msg.source_component = 1
        msg.from_external = True
        self.command_pub.publish(msg)

    def _set_offboard_mode(self) -> None:
        self._publish_vehicle_command(
            "VEHICLE_CMD_DO_SET_MODE",
            176,
            param1=1.0,
            param2=6.0,
        )

    def _set_main_mode(self, name: str, main_mode: int) -> dict[str, Any]:
        self._publish_vehicle_command(
            "VEHICLE_CMD_DO_SET_MODE",
            176,
            param1=1.0,
            param2=float(main_mode),
        )
        ok = self._wait_until(
            lambda: self._nav_state_name().endswith(name) or self._nav_state_name() == f"NAVIGATION_STATE_{name}",
            2.0,
        )
        return {
            "ok": ok,
            "status": "ok" if ok else "timeout",
            "message": f"{name} mode command sent",
            "data": {"mode": self._nav_state_name()},
        }

    def _set_auto_loiter_mode(self) -> dict[str, Any]:
        self._publish_auto_loiter_command()
        with self._lock:
            self._offboard_stream_enabled = False
            self._offboard_deadline_at = None
        ok = self._wait_until(lambda: self._nav_state_name() == "NAVIGATION_STATE_AUTO_LOITER", 2.0)
        return {
            "ok": ok,
            "status": "ok" if ok else "timeout",
            "message": "AUTO_LOITER mode command sent",
            "data": {"mode": self._nav_state_name()},
        }

    def _prepare_for_offboard_takeoff(self) -> None:
        mode = self._nav_state_name()
        if self._armed_state():
            return
        transient = {
            "NAVIGATION_STATE_AUTO_TAKEOFF",
            "NAVIGATION_STATE_AUTO_LAND",
            "NAVIGATION_STATE_AUTO_RTL",
            "NAVIGATION_STATE_AUTO_MISSION",
        }
        if mode in transient or mode.startswith("NAVIGATION_STATE_AUTO_"):
            self._set_main_mode("POSCTL", 3)

    def _ensure_offboard_mode(self, attempts: int = 3) -> bool:
        self._warmup_offboard_stream()
        for _ in range(max(1, attempts)):
            if self._nav_state_name() == "NAVIGATION_STATE_OFFBOARD":
                return True
            self._set_offboard_mode()
            if self._wait_until(lambda: self._nav_state_name() == "NAVIGATION_STATE_OFFBOARD", 1.0):
                return True
        return self._nav_state_name() == "NAVIGATION_STATE_OFFBOARD"

    def _warmup_offboard_stream(self) -> None:
        with self._lock:
            self._offboard_stream_enabled = True
            if self._last_setpoint_command_at <= 0.0:
                self._touch_offboard_command_locked(max_age_sec=self.offboard_watchdog_sec)
        time.sleep(1.1)

    def _set_position_setpoint(
        self,
        x: float,
        y: float,
        z: float,
        yaw: float | None = None,
        *,
        max_age_sec: float | None = None,
    ) -> None:
        with self._lock:
            self._setpoint_mode = "position"
            self._position_sp = [float(x), float(y), float(z)]
            self._velocity_sp = [math.nan, math.nan, math.nan]
            self._yaw_sp = self._current_yaw() if yaw is None else float(yaw)
            self._offboard_stream_enabled = True
            self._touch_offboard_command_locked(max_age_sec=max_age_sec)

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _timestamp_us(self) -> int:
        return int(self.get_clock().now().nanoseconds / 1000)

    def _current_position(self) -> dict[str, float] | None:
        with self._lock:
            local = dict(self._local_position)
            age = _now() - self._last_local_position_at if self._last_local_position_at else None
        if age is None or age > self.px4_topic_stale_sec:
            return None
        if not _finite(local.get("x")) or not _finite(local.get("y")) or not _finite(local.get("z")):
            return None
        return {"x": float(local["x"]), "y": float(local["y"]), "z": float(local["z"])}

    def _current_yaw(self) -> float:
        with self._lock:
            if _finite(self._attitude.get("yaw")):
                return float(self._attitude["yaw"])
            if _finite(self._local_position.get("heading")):
                return float(self._local_position["heading"])
        return 0.0

    @staticmethod
    def _yaw_error_abs(current_yaw: float, target_yaw: float) -> float:
        return abs((current_yaw - target_yaw + math.pi) % (2.0 * math.pi) - math.pi)

    def _nav_state_name(self) -> str:
        with self._lock:
            return str(self._vehicle_status.get("nav_state_name") or "")

    def _armed_state(self) -> bool:
        with self._lock:
            return self._vehicle_status.get("arming_state_name") == "ARMING_STATE_ARMED"

    def _yaw_from_params(self, params: dict[str, Any]) -> float:
        if _finite(params.get("yaw")):
            return float(params["yaw"])
        if _finite(params.get("heading_deg")):
            return math.radians(float(params["heading_deg"]) % 360.0)
        return self._current_yaw()

    def _distance_to(self, target: list[float]) -> float:
        current = self._current_position()
        if current is None:
            return math.inf
        return math.sqrt(
            (current["x"] - target[0]) ** 2
            + (current["y"] - target[1]) ** 2
            + (current["z"] - target[2]) ** 2
        )

    def _estimated_timeout(self, target: list[float], velocity: float) -> float:
        current = self._current_position()
        if current is None:
            return 30.0
        distance = math.sqrt(
            (current["x"] - target[0]) ** 2
            + (current["y"] - target[1]) ** 2
            + (current["z"] - target[2]) ** 2
        )
        return max(10.0, distance / max(0.1, velocity) + 10.0)

    def _check_command_safety(
        self,
        target: list[float] | None = None,
        *,
        action: str,
        require_preflight: bool = False,
    ) -> dict[str, Any]:
        status = self.status_payload()["data"]
        reasons = self._safety_reasons(status, target, require_preflight=require_preflight)
        if not reasons:
            return {"ok": True, "status": "ok", "message": "", "data": {}}
        data: dict[str, Any] = {"action": action, "reasons": reasons}
        if target is not None:
            data["target_position_ned"] = {"x": target[0], "y": target[1], "z": target[2]}
        return {
            "ok": False,
            "status": "safety_blocked",
            "message": "Command rejected by safety interlock: " + ", ".join(reason["code"] for reason in reasons),
            "data": data,
        }

    def _safety_snapshot(self, status: dict[str, Any]) -> dict[str, Any]:
        reasons = self._safety_reasons(status, None, require_preflight=False)
        return {
            "allow_control": not reasons,
            "reasons": reasons,
            "min_battery_remaining": self.min_battery_remaining,
            "min_battery_voltage_v": self.min_battery_voltage_v,
            "no_fly_zones": status.get("geofence", {}).get("no_fly_zones", []),
        }

    def _safety_reasons(
        self,
        status: dict[str, Any],
        target: list[float] | None,
        *,
        require_preflight: bool,
    ) -> list[dict[str, Any]]:
        reasons: list[dict[str, Any]] = []
        if not status.get("px4_seen"):
            reasons.append({"code": "px4_not_seen", "message": "PX4 ROS topics are stale or unavailable."})
        if status.get("failsafe"):
            reasons.append({"code": "px4_failsafe", "message": "PX4 is reporting failsafe."})
        if require_preflight and not status.get("pre_flight_checks_pass"):
            reasons.append({"code": "preflight_checks_failed", "message": "PX4 pre-flight checks are not passing."})

        battery = status.get("battery") if isinstance(status.get("battery"), dict) else {}
        if battery.get("age_s") is not None and battery.get("connected") is False:
            reasons.append({"code": "battery_disconnected", "message": "PX4 battery status reports disconnected."})
        warning = battery.get("warning")
        try:
            warning_value = int(warning)
        except (TypeError, ValueError):
            warning_value = 0
        if warning_value > 0:
            reasons.append(
                {
                    "code": "battery_warning",
                    "message": f"PX4 battery warning is active: {battery.get('warning_name') or warning_value}.",
                    "warning": warning_value,
                }
            )
        remaining = battery.get("remaining")
        if self.min_battery_remaining >= 0.0 and _finite(remaining) and float(remaining) < self.min_battery_remaining:
            reasons.append(
                {
                    "code": "battery_remaining_low",
                    "message": "Battery remaining is below configured minimum.",
                    "remaining": float(remaining),
                    "minimum": self.min_battery_remaining,
                }
            )
        voltage = battery.get("voltage_v")
        if self.min_battery_voltage_v > 0.0 and _finite(voltage) and float(voltage) < self.min_battery_voltage_v:
            reasons.append(
                {
                    "code": "battery_voltage_low",
                    "message": "Battery voltage is below configured minimum.",
                    "voltage_v": float(voltage),
                    "minimum_v": self.min_battery_voltage_v,
                }
            )

        if target is not None:
            if not all(_finite(item) for item in target):
                reasons.append({"code": "invalid_target", "message": "Target NED coordinates must be finite numbers."})
            else:
                zone = self._target_no_fly_zone(target)
                if zone is not None:
                    reasons.append(
                        {
                            "code": "target_in_no_fly_zone",
                            "message": "Target NED coordinate is inside a configured no-fly zone.",
                            "zone": dict(zone),
                        }
                    )
        return reasons

    def _parse_no_fly_zones(self, raw_zones: Any) -> list[dict[str, Any]]:
        if raw_zones in (None, ""):
            return []
        if isinstance(raw_zones, str):
            try:
                raw_zones = json.loads(raw_zones)
            except json.JSONDecodeError as exc:
                raise ValueError(f"no_fly_zones_json is invalid JSON: {exc}") from exc
        if not isinstance(raw_zones, list):
            raise ValueError("no_fly_zones must be a list")

        zones: list[dict[str, Any]] = []
        bound_keys = ("min_x", "max_x", "min_y", "max_y", "min_z", "max_z")
        for index, item in enumerate(raw_zones):
            if not isinstance(item, dict):
                raise ValueError(f"zone {index} must be an object")
            zone: dict[str, Any] = {"id": str(item.get("id") or item.get("name") or f"zone_{index}")}
            has_bound = False
            for key in bound_keys:
                value = item.get(key)
                if value in (None, ""):
                    continue
                if not _finite(value):
                    raise ValueError(f"zone {index} field {key} must be a finite number")
                zone[key] = float(value)
                has_bound = True
            if not has_bound:
                raise ValueError(f"zone {index} must include at least one NED bound")
            for axis in ("x", "y", "z"):
                min_key = f"min_{axis}"
                max_key = f"max_{axis}"
                if min_key in zone and max_key in zone and zone[min_key] > zone[max_key]:
                    raise ValueError(f"zone {index} has {min_key} greater than {max_key}")
            zones.append(zone)
        return zones

    def _target_no_fly_zone(self, target: list[float]) -> dict[str, Any] | None:
        x, y, z = [float(item) for item in target]
        with self._lock:
            zones = [dict(zone) for zone in self._no_fly_zones]
        for zone in zones:
            if (
                float(zone.get("min_x", -math.inf)) <= x <= float(zone.get("max_x", math.inf))
                and float(zone.get("min_y", -math.inf)) <= y <= float(zone.get("max_y", math.inf))
                and float(zone.get("min_z", -math.inf)) <= z <= float(zone.get("max_z", math.inf))
            ):
                return zone
        return None

    def _touch_offboard_command_locked(self, *, max_age_sec: float | None = None) -> None:
        now = _now()
        self._last_setpoint_command_at = now
        self._last_offboard_watchdog = None
        self._offboard_deadline_at = None if max_age_sec is None else now + max(0.2, float(max_age_sec))

    def _offboard_publish_rate_locked(self, now: float) -> float:
        cutoff = now - 2.0
        while self._offboard_tick_times and self._offboard_tick_times[0] < cutoff:
            self._offboard_tick_times.popleft()
        if len(self._offboard_tick_times) < 2:
            return 0.0
        span = self._offboard_tick_times[-1] - self._offboard_tick_times[0]
        if span <= 0.0:
            return 0.0
        return round((len(self._offboard_tick_times) - 1) / span, 2)

    def _deadline_remaining_s(self) -> float | None:
        with self._lock:
            deadline = self._offboard_deadline_at
        if deadline is None:
            return None
        return round(deadline - _now(), 3)

    def _age_is_fresh(self, age: float | None, max_age_sec: float) -> bool:
        return age is not None and age <= max(0.0, float(max_age_sec))

    def _topic_freshness(self, age: float | None, max_age_sec: float) -> dict[str, Any]:
        return {
            "seen": age is not None,
            "fresh": self._age_is_fresh(age, max_age_sec),
            "age_s": round(age, 3) if age is not None else None,
            "stale_after_s": float(max_age_sec),
        }

    def _float_or_none(self, value: Any) -> float | None:
        if not _finite(value):
            return None
        return float(value)

    def _float_or_default(self, value: Any, default: float) -> float:
        if not _finite(value):
            return float(default)
        return float(value)

    def _preflight_required(self, value: Any = None) -> bool:
        return _bool_value(value, self.require_preflight_for_arm)

    def _task_worker_loop(self) -> None:
        while True:
            with self._task_lock:
                if not self._task_queue:
                    self._task_worker_running = False
                    return
                task_id = self._task_queue.popleft()
            self._run_task(task_id)

    def _run_task(self, task_id: str) -> None:
        with self._task_lock:
            task = self._tasks.get(task_id)
            if task is None or task.get("status") == "cancelled":
                return
            steps = list(task.get("steps") or [])
            task.update(
                {
                    "status": "executing",
                    "started_at": _now(),
                    "updated_at": _now(),
                    "progress": 0.0,
                    "current_step_index": 0,
                    "current_step": str(steps[0].get("action") if steps else ""),
                }
            )

        total = max(1, len(steps))
        for index, step in enumerate(steps):
            if self._task_cancel_requested(task_id):
                self._finish_task(task_id, "cancelled", "task cancelled", ok=False)
                self._set_auto_loiter_mode()
                return
            self._update_task(
                task_id,
                status="executing",
                current_step_index=index,
                current_step=str(step.get("action") or ""),
                progress=index / total,
            )
            result = self._execute_task_step(task_id, index, total, step)
            with self._task_lock:
                task = self._tasks.get(task_id)
                if task is not None:
                    task.setdefault("results", []).append(result)
                    task["updated_at"] = _now()
            if self._task_cancel_requested(task_id):
                self._finish_task(task_id, "cancelled", "task cancelled", ok=False)
                self._set_auto_loiter_mode()
                return
            if not result.get("ok"):
                self._finish_task(task_id, "failed", result.get("message") or result.get("status") or "task step failed", ok=False)
                self._set_auto_loiter_mode()
                return
            self._update_task(task_id, progress=(index + 1) / total)
        self._finish_task(task_id, "completed", "task complete", ok=True)

    def _execute_task_step(self, task_id: str, index: int, total: int, step: dict[str, Any]) -> dict[str, Any]:
        action = str(step.get("action") or "").lower()
        if action in {"takeoff", "arm_takeoff"}:
            return self.takeoff({**step, "wait": True})
        if action in {"hold", "hover", "wait"}:
            result = self.hold()
            if not result.get("ok"):
                return result
            duration = max(0.0, float(step.get("duration_sec") or step.get("duration") or step.get("seconds") or 0.0))
            deadline = _now() + duration
            while _now() < deadline:
                if self._task_cancel_requested(task_id):
                    return self._error("cancelled", "task cancelled")
                remaining = max(0.0, deadline - _now())
                elapsed = duration - remaining
                fraction = 1.0 if duration <= 0.0 else min(1.0, max(0.0, elapsed / duration))
                self._update_task(task_id, progress=(index + fraction) / max(1, total))
                time.sleep(min(0.2, max(0.0, remaining)))
            return result
        if action in {"local_setpoint", "local_ned", "fly_to", "setpoint"}:
            return self.local_setpoint({**step, "wait": step.get("wait", True)})
        if action in {"move_relative", "relative"}:
            return self.move_relative(step)
        if action in {"velocity", "fly_velocity"}:
            return self.velocity(step)
        if action in {"path", "fly_path"}:
            return self.path({**step, "wait": True})
        if action in {"rotate", "rotate_to", "heading"}:
            return self.rotate_to(step)
        if action in {"rtl", "return", "return_to_launch"}:
            return self.set_mode({"mode": "RTL"})
        if action in {"loiter", "auto_loiter"}:
            return self.set_mode({"mode": "LOITER"})
        if action == "land":
            return self.land(step)
        if action == "disarm":
            return self.arm(False)
        if action == "arm":
            return self.arm(True)
        return self._error("unsupported_task_action", f"Unsupported task action: {action}")

    def _update_task(self, task_id: str, **updates: Any) -> None:
        with self._task_lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.update(updates)
            if "progress" in updates:
                task["progress"] = round(min(1.0, max(0.0, float(task["progress"]))), 3)
            task["updated_at"] = _now()

    def _finish_task(self, task_id: str, status: str, message: str, *, ok: bool) -> None:
        self._update_task(
            task_id,
            status=status,
            message=message,
            ok=ok,
            progress=1.0 if ok else self._tasks.get(task_id, {}).get("progress", 0.0),
            finished_at=_now(),
        )

    def _task_cancel_requested(self, task_id: str) -> bool:
        with self._task_lock:
            return bool(self._tasks.get(task_id, {}).get("cancel_requested"))

    def _active_task_locked(self) -> dict[str, Any] | None:
        for task in self._tasks.values():
            if task.get("status") == "executing":
                return task
        for task in self._tasks.values():
            if task.get("status") == "queued":
                return task
        return None

    def _active_task_snapshot(self) -> dict[str, Any] | None:
        with self._task_lock:
            task = self._active_task_locked()
            return self._task_public(task) if task is not None else None

    def _task_public(self, task: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": task.get("task_id"),
            "status": task.get("status"),
            "ok": task.get("ok"),
            "message": task.get("message", ""),
            "progress": task.get("progress", 0.0),
            "current_step": task.get("current_step", ""),
            "current_step_index": task.get("current_step_index", -1),
            "steps_total": task.get("steps_total", 0),
            "results": list(task.get("results") or []),
            "created_at": task.get("created_at"),
            "started_at": task.get("started_at"),
            "updated_at": task.get("updated_at"),
            "finished_at": task.get("finished_at"),
            "cancel_requested": bool(task.get("cancel_requested", False)),
        }

    def _wait_until(self, predicate: Callable[[], bool], timeout_sec: float, interval_sec: float = 0.1) -> bool:
        deadline = _now() + max(0.0, timeout_sec)
        while _now() < deadline:
            if predicate():
                return True
            time.sleep(interval_sec)
        return predicate()

    def _constant_name(self, msg_type: Any, prefix: str, value: int) -> str:
        for name in dir(msg_type):
            if name.startswith(prefix) and getattr(msg_type, name) == value:
                return name
        return str(value)

    def _assign_if_exists(self, msg: Any, name: str, value: Any) -> None:
        if hasattr(msg, name):
            setattr(msg, name, value)

    def _quat_to_euler(self, q: list[float]) -> tuple[float, float, float]:
        if len(q) != 4:
            return 0.0, 0.0, 0.0
        w, x, y, z = [float(item) for item in q]
        sinr_cosp = 2.0 * (w * x + y * z)
        cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
        roll = math.atan2(sinr_cosp, cosr_cosp)
        sinp = 2.0 * (w * y - z * x)
        pitch = math.copysign(math.pi / 2.0, sinp) if abs(sinp) >= 1.0 else math.asin(sinp)
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        yaw = math.atan2(siny_cosp, cosy_cosp)
        return roll, pitch, yaw

    def _error(self, status: str, message: str) -> dict[str, Any]:
        return {"ok": False, "status": status, "message": message, "data": {}}


class GatewayHttpHandler(BaseHTTPRequestHandler):
    """HTTP facade for provider calls."""

    protocol_version = "HTTP/1.1"
    gateway: Px4RosGatewayNode

    def log_message(self, fmt: str, *args: Any) -> None:
        self.gateway.get_logger().info(f"http {self.address_string()} - {fmt % args}")

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            self._handle_get(parsed)
        except Exception as exc:
            self._send_internal_error(exc, parsed.path)

    def _handle_get(self, parsed: Any) -> None:
        path = parsed.path
        if path in {"/events", "/providers/px4/telemetry/stream"}:
            self._send_sse(parsed.query)
            return
        if path == "/providers/px4/task/status":
            params = {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
            self._send_provider_result(self.gateway.task_status(params))
            return
        routes: dict[str, Callable[[], dict[str, Any]]] = {
            "/health": self.gateway.health,
            "/providers": lambda: {"ok": True, "status": "ok", "data": {"providers": self.gateway.provider_list()}},
            "/providers/px4/status": self.gateway.status_payload,
            "/providers/safety/status": self.gateway.safety_status,
        }
        handler = routes.get(path)
        if not handler:
            self._send_json({"ok": False, "status": "not_found", "message": path}, HTTPStatus.NOT_FOUND)
            return
        self._send_provider_result(handler())

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            self._handle_post(parsed)
        except Exception as exc:
            self._send_internal_error(exc, parsed.path)

    def _handle_post(self, parsed: Any) -> None:
        path = parsed.path
        payload = self._read_json()
        routes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "/providers/px4/arm": lambda body: self.gateway.arm(
                True,
                wait=_bool_value(body.get("wait"), True),
                require_preflight=body.get("require_preflight"),
            ),
            "/providers/px4/disarm": lambda body: self.gateway.arm(False, wait=_bool_value(body.get("wait"), True)),
            "/providers/px4/takeoff": self.gateway.takeoff,
            "/providers/px4/land": self.gateway.land,
            "/providers/px4/hold": lambda body: self.gateway.hold(),
            "/providers/px4/set_mode": self.gateway.set_mode,
            "/providers/px4/setpoint/local_ned": self.gateway.local_setpoint,
            "/providers/px4/move_relative": self.gateway.move_relative,
            "/providers/px4/velocity": self.gateway.velocity,
            "/providers/px4/path": self.gateway.path,
            "/providers/px4/rotate_to": self.gateway.rotate_to,
            "/providers/obstacle/summary": self.gateway.obstacle_summary,
            "/providers/obstacle/validate_motion": self.gateway.validate_motion,
            "/providers/safety/geofence": self.gateway.configure_no_fly_zones,
            "/providers/px4/task/start": self.gateway.start_task,
            "/providers/px4/task/status": self.gateway.task_status,
            "/providers/px4/task/cancel": self.gateway.cancel_task,
        }
        handler = routes.get(path)
        if not handler:
            self._send_json({"ok": False, "status": "not_found", "message": path}, HTTPStatus.NOT_FOUND)
            return
        self._send_provider_result(handler(payload))

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return {}
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        if not body.strip():
            return {}
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _send_provider_result(self, payload: dict[str, Any]) -> None:
        status = HTTPStatus.OK if payload.get("status") != "not_found" else HTTPStatus.NOT_FOUND
        self._send_json(payload, status)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(_json_safe(payload), ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_internal_error(self, exc: Exception, path: str) -> None:
        message = f"{type(exc).__name__}: {exc}"
        try:
            self.gateway.get_logger().error(f"HTTP handler failed for {self.command} {path}: {message}")
        except Exception:
            pass
        try:
            self._send_json(
                {
                    "ok": False,
                    "status": "internal_error",
                    "message": message,
                    "data": {"path": path},
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        except OSError:
            return

    def _send_sse(self, query: str) -> None:
        params = parse_qs(query)
        requested_hz = self.gateway.telemetry_stream_hz
        raw_hz = params.get("hz", [""])[0]
        if raw_hz:
            try:
                requested_hz = float(raw_hz)
            except ValueError:
                requested_hz = self.gateway.telemetry_stream_hz
        hz = min(30.0, max(1.0, requested_hz))
        interval = 1.0 / hz

        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        sequence = 0
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                payload = self.gateway.telemetry_payload()
                payload["sequence"] = sequence
                body = json.dumps(_json_safe(payload), ensure_ascii=False, allow_nan=False)
                self.wfile.write(f"id: {sequence}\nevent: telemetry\ndata: {body}\n\n".encode("utf-8"))
                self.wfile.flush()
                sequence += 1
                time.sleep(interval)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return
        except Exception as exc:
            try:
                self.gateway.get_logger().error(f"SSE stream failed: {type(exc).__name__}: {exc}")
            except Exception:
                pass
            return

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")


class GatewayThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _start_http_server(node: Px4RosGatewayNode, host: str, port: int) -> ThreadingHTTPServer:
    GatewayHttpHandler.gateway = node
    server = GatewayThreadingHTTPServer((host, port), GatewayHttpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    node.get_logger().info(f"Provider Gateway listening on http://{host}:{port}")
    return server


def _require_ros_imports() -> None:
    missing = []
    if rclpy is None:
        missing.append("rclpy")
    if OffboardControlMode is None or TrajectorySetpoint is None or VehicleCommand is None:
        missing.append("px4_msgs")
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing ROS dependencies: {joined}. Source ROS and build/install px4_msgs first.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8766)
    args, ros_args = parser.parse_known_args(argv)

    _require_ros_imports()
    rclpy.init(args=ros_args)
    node = Px4RosGatewayNode()
    server = _start_http_server(node, args.host, args.port)
    try:
        rclpy.spin(node)
    finally:
        server.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
