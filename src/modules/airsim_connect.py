"""连接与基础设施：RPC 通道、连接生命周期、车辆解析、GPS/NED 与返航点存储。

拆分自 airsim_controller.py（AirSimController 方法按职责迁移，行为不变）。
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


class AirSimConnectMixin:
    def __init__(self, ip: str = "127.0.0.1", port: int = 41452) -> None:
        self._ip = ip
        self._port = port
        self._client: Optional[airsim.MultirotorClient] = None
        self._connected = False
        self._vehicles: list[str] = []
        self._armed: set[str] = set()
        self._control_enabled: set[str] = set()
        self._settings_vehicle_types: dict[str, str] = self._load_settings_vehicle_types()
        # 每机出生点（UE 坐标，来自 settings.json）——返航点的权威来源
        self._settings_spawns: dict[str, tuple[float, float, float]] = self._load_settings_spawns()
        # UE→NED 标定偏移：用任一在地面飞机的 GPS 反推（多机取平均，±5mm 一致性）
        self._ue_ned_offset: tuple[float, float] | None = None
        self._ue_offset_samples: list[tuple[float, float]] = []
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

    def _load_settings_spawns(self) -> dict[str, tuple[float, float, float]]:
        """读取 settings.json 中每机的出生点（UE X/Y/Z）。"""
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
        result: dict[str, tuple[float, float, float]] = {}
        for name, spec in vehicles.items():
            if not isinstance(spec, dict):
                continue
            try:
                result[str(name)] = (
                    float(spec.get("X", 0) or 0),
                    float(spec.get("Y", 0) or 0),
                    float(spec.get("Z", 0) or 0),
                )
            except (TypeError, ValueError):
                continue
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

    def ned_to_gps(self, x: float, y: float, z: float = 0.0) -> dict[str, float] | None:
        """AirSim NED(米) → GPS(度)，gps_to_ned 的逆变换（平面近似）。

        本机 AirSim 版本多机的 getGpsData 读数 = 真值位置 + 各机出生点偏移
        （已用 simGetGroundTruthKinematics 三方验证），不能直接用作地图定位；
        对外上报的 GPS 统一由真值 kinematics 反推，保证与地图/航点同帧。
        """
        if self._origin_geopoint is None:
            return None
        lat0, lon0, alt0 = self._origin_geopoint
        R = 6371000.0
        lat = lat0 + math.degrees(float(x) / R)
        lon = lon0 + math.degrees(float(y) / (R * math.cos(math.radians(lat0))))
        alt = alt0 - float(z)
        return {"lat": round(lat, 7), "lon": round(lon, 7), "alt": round(alt, 2)}

    def _home_store_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "data" / "vehicle_homes.json"

    def _load_home_store(self) -> None:
        """进程启动后首次读取磁盘上的返航点（跨重启保留）。"""
        if type(self)._home_store_loaded:
            return
        type(self)._home_store_loaded = True
        try:
            path = self._home_store_path()
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for name, ned in data.items():
                        if isinstance(ned, dict) and name and name not in type(self)._shared_home_positions:
                            type(self)._shared_home_positions[str(name)] = {
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
            path.write_text(json.dumps(type(self)._shared_home_positions, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"home store save failed: {e}")

    def _record_home_position(self, name: str, status: dict, landed_raw: int = 0) -> None:
        """用真实触地飞机(模拟器 landed_state==0)的 GPS 标定 UE→NED 偏移与地面高度。

        返航点 = settings.json 出生点(UE) + 标定偏移，与"首次落地位置"彻底脱钩：
        服务重启、飞机悬停在外，出生点都由配置权威决定，不会污染。
        悬停中的飞机绝不参与标定（其 z 不能当地面）。
        同时用 kinematics z 标定地面高度（触地判定/起飞 AGL 用，见 _ground_z_kin）。
        """
        if landed_raw != 0:
            return
        gps = status.get("gps")
        if not isinstance(gps, dict):
            return
        spawn = self._settings_spawns.get(name)
        if spawn is None:
            return
        try:
            ned = self.gps_to_ned(float(gps.get("lat")), float(gps.get("lon")), float(gps.get("alt", 0.0) or 0.0))
        except (TypeError, ValueError):
            return
        if ned is None:
            return
        # 该机停在出生点附近（距理论出生点 < 1m）才参与偏移标定，排除已移动的机；
        # 三机样本一致性 ±5mm，位移的机（哪怕整体平移）会被拒绝
        dx = ned["x"] - spawn[0]
        dy = ned["y"] - spawn[1]
        if math.hypot(dx, dy) > 1.0:
            # kinematics 地面标定独立放宽到 5m：返航降落会偏离 +1.5m，
            # 过严会导致无人机的 landed_state==0 永远标定不上（地面 z 丢失）
            self._record_kin_ground_z(status)
            return
        self._record_kin_ground_z(status)
        if self._ue_ned_offset is None or len(self._ue_offset_samples) < 5:
            self._ue_offset_samples.append((dx, dy, ned["z"]))
            self._ue_ned_offset = (
                sum(s[0] for s in self._ue_offset_samples) / len(self._ue_offset_samples),
                sum(s[1] for s in self._ue_offset_samples) / len(self._ue_offset_samples),
            )
            # 地面 NED z（该环境原点可能高于地面 3~6m，绝不能假设 z=0 是地面）
            self._ue_ned_ground_z = sum(s[2] for s in self._ue_offset_samples) / len(self._ue_offset_samples)
        # kinematics 帧地面高度：与 GPS 帧存在 ~2m 系统偏差，必须单独标定
        # （采样统一走 _record_kin_ground_z，见上方近出生点门控处）
        pass

    def _record_kin_ground_z(self, status: dict) -> None:
        kin_pos = status.get("position_ned") if isinstance(status.get("position_ned"), dict) else {}
        kin_z = kin_pos.get("z")
        if kin_z is None or not math.isfinite(float(kin_z)) or abs(float(kin_z)) > 200.0:
            return
        if len(type(self)._ground_z_kin_samples) < 5:
            type(self)._ground_z_kin_samples.append(float(kin_z))
        type(self)._ground_z_kin = (
            sum(type(self)._ground_z_kin_samples) / len(type(self)._ground_z_kin_samples)
            if type(self)._ground_z_kin_samples
            else None
        )

    def _ground_z_kin_value(self) -> float | None:
        """地面高度（kinematics 帧），未标定时返回 None。"""
        return type(self)._ground_z_kin

    def ground_z_kin(self) -> float | None:
        """对外暴露：kinematics 帧地面高度（runtime 触地判定/AGL 计算用）。"""
        return type(self)._ground_z_kin

    def _spawn_home_ned(self, name: str) -> dict[str, float] | None:
        """由出生点 + 标定偏移推导该机返航点（NED）。"""
        if self._ue_ned_offset is None:
            return None
        spawn = self._settings_spawns.get(name)
        if spawn is None:
            return None
        ground_z = self._ue_ned_ground_z if self._ue_ned_ground_z is not None else 0.0
        return {
            "x": round(spawn[0] + self._ue_ned_offset[0], 3),
            "y": round(spawn[1] + self._ue_ned_offset[1], 3),
            "z": round(ground_z, 3),
        }

    def home_position(self, vehicle_name: str = "") -> dict[str, float] | None:
        """返回某机的初始点位（NED），即该机的返航点。

        优先用出生点标定推导（权威）；无标定/无出生点配置时回退磁盘记录。
        """
        self._load_home_store()
        names = self._resolve_vehicles(vehicle_name)
        if not names:
            return None
        derived = self._spawn_home_ned(names[0])
        if derived is not None:
            return derived
        return type(self)._shared_home_positions.get(names[0])

    def _airsim_settings_path(self) -> Path | None:
        explicit = os.environ.get("AIRSIM_SETTINGS_PATH")
        if explicit:
            return Path(explicit)
        home = Path(os.environ.get("USERPROFILE") or str(Path.home()))
        return home / "Documents" / "AirSim" / "settings.json"

    # ------------------------------------------------------------------
    # 飞行控制 — 派发 Async 指令 + 遥测轮询等待到位
    # ------------------------------------------------------------------
