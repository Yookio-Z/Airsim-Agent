"""
FlightController 抽象接口
所有飞行控制后端（AirSim RPC / pymavlink）必须实现此接口

设计原则:
  - 上层工具代码只依赖此接口，不依赖具体后端
  - 状态返回值格式统一（DroneStatus）
  - 坐标系统一为 NED
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DroneStatus:
    """统一的无人机状态返回格式"""
    position_ned: dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0, "z": 0.0})
    velocity_ned: dict[str, float] = field(default_factory=lambda: {"vx": 0.0, "vy": 0.0, "vz": 0.0})
    attitude_rad: dict[str, float] = field(default_factory=lambda: {"roll": 0.0, "pitch": 0.0, "yaw": 0.0})
    armed: bool = False
    flying: bool = False
    mode: str = ""
    gps: dict[str, float] | None = None
    battery_voltage: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "position_ned": self.position_ned,
            "velocity_ned": self.velocity_ned,
            "attitude_rad": self.attitude_rad,
            "armed": self.armed,
            "flying": self.flying,
            "mode": self.mode,
        }
        if self.gps:
            d["gps"] = self.gps
        if self.battery_voltage is not None:
            d["battery_voltage"] = self.battery_voltage
        if self.extra:
            d.update(self.extra)
        return d


@dataclass
class ConnectionInfo:
    """连接信息"""
    backend: str = ""
    connected: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "connected": self.connected,
            **self.details,
        }


class FlightController(ABC):
    """飞行控制抽象接口"""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """后端名称: 'airsim' 或 'mavlink'"""

    @abstractmethod
    def connect(self, **kwargs) -> ConnectionInfo:
        """建立连接"""

    @abstractmethod
    def disconnect(self) -> None:
        """断开连接"""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """是否已连接"""

    @abstractmethod
    def arm(self, vehicle_name: str = "") -> bool:
        """解锁电机"""

    @abstractmethod
    def disarm(self, vehicle_name: str = "") -> bool:
        """锁定电机"""

    @abstractmethod
    def takeoff(self, altitude: float = 3.0, vehicle_name: str = "") -> bool:
        """起飞到指定高度（米）"""

    @abstractmethod
    def land(self, vehicle_name: str = "") -> bool:
        """降落"""

    @abstractmethod
    def hover(self, vehicle_name: str = "") -> bool:
        """悬停"""

    @abstractmethod
    def move_to_position(self, x: float, y: float, z: float, velocity: float = 2.0, vehicle_name: str = "") -> bool:
        """飞到指定 NED 坐标位置"""

    @abstractmethod
    def move_by_velocity(self, vx: float, vy: float, vz: float, duration: float = 0.0, vehicle_name: str = "") -> bool:
        """按速度飞行（NED 坐标系，m/s）"""

    @abstractmethod
    def move_on_path(self, waypoints: list[dict], velocity: float = 2.0, vehicle_name: str = "") -> bool:
        """按航点列表飞行，waypoints: [{"x":..,"y":..,"z":..}]"""

    @abstractmethod
    def get_status(self, vehicle_name: str = "") -> DroneStatus:
        """获取无人机状态"""

    @abstractmethod
    def list_vehicles(self) -> list[str]:
        """列出可用无人机"""

    @abstractmethod
    def set_mode(self, mode: str, vehicle_name: str = "") -> bool:
        """设置飞行模式"""

    @abstractmethod
    def rotate_to_heading(self, heading_deg: float, timeout: float = 30.0, vehicle_name: str = "") -> bool:
        """旋转到指定航向（度，0=North，顺时针+）"""

    def stop(self, vehicle_name: str = "") -> bool:
        """停止运动（默认实现用 hover）"""
        return self.hover(vehicle_name)
