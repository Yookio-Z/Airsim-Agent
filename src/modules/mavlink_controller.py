"""PX4/MAVLink flight controller backend implemented with pymavlink."""

from __future__ import annotations

import math
import os
import struct
import subprocess
import threading
import time
from collections import deque
from typing import Any, Callable

os.environ.setdefault("MAVLINK20", "1")
from pymavlink import mavutil
if getattr(mavutil.mavlink, "WIRE_PROTOCOL_VERSION", "1.0") != "2.0":
    os.environ["MAVLINK20"] = "1"
    mavutil.set_dialect("ardupilotmega")

from .flight_controller import ConnectionInfo, DroneStatus, FlightController
from .mavlink_autodiscovery import (
    discover_serial_mavlink_candidates,
    normalize_serial_baud,
)
from ..config import config
from ..logging_config import get_logger

logger = get_logger(__name__)


_MASK_POSITION_ONLY = (
    mavutil.mavlink.POSITION_TARGET_TYPEMASK_VX_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VY_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VZ_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
)
_MASK_VELOCITY_ONLY = (
    mavutil.mavlink.POSITION_TARGET_TYPEMASK_X_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Y_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Z_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_RATE_IGNORE
)
_MASK_YAW_RATE_ONLY = (
    mavutil.mavlink.POSITION_TARGET_TYPEMASK_X_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Y_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Z_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VX_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VY_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_VZ_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
    | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
)


