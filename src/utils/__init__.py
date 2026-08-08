"""
工具函数层
- coordinates: 统一坐标转换 (NED/ROS/FLU)
"""

from .coordinates import (
    Vector3,
    Quaternion,
    ned_to_enu,
    enu_to_ned,
    ned_to_flu,
    flu_to_ned,
    normalize_angle_deg,
    yaw_to_heading_deg,
    heading_to_yaw_rad,
    gps_to_local_meters,
    haversine_meters,
)

__all__ = [
    "Vector3",
    "Quaternion",
    "ned_to_enu",
    "enu_to_ned",
    "ned_to_flu",
    "flu_to_ned",
    "normalize_angle_deg",
    "yaw_to_heading_deg",
    "heading_to_yaw_rad",
    "gps_to_local_meters",
    "haversine_meters",
]
