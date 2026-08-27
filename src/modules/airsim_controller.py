"""
AirSimController - 基于 AirSim RPC 的飞行控制实现
纯仿真模式，不需要 PX4 / MAVLink
（连接层拆至 airsim_connect.py，飞行层拆至 airsim_flight.py，RPC 代理拆至 airsim_rpc.py）
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Optional, Any, Callable

import airsim

from .flight_controller import FlightController, DroneStatus, ConnectionInfo
from ..logging_config import get_logger

logger = get_logger(__name__)


from .airsim_rpc import _RpcProxy
from .airsim_connect import AirSimConnectMixin
from .airsim_flight import AirSimFlightMixin


class AirSimController(AirSimConnectMixin, AirSimFlightMixin):
    """AirSim RPC 飞行控制后端（线程隔离版）。"""

    # 跨重连保留的按车状态（类级存储）：控制器实例在 reconnect 时会重建，
    # 空中飞机的返航点/派发跟踪不能跟着丢。返航点另存磁盘，进程重启也不丢。
    _shared_home_positions: dict[str, dict[str, float]] = {}
    _shared_dispatched_paths: dict[str, dict[str, Any]] = {}
    _home_store_loaded = False
    _ue_ned_ground_z: float | None = None
    # 地面高度（kinematics 帧）：本机 AirSim 版本中 GPS 反推 z 与
    # getMultirotorState 的 kinematics z 存在 ~2m 系统偏差，触地判定、
    # 起飞 AGL 必须用 kinematics 帧的地面标定值。
    _ground_z_kin: float | None = None
    _ground_z_kin_samples: list[float] = []
