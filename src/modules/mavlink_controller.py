"""PX4/MAVLink flight controller backend implemented with pymavlink.

拆分为 mavlink_connect/telemetry/commands/params/mission 五个 Mixin 与 mavlink_utils 工具；本文件保留头部初始化副作用、掩码常量与组合类。"""
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

from .mavlink_autodiscovery import (
    discover_serial_mavlink_candidates,
    normalize_serial_baud,
)
from ..config import config
from ..logging_config import get_logger

logger = get_logger(__name__)

from .mavlink_utils import (
    _gps_offset_m,
    _gps_from_offset_m,
    _mission_command_for_item,
    _mission_type_for_command,
    _mission_params_for_command,
    _optional_float,
    _optional_int,
)
from .mavlink_connect import MavlinkConnectMixin
from .mavlink_telemetry import MavlinkTelemetryMixin
from .mavlink_commands import MavlinkCommandsMixin
from .mavlink_params import MavlinkParamsMixin
from .mavlink_mission import (
    MavlinkMissionMixin,
    _MASK_POSITION_ONLY,
    _MASK_VELOCITY_ONLY,
    _MASK_YAW_RATE_ONLY,
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



class MavlinkController(
    MavlinkConnectMixin,
    MavlinkTelemetryMixin,
    MavlinkCommandsMixin,
    MavlinkParamsMixin,
    MavlinkMissionMixin,
):
    """FlightController implementation for PX4 SITL or a MAVLink vehicle."""

    @property
    def backend_name(self) -> str:
        return "mavlink"

    # ── 多机分表（QGC 模式）：sysid → per-system 状态表 ──
