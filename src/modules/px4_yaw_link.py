"""Standalone MAVLink yaw servo for a PX4 that this process does not otherwise own.

Why this exists
---------------
AirSim's ``PX4Multirotor`` vehicle type runs a real PX4 SITL locked to the
simulator. In that configuration AirSim does not own attitude: ``rotateToYawAsync``
is a no-op, because the autopilot is what actually turns the airframe. The
previous behaviour was to refuse yaw outright, which left the visual servo
(``_center_target``) and every tracking skill unable to aim the nose in the
simulator at all.

The same autopilot is reachable over its MAVLink endpoint (``ControlPortLocal``
in AirSim's settings.json). So yaw has to be commanded there instead.

Scope and safety
----------------
This link is deliberately *narrow*: connect, hand over to OFFBOARD while holding
position, stream yaw-rate setpoints until the heading converges, then restore a
position hold and disconnect. It never commands position or velocity.

It is also deliberately *sequential*. While AirSim's RPC is driving a move on a
locked-step PX4, the autopilot is already receiving setpoints through the
lock-step channel. Streaming OFFBOARD setpoints from a second link at the same
time means two controllers commanding one vehicle, so callers must run this
**before** issuing a move, never concurrently with one.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

logger = logging.getLogger(__name__)


def normalize_heading_deg(value: float) -> float:
    """Fold an angle into [0, 360)."""
    return float(value) % 360.0


def heading_error_deg(current_deg: float, target_deg: float) -> float:
    """Signed shortest turn from ``current`` to ``target``, in (-180, 180]."""
    error = normalize_heading_deg(target_deg) - normalize_heading_deg(current_deg)
    if error > 180.0:
        error -= 360.0
    elif error <= -180.0:
        error += 360.0
    return error


def bearing_to_deg(dx: float, dy: float) -> float:
    """Heading of the NED vector (dx, dy), in degrees, 0 = north.

    Uses the same convention as every other heading in this codebase: 0 = north,
    increasing clockwise, matching PX4/MAVLink yaw.
    """
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        raise ValueError("zero-length NED vector has no bearing")
    return normalize_heading_deg(math.degrees(math.atan2(dx, dy)))


class Px4YawLink:
    """Command a PX4's heading over MAVLink, holding position throughout."""

    def __init__(
        self,
        connection_string: str,
        setpoint_hz: float = 15.0,
        tolerance_deg: float = 8.0,
        max_rate_dps: float = 45.0,
        connect_timeout: float = 8.0,
    ) -> None:
        self.connection_string = str(connection_string or "").strip()
        self._setpoint_hz = max(2.0, float(setpoint_hz or 15.0))
        self._tolerance_deg = max(0.5, float(tolerance_deg or 8.0))
        self._max_rate_dps = max(1.0, float(max_rate_dps or 45.0))
        self._connect_timeout = max(1.0, float(connect_timeout or 8.0))
        self._mav: Any | None = None
        self._previous_mode: int | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._mav is not None

    def close(self) -> None:
        mav = self._mav
        self._mav = None
        self._previous_mode = None
        if mav is None:
            return
        try:
            mav.close()
        except Exception:
            pass

    def _ensure_connected(self) -> bool:
        if self._mav is not None:
            return True
        if not self.connection_string:
            return False
        try:
            from pymavlink import mavutil

            mav = mavutil.mavlink_connection(
                self.connection_string,
                autoreconnect=True,
                source_system=255,
                source_component=190,
            )
        except Exception as exc:
            logger.debug("yaw link: cannot open %s: %s", self.connection_string, exc)
            return False

        heartbeat = self._wait_heartbeat(mav)
        if heartbeat is None:
            try:
                mav.close()
            except Exception:
                pass
            return False
        try:
            mav.target_system = heartbeat.get_srcSystem()
            mav.target_component = heartbeat.get_srcComponent()
        except Exception:
            try:
                mav.close()
            except Exception:
                pass
            return False
        self._mav = mav
        return True

    def _wait_heartbeat(self, mav: Any) -> Any | None:
        deadline = time.time() + self._connect_timeout
        while time.time() < deadline:
            try:
                msg = mav.recv_match(type=["HEARTBEAT"], blocking=True, timeout=1.0)
            except Exception:
                return None
            if msg is not None:
                return msg
        return None

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------
    def current_heading_deg(self) -> float | None:
        """Heading from GLOBAL_POSITION_INT, or None when the FC does not report it.

        ``hdg`` is centidegrees (0..35999) and 65535 is the documented "unknown"
        sentinel. Note 0 is *not* unknown — it is a valid heading due north, so
        rejecting it here would make a north-facing vehicle look like it had no
        heading feedback at all. The caller must treat None as "no idea", never
        as 0.
        """
        if self._mav is None:
            return None
        try:
            msg = self._mav.recv_match(
                type=["GLOBAL_POSITION_INT"], blocking=True, timeout=0.5
            )
        except Exception:
            return None
        if msg is None:
            return None
        raw = getattr(msg, "hdg", None)
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if value < 0.0 or value >= 65535.0:
            return None
        return normalize_heading_deg(value / 100.0)

    def _current_mode(self) -> int | None:
        if self._mav is None:
            return None
        try:
            msg = self._mav.recv_match(type=["HEARTBEAT"], blocking=True, timeout=0.5)
        except Exception:
            return None
        if msg is None:
            return None
        custom = getattr(msg, "custom_mode", None)
        if custom is None:
            return None
        try:
            return int(custom)
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Setpoints
    # ------------------------------------------------------------------
    def _send_yaw_rate_setpoint(self, yaw_rate_rad_s: float) -> None:
        """Hold position and command a yaw rate.

        PX4 rejects a pure yaw-rate setpoint (no position *and* no velocity
        field active) as invalid, so the mask leaves velocity active at zero and
        ignores position — the same shape mavlink_controller uses.
        """
        if self._mav is None:
            return
        from pymavlink import mavutil

        mask = (
            mavutil.mavlink.POSITION_TARGET_TYPEMASK_X_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Y_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_Z_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AX_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AY_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_AZ_IGNORE
            | mavutil.mavlink.POSITION_TARGET_TYPEMASK_YAW_IGNORE
        )
        self._mav.mav.set_position_target_local_ned_send(
            int(time.time() * 1000) & 0xFFFFFFFF,
            self._mav.target_system,
            self._mav.target_component,
            mavutil.mavlink.MAV_FRAME_LOCAL_NED,
            mask,
            0.0, 0.0, 0.0,          # position ignored
            0.0, 0.0, 0.0,          # velocity zero -> hold station
            0.0,                    # yaw ignored
            float(yaw_rate_rad_s),  # yaw rate
        )

    def _prime_offboard(self, seconds: float = 1.0) -> None:
        """Stream a hold setpoint so PX4 accepts the OFFBOARD switch."""
        deadline = time.time() + max(0.1, seconds)
        while time.time() < deadline:
            self._send_yaw_rate_setpoint(0.0)
            time.sleep(1.0 / self._setpoint_hz)

    def _set_mode(self, mode: int) -> bool:
        if self._mav is None:
            return False
        from pymavlink import mavutil

        try:
            self._mav.mav.command_long_send(
                self._mav.target_system,
                self._mav.target_component,
                mavutil.mavlink.MAV_CMD_DO_SET_MODE,
                0,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                float(mode),
                0.0, 0.0, 0.0, 0.0, 0.0,
            )
        except Exception as exc:
            logger.debug("yaw link: DO_SET_MODE failed: %s", exc)
            return False
        # Give PX4 a moment to acknowledge before the caller assumes success.
        deadline = time.time() + 1.5
        while time.time() < deadline:
            if self._current_mode() == mode:
                return True
            time.sleep(0.1)
        return self._current_mode() == mode

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def rotate_to(self, heading_deg: float, timeout: float = 30.0) -> bool:
        """Turn to ``heading_deg`` while holding position. True once converged."""
        if not self._ensure_connected():
            return False

        target = normalize_heading_deg(heading_deg)
        deadline = time.time() + max(1.0, float(timeout or 30.0))
        from pymavlink import mavutil

        offboard = int(getattr(mavutil.mavlink, "MAV_MODE_OFFBOARD", 4) or 4)
        self._previous_mode = self._current_mode()
        self._prime_offboard()
        if not self._set_mode(offboard):
            self._restore_mode()
            return False

        converged = False
        try:
            while time.time() < deadline:
                current = self.current_heading_deg()
                if current is None:
                    # No heading feedback: blind spinning is not acceptable.
                    self._send_yaw_rate_setpoint(0.0)
                    return False
                error = heading_error_deg(current, target)
                if abs(error) <= self._tolerance_deg:
                    converged = True
                    break
                # Proportional on the wrapped error, clamped to the configured
                # rate limit. Near the target the rate naturally shrinks, so the
                # vehicle settles instead of oscillating around it.
                rate_dps = max(
                    -self._max_rate_dps, min(self._max_rate_dps, error * 0.4)
                )
                self._send_yaw_rate_setpoint(math.radians(rate_dps))
                time.sleep(1.0 / self._setpoint_hz)
        except Exception as exc:
            logger.warning("yaw link: rotate failed: %s", exc)
        finally:
            # Stop commanding rotation and leave the vehicle holding station.
            self._send_yaw_rate_setpoint(0.0)
            self._restore_mode()
        return converged

    def _restore_mode(self) -> None:
        """Put the autopilot back where it was, if it was somewhere else."""
        previous = self._previous_mode
        if previous is None or self._mav is None:
            return
        if self._current_mode() == previous:
            return
        from pymavlink import mavutil

        try:
            self._mav.mav.command_long_send(
                self._mav.target_system,
                self._mav.target_component,
                mavutil.mavlink.MAV_CMD_DO_SET_MODE,
                0,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                float(previous),
                0.0, 0.0, 0.0, 0.0, 0.0,
            )
        except Exception as exc:
            logger.debug("yaw link: restore mode failed: %s", exc)
