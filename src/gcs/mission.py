"""Backend-neutral mission models for the ground control station."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from src.utils.coordinates import gps_to_local_meters, haversine_meters


def _mission_id() -> str:
    return f"mission_{int(time.time() * 1000)}"


@dataclass
class GeoPoint:
    """Global WGS84 position with relative altitude in meters."""

    lat: float
    lon: float
    alt_m: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"lat": self.lat, "lon": self.lon, "alt_m": self.alt_m}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> GeoPoint | None:
        if not isinstance(data, dict):
            return None
        lat = data.get("lat")
        lon = data.get("lon")
        if lat is None or lon is None:
            return None
        return cls(lat=float(lat), lon=float(lon), alt_m=float(data.get("alt_m", data.get("alt", 0.0)) or 0.0))


@dataclass
class LocalNedPoint:
    """Local NED position. Negative z means above the local origin."""

    x: float
    y: float
    z: float

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "z": self.z}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> LocalNedPoint | None:
        if not isinstance(data, dict):
            return None
        if not all(k in data for k in ("x", "y", "z")):
            return None
        return cls(x=float(data["x"]), y=float(data["y"]), z=float(data["z"]))


@dataclass
class MissionItem:
    """One backend-neutral mission item.

    `global_relative_alt` uses lat/lon/alt_m and maps naturally to MAVLink
    mission items. `local_ned` uses x/y/z and maps naturally to AirSim paths.
    """

    id: str
    type: str = "waypoint"
    frame: str = "global_relative_alt"
    lat: float | None = None
    lon: float | None = None
    alt_m: float = 0.0
    x: float | None = None
    y: float | None = None
    z: float | None = None
    speed_mps: float = 0.0
    hold_s: float = 0.0
    acceptance_radius_m: float = 2.0
    actions: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "frame": self.frame,
            "lat": self.lat,
            "lon": self.lon,
            "alt_m": self.alt_m,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "speed_mps": self.speed_mps,
            "hold_s": self.hold_s,
            "acceptance_radius_m": self.acceptance_radius_m,
            "actions": list(self.actions),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionItem:
        return cls(
            id=str(data.get("id") or f"wp_{int(time.time() * 1000)}"),
            type=str(data.get("type") or "waypoint"),
            frame=str(data.get("frame") or "global_relative_alt"),
            lat=_optional_float(data.get("lat")),
            lon=_optional_float(data.get("lon")),
            alt_m=float(data.get("alt_m", 0.0) or 0.0),
            x=_optional_float(data.get("x")),
            y=_optional_float(data.get("y")),
            z=_optional_float(data.get("z")),
            speed_mps=float(data.get("speed_mps", 0.0) or 0.0),
            hold_s=float(data.get("hold_s", 0.0) or 0.0),
            acceptance_radius_m=float(data.get("acceptance_radius_m", 2.0) or 2.0),
            actions=[dict(a) for a in (data.get("actions") or []) if isinstance(a, dict)],
            metadata=dict(data.get("metadata") or {}),
        )

    def is_global(self) -> bool:
        return self.frame.startswith("global") and self.lat is not None and self.lon is not None

    def is_local_ned(self) -> bool:
        return self.frame == "local_ned" and self.x is not None and self.y is not None and self.z is not None

    def to_local_ned(self, home: GeoPoint | None = None) -> LocalNedPoint | None:
        if self.is_local_ned():
            return LocalNedPoint(float(self.x), float(self.y), float(self.z))
        if self.is_global() and home:
            east, north = gps_to_local_meters(float(self.lat), float(self.lon), home.lat, home.lon)
            return LocalNedPoint(x=north, y=east, z=-abs(float(self.alt_m)))
        return None


@dataclass
class MissionPlanDraft:
    """Editable mission plan shared by UI, PX4, AirSim, and Agent code."""

    id: str = field(default_factory=_mission_id)
    name: str = "Untitled mission"
    vehicle: str = ""
    home: GeoPoint | None = None
    items: list[MissionItem] = field(default_factory=list)
    total_distance_m: float = 0.0
    estimated_duration_s: float = 0.0
    validation_warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def recalculate(self) -> MissionPlanDraft:
        self.validation_warnings = self.validate()
        self.total_distance_m = round(self._estimate_distance(), 3)
        self.estimated_duration_s = round(self._estimate_duration(), 3)
        self.updated_at = time.time()
        return self

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if not self.items:
            warnings.append("mission_has_no_items")
            return warnings
        for item in self.items:
            if not (item.is_global() or item.is_local_ned()):
                warnings.append(f"{item.id}:missing_position")
            if item.frame.startswith("global") and not self.home:
                warnings.append("global_mission_without_home")
            if item.type == "waypoint" and item.alt_m < 0:
                warnings.append(f"{item.id}:negative_global_altitude")
        return warnings

    def to_dict(self) -> dict[str, Any]:
        self.recalculate()
        return {
            "id": self.id,
            "name": self.name,
            "vehicle": self.vehicle,
            "home": self.home.to_dict() if self.home else None,
            "items": [item.to_dict() for item in self.items],
            "total_distance_m": self.total_distance_m,
            "estimated_duration_s": self.estimated_duration_s,
            "validation_warnings": list(self.validation_warnings),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionPlanDraft:
        plan = cls(
            id=str(data.get("id") or _mission_id()),
            name=str(data.get("name") or "Untitled mission"),
            vehicle=str(data.get("vehicle") or ""),
            home=GeoPoint.from_dict(data.get("home")),
            items=[MissionItem.from_dict(x) for x in (data.get("items") or []) if isinstance(x, dict)],
            metadata=dict(data.get("metadata") or {}),
            created_at=float(data.get("created_at", time.time()) or time.time()),
            updated_at=float(data.get("updated_at", time.time()) or time.time()),
        )
        return plan.recalculate()

    def _estimate_distance(self) -> float:
        if len(self.items) < 2:
            return 0.0
        total = 0.0
        for prev, cur in zip(self.items, self.items[1:]):
            total += _item_distance(prev, cur, self.home)
        return total

    def _estimate_duration(self) -> float:
        if not self.items:
            return 0.0
        duration = sum(max(0.0, item.hold_s) for item in self.items)
        for prev, cur in zip(self.items, self.items[1:]):
            speed = cur.speed_mps or prev.speed_mps or 3.0
            duration += _item_distance(prev, cur, self.home) / max(0.1, speed)
        return duration


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _item_distance(a: MissionItem, b: MissionItem, home: GeoPoint | None) -> float:
    if a.is_global() and b.is_global():
        return haversine_meters(float(a.lat), float(a.lon), float(b.lat), float(b.lon))
    pa = a.to_local_ned(home)
    pb = b.to_local_ned(home)
    if pa and pb:
        return math.sqrt((pa.x - pb.x) ** 2 + (pa.y - pb.y) ** 2 + (pa.z - pb.z) ** 2)
    return 0.0
