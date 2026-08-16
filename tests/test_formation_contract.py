"""Integration tests for the formation_command tool: registration gating,
dispatch, conflict guard, risk/approval, planner visibility, and the
formation_stable task-contract criterion."""

from __future__ import annotations

import threading
import time

import pytest

from src.agent.agent_loop import AgentLoop
from src.agent.loop_types import LoopActionResult, LoopObservation, LoopState
from src.agent.runtime import AgentRuntime
from src.agent.tool_executor import ToolCallResult, ToolCollector, ToolRuntime
from src.modules.safety_validator import FlightConstraint, SafetyValidator


class _FakeStatus:
    def __init__(self, position: dict, flying: bool = True) -> None:
        self._position = position
        self._flying = flying

    def to_dict(self) -> dict:
        return {"position_ned": dict(self._position), "flying": self._flying}


class _FakeController:
    def __init__(self, vehicles: dict | None = None) -> None:
        self.positions: dict[str, dict] = vehicles or {
            "d0": {"x": 0.0, "y": 0.0, "z": -10.0},
            "d1": {"x": 0.0, "y": 0.0, "z": -10.0},
        }
        self.hover_calls: list[str] = []

    def list_vehicles(self) -> list[str]:
        return list(self.positions)

    def get_status(self, vehicle_name: str = ""):
        return _FakeStatus(self.positions.get(vehicle_name, {"x": 0, "y": 0, "z": -10}))

    def arm(self, vehicle_name: str = "") -> bool:
        return True

    def takeoff(self, altitude: float = 3.0, vehicle_name: str = "") -> bool:
        self.positions[vehicle_name]["z"] = -abs(altitude)
        return True

    def land(self, vehicle_name: str = "") -> bool:
        return True

    def hover(self, vehicle_name: str = "") -> bool:
        self.hover_calls.append(vehicle_name)
        return True

    def move_by_velocity(self, vx, vy, vz, duration=0.0, vehicle_name: str = "") -> bool:
        return True

    @property
    def is_connected(self) -> bool:
        return True


def _runtime_tools(backend_id: str = "airsim", vehicles: dict | None = None) -> ToolRuntime:
    rt = object.__new__(ToolRuntime)
    rt.backend_id = backend_id
    rt.controller = _FakeController(vehicles)
    rt.collector = ToolCollector()
    rt._formation = None
    rt._formation_stop_provider = None
    rt.safety = SafetyValidator(
        FlightConstraint(max_altitude=50.0, min_altitude=0.5, max_velocity=8.0, max_distance_from_home=100.0)
    )
    rt._lock = threading.RLock()
    rt.available = True
    rt.ensure_ready = lambda: True  # type: ignore[method-assign]
    rt._camera_source_enabled = lambda: False  # type: ignore[method-assign]
    return rt


# ---------------------------------------------------------------------------
# registration gating (M1 / S11)
# ---------------------------------------------------------------------------


def test_registration_gates_on_formation_capable_backends():
    rt = _runtime_tools("airsim", {"d0": {"x": 0.0, "y": 0.0, "z": -10.0}, "d1": {"x": 0.0, "y": 0.0, "z": -10.0}})
    collector, message = rt._ensure_formation_tools()
    assert collector is not None
    assert "formation_command" in collector.tools
    assert message == ""

    # PX4 MAVLink (single link, multiple systems) is formation-capable
    rt_mavlink = _runtime_tools("px4_mavlink")
    collector, message = rt_mavlink._ensure_formation_tools()
    assert collector is not None
    assert "formation_command" in collector.tools

    # PX4 ROS2 bridge is deferred (HTTP velocity semantics not validated yet)
    rt_ros2 = _runtime_tools("px4_ros2")
    collector, message = rt_ros2._ensure_formation_tools()
    assert collector is None
    assert "formation" in message


def test_registration_idempotent():
    rt = _runtime_tools()
    collector, _ = rt._ensure_formation_tools()
    collector2, _ = rt._ensure_formation_tools()
    assert collector is collector2


def test_flight_actions_gated_on_minimum_two_vehicles():
    rt = _runtime_tools("airsim", {"d0": {"x": 0.0, "y": 0.0, "z": -10.0}})
    collector, _ = rt._ensure_formation_tools()
    result = collector.tools["formation_command"](action="takeoff", altitude=10.0)
    assert "requires at least 2 vehicles" in result
    # status works with a single vehicle
    status = collector.tools["formation_command"](action="status")
    assert '"status": "ok"' in status


