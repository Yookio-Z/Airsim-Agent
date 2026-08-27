"""连接与链路：初始化/属性、connect/disconnect、链路健康、连接发现与心跳。

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

class MavlinkConnectMixin:
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
            return self._expand_windows_wsl_targets(explicit_host, int(remote_port))

        if 14550 <= port <= 14559:
            return self._expand_windows_wsl_targets(target_host, 18570 + (port - 14550))
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
                timeout=1.5,
            )
        except (OSError, subprocess.SubprocessError):
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
