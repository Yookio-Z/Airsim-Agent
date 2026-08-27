"""参数与固件：参数读写/下载、vehicle 快照、参数/固件解码。

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

class MavlinkParamsMixin:
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
            return {key: MavlinkParamsMixin._copy_payload(item) for key, item in value.items()}
        if isinstance(value, list):
            return [MavlinkParamsMixin._copy_payload(item) for item in value]
        if isinstance(value, tuple):
            return [MavlinkParamsMixin._copy_payload(item) for item in value]
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
            "sensor_state": MavlinkParamsMixin._sensor_state_text(rc),
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
            "sensor_state": MavlinkParamsMixin._sensor_state_text(battery_sensor),
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
            "sensor_state": MavlinkParamsMixin._sensor_state_text(motor),
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
        type_name = MavlinkParamsMixin._enum_label("MAV_PARAM_TYPE", type_id) or str(type_id or "")
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
            "type_name": MavlinkParamsMixin._enum_label("FIRMWARE_VERSION_TYPE", version_type),
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