def test_tool_dispatch_routes_actions_to_controller():
    rt = _runtime_tools("airsim", {"d0": {"x": 0.0, "y": 0.0, "z": -10.0}, "d1": {"x": 0.0, "y": 0.0, "z": -10.0}})
    collector, _ = rt._ensure_formation_tools()
    cmd = collector.tools["formation_command"]
    out = cmd(action="set_drones", vehicle_ids="d0,d1")
    assert '"status": "ok"' in out and '"drones": ["d0", "d1"]' in out
    out = cmd(action="set_formation", formation_type="line", spacing=5.0)
    assert '"formation_type": "line"' in out
    out = cmd(action="takeoff", altitude=10.0)
    assert '"mode": "formation"' in out
    status = cmd(action="status")
    assert '"stable"' in status


def test_unknown_action_returns_helpful_error():
    rt = _runtime_tools()
    collector, _ = rt._ensure_formation_tools()
    out = collector.tools["formation_command"](action="nope")
    assert "unknown action" in out
    assert "valid_actions" in out


# ---------------------------------------------------------------------------
# conflicts / lifecycle (M5 / M2)
# ---------------------------------------------------------------------------


class _FormationTools:
    CONTROL_TOOLS = {"drone_fly_to", "drone_hover"}
    READ_ONLY_TOOLS = {"drone_get_status"}

    def __init__(self) -> None:
        self.active = False

    def formation_active(self) -> bool:
        return self.active


def test_formation_conflict_blocks_single_vehicle_control():
    rt = object.__new__(AgentRuntime)
    rt._lock = threading.RLock()
    rt._execution_thread_id = 0
    rt._execution_slot = threading.Lock()
    rt.tools = _FormationTools()
    rt.tools.active = True
    result = rt._execute_agent_tool("drone_fly_to", {"x": 1, "y": 2, "z": -3})
    assert result.ok is False
    assert result.error_code == "BLOCKED"
    assert "formation" in result.data["message"]


def test_formation_conflict_exempts_hover_land():
    rt = object.__new__(AgentRuntime)
    rt._lock = threading.RLock()
    rt._execution_thread_id = 0
    rt._execution_slot = threading.Lock()
    rt.tools = _FormationTools()
    rt.tools.active = True
    # hover is exempt from the conflict guard: it must reach the tool layer
    # and be executed (returns ok) rather than blocked
    executed = []

    def execute(name, params, dry_run=False, blocked_by_supervisor=False):
        executed.append(name)
        now = time.time()
        return ToolCallResult(name, dict(params), True, {"status": "ok"}, now, now)

    rt.tools.status_snapshot = lambda: {"backend_profile": {"capabilities": {"real_vehicle": False}}}

    class _Supervisor:
        def is_emergency_stopped(self):
            return False

    rt.supervisor = _Supervisor()
    rt.tools.execute = execute
    result = rt._execute_agent_tool("drone_hover", {})
    assert result.ok is True
    assert executed == ["drone_hover"]


def test_manual_return_home_blocked_during_formation():
    rt = object.__new__(AgentRuntime)
    rt.tools = _FormationTools()
    rt.tools.active = True
    result = rt._manual_return_home()
    assert result["ok"] is False
    assert "formation" in result["error"]


def test_reset_connection_shuts_down_formation():
    rt = _runtime_tools("airsim", {"d0": {"x": 0.0, "y": 0.0, "z": -10.0}, "d1": {"x": 0.0, "y": 0.0, "z": -10.0}})
    collector, _ = rt._ensure_formation_tools()
    collector.tools["formation_command"](action="set_drones", vehicle_ids="d0,d1")
    collector.tools["formation_command"](action="set_formation")
    collector.tools["formation_command"](action="takeoff", altitude=10.0)
    assert rt.formation_active() is True
    rt.reset_connection()
    assert rt.formation_active() is False
    assert rt._formation is None


# ---------------------------------------------------------------------------
# risk / approval (S5)
# ---------------------------------------------------------------------------


def test_risk_level_high_for_flight_actions_on_real_vehicle():
    rt = object.__new__(AgentRuntime)
    for action in ("takeoff", "move_center", "set_formation", "coverage_start"):
        level = rt._tool_risk_level("formation_command", {"real_vehicle": True}, params={"action": action})
        assert level == "high", action
    level = rt._tool_risk_level("formation_command", {"real_vehicle": True}, params={"action": "status"})
    assert level != "high"


def test_approval_reason_includes_vehicles():
    reason = AgentRuntime._approval_reason("formation_command", {"action": "takeoff", "vehicle_ids": "d0,d1"})
    assert "action=takeoff" in reason
    assert "vehicles=d0,d1" in reason


# ---------------------------------------------------------------------------
# planner visibility (S4)
# ---------------------------------------------------------------------------


