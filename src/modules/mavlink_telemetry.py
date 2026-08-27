"""遥测解析：update_telemetry/_handle_message、sysid 解析、消息归一化。

拆分自 mavlink_controller.py（MavlinkController 方法按职责迁移，行为不变）。
"""
from __future__ import annotations

import math
import os
import struct
import subprocess
import threading
import time
from collections import deque
from typing import Any, Callable

from pymavlink import mavutil
from .flight_controller import ConnectionInfo, DroneStatus, FlightController
from .mavlink_autodiscovery import (
    discover_serial_mavlink_candidates,
    normalize_serial_baud,
)
from ..config import config
from ..logging_config import get_logger
from .mavlink_utils import (
    _gps_offset_m,
    _gps_from_offset_m,
    _mission_command_for_item,
    _mission_type_for_command,
    _mission_params_for_command,
    _optional_float,
    _optional_int,
)
logger = get_logger(__name__)

class MavlinkTelemetryMixin:
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
