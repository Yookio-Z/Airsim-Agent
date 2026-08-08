"""Ground control station manager contracts.

These protocols define the shared boundary for manual UI actions and Agent
actions. Implementations may wrap ToolRuntime at first and can later move to
dedicated MAVLink, ROS2, or real-vehicle services.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .mission import MissionPlanDraft
from .state import GroundStationState, LinkState, MissionState, SafetyState, VehicleTelemetry


@dataclass
class ManagerResult:
    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "message": self.message, "data": self.data}


class LinkManager(Protocol):
    """Owns low-level communication links such as MAVLink UDP/TCP/serial."""

    def list_links(self) -> list[LinkState]:
        """Return all known links and their current state."""

    def connect(self, transport: str, endpoint: str, **params: Any) -> ManagerResult:
        """Open a link, for example UDP `127.0.0.1:14540` or a serial port."""

    def disconnect(self, link_id: str = "") -> ManagerResult:
        """Close one link or the active link when `link_id` is empty."""

    def status(self) -> LinkState:
        """Return the active link state."""


class VehicleManager(Protocol):
    """Discovers and selects vehicles from link heartbeats."""

    def list_vehicles(self) -> list[dict[str, Any]]:
        """Return vehicles known to the ground station."""

    def active_vehicle_id(self) -> str:
        """Return the active vehicle id, or an empty string if none is active."""

    def set_active_vehicle(self, vehicle_id: str) -> ManagerResult:
        """Select the vehicle that UI and Agent commands target."""

    def remove_vehicle(self, vehicle_id: str) -> ManagerResult:
        """Forget a vehicle that is no longer connected."""


class TelemetryManager(Protocol):
    """Publishes the single source of truth for vehicle telemetry."""

    def get_vehicle(self, vehicle_id: str = "") -> VehicleTelemetry | None:
        """Return telemetry for a vehicle, defaulting to the active vehicle."""

    def get_state(self) -> GroundStationState:
        """Return a full ground station state snapshot."""

    def refresh(self) -> GroundStationState:
        """Poll or consume backend telemetry and return the latest snapshot."""


class MissionManager(Protocol):
    """Owns local mission drafts and vehicle mission synchronization."""

    def get_draft(self) -> MissionPlanDraft | None:
        """Return the current local editable mission draft."""

    def set_draft(self, draft: MissionPlanDraft) -> ManagerResult:
        """Replace the current local mission draft."""

    def validate(self, draft: MissionPlanDraft | None = None) -> ManagerResult:
        """Validate a mission against backend capabilities and safety rules."""

    def upload(self, draft: MissionPlanDraft | None = None) -> ManagerResult:
        """Upload a mission to the active vehicle."""

    def download(self) -> MissionPlanDraft | None:
        """Download the active vehicle mission into a local draft."""

    def clear(self) -> ManagerResult:
        """Clear the active vehicle mission and local mission state."""

    def start(self, draft: MissionPlanDraft | None = None) -> ManagerResult:
        """Start the uploaded mission on the active vehicle."""

    def pause(self) -> ManagerResult:
        """Pause mission execution when supported by the backend."""

    def resume(self) -> ManagerResult:
        """Resume mission execution when supported by the backend."""

    def progress(self) -> MissionState:
        """Return mission execution progress."""


class CommandManager(Protocol):
    """Executes high-level vehicle commands after safety validation."""

    def arm(self, vehicle_id: str = "") -> ManagerResult:
        """Arm the active vehicle."""

    def disarm(self, vehicle_id: str = "") -> ManagerResult:
        """Disarm the active vehicle."""

    def takeoff(self, altitude_m: float, vehicle_id: str = "") -> ManagerResult:
        """Take off to a relative altitude."""

    def land(self, vehicle_id: str = "") -> ManagerResult:
        """Land the active vehicle."""

    def hold(self, vehicle_id: str = "") -> ManagerResult:
        """Hold/hover at the current position."""

    def rtl(self, vehicle_id: str = "") -> ManagerResult:
        """Return to launch/home when the backend supports it."""

    def set_mode(self, mode: str, vehicle_id: str = "") -> ManagerResult:
        """Set a flight mode such as GUIDED, LOITER, HOLD, or LAND."""

    def goto(self, item: dict[str, Any], vehicle_id: str = "") -> ManagerResult:
        """Command a one-shot navigation target."""


class SafetyManager(Protocol):
    """Validates commands and missions before they reach a vehicle backend."""

    def state(self) -> SafetyState:
        """Return current safety state."""

    def validate_command(self, command: str, params: dict[str, Any], state: GroundStationState) -> ManagerResult:
        """Validate one command against platform and mission constraints."""

    def validate_mission(self, draft: MissionPlanDraft, state: GroundStationState) -> ManagerResult:
        """Validate a mission before upload or execution."""

    def emergency_stop(self) -> ManagerResult:
        """Trigger an emergency stop/hold command."""

    def reset_emergency(self) -> ManagerResult:
        """Clear emergency stop state when allowed."""
