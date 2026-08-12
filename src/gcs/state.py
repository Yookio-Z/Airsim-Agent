"""Shared state snapshots for the ground control station."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .mission import MissionPlanDraft


@dataclass
class LinkState:
    backend: str = ""
    connected: bool = False
    ready: bool = False
    stale: bool = False
    transport: str = ""
    endpoint: str = ""
    last_heartbeat_at: float = 0.0
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "connected": self.connected,
            "ready": self.ready,
            "stale": self.stale,
            "transport": self.transport,
            "endpoint": self.endpoint,
            "last_heartbeat_at": self.last_heartbeat_at,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class VehicleTelemetry:
    vehicle_id: str = ""
    position_ned: dict[str, float] = field(default_factory=dict)
    velocity_ned: dict[str, float] = field(default_factory=dict)
    attitude_rad: dict[str, float] = field(default_factory=dict)
    gps: dict[str, float] | None = None
    heading_deg: float | None = None
    battery_voltage: float | None = None
    armed: bool = False
    flying: bool = False
    mode: str = ""
    has_collided: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vehicle_id": self.vehicle_id,
            "position_ned": self.position_ned,
            "velocity_ned": self.velocity_ned,
            "attitude_rad": self.attitude_rad,
            "gps": self.gps,
            "heading_deg": self.heading_deg,
            "battery_voltage": self.battery_voltage,
            "armed": self.armed,
            "flying": self.flying,
            "mode": self.mode,
            "has_collided": self.has_collided,
            "raw": self.raw,
        }

    @classmethod
    def from_drone_status(cls, data: dict[str, Any] | None, vehicle_id: str = "") -> VehicleTelemetry | None:
        if not isinstance(data, dict):
            return None
        return cls(
            vehicle_id=vehicle_id or str(data.get("vehicle_name") or data.get("vehicle_id") or ""),
            position_ned=dict(data.get("position_ned") or {}),
            velocity_ned=dict(data.get("velocity_ned") or {}),
            attitude_rad=dict(data.get("attitude_rad") or {}),
            gps=dict(data.get("gps")) if isinstance(data.get("gps"), dict) else None,
            heading_deg=_optional_float(data.get("heading_deg")),
            battery_voltage=_optional_float(data.get("battery_voltage")),
            armed=bool(data.get("armed", False)),
            flying=bool(data.get("flying", False)),
            mode=str(data.get("mode") or ""),
            has_collided=data.get("has_collided") if isinstance(data.get("has_collided"), bool) else None,
            raw=dict(data),
        )


@dataclass
class MissionState:
    draft: MissionPlanDraft | None = None
    uploaded: bool = False
    running: bool = False
    current_seq: int = 0
    total_items: int = 0
    last_reached_seq: int | None = None
    progress: float = 0.0
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft": self.draft.to_dict() if self.draft else None,
            "uploaded": self.uploaded,
            "running": self.running,
            "current_seq": self.current_seq,
            "total_items": self.total_items,
            "last_reached_seq": self.last_reached_seq,
            "progress": round(self.progress, 3),
            "message": self.message,
            "details": self.details,
        }


@dataclass
class SafetyState:
    level: str = "safe"
    geofence_ok: bool = True
    altitude_ok: bool = True
    mode_ok: bool = True
    gps_ok: bool = True
    battery_ok: bool = True
    emergency_stop: bool = False
    paused: bool = False
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "geofence_ok": self.geofence_ok,
            "altitude_ok": self.altitude_ok,
            "mode_ok": self.mode_ok,
            "gps_ok": self.gps_ok,
            "battery_ok": self.battery_ok,
            "emergency_stop": self.emergency_stop,
            "paused": self.paused,
            "warnings": list(self.warnings),
            "details": self.details,
        }


@dataclass
class AgentState:
    status: str = "idle"
    current_run: dict[str, Any] | None = None
    task_level: str = ""
    route_strategy: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "current_run": self.current_run,
            "task_level": self.task_level,
            "route_strategy": self.route_strategy,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class GroundStationState:
    backend: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    link: LinkState = field(default_factory=LinkState)
    vehicle: VehicleTelemetry | None = None
    vehicles: list[VehicleTelemetry] = field(default_factory=list)
    mission: MissionState = field(default_factory=MissionState)
    safety: SafetyState = field(default_factory=SafetyState)
    agent: AgentState = field(default_factory=AgentState)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "capabilities": self.capabilities,
            "link": self.link.to_dict(),
            "vehicle": self.vehicle.to_dict() if self.vehicle else None,
            "vehicles": [item.to_dict() for item in self.vehicles],
            "mission": self.mission.to_dict(),
            "safety": self.safety.to_dict(),
            "agent": self.agent.to_dict(),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_tool_runtime(
        cls,
        snapshot: dict[str, Any],
        supervisor: dict[str, Any] | None = None,
        current_run: dict[str, Any] | None = None,
    ) -> GroundStationState:
        backend_profile = snapshot.get("backend_profile") or {}
        capabilities = dict(backend_profile.get("capabilities") or {})
        link = LinkState(
            backend=str(snapshot.get("backend") or backend_profile.get("id") or ""),
            connected=bool(snapshot.get("connected", False)),
            ready=bool(snapshot.get("ready", False)),
            stale=bool(snapshot.get("stale_connection", False)),
            message=str(snapshot.get("init_error") or ""),
            details={k: v for k, v in snapshot.items() if k not in {"drone", "backends"}},
        )
        supervisor = supervisor or {}
        safety = SafetyState(
            emergency_stop=bool(supervisor.get("emergency_stop", False)),
            paused=bool(supervisor.get("paused", False)),
            details=dict(supervisor),
        )
        agent = AgentState(
            status=str((current_run or {}).get("status") or "idle"),
            current_run=current_run,
            task_level=str((current_run or {}).get("task_level") or ""),
            route_strategy=str((current_run or {}).get("route_strategy") or ""),
        )
        vehicles = [
            VehicleTelemetry.from_drone_status(item)
            for item in snapshot.get("vehicles") or []
            if isinstance(item, dict)
        ]
        if not vehicles:
            default = VehicleTelemetry.from_drone_status(snapshot.get("drone"))
            if default is not None:
                vehicles = [default]
        return cls(
            backend=dict(backend_profile),
            capabilities=capabilities,
            link=link,
            vehicle=VehicleTelemetry.from_drone_status(snapshot.get("drone")),
            vehicles=vehicles,
            safety=safety,
            agent=agent,
        )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
