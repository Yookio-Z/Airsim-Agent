"""Multi-vehicle support tests: tool-level vehicle_name semantics (""=default,
"all"=broadcast, name=single), vehicles state model, and GCS wiring."""

from __future__ import annotations

from types import SimpleNamespace

from src.agent.runtime import AgentRuntime
from src.agent.tool_executor import ToolCollector, ToolRuntime
from src.gcs.services import ToolCommandManager, ToolSafetyManager, ToolTelemetryManager
from src.gcs.state import GroundStationState
from src.tools.core import register_core_tools


class _FleetController:
    """AirSim-like multi-vehicle controller recording every call."""

    backend_name = "airsim"
    vehicles = ["Drone1", "Drone2"]

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def list_vehicles(self) -> list[str]:
        return list(self.vehicles)

    def is_connected(self) -> bool:
        return True

    def arm(self, vehicle_name: str = "") -> bool:
        self.calls.append(("arm", vehicle_name))
        return True

    def takeoff(self, altitude: float = 3.0, vehicle_name: str = "") -> bool:
        self.calls.append(("takeoff", vehicle_name, altitude))
        return True

    def land(self, vehicle_name: str = "") -> bool:
        self.calls.append(("land", vehicle_name))
        return True

    def hover(self, vehicle_name: str = "") -> bool:
        self.calls.append(("hover", vehicle_name))
        return True

    def get_status(self, vehicle_name: str = ""):
        # AirSim 语义：空名返回默认（第一架）机状态
        name = vehicle_name or (self.vehicles[0] if self.vehicles else "")
        return SimpleNamespace(
            position_ned={"x": 1.0, "y": 2.0, "z": -3.0},
            attitude_rad={"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            extra={"vehicle_name": name},
            to_dict=lambda: {"vehicle_name": name, "position_ned": {"x": 1.0, "y": 2.0, "z": -3.0}},
        )


def _fleet_tools(controller: _FleetController) -> ToolCollector:
    collector = ToolCollector()
    register_core_tools(collector, controller, lambda data: data)
    return collector


# ── tool-level vehicle_name semantics ──


def test_empty_vehicle_name_targets_default_vehicle_only() -> None:
    controller = _FleetController()
    tools = _fleet_tools(controller)

    result = tools.tools["drone_takeoff"](altitude=5.0)

    assert result["status"] == "ok"
    assert result["vehicles"] == ["Drone1"]
    assert controller.calls == [("takeoff", "Drone1", 5.0)]


def test_all_vehicle_name_broadcasts_to_every_vehicle() -> None:
    controller = _FleetController()
    tools = _fleet_tools(controller)

    result = tools.tools["drone_takeoff"](altitude=5.0, vehicle_name="all")

    assert result["status"] == "ok"
    assert result["vehicles"] == ["Drone1", "Drone2"]
    assert controller.calls == [("takeoff", "Drone1", 5.0), ("takeoff", "Drone2", 5.0)]


def test_named_vehicle_targets_only_that_vehicle() -> None:
    controller = _FleetController()
    tools = _fleet_tools(controller)

    result = tools.tools["drone_arm"](vehicle_name="Drone2")

    assert result["status"] == "ok"
    assert result["vehicles"] == ["Drone2"]
    assert controller.calls == [("arm", "Drone2")]


def test_empty_vehicle_name_single_vehicle_backend_uses_that_vehicle() -> None:
    controller = _FleetController()
    controller.vehicles = ["px4_drone"]
    tools = _fleet_tools(controller)

    result = tools.tools["drone_hover"]()

    assert result["vehicles"] == ["px4_drone"]
    assert controller.calls == [("hover", "px4_drone")]


def test_partial_failure_stops_and_reports_error() -> None:
    class _FailingFleet(_FleetController):
        def arm(self, vehicle_name: str = "") -> bool:
            self.calls.append(("arm", vehicle_name))
            return vehicle_name != "Drone2"

    controller = _FailingFleet()
    tools = _fleet_tools(controller)

    result = tools.tools["drone_arm"](vehicle_name="all")

    assert result["status"] == "error"
    assert controller.calls == [("arm", "Drone1"), ("arm", "Drone2")]


# ── vehicles state model ──


def test_status_snapshot_includes_vehicles() -> None:
    controller = _FleetController()
    runtime = ToolRuntime(backend_id="airsim")
    runtime.backend_profile = runtime.backend_registry.require("airsim")
    runtime.controller = controller
    runtime.collector = _fleet_tools(controller)
    runtime.available = True

    snapshot = runtime.status_snapshot()

    assert snapshot["connected"] is True
    vehicles = snapshot.get("vehicles")
    assert isinstance(vehicles, list)
    assert {item["vehicle_name"] for item in vehicles} == {"Drone1", "Drone2"}
    # drone 字段保留（默认机，兼容现有消费者）
    assert snapshot["drone"]["vehicle_name"] == "Drone1"


def test_gcs_state_builds_vehicle_list() -> None:
    state = GroundStationState.from_tool_runtime(
        {
            "backend": "airsim",
            "backend_profile": {"id": "airsim", "capabilities": {}},
            "connected": True,
            "ready": True,
            "drone": {"vehicle_name": "Drone1", "position_ned": {"x": 0.0, "y": 0.0, "z": -3.0}},
            "vehicles": [
                {"vehicle_name": "Drone1", "position_ned": {"x": 0.0, "y": 0.0, "z": -3.0}},
                {"vehicle_name": "Drone2", "position_ned": {"x": 5.0, "y": 0.0, "z": -3.0}},
            ],
        }
    )
    assert len(state.vehicles) == 2
    assert state.vehicles[1].vehicle_id == "Drone2"


def test_gcs_state_falls_back_to_single_vehicle() -> None:
    state = GroundStationState.from_tool_runtime(
        {
            "backend": "px4_mavlink",
            "backend_profile": {"id": "px4_mavlink", "capabilities": {}},
            "connected": True,
            "ready": True,
            "drone": {"vehicle_name": "px4_drone", "position_ned": {"x": 0.0, "y": 0.0, "z": -3.0}},
        }
    )
    assert len(state.vehicles) == 1
    assert state.vehicles[0].vehicle_id == "px4_drone"


# ── approval reason carries vehicle context ──


def test_approval_reason_includes_vehicle() -> None:
    assert AgentRuntime._approval_reason("drone_takeoff", {"vehicle_name": "all"}) == (
        "governed high-risk tool call: drone_takeoff (vehicle=all)"
    )
    assert AgentRuntime._approval_reason("drone_takeoff", {}) == (
        "governed high-risk tool call: drone_takeoff"
    )


# ── GCS command manager forwards vehicle_id ──


class _RecordingTools:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    def execute(self, tool: str, params: dict, **kwargs) -> SimpleNamespace:
        self.executed.append((tool, dict(params)))
        return SimpleNamespace(
            ok=True,
            data={"status": "ok"},
            safety=None,
            to_dict=lambda: {"tool": tool, "ok": True, "data": {"status": "ok"}},
        )

    def validate(self, tool: str, params: dict) -> dict:
        return {"level": "safe"}

    def status_snapshot(self) -> dict:
        return {
            "backend": "airsim",
            "backend_profile": {"id": "airsim", "capabilities": {}},
            "connected": True,
            "ready": True,
            "drone": {},
            "vehicles": [],
        }


def test_command_manager_forwards_vehicle_id() -> None:
    tools = _RecordingTools()
    telemetry = ToolTelemetryManager(tools, lambda: {})
    safety = ToolSafetyManager(tools)
    manager = ToolCommandManager(tools, telemetry, safety)

    manager.takeoff(altitude_m=5.0, vehicle_id="Drone2")
    manager.hold(vehicle_id="all")

    assert tools.executed == [
        ("drone_takeoff", {"altitude": 5.0, "vehicle_name": "Drone2"}),
        ("drone_hover", {"vehicle_name": "all"}),
    ]
