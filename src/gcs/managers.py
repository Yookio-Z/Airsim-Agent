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
    """机队清单（读遥测快照；单机后端可能为空 = "默认那一台"）。"""

    def list_vehicles(self) -> list[dict[str, Any]]:
        """Return the fleet as the panels see it (telemetry snapshot)."""


class TelemetryManager(Protocol):
    """Publishes the single source of truth for vehicle telemetry."""

    def get_vehicle(self, vehicle_id: str = "") -> VehicleTelemetry | None:
        """Return telemetry for a vehicle, defaulting to the active vehicle."""

    def get_state(self) -> GroundStationState:
        """Return a full ground station state snapshot."""

    def refresh(self) -> GroundStationState:
        """Poll or consume backend telemetry and return the latest snapshot."""


class CommandManager(Protocol):
    """设备动作入口：先安全校验，再交运行时治理执行。"""

    def execute_tool(
        self, tool: str, params: dict[str, Any] | None = None, vehicle_id: str = ""
    ) -> Any:
        """Run one device action and return its ToolCallResult."""


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
