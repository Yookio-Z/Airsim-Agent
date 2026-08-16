"""
AirSimController - 基于 AirSim RPC 的飞行控制实现
纯仿真模式，不需要 PX4 / MAVLink

线程安全设计：
  - _rpc_exec_lock: 全局 RPC 执行锁，确保同一时间只有一个 AirSim RPC 调用在执行
  - capture_image: 使用独立线程 + join(timeout) 模式，避免 executor 阻塞
  - 飞行命令: 使用 Async().join() 在 executor 中等待完成，不再用 time.sleep() 估算
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


class _RpcProxy:
    """AirSim Client 的线程隔离代理（带执行锁 + 超时保护）。"""

    HEAVY_METHODS = {"simGetImage", "simGetImages", "simGetPointCloud"}
    HEAVY_TIMEOUT = 15.0
    DEFAULT_TIMEOUT = 10.0

    def __init__(
        self,
        executor: ThreadPoolExecutor,
        real_client: airsim.MultirotorClient,
        rpc_exec_lock: threading.Lock,
    ) -> None:
        self._executor = executor
        self._real = real_client
        self._rpc_exec_lock = rpc_exec_lock

    def __getattr__(self, name: str) -> Any:
        real_method = getattr(self._real, name)

        def _wrapped(*args, **kwargs):
            timeout = self.HEAVY_TIMEOUT if name in self.HEAVY_METHODS else self.DEFAULT_TIMEOUT
            result_box = {"value": None, "error": None}

            def _call():
                acquired = self._rpc_exec_lock.acquire(timeout=20.0)
                if not acquired:
                    result_box["error"] = TimeoutError("RPC 执行锁获取超时(20s)")
                    return
                try:
                    result_box["value"] = real_method(*args, **kwargs)
                except Exception as e:
                    result_box["error"] = e
                finally:
                    self._rpc_exec_lock.release()

            future = self._executor.submit(_call)
            try:
                future.result(timeout=timeout + 20.0)
            except FutureTimeoutError:
                raise TimeoutError(f"AirSim RPC '{name}' 超时 ({timeout}s)")

            if isinstance(result_box["error"], Exception):
                raise result_box["error"]
            return result_box["value"]

        return _wrapped


class AirSimController(FlightController):
    """AirSim RPC 飞行控制后端（线程隔离版）。"""

    def __init__(self, ip: str = "127.0.0.1", port: int = 41452) -> None:
        self._ip = ip
        self._port = port
        self._client: Optional[airsim.MultirotorClient] = None
        self._connected = False
        self._vehicles: list[str] = []
        self._armed: set[str] = set()
        self._control_enabled: set[str] = set()
        self._settings_vehicle_types: dict[str, str] = self._load_settings_vehicle_types()
        self.last_error = ""

        # msgpackrpc.Client is thread-affine in practice: creating it in one
        # thread and calling it from another may time out. Keep a single RPC
        # worker that owns the client and all AirSim calls.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="airsim_rpc")
        self._rpc_exec_lock = threading.Lock()
        # External stop/cancel signal (emergency stop / task cancel). Polled
        # while blocking flight commands run so they can be preempted.
        self._stop_provider: Optional[Callable[[], bool]] = None

    # ------------------------------------------------------------------
    # 线程隔离 RPC 基础设施
    # ------------------------------------------------------------------

    def _rpc(self, fn, *args, timeout=10.0, **kwargs):
        """在独立线程中执行 AirSim RPC 调用（带执行锁 + 超时）。"""
        result_box = {"value": None, "error": None}

        def _call():
            acquired = self._rpc_exec_lock.acquire(timeout=20.0)
            if not acquired:
                result_box["error"] = TimeoutError("RPC 执行锁获取超时(20s)")
                return
            try:
                result_box["value"] = fn(*args, **kwargs)
            except Exception as e:
                result_box["error"] = e
            finally:
                self._rpc_exec_lock.release()

        future = self._executor.submit(_call)
        try:
            future.result(timeout=timeout + 20.0)
        except FutureTimeoutError:
            logger.warning(f"RPC 超时 ({timeout}s)")
            self._reset_rpc_runtime()
            raise TimeoutError(f"AirSim RPC 超时 ({timeout}s)")

        if isinstance(result_box["error"], Exception):
            raise result_box["error"]
        return result_box["value"]

    def _rpc_call(self, callable_obj, timeout=60.0):
        """在独立线程中执行无参 callable（带执行锁 + 超时）。"""
        result_box = {"value": None, "error": None}

        def _call():
            acquired = self._rpc_exec_lock.acquire(timeout=20.0)
            if not acquired:
                result_box["error"] = TimeoutError("RPC 执行锁获取超时(20s)")
                return
            try:
                result_box["value"] = callable_obj()
            except Exception as e:
                result_box["error"] = e
            finally:
                self._rpc_exec_lock.release()

        future = self._executor.submit(_call)
        try:
            future.result(timeout=timeout + 20.0)
        except FutureTimeoutError:
            logger.warning(f"RPC call 超时 ({timeout}s)")
            self._reset_rpc_runtime()
            raise TimeoutError(f"AirSim RPC 超时 ({timeout}s)")

        if isinstance(result_box["error"], Exception):
            raise result_box["error"]
        return result_box["value"]

    def rpc(self, method_name: str, *args, **kwargs):
        """在线程池中执行指定的 AirSim client 方法。"""
        if self._client is None:
            raise RuntimeError("AirSim not connected")
        method = getattr(self._client, method_name)
        return self._rpc(method, *args, **kwargs)

    def rpc_call(self, method_name: str, *args, **kwargs):
        """在线程池中执行方法并自动 .join()（用于 Async 方法）。"""
        if self._client is None:
            raise RuntimeError("AirSim not connected")
        method = getattr(self._client, method_name)
        return self._rpc_call(lambda: method(*args, **kwargs).join())

    # ------------------------------------------------------------------
    # 拍照
    # ------------------------------------------------------------------

    def capture_image(
        self,
        camera_name: str = "0",
        image_type: int = 0,
        vehicle_name: str = "",
        timeout: float = 15.0,
    ) -> Optional[bytes]:
        """用 simGetImages 拍照，返回 PNG bytes。"""
        if not self._ensure_connected():
            raise RuntimeError("AirSim not connected")

        request = airsim.ImageRequest(camera_name, image_type, False, True)
        try:
            responses = self._rpc(
                self._client.simGetImages,
                [request],
                timeout=timeout,
                vehicle_name=vehicle_name,
            )
            if responses and len(responses) > 0:
                img_data = responses[0].image_data_uint8
                if img_data and len(img_data) > 0:
                    return bytes(img_data)
            return None
        except TimeoutError:
            logger.warning(f"capture_image 超时 ({timeout}s)，标记连接不健康")
            raise
        except Exception as e:
            logger.warning(f"capture_image 错误: {e}")
            raise RuntimeError(f"capture_image 失败: {e}")

    def capture_image_with_retry(
        self,
        camera_name: str = "0",
        image_type: int = 0,
        vehicle_name: str = "",
        timeout: float = 15.0,
        max_retries: int = 3,
    ) -> Optional[bytes]:
        """拍照（带重试）。超时后等待并重连，再重试。"""
        last_error = None
        for attempt in range(max_retries):
            try:
                data = self.capture_image(camera_name, image_type, vehicle_name, timeout)
                if data and len(data) > 0:
                    return data
                last_error = "图像数据为空"
            except TimeoutError as e:
                last_error = str(e)
                logger.warning(f"capture_image 第{attempt+1}次超时: {e}")
                if attempt < max_retries - 1:
                    time.sleep(3.0)
                    self._ensure_connected()
            except Exception as e:
                last_error = str(e)
                logger.warning(f"capture_image 第{attempt+1}次失败: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2.0)
                    self._ensure_connected()

        raise TimeoutError(f"capture_image 重试{max_retries}次均失败: {last_error}")

    # ------------------------------------------------------------------
    # 等待无人机静止
    # ------------------------------------------------------------------

    def wait_until_stationary(
        self,
        vehicle_name: str = "",
        threshold: float = 0.3,
        max_wait: float = 10.0,
        check_interval: float = 0.5,
    ) -> bool:
        """等待无人机速度降到阈值以下。拍照前调用确保图像清晰。"""
        start = time.time()
        while time.time() - start < max_wait:
            try:
                state = self._rpc(self._client.getMultirotorState, vehicle_name)
                vel = state.kinematics_estimated.linear_velocity
                speed = (vel.x_val ** 2 + vel.y_val ** 2 + vel.z_val ** 2) ** 0.5
                if speed < threshold:
                    return True
            except Exception:
                pass
            time.sleep(check_interval)
        logger.warning(f"wait_until_stationary 超时 ({max_wait}s)")
        return False

    # ------------------------------------------------------------------
    # 健康检查 & 重连
    # ------------------------------------------------------------------

    def _ping_check(self, timeout: float = 3.0) -> bool:
        """快速检查 AirSim 连接是否存活。"""
        if self._client is None:
            return False
        try:
            return bool(self._rpc(self._client.ping, timeout=timeout))
        except Exception:
            return False

    def _force_reconnect(self) -> bool:
        """强制重连：新锁 + 新 client + 新 executor。"""
        logger.warning("强制重连 AirSim...")
        self._connected = False
        self._client = None
        self._armed.clear()
        self._control_enabled.clear()
        self._reset_rpc_runtime()
        try:
            info = self.connect(ip=self._ip, port=self._port)
            return info.connected
        except Exception:
            return False

    def _reset_rpc_runtime(self) -> None:
        """Replace RPC lock/executor after a stuck AirSim call.

        AirSim/msgpack calls cannot always be cancelled safely once blocked.
        Swapping the lock and executor lets future commands proceed instead of
        waiting forever behind a stale lock.
        """
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        self._connected = False
        self._client = None
        self._rpc_exec_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="airsim_rpc")

    # ------------------------------------------------------------------
    # 重置
    # ------------------------------------------------------------------

    def reset_vehicle(self, vehicle_name: str = "") -> bool:
        """重置无人机到初始状态。reset() 后必须重新 enableApiControl + arm。"""
        if not self._ensure_connected():
            return False
        try:
            self._rpc(self._client.reset)
            self._armed.discard(vehicle_name)
            self._control_enabled.discard(vehicle_name)
            time.sleep(1.0)
            self._ensure_control(vehicle_name)
            return True
        except Exception as e:
            logger.error(f"reset_vehicle failed: {e}")
            return False

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def backend_name(self) -> str:
        return "airsim"

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    @property
    def client(self) -> _RpcProxy:
        """返回线程隔离代理对象。"""
        if self._client is None:
            raise RuntimeError("AirSim not connected")
        return _RpcProxy(self._executor, self._client, self._rpc_exec_lock)

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def connect(self, **kwargs) -> ConnectionInfo:
        ip = kwargs.get("ip", self._ip)
        port = kwargs.get("port", self._port)
        self._ip = ip
        self._port = port

        if self.is_connected:
            try:
                self._rpc(self._client.ping, timeout=3.0)
                return ConnectionInfo(
                    backend="airsim",
                    connected=True,
                    details={
                        "vehicles": self._vehicles,
                        "message": "already connected",
                        "vehicle_types": self._settings_vehicle_types,
                        "external_flight_controller": self._uses_external_px4_controller(),
                    },
                )
            except Exception:
                self._connected = False
                self._client = None

        try:
            # Always start a fresh RPC runtime for a new connection attempt. A
            # previous timeout may have left msgpackrpc blocked in another
            # thread holding the old lock/client.
            self._reset_rpc_runtime()

            def _connect_call():
                client = airsim.MultirotorClient(ip=ip, port=port, timeout_value=5)
                if not client.ping():
                    raise TimeoutError("AirSim ping returned false")
                vehicles: list[str] = []
                for _ in range(3):
                    vehicles = client.listVehicles()
                    if vehicles:
                        break
                    time.sleep(0.5)
                return {
                    "client": client,
                    "server_version": client.getServerVersion(),
                    "client_version": client.getClientVersion(),
                    "vehicles": vehicles,
                }

            future = self._executor.submit(_connect_call)
            try:
                connected = future.result(timeout=12.0)
            except FutureTimeoutError:
                self._reset_rpc_runtime()
                raise TimeoutError("AirSim connect timed out")

            self._client = connected["client"]
            self._vehicles = connected["vehicles"]
            self._settings_vehicle_types = self._load_settings_vehicle_types()
            server_version = connected["server_version"]
            client_version = connected["client_version"]

            self._connected = True
            return ConnectionInfo(
                backend="airsim",
                connected=True,
                details={
                    "ip": ip,
                    "port": port,
                    "vehicles": self._vehicles,
                    "vehicle_count": len(self._vehicles),
                    "vehicle_types": self._settings_vehicle_types,
                    "external_flight_controller": self._uses_external_px4_controller(),
                    "server_version": server_version,
                    "client_version": client_version,
                },
            )
        except Exception as e:
            self._client = None
            self._connected = False
            return ConnectionInfo(
                backend="airsim",
                connected=False,
                details={"message": str(e)},
            )

    def _ensure_connected(self) -> bool:
        """如果未连接或连接不健康，自动重连。"""
        if self.is_connected:
            if self._ping_check(3.0):
                return True
            logger.warning("_ensure_connected: 连接不健康，重连...")
            self._connected = False
            self._client = None
        try:
            info = self.connect(ip=self._ip, port=self._port)
            return info.connected
        except Exception:
            return False

    def disconnect(self) -> None:
        if not self.is_connected:
            return
        for v in list(self._armed):
            try:
                self._rpc(self._client.armDisarm, False, v)
            except Exception:
                pass
        for v in list(self._control_enabled):
            try:
                self._rpc(self._client.enableApiControl, False, v)
            except Exception:
                pass
        self._armed.clear()
        self._control_enabled.clear()
        self._client = None
        self._vehicles = []
        self._connected = False

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _ensure_control(self, vehicle_name: str = "") -> None:
        self._ensure_internal_airsim_flight_control(vehicle_name)
        names = self._resolve_vehicles(vehicle_name)
        for name in names:
            if name not in self._control_enabled:
                self._rpc(self._client.enableApiControl, True, name)
                time.sleep(0.3)
                self._control_enabled.add(name)
            if name not in self._armed:
                self._rpc(self._client.armDisarm, True, name)
                self._armed.add(name)

    def _resolve_vehicles(self, vehicle_name: str = "") -> list[str]:
        if vehicle_name:
            return [vehicle_name]
        if self._vehicles:
            return list(self._vehicles)
        # AirSim controls the default single vehicle with an empty vehicle name
        # when settings.json does not declare a named Vehicles entry.
        return [""]

    def _ensure_internal_airsim_flight_control(self, vehicle_name: str = "") -> None:
        if not self._uses_external_px4_controller(vehicle_name):
            return
        self.last_error = (
            "AirSim settings use PX4Multirotor, so AirSim RPC is not the authoritative flight controller. "
            "Use PX4 MAVLink or PX4 ROS2 mode for arm, takeoff, landing, and motion, or switch AirSim "
            "settings to SimpleFlight and restart Unreal/AirSim for pure AirSim control."
        )
        raise RuntimeError(self.last_error)

    def _uses_external_px4_controller(self, vehicle_name: str = "") -> bool:
        types = self._settings_vehicle_types or {}
        if not types:
            return False
        names = [vehicle_name] if vehicle_name else (self._vehicles or list(types))
        if not names or names == [""]:
            return any(value == "px4multirotor" for value in types.values())
        return any(types.get(str(name), "").lower() == "px4multirotor" for name in names)

    def _load_settings_vehicle_types(self) -> dict[str, str]:
        path = self._airsim_settings_path()
        if path is None or not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        vehicles = data.get("Vehicles")
        if not isinstance(vehicles, dict):
            return {}
        result: dict[str, str] = {}
        for name, spec in vehicles.items():
            if not isinstance(spec, dict):
                continue
            vehicle_type = str(spec.get("VehicleType") or "").strip().lower()
            if vehicle_type:
                result[str(name)] = vehicle_type
        return result

    def _airsim_settings_path(self) -> Path | None:
        explicit = os.environ.get("AIRSIM_SETTINGS_PATH")
        if explicit:
            return Path(explicit)
        home = Path(os.environ.get("USERPROFILE") or str(Path.home()))
        return home / "Documents" / "AirSim" / "settings.json"

    # ------------------------------------------------------------------
    # 飞行控制 — 使用 Async().join() 等待完成
    # ------------------------------------------------------------------

    def arm(self, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            self._ensure_control(vehicle_name)
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"arm failed: {e}")
            return False

    def disarm(self, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        if self._uses_external_px4_controller(vehicle_name):
            self.last_error = "AirSim settings use PX4Multirotor; use PX4 MAVLink or PX4 ROS2 mode for disarm."
            return False
        try:
            names = self._resolve_vehicles(vehicle_name)
            for name in names:
                self._rpc(self._client.armDisarm, False, name)
                self._armed.discard(name)
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"disarm failed: {e}")
            return False

    def takeoff(self, altitude: float = 3.0, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            altitude = max(0.5, abs(float(altitude)))
            self._ensure_control(vehicle_name)
            names = self._resolve_vehicles(vehicle_name)
            for name in names:
                self._rpc_call(
                    lambda n=name: self._client.takeoffAsync(
                        timeout_sec=max(30, altitude * 5), vehicle_name=n
                    ).join(),
                    timeout=max(30, altitude * 5) + 10.0,
                )
                self._rpc_call(
                    lambda n=name: self._client.moveToZAsync(
                        -altitude,
                        velocity=1.5,
                        timeout_sec=max(20, altitude * 6),
                        vehicle_name=n,
                    ).join(),
                    timeout=max(25, altitude * 6 + 8),
                )
                self._rpc_call(
                    lambda n=name: self._client.hoverAsync(vehicle_name=n).join(),
                    timeout=8.0,
                )
                if not self._wait_until_airborne_at_altitude(name, altitude, timeout=4.0):
                    self.last_error = f"AirSim takeoff command returned but vehicle did not reach {altitude:.1f}m"
                    return False
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"takeoff failed: {e}")
            return False

    def land(self, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        if self._uses_external_px4_controller(vehicle_name):
            self.last_error = "AirSim settings use PX4Multirotor; use PX4 MAVLink or PX4 ROS2 mode for landing."
            return False
        try:
            names = self._resolve_vehicles(vehicle_name)
            for name in names:
                if name not in self._control_enabled:
                    self._rpc(self._client.enableApiControl, True, name)
                    time.sleep(0.2)
                    self._control_enabled.add(name)
                self._rpc_call(
                    lambda n=name: self._client.landAsync(
                        timeout_sec=30, vehicle_name=n
                    ).join(),
                    timeout=35.0,
                )
                if not self._wait_until_landed(name, timeout=12.0):
                    logger.warning(f"landAsync returned but vehicle is still flying: {name}")
                    return False
                try:
                    self._rpc(self._client.armDisarm, False, name)
                    self._armed.discard(name)
                except Exception:
                    pass
            return True
        except Exception as e:
            logger.error(f"land failed: {e}")
            return False

    def _wait_until_landed(self, vehicle_name: str, timeout: float = 12.0) -> bool:
        deadline = time.time() + timeout
        last_state = None
        while time.time() < deadline:
            try:
                state = self._rpc(self._client.getMultirotorState, vehicle_name)
                last_state = state
                if state.landed_state == 0:
                    return True
            except Exception:
                pass
            time.sleep(0.4)
        if last_state is not None:
            logger.warning(f"wait_until_landed timeout: landed_state={getattr(last_state, 'landed_state', None)}")
        return False

    def _wait_until_airborne_at_altitude(self, vehicle_name: str, altitude: float, timeout: float = 4.0) -> bool:
        deadline = time.time() + max(0.0, timeout)
        required_altitude = max(0.4, altitude * 0.75)
        while time.time() < deadline:
            try:
                state = self._rpc(self._client.getMultirotorState, vehicle_name)
                pos = state.kinematics_estimated.position
                current_altitude = max(0.0, -float(pos.z_val))
                if state.landed_state == 1 and current_altitude >= required_altitude:
                    return True
            except Exception:
                pass
            time.sleep(0.2)
        return False

    def hover(self, vehicle_name: str = "") -> bool:
        """Send hover to the vehicle(s) and return immediately.

        The hover command is fire-and-forget: AirSim executes it regardless of
        whether we wait, and not waiting keeps emergency-stop paths from queueing
        behind slow RPC calls (formation shutdown hovers every drone).
        """
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        if self._uses_external_px4_controller(vehicle_name):
            self.last_error = "AirSim settings use PX4Multirotor; use PX4 MAVLink or PX4 ROS2 mode for hover."
            return False
        try:
            names = self._resolve_vehicles(vehicle_name)
            for name in names:
                self._rpc(
                    lambda n=name: self._client.hoverAsync(vehicle_name=n),
                )
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"hover failed: {e}")
            return False

    def move_to_position(self, x: float, y: float, z: float, velocity: float = 2.0, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            self._ensure_control(vehicle_name)
            names = self._resolve_vehicles(vehicle_name)
            dist = (x**2 + y**2 + z**2) ** 0.5
            flight_timeout = max(30, dist / max(velocity, 0.5) + 10.0)
            for name in names:
                task = self._rpc(
                    lambda n=name: self._client.moveToPositionAsync(
                        x, y, z, velocity,
                        timeout_sec=flight_timeout,
                        vehicle_name=n,
                    ),
                    timeout=15.0,
                )
                if not self._wait_async_interruptible(task, name, flight_timeout + 10.0):
                    return False
            time.sleep(1.0)
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"move_to_position failed: {e}")
            return False

    def move_on_path(self, waypoints: list[dict], velocity: float = 2.0, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            self._ensure_control(vehicle_name)
            names = self._resolve_vehicles(vehicle_name)
            airsim_path = [
                airsim.Vector3r(wp.get("x", 0), wp.get("y", 0), wp.get("z", 0))
                for wp in waypoints
            ]
            total_dist = sum(
                ((waypoints[i].get("x",0)-waypoints[i-1].get("x",0))**2 +
                 (waypoints[i].get("y",0)-waypoints[i-1].get("y",0))**2 +
                 (waypoints[i].get("z",0)-waypoints[i-1].get("z",0))**2) ** 0.5
                for i in range(1, len(waypoints))
            ) if len(waypoints) > 1 else 0
            flight_timeout = max(30, total_dist / max(velocity, 0.5) + 10.0)
            for name in names:
                task = self._rpc(
                    lambda n=name: self._client.moveOnPathAsync(
                        airsim_path, velocity,
                        timeout_sec=flight_timeout,
                        vehicle_name=n,
                    ),
                    timeout=15.0,
                )
                if not self._wait_async_interruptible(task, name, flight_timeout + 10.0):
                    return False
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"move_on_path failed: {e}")
            return False

    def _wait_async_interruptible(self, task, vehicle_name: str, timeout: float) -> bool:
        """Wait for an AirSim async task, preempting on external stop/cancel.

        The task's ``join()`` must run on the single RPC worker (thread-affine
        client), so it is submitted there and the caller polls for completion
        and for the stop provider. On stop, a fire-and-forget hover preempts
        the running move in the simulator and False is returned immediately;
        the worker thread exits on its own once the simulator completes the
        task (bounded by the task's own timeout_sec)."""
        result_box: dict[str, Any] = {}

        def _wait() -> None:
            try:
                task.join()
                result_box["done"] = True
            except Exception as exc:
                result_box["error"] = exc

        self._executor.submit(_wait)
        deadline = time.time() + max(1.0, float(timeout))
        while time.time() < deadline:
            if self._stop_requested():
                # hoverAsync is a send-only call; AirSim preempts the running
                # move with the hover command. Best-effort: the RPC worker is
                # busy joining the task, so we cannot route through _rpc.
                try:
                    self._client.hoverAsync(vehicle_name=vehicle_name)
                except Exception as exc:
                    self.last_error = str(exc)
                self.last_error = "interrupted by emergency stop / cancel"
                return False
            if "done" in result_box:
                return True
            if result_box.get("error") is not None:
                raise result_box["error"]
            time.sleep(0.02)
        self._reset_rpc_runtime()
        raise TimeoutError(f"AirSim task timed out after {timeout:.0f}s")

    def set_stop_provider(self, stop_provider: Callable[[], bool] | None = None) -> None:
        """Wire an external stop/cancel signal into blocking flight commands."""
        self._stop_provider = stop_provider

    def _stop_requested(self) -> bool:
        provider = getattr(self, "_stop_provider", None)
        try:
            return bool(provider and provider())
        except Exception:
            return False

    def move_by_velocity(self, vx: float, vy: float, vz: float, duration: float = 0.0, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            self._ensure_control(vehicle_name)
            names = self._resolve_vehicles(vehicle_name)
            for name in names:
                self._rpc(self._client.moveByVelocityAsync, vx, vy, vz, duration or 0.1, vehicle_name=name)
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"move_by_velocity failed: {e}")
            return False

    def move_on_path(self, waypoints: list[dict], velocity: float = 2.0, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            self._ensure_control(vehicle_name)
            names = self._resolve_vehicles(vehicle_name)
            airsim_path = [
                airsim.Vector3r(wp.get("x", 0), wp.get("y", 0), wp.get("z", 0))
                for wp in waypoints
            ]
            total_dist = sum(
                ((waypoints[i].get("x",0)-waypoints[i-1].get("x",0))**2 +
                 (waypoints[i].get("y",0)-waypoints[i-1].get("y",0))**2 +
                 (waypoints[i].get("z",0)-waypoints[i-1].get("z",0))**2) ** 0.5
                for i in range(1, len(waypoints))
            ) if len(waypoints) > 1 else 0
            flight_timeout = max(30, total_dist / max(velocity, 0.5) + 10.0)
            for name in names:
                self._rpc_call(
                    lambda n=name: self._client.moveOnPathAsync(
                        airsim_path, velocity,
                        timeout_sec=flight_timeout,
                        vehicle_name=n,
                    ).join(),
                    timeout=flight_timeout + 10.0,
                )
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"move_on_path failed: {e}")
            return False

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_status(self, vehicle_name: str = "") -> DroneStatus:
        if not self._ensure_connected():
            return DroneStatus(extra={"connection_error": "AirSim not connected"})

        names = self._resolve_vehicles(vehicle_name)
        if not names:
            return DroneStatus()

        name = names[0]
        try:
            state = self._rpc(self._client.getMultirotorState, name)
            pos = state.kinematics_estimated.position
            vel = state.kinematics_estimated.linear_velocity
            ori = state.kinematics_estimated.orientation
            landed = state.landed_state

            position = {"x": round(pos.x_val, 3), "y": round(pos.y_val, 3), "z": round(pos.z_val, 3)}
            world_position = None
            try:
                world_pose = self._rpc(self._client.simGetObjectPose, name)
                wpos = world_pose.position
                world_position = {"x": round(wpos.x_val, 3), "y": round(wpos.y_val, 3), "z": round(wpos.z_val, 3)}
            except Exception:
                pass

            _, pitch, yaw = self._quat_to_euler(ori)
            heading_deg = math.degrees(yaw)
            if heading_deg < 0:
                heading_deg += 360.0
            extra = {
                "heading_deg": round(heading_deg, 1),
                "landed_state": "flying" if landed == 1 else "landed",
                "has_collided": getattr(state, 'collision', None) and state.collision.has_collided,
                "api_control_enabled": name in self._control_enabled,
                "vehicle_types": self._settings_vehicle_types,
                "external_flight_controller": self._uses_external_px4_controller(name),
            }
            if world_position is not None:
                extra["world_position"] = world_position

            return DroneStatus(
                position_ned=position,
                velocity_ned={
                    "vx": round(vel.x_val, 3),
                    "vy": round(vel.y_val, 3),
                    "vz": round(vel.z_val, 3),
                },
                attitude_rad={
                    "roll": round(math.atan2(2 * (ori.w_val * ori.x_val + ori.y_val * ori.z_val),
                                             1 - 2 * (ori.x_val ** 2 + ori.y_val ** 2)), 4),
                    "pitch": round(math.asin(max(-1, min(1, 2 * (ori.w_val * ori.y_val - ori.z_val * ori.x_val)))), 4),
                    "yaw": round(yaw, 4),
                },
                armed=name in self._armed,
                flying=landed == 1,
                mode="airsim_simpleflight",
                extra=extra,
            )
        except Exception as e:
            logger.error(f"get_status failed: {e}")
            self._connected = False
            self._client = None
            return DroneStatus(extra={"connection_error": str(e)})

    def list_vehicles(self) -> list[str]:
        if not self._ensure_connected():
            return []
        try:
            self._vehicles = self._rpc(self._client.listVehicles)
            return self._vehicles or [""]
        except Exception:
            return self._vehicles or [""]

    def set_mode(self, mode: str, vehicle_name: str = "") -> bool:
        logger.warning("AirSim does not support flight modes")
        return False

    def rotate_to_heading(self, heading_deg: float, timeout: float = 30.0, vehicle_name: str = "") -> bool:
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        if self._uses_external_px4_controller(vehicle_name):
            self.last_error = "AirSim settings use PX4Multirotor; use PX4 MAVLink or PX4 ROS2 mode for yaw control."
            return False
        try:
            names = self._resolve_vehicles(vehicle_name)
            for name in names:
                self._rpc_call(
                    lambda n=name: self._client.rotateToYawAsync(
                        heading_deg, timeout_sec=30, vehicle_name=n
                    ).join(),
                    timeout=40.0,
                )
            return True
        except Exception as e:
            logger.error(f"rotate_to_heading failed: {e}")
            return False

    def get_heading(self, vehicle_name: str = "") -> float:
        if not self._ensure_connected():
            return 0.0
        try:
            names = self._resolve_vehicles(vehicle_name)
            name = names[0]
            state = self._rpc(self._client.getMultirotorState, vehicle_name=name)
            q = state.kinematics_estimated.orientation
            _, _, yaw_rad = self._quat_to_euler(q)
            yaw_deg = math.degrees(yaw_rad) % 360.0
            return round(yaw_deg, 1)
        except Exception as e:
            logger.error(f"get_heading failed: {e}")
            return 0.0

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _quat_to_euler(q) -> tuple:
        sinr_cosp = 2.0 * (q.w_val * q.x_val + q.y_val * q.z_val)
        cosr_cosp = 1.0 - 2.0 * (q.x_val ** 2 + q.y_val ** 2)
        roll = math.atan2(sinr_cosp, cosr_cosp)

        sinp = 2.0 * (q.w_val * q.y_val - q.z_val * q.x_val)
        pitch = math.asin(max(-1.0, min(1.0, sinp)))

        siny_cosp = 2.0 * (q.w_val * q.z_val + q.x_val * q.y_val)
        cosy_cosp = 1.0 - 2.0 * (q.y_val ** 2 + q.z_val ** 2)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        return roll, pitch, yaw

    def world_to_local(self, vehicle_name: str, x: float, y: float, z: float) -> tuple:
        try:
            offset = self.get_world_offset(vehicle_name)
            return (x - offset["dx"], y - offset["dy"], z - offset["dz"])
        except Exception:
            return (x, y, z)

    def get_world_offset(self, vehicle_name: str) -> dict:
        try:
            ned = self._rpc(self._client.getMultirotorState, vehicle_name).kinematics_estimated.position
            world = self._rpc(self._client.simGetObjectPose, vehicle_name).position
            return {"dx": world.x_val - ned.x_val, "dy": world.y_val - ned.y_val, "dz": world.z_val - ned.z_val}
        except Exception:
            return {"dx": 0, "dy": 0, "dz": 0}