class MavlinkController(FlightController):
    """FlightController implementation for PX4 SITL or a MAVLink vehicle."""

    def __init__(
        self,
        connection_string: str | None = None,
        outdoor: bool = False,
        max_velocity: float = 5.0,
        arrival_threshold_m: float | None = None,
        arrival_timeout_s: float | None = None,
        setpoint_hz: float | None = None,
    ) -> None:
        self._connection_string = connection_string or config.px4_connection_string
        self._outdoor = outdoor
        self._max_velocity = max_velocity
        self._arrival_threshold_m = arrival_threshold_m or config.arrival_threshold_m
        self._arrival_timeout_s = arrival_timeout_s or config.arrival_timeout_s
        self._setpoint_hz = max(2.0, setpoint_hz or config.offboard_setpoint_hz)
        self._mavlink: Any | None = None
        self._connected = False
        # 多机（QGC 模式）：按 MAVLink system id 分表；单机时只有 sysid=0 一张表
        self._systems: dict[int, dict[str, Any]] = {}
        self._systems_history: dict[int, dict[str, deque]] = {}
        self._selected_sysid = 0
        self._telemetry: dict[str, Any] = {}
        self._position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._velocity = {"vx": 0.0, "vy": 0.0, "vz": 0.0}
        self._gps_origin: dict[str, float] | None = None
        self._last_heartbeat = 0.0
        self._last_local_position = 0.0
        self._last_global_position = 0.0
        self._autopilot: int | None = None
        self._vehicle_type: int | None = None
        self._lock = threading.RLock()
        self._offboard_hold_stop: threading.Event | None = None
        self._offboard_hold_thread: threading.Thread | None = None
        self._offboard_hold_target: dict[str, float] | None = None
        self._last_path_error: dict[str, Any] = {}
        self._last_action_error = ""
        self._real_vehicle = False
        self._candidate_metadata: dict[str, dict[str, Any]] = {}
        self._active_connection_details: dict[str, Any] = {}
        self._firmware_info: dict[str, Any] = {}
        self._parameters: dict[str, dict[str, Any]] = {}
        self._telemetry_history: dict[str, deque[dict[str, Any]]] = {
            "attitude": deque(maxlen=2400),
            "rate": deque(maxlen=2400),
            "imu": deque(maxlen=2400),
            "vibration": deque(maxlen=2400),
            "battery": deque(maxlen=300),
            "position": deque(maxlen=2400),
            "velocity": deque(maxlen=2400),
            "rc": deque(maxlen=2400),
            "servo": deque(maxlen=2400),
        }
        # 默认表（sysid=0）：单机路径与 __init__ 赋值顺序兼容
        self._systems[0] = self._new_system_table()
        self._systems_history[0] = self._telemetry_history
        self._telemetry = {}
        self._position = {"x": 0.0, "y": 0.0, "z": 0.0}
        self._velocity = {"vx": 0.0, "vy": 0.0, "vz": 0.0}
        self._parameter_component_counts: dict[int, int] = {}
        self._parameter_component_indices: dict[int, set[int]] = {}
        self._parameter_download_state = "not_requested"
        self._parameter_download_started_at: float | None = None
        self._parameter_download_finished_at: float | None = None
        self._parameter_last_message_at: float | None = None
        self._parameter_last_error = ""
        self._parameter_download_lock = threading.Lock()

    @property
    def backend_name(self) -> str:
        return "mavlink"

    # ── 多机分表（QGC 模式）：sysid → per-system 状态表 ──

    def _new_system_table(self) -> dict[str, Any]:
        return {
            "telemetry": {},
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "velocity": {"vx": 0.0, "vy": 0.0, "vz": 0.0},
            "gps_origin": None,
            "last_heartbeat": 0.0,
            "last_local_position": 0.0,
            "last_global_position": 0.0,
            "autopilot": None,
            "vehicle_type": None,
            "firmware_info": {},
        }

    def _system_table(self, sysid: int) -> dict[str, Any]:
        table = self._systems.setdefault(int(sysid), self._new_system_table())
        if int(sysid) not in self._systems_history:
            base = getattr(self, "_telemetry_history", None)
            self._systems_history[int(sysid)] = (
                {key: deque(maxlen=queue.maxlen) for key, queue in base.items()} if base else {}
            )
        return table

    @property
    def _active_sysid(self) -> int:
        if self._selected_sysid in self._systems:
            return self._selected_sysid
        if self._systems:
            return next(iter(self._systems))
        return 0

    def _target_sysid(self) -> int:
        """命令上下文的目标机：target_system 已设置时用它，否则 active 机。

        sysid 0 is never a real vehicle (the default-initialized bookkeeping
        table), so it always falls back to the active vehicle.
        """
        if self._mavlink is not None:
            sysid = int(getattr(self._mavlink, "target_system", 0) or 0)
            if sysid and sysid in self._systems:
                return sysid
        return self._active_sysid

    def _telemetry_for_target(self) -> dict[str, Any]:
        return self._system_table(self._target_sysid())["telemetry"]

    @property
    def _telemetry(self) -> dict[str, Any]:
        return self._system_table(self._active_sysid)["telemetry"]

    @_telemetry.setter
    def _telemetry(self, value: dict[str, Any]) -> None:
        self._system_table(self._active_sysid)["telemetry"] = value

    @property
    def _position(self) -> dict[str, float]:
        # target-first: per-vehicle command paths _set_target() before reading,
        # so this returns the CURRENT vehicle's position, not the first one's
        return self._system_table(self._target_sysid())["position"]

    @_position.setter
    def _position(self, value: dict[str, float]) -> None:
        self._system_table(self._target_sysid())["position"] = value

    @property
    def _velocity(self) -> dict[str, float]:
        return self._system_table(self._target_sysid())["velocity"]

    @_velocity.setter
    def _velocity(self, value: dict[str, float]) -> None:
        self._system_table(self._target_sysid())["velocity"] = value

    @property
    def _gps_origin(self) -> dict[str, float] | None:
        return self._system_table(self._target_sysid())["gps_origin"]

    @_gps_origin.setter
    def _gps_origin(self, value: dict[str, float] | None) -> None:
        self._system_table(self._target_sysid())["gps_origin"] = value

    @property
    def _last_heartbeat(self) -> float:
        return self._system_table(self._active_sysid)["last_heartbeat"]

    @_last_heartbeat.setter
    def _last_heartbeat(self, value: float) -> None:
        self._system_table(self._active_sysid)["last_heartbeat"] = value

    @property
    def _last_local_position(self) -> float:
        return self._system_table(self._active_sysid)["last_local_position"]

    @_last_local_position.setter
    def _last_local_position(self, value: float) -> None:
        self._system_table(self._active_sysid)["last_local_position"] = value

    @property
    def _last_global_position(self) -> float:
        return self._system_table(self._active_sysid)["last_global_position"]

    @_last_global_position.setter
    def _last_global_position(self, value: float) -> None:
        self._system_table(self._active_sysid)["last_global_position"] = value

    @property
    def _autopilot(self) -> int | None:
        return self._system_table(self._active_sysid)["autopilot"]

    @_autopilot.setter
    def _autopilot(self, value: int | None) -> None:
        self._system_table(self._active_sysid)["autopilot"] = value

    @property
    def _vehicle_type(self) -> int | None:
        return self._system_table(self._active_sysid)["vehicle_type"]

    @_vehicle_type.setter
    def _vehicle_type(self, value: int | None) -> None:
        self._system_table(self._active_sysid)["vehicle_type"] = value

    @property
    def _firmware_info(self) -> dict[str, Any]:
        return self._system_table(self._active_sysid)["firmware_info"]

    @_firmware_info.setter
    def _firmware_info(self, value: dict[str, Any]) -> None:
        self._system_table(self._active_sysid)["firmware_info"] = value

    @property
    def is_connected(self) -> bool:
        return self._connected and self._mavlink is not None

    @property
    def last_error(self) -> str:
        return self._last_action_error

    def connect(self, **kwargs) -> ConnectionInfo:
        url = str(kwargs.get("url") or self._connection_string)
        self._connection_string = url
        self._real_vehicle = bool(kwargs.get("real_vehicle", False))
        fallback_url = str(kwargs.get("fallback_url") or "")
        remote_host = str(kwargs.get("remote_host") or "")
        remote_port = int(kwargs.get("remote_port") or 0)
        errors: list[str] = []

        for candidate in self._candidate_urls(url, fallback_url=fallback_url):
            candidate_meta = dict(self._candidate_metadata.get(candidate) or {})
            try:
                self.disconnect()
                logger.info(f"Connecting to MAVLink endpoint {candidate}")
                self._mavlink = self._open_mavlink_connection(candidate)
                # On Windows, udpout sockets may not bind a local address before recvfrom,
                # which can raise WSAEINVAL (10022). Bind a temporary local port first.
                if candidate.startswith("udpout:") and hasattr(self._mavlink, "port"):
                    try:
                        self._mavlink.port.bind(("0.0.0.0", 0))
                    except OSError:
                        pass
                probe_targets = self._heartbeat_probe_targets(
                    candidate,
                    remote_host=remote_host,
                    remote_port=remote_port,
                )
                heartbeat = self._wait_vehicle_heartbeat(
                    timeout=self._heartbeat_timeout_for_candidate(candidate),
                    probe_targets=probe_targets,
                )
                self._connected = True
                if candidate.startswith("serial:"):
                    self._real_vehicle = True
                if heartbeat is not None:
                    self._mavlink.target_system = heartbeat.get_srcSystem()
                    self._mavlink.target_component = heartbeat.get_srcComponent()
                    self._handle_message(heartbeat)
                self._request_message_intervals()
                self.update_telemetry(timeout=1.0)
                status = self.get_status()
                details = {
                    "url": candidate,
                    "requested_url": url,
                    "system_id": self._mavlink.target_system,
                    "component_id": self._mavlink.target_component,
                    "mode": status.mode,
                    "armed": status.armed,
                    "flying": status.flying,
                    "gps": status.gps,
                    "real_vehicle": self._real_vehicle,
                }
                if candidate.startswith("udpin:"):
                    details["local_listen_url"] = candidate
                if remote_host or remote_port:
                    details["px4_remote_host"] = remote_host
                    details["px4_remote_port"] = remote_port or None
                    if remote_host and remote_port:
                        details["px4_remote_endpoint"] = f"{remote_host}:{remote_port}"
                if probe_targets:
                    details["probe_targets"] = [
                        {"host": host, "port": port}
                        for host, port in probe_targets
                    ]
                if fallback_url:
                    details["fallback_url"] = fallback_url
                if candidate_meta:
                    details["detected_link"] = candidate_meta
                self._active_connection_details = dict(details)
                try:
                    self.get_firmware_info(force=True, timeout=2.5)
                except Exception:
                    logger.debug("AUTOPILOT_VERSION request failed during connect", exc_info=True)
                try:
                    self.download_parameters(force=True, timeout=8.0)
                except Exception:
                    logger.debug("PARAM_REQUEST_LIST failed during connect", exc_info=True)
                return ConnectionInfo(
                    backend="mavlink",
                    connected=True,
                    details=details,
                )
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
                logger.error(f"MAVLink connection failed for {candidate}: {exc}")

        self.disconnect()
        return ConnectionInfo(
            backend="mavlink",
            connected=False,
            details={
                "message": "; ".join(errors) or "connection failed",
                "requested_url": url,
                "fallback_url": fallback_url,
                "px4_remote_host": remote_host,
                "px4_remote_port": remote_port or None,
                "detected_links": list(self._candidate_metadata.values()),
            },
        )

    def disconnect(self) -> None:
        self._stop_offboard_hold()
        with self._lock:
            mav = self._mavlink
            self._mavlink = None
            self._connected = False
            self._telemetry = {}
            for history in self._telemetry_history.values():
                history.clear()
            self._active_connection_details = {}
            self._firmware_info = {}
            self._reset_parameter_cache(state="disconnected")
        if mav is not None:
            try:
                mav.close()
            except Exception:
                pass

    def update_telemetry(self, timeout: float = 0.2) -> None:
        if not self.is_connected:
            return

        end_time = time.time() + max(0.0, timeout)
        while time.time() < end_time:
            with self._lock:
                if not self.is_connected:
                    return
                msg = self._mavlink.recv_match(blocking=False)
            if msg is None:
                time.sleep(0.002)
                continue
            self._handle_message(msg)

    def _handle_message(self, msg: Any) -> None:
        msg_type = msg.get_type()
        if msg_type == "BAD_DATA":
            return
        try:
            sysid = int(msg.get_srcSystem() or 0)
        except Exception:
            sysid = 0
        table = self._system_table(sysid)
        msg_dict = msg.to_dict()

        if msg_type == "HEARTBEAT":
            if not self._is_vehicle_heartbeat(msg):
                self._telemetry["GCS_HEARTBEAT"] = msg_dict
                return
            table["last_heartbeat"] = time.time()
            table["autopilot"] = msg_dict.get("autopilot")
            table["vehicle_type"] = msg_dict.get("type")
            # 多机（QGC 模式）：不再重定向 target_system —— 命令按 vehicle_name 显式设置
            if self._selected_sysid == 0 or self._selected_sysid not in self._systems:
                self._selected_sysid = sysid
            base_mode = int(msg_dict.get("base_mode", 0) or 0)
            msg_dict["armed"] = bool(base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            msg_dict["mode"] = self._mode_from_heartbeat_msg(msg)

        elif msg_type == "LOCAL_POSITION_NED":
            table["last_local_position"] = time.time()
            table["position"] = {
                "x": float(msg_dict.get("x", 0.0) or 0.0),
                "y": float(msg_dict.get("y", 0.0) or 0.0),
                "z": float(msg_dict.get("z", 0.0) or 0.0),
            }
            table["velocity"] = {
                "vx": float(msg_dict.get("vx", 0.0) or 0.0),
                "vy": float(msg_dict.get("vy", 0.0) or 0.0),
                "vz": float(msg_dict.get("vz", 0.0) or 0.0),
            }
            now = time.time()
            self._append_history("position", {"t": now, **dict(table["position"])}, sysid=sysid)
            self._append_history("velocity", {"t": now, **dict(table["velocity"])}, sysid=sysid)

        elif msg_type == "GLOBAL_POSITION_INT":
            table["last_global_position"] = time.time()
            msg_dict["lat"] = float(msg_dict.get("lat", 0) or 0) / 1e7
            msg_dict["lon"] = float(msg_dict.get("lon", 0) or 0) / 1e7
            msg_dict["alt"] = float(msg_dict.get("alt", 0) or 0) / 1000.0
            msg_dict["relative_alt"] = float(msg_dict.get("relative_alt", 0) or 0) / 1000.0
            msg_dict["vx"] = float(msg_dict.get("vx", 0) or 0) / 100.0
            msg_dict["vy"] = float(msg_dict.get("vy", 0) or 0) / 100.0
            msg_dict["vz"] = float(msg_dict.get("vz", 0) or 0) / 100.0
            raw_hdg = float(msg_dict.get("hdg", 0) or 0)
            msg_dict["hdg"] = 0.0 if raw_hdg >= 65535 else raw_hdg / 100.0
            self._update_position_from_global(msg_dict, sysid=sysid)

        elif msg_type == "VFR_HUD":
            # 空速/地速/高度/爬升率（m/s, m）
            table["telemetry"]["VFR_HUD"] = {
                "airspeed": float(msg_dict.get("airspeed", 0.0) or 0.0),
                "groundspeed": float(msg_dict.get("groundspeed", 0.0) or 0.0),
                "alt": float(msg_dict.get("alt", 0.0) or 0.0),
                "climb": float(msg_dict.get("climb", 0.0) or 0.0),
                "heading": float(msg_dict.get("heading", 0.0) or 0.0),
                "throttle": float(msg_dict.get("throttle", 0) or 0),
            }
            if not table["velocity"]:
                table["velocity"] = {
                    "vx": 0.0,
                    "vy": 0.0,
                    "vz": -float(msg_dict.get("climb", 0.0) or 0.0),
                }

        elif msg_type == "SYS_STATUS":
            msg_dict["voltage_battery"] = float(msg_dict.get("voltage_battery", 0) or 0) / 1000.0
            msg_dict["current_battery"] = float(msg_dict.get("current_battery", 0) or 0) / 100.0

        elif msg_type == "GPS_RAW_INT":
            msg_dict["lat"] = float(msg_dict.get("lat", 0) or 0) / 1e7
            msg_dict["lon"] = float(msg_dict.get("lon", 0) or 0) / 1e7
            msg_dict["alt"] = float(msg_dict.get("alt", 0) or 0) / 1000.0
            eph = _optional_float(msg_dict.get("eph"))
            epv = _optional_float(msg_dict.get("epv"))
            msg_dict["horizontal_accuracy_m"] = (
                None if eph is None or eph >= 65535 else round(eph / 100.0, 2)
            )
            msg_dict["vertical_accuracy_m"] = (
                None if epv is None or epv >= 65535 else round(epv / 100.0, 2)
            )

        elif msg_type == "HOME_POSITION":
            msg_dict["latitude"] = float(msg_dict.get("latitude", 0) or 0) / 1e7
            msg_dict["longitude"] = float(msg_dict.get("longitude", 0) or 0) / 1e7
            msg_dict["altitude"] = float(msg_dict.get("altitude", 0) or 0) / 1000.0

        elif msg_type == "ATTITUDE":
            msg_dict["roll_deg"] = math.degrees(float(msg_dict.get("roll", 0.0) or 0.0))
            msg_dict["pitch_deg"] = math.degrees(float(msg_dict.get("pitch", 0.0) or 0.0))
            msg_dict["yaw_deg"] = (math.degrees(float(msg_dict.get("yaw", 0.0) or 0.0)) + 360.0) % 360.0
            msg_dict["rollspeed_deg_s"] = math.degrees(float(msg_dict.get("rollspeed", 0.0) or 0.0))
            msg_dict["pitchspeed_deg_s"] = math.degrees(float(msg_dict.get("pitchspeed", 0.0) or 0.0))
            msg_dict["yawspeed_deg_s"] = math.degrees(float(msg_dict.get("yawspeed", 0.0) or 0.0))
            target = table["telemetry"].get("ATTITUDE_TARGET") or {}
            self._append_history("attitude", {
                "t": time.time(),
                "roll": msg_dict["roll_deg"],
                "pitch": msg_dict["pitch_deg"],
                "yaw": msg_dict["yaw_deg"],
                "roll_setpoint": _optional_float(target.get("roll_deg")),
                "pitch_setpoint": _optional_float(target.get("pitch_deg")),
                "yaw_setpoint": _optional_float(target.get("yaw_deg")),
            })
            self._append_history("rate", {
                "t": time.time(),
                "roll": msg_dict["rollspeed_deg_s"],
                "pitch": msg_dict["pitchspeed_deg_s"],
                "yaw": msg_dict["yawspeed_deg_s"],
                "roll_setpoint": math.degrees(float(target.get("body_roll_rate", 0.0) or 0.0)) if target else None,
                "pitch_setpoint": math.degrees(float(target.get("body_pitch_rate", 0.0) or 0.0)) if target else None,
                "yaw_setpoint": math.degrees(float(target.get("body_yaw_rate", 0.0) or 0.0)) if target else None,
            })

        elif msg_type == "ATTITUDE_TARGET":
            euler = self._quaternion_to_euler_deg(msg_dict.get("q"))
            if euler:
                msg_dict.update(euler)
            msg_dict["body_roll_rate_deg_s"] = math.degrees(float(msg_dict.get("body_roll_rate", 0.0) or 0.0))
            msg_dict["body_pitch_rate_deg_s"] = math.degrees(float(msg_dict.get("body_pitch_rate", 0.0) or 0.0))
            msg_dict["body_yaw_rate_deg_s"] = math.degrees(float(msg_dict.get("body_yaw_rate", 0.0) or 0.0))

        elif msg_type in {"RAW_IMU", "SCALED_IMU", "SCALED_IMU2", "SCALED_IMU3"}:
            msg_dict = self._normalise_scaled_imu(msg_type, msg_dict)
            self._append_history("imu", {
                "t": time.time(),
                "source": msg_type,
                "xacc": _optional_float(msg_dict.get("xacc")),
                "yacc": _optional_float(msg_dict.get("yacc")),
                "zacc": _optional_float(msg_dict.get("zacc")),
                "xgyro": _optional_float(msg_dict.get("xgyro")),
                "ygyro": _optional_float(msg_dict.get("ygyro")),
                "zgyro": _optional_float(msg_dict.get("zgyro")),
                "xmag": _optional_float(msg_dict.get("xmag")),
                "ymag": _optional_float(msg_dict.get("ymag")),
                "zmag": _optional_float(msg_dict.get("zmag")),
            })

        elif msg_type == "HIGHRES_IMU":
            msg_dict = self._normalise_highres_imu(msg_dict)
            self._append_history("imu", {
                "t": time.time(),
                "source": msg_type,
                "xacc": _optional_float(msg_dict.get("xacc")),
                "yacc": _optional_float(msg_dict.get("yacc")),
                "zacc": _optional_float(msg_dict.get("zacc")),
                "xgyro": _optional_float(msg_dict.get("xgyro")),
                "ygyro": _optional_float(msg_dict.get("ygyro")),
                "zgyro": _optional_float(msg_dict.get("zgyro")),
                "xmag": _optional_float(msg_dict.get("xmag")),
                "ymag": _optional_float(msg_dict.get("ymag")),
                "zmag": _optional_float(msg_dict.get("zmag")),
            })

        elif msg_type == "VIBRATION":
            self._append_history("vibration", {
                "t": time.time(),
                "x": _optional_float(msg_dict.get("vibration_x")),
                "y": _optional_float(msg_dict.get("vibration_y")),
                "z": _optional_float(msg_dict.get("vibration_z")),
            })

        elif msg_type == "BATTERY_STATUS":
            msg_dict = self._normalise_battery_status(msg_dict)
            self._append_history("battery", {
                "t": time.time(),
                "voltage": _optional_float(msg_dict.get("voltage")),
                "current": _optional_float(msg_dict.get("current_battery")),
                "remaining": _optional_float(msg_dict.get("battery_remaining")),
            })

        elif msg_type == "POWER_STATUS":
            msg_dict["Vcc"] = float(msg_dict.get("Vcc", 0) or 0) / 1000.0
            msg_dict["Vservo"] = float(msg_dict.get("Vservo", 0) or 0) / 1000.0

        elif msg_type == "RC_CHANNELS":
            msg_dict = self._normalise_rc_channels(msg_dict)
            self._append_history("rc", {
                "t": time.time(),
                **{f"ch{index + 1}": value for index, value in enumerate(msg_dict.get("channels") or []) if value is not None},
            })

        elif msg_type == "SERVO_OUTPUT_RAW":
            msg_dict = self._normalise_servo_output(msg_dict)
            self._append_history("servo", {
                "t": time.time(),
                **{f"out{index + 1}": value for index, value in enumerate(msg_dict.get("outputs") or []) if value is not None},
            })

        elif msg_type == "AUTOPILOT_VERSION":
            msg_dict = self._decode_autopilot_version(msg_dict)
            table["firmware_info"] = dict(msg_dict)

        elif msg_type == "PARAM_VALUE":
            msg_dict = self._record_parameter_value(msg, msg_dict)

        table["telemetry"][msg_type] = msg_dict

    def _resolve_sysids(self, vehicle_name: str = "") -> list[int]:
        """Map tool-level vehicle_name onto MAVLink system ids (QGC 模式).

        ""   -> 默认机（第一架，绝不隐式广播）
        "all" -> 链路上所有 system
        名称  -> px4_sys1 / sys1 / 1 解析到对应 system
        """
        systems = sorted(
            sysid for sysid in self._systems if self._systems[sysid]["last_heartbeat"]
        )
        if not systems:
            systems = sorted(self._systems.keys()) or [0]
        if not vehicle_name:
            if len(systems) <= 1:
                return systems or [0]
            return [systems[0]]
        name = str(vehicle_name).strip().lower()
        if name == "all":
            return systems or [0]
        for sysid in systems:
            if name in {f"px4_sys{sysid}", f"sys{sysid}", str(sysid)}:
                return [sysid]
        return systems[:1] or [0]

    def _set_target(self, sysid: int) -> None:
        """命令执行前把 MAVLink target 指向目标 system。"""
        if self._mavlink is not None:
            try:
                self._mavlink.target_system = int(sysid)
                self._mavlink.target_component = 1
            except Exception:
                pass

    def _append_history(self, key: str, entry: dict[str, Any], sysid: int = 0) -> None:
        history = self._systems_history.setdefault(int(sysid), {}).get(key)
        if history is not None:
            history.append(entry)

    @staticmethod
    def _quaternion_to_euler_deg(value: Any) -> dict[str, float] | None:
        if not isinstance(value, (list, tuple)) or len(value) < 4:
            return None
        try:
            w, x, y, z = [float(item or 0.0) for item in value[:4]]
            sinr_cosp = 2.0 * (w * x + y * z)
            cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
            roll = math.atan2(sinr_cosp, cosr_cosp)

            sinp = 2.0 * (w * y - z * x)
            if abs(sinp) >= 1:
                pitch = math.copysign(math.pi / 2, sinp)
            else:
                pitch = math.asin(sinp)

            siny_cosp = 2.0 * (w * z + x * y)
            cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
            yaw = math.atan2(siny_cosp, cosy_cosp)
            return {
                "roll_deg": math.degrees(roll),
                "pitch_deg": math.degrees(pitch),
                "yaw_deg": (math.degrees(yaw) + 360.0) % 360.0,
            }
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalise_scaled_imu(msg_type: str, msg_dict: dict[str, Any]) -> dict[str, Any]:
        data = dict(msg_dict)
        if msg_type == "RAW_IMU":
            data["unit"] = "raw"
        else:
            data["unit"] = "mG / mrad/s / mgauss"
        for field in ("xacc", "yacc", "zacc", "xgyro", "ygyro", "zgyro", "xmag", "ymag", "zmag"):
            data[field] = _optional_float(data.get(field))
        return data

    @staticmethod
    def _normalise_highres_imu(msg_dict: dict[str, Any]) -> dict[str, Any]:
        data = dict(msg_dict)
        data["unit"] = "m/s^2 / rad/s / gauss"
        for field in (
            "xacc", "yacc", "zacc", "xgyro", "ygyro", "zgyro", "xmag", "ymag", "zmag",
            "abs_pressure", "diff_pressure", "pressure_alt", "temperature",
        ):
            data[field] = _optional_float(data.get(field))
        return data

    @staticmethod
    def _normalise_battery_status(msg_dict: dict[str, Any]) -> dict[str, Any]:
        data = dict(msg_dict)
        voltages = data.get("voltages") or []
        if isinstance(voltages, (list, tuple)):
            cells = [
                round(float(value) / 1000.0, 3)
                for value in voltages
                if value not in (None, 0, 65535)
            ]
        else:
            cells = []
        data["cell_voltages"] = cells
        data["voltage"] = round(sum(cells), 3) if cells else None
        current_raw = _optional_float(data.get("current_battery"))
        data["current_battery"] = None if current_raw is None or current_raw == -1 else round(current_raw / 100.0, 2)
        return data

    @staticmethod
    def _normalise_rc_channels(msg_dict: dict[str, Any]) -> dict[str, Any]:
        data = dict(msg_dict)
        channels: list[int | None] = []
        for index in range(1, 19):
            value = _optional_int(data.get(f"chan{index}_raw"))
            channels.append(value if value and value != 65535 else None)
        data["channels"] = channels
        data["valid_channels"] = [value for value in channels if value is not None]
        return data

    @staticmethod
    def _normalise_servo_output(msg_dict: dict[str, Any]) -> dict[str, Any]:
        data = dict(msg_dict)
        outputs: list[int | None] = []
        for index in range(1, 17):
            value = _optional_int(data.get(f"servo{index}_raw"))
            outputs.append(value if value and value != 65535 else None)
        data["outputs"] = outputs
        data["valid_outputs"] = [value for value in outputs if value is not None]
        return data

    def _update_position_from_global(self, gps: dict[str, Any], sysid: int = 0) -> None:
        # Prefer LOCAL_POSITION_NED. GLOBAL_POSITION_INT is only a fallback.
        table = self._system_table(sysid)
        if time.time() - table["last_local_position"] < 2.0:
            return

        lat = float(gps.get("lat", 0.0) or 0.0)
        lon = float(gps.get("lon", 0.0) or 0.0)
        if self._outdoor and lat and lon:
            if table["gps_origin"] is None:
                table["gps_origin"] = {"lat": lat, "lon": lon}
            earth_radius_m = 6371000.0
            dlat = math.radians(lat - table["gps_origin"]["lat"])
            dlon = math.radians(lon - table["gps_origin"]["lon"])
            table["position"]["x"] = dlat * earth_radius_m
            table["position"]["y"] = dlon * earth_radius_m * math.cos(math.radians(table["gps_origin"]["lat"]))
        table["position"]["z"] = -abs(float(gps.get("relative_alt", 0.0) or 0.0))
        table["velocity"] = {
            "vx": float(gps.get("vx", 0.0) or 0.0),
            "vy": float(gps.get("vy", 0.0) or 0.0),
            "vz": float(gps.get("vz", 0.0) or 0.0),
        }

    def _request_message_intervals(self) -> None:
        if not self.is_connected:
            return
        rates_hz = {
            mavutil.mavlink.MAVLINK_MSG_ID_HEARTBEAT: 2.0,
            mavutil.mavlink.MAVLINK_MSG_ID_LOCAL_POSITION_NED: 20.0,
            mavutil.mavlink.MAVLINK_MSG_ID_GLOBAL_POSITION_INT: 10.0,
            mavutil.mavlink.MAVLINK_MSG_ID_ATTITUDE: 20.0,
            mavutil.mavlink.MAVLINK_MSG_ID_SYS_STATUS: 1.0,
            mavutil.mavlink.MAVLINK_MSG_ID_EXTENDED_SYS_STATE: 2.0,
            mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT: 1.0,
            mavutil.mavlink.MAVLINK_MSG_ID_MISSION_CURRENT: 2.0,
            mavutil.mavlink.MAVLINK_MSG_ID_VFR_HUD: 5.0,
        }
        optional_rates = {
            "MAVLINK_MSG_ID_ATTITUDE_TARGET": 20.0,
            "MAVLINK_MSG_ID_HOME_POSITION": 0.5,
            "MAVLINK_MSG_ID_RAW_IMU": 5.0,
            "MAVLINK_MSG_ID_SCALED_IMU": 20.0,
            "MAVLINK_MSG_ID_SCALED_IMU2": 5.0,
            "MAVLINK_MSG_ID_SCALED_IMU3": 5.0,
            "MAVLINK_MSG_ID_HIGHRES_IMU": 20.0,
            "MAVLINK_MSG_ID_VIBRATION": 5.0,
            "MAVLINK_MSG_ID_RC_CHANNELS": 5.0,
            "MAVLINK_MSG_ID_SERVO_OUTPUT_RAW": 5.0,
            "MAVLINK_MSG_ID_BATTERY_STATUS": 2.0,
            "MAVLINK_MSG_ID_POWER_STATUS": 1.0,
        }
        for attr, rate_hz in optional_rates.items():
            msg_id = getattr(mavutil.mavlink, attr, None)
            if msg_id is not None:
                rates_hz[int(msg_id)] = rate_hz
        for msg_id, rate_hz in rates_hz.items():
            interval_us = int(1_000_000 / rate_hz)
            try:
                self._mavlink.mav.command_long_send(
                    self._mavlink.target_system,
                    self._mavlink.target_component,
                    mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
                    0,
                    msg_id,
                    interval_us,
                    0,
                    0,
                    0,
                    0,
                    0,
                )
            except Exception:
                logger.debug(f"Failed to request MAVLink message interval for {msg_id}")

    def arm(self, vehicle_name: str = "") -> bool:
        """多机：vehicle_name（""=默认机 / all=全部 / px4_sysN）解析后逐机执行。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            self._last_action_error = "no MAVLink system available"
            return False
        ok = True
        for sysid in targets:
            self._set_target(sysid)
            if not self._arm_one():
                ok = False
                break
        return ok

    def _arm_one(self) -> bool:
        self._last_action_error = ""
        if not self.is_connected:
            self._last_action_error = "MAVLink is not connected"
            return False
        if self._link_is_stale():
            self._last_action_error = self._stale_link_message()
            return False
        if self._is_armed():
            return True
        if self._is_px4() and not self._prepare_px4_mode_for_arm():
            return False
        # PX4 commander 在预检/状态机冷却窗口（~1s）内会以
        # MAV_RESULT_TEMPORARILY_REJECTED 拒绝 arm，之后重试通常成功；
        # 重试间隔留出冷却期，并把飞控 STATUSTEXT 并入错误信息
        attempts = 3 if self._is_px4() else 1
        for attempt in range(attempts):
            self._mavlink.mav.command_long_send(
                self._mavlink.target_system,
                self._mavlink.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0,
                1,
                0,
                0,
                0,
                0,
                0,
                0,
            )
            ack_ok = self._wait_command_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, timeout=5.0)
            if ack_ok:
                armed = self._wait_until(lambda: self._is_armed(), timeout=6.0)
                if armed:
                    return True
                self._last_action_error = self._with_status_text(
                    "Flight controller accepted the arm command but did not enter armed state; check PX4 preflight checks and the safety switch."
                )
                return False
            if attempt < attempts - 1:
                logger.warning(
                    f"arm attempt {attempt + 1} rejected: {self._last_action_error}; retrying after PX4 arming window"
                )
                time.sleep(1.2)
        return False

    def disarm(self, vehicle_name: str = "") -> bool:
        """多机：vehicle_name（""=默认机 / all=全部 / px4_sysN）解析后逐机执行。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            self._last_action_error = "no MAVLink system available"
            return False
        ok = True
        for sysid in targets:
            self._set_target(sysid)
            if not self._disarm_one():
                ok = False
                break
        return ok

    def _disarm_one(self) -> bool:
        self._last_action_error = ""
        if not self.is_connected:
            self._last_action_error = "MAVLink is not connected"
            return False
        if self._link_is_stale():
            self._last_action_error = self._stale_link_message()
            return False
        self._stop_offboard_hold()
        self._mavlink.mav.command_long_send(
            self._mavlink.target_system,
            self._mavlink.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        ack_ok = self._wait_command_ack(mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, timeout=5.0)
        if not ack_ok:
            return False
        disarmed = self._wait_until(lambda: not self._is_armed(), timeout=6.0)
        if not disarmed:
            self._last_action_error = self._with_status_text(
                "Flight controller accepted the disarm command but remains armed."
            )
        return disarmed

    def takeoff(self, altitude: float = 3.0, vehicle_name: str = "") -> bool:
        """多机：vehicle_name（""=默认机 / all=全部 / px4_sysN）解析后逐机执行。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            self._last_action_error = "no MAVLink system available"
            return False
        ok = True
        for sysid in targets:
            self._set_target(sysid)
            if not self._takeoff_one(altitude, vehicle_name):
                ok = False
                break
        return ok

    def _takeoff_one(self, altitude: float = 3.0, vehicle_name: str = "") -> bool:
        self._last_action_error = ""
        if not self.is_connected:
            self._last_action_error = "MAVLink is not connected"
            return False
        if self._link_is_stale():
            self._last_action_error = self._stale_link_message()
            return False
        altitude = max(0.5, abs(float(altitude)))

        offboard_target = {
            "x": float(self._position.get("x", 0.0) or 0.0),
            "y": float(self._position.get("y", 0.0) or 0.0),
            "z": -altitude,
        }
        if self._takeoff_via_offboard(offboard_target, altitude, vehicle_name):
            return True

        # Fall back to the native takeoff command when OFFBOARD is unavailable.
        # This keeps older PX4/SITL configurations usable while the preferred
        # path remains a continuous local-position setpoint loop.
        if not self._is_armed() and not self.arm(vehicle_name):
            logger.warning("takeoff aborted: vehicle did not arm")
            return False

        self._mavlink.mav.command_long_send(
            self._mavlink.target_system,
            self._mavlink.target_component,
            mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
            0,
            0,
            0,
            0,
            math.nan,
            math.nan,
            math.nan,
            altitude,
        )
        ack_ok = self._wait_command_ack(mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, timeout=5.0)
        if not ack_ok:
            return False
        timeout = max(20.0, min(45.0, altitude / 0.3 + 15.0))
        minimum_reached = max(0.5, altitude * 0.85, altitude - 0.5)
        deadline = time.time() + timeout
        stable_since: float | None = None
        while time.time() < deadline:
            self.update_telemetry(timeout=0.2)
            current_altitude = self._current_altitude_m()
            if current_altitude >= minimum_reached:
                mode = self._current_mode()
                return mode in {"LOITER", "POSCTL"} or self.hover(vehicle_name)

            mode = self._current_mode()
            vertical_speed = abs(float(self._velocity.get("vz", 0.0) or 0.0))
            native_takeoff_stopped = (
                current_altitude >= 0.5
                and mode in {"LOITER", "POSCTL"}
                and vertical_speed <= 0.2
            )
            if native_takeoff_stopped:
                stable_since = stable_since or time.time()
                if time.time() - stable_since >= 1.0:
                    break
            else:
                stable_since = None

        current_altitude = self._current_altitude_m()
        if self._is_armed() and current_altitude >= 0.5:
            logger.info(
                f"PX4 native takeoff stopped at {current_altitude:.2f}m; "
                f"continuing to {altitude:.2f}m with local position control"
            )
            return self.move_to_position(
                self._position["x"],
                self._position["y"],
                -altitude,
                min(1.5, self._max_velocity),
                vehicle_name,
                arrival_threshold_m=min(0.25, self._arrival_threshold_m),
            )

        logger.warning(
            f"takeoff did not reach target altitude: ack={ack_ok} "
            f"current_altitude={current_altitude:.2f} "
            f"target_altitude={altitude:.2f} mode={self._current_mode()} "
            f"armed={self._is_armed()}"
        )
        self._last_action_error = self._with_status_text(
            f"Takeoff command was accepted but target altitude {altitude:.1f}m was not reached (current {current_altitude:.1f}m)."
        )
        return False

    def _takeoff_via_offboard(self, target: dict[str, float], altitude: float, vehicle_name: str = "") -> bool:
        if not self._prime_offboard_position(target):
            self._stop_offboard_hold()
            self._last_action_error = self._with_status_text(
                "takeoff interrupted by emergency stop / cancel"
            )
            return False
        if not self._set_mode_one("OFFBOARD") and self._current_mode() != "OFFBOARD":
            self._stop_offboard_hold()
            self._last_action_error = self._with_status_text(
                f"OFFBOARD takeoff rejected before arming; current mode={self._current_mode()}"
            )
            return False
        if not self._is_armed() and not self.arm(vehicle_name):
            self._stop_offboard_hold()
            logger.warning("offboard takeoff aborted: vehicle did not arm")
            return False

        timeout = max(20.0, min(45.0, altitude / 0.4 + 15.0))
        deadline = time.time() + timeout
        arrival_threshold = max(0.15, min(0.45, self._arrival_threshold_m))
        period = 1.0 / self._setpoint_hz
        while time.time() < deadline:
            if self._stop_requested():
                self._finish_offboard_position_hold(dict(self._position))
                self._last_action_error = self._with_status_text(
                    "takeoff interrupted by emergency stop / cancel"
                )
                return False
            self._send_position_setpoint(target)
            self.update_telemetry(timeout=0.02)
            if self._distance_to(target) <= arrival_threshold:
                return self._finish_offboard_position_hold(target)
            time.sleep(period)

        self._finish_offboard_position_hold(dict(self._position))
        self._last_action_error = self._with_status_text(
            f"OFFBOARD takeoff timed out at {self._current_altitude_m():.1f}m while targeting {altitude:.1f}m"
        )
        return False

    def land(self, vehicle_name: str = "") -> bool:
        """多机：vehicle_name（""=默认机 / all=全部 / px4_sysN）解析后逐机执行。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            self._last_action_error = "no MAVLink system available"
            return False
        ok = True
        for sysid in targets:
            self._set_target(sysid)
            if not self._land_one():
                ok = False
                break
        return ok

    def _land_one(self) -> bool:
        if not self.is_connected:
            return False
        mode_ok = self._set_mode_one("LAND")
        self._mavlink.mav.command_long_send(
            self._mavlink.target_system,
            self._mavlink.target_component,
            mavutil.mavlink.MAV_CMD_NAV_LAND,
            0,
            0,
            0,
            0,
            math.nan,
            0,
            0,
            0,
        )
        ack_ok = self._wait_command_ack(mavutil.mavlink.MAV_CMD_NAV_LAND, timeout=3.0)
        if mode_ok or ack_ok:
            self._stop_offboard_hold()
        return mode_ok or ack_ok

    def hover(self, vehicle_name: str = "") -> bool:
        """多机：vehicle_name（""=默认机 / all=全部 / px4_sysN）解析后逐机执行。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            self._last_action_error = "no MAVLink system available"
            return False
        ok = True
        for sysid in targets:
            self._set_target(sysid)
            if not self._hover_one():
                ok = False
                break
        return ok

    def _hover_one(self) -> bool:
        if not self.is_connected:
            return False
        if self._current_altitude_m() < 0.5:
            # on the ground: stop the current target (never re-resolve to the
            # first vehicle), mirroring stop()'s ground branch
            self._stop_offboard_hold()
            return self._stream_velocity_setpoint({"vx": 0.0, "vy": 0.0, "vz": 0.0}, duration=0.5)
        current = dict(self._position)
        mode = self._current_mode()
        if mode in {"LOITER", "POSCTL"}:
            return True
        if mode == "OFFBOARD":
            return self._finish_offboard_position_hold(current)
        return self._set_mode_one("LOITER") or self._set_mode_one("POSCTL")

    def stop(self, vehicle_name: str = "") -> bool:
        if not self.is_connected:
            return False
        if self._current_altitude_m() >= 0.5:
            return self.hover(vehicle_name)
        self._stop_offboard_hold()
        return self._stream_velocity_setpoint({"vx": 0.0, "vy": 0.0, "vz": 0.0}, duration=0.5)

    def move_to_position(
        self,
        x: float,
        y: float,
        z: float,
        velocity: float = 2.0,
        vehicle_name: str = "",
        arrival_threshold_m: float | None = None,
    ) -> bool:
        """多机：vehicle_name（""=默认机 / all=全部 / px4_sysN）解析后逐机执行。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            self._last_action_error = "no MAVLink system available"
            return False
        ok = True
        for sysid in targets:
            self._set_target(sysid)
            if not self._move_to_position_one(x, y, z, velocity, arrival_threshold_m):
                ok = False
                break
        return ok

    def _move_to_position_one(
        self,
        x: float,
        y: float,
        z: float,
        velocity: float = 2.0,
        arrival_threshold_m: float | None = None,
    ) -> bool:
        if not self.is_connected:
            self._last_path_error = {"stage": "move_to_position", "message": "not connected", "target": {"x": x, "y": y, "z": z}}
            return False
        target = {"x": float(x), "y": float(y), "z": float(z)}
        velocity = max(0.2, min(abs(float(velocity)), self._max_velocity))

        if not self._prime_offboard_position(target):
            self._stop_offboard_hold()
            self._last_path_error = {
                "stage": "move_to_position",
                "message": "interrupted by emergency stop / cancel",
                "target": target,
            }
            return False
        if not self._set_mode_one("OFFBOARD"):
            if self._current_mode() != "OFFBOARD":
                self._stop_offboard_hold()
            self._last_path_error = {
                "stage": "move_to_position",
                "message": "OFFBOARD mode rejected",
                "target": target,
                "position": dict(self._position),
                "mode": self._current_mode(),
                "armed": self._is_armed(),
                "altitude_m": round(self._current_altitude_m(), 3),
            }
            return False

        start = time.time()
        distance = self._distance_to(target)
        timeout = max(self._arrival_timeout_s, distance / max(velocity, 0.2) + 8.0)
        arrival_threshold = max(
            0.05,
            float(arrival_threshold_m or self._arrival_threshold_m),
        )
        period = 1.0 / self._setpoint_hz
        while time.time() - start < timeout:
            if self._stop_requested():
                # leave OFFBOARD cleanly at the current position so the
                # vehicle holds instead of continuing toward the target
                self._finish_offboard_position_hold(dict(self._position))
                self._last_path_error = {
                    "stage": "move_to_position",
                    "message": "interrupted by emergency stop / cancel",
                    "target": target,
                    "position": dict(self._position),
                }
                return False
            self._send_position_setpoint(target)
            self.update_telemetry(timeout=0.02)
            if self._distance_to(target) <= arrival_threshold:
                return self._finish_offboard_position_hold(target)
            time.sleep(period)
        self._finish_offboard_position_hold(dict(self._position))
        self._last_path_error = {
            "stage": "move_to_position",
            "message": "arrival timeout",
            "target": target,
            "position": dict(self._position),
            "distance_m": round(self._distance_to(target), 3),
            "timeout_s": round(timeout, 3),
            "mode": self._current_mode(),
            "armed": self._is_armed(),
            "altitude_m": round(self._current_altitude_m(), 3),
        }
        return False

    def move_by_velocity(self, vx: float, vy: float, vz: float, duration: float = 0.0, vehicle_name: str = "") -> bool:
        """多机：vehicle_name（""=默认机 / all=全部 / px4_sysN）解析后逐机执行。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            self._last_action_error = "no MAVLink system available"
            return False
        ok = True
        for sysid in targets:
            self._set_target(sysid)
            if not self._move_by_velocity_one(vx, vy, vz, duration):
                ok = False
                break
        return ok

    # ------------------------------------------------------------------
    # Formation velocity-control protocol (duck-typed for FormationController)
    #
    # The formation loop re-issues a setpoint every tick (~10Hz), forming a
    # continuous OFFBOARD stream. These methods deliberately avoid the
    # single-shot streaming semantics of move_by_velocity (which blocks for
    # `duration` and then leaves OFFBOARD).
    # ------------------------------------------------------------------

    def send_velocity_setpoint(self, vx: float, vy: float, vz: float, vehicle_name: str = "") -> bool:
        """Send ONE velocity setpoint per vehicle and return immediately."""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            self._last_action_error = "no MAVLink system available"
            return False
        if not self.is_connected:
            self._last_action_error = "MAVLink link is not connected"
            return False
        ok = True
        for sysid in targets:
            self._set_target(sysid)
            try:
                self._send_velocity_setpoint(
                    self._limit_velocity({"vx": float(vx), "vy": float(vy), "vz": float(vz)})
                )
            except Exception:
                ok = False
                break
        return ok

    def prepare_velocity_control(self, vehicle_name: str = "") -> bool:
        """Enter OFFBOARD for the formation velocity loop.

        Stops the shared hold/velocity thread first so it never competes with
        the formation loop's setpoint stream.
        """
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            self._last_action_error = "no MAVLink system available"
            return False
        ok = True
        for sysid in targets:
            self._set_target(sysid)
            self._stop_offboard_hold()
            if not self._set_mode_one("OFFBOARD") and self._current_mode() != "OFFBOARD":
                self._last_action_error = self._with_status_text(
                    f"OFFBOARD activation rejected for {sysid}; current mode={self._current_mode()}"
                )
                ok = False
                break
        return ok

    def is_velocity_control_active(self, vehicle_name: str = "") -> bool:
        """True while every resolved vehicle is still in OFFBOARD with a fresh
        heartbeat.

        RC takeover or an operator mode switch exits OFFBOARD; a stale heartbeat
        (link loss) also counts as inactive so the formation loop stops instead
        of commanding a dead link. The formation loop polls this each tick.
        """
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            return False
        for sysid in targets:
            self._set_target(sysid)
            if self._current_mode() != "OFFBOARD":
                return False
            heartbeat_age = time.time() - self._system_table(sysid)["last_heartbeat"]
            if heartbeat_age > 2.0:
                return False
        return True

    def release_velocity_control(self, vehicle_name: str = "") -> bool:
        """Leave OFFBOARD safely: LOITER/POSCTL, or a position-hold stream."""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            return False
        ok = True
        for sysid in targets:
            self._set_target(sysid)
            if self._current_mode() == "OFFBOARD":
                if not self._finish_offboard_position_hold(dict(self._position)):
                    ok = False
            else:
                self._stop_offboard_hold()
        return ok

    # ------------------------------------------------------------------
    # External stop/cancel signal (emergency stop preemption)
    # ------------------------------------------------------------------

    def set_stop_provider(self, stop_provider: Callable[[], bool] | None = None) -> None:
        """Wire an external stop/cancel signal into blocking flight commands.

        The provider is polled while a single-vehicle position move, takeoff,
        or hold stream is in flight. When it returns True the command exits
        cleanly (OFFBOARD left through a position hold / LOITER) instead of
        letting the vehicle keep flying after an emergency stop.
        """
        self._stop_provider = stop_provider

    def _stop_requested(self) -> bool:
        provider = getattr(self, "_stop_provider", None)
        try:
            return bool(provider and provider())
        except Exception:
            return False

    def _sleep_interruptible(self, seconds: float) -> bool:
        """Sleep in small slices, returning False early when a stop is requested."""
        deadline = time.time() + max(0.0, float(seconds))
        while time.time() < deadline:
            if self._stop_requested():
                return False
            time.sleep(min(0.05, deadline - time.time()))
        return True

    def _move_by_velocity_one(self, vx: float, vy: float, vz: float, duration: float = 0.0) -> bool:
        if not self.is_connected:
            return False
        velocity = self._limit_velocity({"vx": float(vx), "vy": float(vy), "vz": float(vz)})
        duration = max(0.8, float(duration or 0.8))
        if not self._prime_offboard_velocity(velocity):
            self._stop_offboard_hold()
            return False
        if not self._set_mode_one("OFFBOARD"):
            if self._current_mode() != "OFFBOARD":
                self._stop_offboard_hold()
            return False
        streamed = self._stream_velocity_setpoint(velocity, duration=duration)
        self.update_telemetry(timeout=0.1)
        # Always leave OFFBOARD through the safe hold path, even when the
        # stream was interrupted by an emergency stop.
        held = self._finish_offboard_position_hold(dict(self._position))
        return streamed and held

    def move_on_path(self, waypoints: list[dict], velocity: float = 2.0, vehicle_name: str = "") -> bool:
        """多机：vehicle_name（""=默认机 / all=全部 / px4_sysN）解析后逐机执行。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            self._last_action_error = "no MAVLink system available"
            return False
        ok = True
        for sysid in targets:
            self._set_target(sysid)
            if not self._move_on_path_one(waypoints, velocity):
                ok = False
                break
        return ok

    def _move_on_path_one(self, waypoints: list[dict], velocity: float = 2.0) -> bool:
        if not self.is_connected:
            self._last_path_error = {"stage": "move_on_path", "message": "not connected"}
            return False
        self._last_path_error = {}
        for index, waypoint in enumerate(waypoints):
            ok = self.move_to_position(
                float(waypoint.get("x", 0.0) or 0.0),
                float(waypoint.get("y", 0.0) or 0.0),
                float(waypoint.get("z", -3.0) or -3.0),
                velocity,
                vehicle_name,
            )
            if not ok:
                self._last_path_error = {
                    **self._last_path_error,
                    "stage": self._last_path_error.get("stage", "move_on_path"),
                    "failed_waypoint_index": index,
                    "failed_waypoint": {
                        "x": float(waypoint.get("x", 0.0) or 0.0),
                        "y": float(waypoint.get("y", 0.0) or 0.0),
                        "z": float(waypoint.get("z", -3.0) or -3.0),
                    },
                }
                return False
        return True

    def get_last_path_error(self) -> dict[str, Any]:
        return dict(self._last_path_error)

    def get_cached_status(self, vehicle_name: str = "") -> DroneStatus:
        """Return the latest decoded telemetry without reading from the MAVLink socket."""
        if not self.is_connected:
            return DroneStatus(mode="DISCONNECTED")
        return self._status_from_current_telemetry()

    def get_status(self, vehicle_name: str = "") -> DroneStatus:
        if not self.is_connected:
            return DroneStatus(mode="DISCONNECTED")

        self.update_telemetry(timeout=0.08)
        sysids = self._resolve_sysids(vehicle_name)
        sysid = sysids[0] if sysids else self._active_sysid
        return self._status_from_current_telemetry(sysid=sysid)

    def _status_from_current_telemetry(self, sysid: int | None = None) -> DroneStatus:
        table = self._system_table(sysid if sysid is not None else self._active_sysid)
        with self._lock:
            telemetry = table["telemetry"]
            gps = dict(telemetry.get("GLOBAL_POSITION_INT", {}) or {})
            att = dict(telemetry.get("ATTITUDE", {}) or {})
            heartbeat = dict(telemetry.get("HEARTBEAT", {}) or {})
            sys_status = dict(telemetry.get("SYS_STATUS", {}) or {})
            ext = dict(telemetry.get("EXTENDED_SYS_STATE", {}) or {})
            gps_raw = dict(telemetry.get("GPS_RAW_INT", {}) or {})
            home_position = dict(telemetry.get("HOME_POSITION", {}) or {})
            position = dict(table["position"])
            velocity = dict(table["velocity"])
            last_heartbeat = table["last_heartbeat"]
            autopilot = table["autopilot"]
            vehicle_type = table["vehicle_type"]
            mav = self._mavlink

        landed_state = ext.get("landed_state")
        if landed_state is not None:
            flying = bool(heartbeat.get("armed", False)) and landed_state == mavutil.mavlink.MAV_LANDED_STATE_IN_AIR
        else:
            flying = bool(heartbeat.get("armed", False)) and abs(float(position.get("z", 0.0) or 0.0)) > 0.5

        gps_payload: dict[str, float] = {}
        if gps:
            gps_payload = {
                "lat": round(float(gps.get("lat", 0.0) or 0.0), 7),
                "lon": round(float(gps.get("lon", 0.0) or 0.0), 7),
                "alt": round(float(gps.get("relative_alt", 0.0) or 0.0), 2),
                "absolute_alt": round(float(gps.get("alt", 0.0) or 0.0), 2),
            }

        heartbeat_age = time.time() - last_heartbeat if last_heartbeat else math.inf
        local_position_age = time.time() - table["last_local_position"] if table["last_local_position"] else math.inf
        global_position_age = time.time() - table["last_global_position"] if table["last_global_position"] else math.inf
        gps_fix_type = gps_raw.get("fix_type")
        try:
            gps_fix_level = int(gps_fix_type or 0)
        except (TypeError, ValueError):
            gps_fix_level = 0
        has_reliable_gps = bool(
            gps_payload
            and gps_fix_level >= 3
            and abs(float(gps_payload.get("lat", 0.0) or 0.0)) > 0.001
            and abs(float(gps_payload.get("lon", 0.0) or 0.0)) > 0.001
            and math.isfinite(global_position_age)
            and global_position_age <= 3.0
        )
        horizontal_accuracy = _optional_float(gps_raw.get("horizontal_accuracy_m"))
        if horizontal_accuracy is not None and horizontal_accuracy > 50.0:
            has_reliable_gps = False
        has_recent_local = math.isfinite(local_position_age) and local_position_age <= 3.0
        map_position_valid = has_reliable_gps or (not self._real_vehicle and has_recent_local)
        navigation_position_valid = has_reliable_gps or (has_recent_local and (not self._real_vehicle or flying))
        battery_voltage = sys_status.get("voltage_battery")
        try:
            battery_voltage_value = float(battery_voltage)
            if battery_voltage_value <= 0.0 or battery_voltage_value >= 65.0:
                battery_voltage = None
        except (TypeError, ValueError):
            battery_voltage = None
        extra = {
            "heading_deg": round(float(gps.get("hdg", 0.0) or 0.0), 1),
            "heartbeat_age_s": round(heartbeat_age, 2) if math.isfinite(heartbeat_age) else None,
            "link_stale": heartbeat_age > 5.0 if math.isfinite(heartbeat_age) else True,
            "offboard_hold_active": self._offboard_hold_active(),
            "custom_mode": heartbeat.get("custom_mode"),
            "base_mode": heartbeat.get("base_mode"),
            "system_status": heartbeat.get("system_status"),
            "autopilot": autopilot,
            "vehicle_type": vehicle_type,
            "real_vehicle": self._real_vehicle,
            "position_source": "gps" if has_reliable_gps else ("local_position_ned" if has_recent_local else "none"),
            "map_position_valid": map_position_valid,
            "navigation_position_valid": navigation_position_valid,
            "local_position_age_s": round(local_position_age, 2) if math.isfinite(local_position_age) else None,
            "global_position_age_s": round(global_position_age, 2) if math.isfinite(global_position_age) else None,
            "active_link": self.get_connection_info(),
            "parameter_status": self.get_parameter_status(),
        }
        if table["firmware_info"]:
            extra["firmware"] = dict(table["firmware_info"])
        if mav is not None:
            extra["gcs_source_system"] = getattr(mav, "source_system", None)
            extra["gcs_source_component"] = getattr(mav, "source_component", None)
            extra["mavlink_wire_protocol"] = getattr(mavutil.mavlink, "WIRE_PROTOCOL_VERSION", None)
        if gps_raw:
            extra["gps_fix_type"] = gps_raw.get("fix_type")
            extra["satellites_visible"] = gps_raw.get("satellites_visible")
            extra["gps_horizontal_accuracy_m"] = gps_raw.get("horizontal_accuracy_m")
            extra["gps_vertical_accuracy_m"] = gps_raw.get("vertical_accuracy_m")
        home_lat = _optional_float(home_position.get("latitude"))
        home_lon = _optional_float(home_position.get("longitude"))
        if home_lat is not None and home_lon is not None and abs(home_lat) > 0.001 and abs(home_lon) > 0.001:
            extra["home_position"] = {
                "lat": round(home_lat, 7),
                "lon": round(home_lon, 7),
                "alt": round(float(home_position.get("altitude", 0.0) or 0.0), 2),
                "source": "MAVLink HOME_POSITION",
            }

        return DroneStatus(
            position_ned={
                "x": round(float(position.get("x", 0.0) or 0.0), 3),
                "y": round(float(position.get("y", 0.0) or 0.0), 3),
                "z": round(float(position.get("z", 0.0) or 0.0), 3),
            },
            velocity_ned={
                "vx": round(float(velocity.get("vx", 0.0) or 0.0), 3),
                "vy": round(float(velocity.get("vy", 0.0) or 0.0), 3),
                "vz": round(float(velocity.get("vz", 0.0) or 0.0), 3),
            },
            attitude_rad={
                "roll": round(float(att.get("roll", 0.0) or 0.0), 4),
                "pitch": round(float(att.get("pitch", 0.0) or 0.0), 4),
                "yaw": round(float(att.get("yaw", 0.0) or 0.0), 4),
            },
            armed=bool(heartbeat.get("armed", False)),
            flying=flying,
            mode=str(heartbeat.get("mode") or self._decode_mode(int(heartbeat.get("custom_mode", -1) or -1))),
            gps=gps_payload or None,
            battery_voltage=battery_voltage,
            extra=extra,
        )

    def get_connection_info(self) -> dict[str, Any]:
        """Return the active MAVLink endpoint and target metadata."""
        with self._lock:
            details = dict(self._active_connection_details)
            mav = self._mavlink
            heartbeat_age = time.time() - self._last_heartbeat if self._last_heartbeat else math.inf
        if mav is not None:
            details.setdefault("system_id", getattr(mav, "target_system", None))
            details.setdefault("component_id", getattr(mav, "target_component", None))
            details["source_system"] = getattr(mav, "source_system", None)
            details["source_component"] = getattr(mav, "source_component", None)
        details["connected"] = self.is_connected
        details["real_vehicle"] = self._real_vehicle
        details["heartbeat_age_s"] = round(heartbeat_age, 2) if math.isfinite(heartbeat_age) else None
        details["mavlink_wire_protocol"] = getattr(mavutil.mavlink, "WIRE_PROTOCOL_VERSION", None)
        return details

    def get_firmware_info(self, force: bool = False, timeout: float = 3.0) -> dict[str, Any]:
        """Request and return AUTOPILOT_VERSION, matching QGC's initial connect flow."""
        if not self.is_connected:
            return {"status": "error", "message": "not connected"}
        if self._firmware_info and not force:
            return {"status": "ok", "cached": True, **dict(self._firmware_info)}

        msg_id = mavutil.mavlink.MAVLINK_MSG_ID_AUTOPILOT_VERSION
        attempts = 2 if force else 1
        deadline_per_attempt = max(0.5, float(timeout) / attempts)
        last_ack: dict[str, Any] | None = None

        for _ in range(attempts):
            try:
                self._mavlink.mav.command_long_send(
                    self._mavlink.target_system,
                    self._mavlink.target_component,
                    mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
                    0,
                    msg_id,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                )
            except Exception as exc:
                return {"status": "error", "message": str(exc)}

            deadline = time.time() + deadline_per_attempt
            while time.time() < deadline:
                with self._lock:
                    msg = self._mavlink.recv_match(blocking=True, timeout=0.12)
                if msg is None:
                    continue
                msg_type = msg.get_type()
                if msg_type == "BAD_DATA":
                    continue
                if msg_type == "COMMAND_ACK":
                    ack = msg.to_dict()
                    if int(ack.get("command", -1) or -1) == mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE:
                        last_ack = ack
                self._handle_message(msg)
                if msg_type == "AUTOPILOT_VERSION" and self._firmware_info:
                    return {"status": "ok", "cached": False, **dict(self._firmware_info)}

        payload: dict[str, Any] = {
            "status": "error",
            "message": "AUTOPILOT_VERSION not received",
        }
        if last_ack:
            payload["command_ack"] = last_ack
        if self._firmware_info:
            payload.update({"cached": True, **dict(self._firmware_info)})
            payload["status"] = "ok"
            payload["message"] = "using cached AUTOPILOT_VERSION"
        return payload

    def get_parameter_status(self) -> dict[str, Any]:
        """Return parameter download/cache state without blocking on the MAVLink stream."""
        return self._parameter_snapshot(include_parameters=False)

    def get_parameters(
        self,
        refresh: bool = False,
        timeout: float = 20.0,
        query: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return cached PX4 parameters, downloading them on first request.

        This is intentionally read-only. It mirrors the non-FTP QGC path:
        PARAM_REQUEST_LIST followed by PARAM_VALUE collection.
        """
        if not self.is_connected:
            return self._parameter_snapshot(query=query, limit=limit, offset=offset)
        if refresh or not self._parameters:
            return self.download_parameters(
                force=refresh,
                timeout=timeout,
                query=query,
                limit=limit,
                offset=offset,
            )
        self.update_telemetry(timeout=0.1)
        return self._parameter_snapshot(query=query, limit=limit, offset=offset)

    def set_parameter(
        self,
        name: str,
        value: Any,
        component_id: int | None = None,
        param_type: int | None = None,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        """Set a single PX4 parameter through MAVLink PARAM_SET and wait for PARAM_VALUE."""
        if not self.is_connected:
            return {"status": "error", "connected": False, "message": "not connected"}

        parameter_name = self._normalise_param_id(name)
        if not parameter_name:
            return {"status": "error", "connected": True, "message": "parameter name is required"}
        if len(parameter_name.encode("ascii", errors="ignore")) > 16:
            return {"status": "error", "connected": True, "message": "MAVLink PARAM_SET names must be 16 bytes or shorter"}

        with self._lock:
            cached_entries = [
                dict(entry)
                for entry in self._parameters.values()
                if entry.get("name") == parameter_name
            ]
            mav = self._mavlink

        if mav is None:
            return {"status": "error", "connected": False, "message": "MAVLink is not initialized"}

        cached = None
        if component_id is not None:
            target_component = int(component_id)
            cached = next((entry for entry in cached_entries if int(entry.get("component_id") or 0) == target_component), None)
        else:
            cached = cached_entries[0] if cached_entries else None
            target_component = int((cached or {}).get("component_id") or getattr(mav, "target_component", 1) or 1)

        type_id = int(param_type if param_type is not None else ((cached or {}).get("type") or getattr(mavutil.mavlink, "MAV_PARAM_TYPE_REAL32", 9)))
        encoded_value = self._coerce_param_set_value(value, type_id)
        if encoded_value is None:
            return {"status": "error", "connected": True, "message": f"invalid value for {parameter_name}: {value!r}"}

        sent_at = time.time()
        try:
            with self._lock:
                if not self.is_connected:
                    return {"status": "error", "connected": False, "message": "disconnected before PARAM_SET"}
                self._mavlink.mav.param_set_send(
                    self._mavlink.target_system,
                    target_component,
                    parameter_name.encode("ascii", errors="ignore"),
                    float(encoded_value),
                    type_id,
                )
        except Exception as exc:
            return {"status": "error", "connected": self.is_connected, "message": str(exc)}

        deadline = time.time() + max(0.5, float(timeout or 3.0))
        confirmed_entry: dict[str, Any] | None = None
        while time.time() < deadline:
            with self._lock:
                if not self.is_connected:
                    break
                msg = self._mavlink.recv_match(blocking=True, timeout=0.15)
            if msg is None:
                continue
            msg_type = msg.get_type()
            if msg_type == "BAD_DATA":
                continue
            self._handle_message(msg)
            if msg_type != "PARAM_VALUE":
                continue
            data = msg.to_dict()
            ack_name = self._normalise_param_id(data.get("param_id"))
            if ack_name != parameter_name:
                continue
            try:
                ack_component = int(msg.get_srcComponent())
            except Exception:
                ack_component = target_component
            if ack_component != target_component:
                continue
            with self._lock:
                confirmed_entry = dict(self._parameters.get(f"{target_component}:{parameter_name}") or {})
            break

        if confirmed_entry:
            return {
                "status": "ok",
                "connected": True,
                "name": parameter_name,
                "component_id": target_component,
                "sent_value": encoded_value,
                "parameter": confirmed_entry,
                "elapsed_s": round(time.time() - sent_at, 3),
            }

        current = cached or {}
        return {
            "status": "timeout",
            "connected": self.is_connected,
            "name": parameter_name,
            "component_id": target_component,
            "sent_value": encoded_value,
            "parameter": current,
            "message": "PARAM_VALUE confirmation not received",
        }

    @staticmethod
    def _coerce_param_set_value(value: Any, param_type: int) -> float | None:
        raw = _optional_float(value)
        if raw is None or not math.isfinite(raw):
            return None
        integer_types = {
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_UINT8", 1),
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_INT8", 2),
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_UINT16", 3),
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_INT16", 4),
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_UINT32", 5),
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_INT32", 6),
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_UINT64", 7),
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_INT64", 8),
        }
        if int(param_type or 0) in integer_types:
            return float(int(round(raw)))
        return float(raw)

    def download_parameters(
        self,
        force: bool = False,
        timeout: float = 20.0,
        component_id: int | None = None,
        query: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Request all PX4 parameters using MAVLink PARAM_REQUEST_LIST."""
        if not self.is_connected:
            return self._parameter_snapshot(query=query, limit=limit, offset=offset)
        if self._parameters and not force:
            return self._parameter_snapshot(query=query, limit=limit, offset=offset)
        if not self._parameter_download_lock.acquire(blocking=False):
            payload = self._parameter_snapshot(query=query, limit=limit, offset=offset)
            payload["status"] = "busy"
            payload["message"] = "parameter download already running"
            return payload

        try:
            target_component = int(component_id if component_id is not None else getattr(mavutil.mavlink, "MAV_COMP_ID_ALL", 0))
            self._reset_parameter_cache(state="downloading")
            with self._lock:
                self._parameter_download_started_at = time.time()
                self._parameter_download_finished_at = None
                self._parameter_last_error = ""
                if not self.is_connected:
                    return self._parameter_snapshot(query=query, limit=limit, offset=offset)
                self._mavlink.mav.param_request_list_send(
                    self._mavlink.target_system,
                    target_component,
                )

            deadline = time.time() + max(1.0, float(timeout))
            while time.time() < deadline:
                with self._lock:
                    if not self.is_connected:
                        break
                    msg = self._mavlink.recv_match(blocking=True, timeout=0.25)
                if msg is None:
                    continue
                msg_type = msg.get_type()
                if msg_type == "BAD_DATA":
                    continue
                self._handle_message(msg)
                if msg_type != "PARAM_VALUE":
                    continue
                status = self._parameter_status_locked_snapshot()
                expected = int(status.get("expected_count") or 0)
                received = int(status.get("received_count") or 0)
                if expected and received >= expected:
                    break

            with self._lock:
                status = self._parameter_status_locked_snapshot()
                expected = int(status.get("expected_count") or 0)
                received = int(status.get("received_count") or 0)
                self._parameter_download_finished_at = time.time()
                if received <= 0:
                    self._parameter_download_state = "error"
                    self._parameter_last_error = "PARAM_VALUE not received"
                elif expected and received >= expected:
                    self._parameter_download_state = "ready"
                    self._parameter_last_error = ""
                else:
                    self._parameter_download_state = "partial"
                    self._parameter_last_error = (
                        f"received {received}/{expected} parameters before timeout"
                        if expected else "parameter count was not reported"
                    )
            return self._parameter_snapshot(query=query, limit=limit, offset=offset)
        except Exception as exc:
            with self._lock:
                self._parameter_download_state = "error"
                self._parameter_download_finished_at = time.time()
                self._parameter_last_error = str(exc)
            return self._parameter_snapshot(query=query, limit=limit, offset=offset)
        finally:
            self._parameter_download_lock.release()

    def get_vehicle_telemetry_snapshot(
        self,
        include_history: bool = True,
        history_limit: int = 240,
        history_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return the lightweight live telemetry subset used by real-time settings views."""
        if self.is_connected:
            self.update_telemetry(timeout=0.02)

        connected = self.is_connected
        status = self.get_cached_status().to_dict() if connected else DroneStatus(mode="DISCONNECTED").to_dict()
        connection = self.get_connection_info()
        with self._lock:
            telemetry = {
                key: self._copy_payload(value)
                for key, value in self._telemetry.items()
                if not str(key).startswith("STALE_")
            }
            allowed_history = set(history_keys or self._telemetry_history.keys())
            histories = {
                key: list(history)[-max(1, min(int(history_limit or 240), 2400)):]
                for key, history in self._telemetry_history.items()
                if key in allowed_history
            } if include_history else {}

        sensor_health = self._sensor_health_snapshot(telemetry.get("SYS_STATUS") or {})
        battery = self._battery_snapshot(telemetry)
        highres_imu = telemetry.get("HIGHRES_IMU") or {}
        primary_imu = highres_imu or telemetry.get("SCALED_IMU") or telemetry.get("RAW_IMU") or {}

        return {
            "status": "ok" if connected else "disconnected",
            "connected": connected,
            "backend": "px4_mavlink",
            "updated_at": time.time(),
            "connection": connection,
            "telemetry": {
                "status": status,
                "attitude": telemetry.get("ATTITUDE") or {},
                "attitude_target": telemetry.get("ATTITUDE_TARGET") or {},
                "position": telemetry.get("LOCAL_POSITION_NED") or {},
                "global_position": telemetry.get("GLOBAL_POSITION_INT") or {},
                "gps_raw": telemetry.get("GPS_RAW_INT") or {},
                "imu": primary_imu,
                "imu_sources": {
                    key: telemetry.get(key) or {}
                    for key in ("HIGHRES_IMU", "RAW_IMU", "SCALED_IMU", "SCALED_IMU2", "SCALED_IMU3")
                    if telemetry.get(key)
                },
                "vibration": telemetry.get("VIBRATION") or {},
                "rc_channels": telemetry.get("RC_CHANNELS") or {},
                "servo_output": telemetry.get("SERVO_OUTPUT_RAW") or {},
                "battery": battery,
                "power_status": telemetry.get("POWER_STATUS") or {},
                "sensor_health": sensor_health,
            },
            "history": self._history_snapshot(histories) if include_history else {},
        }

    def get_vehicle_setup_snapshot(self, include_history: bool = True, history_limit: int = 240) -> dict[str, Any]:
        """Return a QGC-style read-only vehicle setup snapshot for the UI."""
        if self.is_connected:
            self.update_telemetry(timeout=0.04)

        connected = self.is_connected
        status = self.get_cached_status().to_dict() if connected else DroneStatus(mode="DISCONNECTED").to_dict()
        connection = self.get_connection_info()
        parameters = self._parameter_snapshot(include_parameters=False)
        with self._lock:
            telemetry = {
                key: self._copy_payload(value)
                for key, value in self._telemetry.items()
                if not str(key).startswith("STALE_")
            }
            firmware = dict(self._firmware_info)
            param_entries = [dict(item) for item in self._parameters.values()]
            histories = {
                key: list(history)[-max(1, min(int(history_limit or 240), 2400)):]
                for key, history in self._telemetry_history.items()
            } if include_history else {}

        param_map = {
            str(entry.get("name") or ""): entry
            for entry in param_entries
            if entry.get("name")
        }
        sensor_health = self._sensor_health_snapshot(telemetry.get("SYS_STATUS") or {})
        battery = self._battery_snapshot(telemetry)
        rc_channels = telemetry.get("RC_CHANNELS") or {}
        servo_output = telemetry.get("SERVO_OUTPUT_RAW") or {}
        gps_raw = telemetry.get("GPS_RAW_INT") or {}
        attitude = telemetry.get("ATTITUDE") or {}
        highres_imu = telemetry.get("HIGHRES_IMU") or {}
        primary_imu = highres_imu or telemetry.get("SCALED_IMU") or telemetry.get("RAW_IMU") or {}

        return {
            "status": "ok" if connected else "disconnected",
            "connected": connected,
            "backend": "px4_mavlink",
            "updated_at": time.time(),
            "connection": connection,
            "firmware": {"status": "ok", **firmware} if firmware else {"status": "empty"},
            "parameters": parameters,
            "parameter_groups": self._parameter_group_counts(param_entries),
            "parameter_highlights": self._parameter_highlights(param_map),
            "summary": {
                "airframe": self._airframe_summary(param_map, firmware),
                "sensors": self._sensors_summary(sensor_health, telemetry, param_map),
                "radio": self._radio_summary(sensor_health, rc_channels),
                "flight_modes": self._flight_modes_summary(param_map, status),
                "power": self._power_summary(sensor_health, battery),
                "safety": self._safety_summary(param_map, status),
                "actuators": self._actuator_summary(sensor_health, servo_output),
            },
            "telemetry": {
                "status": status,
                "attitude": attitude,
                "attitude_target": telemetry.get("ATTITUDE_TARGET") or {},
                "position": telemetry.get("LOCAL_POSITION_NED") or {},
                "global_position": telemetry.get("GLOBAL_POSITION_INT") or {},
                "gps_raw": gps_raw,
                "imu": primary_imu,
                "imu_sources": {
                    key: telemetry.get(key) or {}
                    for key in ("HIGHRES_IMU", "RAW_IMU", "SCALED_IMU", "SCALED_IMU2", "SCALED_IMU3")
                    if telemetry.get(key)
                },
                "vibration": telemetry.get("VIBRATION") or {},
                "rc_channels": rc_channels,
                "servo_output": servo_output,
                "battery": battery,
                "power_status": telemetry.get("POWER_STATUS") or {},
                "sensor_health": sensor_health,
            },
            "history": self._history_snapshot(histories) if include_history else {},
            "read_only": {
                "calibration": True,
                "parameter_write": False,
                "firmware_flash": True,
                "message": "当前设置页已开放单参数写入；校准、电机测试和固件烧录暂未开放。",
            },
        }

    @staticmethod
    def _copy_payload(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: MavlinkController._copy_payload(item) for key, item in value.items()}
        if isinstance(value, list):
            return [MavlinkController._copy_payload(item) for item in value]
        if isinstance(value, tuple):
            return [MavlinkController._copy_payload(item) for item in value]
        return value

    @staticmethod
    def _parameter_group_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
        groups: dict[str, int] = {}
        for entry in entries:
            name = str(entry.get("name") or "")
            prefix = name.split("_", 1)[0] if "_" in name else (name[:3] or "OTHER")
            prefix = prefix.upper()
            groups[prefix] = groups.get(prefix, 0) + 1
        return dict(sorted(groups.items(), key=lambda item: (-item[1], item[0]))[:32])

    @staticmethod
    def _param_text(param_map: dict[str, dict[str, Any]], *names: str, default: str = "--") -> str:
        for name in names:
            entry = param_map.get(name)
            if entry is not None:
                return str(entry.get("value_text") if entry.get("value_text") is not None else entry.get("value", default))
        return default

    @staticmethod
    def _parameter_highlights(param_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
        names = [
            "MC_AIRMODE", "THR_MDL_FAC",
            "MC_ROLLRATE_K", "MC_PITCHRATE_K", "MC_YAWRATE_K",
            "MC_ROLLRATE_P", "MC_ROLLRATE_I", "MC_ROLLRATE_D",
            "MC_PITCHRATE_P", "MC_PITCHRATE_I", "MC_PITCHRATE_D",
            "MC_YAWRATE_P", "MC_YAWRATE_I",
            "MPC_XY_VEL_MAX", "MPC_Z_VEL_MAX_UP", "MPC_Z_VEL_MAX_DN", "MPC_TKO_SPEED",
            "NAV_ACC_RAD", "COM_RC_LOSS_T", "COM_LOW_BAT_ACT", "NAV_RCL_ACT",
            "COM_FLTMODE1", "COM_FLTMODE2", "COM_FLTMODE3", "COM_FLTMODE4", "COM_FLTMODE5", "COM_FLTMODE6",
            "SYS_AUTOSTART", "SYS_AUTOCONFIG", "MAV_SYS_ID",
            "SENS_BOARD_ROT", "CAL_MAG0_ID", "CAL_GYRO0_ID", "CAL_ACC0_ID",
        ]
        payload: dict[str, Any] = {}
        for name in names:
            entry = param_map.get(name)
            if entry is not None:
                payload[name] = entry.get("value_text") if entry.get("value_text") is not None else entry.get("value")
        return payload

    def _airframe_summary(self, param_map: dict[str, dict[str, Any]], firmware: dict[str, Any]) -> dict[str, Any]:
        return {
            "setup": "ok" if self.is_connected else "missing",
            "system_id": self._param_text(param_map, "MAV_SYS_ID", default=str((self._active_connection_details or {}).get("system_id") or "--")),
            "vehicle_type": firmware.get("vehicle_type") or self._enum_label("MAV_TYPE", self._vehicle_type),
            "autopilot": firmware.get("autopilot") or self._enum_label("MAV_AUTOPILOT", self._autopilot),
            "autostart": self._param_text(param_map, "SYS_AUTOSTART"),
            "airframe_id": self._param_text(param_map, "SYS_AUTOCONFIG"),
            "firmware_version": ((firmware.get("flight_version") or {}).get("text") or ""),
        }

    def _sensors_summary(
        self,
        sensor_health: dict[str, Any],
        telemetry: dict[str, Any],
        param_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        items = sensor_health.get("items") or {}
        required = ("gyro", "accel", "mag", "baro")
        ok_count = sum(1 for key in required if (items.get(key) or {}).get("healthy"))
        return {
            "setup": "ok" if ok_count >= 3 else "warning",
            "gyro": self._sensor_state_text(items.get("gyro")),
            "accel": self._sensor_state_text(items.get("accel")),
            "mag": self._sensor_state_text(items.get("mag")),
            "baro": self._sensor_state_text(items.get("baro")),
            "gps": self._sensor_state_text(items.get("gps")),
            "board_rotation": self._param_text(param_map, "SENS_BOARD_ROT"),
            "mag0_id": self._param_text(param_map, "CAL_MAG0_ID"),
            "gyro0_id": self._param_text(param_map, "CAL_GYRO0_ID"),
            "acc0_id": self._param_text(param_map, "CAL_ACC0_ID"),
            "latest_imu_source": "HIGHRES_IMU" if telemetry.get("HIGHRES_IMU") else ("SCALED_IMU" if telemetry.get("SCALED_IMU") else ("RAW_IMU" if telemetry.get("RAW_IMU") else "")),
        }

    @staticmethod
    def _radio_summary(sensor_health: dict[str, Any], rc_channels: dict[str, Any]) -> dict[str, Any]:
        valid_channels = rc_channels.get("valid_channels") or []
        rc = (sensor_health.get("items") or {}).get("rc")
        return {
            "setup": "ok" if valid_channels else ("warning" if rc and rc.get("present") else "missing"),
            "channels": len(valid_channels),
            "rssi": rc_channels.get("rssi"),
            "last": rc_channels.get("time_boot_ms"),
            "sensor_state": MavlinkController._sensor_state_text(rc),
        }

    def _flight_modes_summary(self, param_map: dict[str, dict[str, Any]], status: dict[str, Any]) -> dict[str, Any]:
        modes = {
            f"flight_mode_{index}": self._param_text(param_map, f"COM_FLTMODE{index}")
            for index in range(1, 7)
        }
        return {
            "setup": "ok" if any(value != "--" for value in modes.values()) else "warning",
            "current_mode": status.get("mode") or "--",
            **modes,
        }

    @staticmethod
    def _power_summary(sensor_health: dict[str, Any], battery: dict[str, Any]) -> dict[str, Any]:
        battery_sensor = (sensor_health.get("items") or {}).get("battery")
        voltage = battery.get("voltage") or battery.get("voltage_battery")
        return {
            "setup": "ok" if voltage else ("warning" if battery_sensor and battery_sensor.get("present") else "missing"),
            "voltage": voltage,
            "current": battery.get("current_battery"),
            "remaining": battery.get("battery_remaining"),
            "cells": len(battery.get("cell_voltages") or []),
            "sensor_state": MavlinkController._sensor_state_text(battery_sensor),
        }

    def _safety_summary(self, param_map: dict[str, dict[str, Any]], status: dict[str, Any]) -> dict[str, Any]:
        return {
            "setup": "warning" if not status.get("armed") else "ok",
            "armed": bool(status.get("armed")),
            "flying": bool(status.get("flying")),
            "low_battery_action": self._param_text(param_map, "COM_LOW_BAT_ACT"),
            "rc_loss_action": self._param_text(param_map, "NAV_RCL_ACT", "COM_RC_LOSS_T"),
            "data_link_loss": self._param_text(param_map, "NAV_DLL_ACT", "COM_DL_LOSS_T"),
            "return_altitude": self._param_text(param_map, "RTL_RETURN_ALT", "RTL_DESCEND_ALT"),
            "mode": status.get("mode") or "--",
        }

    @staticmethod
    def _actuator_summary(sensor_health: dict[str, Any], servo_output: dict[str, Any]) -> dict[str, Any]:
        outputs = servo_output.get("valid_outputs") or []
        motor = (sensor_health.get("items") or {}).get("motor")
        return {
            "setup": "ok" if outputs else ("warning" if motor and motor.get("present") else "missing"),
            "outputs": len(outputs),
            "active_outputs": sum(1 for value in outputs if isinstance(value, int) and value > 900),
            "sensor_state": MavlinkController._sensor_state_text(motor),
        }

    @staticmethod
    def _sensor_state_text(sensor: dict[str, Any] | None) -> str:
        if not sensor or not sensor.get("present"):
            return "missing"
        if sensor.get("healthy"):
            return "ready"
        if sensor.get("enabled"):
            return "needs_attention"
        return "disabled"

    @staticmethod
    def _battery_snapshot(telemetry: dict[str, Any]) -> dict[str, Any]:
        battery = dict(telemetry.get("BATTERY_STATUS") or {})
        sys_status = telemetry.get("SYS_STATUS") or {}
        if not battery:
            battery = {}
        if not battery.get("voltage") and sys_status.get("voltage_battery") is not None:
            battery["voltage_battery"] = sys_status.get("voltage_battery")
            battery["voltage"] = sys_status.get("voltage_battery")
        if battery.get("current_battery") is None and sys_status.get("current_battery") is not None:
            battery["current_battery"] = sys_status.get("current_battery")
        if battery.get("battery_remaining") is None and sys_status.get("battery_remaining") is not None:
            battery["battery_remaining"] = sys_status.get("battery_remaining")
        return battery

    @staticmethod
    def _history_snapshot(histories: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, entries in histories.items():
            if not entries:
                result[key] = []
                continue
            first_t = float(entries[0].get("t", 0.0) or 0.0)
            cleaned: list[dict[str, Any]] = []
            for entry in entries:
                item = dict(entry)
                timestamp = float(item.get("t", 0.0) or 0.0)
                item["sec"] = round(timestamp - first_t, 3)
                cleaned.append(item)
            result[key] = cleaned
        return result

    @staticmethod
    def _sensor_health_snapshot(sys_status: dict[str, Any]) -> dict[str, Any]:
        present = int(sys_status.get("onboard_control_sensors_present", 0) or 0)
        enabled = int(sys_status.get("onboard_control_sensors_enabled", 0) or 0)
        health = int(sys_status.get("onboard_control_sensors_health", 0) or 0)
        sensor_bits = {
            "gyro": ("Gyro", "MAV_SYS_STATUS_SENSOR_3D_GYRO"),
            "accel": ("Accelerometer", "MAV_SYS_STATUS_SENSOR_3D_ACCEL"),
            "mag": ("Magnetometer", "MAV_SYS_STATUS_SENSOR_3D_MAG"),
            "baro": ("Barometer", "MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE"),
            "gps": ("GPS", "MAV_SYS_STATUS_SENSOR_GPS"),
            "rc": ("RC Receiver", "MAV_SYS_STATUS_SENSOR_RC_RECEIVER"),
            "battery": ("Battery", "MAV_SYS_STATUS_SENSOR_BATTERY"),
            "motor": ("Motor Outputs", "MAV_SYS_STATUS_SENSOR_MOTOR_OUTPUTS"),
        }
        items: dict[str, dict[str, Any]] = {}
        for key, (label, attr) in sensor_bits.items():
            bit = int(getattr(mavutil.mavlink, attr, 0) or 0)
            items[key] = {
                "label": label,
                "bit": bit,
                "present": bool(bit and present & bit),
                "enabled": bool(bit and enabled & bit),
                "healthy": bool(bit and health & bit),
            }
        ready = sum(1 for item in items.values() if item["healthy"])
        return {
            "present_mask": present,
            "enabled_mask": enabled,
            "health_mask": health,
            "ready_count": ready,
            "total_count": len(items),
            "items": items,
        }

    def _reset_parameter_cache(self, state: str = "not_requested") -> None:
        with self._lock:
            self._parameters = {}
            self._parameter_component_counts = {}
            self._parameter_component_indices = {}
            self._parameter_download_state = state
            self._parameter_download_started_at = None
            self._parameter_download_finished_at = None
            self._parameter_last_message_at = None
            self._parameter_last_error = ""

    def _record_parameter_value(self, msg: Any, msg_dict: dict[str, Any]) -> dict[str, Any]:
        data = dict(msg_dict)
        parameter_name = self._normalise_param_id(data.get("param_id"))
        if not parameter_name:
            return data

        component_id = _optional_int(data.get("_src_component"))
        system_id = _optional_int(data.get("_src_system"))
        try:
            component_id = msg.get_srcComponent()
            system_id = msg.get_srcSystem()
        except Exception:
            pass
        component_id = int(component_id if component_id is not None else 0)
        system_id = int(system_id if system_id is not None else 0)
        param_type = _optional_int(data.get("param_type"))
        param_index = _optional_int(data.get("param_index"))
        param_count = _optional_int(data.get("param_count"))
        decoded = self._decode_param_value(data.get("param_value"), param_type)
        received_at = time.time()

        entry = {
            "name": parameter_name,
            "key": f"{component_id}:{parameter_name}",
            "value": decoded["value"],
            "value_text": decoded["text"],
            "raw_float": decoded["raw_float"],
            "encoding": decoded["encoding"],
            "type": param_type,
            "type_name": decoded["type_name"],
            "component_id": component_id,
            "system_id": system_id,
            "index": param_index,
            "count": param_count,
            "received_at": received_at,
        }

        with self._lock:
            self._parameters[entry["key"]] = entry
            if param_count is not None and param_count > 0:
                self._parameter_component_counts[component_id] = max(
                    param_count,
                    self._parameter_component_counts.get(component_id, 0),
                )
            if param_index is not None and param_index >= 0:
                self._parameter_component_indices.setdefault(component_id, set()).add(param_index)
            if self._parameter_download_state in {"not_requested", "idle", "error"}:
                self._parameter_download_state = "receiving"
            self._parameter_last_message_at = received_at

        data["param_id"] = parameter_name
        data["decoded_value"] = entry["value"]
        data["value_text"] = entry["value_text"]
        data["param_type_name"] = entry["type_name"]
        data["_src_system"] = system_id
        data["_src_component"] = component_id
        return data

    def _parameter_snapshot(
        self,
        query: str = "",
        limit: int = 200,
        offset: int = 0,
        include_parameters: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            status = self._parameter_status_locked_snapshot()
            entries = [dict(item) for item in self._parameters.values()]
        entries.sort(key=lambda item: (int(item.get("component_id") or 0), str(item.get("name") or "")))
        clean_query = str(query or "").strip().lower()
        if clean_query:
            entries = [
                item for item in entries
                if clean_query in str(item.get("name") or "").lower()
                or clean_query in str(item.get("value_text") or "").lower()
                or clean_query in str(item.get("type_name") or "").lower()
            ]
        try:
            clean_offset = max(0, int(offset))
        except (TypeError, ValueError):
            clean_offset = 0
        try:
            clean_limit = int(limit)
        except (TypeError, ValueError):
            clean_limit = 200
        clean_limit = max(0, min(clean_limit, 1000))

        payload = {
            **status,
            "query": query,
            "total": len(entries),
            "offset": clean_offset,
            "limit": clean_limit,
        }
        if include_parameters:
            payload["parameters"] = entries[clean_offset: clean_offset + clean_limit] if clean_limit else []
        return payload

    def _parameter_status_locked_snapshot(self) -> dict[str, Any]:
        expected_count = sum(count for count in self._parameter_component_counts.values() if count > 0)
        received_count = len(self._parameters)
        component_received: dict[int, int] = {}
        for entry in self._parameters.values():
            component_id = int(entry.get("component_id") or 0)
            component_received[component_id] = component_received.get(component_id, 0) + 1
        missing_count = max(0, expected_count - received_count) if expected_count else None
        progress = round(min(1.0, received_count / expected_count), 4) if expected_count else None
        state = self._parameter_download_state
        if not self.is_connected:
            state = "disconnected"
        elif received_count and expected_count and received_count >= expected_count and state != "downloading":
            state = "ready"
        return {
            "status": state,
            "connected": self.is_connected,
            "ready": state == "ready",
            "received_count": received_count,
            "expected_count": expected_count,
            "missing_count": missing_count,
            "progress": progress,
            "component_counts": {str(key): value for key, value in sorted(self._parameter_component_counts.items())},
            "component_received": {str(key): value for key, value in sorted(component_received.items())},
            "started_at": self._parameter_download_started_at,
            "finished_at": self._parameter_download_finished_at,
            "last_message_at": self._parameter_last_message_at,
            "message": self._parameter_last_error,
        }

    @staticmethod
    def _normalise_param_id(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            text = value.decode("latin1", errors="ignore")
        elif isinstance(value, bytearray):
            text = bytes(value).decode("latin1", errors="ignore")
        elif isinstance(value, (list, tuple)):
            try:
                text = bytes(int(item or 0) & 0xFF for item in value).decode("latin1", errors="ignore")
            except Exception:
                text = "".join(str(item or "") for item in value)
        else:
            text = str(value)
        return text.split("\x00", 1)[0].strip()

    @staticmethod
    def _decode_param_value(raw_value: Any, param_type: Any) -> dict[str, Any]:
        try:
            raw_float = float(raw_value or 0.0)
        except (TypeError, ValueError):
            raw_float = 0.0
        type_id = _optional_int(param_type) or 0
        type_name = MavlinkController._enum_label("MAV_PARAM_TYPE", type_id) or str(type_id or "")
        integer_formats = {
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_UINT8", 1): ("<B", 1),
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_INT8", 2): ("<b", 1),
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_UINT16", 3): ("<H", 2),
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_INT16", 4): ("<h", 2),
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_UINT32", 5): ("<I", 4),
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_INT32", 6): ("<i", 4),
        }
        value: Any = raw_float
        encoding = "float"
        if type_id in integer_formats:
            fmt, size = integer_formats[type_id]
            packed = struct.pack("<f", raw_float)
            bytewise = struct.unpack(fmt, packed[:size])[0]
            if math.isfinite(raw_float) and abs(raw_float) > 1e-20 and abs(raw_float - round(raw_float)) < 1e-6:
                value = int(round(raw_float))
                encoding = "cast"
            else:
                value = bytewise
                encoding = "bytewise"
        elif type_id in {
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_UINT64", 7),
            getattr(mavutil.mavlink, "MAV_PARAM_TYPE_INT64", 8),
        } and math.isfinite(raw_float):
            value = int(round(raw_float))
            encoding = "cast"

        if isinstance(value, float):
            if math.isfinite(value):
                text = f"{value:.8g}"
            else:
                text = str(value)
        else:
            text = str(value)
        return {
            "value": value,
            "text": text,
            "raw_float": raw_float,
            "type_name": type_name,
            "encoding": encoding,
        }

    def _decode_autopilot_version(self, msg_dict: dict[str, Any]) -> dict[str, Any]:
        flight_sw_version = int(msg_dict.get("flight_sw_version", 0) or 0)
        middleware_sw_version = int(msg_dict.get("middleware_sw_version", 0) or 0)
        os_sw_version = int(msg_dict.get("os_sw_version", 0) or 0)
        board_version = int(msg_dict.get("board_version", 0) or 0)
        capabilities = int(msg_dict.get("capabilities", 0) or 0)
        flight_custom = self._bytes_from_mavlink_array(msg_dict.get("flight_custom_version"))
        middleware_custom = self._bytes_from_mavlink_array(msg_dict.get("middleware_custom_version"))
        os_custom = self._bytes_from_mavlink_array(msg_dict.get("os_custom_version"))
        decoded = {
            "raw": dict(msg_dict),
            "autopilot": self._enum_label("MAV_AUTOPILOT", self._autopilot),
            "vehicle_type": self._enum_label("MAV_TYPE", self._vehicle_type),
            "flight_sw_version": flight_sw_version,
            "flight_version": self._decode_firmware_version(flight_sw_version),
            "middleware_sw_version": middleware_sw_version,
            "middleware_version": self._decode_firmware_version(middleware_sw_version),
            "os_sw_version": os_sw_version,
            "os_version": self._decode_firmware_version(os_sw_version),
            "board_version": board_version,
            "vendor_id": msg_dict.get("vendor_id"),
            "product_id": msg_dict.get("product_id"),
            "uid": str(msg_dict.get("uid", "")),
            "capabilities": capabilities,
            "capability_flags": self._decode_capabilities(capabilities),
            "flight_custom_version_hex": flight_custom.hex(),
            "middleware_custom_version_hex": middleware_custom.hex(),
            "os_custom_version_hex": os_custom.hex(),
            "received_at": time.time(),
        }
        if flight_custom:
            decoded["px4_custom_version"] = {
                "major": flight_custom[2] if len(flight_custom) > 2 else 0,
                "minor": flight_custom[1] if len(flight_custom) > 1 else 0,
                "patch": flight_custom[0] if len(flight_custom) > 0 else 0,
                "text": ".".join(str(value) for value in [
                    flight_custom[2] if len(flight_custom) > 2 else 0,
                    flight_custom[1] if len(flight_custom) > 1 else 0,
                    flight_custom[0] if len(flight_custom) > 0 else 0,
                ]),
            }
            # QGC decodes PX4 git hash by reversing the 8 binary bytes.
            decoded["git_hash"] = "".join(f"{byte:02x}" for byte in reversed(flight_custom[:8]))
        return decoded

    @staticmethod
    def _decode_firmware_version(raw: int) -> dict[str, Any]:
        if not raw:
            return {"raw": 0, "text": ""}
        major = (raw >> 24) & 0xFF
        minor = (raw >> 16) & 0xFF
        patch = (raw >> 8) & 0xFF
        version_type = raw & 0xFF
        return {
            "raw": raw,
            "major": major,
            "minor": minor,
            "patch": patch,
            "type": version_type,
            "type_name": MavlinkController._enum_label("FIRMWARE_VERSION_TYPE", version_type),
            "text": f"{major}.{minor}.{patch}",
        }

    @staticmethod
    def _bytes_from_mavlink_array(value: Any) -> bytes:
        if value is None:
            return b""
        if isinstance(value, bytes):
            return value[:8]
        if isinstance(value, bytearray):
            return bytes(value[:8])
        if isinstance(value, str):
            return value.encode("latin1", errors="ignore")[:8]
        try:
            return bytes(int(item or 0) & 0xFF for item in list(value)[:8])
        except Exception:
            return b""

    @staticmethod
    def _enum_label(enum_name: str, value: Any) -> str:
        try:
            enum_value = int(value)
        except (TypeError, ValueError):
            return ""
        try:
            entry = mavutil.mavlink.enums.get(enum_name, {}).get(enum_value)
            return str(getattr(entry, "name", "") or "")
        except Exception:
            return ""

    @staticmethod
    def _decode_capabilities(value: int) -> list[str]:
        names: list[str] = []
        for attr in dir(mavutil.mavlink):
            if not attr.startswith("MAV_PROTOCOL_CAPABILITY_"):
                continue
            try:
                bit = int(getattr(mavutil.mavlink, attr))
            except (TypeError, ValueError):
                continue
            if bit and value & bit:
                names.append(attr.removeprefix("MAV_PROTOCOL_CAPABILITY_"))
        return sorted(names)

    def list_vehicles(self) -> list[str]:
        if not self.is_connected:
            return []
        # 只列有真实心跳的 system（排除 __init__ 的 sysid=0 空表）
        names = [
            f"px4_sys{sysid}"
            for sysid in sorted(self._systems)
            if self._systems[sysid]["last_heartbeat"]
        ]
        return names or ["px4_drone"]

    def set_mode(self, mode: str, vehicle_name: str = "") -> bool:
        """多机：vehicle_name 解析后逐机执行。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            self._last_action_error = "no MAVLink system available"
            return False
        ok = True
        for sysid in targets:
            self._set_target(sysid)
            if not self._set_mode_one(mode):
                ok = False
                break
        return ok

    def _set_mode_one(self, mode: str) -> bool:
        if not self.is_connected:
            return False
        requested = (mode or "").strip().upper().replace("-", "_")
        canonical = self._canonical_px4_mode(requested)
        leaving_offboard = self._current_mode() == "OFFBOARD" and canonical != "OFFBOARD"
        if leaving_offboard and not self._offboard_hold_active():
            self._start_offboard_position_hold(dict(self._position))

        try:
            if self._is_px4():
                if canonical not in mavutil.px4_map:
                    logger.error(f"Unknown PX4 mode: {mode}")
                    return False
                self._mavlink.set_mode(canonical)
            else:
                self._mavlink.set_mode(self._canonical_apm_mode(requested))
        except Exception as exc:
            logger.error(f"set_mode({mode}) failed: {exc}")
            return False

        ack_ok = self._wait_command_ack(mavutil.mavlink.MAV_CMD_DO_SET_MODE, timeout=1.5)
        mode_ok = self._wait_mode(canonical, timeout=3.0) if self._is_px4() else True
        # PX4 can ACK a mode command before the corresponding flight task is
        # actually running. Require the heartbeat mode transition so a rejected
        # LOITER/POSCTL switch never stops the OFFBOARD safety stream.
        succeeded = mode_ok if self._is_px4() else (ack_ok or mode_ok)
        if succeeded and canonical != "OFFBOARD":
            self._stop_offboard_hold()
        return succeeded

    def rotate_to_heading(self, heading_deg: float, timeout: float = 30.0, vehicle_name: str = "") -> bool:
        if not self.is_connected:
            return False
        target = heading_deg % 360.0
        start = time.time()
        if not self._prime_offboard_velocity({"vx": 0.0, "vy": 0.0, "vz": 0.0}):
            self._stop_offboard_hold()
            return False
        if not self._set_mode_one("OFFBOARD"):
            if self._current_mode() != "OFFBOARD":
                self._stop_offboard_hold()
            return False
        self._send_yaw_rate_setpoint(0.0)
        self._stop_offboard_hold()
        while time.time() - start < timeout:
            if self._stop_requested():
                self._finish_offboard_position_hold(dict(self._position))
                return False
            self.update_telemetry(timeout=0.05)
            gps = self._telemetry.get("GLOBAL_POSITION_INT", {})
            current = float(gps.get("hdg", 0.0) or 0.0)
            error = target - current
            if error > 180:
                error -= 360
            elif error < -180:
                error += 360
            if abs(error) < 8.0:
                return self._finish_offboard_position_hold(dict(self._position))
            yaw_rate = math.radians(max(-45.0, min(45.0, error * 0.4)))
            self._send_yaw_rate_setpoint(yaw_rate)
            time.sleep(1.0 / self._setpoint_hz)
        self._finish_offboard_position_hold(dict(self._position))
        return False

    def fly_to_gps(self, lat: float, lon: float, alt: float) -> str:
        if not self.is_connected:
            return "Failed: Not connected"
        self.update_telemetry(timeout=0.5)
        gps = self._telemetry.get("GLOBAL_POSITION_INT", {})
        if not gps:
            return "Failed: No GPS position available"
        current_lat = float(gps.get("lat", 0.0) or 0.0)
        current_lon = float(gps.get("lon", 0.0) or 0.0)
        current_alt = float(gps.get("relative_alt", 0.0) or 0.0)
        north, east = _gps_offset_m(current_lat, current_lon, float(lat), float(lon))
        target = {
            "x": self._position["x"] + north,
            "y": self._position["y"] + east,
            "z": -abs(float(alt if alt is not None else current_alt)),
        }
        ok = self.move_to_position(target["x"], target["y"], target["z"], self._max_velocity)
        return f"Success: Reached GPS target" if ok else "Failed: GPS target timeout"

    def upload_mission(self, waypoints: list[dict[str, Any]], vehicle_name: str = "") -> dict[str, Any]:
        """多机：vehicle_name 解析后逐机执行（返回最后目标的结果）。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            return {"status": "error", "message": "no MAVLink system available"}
        last: dict = {}
        for sysid in targets:
            self._set_target(sysid)
            last = self._upload_mission_one(waypoints)
            if not (last or {}).get("status") == "ok":
                break
        return last

    def _upload_mission_one(self, waypoints: list[dict[str, Any]], vehicle_name: str = "") -> dict[str, Any]:
        """Upload a MAVLink waypoint mission to the active PX4 vehicle."""

        if not self.is_connected:
            return {"status": "error", "message": "not connected"}
        mission_items = self._normalize_mission_items(waypoints)
        if not mission_items:
            return {"status": "error", "message": "mission has no valid waypoints"}

        clear_result = self.clear_mission(wait_ack=True)
        if clear_result.get("status") != "ok":
            return {
                "status": "error",
                "message": "mission clear before upload failed",
                "clear_result": clear_result,
            }
        # Drop any trailing mission transfer messages before starting a fresh
        # upload. The clear call above waits for its ACK, so this is only a
        # stale-message guard, not an async race.
        self._drain_mission_transfer_messages()
        self._mavlink.mav.mission_count_send(
            self._mavlink.target_system,
            self._mavlink.target_component,
            len(mission_items),
        )

        sent: set[int] = set()
        last_requested_seq: int | None = None
        last_request_type: str | None = None
        deadline = time.time() + max(15.0, len(mission_items) * 4.0)
        while time.time() < deadline and len(sent) < len(mission_items):
            msg = self._mavlink.recv_match(
                type=["MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"],
                blocking=True,
                timeout=1.0,
            )
            if msg is None:
                continue
            msg_type = msg.get_type()
            if msg_type == "MISSION_ACK":
                ack = self._message_dict_with_source(msg)
                self._telemetry["MISSION_ACK"] = ack
                ack_type = int(ack.get("type", mavutil.mavlink.MAV_MISSION_ERROR))
                if not sent and not self._mission_ack_targets_this_gcs(ack):
                    self._telemetry["STALE_MISSION_ACK"] = ack
                    continue
                if ack_type == mavutil.mavlink.MAV_MISSION_ACCEPTED and len(sent) == len(mission_items):
                    self._telemetry["UPLOADED_MISSION"] = {"items": mission_items}
                    return {"status": "ok", "message": "mission uploaded", "count": len(mission_items), "items": mission_items}
                if ack_type != mavutil.mavlink.MAV_MISSION_ACCEPTED:
                    return {
                        "status": "error",
                        "message": f"mission upload rejected: {ack_type}",
                        "ack": ack,
                        "sent_count": len(sent),
                        "count": len(mission_items),
                        "last_requested_seq": last_requested_seq,
                        "last_request_type": last_request_type,
                        "last_requested_item": (
                            mission_items[last_requested_seq]
                            if last_requested_seq is not None and 0 <= last_requested_seq < len(mission_items)
                            else None
                        ),
                    }
                continue
            seq = int(getattr(msg, "seq", -1))
            if seq < 0 or seq >= len(mission_items):
                return {"status": "error", "message": f"mission request out of range: {seq}"}
            last_requested_seq = seq
            last_request_type = msg_type
            if msg_type == "MISSION_REQUEST_INT":
                self._send_mission_item_int(seq, mission_items[seq])
            else:
                self._send_mission_item(seq, mission_items[seq])
            sent.add(seq)

        ack = self._wait_mission_ack(timeout=5.0)
        if ack.get("accepted"):
            self._telemetry["UPLOADED_MISSION"] = {"items": mission_items}
            return {"status": "ok", "message": "mission uploaded", "count": len(mission_items), "items": mission_items, "ack": ack}
        return {
            "status": "error",
            "message": "mission upload timed out",
            "sent_count": len(sent),
            "count": len(mission_items),
            "ack": ack,
            "last_requested_seq": last_requested_seq,
            "last_request_type": last_request_type,
        }

    def download_mission(self, vehicle_name: str = "") -> dict[str, Any]:
        """多机：vehicle_name 解析后逐机执行（返回最后目标的结果）。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            return {"status": "error", "message": "no MAVLink system available"}
        last: dict = {}
        for sysid in targets:
            self._set_target(sysid)
            last = self._download_mission_one()
            if not (last or {}).get("status") == "ok":
                break
        return last

    def _download_mission_one(self, vehicle_name: str = "") -> dict[str, Any]:
        """Download the current vehicle mission as backend-neutral items."""

        if not self.is_connected:
            return {"status": "error", "message": "not connected"}
        self._mavlink.mav.mission_request_list_send(self._mavlink.target_system, self._mavlink.target_component)
        count_msg = self._wait_message("MISSION_COUNT", timeout=5.0)
        if count_msg is None:
            return {"status": "error", "message": "mission count timeout"}
        count = int(count_msg.to_dict().get("count", 0) or 0)
        items: list[dict[str, Any]] = []
        for seq in range(count):
            self._mavlink.mav.mission_request_int_send(
                self._mavlink.target_system,
                self._mavlink.target_component,
                seq,
            )
            item_msg = self._wait_mission_item(seq, timeout=5.0)
            if item_msg is None:
                return {"status": "error", "message": f"mission item {seq} timeout", "items": items}
            item = self._mission_item_from_msg(item_msg)
            items.append(item)
        self._mavlink.mav.mission_ack_send(
            self._mavlink.target_system,
            self._mavlink.target_component,
            mavutil.mavlink.MAV_MISSION_ACCEPTED,
        )
        self._telemetry["DOWNLOADED_MISSION"] = {"items": items}
        return {
            "status": "ok",
            "message": "mission downloaded",
            "count": len(items),
            "items": items,
            "mission": {"items": items},
        }

    def clear_mission(self, wait_ack: bool = True, vehicle_name: str = "") -> dict[str, Any]:
        """多机：vehicle_name 解析后逐机执行（返回最后目标的结果）。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            return {"status": "error", "message": "no MAVLink system available"}
        last: dict = {}
        for sysid in targets:
            self._set_target(sysid)
            last = self._clear_mission_one(wait_ack)
            if not (last or {}).get("status") == "ok":
                break
        return last

    def _clear_mission_one(self, wait_ack: bool = True, vehicle_name: str = "") -> dict[str, Any]:
        """Clear all vehicle mission items."""

        if not self.is_connected:
            return {"status": "error", "message": "not connected"}
        self._mavlink.mav.mission_clear_all_send(self._mavlink.target_system, self._mavlink.target_component)
        if not wait_ack:
            self._telemetry.pop("UPLOADED_MISSION", None)
            return {"status": "ok", "message": "mission clear sent"}
        ack = self._wait_mission_ack(timeout=5.0)
        ok = bool(ack.get("accepted"))
        if ok:
            self._telemetry.pop("UPLOADED_MISSION", None)
        return {
            "status": "ok" if ok else "error",
            "message": "mission cleared" if ok else "mission clear not acknowledged",
            "ack": ack,
        }

    def start_mission(self, vehicle_name: str = "") -> dict[str, Any]:
        """多机：vehicle_name 解析后逐机执行（返回最后目标的结果）。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            return {"status": "error", "message": "no MAVLink system available"}
        last: dict = {}
        for sysid in targets:
            self._set_target(sysid)
            last = self._start_mission_one()
            if not (last or {}).get("status") == "ok":
                break
        return last

    def _start_mission_one(self, vehicle_name: str = "") -> dict[str, Any]:
        """Start the uploaded mission. The vehicle must satisfy PX4 mission preconditions."""

        if not self.is_connected:
            return {"status": "error", "message": "not connected"}
        self.update_telemetry(timeout=0.4)
        uploaded = self._telemetry.get("UPLOADED_MISSION", {})
        mission_count = len(uploaded.get("items") or [])
        armed_before = self._is_armed()
        arm_ok = True
        if not armed_before:
            arm_ok = self.arm()
            if not arm_ok:
                return {
                    "status": "error",
                    "message": "mission start rejected: vehicle is disarmed and arming failed",
                    "armed_before": False,
                    "arm_ok": False,
                    "mode_ok": False,
                    "command_ack": False,
                    "mission_item_count": mission_count,
                    "progress": self.get_mission_progress(),
                }
        mode_ok = self.set_mode("MISSION")
        self._mavlink.mav.command_long_send(
            self._mavlink.target_system,
            self._mavlink.target_component,
            mavutil.mavlink.MAV_CMD_MISSION_START,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        ack_ok = self._wait_command_ack(mavutil.mavlink.MAV_CMD_MISSION_START, timeout=3.0)
        progress = self.get_mission_progress()
        ok = bool(mode_ok or ack_ok or progress.get("running"))
        return {
            "status": "ok" if ok else "error",
            "message": "mission start requested" if ok else "mission start rejected",
            "armed_before": armed_before,
            "arm_ok": arm_ok,
            "armed": self._is_armed(),
            "mode_ok": mode_ok,
            "command_ack": ack_ok,
            "mission_item_count": mission_count,
            "progress": progress,
        }

    def get_mission_progress(self, vehicle_name: str = "") -> dict[str, Any]:
        """多机：vehicle_name 解析后逐机执行（返回最后目标的结果）。"""
        targets = self._resolve_sysids(vehicle_name)
        if not targets:
            return {"status": "error", "message": "no MAVLink system available"}
        last: dict = {}
        for sysid in targets:
            self._set_target(sysid)
            last = self._get_mission_progress_one()
            if not (last or {}).get("status") == "ok":
                break
        return last

    def _get_mission_progress_one(self, vehicle_name: str = "") -> dict[str, Any]:
        """Return current mission execution progress from MAVLink telemetry."""

        if not self.is_connected:
            return {"status": "error", "message": "not connected"}
        self.update_telemetry(timeout=0.4)
        current = self._telemetry.get("MISSION_CURRENT", {})
        reached = self._telemetry.get("MISSION_ITEM_REACHED", {})
        uploaded = self._telemetry.get("UPLOADED_MISSION", {})
        downloaded = self._telemetry.get("DOWNLOADED_MISSION", {})
        cached_total = len(uploaded.get("items") or downloaded.get("items") or [])
        current_total = _optional_int(current.get("total"))
        total = current_total if current_total and current_total > 0 else (cached_total or None)
        current_seq = _optional_int(current.get("seq"))
        reached_seq = _optional_int(reached.get("seq"))
        return {
            "status": "ok",
            "current_seq": current_seq,
            "reached_seq": reached_seq,
            "total": total,
            "running": self._current_mode() == "MISSION",
            "mode": self._current_mode(),
        }

    def _normalize_mission_items(self, waypoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.update_telemetry(timeout=0.5)
        gps = self._telemetry.get("GLOBAL_POSITION_INT", {})
        base_lat = float(gps.get("lat", 0.0) or 0.0)
        base_lon = float(gps.get("lon", 0.0) or 0.0)
        items: list[dict[str, Any]] = []

        for index, raw in enumerate(waypoints):
            if not isinstance(raw, dict):
                continue
            lat = _optional_float(raw.get("lat"))
            lon = _optional_float(raw.get("lon"))
            alt_m = _optional_float(raw.get("alt_m"))
            if alt_m is None:
                alt_m = _optional_float(raw.get("alt"))

            if lat is None or lon is None:
                x = _optional_float(raw.get("x"))
                y = _optional_float(raw.get("y"))
                z = _optional_float(raw.get("z"))
                if x is None or y is None or base_lat == 0.0 or base_lon == 0.0:
                    continue
                lat, lon = _gps_from_offset_m(base_lat, base_lon, north_m=x, east_m=y)
                if alt_m is None:
                    alt_m = abs(float(z if z is not None else -3.0))

            if alt_m is None:
                alt_m = 3.0

            item_type = str(raw.get("type") or "waypoint").strip().lower()
            command = _mission_command_for_item(item_type, raw)
            min_alt = 0.0 if command == mavutil.mavlink.MAV_CMD_NAV_LAND else 0.5

            item = {
                "id": str(raw.get("id") or f"wp_{index:03d}"),
                "type": _mission_type_for_command(command),
                "frame": "global_relative_alt",
                "lat": float(lat),
                "lon": float(lon),
                "alt_m": max(min_alt, abs(float(alt_m))),
                "speed_mps": max(0.0, float(raw.get("speed_mps", raw.get("velocity", 0.0)) or 0.0)),
                "hold_s": max(0.0, float(raw.get("hold_s", 0.0) or 0.0)),
                "acceptance_radius_m": max(0.1, float(raw.get("acceptance_radius_m", 2.0) or 2.0)),
                "actions": list(raw.get("actions") or []),
                "metadata": {**dict(raw.get("metadata") or {}), "mav_command": command},
            }
            items.append(item)
        return items

    def _send_mission_item_int(self, seq: int, item: dict[str, Any]) -> None:
        command = int(dict(item.get("metadata") or {}).get("mav_command") or mavutil.mavlink.MAV_CMD_NAV_WAYPOINT)
        param1, param2, param3, param4 = _mission_params_for_command(command, item)
        self._mavlink.mav.mission_item_int_send(
            self._mavlink.target_system,
            self._mavlink.target_component,
            seq,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
            command,
            1 if seq == 0 else 0,
            1,
            param1,
            param2,
            param3,
            param4,
            int(round(float(item["lat"]) * 1e7)),
            int(round(float(item["lon"]) * 1e7)),
            float(item["alt_m"]),
        )

    def _send_mission_item(self, seq: int, item: dict[str, Any]) -> None:
        command = int(dict(item.get("metadata") or {}).get("mav_command") or mavutil.mavlink.MAV_CMD_NAV_WAYPOINT)
        param1, param2, param3, param4 = _mission_params_for_command(command, item)
        self._mavlink.mav.mission_item_send(
            self._mavlink.target_system,
            self._mavlink.target_component,
            seq,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
            command,
            1 if seq == 0 else 0,
            1,
            param1,
            param2,
            param3,
            param4,
            float(item["lat"]),
            float(item["lon"]),
            float(item["alt_m"]),
        )

    def _drain_mission_transfer_messages(self, timeout: float = 0.25) -> None:
        """Drop stale mission protocol messages before starting a new upload."""

        if not self.is_connected:
            return
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            msg = self._mavlink.recv_match(
                type=["MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"],
                blocking=False,
            )
            if msg is None:
                time.sleep(0.02)
                continue
            self._telemetry[f"STALE_{msg.get_type()}"] = self._message_dict_with_source(msg)

    def _message_dict_with_source(self, msg: Any) -> dict[str, Any]:
        data = msg.to_dict()
        try:
            data["_src_system"] = msg.get_srcSystem()
            data["_src_component"] = msg.get_srcComponent()
        except Exception:
            pass
        return data

    def _mission_ack_targets_this_gcs(self, ack: dict[str, Any]) -> bool:
        """Return whether a MISSION_ACK belongs to this GCS instance."""

        if self._mavlink is None:
            return False
        own_system = getattr(self._mavlink, "source_system", None)
        own_component = getattr(self._mavlink, "source_component", None)
        mav = getattr(self._mavlink, "mav", None)
        if own_system is None and mav is not None:
            own_system = getattr(mav, "srcSystem", None)
        if own_component is None and mav is not None:
            own_component = getattr(mav, "srcComponent", None)
        try:
            target_system = int(ack.get("target_system"))
            target_component = int(ack.get("target_component"))
            return target_system == int(own_system) and target_component == int(own_component)
        except (TypeError, ValueError):
            return False

    def _mission_item_from_msg(self, msg: Any) -> dict[str, Any]:
        data = msg.to_dict()
        seq = int(data.get("seq", len(self._telemetry.get("DOWNLOADED_MISSION", {}).get("items", []))) or 0)
        frame = int(data.get("frame", mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT) or 0)
        x_value = data.get("x", 0)
        y_value = data.get("y", 0)
        if msg.get_type() == "MISSION_ITEM_INT" and frame in {
            mavutil.mavlink.MAV_FRAME_GLOBAL_INT,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            mavutil.mavlink.MAV_FRAME_GLOBAL_TERRAIN_ALT_INT,
        }:
            lat = float(x_value) / 1e7
            lon = float(y_value) / 1e7
        else:
            lat = float(x_value or 0.0)
            lon = float(y_value or 0.0)
        command = int(data.get("command", 0) or 0)
        return {
            "id": f"wp_{seq:03d}",
            "type": _mission_type_for_command(command),
            "frame": "global_relative_alt",
            "lat": lat,
            "lon": lon,
            "alt_m": float(data.get("z", 0.0) or 0.0),
            "speed_mps": 0.0,
            "hold_s": float(data.get("param1", 0.0) or 0.0),
            "acceptance_radius_m": float(data.get("param2", 2.0) or 2.0),
            "actions": [],
            "metadata": {
                "seq": seq,
                "mav_frame": frame,
                "mav_command": command,
                "autocontinue": int(data.get("autocontinue", 1) or 0),
            },
        }

    def _wait_message(self, msg_type: str | list[str], timeout: float) -> Any | None:
        if not self.is_connected:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if not self.is_connected:
                    return None
                msg = self._mavlink.recv_match(type=msg_type, blocking=True, timeout=0.3)
            if msg is None:
                continue
            if msg.get_type() in {"MISSION_CURRENT", "MISSION_ITEM_REACHED", "HEARTBEAT", "LOCAL_POSITION_NED", "GLOBAL_POSITION_INT"}:
                self._handle_message(msg)
            return msg
        return None

    def _wait_mission_item(self, seq: int, timeout: float) -> Any | None:
        if not self.is_connected:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self._mavlink.recv_match(type=["MISSION_ITEM_INT", "MISSION_ITEM"], blocking=True, timeout=0.3)
            if msg is None:
                continue
            try:
                msg_seq = int(getattr(msg, "seq", -1))
            except (TypeError, ValueError):
                msg_seq = -1
            if msg_seq == seq:
                return msg
        return None

    def _wait_mission_ack(self, timeout: float) -> dict[str, Any]:
        msg = self._wait_message("MISSION_ACK", timeout=timeout)
        if msg is None:
            return {"accepted": False, "type": None, "message": "timeout"}
        data = msg.to_dict()
        ack_type = int(data.get("type", mavutil.mavlink.MAV_MISSION_ERROR) or 0)
        data["accepted"] = ack_type == mavutil.mavlink.MAV_MISSION_ACCEPTED
        self._telemetry["MISSION_ACK"] = data
        return data

    def _offboard_hold_active(self) -> bool:
        thread = self._offboard_hold_thread
        return bool(thread and thread.is_alive())

    def _start_offboard_position_hold(self, target: dict[str, float]) -> None:
        self._stop_offboard_hold()
        target = {key: float(target[key]) for key in ("x", "y", "z")}
        stop_event = threading.Event()

        def stream_hold() -> None:
            period = 1.0 / self._setpoint_hz
            while self.is_connected and not stop_event.is_set():
                self._send_position_setpoint(target)
                stop_event.wait(period)

        thread = threading.Thread(
            target=stream_hold,
            name="px4-offboard-hold",
            daemon=True,
        )
        self._offboard_hold_target = target
        self._offboard_hold_stop = stop_event
        self._offboard_hold_thread = thread
        thread.start()

    def _start_offboard_velocity_stream(self, velocity: dict[str, float]) -> None:
        self._stop_offboard_hold()
        velocity = {key: float(velocity[key]) for key in ("vx", "vy", "vz")}
        stop_event = threading.Event()

        def stream_velocity() -> None:
            period = 1.0 / self._setpoint_hz
            while self.is_connected and not stop_event.is_set():
                self._send_velocity_setpoint(velocity)
                stop_event.wait(period)

        thread = threading.Thread(
            target=stream_velocity,
            name="px4-offboard-velocity",
            daemon=True,
        )
        self._offboard_hold_target = None
        self._offboard_hold_stop = stop_event
        self._offboard_hold_thread = thread
        thread.start()

    def _stop_offboard_hold(self) -> None:
        stop_event = self._offboard_hold_stop
        thread = self._offboard_hold_thread
        self._offboard_hold_stop = None
        self._offboard_hold_thread = None
        self._offboard_hold_target = None
        if stop_event is not None:
            stop_event.set()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=0.6)

    def _finish_offboard_position_hold(self, target: dict[str, float]) -> bool:
        """Leave OFFBOARD without a setpoint gap, or keep a safe hold worker alive.

        Mode switches go through _set_mode_one so they act on the CURRENT
        target system (multi-vehicle), never re-resolving to the first one.
        """
        self._start_offboard_position_hold(target)
        if self._set_mode_one("LOITER") or self._set_mode_one("POSCTL"):
            self._stop_offboard_hold()
            return True
        logger.warning(
            f"PX4 did not accept LOITER/POSCTL; continuing OFFBOARD position hold at {target}"
        )
        return self._offboard_hold_active()

    def _prime_offboard_position(self, target: dict[str, float]) -> bool:
        """Start streaming position setpoints and wait briefly so PX4 sees the
        stream before the OFFBOARD mode switch. Returns False on stop."""
        self._start_offboard_position_hold(target)
        return self._sleep_interruptible(1.0)

    def _prime_offboard_velocity(self, velocity: dict[str, float]) -> bool:
        """Start streaming velocity setpoints and wait briefly so PX4 sees the
        stream before the OFFBOARD mode switch. Returns False on stop."""
        self._start_offboard_velocity_stream(velocity)
        return self._sleep_interruptible(1.0)

    def _stream_position_setpoint(self, target: dict[str, float], duration: float) -> bool:
        end = time.time() + max(0.0, duration)
        while time.time() < end:
            if self._stop_requested():
                return False
            self._send_position_setpoint(target)
            time.sleep(1.0 / self._setpoint_hz)
        return True

    def _stream_velocity_setpoint(self, velocity: dict[str, float], duration: float) -> bool:
        end = time.time() + max(0.0, duration)
        while time.time() < end:
            if self._stop_requested():
                return False
            self._send_velocity_setpoint(velocity)
            time.sleep(1.0 / self._setpoint_hz)
        return True

    def _send_position_setpoint(self, target: dict[str, float]) -> None:
        self._send_position_target_local_ned(
            frame=mavutil.mavlink.MAV_FRAME_LOCAL_NED,
            type_mask=_MASK_POSITION_ONLY,
            x=target["x"],
            y=target["y"],
            z=target["z"],
        )

    def _send_velocity_setpoint(self, velocity: dict[str, float]) -> None:
        self._send_position_target_local_ned(
            frame=mavutil.mavlink.MAV_FRAME_LOCAL_NED,
            type_mask=_MASK_VELOCITY_ONLY,
            vx=velocity["vx"],
            vy=velocity["vy"],
            vz=velocity["vz"],
        )

    def _send_yaw_rate_setpoint(self, yaw_rate: float) -> None:
        self._send_position_target_local_ned(
            frame=mavutil.mavlink.MAV_FRAME_LOCAL_NED,
            type_mask=_MASK_YAW_RATE_ONLY,
            yaw_rate=yaw_rate,
        )

    def _send_position_target_local_ned(
        self,
        frame: int,
        type_mask: int,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        vx: float = 0.0,
        vy: float = 0.0,
        vz: float = 0.0,
        yaw: float = 0.0,
        yaw_rate: float = 0.0,
    ) -> None:
        with self._lock:
            if not self.is_connected:
                return
            self._mavlink.mav.set_position_target_local_ned_send(
                int(time.time() * 1000) & 0xFFFFFFFF,
                self._mavlink.target_system,
                self._mavlink.target_component,
                frame,
                type_mask,
                x,
                y,
                z,
                vx,
                vy,
                vz,
                0,
                0,
                0,
                yaw,
                yaw_rate,
            )

    def _wait_command_ack(self, command: int, timeout: float) -> bool:
        if not self.is_connected:
            self._last_action_error = "MAVLink is not connected"
            return False
        deadline = time.time() + timeout
        accepted = {
            mavutil.mavlink.MAV_RESULT_ACCEPTED,
            mavutil.mavlink.MAV_RESULT_IN_PROGRESS,
        }
        while time.time() < deadline:
            with self._lock:
                if not self.is_connected:
                    return False
                msg = self._mavlink.recv_match(blocking=True, timeout=0.2)
            if msg is None:
                continue
            if msg.get_type() == "COMMAND_ACK":
                msg_dict = msg.to_dict()
                self._telemetry["COMMAND_ACK"] = msg_dict
                if int(msg_dict.get("command", -1)) == int(command):
                    result = int(msg_dict.get("result", -1))
                    if result in accepted:
                        return True
                    result_name = self._mav_result_name(result)
                    command_name = self._mav_command_name(command)
                    detail = f"Flight controller rejected {command_name} ({command}): {result_name}"
                    result_param2 = int(msg_dict.get("result_param2", 0) or 0)
                    if result_param2:
                        detail += f" (result_param2={result_param2})"
                    self._last_action_error = self._with_status_text(detail)
                    return False
            else:
                self._handle_message(msg)
        if self._link_is_stale():
            self._last_action_error = self._stale_link_message()
        else:
            self._last_action_error = self._with_status_text(f"Timed out waiting for COMMAND_ACK for command {command}")
        return False

    def _mav_result_name(self, result: int) -> str:
        try:
            enum_value = mavutil.mavlink.enums["MAV_RESULT"].get(result)
            return str(getattr(enum_value, "name", "") or f"MAV_RESULT_{result}")
        except Exception:
            return f"MAV_RESULT_{result}"

    def _mav_command_name(self, command: int) -> str:
        try:
            enum_value = mavutil.mavlink.enums["MAV_CMD"].get(int(command))
            return str(getattr(enum_value, "name", "") or f"MAV_CMD_{command}")
        except Exception:
            return f"MAV_CMD_{command}"

    def _latest_status_text(self) -> str:
        raw = (self._telemetry.get("STATUSTEXT") or {}).get("text", "")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return str(raw or "").strip().strip("\x00")

    def _with_status_text(self, message: str) -> str:
        status_text = self._latest_status_text()
        if status_text and status_text.lower() not in message.lower():
            return f"{message}; PX4: {status_text}"
        return message

    def _link_is_stale(self, max_age_s: float = 5.0) -> bool:
        last_heartbeat = self._system_table(self._target_sysid())["last_heartbeat"]
        if not last_heartbeat:
            return True
        return time.time() - last_heartbeat > max_age_s

    def _stale_link_message(self) -> str:
        last_heartbeat = self._system_table(self._target_sysid())["last_heartbeat"]
        if not last_heartbeat:
            return "No PX4 MAVLink heartbeat has been received yet."
        age = time.time() - last_heartbeat
        return f"PX4 MAVLink heartbeat is lost (last heartbeat {age:.1f}s ago); reconnect the flight controller."

    def _wait_vehicle_heartbeat(
        self,
        timeout: float,
        probe_targets: list[tuple[str, int]] | None = None,
    ) -> Any:
        if self._mavlink is None:
            raise RuntimeError("MAVLink endpoint is not open")
        deadline = time.time() + timeout
        next_probe = 0.0
        last_heartbeat: Any | None = None
        while time.time() < deadline:
            now = time.time()
            if now >= next_probe:
                self._send_gcs_heartbeat(probe_targets or [])
                next_probe = now + 0.5
            with self._lock:
                if self._mavlink is None:
                    raise RuntimeError("MAVLink endpoint is not open")
                msg = self._mavlink.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
            if msg is None:
                continue
            last_heartbeat = msg
            if self._is_vehicle_heartbeat(msg):
                return msg
            self._telemetry["GCS_HEARTBEAT"] = msg.to_dict()
        if last_heartbeat is None:
            raise TimeoutError("no MAVLink heartbeat received")
        raise TimeoutError("no vehicle heartbeat received; only non-vehicle heartbeats were observed")

    def _send_gcs_heartbeat(self, targets: list[tuple[str, int]]) -> None:
        """Announce this client so PX4 can learn or accept the GCS endpoint."""
        if self._mavlink is None:
            return
        try:
            message = mavutil.mavlink.MAVLink_heartbeat_message(
                mavutil.mavlink.MAV_TYPE_GCS,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                0,
                0,
                mavutil.mavlink.MAV_STATE_ACTIVE,
                3,
            )
            if targets and hasattr(self._mavlink, "port"):
                packet = message.pack(self._mavlink.mav)
                for target in targets:
                    self._mavlink.port.sendto(packet, target)
            elif not targets:
                self._mavlink.mav.heartbeat_send(
                    mavutil.mavlink.MAV_TYPE_GCS,
                    mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                    0,
                    0,
                    mavutil.mavlink.MAV_STATE_ACTIVE,
                )
        except (AttributeError, OSError) as exc:
            logger.debug(f"MAVLink heartbeat probe failed: {exc}")

    def _is_vehicle_heartbeat(self, msg: Any) -> bool:
        try:
            msg_dict = msg.to_dict()
        except Exception:
            return False
        vehicle_type = int(msg_dict.get("type", -1))
        autopilot = int(msg_dict.get("autopilot", -1))
        if vehicle_type == mavutil.mavlink.MAV_TYPE_GCS:
            return False
        if autopilot == mavutil.mavlink.MAV_AUTOPILOT_PX4:
            return True
        vehicle_types = {
            mavutil.mavlink.MAV_TYPE_FIXED_WING,
            mavutil.mavlink.MAV_TYPE_QUADROTOR,
            mavutil.mavlink.MAV_TYPE_COAXIAL,
            mavutil.mavlink.MAV_TYPE_HELICOPTER,
            mavutil.mavlink.MAV_TYPE_HEXAROTOR,
            mavutil.mavlink.MAV_TYPE_OCTOROTOR,
            mavutil.mavlink.MAV_TYPE_TRICOPTER,
            mavutil.mavlink.MAV_TYPE_VTOL_DUOROTOR,
            mavutil.mavlink.MAV_TYPE_VTOL_QUADROTOR,
            mavutil.mavlink.MAV_TYPE_VTOL_TILTROTOR,
        }
        return vehicle_type in vehicle_types

    def _wait_until(self, predicate, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.update_telemetry(timeout=0.2)
            if predicate():
                return True
        return False

    def _wait_mode(self, canonical_mode: str, timeout: float) -> bool:
        return self._wait_until(lambda: self._current_mode() == canonical_mode, timeout=timeout)

    def _prepare_px4_mode_for_arm(self) -> bool:
        mode = self._current_mode()
        if mode in {"", "OFFBOARD", "POSCTL", "LOITER", "ALTCTL", "MANUAL"}:
            return True
        if mode in {"TAKEOFF", "AUTO.TAKEOFF", "AUTO_TAKEOFF", "LAND", "RTL", "MISSION"} or mode.startswith("AUTO."):
            if self.set_mode("POSCTL") or self.set_mode("LOITER"):
                return True
            self._last_action_error = self._with_status_text(
                f"PX4 is in {mode}; could not switch to POSCTL/LOITER before arming."
            )
            return False
        return True

    def _current_mode(self) -> str:
        heartbeat = self._telemetry_for_target().get("HEARTBEAT", {})
        mode = str(heartbeat.get("mode") or "")
        if mode == "AUTO.LOITER":
            return "LOITER"
        if mode == "AUTO.MISSION":
            return "MISSION"
        return mode

    def _mode_from_heartbeat_msg(self, msg: Any) -> str:
        try:
            data = msg.to_dict()
            if int(data.get("autopilot", -1)) == mavutil.mavlink.MAV_AUTOPILOT_PX4:
                custom_mode = int(data.get("custom_mode", 0) or 0)
                main_mode = (custom_mode >> 16) & 0xFF
                sub_mode = (custom_mode >> 24) & 0xFF
                main_modes = {
                    1: "MANUAL",
                    2: "ALTCTL",
                    3: "POSCTL",
                    5: "ACRO",
                    6: "OFFBOARD",
                    7: "STABILIZED",
                    8: "RATTITUDE",
                }
                auto_modes = {
                    1: "AUTO.READY",
                    2: "TAKEOFF",
                    3: "LOITER",
                    4: "MISSION",
                    5: "RTL",
                    6: "LAND",
                    7: "AUTO.RTGS",
                    8: "AUTO.FOLLOW_TARGET",
                    9: "AUTO.PRECLAND",
                }
                if main_mode == 4:
                    return auto_modes.get(sub_mode, f"AUTO({sub_mode})")
                if main_mode in main_modes:
                    return main_modes[main_mode]
        except Exception:
            pass
        try:
            mode = mavutil.mode_string_v10(msg)
        except Exception:
            mode = f"Mode({getattr(msg, 'custom_mode', -1)})"
        if mode == "AUTO.LOITER":
            return "LOITER"
        if mode == "AUTO.MISSION":
            return "MISSION"
        return mode

    def _canonical_px4_mode(self, mode: str) -> str:
        aliases = {
            "GUIDED": "OFFBOARD",
            "HOLD": "LOITER",
            "POSHOLD": "POSCTL",
            "POSITION": "POSCTL",
            "POSITION_HOLD": "POSCTL",
            "AUTO_LOITER": "LOITER",
            "AUTO.LOITER": "LOITER",
            "AUTO_MISSION": "MISSION",
            "AUTO.MISSION": "MISSION",
            "BRAKE": "LOITER",
        }
        return aliases.get(mode, mode)

    def _canonical_apm_mode(self, mode: str) -> str:
        aliases = {"OFFBOARD": "GUIDED", "POSCTL": "LOITER", "POSHOLD": "LOITER"}
        return aliases.get(mode, mode)

    def _is_px4(self) -> bool:
        return self._system_table(self._target_sysid())["autopilot"] == mavutil.mavlink.MAV_AUTOPILOT_PX4

    def _is_armed(self) -> bool:
        self.update_telemetry(timeout=0.1)
        return bool((self._telemetry_for_target().get("HEARTBEAT") or {}).get("armed", False))

    def _current_altitude_m(self) -> float:
        return abs(float(self._system_table(self._target_sysid())["position"].get("z", 0.0) or 0.0))

    def _distance_to(self, target: dict[str, float]) -> float:
        position = self._system_table(self._target_sysid())["position"]
        return math.sqrt(
            (position["x"] - target["x"]) ** 2
            + (position["y"] - target["y"]) ** 2
            + (position["z"] - target["z"]) ** 2
        )

    def _limit_velocity(self, velocity: dict[str, float]) -> dict[str, float]:
        speed = math.sqrt(velocity["vx"] ** 2 + velocity["vy"] ** 2 + velocity["vz"] ** 2)
        if speed <= self._max_velocity or speed <= 1e-6:
            return velocity
        scale = self._max_velocity / speed
        return {key: value * scale for key, value in velocity.items()}

    def _decode_mode(self, custom_mode: int) -> str:
        heartbeat = self._telemetry.get("HEARTBEAT", {})
        if heartbeat.get("mode"):
            return str(heartbeat["mode"])
        return f"UNKNOWN({custom_mode})"

    def _open_mavlink_connection(self, candidate: str) -> Any:
        options = {
            "autoreconnect": True,
            "source_system": 255,
            "source_component": 190,
        }
        if candidate.startswith("serial:"):
            endpoint = candidate[len("serial:") :]
            device, separator, baud_text = endpoint.rpartition(":")
            if not separator or not device:
                raise ValueError("serial URL must be serial:PORT:BAUD")
            baud = normalize_serial_baud(baud_text)
            return mavutil.mavlink_connection(device, baud=baud, **options)
        return mavutil.mavlink_connection(candidate, **options)

    def _candidate_urls(self, url: str, fallback_url: str = "") -> list[str]:
        # Build a list of candidate connection URLs to try sequentially.
        # For UDP URLs we try both "listen" (udpin) and "send" (udpout)
        # because PX4 SITL's GCS link sends *to* port 14550 while
        # listening on 18570.  The GCS must therefore bind to 14550 to
        # receive PX4's heartbeats.
        #
        # User can explicitly prefix with udpin:/udpout: to skip guessing.
        urls, metadata = self._candidate_urls_with_metadata(url, fallback_url=fallback_url)
        self._candidate_metadata = metadata
        return urls

    def _candidate_urls_with_metadata(
        self,
        url: str,
        fallback_url: str = "",
    ) -> tuple[list[str], dict[str, dict[str, Any]]]:
        stripped = url.strip()

        if stripped in {"", "auto", "auto:", "autodetect", "autodetect:"}:
            return self._auto_candidate_urls(serial_only=False, fallback_url=fallback_url)

        if stripped in {"auto:serial", "serial:auto", "serial:"}:
            return self._auto_candidate_urls(serial_only=True, fallback_url="")

        # If user already specified the exact mode, honour it.
        if stripped.startswith("serial:"):
            serial_url, serial_meta = self._normalize_serial_url(stripped)
            return [serial_url], {serial_url: serial_meta}
        if stripped.startswith(("udpin:", "udpout:", "tcp:")):
            return [stripped], {}

        # udp:HOST:PORT - try udpin (listen) first, then udpout (send).
        # Many PX4 SITL setups broadcast heartbeats to a well-known port
        # (14550), so binding to that port is the fastest path.
        if stripped.startswith("udp:"):
            host_port = stripped[4:]  # "HOST:PORT"
            return [
                f"udpin:0.0.0.0:{host_port.split(':')[-1]}",  # listen on PORT
                stripped,                                       # udp: bidir
                f"udpout:{host_port}",                          # send to HOST:PORT
            ], {}

        # Bare HOST:PORT or no prefix - treat as udpout.
        return [stripped, f"udpin:0.0.0.0:14550"], {}

    def _auto_candidate_urls(
        self,
        serial_only: bool,
        fallback_url: str = "",
    ) -> tuple[list[str], dict[str, dict[str, Any]]]:
        urls: list[str] = []
        metadata: dict[str, dict[str, Any]] = {}
        serial_candidates = discover_serial_mavlink_candidates()
        for candidate in serial_candidates:
            urls.append(candidate.url)
            metadata[candidate.url] = {"type": "serial", **candidate.to_dict()}

        if not serial_only:
            fallback = fallback_url.strip() or config.px4_connection_string
            fallback_urls, fallback_metadata = self._candidate_urls_with_metadata(fallback, fallback_url="")
            urls.extend(fallback_urls)
            metadata.update(fallback_metadata)

        return self._dedupe_urls(urls), metadata

    def _normalize_serial_url(self, url: str) -> tuple[str, dict[str, Any]]:
        endpoint = url[len("serial:") :]
        device, separator, baud_text = endpoint.rpartition(":")
        if not separator:
            device = endpoint
            baud_text = ""
        device = device.strip()
        if not device or device.lower() == "auto":
            candidates = discover_serial_mavlink_candidates(preferred_baud=baud_text)
            if not candidates:
                raise ValueError("no MAVLink serial ports were detected")
            candidate = candidates[0]
            return candidate.url, {"type": "serial", **candidate.to_dict()}
        candidates = discover_serial_mavlink_candidates(
            preferred_device=device,
            preferred_baud=baud_text,
            include_unknown=True,
        )
        if candidates:
            candidate = candidates[0]
            return candidate.url, {"type": "serial", **candidate.to_dict()}
        baud = normalize_serial_baud(baud_text)
        normalized = f"serial:{device}:{baud}"
        return normalized, {
            "type": "serial",
            "device": device,
            "url": normalized,
            "baud": baud,
            "board_type": "Unknown",
            "board_name": device,
            "score": 1,
        }

    def _dedupe_urls(self, urls: list[str]) -> list[str]:
        deduped: list[str] = []
        for item in urls:
            if item not in deduped:
                deduped.append(item)
        return deduped

    def _heartbeat_timeout_for_candidate(self, candidate: str) -> float:
        return 6.0 if candidate.startswith("serial:") else 4.0

    def _heartbeat_probe_targets(
        self,
        url: str,
        remote_host: str = "",
        remote_port: int = 0,
    ) -> list[tuple[str, int]]:
        """Return UDP endpoints that may need a GCS heartbeat before replying.

        PX4 SITL instances listen for GCS traffic on 18570 + instance while
        GCS clients conventionally listen on 14550 + instance. Recent PX4
        startup scripts do not set a fixed remote endpoint, so a client must
        announce itself before PX4 knows where to send telemetry.
        """
        stripped = url.strip()
        prefix = next(
            (value for value in ("udpout:", "udpin:", "udp:") if stripped.startswith(value)),
            "",
        )
        if not prefix:
            return []

        endpoint = stripped[len(prefix) :]
        host, separator, port_text = endpoint.rpartition(":")
        if not separator:
            return []
        try:
            port = int(port_text)
        except ValueError:
            return []

        target_host = host.strip("[]")
        if target_host in {"", "0.0.0.0", "::"}:
            target_host = "127.0.0.1"

        if remote_port:
            explicit_host = remote_host.strip().strip("[]") or target_host
            try:
                return self._expand_windows_wsl_targets(explicit_host, int(remote_port))
            except Exception:
                return [(explicit_host, int(remote_port))]

        if 14550 <= port <= 14559:
            try:
                return self._expand_windows_wsl_targets(target_host, 18570 + (port - 14550))
            except Exception:
                return [(target_host, 18570 + (port - 14550))]
        if prefix == "udpout:" and 18570 <= port <= 18579:
            return self._expand_windows_wsl_targets(target_host, port)
        return []

    def _expand_windows_wsl_targets(self, host: str, port: int) -> list[tuple[str, int]]:
        targets = [(host, port)]
        if os.name != "nt" or not self._is_loopback_host(host):
            return targets
        for candidate in self._wsl_ipv4_candidates():
            if candidate != host:
                targets.append((candidate, port))
        return targets

    def _is_loopback_host(self, host: str) -> bool:
        return host in {"", "localhost", "127.0.0.1", "::1"}

    def _wsl_ipv4_candidates(self) -> list[str]:
        try:
            completed = subprocess.run(
                ["wsl.exe", "--", "bash", "-lc", "hostname -I"],
                check=False,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=1.5,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        # wsl.exe 输出可能含非法 UTF-8 字节（ANSI/UTF-16 混杂），解码失败时
        # stdout 会是 None——判空防止 None.split() 把整个连接流程打崩
        if not completed.stdout:
            return []
        addresses: list[str] = []
        for token in completed.stdout.split():
            token = token.strip()
            if not token or ":" in token or token.startswith("169.254."):
                continue
            parts = token.split(".")
            if len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
                addresses.append(token)
        return addresses


def _gps_offset_m(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    lat_rad = math.radians(lat1)
    meters_per_lat = 111132.92 - 559.82 * math.cos(2 * lat_rad) + 1.175 * math.cos(4 * lat_rad)
    meters_per_lon = 111412.84 * math.cos(lat_rad) - 93.5 * math.cos(3 * lat_rad)
    return (lat2 - lat1) * meters_per_lat, (lon2 - lon1) * meters_per_lon


def _gps_from_offset_m(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    lat_rad = math.radians(lat)
    meters_per_lat = 111132.92 - 559.82 * math.cos(2 * lat_rad) + 1.175 * math.cos(4 * lat_rad)
    meters_per_lon = 111412.84 * math.cos(lat_rad) - 93.5 * math.cos(3 * lat_rad)
    if abs(meters_per_lon) < 1e-6:
        meters_per_lon = 1e-6
    return lat + north_m / meters_per_lat, lon + east_m / meters_per_lon


def _mission_command_for_item(item_type: str, item: dict[str, Any]) -> int:
    metadata = dict(item.get("metadata") or {})
    explicit = _optional_int(item.get("mav_command"))
    if explicit is None:
        explicit = _optional_int(metadata.get("mav_command"))
    if explicit is not None:
        return explicit

    normalized = (item_type or "waypoint").strip().lower().replace("-", "_")
    if normalized in {"takeoff", "nav_takeoff"}:
        return mavutil.mavlink.MAV_CMD_NAV_TAKEOFF
    if normalized in {"land", "landing", "nav_land"}:
        return mavutil.mavlink.MAV_CMD_NAV_LAND
    if normalized in {"rtl", "return_home", "return_to_launch", "nav_return_to_launch"}:
        return mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH
    return mavutil.mavlink.MAV_CMD_NAV_WAYPOINT


def _mission_type_for_command(command: int) -> str:
    if command == mavutil.mavlink.MAV_CMD_NAV_TAKEOFF:
        return "takeoff"
    if command == mavutil.mavlink.MAV_CMD_NAV_LAND:
        return "land"
    if command == mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH:
        return "return_to_launch"
    return "waypoint"


def _mission_params_for_command(command: int, item: dict[str, Any]) -> tuple[float, float, float, float]:
    yaw = _optional_float(item.get("yaw_deg")) or 0.0
    if command == mavutil.mavlink.MAV_CMD_NAV_WAYPOINT:
        return (
            max(0.0, float(item.get("hold_s", 0.0) or 0.0)),
            max(0.0, float(item.get("acceptance_radius_m", 2.0) or 2.0)),
            0.0,
            yaw,
        )
    if command == mavutil.mavlink.MAV_CMD_NAV_TAKEOFF:
        return (0.0, 0.0, 0.0, yaw)
    if command == mavutil.mavlink.MAV_CMD_NAV_LAND:
        return (0.0, 0.0, 0.0, yaw)
    if command == mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH:
        return (0.0, 0.0, 0.0, 0.0)
    return (0.0, 0.0, 0.0, yaw)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
