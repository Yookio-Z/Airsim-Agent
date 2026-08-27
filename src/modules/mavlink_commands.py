"""飞行指令：起降、运动/速度控制、move_on_path 与状态汇总。

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

class MavlinkCommandsMixin:
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
        if not ack_ok:
            return False
        armed = self._wait_until(lambda: self._is_armed(), timeout=6.0)
        if not armed:
            self._last_action_error = self._with_status_text(
                "Flight controller accepted the arm command but did not enter armed state; check PX4 preflight checks and the safety switch."
            )
        return armed

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
            if not self._takeoff_one(altitude):
                ok = False
                break
        return ok

    def _takeoff_one(self, altitude: float = 3.0) -> bool:
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
