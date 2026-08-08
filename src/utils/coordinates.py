"""
统一坐标转换层
处理 AirSim/NED、ROS/ENU、FLU (Forward-Left-Up) 坐标系之间的转换

坐标系定义：
- NED (North-East-Down): AirSim/PX4 使用，X=North, Y=East, Z=Down
- ENU (East-North-Up): ROS 使用，X=East, Y=North, Z=Up
- FLU (Forward-Left-Up): 无人机体坐标系，X=Forward, Y=Left, Z=Up

参考: https://docs.px4.io/main/en/ros/external_position_estimation.html
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Vector3:
    """三维向量，不绑定具体坐标系"""
    x: float
    y: float
    z: float

    def __add__(self, other: Vector3) -> Vector3:
        return Vector3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Vector3) -> Vector3:
        return Vector3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, scalar: float) -> Vector3:
        return Vector3(self.x * scalar, self.y * scalar, self.z * scalar)

    def to_tuple(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass(frozen=True)
class Quaternion:
    """四元数 w + xi + yj + zk"""
    w: float
    x: float
    y: float
    z: float

    @staticmethod
    def from_euler(roll: float, pitch: float, yaw: float) -> Quaternion:
        """从欧拉角(弧度)创建四元数，按 roll->pitch->yaw 顺序"""
        cr, sr = math.cos(roll * 0.5), math.sin(roll * 0.5)
        cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
        cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)

        w = cr * cp * cy + sr * sp * sy
        x = sr * cp * cy - cr * sp * sy
        y = cr * sp * cy + sr * cp * sy
        z = cr * cp * sy - sr * sp * cy
        return Quaternion(w, x, y, z)

    def to_euler(self) -> Tuple[float, float, float]:
        """转换为欧拉角 (roll, pitch, yaw)，单位弧度"""
        sinr_cosp = 2.0 * (self.w * self.x + self.y * self.z)
        cosr_cosp = 1.0 - 2.0 * (self.x * self.x + self.y * self.y)
        roll = math.atan2(sinr_cosp, cosr_cosp)

        sinp = 2.0 * (self.w * self.y - self.z * self.x)
        pitch = math.asin(max(-1.0, min(1.0, sinp)))

        siny_cosp = 2.0 * (self.w * self.z + self.x * self.y)
        cosy_cosp = 1.0 - 2.0 * (self.y * self.y + self.z * self.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        return (roll, pitch, yaw)

    def to_euler_degrees(self) -> Tuple[float, float, float]:
        """转换为欧拉角 (roll, pitch, yaw)，单位度"""
        r, p, y = self.to_euler()
        return (math.degrees(r), math.degrees(p), math.degrees(y))


def ned_to_enu(v: Vector3) -> Vector3:
    """NED -> ENU (AirSim/PX4 -> ROS)
    NED: X=North, Y=East, Z=Down
    ENU: X=East, Y=North, Z=Up
    """
    return Vector3(v.y, v.x, -v.z)


def enu_to_ned(v: Vector3) -> Vector3:
    """ENU -> NED (ROS -> AirSim/PX4)"""
    return Vector3(v.y, v.x, -v.z)


def ned_to_flu(v: Vector3, yaw: float) -> Vector3:
    """NED -> FLU (世界坐标 -> 机体坐标)
    需要传入机体航向角 yaw (弧度)
    """
    cy, sy = math.cos(yaw), math.sin(yaw)
    return Vector3(
        v.x * cy + v.y * sy,
        -v.x * sy + v.y * cy,
        -v.z,
    )


def flu_to_ned(v: Vector3, yaw: float) -> Vector3:
    """FLU -> NED (机体坐标 -> 世界坐标)
    需要传入机体航向角 yaw (弧度)
    """
    cy, sy = math.cos(yaw), math.sin(yaw)
    return Vector3(
        v.x * cy - v.y * sy,
        v.x * sy + v.y * cy,
        -v.z,
    )


def euler_ned_to_enu(roll: float, pitch: float, yaw: float) -> Tuple[float, float, float]:
    """NED欧拉角 -> ENU欧拉角
    NED: roll绕X(北), pitch绕Y(东, nose up=+), yaw绕Z(顺时针+)
    ENU: roll绕X(东), pitch绕Y(北, nose down=+), yaw绕Z(逆时针+)
    MAVLink -> ROS 转换
    """
    return (roll, -pitch, -yaw)


def euler_enu_to_ned(roll: float, pitch: float, yaw: float) -> Tuple[float, float, float]:
    """ENU欧拉角 -> NED欧拉角 (ROS -> MAVLink)"""
    return (roll, -pitch, -yaw)


def quat_ned_to_enu(q: Quaternion) -> Quaternion:
    """NED四元数 -> ENU四元数
    等价于绕 [1,1,1]/sqrt(3) 旋转 120 度，或直接用欧拉角转换
    """
    roll, pitch, yaw = q.to_euler()
    r2, p2, y2 = euler_ned_to_enu(roll, pitch, yaw)
    return Quaternion.from_euler(r2, p2, y2)


def quat_enu_to_ned(q: Quaternion) -> Quaternion:
    """ENU四元数 -> NED四元数"""
    roll, pitch, yaw = q.to_euler()
    r2, p2, y2 = euler_enu_to_ned(roll, pitch, yaw)
    return Quaternion.from_euler(r2, p2, y2)


def normalize_angle_deg(angle: float) -> float:
    """将角度规范化到 [-180, 180] 范围"""
    while angle > 180.0:
        angle -= 360.0
    while angle < -180.0:
        angle += 360.0
    return angle


def yaw_to_heading_deg(yaw_rad: float) -> float:
    """将弧度 yaw (NED, 0=North, 顺时针+) 转换为度 heading (0=North, 顺时针+)"""
    heading = math.degrees(yaw_rad)
    if heading < 0:
        heading += 360.0
    return heading


def heading_to_yaw_rad(heading_deg: float) -> float:
    """将度 heading 转换为弧度 yaw (NED)"""
    yaw = math.radians(heading_deg)
    if yaw > math.pi:
        yaw -= 2 * math.pi
    return yaw


def gps_to_local_meters(lat: float, lon: float, lat_origin: float, lon_origin: float) -> Tuple[float, float]:
    """GPS坐标(度)转换为本地ENU平面坐标(米)
    返回 (x=East, y=North)
    """
    R = 6371000.0
    dlat = math.radians(lat - lat_origin)
    dlon = math.radians(lon - lon_origin)
    x = dlon * R * math.cos(math.radians(lat_origin))
    y = dlat * R
    return (x, y)


def local_meters_to_gps(x: float, y: float, lat_origin: float, lon_origin: float) -> Tuple[float, float]:
    """本地ENU平面坐标(米)转换为GPS坐标(度)
    x=East, y=North
    """
    R = 6371000.0
    dlat = y / R
    dlon = x / (R * math.cos(math.radians(lat_origin)))
    lat = lat_origin + math.degrees(dlat)
    lon = lon_origin + math.degrees(dlon)
    return (lat, lon)


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """计算从点1到点2的方位角(度)，0=North, 顺时针+"""
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    bearing = math.degrees(math.atan2(x, y))
    if bearing < 0:
        bearing += 360.0
    return bearing


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """计算两点间球面距离(米)"""
    R = 6371000.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))
