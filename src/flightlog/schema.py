"""飞行记录（flight log）的帧格式与统计。

这里是**帧内容的唯一来源**：写盘、读取、以后的离线分析都从 `build_frame()` 与
`summarize_frames()` 取，字段不再随 `status_snapshot()` 漂移。格式带版本号，
以后加字段只升版本、不改旧含义。
"""

from __future__ import annotations

import math
from typing import Any

FLIGHTLOG_SCHEMA_VERSION = 2

# 每次飞行都记录的遥测字段（来自 gcs.telemetry.snapshot() 的 drone 字典）。
# 这里只是"文档"，真正透传的是快照本身；新增字段不需要改代码。
DRONE_FIELDS = (
    "position_ned",
    "velocity_ned",
    "attitude_rad",
    "armed",
    "flying",
    "mode",
    "heading_deg",
    "landed_state",
    "has_collided",
    "vehicle_types",
    "external_flight_controller",
    "gps",
    "battery_voltage",
)


def build_frame(snapshot: dict[str, Any]) -> dict[str, Any]:
    """一份工具运行时快照 → 一帧飞行记录（只留遥测，不带工具内部字段）。"""
    drone = snapshot.get("drone")
    vehicles = snapshot.get("vehicles") if isinstance(snapshot.get("vehicles"), list) else []
    return {
        "backend": str(snapshot.get("backend") or ""),
        "drone": drone if isinstance(drone, dict) else {},
        "vehicles": [item for item in vehicles if isinstance(item, dict)],
    }


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pos(row: dict[str, Any]) -> tuple[float, float, float] | None:
    pos = row.get("position_ned") if isinstance(row.get("position_ned"), dict) else None
    if not pos:
        return None
    x, y, z = _num(pos.get("x")), _num(pos.get("y")), _num(pos.get("z"))
    if x is None or y is None or z is None:
        return None
    return x, y, z


def summarize_frames(frames: list[dict[str, Any]], duration_s: float = 0.0) -> dict[str, Any]:
    """一次飞行的摘要：时长、里程、高度/速度极值、状态计数。

    统计只依赖帧数据，不做任何推断；读不到的位置/速度会被跳过，不产生假值。
    """
    distance = 0.0
    previous: tuple[float, float, float] | None = None
    altitudes: list[float] = []
    h_speeds: list[float] = []
    v_speeds: list[float] = []
    armed_frames = flying_frames = collision_frames = 0
    vehicles: set[str] = set()

    for frame in frames:
        data = frame.get("data") if isinstance(frame.get("data"), dict) else frame
        drone = data.get("drone") if isinstance(data, dict) else None
        if not isinstance(drone, dict):
            continue
        pos = _pos(drone)
        if pos is not None:
            if previous is not None:
                distance += math.dist(previous, pos)
            previous = pos
            altitudes.append(abs(pos[2]))
        vel = drone.get("velocity_ned") if isinstance(drone.get("velocity_ned"), dict) else {}
        vx, vy, vz = _num(vel.get("vx")), _num(vel.get("vy")), _num(vel.get("vz"))
        if vx is not None and vy is not None:
            h_speeds.append(math.hypot(vx, vy))
        if vz is not None:
            v_speeds.append(abs(vz))
        if drone.get("armed"):
            armed_frames += 1
        if drone.get("flying"):
            flying_frames += 1
        if drone.get("has_collided"):
            collision_frames += 1
        for item in data.get("vehicles") or []:
            if isinstance(item, dict) and item.get("vehicle_name"):
                vehicles.add(str(item["vehicle_name"]))

    return {
        "frame_count": len(frames),
        "duration_s": round(float(duration_s), 2),
        "distance_m": round(distance, 2),
        "max_altitude_m": round(max(altitudes), 2) if altitudes else None,
        "min_altitude_m": round(min(altitudes), 2) if altitudes else None,
        "max_horizontal_speed_mps": round(max(h_speeds), 2) if h_speeds else None,
        "max_vertical_speed_mps": round(max(v_speeds), 2) if v_speeds else None,
        "armed_frames": armed_frames,
        "flying_frames": flying_frames,
        "collision_frames": collision_frames,
        "vehicles": sorted(vehicles),
    }
