"""
AirSim 连接管理器 - 维护与 AirSim 仿真器的持久连接
管理多无人机的连接状态、控制权限和生命周期
"""

from __future__ import annotations

import time
from typing import Optional

import airsim


class AirSimClientManager:
    _instance: Optional[AirSimClientManager] = None

    def __init__(self):
        self.client: Optional[airsim.MultirotorClient] = None
        self.vehicles: list[str] = []
        self._connected = False
        self._armed: set[str] = set()
        self._control_enabled: set[str] = set()
        self._ip = "127.0.0.1"
        self._port = 41451

    @classmethod
    def get(cls) -> AirSimClientManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_connected(self) -> bool:
        return self._connected and self.client is not None

    def connect(self, ip: str = "127.0.0.1", port: int = 41451) -> dict:
        if self.is_connected:
            return {
                "status": "already_connected",
                "vehicles": self.vehicles,
                "message": "已连接到 AirSim，无需重复连接",
            }

        try:
            self._ip = ip
            self._port = port
            self.client = airsim.MultirotorClient(ip=ip, port=port, timeout_value=10)

            connected_ok = False
            for attempt in range(3):
                try:
                    result = self.client.ping()
                    if result:
                        connected_ok = True
                        break
                except Exception:
                    pass
                time.sleep(1)

            if not connected_ok:
                self.client = None
                return {
                    "status": "error",
                    "vehicles": [],
                    "message": f"无法 ping 通 AirSim ({ip}:{port})。请确认 AirSim 仿真器已启动。",
                }

            self.vehicles = []
            for attempt in range(3):
                try:
                    self.vehicles = self.client.listVehicles()
                    if self.vehicles:
                        break
                except Exception:
                    pass
                if attempt < 2:
                    time.sleep(1)

            self._connected = True

            if not self.vehicles:
                return {
                    "status": "connected_no_vehicles",
                    "vehicles": [],
                    "vehicle_count": 0,
                    "message": f"已连接到 AirSim ({ip}:{port}) 但未发现无人机。场景可能未加载或无人机名称不匹配。",
                }

            return {
                "status": "connected",
                "vehicles": self.vehicles,
                "vehicle_count": len(self.vehicles),
                "message": f"成功连接到 AirSim ({ip}:{port})，发现 {len(self.vehicles)} 架无人机：{self.vehicles}",
            }
        except Exception as e:
            self.client = None
            self._connected = False
            return {
                "status": "error",
                "vehicles": [],
                "message": f"连接 AirSim 失败：{e}。请确保 AirSim 仿真器已启动并监听在 {ip}:{port}",
            }

    def disconnect(self) -> dict:
        if not self.is_connected:
            return {"status": "not_connected", "message": "当前未连接到 AirSim"}

        for v in list(self._armed):
            try:
                self.client.armDisarm(False, v)
            except Exception:
                pass
        for v in list(self._control_enabled):
            try:
                self.client.enableApiControl(False, v)
            except Exception:
                pass

        self._armed.clear()
        self._control_enabled.clear()
        self.client = None
        self.vehicles = []
        self._connected = False
        return {"status": "disconnected", "message": "已断开与 AirSim 的连接"}

    def ensure_connected(self) -> Optional[str]:
        if not self.is_connected:
            result = self.connect(self._ip, self._port)
            if result["status"] == "error":
                return result["message"]
        return None

    def ensure_control(self, vehicle_name: str = "", force: bool = False) -> Optional[str]:
        err = self.ensure_connected()
        if err:
            return err

        names = self._resolve_vehicles(vehicle_name)
        for name in names:
            # 检查当前飞行状态
            try:
                state = self.client.getMultirotorState(vehicle_name=name)
                is_flying = state.landed_state == 1  # 1 = flying, 0 = landed
            except Exception:
                is_flying = False
            
            # 如果无人机正在飞行，只确保 API 控制启用，不要 disable 再 enable（会导致降落）
            if is_flying and name in self._control_enabled:
                # 飞行中且已有控制，直接跳过
                if name not in self._armed:
                    try:
                        self.client.armDisarm(True, name)
                        self._armed.add(name)
                    except Exception as e:
                        return f"重新解锁 {name} 电机失败：{e}"
                continue
            
            # 地面状态或首次控制，需要重新获取
            try:
                # 先启用 API 控制
                self.client.enableApiControl(True, name)
                time.sleep(0.5)
                # 验证 API 控制是否真正启用
                api_enabled = self.client.isApiControlEnabled(name)
                if api_enabled:
                    self._control_enabled.add(name)
                else:
                    print(f"[警告] {name} API 控制状态为 False，但继续尝试...")
                    self._control_enabled.add(name)
            except Exception as e:
                return f"启用 {name} API 控制失败：{e}"
            
            if name not in self._armed:
                try:
                    self.client.armDisarm(True, name)
                    self._armed.add(name)
                except Exception as e:
                    return f"解锁 {name} 电机失败：{e}"
        return None

    def reset_control_state(self, vehicle_name: str = ""):
        """重置控制状态，强制下次重新获取控制"""
        if vehicle_name:
            self._control_enabled.discard(vehicle_name)
            self._armed.discard(vehicle_name)
        else:
            self._control_enabled.clear()
            self._armed.clear()

    def _resolve_vehicles(self, vehicle_name: str = "") -> list[str]:
        """解析无人机名称。如果指定了名称，尝试直接使用（不依赖缓存列表）。"""
        if vehicle_name:
            # 不检查缓存列表，直接返回指定名称
            # 这样即使 AirSim 重启后无人机名称变化也能工作
            return [vehicle_name]
        # 未指定名称时，返回缓存列表中的所有无人机
        return list(self.vehicles)

    def get_world_offset(self, vehicle_name: str) -> dict:
        ned = self.client.getMultirotorState(vehicle_name=vehicle_name).kinematics_estimated.position
        try:
            world = self.client.simGetObjectPose(vehicle_name).position
            return {"dx": world.x_val - ned.x_val, "dy": world.y_val - ned.y_val, "dz": world.z_val - ned.z_val}
        except Exception:
            return {"dx": 0, "dy": 0, "dz": 0}

    def world_to_local(self, vehicle_name: str, x: float, y: float, z: float) -> tuple:
        offset = self.get_world_offset(vehicle_name)
        return (x - offset["dx"], y - offset["dy"], z - offset["dz"])

    def get_vehicle_state(self, vehicle_name: str) -> dict:
        start_time = time.time()
        
        state = self.client.getMultirotorState(vehicle_name=vehicle_name)
        vel = state.kinematics_estimated.linear_velocity
        ori = state.kinematics_estimated.orientation
        landed = state.landed_state
        collision_info = getattr(state, 'collision', None)
        has_collided = collision_info.has_collided if collision_info else False
        api_control_enabled = vehicle_name in self._control_enabled

        # 主位置：simGetObjectPose 返回 UE 世界绝对坐标（可靠准确）
        world_pose = self.client.simGetObjectPose(vehicle_name)
        pos = world_pose.position

        # 高度（Z 向上为正，所以高度 = Z 值）
        height_m = round(pos.z_val, 2)

        landed_str = {0: "landed", 1: "flying"}.get(landed, f"unknown({landed})")

        result = {
            "vehicle_name": vehicle_name,
            "position": {
                "x": round(pos.x_val, 3),
                "y": round(pos.y_val, 3),
                "z": round(pos.z_val, 3),
            },
            "height_m": height_m,
            "coordinate_system": "UE_World",
            "coordinate_note": "UE 世界绝对坐标（simGetObjectPose），Z 向上为正",
            "velocity": {
                "x": round(vel.x_val, 3),
                "y": round(vel.y_val, 3),
                "z": round(vel.z_val, 3),
            },
            "orientation": {
                "w": round(ori.w_val, 4),
                "x": round(ori.x_val, 4),
                "y": round(ori.y_val, 4),
                "z": round(ori.z_val, 4),
            },
            "landed_state": landed_str,
            "has_collided": has_collided,
            "api_control_enabled": api_control_enabled,
            "armed": vehicle_name in self._armed,
        }
        
        elapsed = time.time() - start_time
        if elapsed > 1.0:
            result["_performance_warning"] = f"查询耗时 {elapsed:.2f}s，请检查 AirSim 连接"
        
        return result
