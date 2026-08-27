"""任务与 offboard：模式切换、任务上传/下载/启动、offboard 流与控制命令。

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



class MavlinkMissionMixin:
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
