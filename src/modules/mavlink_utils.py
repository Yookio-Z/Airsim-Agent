"""MAVLink 模块级转换工具（拆分自 mavlink_controller.py）。"""
from __future__ import annotations

import math
from typing import Any
from pymavlink import mavutil

def _gps_offset_m(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    lat_rad = math.radians(lat1)
    meters_per_lat = 111132.92 - 559.82 * math.cos(2 * lat_rad) + 1.175 * math.cos(4 * lat_rad)
    meters_per_lon = 111412.84 * math.cos(lat_rad) - 93.5 * math.cos(3 * lat_rad)
    return (lat2 - lat1) * meters_per_lat, (lon2 - lon1) * meters_per_lon


def _gps_from_offset_m(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    lat_rad = math.radians(lat)
    meters_per_lat = 111132.92 - 559.82 * math.cos(2 * lat_rad) + 1.175 * math.cos(4 * lat_rad)
    meters_per_lon = 111412.84 * math.cos(lat_rad) - 93.5 * math.cos(3 * lat_rad)
    if abs(meters_per_lon) < 1e-6:
        meters_per_lon = 1e-6
    return lat + north_m / meters_per_lat, lon + east_m / meters_per_lon


def _mission_command_for_item(item_type: str, item: dict[str, Any]) -> int:
    metadata = dict(item.get("metadata") or {})
    explicit = _optional_int(item.get("mav_command"))
    if explicit is None:
        explicit = _optional_int(metadata.get("mav_command"))
    if explicit is not None:
        return explicit

    normalized = (item_type or "waypoint").strip().lower().replace("-", "_")
    if normalized in {"takeoff", "nav_takeoff"}:
        return mavutil.mavlink.MAV_CMD_NAV_TAKEOFF
    if normalized in {"land", "landing", "nav_land"}:
        return mavutil.mavlink.MAV_CMD_NAV_LAND
    if normalized in {"rtl", "return_home", "return_to_launch", "nav_return_to_launch"}:
        return mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH
    return mavutil.mavlink.MAV_CMD_NAV_WAYPOINT


def _mission_type_for_command(command: int) -> str:
    if command == mavutil.mavlink.MAV_CMD_NAV_TAKEOFF:
        return "takeoff"
    if command == mavutil.mavlink.MAV_CMD_NAV_LAND:
        return "land"
    if command == mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH:
        return "return_to_launch"
    return "waypoint"


def _mission_params_for_command(command: int, item: dict[str, Any]) -> tuple[float, float, float, float]:
    yaw = _optional_float(item.get("yaw_deg")) or 0.0
    if command == mavutil.mavlink.MAV_CMD_NAV_WAYPOINT:
        return (
            max(0.0, float(item.get("hold_s", 0.0) or 0.0)),
            max(0.0, float(item.get("acceptance_radius_m", 2.0) or 2.0)),
            0.0,
            yaw,
        )
    if command == mavutil.mavlink.MAV_CMD_NAV_TAKEOFF:
        return (0.0, 0.0, 0.0, yaw)
    if command == mavutil.mavlink.MAV_CMD_NAV_LAND:
        return (0.0, 0.0, 0.0, yaw)
    if command == mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH:
        return (0.0, 0.0, 0.0, 0.0)
    return (0.0, 0.0, 0.0, yaw)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
