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

    # 跨重连保留的按车状态（类级存储）：控制器实例在 reconnect 时会重建，
    # 空中飞机的返航点/派发跟踪不能跟着丢。返航点另存磁盘，进程重启也不丢。
    _shared_home_positions: dict[str, dict[str, float]] = {}
    _shared_dispatched_paths: dict[str, dict[str, Any]] = {}
    _home_store_loaded = False

    def __init__(self, ip: str = "127.0.0.1", port: int = 41452) -> None:
        self._ip = ip
        self._port = port
        self._client: Optional[airsim.MultirotorClient] = None
        self._connected = False
        self._vehicles: list[str] = []
        self._armed: set[str] = set()
        self._control_enabled: set[str] = set()
        self._settings_vehicle_types: dict[str, str] = self._load_settings_vehicle_types()
        # 每机返航点与派发航线跟踪使用类级存储（跨重连保留），见类定义处。
        self._origin_geopoint: tuple[float, float, float] | None = self._load_origin_geopoint()
        self.last_error = ""

        # msgpackrpc.Client is thread-affine in practice: creating it in one
        # thread and calling it from another may time out. Keep a single RPC
        # worker that owns the client and all AirSim calls.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="airsim_rpc")
        self._rpc_exec_lock = threading.Lock()
        self._last_connected_check = 0.0
        # land 成功后记录落地事实（AirSim 落地后遥测滞后/位置残留，见 get_status）
        self._landed_vehicles: set[str] = set()
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
            # The ping probe is an RPC: under the UI's 250ms polling × N
            # requests, pinging on every call piles up pressure on the single
            # RPC worker (and a stuck ping blocks everything until timeout).
            # Once verified healthy, skip probing for 5 seconds.
            now = time.time()
            if now - self._last_connected_check < 5.0:
                return True
            self._last_connected_check = now
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
            # enableApiControl / armDisarm 必须每次都重发（幂等指令）：
            # 模拟器侧可能在降落循环后丢失 API 控制权（"entering hover mode
            # for safety"），内存集合认为已使能会导致
            # "Vehicle cannot be armed via API because API has not been given control"，
            # 起飞指令被模拟器无视（表现为部分飞机不起飞）。
            self._rpc(self._client.enableApiControl, True, name)
            self._control_enabled.add(name)
            self._rpc(self._client.armDisarm, True, name)
            self._armed.add(name)
            time.sleep(0.3)
            # 解锁即离开落地确认状态（takeoff/land 的遥测重新可信）
            self._landed_vehicles.discard(name)

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

    def _load_origin_geopoint(self) -> tuple[float, float, float] | None:
        """读取 settings.json 的 OriginGeopoint（NED 原点经纬度），用于 GPS↔NED 换算。"""
        path = self._airsim_settings_path()
        if path is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        origin = data.get("OriginGeopoint")
        if not isinstance(origin, dict):
            return None
        try:
            lat = float(origin.get("Latitude"))
            lon = float(origin.get("Longitude"))
            alt = float(origin.get("Altitude", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        if abs(lat) < 0.001 or abs(lon) < 0.001:
            return None
        return (lat, lon, alt)

    def gps_to_ned(self, lat: float, lon: float, alt: float = 0.0) -> dict[str, float] | None:
        """GPS(度) → AirSim NED(米)，与 OriginGeopoint 对齐（平面近似）。"""
        if self._origin_geopoint is None:
            return None
        lat0, lon0, alt0 = self._origin_geopoint
        R = 6371000.0
        north = math.radians(lat - lat0) * R
        east = math.radians(lon - lon0) * R * math.cos(math.radians(lat0))
        down = -(float(alt) - alt0)
        return {"x": round(north, 3), "y": round(east, 3), "z": round(down, 3)}

    def _home_store_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "vehicle_homes.json"

    def _load_home_store(self) -> None:
        """进程启动后首次读取磁盘上的返航点（跨重启保留）。"""
        if AirSimController._home_store_loaded:
            return
        AirSimController._home_store_loaded = True
        try:
            path = self._home_store_path()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for name, ned in data.items():
                        if isinstance(ned, dict) and name and name not in AirSimController._shared_home_positions:
                            AirSimController._shared_home_positions[str(name)] = {
                                "x": float(ned.get("x", 0.0)),
                                "y": float(ned.get("y", 0.0)),
                                "z": float(ned.get("z", 0.0)),
                            }
        except Exception as e:
            logger.warning(f"home store load failed: {e}")

    def _save_home_store(self) -> None:
        try:
            path = self._home_store_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(AirSimController._shared_home_positions, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"home store save failed: {e}")

    def _record_home_position(self, name: str, status: dict) -> None:
        """未飞行时记录该机的初始位置（仅首次），作为其专属返航点。

        注意：部分 AirSim 版本地面状态下 kinematics_estimated 对多机返回同一读数，
        因此这里必须用按车正确的 GPS 换算，而不是 position_ned。
        已解锁但仍在地面的机同样记录——解锁瞬间正是"初始点位"。
        """
        self._load_home_store()
        if name in AirSimController._shared_home_positions:
            return
        if status.get("flying"):
            return
        gps = status.get("gps")
        if not isinstance(gps, dict):
            return
        try:
            ned = self.gps_to_ned(float(gps.get("lat")), float(gps.get("lon")), float(gps.get("alt", 0.0) or 0.0))
        except (TypeError, ValueError):
            return
        if ned is None:
            return
        AirSimController._shared_home_positions[name] = ned
        self._save_home_store()

    def home_position(self, vehicle_name: str = "") -> dict[str, float] | None:
        """返回某机的初始点位（NED），即该机的返航点。"""
        self._load_home_store()
        names = self._resolve_vehicles(vehicle_name)
        if not names:
            return None
        return AirSimController._shared_home_positions.get(names[0])

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
            # 多机并行：先把全部起飞/爬升任务发给模拟器（AirSim 并发执行），
            # 再统一等待到位——逐架 join 会把 N 架的起飞时间串行相加
            for name in names:
                self._rpc(
                    lambda n=name: self._client.takeoffAsync(
                        timeout_sec=max(30, altitude * 5), vehicle_name=n
                    ),
                    timeout=15.0,
                )
            for name in names:
                self._rpc(
                    lambda n=name: self._client.moveToZAsync(
                        -altitude,
                        velocity=1.5,
                        timeout_sec=max(20, altitude * 6),
                        vehicle_name=n,
                    ),
                    timeout=15.0,
                )
            for name in names:
                if not self._wait_until_airborne_at_altitude(name, altitude, timeout=max(30, altitude * 6)):
                    self.last_error = f"AirSim takeoff command returned but {name} did not reach {altitude:.1f}m"
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
                # 与 _ensure_control 相同：enableApiControl 每次重发（幂等），
                # 防止模拟器侧已丢失 API 控制权而内存集合未同步
                self._rpc(self._client.enableApiControl, True, name)
                time.sleep(0.2)
                self._control_enabled.add(name)
            # 唤醒：悬停超过看门狗窗口后 SimpleFlight 进入安全悬停态并忽略
            # 后续指令（"API call was not received"），先发 hover 重置看门狗
            for name in names:
                try:
                    self._rpc(lambda n=name: self._client.hoverAsync(vehicle_name=n), timeout=10.0)
                except Exception:
                    pass
                time.sleep(0.2)
            # 多机并行降落：先给全部车辆发出 landAsync（AirSim 并发执行），
            # 再统一轮询落地确认——逐架 join 会把 N 架的降落时间串行相加
            for name in names:
                self._rpc(
                    lambda n=name: self._client.landAsync(
                        timeout_sec=60, vehicle_name=n
                    ),
                    timeout=15.0,
                )
            for name in names:
                if not self._wait_until_landed(name, timeout=45.0):
                    logger.warning(f"landAsync returned but vehicle is still flying: {name}")
                    return False
                self._landed_vehicles.add(name)
                try:
                    self._rpc(self._client.armDisarm, False, name)
                    self._armed.discard(name)
                except Exception:
                    pass
            return True
        except Exception as e:
            logger.error(f"land failed: {e}")
            return False

    def _wait_until_landed(self, vehicle_name: str, timeout: float = 20.0) -> bool:
        deadline = time.time() + timeout
        last_state = None
        while time.time() < deadline:
            try:
                state = self._rpc(self._client.getMultirotorState, vehicle_name)
                last_state = state
                if state.landed_state == 0:
                    return True
                # Some AirSim releases lag the landed_state enum after a
                # successful touchdown; being on the ground by altitude is
                # equally safe and avoids a spurious "landing failed".
                pos = state.kinematics_estimated.position
                if -float(pos.z_val) < 0.4:
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

    # ------------------------------------------------------------------
    # 非阻塞派发（fire-and-forget）：多机同时起飞/执行各自航线用。
    # AirSim 的 Async 指令在模拟器侧执行，RPC 派发后立即返回，不 join。
    # ------------------------------------------------------------------

    def dispatch_takeoff(self, altitude: float, vehicle_name: str = "") -> bool:
        """派发起飞（不等待完成）。多机并发起飞的基础。

        注意：部分 AirSim 版本在"降落→解锁→再起飞"循环后 takeoffAsync 只翻转
        状态不实际爬升，因此与阻塞版 takeoff() 一致，补一段 moveToZ 真正爬升。
        """
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            altitude = max(0.5, abs(float(altitude)))
            names = self._resolve_vehicles(vehicle_name)
            for name in names:
                self._ensure_control(name)
                self._rpc(
                    lambda n=name: self._client.takeoffAsync(timeout_sec=max(30, altitude * 5), vehicle_name=n),
                    timeout=10.0,
                )
            # 实际爬升到目标高度（fire-and-forget）
            for name in names:
                self._rpc(
                    lambda n=name: self._client.moveToZAsync(
                        -altitude,
                        velocity=1.5,
                        timeout_sec=max(20, altitude * 6),
                        vehicle_name=n,
                    ),
                    timeout=10.0,
                )
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"dispatch_takeoff failed: {e}")
            return False

    def dispatch_move_on_path(self, waypoints: list[dict], velocity: float = 2.0, vehicle_name: str = "") -> bool:
        """派发航线（不等待完成）。每架机各自执行自己的路径。"""
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        if not waypoints:
            self.last_error = "waypoints is empty"
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
                self._rpc(
                    lambda n=name: self._client.moveOnPathAsync(
                        airsim_path, velocity,
                        timeout_sec=flight_timeout,
                        vehicle_name=n,
                    ),
                    timeout=10.0,
                )
                # 记录派发，供完成跟踪（update_flight_task_progress）使用
                AirSimController._shared_dispatched_paths[name] = {
                    "waypoints": [dict(wp) for wp in waypoints],
                    "velocity": float(velocity),
                    "dispatched_at": time.time(),
                    "done": False,
                    "near_count": 0,
                }
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"dispatch_move_on_path failed: {e}")
            return False

    def is_flying(self, vehicle_name: str = "") -> bool:
        status = self.get_status(vehicle_name)
        return bool(status.flying)

    def dispatch_land(self, vehicle_name: str = "") -> bool:
        """派发降落（fire-and-forget）。多机同时降落用，完成验证由调用方轮询。"""
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        try:
            names = self._resolve_vehicles(vehicle_name)
            # 唤醒看门狗（悬停久了安全悬停态会忽略 landAsync）
            for name in names:
                try:
                    self._rpc(lambda n=name: self._client.hoverAsync(vehicle_name=n), timeout=10.0)
                except Exception:
                    pass
                time.sleep(0.2)
            for name in names:
                self._rpc(
                    lambda n=name: self._client.landAsync(timeout_sec=60, vehicle_name=n),
                    timeout=10.0,
                )
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"dispatch_land failed: {e}")
            return False

    def dispatch_return_and_land(
        self, x: float, y: float, z: float, velocity: float = 3.0, vehicle_name: str = ""
    ) -> bool:
        """派发返航：先飞回 (x,y,z)，到位后自动降落并锁定（后台监视线程完成）。

        返航语义 = 到达初始点 + 降落 + 上锁，全程异步，不阻塞调用方。
        空中机先发 hover 唤醒（任务结束悬停久了 SimpleFlight 安全看门狗会进入
        "hover mode for safety"，忽略后续指令，hover 重置看门狗恢复控制）；
        地面待飞机先爬升再返航。
        """
        self.last_error = ""
        if not self._ensure_connected():
            self.last_error = "AirSim not connected"
            return False
        names = self._resolve_vehicles(vehicle_name)
        for name in names:
            self._ensure_control(name)
            try:
                status = self.get_status(name).to_dict()
            except Exception:
                status = {}
            path: list[dict[str, float]] = []
            if status.get("flying"):
                # 唤醒：重置 SimpleFlight 的 API 看门狗（悬停中发 hover 无副作用）
                try:
                    self._rpc(lambda n=name: self._client.hoverAsync(vehicle_name=n), timeout=10.0)
                    time.sleep(0.3)
                except Exception:
                    pass
                path.append({"x": float(x), "y": float(y), "z": float(z)})
            else:
                # 地面待飞机：从当前位置(GPS 换算，地面 kinematics 多机读数不可靠)
                # 先垂直爬升到巡航高度，再飞回初始点
                climb = {"x": float(x), "y": float(y), "z": min(float(z), -1.0)}
                try:
                    gnss = self._rpc(self._client.getGpsData, vehicle_name=name, timeout=4.0).gnss
                    geo = gnss.geo_point
                    cur = self.gps_to_ned(float(geo.latitude), float(geo.longitude), float(geo.altitude))
                    if cur:
                        climb = {"x": cur["x"], "y": cur["y"], "z": min(float(z), -1.0)}
                except Exception:
                    pass
                path.append(climb)
                path.append({"x": float(x), "y": float(y), "z": float(z)})
            if not self.dispatch_move_on_path(path, velocity, name):
                return False
            monitor = threading.Thread(
                target=self._return_land_monitor,
                args=(name, float(x), float(y), float(z), float(velocity)),
                daemon=True,
                name=f"return_land_{name}",
            )
            monitor.start()
        return True

    def _return_land_monitor(self, name: str, x: float, y: float, z: float, velocity: float = 3.0) -> None:
        """等待返航到位 → 降落 → 上锁。独立守护线程，单机超时 180s。

        若派发后 8 秒内完全没位移（安全看门狗吞掉了指令），自动重发一次返航路径。
        """
        deadline = time.time() + 180.0
        started = time.time()
        start_pos = None
        retried = False
        arrived = False
        while time.time() < deadline:
            if self._stop_requested():
                return
            try:
                status = self.get_status(name).to_dict()
                pos = status.get("position_ned") or {}
                vel = status.get("velocity_ned") or {}
                px = float(pos.get("x", 0.0) or 0.0)
                py = float(pos.get("y", 0.0) or 0.0)
                pz = float(pos.get("z", 0.0) or 0.0)
                dist = math.sqrt((px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2)
                speed = math.sqrt(
                    float(vel.get("vx", 0.0) or 0.0) ** 2
                    + float(vel.get("vy", 0.0) or 0.0) ** 2
                    + float(vel.get("vz", 0.0) or 0.0) ** 2
                )
                if start_pos is None:
                    start_pos = (px, py, pz)
                if dist < 1.2 and speed < 0.5:
                    arrived = True
                    break
                # 8 秒无位移 → 指令被安全看门狗吞掉，重发一次
                if not retried and time.time() - started > 8.0:
                    moved = math.sqrt(
                        (px - start_pos[0]) ** 2 + (py - start_pos[1]) ** 2 + (pz - start_pos[2]) ** 2
                    )
                    if moved < 0.5:
                        retried = True
                        logger.warning(f"return_land_monitor: {name} 无位移，重发返航路径")
                        self._ensure_control(name)
                        try:
                            self._rpc(lambda n=name: self._client.hoverAsync(vehicle_name=n), timeout=10.0)
                            time.sleep(0.3)
                        except Exception:
                            pass
                        self.dispatch_move_on_path([{"x": x, "y": y, "z": z}], velocity, name)
            except Exception:
                pass
            time.sleep(0.5)
        if not arrived:
            logger.warning(f"return_land_monitor: {name} 未在超时前到位，跳过自动降落")
            return
        try:
            time.sleep(0.5)
            if self.land(vehicle_name=name):
                self.disarm(vehicle_name=name)
                logger.info(f"return_land_monitor: {name} 已返航降落并锁定")
        except Exception as e:
            logger.error(f"return_land_monitor: {name} 降落/锁定失败: {e}")

    def update_flight_task_progress(self, vehicles: list[dict]) -> dict[str, dict[str, Any]]:
        """用最新逐车遥测更新已派发航线的执行状态（纯计算，无 RPC）。

        判定完成：连续多次距最后航点 < 1.2m 且速度接近 0（悬停到位）。
        返回 {vehicle_name: {state, waypoint_count, dist_to_target, speed}}。
        """
        for vehicle in vehicles or []:
            if not isinstance(vehicle, dict):
                continue
            name = str(vehicle.get("vehicle_name") or "")
            info = AirSimController._shared_dispatched_paths.get(name)
            if not info or info.get("done"):
                continue
            pos = vehicle.get("position_ned") if isinstance(vehicle.get("position_ned"), dict) else {}
            vel = vehicle.get("velocity_ned") if isinstance(vehicle.get("velocity_ned"), dict) else {}
            final = info["waypoints"][-1]
            dist = math.sqrt(
                (float(pos.get("x", 0.0) or 0.0) - float(final.get("x", 0.0))) ** 2
                + (float(pos.get("y", 0.0) or 0.0) - float(final.get("y", 0.0))) ** 2
                + (float(pos.get("z", 0.0) or 0.0) - float(final.get("z", 0.0))) ** 2
            )
            speed = math.sqrt(
                float(vel.get("vx", 0.0) or 0.0) ** 2
                + float(vel.get("vy", 0.0) or 0.0) ** 2
                + float(vel.get("vz", 0.0) or 0.0) ** 2
            )
            if dist < 1.2 and speed < 0.5:
                info["near_count"] = int(info.get("near_count", 0)) + 1
            else:
                info["near_count"] = 0
            # 飞机已落地(降落/被返航接管) → 路径任务自然结束,不留滞留记录
            grounded = (not vehicle.get("flying")) and (not vehicle.get("armed")) and speed < 0.5
            if info["near_count"] >= 3 or grounded:
                info["done"] = True
                info["done_at"] = time.time()
            info["last_dist"] = round(dist, 2)
            info["last_speed"] = round(speed, 2)

        return {
            name: {
                "state": "done" if info.get("done") else "flying",
                "waypoint_count": len(info["waypoints"]),
                "dist_to_target": info.get("last_dist"),
                "speed": info.get("last_speed"),
                "dispatched_at": info.get("dispatched_at"),
            }
            for name, info in AirSimController._shared_dispatched_paths.items()
        }

    def wait_until_flying(self, vehicle_name: str = "", timeout: float = 15.0) -> bool:
        """轮询等待某机进入飞行状态（起飞派发后确认用）。"""
        deadline = time.time() + max(1.0, float(timeout))
        while time.time() < deadline:
            if self._stop_requested():
                return False
            try:
                if self.is_flying(vehicle_name):
                    return True
            except Exception:
                pass
            time.sleep(0.4)
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
            # tight RPC timeout: get_status is called on every UI poll per
            # vehicle; a stuck AirSim response must not block the single RPC
            # worker for the default 30s recovery window on every poll
            state = self._rpc(self._client.getMultirotorState, name, timeout=4.0)
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

            # 多机注意：部分 AirSim 版本在地面未解锁状态下 getMultirotorState 的
            # kinematics_estimated 对所有车返回第一架车的读数（飞行中才恢复按车），
            # 因此地图定位必须用 getGpsData（全程按车正确，由 OriginGeopoint 换算）。
            gps_data = None
            try:
                gnss = self._rpc(self._client.getGpsData, vehicle_name=name, timeout=4.0).gnss
                geo = gnss.geo_point
                if geo and abs(float(geo.latitude)) > 0.001 and abs(float(geo.longitude)) > 0.001:
                    gps_data = {
                        "lat": round(float(geo.latitude), 7),
                        "lon": round(float(geo.longitude), 7),
                        "alt": round(float(geo.altitude), 2),
                    }
            except Exception:
                gps_data = None

            _, pitch, yaw = self._quat_to_euler(ori)
            heading_deg = math.degrees(yaw)
            if heading_deg < 0:
                heading_deg += 360.0
            # AirSim 落地后 getMultirotorState 的遥测不可靠（landed_state 可能
            # 滞后为 Flying、位置 z 残留甚至翻转）。land 成功后控制器记录落地
            # 事实，直到下一次 arm 前都按"已着陆"上报，保证状态自洽。
            landed_confirmed = name in getattr(self, "_landed_vehicles", set())
            speed = math.sqrt(vel.x_val ** 2 + vel.y_val ** 2 + vel.z_val ** 2)
            # 悬停时速度≈0 但 z 明显在原点上方——只看速度会把悬停误判成落地，
            # 导致返航被 "vehicle is not airborne" 拒绝。速度或高度任一在空即算飞行。
            airborne_by_altitude = pos.z_val < -0.5
            flying = (landed == 1) and not landed_confirmed and (speed > 0.3 or airborne_by_altitude)
            if landed_confirmed:
                position = {"x": float(position.get("x", 0.0) or 0.0), "y": float(position.get("y", 0.0) or 0.0), "z": 0.0}
            extra = {
                "heading_deg": round(heading_deg, 1),
                "landed_state": "flying" if flying else "landed",
                "has_collided": getattr(state, 'collision', None) and state.collision.has_collided,
                "api_control_enabled": name in self._control_enabled,
                "vehicle_types": self._settings_vehicle_types,
                "external_flight_controller": self._uses_external_px4_controller(name),
            }
            if world_position is not None:
                extra["world_position"] = world_position

            status_dict = DroneStatus(
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
                flying=flying,
                mode="airsim_simpleflight",
                gps=gps_data,
                extra=extra,
            )
            self._record_home_position(name, status_dict.to_dict())
            home = AirSimController._shared_home_positions.get(name)
            if home is not None:
                status_dict.extra["home_position_ned"] = dict(home)
            return status_dict
        except Exception as e:
            logger.error(f"get_status failed: {e}")
            self._connected = False
            self._client = None
            return DroneStatus(extra={"connection_error": str(e)})

    def list_vehicles(self, refresh: bool = False) -> list[str]:
        """Return the known vehicle list.

        Default reads the locally cached list populated at connect time —
        the vehicle composition does not change while AirSim runs, and a
        live RPC here would queue behind the single RPC worker on every
        250ms UI poll (a stuck AirSim response then blocks ALL telemetry
        until the 30s runtime reset, which shows up as the vehicle panel
        flickering). Pass ``refresh=True`` for a force re-read.
        """
        if refresh:
            if not self._ensure_connected():
                return self._vehicles or []
            try:
                fresh = self._rpc(self._client.listVehicles, timeout=5.0)
                if fresh:
                    self._vehicles = list(fresh)
            except Exception:
                pass
        if not self.is_connected:
            return []  # keep the pre-cache semantics for tool-layer callers
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
