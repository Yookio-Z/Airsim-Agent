"""
搜索模式生成器
生成无人机搜索航点：螺旋扩展 / 网格扫描 / 扇形扫描

设计原则:
  - 少量航点（9-13个），每个航点4方向拍照覆盖360°
  - 包含中心航点，确保搜索中心区域不被遗漏
  - 每个航点拍照前旋转朝向搜索中心，确保相机（Pitch=-45°）朝目标区域看
  - 航点间距 ≈ 2 × 高度（Pitch=-45°时，5m高度约覆盖前方5m地面区域）
"""

from __future__ import annotations

import math
from typing import Literal


def _yaw_to_center(x: float, y: float, center_x: float = 0.0, center_y: float = 0.0) -> float:
    """计算从 (x,y) 朝向 (center_x, center_y) 的偏航角（度）。

    AirSim NED 坐标系: yaw=0 朝北(+X), yaw=90 朝东(+Y)
    """
    dx = center_x - x
    dy = center_y - y
    yaw_rad = math.atan2(dy, dx)
    yaw_deg = math.degrees(yaw_rad)
    return round(yaw_deg, 1)


def generate_spiral_waypoints(
    center_x: float = 0.0,
    center_y: float = 0.0,
    altitude: float = -3.0,
    radius_step: float = 6.0,
    max_radius: float = 25.0,
    points_per_circle: int = 4,
) -> list[dict]:
    """螺旋扩展搜索模式。

    从中心开始，逐渐扩大搜索半径。每个航点包含 yaw 朝向中心。

    Args:
        center_x: 搜索中心 X (NED)
        center_y: 搜索中心 Y (NED)
        altitude: 搜索高度 (NED, 负值=向上)，默认-3（3米高）
        radius_step: 每圈增加的半径 (米)，默认6
        max_radius: 最大搜索半径 (米)，默认25
        points_per_circle: 每圈航点数，默认4

    Returns:
        航点列表 [{"x": ..., "y": ..., "z": ..., "yaw": ...}, ...]
    """
    waypoints = []

    waypoints.append({"x": round(center_x, 1), "y": round(center_y, 1), "z": altitude, "yaw": 0.0})

    radius = radius_step

    while radius <= max_radius:
        for i in range(points_per_circle):
            angle = 2 * math.pi * i / points_per_circle
            x = center_x + radius * math.cos(angle)
            y = center_y + radius * math.sin(angle)
            yaw = _yaw_to_center(x, y, center_x, center_y)
            waypoints.append({"x": round(x, 1), "y": round(y, 1), "z": altitude, "yaw": yaw})

        radius += radius_step

    return waypoints


def generate_grid_waypoints(
    center_x: float = 0.0,
    center_y: float = 0.0,
    altitude: float = -7.0,
    area_size: float = 40.0,
    spacing: float = 14.0,
) -> list[dict]:
    """网格扫描搜索模式（割草机模式）。

    Args:
        center_x: 搜索中心 X
        center_y: 搜索中心 Y
        altitude: 搜索高度
        area_size: 搜索区域边长 (米)
        spacing: 扫描线间距 (米)，默认14

    Returns:
        航点列表
    """
    waypoints = []
    half = area_size / 2
    start_x = center_x - half
    start_y = center_y - half

    rows = int(area_size / spacing) + 1
    cols = int(area_size / spacing) + 1

    for row in range(rows):
        y = start_y + row * spacing
        if row % 2 == 0:
            col_range = range(cols)
        else:
            col_range = range(cols - 1, -1, -1)

        for col in col_range:
            x = start_x + col * spacing
            yaw = _yaw_to_center(x, y, center_x, center_y)
            waypoints.append({"x": round(x, 1), "y": round(y, 1), "z": altitude, "yaw": yaw})

    return waypoints


def generate_fan_waypoints(
    center_x: float = 0.0,
    center_y: float = 0.0,
    altitude: float = -7.0,
    heading_deg: float = 0.0,
    fan_angle: float = 120.0,
    num_rays: int = 5,
    ray_length: float = 40.0,
    ray_points: int = 3,
) -> list[dict]:
    """扇形扫描搜索模式。

    Args:
        center_x: 搜索中心 X
        center_y: 搜索中心 Y
        altitude: 搜索高度
        heading_deg: 扇形中心朝向 (度)
        fan_angle: 扇形张角 (度)
        num_rays: 射线数量
        ray_length: 射线长度 (米)
        ray_points: 每条射线的航点数

    Returns:
        航点列表
    """
    waypoints = []
    heading_rad = math.radians(heading_deg)
    half_fan = math.radians(fan_angle / 2)

    for i in range(num_rays):
        if num_rays > 1:
            angle = heading_rad - half_fan + (2 * half_fan * i / (num_rays - 1))
        else:
            angle = heading_rad

        for j in range(1, ray_points + 1):
            dist = ray_length * j / ray_points
            x = center_x + dist * math.cos(angle)
            y = center_y + dist * math.sin(angle)
            yaw = _yaw_to_center(x, y, center_x, center_y)
            waypoints.append({"x": round(x, 1), "y": round(y, 1), "z": altitude, "yaw": yaw})

    for j in range(ray_points - 1, 0, -1):
        dist = ray_length * j / ray_points
        x = center_x + dist * math.cos(angle)
        y = center_y + dist * math.sin(angle)
        yaw = _yaw_to_center(x, y, center_x, center_y)
        waypoints.append({"x": round(x, 1), "y": round(y, 1), "z": altitude, "yaw": yaw})

    return waypoints


def generate_search_waypoints(
    pattern: Literal["spiral", "grid", "fan"] = "spiral",
    **kwargs,
) -> list[dict]:
    """生成搜索航点。每个航点包含 yaw 朝向搜索中心。"""
    generators = {
        "spiral": generate_spiral_waypoints,
        "grid": generate_grid_waypoints,
        "fan": generate_fan_waypoints,
    }
    gen = generators.get(pattern, generate_spiral_waypoints)
    return gen(**kwargs)