def test_planner_atomic_tools_include_formation_for_keywords():
    rt = object.__new__(AgentRuntime)
    allowed = rt._allowed_planner_atomic_tools("编队飞行到 (50,50)", set(), {"flight_control": True})
    assert "formation_command" in allowed
    allowed_en = rt._allowed_planner_atomic_tools("formation fly to 50,50", set(), {"flight_control": True})
    assert "formation_command" in allowed_en


# ---------------------------------------------------------------------------
# task contract criterion
# ---------------------------------------------------------------------------


def test_formation_stable_criterion():
    loop = object.__new__(AgentLoop)
    state = LoopState(run_id="run", command="编队")
    state.results.append(
        LoopActionResult(
            step_index=1,
            tool="formation_command",
            params={"action": "status"},
            ok=True,
            data={"status": "ok", "mode": "formation", "stable": True},
            duration_ms=1.0,
        )
    )
    base = loop._verify_criterion({"metric": "formation_stable"}, state, LoopObservation(1, {}))
    assert base["satisfied"] is True

    unstable = LoopState(run_id="run", command="编队")
    unstable.results.append(
        LoopActionResult(
            step_index=1,
            tool="formation_command",
            params={"action": "status"},
            ok=True,
            data={"status": "ok", "mode": "formation", "stable": False},
            duration_ms=1.0,
        )
    )
    assert loop._verify_criterion({"metric": "formation_stable"}, unstable, LoopObservation(1, {}))["satisfied"] is False


# ---------------------------------------------------------------------------
# post-review fixes: executor-level guard, coverage geofence, last-status rule
# ---------------------------------------------------------------------------


def test_executor_level_guard_blocks_direct_tool_calls():
    """M5: the conflict guard must live in ToolRuntime.execute so the GCS panel
    (which bypasses the agent runtime) cannot fight the formation loop."""
    rt = _runtime_tools("airsim", {"d0": {"x": 0.0, "y": 0.0, "z": -10.0}, "d1": {"x": 0.0, "y": 0.0, "z": -10.0}})
    collector, _ = rt._ensure_formation_tools()
    collector.tools["formation_command"](action="set_drones", vehicle_ids="d0,d1")
    collector.tools["formation_command"](action="set_formation")
    collector.tools["formation_command"](action="takeoff", altitude=10.0)
    assert rt.formation_active() is True
    result = rt.execute("drone_fly_to", {"x": 5, "y": 5, "z": -10}, allow_reconnect=False)
    assert result.ok is False
    assert result.error_code == "BLOCKED"
    assert "formation" in result.data["message"]
    # hover stays exempt even through the executor
    collector.tools["drone_hover"] = lambda **kwargs: '{"status": "ok"}'
    result = rt.execute("drone_hover", {})
    assert result.ok is True


def test_coverage_plan_geofence_validation():
    """M4: coverage areas outside the geofence must be blocked like drone_fly_to."""
    rt = _runtime_tools("airsim", {"d0": {"x": 0.0, "y": 0.0, "z": -10.0}, "d1": {"x": 0.0, "y": 0.0, "z": -10.0}})
    inside = rt.validate("formation_command", {"action": "coverage_plan", "area_width": 20, "area_height": 20, "area_x": 10, "area_y": 10, "area_altitude": 10})
    assert inside["level"] != "danger"
    outside = rt.validate("formation_command", {"action": "coverage_plan", "area_width": 200, "area_height": 200, "area_x": 2000, "area_y": 0, "area_altitude": 10})
    assert outside["level"] == "danger"
    circle_outside = rt.validate("formation_command", {"action": "coverage_plan", "area_shape": "circle", "area_radius": 100, "area_x": 0, "area_y": 150, "area_altitude": 10})
    assert circle_outside["level"] == "danger"


def test_formation_stable_uses_latest_status():
    """S4: a stale stable=true from an earlier status must not satisfy the
    criterion once a later status reports unstable."""
    loop = object.__new__(AgentLoop)
    state = LoopState(run_id="run", command="编队")
    state.results.append(
        LoopActionResult(
            step_index=1,
            tool="formation_command",
            params={"action": "status"},
            ok=True,
            data={"status": "ok", "mode": "formation", "stable": True},
            duration_ms=1.0,
        )
    )
    state.results.append(
        LoopActionResult(
            step_index=2,
            tool="formation_command",
            params={"action": "status"},
            ok=True,
            data={"status": "ok", "mode": "formation", "stable": False},
            duration_ms=1.0,
        )
    )
    criterion = loop._verify_criterion({"metric": "formation_stable"}, state, LoopObservation(1, {}))
    assert criterion["satisfied"] is False
