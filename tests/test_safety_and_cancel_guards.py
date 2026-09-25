"""Regression tests for the safety-gate, cancellation and telemetry-trust fixes.

Each test here encodes a defect that was live in the tree:

- the safety layer used to *clamp* danger-level violations instead of blocking
  them, so "fly to 10 m up" (positive z) silently became a 0.5 m AGL flight;
- NaN sailed through every bound check and reached the flight controller;
- ``drone_fly_velocity`` was validated as an instantaneous vector, so
  5 m/s down for 60 s (ground strike) or 8 m/s for 300 s (2.4 km) passed;
- the operator-approval gate could never fire on the px4_ros2 backend;
- mid-risk position/mode tools needed no approval on a real vehicle;
- the GCS "start mission" path bypassed the emergency-stop latch and the
  MAVLink start auto-arms;
- an interrupted fixed sequence kept flying, and a paused-then-cancelled run
  wedged the execution slot forever;
- a chat message cleared a live run's cancel flag;
- approving after a cancel executed the high-risk tool anyway;
- a dead AirSim link reported a successful status read;
- a camera timeout tore down and reconnected the *flight* link;
- ``bool("false")`` completed a task that never executed an action.
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from src.agent.agent_loop import AgentLoop
from src.agent.llm import LLMMissionPlanner, LLMUnavailableError, _as_bool, _extract_json
from src.agent.loop_types import LoopDecision
from src.agent.runtime import AgentRuntime, RunState
from src.agent.tool_executor import ToolCallResult, ToolCollector, ToolRuntime
from src.agent.backends import create_builtin_backend_registry
from _runtime_factories import agent_runtime, status_controller, tool_runtime
from src.modules.safety_validator import FlightConstraint, SafetyValidator

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _rt(collector: ToolCollector, *, controller: Any = None) -> ToolRuntime:
    # shared shell: see tests/_runtime_factories.py
    return tool_runtime(collector, controller)


def _shell_runtime(**attrs: Any) -> AgentRuntime:
    """AgentRuntime shell for _run_plan / _execute_agent_tool tests."""
    return agent_runtime(**attrs)


def _noop_tool(**kwargs) -> str:
    return json.dumps({"status": "ok"})


# ---------------------------------------------------------------------------
# safety gate: block, never silently clamp a danger violation
# ---------------------------------------------------------------------------


def test_positive_z_fly_to_is_blocked_not_clamped_to_half_metre():
    collector = ToolCollector()
    collector.tools["drone_fly_to"] = _noop_tool
    rt = _rt(collector)

    result = rt.execute("drone_fly_to", {"x": 10.0, "y": 0.0, "z": 10.0})

    assert result.ok is False
    assert result.error_code == "SAFETY_BLOCKED"
    # The dangerous old behaviour: params were rewritten to z = -min_altitude
    # (-0.5 m) and the move was executed at 0.5 m AGL.
    assert result.params.get("z") == 10.0, "parameters must not be silently rewritten"
    assert result.data["suggested_params"]["z"] == -0.5


def test_geofence_violation_is_blocked_not_clamped():
    collector = ToolCollector()
    collector.tools["drone_fly_to"] = _noop_tool
    rt = _rt(collector)

    result = rt.execute("drone_fly_to", {"x": 500.0, "y": 0.0, "z": -5.0})

    assert result.ok is False
    assert result.error_code == "SAFETY_BLOCKED"
    assert "围栏" in " ".join(result.data["violations"])
    assert result.params.get("x") == 500.0


def test_over_altitude_still_auto_clamps_as_warning():
    """Warnings stay auto-corrected: they converge to a safe value instead of
    changing the goal, so the mission still runs."""
    collector = ToolCollector()
    collector.tools["drone_takeoff"] = _noop_tool
    rt = _rt(collector)

    result = rt.execute("drone_takeoff", {"altitude": 120.0})

    assert result.ok is True
    assert result.params["altitude"] == 50.0


# ---------------------------------------------------------------------------
# non-finite parameters
# ---------------------------------------------------------------------------


def test_nan_position_is_rejected_before_any_bound_check():
    result = SafetyValidator().validate_position(float("nan"), 0.0, -3.0)

    assert result.level == "danger"
    assert result.is_safe is False
    assert result.corrected is None, "a NaN must never come with a 'corrected' value"
    assert any("有限" in item for item in result.violations)


def test_nan_velocity_is_rejected_without_nan_correction():
    result = SafetyValidator().validate_velocity(float("nan"), 0.0, 0.0)

    assert result.level == "danger"
    assert result.corrected is None


def test_nan_tool_param_is_blocked_by_the_dispatcher():
    collector = ToolCollector()
    collector.tools["drone_move_relative"] = _noop_tool
    rt = _rt(collector, controller=status_controller())

    result = rt.execute("drone_move_relative", {"forward_m": float("nan")})

    assert result.ok is False
    assert result.error_code == "SAFETY_BLOCKED"
    assert any("有限" in item for item in result.data["violations"])


def test_nan_string_param_is_blocked_too():
    collector = ToolCollector()
    collector.tools["drone_fly_velocity"] = _noop_tool
    rt = _rt(collector, controller=status_controller())

    result = rt.execute("drone_fly_velocity", {"vx": "nan", "vy": 0.0, "vz": 0.0})

    assert result.ok is False
    assert result.error_code == "SAFETY_BLOCKED"


# ---------------------------------------------------------------------------
# drone_fly_velocity must validate the displacement duration implies
# ---------------------------------------------------------------------------


def test_fly_velocity_descent_into_the_ground_is_blocked():
    """5 m/s down for 60 s from 3 m: the instantaneous speed check passes."""
    collector = ToolCollector()
    collector.tools["drone_fly_velocity"] = _noop_tool
    rt = _rt(collector, controller=status_controller(z=-3.0))

    result = rt.execute("drone_fly_velocity", {"vx": 0.0, "vy": 0.0, "vz": 5.0, "duration": 60.0})

    assert result.ok is False
    assert result.error_code == "SAFETY_BLOCKED"


def test_fly_velocity_long_run_into_the_fence_is_blocked():
    """8 m/s for 300 s travels ~2.4 km with a 100 m geofence."""
    collector = ToolCollector()
    collector.tools["drone_fly_velocity"] = _noop_tool
    rt = _rt(collector, controller=status_controller(z=-3.0))

    result = rt.execute("drone_fly_velocity", {"vx": 8.0, "vy": 0.0, "vz": 0.0, "duration": 300.0})

    assert result.ok is False
    assert result.error_code == "SAFETY_BLOCKED"


def test_fly_velocity_short_bounded_burst_is_allowed():
    collector = ToolCollector()
    collector.tools["drone_fly_velocity"] = _noop_tool
    rt = _rt(collector, controller=status_controller(z=-3.0))

    result = rt.execute("drone_fly_velocity", {"vx": 1.0, "vy": 0.0, "vz": 0.0, "duration": 2.0})

    assert result.ok is True


def test_fly_velocity_without_position_readback_bounds_the_whole_displacement():
    """No telemetry: the implied displacement must still fit inside the fence."""
    collector = ToolCollector()
    collector.tools["drone_fly_velocity"] = _noop_tool
    rt = _rt(collector, controller=None)

    blocked = rt.execute("drone_fly_velocity", {"vx": 8.0, "vy": 0.0, "vz": 0.0, "duration": 300.0})
    allowed = rt.execute("drone_fly_velocity", {"vx": 1.0, "vy": 0.0, "vz": 0.0, "duration": 5.0})

    assert blocked.ok is False
    assert allowed.ok is True


# ---------------------------------------------------------------------------
# approval gate reachability and risk classification
# ---------------------------------------------------------------------------


def _runtime_stub(**attrs: Any) -> AgentRuntime:
    # shared shell: every runtime field is initialised by _init_runtime_state(),
    # so a test only names what it cares about.
    return agent_runtime(**attrs)


def test_real_vehicle_approval_is_not_restricted_to_mavlink_backend():
    """px4_ros2 used to be excluded, so the gate could never fire on a real
    drone driven through the ROS2 gateway."""
    rt = _rt(ToolCollector())
    rt.backend_id = "px4_ros2"
    rt._real_vehicle = True
    rt.backend_profile = SimpleNamespace(
        to_public_dict=lambda: {
            "backend": "px4_ros2",
            "capabilities": {"flight_control": True, "real_vehicle": False},
        }
    )
    rt._camera_capabilities = lambda caps: dict(caps)

    profile = rt._public_backend_profile()

    assert profile["capabilities"]["real_vehicle"] is True
    assert profile["capabilities"]["requires_operator_approval"] is True


def test_position_and_mode_tools_need_approval_on_a_real_vehicle():
    runtime = _runtime_stub()
    capabilities = {"real_vehicle": True}
    for tool in ("drone_fly_to", "drone_move_relative", "drone_fly_path", "drone_set_mode", "drone_disarm"):
        assert runtime._tool_risk_level(tool, capabilities) == "high", tool
    assert runtime._tool_risk_level("drone_disarm", capabilities) == "high"


def test_same_tools_stay_medium_on_a_simulation():
    runtime = _runtime_stub()
    capabilities = {"real_vehicle": False}
    assert runtime._tool_risk_level("drone_fly_to", capabilities) == "medium"


def test_missing_tool_card_for_a_control_tool_fails_safe_to_high():
    """drone_dispatch_* are in CONTROL_TOOLS but absent from TOOL_CARDS; the old
    default of "low" meant no approval gate for a flight action."""
    runtime = _runtime_stub()
    runtime.tools = SimpleNamespace(
        CONTROL_TOOLS={"drone_dispatch_path"},
        READ_ONLY_TOOLS=set(),
    )

    assert runtime._tool_risk_level("drone_dispatch_path", {"real_vehicle": False}) == "high"


# ---------------------------------------------------------------------------
# fixed-sequence cancellation / pause
# ---------------------------------------------------------------------------


def test_fixed_sequence_stops_at_the_next_step_when_cancelled():
    steps = [_step("s01", "drone_takeoff"), _step("s02", "drone_fly_to"), _step("s03", "drone_land")]
    plan = _plan(steps)
    run = RunState(run_id="run_cancel", command="fly", intent="", summary="", plan=plan, execute=True)

    executed: list[str] = []
    runtime = _shell_runtime(
        _cancelled_request_ids={"run_cancel"},
        _cancel_requested_at=0.0,
        _cancel_requested_run_id="run_cancel",
        _current=run,
    )
    runtime.supervisor = SimpleNamespace(
        should_pause=lambda: False,
        is_emergency_stopped=lambda: False,
    )
    runtime.execute = True
    runtime._backend_generation = 0
    runtime.tools = SimpleNamespace(
        CONTROL_TOOLS={"drone_takeoff", "drone_fly_to", "drone_land"},
        READ_ONLY_TOOLS=set(),
        backend_id="fake",
        status_snapshot=lambda: {"drone": {}, "connected": False},
        execute=lambda *a, **k: None,
    )
    runtime._preapprove_first_high_risk_tool = lambda run: None
    runtime._capture_start_telemetry = lambda run: None
    runtime._maybe_skip_idempotent_step = lambda step: None
    runtime._execute_agent_tool = lambda tool, params, **k: (
        executed.append(tool) or ToolCallResult(tool, params, True, {"status": "ok"}, time.time(), time.time())
    )
    runtime._record_task_tool_result = lambda *a, **k: None
    runtime.memory = SimpleNamespace(remember_tool_call=lambda *a, **k: None)
    runtime._remember_task_start = lambda *a, **k: None
    runtime._remember_position_from_payload = lambda *a, **k: None
    runtime._publish_run_update = lambda *a, **k: None
    runtime._update_execution_trace_for_step = lambda *a, **k: None
    runtime._update_execution_trace_after_step = lambda *a, **k: None
    runtime._append_event = lambda *a, **k: None
    runtime._append_thought = lambda *a, **k: None
    runtime._append_process = lambda *a, **k: None
    runtime._update_assistant_message = lambda *a, **k: None
    runtime._message_details = lambda run: {}
    runtime._progress_message = lambda run: ""
    runtime._verify_run_outcome = lambda run: {}
    runtime._upsert_verify_row = lambda *a, **k: None
    runtime.task_runs = None
    runtime._remember_task_end = lambda *a, **k: None

    AgentRuntime._run_plan(runtime, run, finalize=False, remember=False)

    assert executed == [], "a cancelled run must not execute any remaining step"
    assert run.status == "cancelled"
    assert run.failure_reason == "operator cancelled task"


def test_paused_then_cancelled_run_does_not_wedge_the_slot():
    """The pause poll now also exits on cancel, so the slot is released."""
    steps = [_step("s01", "drone_takeoff")]
    plan = _plan(steps)
    run = RunState(run_id="run_pause_cancel", command="fly", intent="", summary="", plan=plan, execute=True)

    paused = {"value": True}
    runtime = _shell_runtime(
        _cancelled_request_ids={"run_pause_cancel"},
        _cancel_requested_at=0.0,
        _cancel_requested_run_id="run_pause_cancel",
        _current=run,
    )
    runtime.supervisor = SimpleNamespace(
        should_pause=lambda: paused["value"],
        is_emergency_stopped=lambda: False,
    )
    runtime.tools = SimpleNamespace(
        CONTROL_TOOLS={"drone_takeoff"},
        READ_ONLY_TOOLS=set(),
        backend_id="fake",
        status_snapshot=lambda: {"drone": {}, "connected": False},
        execute=lambda *a, **k: None,
    )
    runtime._preapprove_first_high_risk_tool = lambda run: None
    runtime._capture_start_telemetry = lambda run: None
    runtime._publish_run_update = lambda *a, **k: None
    runtime._append_event = lambda *a, **k: None
    runtime._publish = lambda *a, **k: None
    runtime._append_thought = lambda *a, **k: None
    runtime._append_process = lambda *a, **k: None
    runtime._update_assistant_message = lambda *a, **k: None
    runtime._message_details = lambda run: {}
    runtime._progress_message = lambda run: ""
    runtime._verify_run_outcome = lambda run: {}
    runtime._upsert_verify_row = lambda *a, **k: None
    runtime._remember_task_end = lambda *a, **k: None

    started = time.time()
    AgentRuntime._run_plan(runtime, run, finalize=False, remember=False)
    elapsed = time.time() - started

    assert elapsed < 5.0, "the pause poll must not spin forever after a cancel"
    assert run.status == "cancelled"


# ---------------------------------------------------------------------------
# cancel identity: a chat message must not clear a live run's cancel
# ---------------------------------------------------------------------------


def test_is_run_cancelled_is_bound_to_the_run_id():
    runtime = _runtime_stub(
        _lock=threading.RLock(),
        _cancelled_request_ids=set(),
        _cancel_requested=threading.Event(),
        _cancel_requested_at=time.time(),
        _cancel_requested_run_id="run_A",
    )
    runtime._cancel_requested.set()

    assert runtime._is_run_cancelled("run_A") is True
    # A different run must not inherit the previous run's cancel.
    assert runtime._is_run_cancelled("run_B") is False


def test_active_run_cancelled_uses_the_cancelled_id_set():
    cancel = threading.Event()
    runtime = _runtime_stub(
        _lock=threading.RLock(),
        _cancelled_request_ids={"run_A"},
        _cancel_requested=cancel,          # flag already cleared by a later submit
        _cancel_requested_at=0.0,
        _cancel_requested_run_id="",
        _current=SimpleNamespace(run_id="run_A"),
    )

    assert runtime._active_run_cancelled() is True, (
        "clearing the global flag (e.g. a chat message) must not un-cancel the run"
    )


def test_flight_abort_window_expires_so_a_later_takeoff_is_not_interrupted():
    cancel = threading.Event()
    cancel.set()
    runtime = _runtime_stub(
        _lock=threading.RLock(),
        _cancel_requested=cancel,
        _cancel_requested_at=time.time() - 3600.0,
        _cancel_requested_run_id="",
        supervisor=SimpleNamespace(is_emergency_stopped=lambda: False, resume=lambda: None),
    )

    assert runtime._flight_abort_requested() is False, (
        "a stale cancel flag must not interrupt the operator's next manual takeoff"
    )


# ---------------------------------------------------------------------------
# approval gate vs cancel
# ---------------------------------------------------------------------------


class _ApprovalRun:
    def __init__(self) -> None:
        self.run_id = "run_approval"
        self.command = "land"
        self.status = "awaiting_approval"
        self.phase = "awaiting_approval"
        self.failure_reason = ""
        self.finished_at = 0.0


def test_approval_wait_aborts_when_the_run_was_cancelled():
    run = _ApprovalRun()
    cancel = threading.Event()
    cancel.set()
    runtime = _runtime_stub(
        _lock=threading.RLock(),
        _pending_approvals={},
        _cancelled_request_ids={"run_approval"},
        _cancel_requested=cancel,
        _cancel_requested_at=time.time(),
        _cancel_requested_run_id="run_approval",
        _current=run,
    )
    runtime.supervisor = SimpleNamespace(is_emergency_stopped=lambda: False)
    runtime._append_event = lambda *a, **k: None
    runtime._publish_run_update = lambda *a, **k: None
    runtime._publish = lambda *a, **k: None
    runtime._cleanup_approval = lambda run_id: None

    approved = AgentRuntime._await_tool_approval(runtime, run, "drone_land", {}, "high")

    assert approved is False
    assert run.status == "cancelled"


def test_cancel_rejects_and_wakes_a_pending_approval():
    request = SimpleNamespace(run_id="run_x", approved=None, event=threading.Event())
    run = SimpleNamespace(
        run_id="run_x",
        mode="execute",
        status="awaiting_approval",
        phase="awaiting_approval",
        failure_reason="",
        finished_at=0.0,
        progress=0.0,
        assistant_message="",
    )
    runtime = _shell_runtime(_current=run, _pending_approvals={"run_x": request})
    runtime.supervisor = SimpleNamespace(resume=lambda: None)
    runtime.tools = SimpleNamespace(status_snapshot=lambda: {"backend_profile": {"capabilities": {}}})
    runtime._append_event = lambda *a, **k: None
    runtime._update_assistant_message = lambda *a, **k: None
    runtime._publish_run_update = lambda *a, **k: None
    runtime._message_details = lambda run: {}

    result = AgentRuntime._cancel_active_work(runtime)

    assert request.event.is_set(), "the waiting worker must be woken"
    assert request.approved is False, "the pending approval must be rejected"
    assert "run_x" in result["rejected_approvals"]


# ---------------------------------------------------------------------------
# telemetry trust
# ---------------------------------------------------------------------------


def test_error_code_reaches_the_agent_loop_result_data():
    loop = object.__new__(AgentLoop)
    loop.execute_tool = None
    loop.should_stop = None
    loop.tools = SimpleNamespace(
        execute=lambda name, params, dry_run=False, **kwargs: ToolCallResult(
            name, params, False, {"status": "error", "message": "boom"},
            time.time(), time.time(), error_code="NOT_CONNECTED",
        )
    )

    payload = AgentLoop._execute_action(loop, LoopDecision(action="drone_get_status", params={}), dry_run=False)

    assert payload["data"]["error_code"] == "NOT_CONNECTED"
    assert payload["ok"] is False


def test_camera_tools_never_trigger_a_flight_link_reconnect():
    rt = _rt(ToolCollector())

    assert rt._requires_vehicle_connection("airsim_take_photo") is False
    assert rt._requires_vehicle_connection("airsim_get_depth_map") is False
    assert rt._requires_vehicle_connection("drone_fly_to") is True


def test_generic_timeout_text_is_not_treated_as_link_loss():
    rt = _rt(ToolCollector())

    assert rt._is_connection_error({"message": "operation timeout after 30s"}) is False
    assert rt._is_connection_error({"message": "not connected"}) is True
    assert rt._is_connection_error({"message": "connection refused"}) is True


# ---------------------------------------------------------------------------
# LLM protocol robustness
# ---------------------------------------------------------------------------


def test_bool_strings_do_not_flip_meaning():
    assert _as_bool("false") is False
    assert _as_bool("False") is False
    assert _as_bool("0") is False
    assert _as_bool("no") is False
    assert _as_bool(True) is True
    assert _as_bool("true") is True
    assert _as_bool("是") is True
    assert _as_bool(None) is False


def test_extract_json_pulls_json_out_of_prose_and_fences():
    assert _extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _extract_json('Sure, here it is: {"a": 1}') == '{"a": 1}'
    assert _extract_json('{"a": 1}') == '{"a": 1}'
    assert _extract_json('```json{"a": 1}```') == '{"a": 1}'
    assert _extract_json('[{"a": 1}]') == '[{"a": 1}]'
    assert _extract_json('no json here') == 'no json here'


def test_native_empty_response_is_not_reported_as_complete():
    planner = object.__new__(LLMMissionPlanner)

    with pytest.raises(LLMUnavailableError):
        planner._decision_from_tool_calls([], "", set())

    decision = planner._decision_from_tool_calls([], "all done, nothing to do", set())
    assert decision.is_complete is True


def test_skill_markdown_survives_the_result_compactor():
    """The activation payload carries a 4-9 KB SKILL.md body; the compactor used
    to cut the serialized JSON at 1200 chars, handing the model invalid JSON."""
    planner = object.__new__(LLMMissionPlanner)
    body = "# Region search\n" + ("step line\n" * 700)

    trimmed = planner._trim_data(
        {"status": "activated", "skill": "skill:search", "markdown": body, "message": "loaded"}
    )

    assert trimmed["markdown"] == body
    assert trimmed["skill"] == "skill:search"


def test_oversized_non_skill_result_is_still_summarised():
    planner = object.__new__(LLMMissionPlanner)

    trimmed = planner._trim_data({"status": "ok", "blob": "x" * 5000})

    assert "summary" in trimmed


# ---------------------------------------------------------------------------
# envelope watchdog scope
# ---------------------------------------------------------------------------


def test_envelope_guard_stays_armed_while_waiting_for_approval():
    """awaiting_approval is a live state; breaking out of the loop there used to
    disable the watchdog for the rest of the plan-execute run."""
    guard = object.__new__(AgentRuntime)
    guard._envelope_stop = threading.Event()
    guard._lock = threading.RLock()
    guard._current = SimpleNamespace(run_id="run_g", status="awaiting_approval")
    guard.supervisor = SimpleNamespace(is_emergency_stopped=lambda: False)
    guard.status_snapshot_calls = 0

    def _snapshot() -> dict:
        guard.status_snapshot_calls += 1
        if guard.status_snapshot_calls >= 2:
            guard._envelope_stop.set()
        return {"drone": {"flying": False}}

    guard.tools = SimpleNamespace(status_snapshot=_snapshot)
    guard._append_event = lambda *a, **k: None
    guard._attempt_hold_position = lambda *a, **k: None

    # 包线现在由 _envelope_profile 按任务类型给出，循环只负责采样与判定
    AgentRuntime._envelope_guard_loop(guard, "run_g", 8.0, 70.0)

    assert guard.status_snapshot_calls >= 2, "the guard exited while approval was pending"


def test_backend_generation_mismatch_blocks_control_tools():
    run = SimpleNamespace(run_id="run_b", backend_generation=3)
    runtime = _shell_runtime(_backend_generation=4, _current=run)
    runtime.tools = SimpleNamespace(
        backend_id="fake",
        CONTROL_TOOLS={"drone_fly_to"},
        READ_ONLY_TOOLS=set(),
        status_snapshot=lambda: {"backend_profile": {"capabilities": {}}},
    )
    runtime._blocked_tool_result = lambda tool, params, message: ToolCallResult(
        tool, params, False, {"status": "blocked", "message": message}, time.time(), time.time(),
        error_code="BLOCKED",
    )

    result = AgentRuntime._execute_agent_tool(runtime, "drone_fly_to", {"x": 1.0}, run=run)

    assert result.ok is False
    assert "后端已被切换" in result.data["message"]


# ---------------------------------------------------------------------------
# execution-slot ownership
# ---------------------------------------------------------------------------


def test_active_execute_run_ignores_a_non_execution_run():
    runtime = _runtime_stub(
        _lock=threading.RLock(),
        _execution_slot=threading.Lock(),
        _current=SimpleNamespace(run_id="run_plan", status="running"),
    )

    assert runtime._active_execute_run() is None


def test_run_in_progress_blocks_backend_and_session_changes():
    slot = threading.Lock()
    slot.acquire()
    runtime = _runtime_stub(
        _lock=threading.RLock(),
        _execution_slot=slot,
        _current=SimpleNamespace(run_id="run_exec", status="running"),
    )

    blocked = runtime._run_in_progress_error("切换飞行后端")

    assert blocked is not None and blocked["ok"] is False
    slot.release()


# ---------------------------------------------------------------------------
# memory hygiene
# ---------------------------------------------------------------------------


def test_memory_clear_removes_risk_events_and_does_not_raise(tmp_path):
    from src.agent.memory import AgentMemory

    memory = AgentMemory(data_dir=tmp_path)
    memory.remember_mission(
        {
            "command": "patrol",
            "summary": "patrol aborted",
            "status": "failed",
            "failure_reason": "battery low",
            "tool_sequence": ["drone_takeoff"],
        }
    )

    removed = memory.clear()

    assert removed["risk_events"] >= 1
    persisted = json.loads((tmp_path / "memory.json").read_text(encoding="utf-8"))
    assert persisted["risk_events"] == []
    assert persisted["missions"] == []
    assert memory.guidance()["recent_risk_events"] == []


def test_malformed_memory_rows_are_dropped_instead_of_crashing_recall(tmp_path):
    from src.agent.memory import AgentMemory

    path = tmp_path / "memory.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "missions": [12345, "not-a-dict", {"command": "ok", "timestamp": "not-a-number"}],
                "lessons": {},
                "facts": {"good": {"value": "v"}, "bad": "not-a-dict"},
            }
        ),
        encoding="utf-8",
    )

    memory = AgentMemory(data_dir=tmp_path)
    hits = memory.recall("anything")

    assert isinstance(hits, list)


# ---------------------------------------------------------------------------
# helpers for plan construction
# ---------------------------------------------------------------------------


def _step(step_id: str, tool: str):
    from src.agent.planner import MissionStep

    return MissionStep(id=step_id, label=tool, tool=tool)


def _plan(steps):
    from src.agent.planner import MissionPlan

    return MissionPlan(
        run_id="run_x", command="fly", intent="test", summary="test", steps=list(steps)
    )


# ---------------------------------------------------------------------------
# GCS mission start must respect the emergency-stop latch
# ---------------------------------------------------------------------------


class _GcsTools:
    """ToolRuntime stand-in that records which tools were dispatched."""

    def __init__(self, names: list[str]) -> None:
        self._names = list(names)
        self.calls: list[str] = []

    def list_tools(self) -> list[dict[str, str]]:
        return [{"name": name} for name in self._names]

    def execute(self, name: str, params: dict | None = None, **kwargs: Any) -> ToolCallResult:
        self.calls.append(name)
        return ToolCallResult(
            name, dict(params or {}), True, {"status": "ok"}, time.time(), time.time()
        )


class _GcsSafety:
    def __init__(self, *, emergency_stop: bool) -> None:
        self._stop = emergency_stop

    def state(self) -> SimpleNamespace:
        return SimpleNamespace(emergency_stop=self._stop, paused=False, details={})


def _mission_manager(*, emergency_stop: bool) -> tuple[Any, _GcsTools]:
    from src.gcs.mission import MissionPlanDraft
    from src.gcs.services import ToolMissionManager

    tools = _GcsTools(["drone_upload_mission", "drone_start_mission"])
    manager = ToolMissionManager(tools, telemetry=SimpleNamespace(), safety=_GcsSafety(emergency_stop=emergency_stop))
    manager.set_draft(MissionPlanDraft(items=[], vehicle="PX4", name="test"))
    manager._state.uploaded = True
    manager._state.details = {"execution_mode": "native"}
    return manager, tools


def test_gcs_mission_start_is_refused_while_emergency_stop_is_latched():
    """The UI/GCS path used to call drone_start_mission without the supervisor
    gate, and MAVLink start auto-arms a disarmed vehicle."""
    manager, tools = _mission_manager(emergency_stop=True)

    result = manager.start()

    assert result.ok is False
    assert "emergency stop" in result.message
    assert "drone_start_mission" not in tools.calls, "the mission must not be started"


def test_gcs_mission_start_proceeds_when_no_emergency_stop():
    manager, tools = _mission_manager(emergency_stop=False)

    result = manager.start()

    assert result.ok is True
    assert tools.calls == ["drone_start_mission"]


# ---------------------------------------------------------------------------
# no-fly zones: configured, parsed and actually enforced
# ---------------------------------------------------------------------------


def test_parse_no_fly_zones_accepts_json_and_list_and_skips_garbage():
    from src.agent.tool_executor import parse_no_fly_zones

    assert parse_no_fly_zones("") == []
    assert parse_no_fly_zones(None) == []
    assert parse_no_fly_zones("{not json") == []
    assert parse_no_fly_zones([{"x": 0, "y": 0, "radius": 5}]) == [
        {"x": 0.0, "y": 0.0, "radius": 5.0}
    ]
    assert parse_no_fly_zones('[{"x": 1, "y": 2, "radius": 3, "name": "tower"}]') == [
        {"x": 1.0, "y": 2.0, "radius": 3.0, "name": "tower"}
    ]
    # invalid entries are skipped, not raised: a broken safety config must not
    # stop the ground station from starting
    assert parse_no_fly_zones('[{"x": 1, "y": 2}, {"x": 5, "y": 5, "radius": 0}]') == []
    assert parse_no_fly_zones('[{"x": "nan", "y": 0, "radius": 5}]') == []


def _rt_with_zone(zone: dict, *, z: float = -10.0) -> ToolRuntime:
    rt = _rt(ToolCollector(), controller=status_controller(z=z))
    rt.safety = SafetyValidator(
        FlightConstraint(
            max_altitude=50.0,
            min_altitude=0.5,
            max_velocity=8.0,
            max_distance_from_home=100.0,
            no_fly_zones=[zone],
        )
    )
    return rt


def test_no_fly_zone_blocks_a_flight_that_crosses_it():
    """The segment/circle check existed but no production path ever called it,
    so a zone in the config would have had no effect on a transit."""
    rt = _rt_with_zone({"x": 20.0, "y": 0.0, "radius": 10.0})

    safety = rt.validate("drone_fly_to", {"x": 40.0, "y": 0.0, "z": -10.0})

    assert safety["level"] == "danger"
    assert any("禁飞区" in item for item in safety["violations"])


def test_no_fly_zone_does_not_block_a_flight_that_goes_around_it():
    rt = _rt_with_zone({"x": 20.0, "y": 0.0, "radius": 10.0})

    safety = rt.validate("drone_fly_to", {"x": 40.0, "y": 40.0, "z": -10.0})

    assert not any("禁飞区" in item for item in safety["violations"])


def test_no_fly_zone_blocks_a_crossing_waypoint_leg():
    rt = _rt_with_zone({"x": 20.0, "y": 0.0, "radius": 8.0})
    waypoints = json.dumps([{"x": 40.0, "y": 0.0, "z": -10.0}])

    safety = rt.validate("drone_fly_path", {"waypoints_json": waypoints})

    assert safety["level"] == "danger"
    assert any("禁飞区" in item for item in safety["violations"])


def test_no_zone_configured_costs_nothing_and_never_reads_telemetry():
    """Without zones the check must return immediately: an extra position
    readback per command would add an RPC (and a multi-second connect attempt
    when offline)."""
    rt = _rt(ToolCollector(), controller=None)

    safety = rt.validate("drone_fly_to", {"x": 40.0, "y": 0.0, "z": -10.0})

    assert safety["level"] == "safe"


# ---------------------------------------------------------------------------
# flight-envelope watchdog profiles
# ---------------------------------------------------------------------------


def _run_for_envelope(command: str, *, flight_control: bool = True) -> RunState:
    run = RunState(run_id="run_env", command=command, intent="", summary="", execute=True)
    return run


def _envelope_runtime(*, flight_control: bool = True, close_range: bool = False) -> AgentRuntime:
    runtime = _shell_runtime()
    runtime.planner = SimpleNamespace(_is_close_range_visual_command=lambda command: close_range)
    runtime.tools = SimpleNamespace(
        status_snapshot=lambda: {"backend_profile": {"capabilities": {"flight_control": flight_control}}}
    )
    return runtime


def test_normal_flight_task_uses_the_safety_envelope_not_the_close_range_one():
    """Regression guard: the tight 8 m close-range envelope must not be applied
    to ordinary flight, where a 15 m survey altitude is normal operation — the
    watchdog lands the aircraft after 3 consecutive breaches."""
    runtime = _envelope_runtime(close_range=False)

    profile = runtime._envelope_profile(_run_for_envelope("向北飞 40 米并巡检"))

    assert profile is not None
    max_alt_m, max_dist_m, from_takeoff = profile
    assert max_alt_m > 15.0, "a 15 m survey altitude must not breach the envelope"
    assert max_alt_m >= 50.0
    assert max_dist_m >= 100.0
    assert from_takeoff is False, "ordinary flight must be measured from the fence origin"


def test_close_range_visual_task_keeps_the_tight_envelope():
    runtime = _envelope_runtime(close_range=True)

    profile = runtime._envelope_profile(_run_for_envelope("靠近那辆车看一眼"))

    assert profile == (8.0, 70.0, True), "close-range work is measured from the takeoff point"


def test_envelope_profile_is_none_without_flight_control():
    runtime = _envelope_runtime(flight_control=False)

    assert runtime._envelope_profile(_run_for_envelope("读取状态")) is None


# ---------------------------------------------------------------------------
# regressions found by an independent audit of the first round of fixes
# ---------------------------------------------------------------------------


def test_configured_no_fly_zone_reaches_the_validator(monkeypatch):
    """The config -> FlightConstraint plumbing must be covered end to end.

    Every other zone test builds SafetyValidator by hand, so a rename or typo in
    the config key would silently disable the whole feature with a green suite
    (and the dataclass default max_velocity is 10.0 against the config's 8.0, so
    broken wiring would also be silently looser).
    """
    from src.agent.tool_executor import ToolRuntime
    from src import config as config_module

    # the setting lives on the DroneConfig instance (src.config.config), not on
    # the module
    monkeypatch.setattr(
        config_module.config,
        "safety_no_fly_zones_json",
        '[{"x": 20.0, "y": 0.0, "radius": 8.0, "name": "tower"}]',
    )
    rt = ToolRuntime(backend_id="px4_mavlink")

    zones = rt.safety.constraints.no_fly_zones
    assert zones, "configured zones must reach FlightConstraint"
    assert zones[0]["radius"] == 8.0
    assert rt.safety.constraints.max_velocity == float(config_module.config.safety_max_velocity_mps)
    assert rt.safety.constraints.max_altitude == float(config_module.config.safety_max_altitude_m)


def test_mission_item_with_both_coordinate_systems_is_validated_as_global():
    """The uploader prefers lat/lon when both are present (mavlink_controller
    checks `lat is None or lon is None` before falling back to x/y/z), so the
    validator must use the same precedence. It used to check x/y/z first, which
    let an item fly to a lat/lon far outside the fence while the validated point
    sat at home. MissionItem.to_dict() emits all six keys, so this is realistic.
    """
    rt = _rt(ToolCollector(), controller=status_controller(gps={"lat": 39.905163, "lon": 116.407089}))
    item = {"x": 0.0, "y": 0.0, "z": -10.0, "lat": 39.95, "lon": 116.407089, "alt_m": 10.0}

    safety = rt.validate("drone_upload_mission", {"waypoints_json": json.dumps([item])})

    assert safety["level"] == "danger"
    assert any("超出围栏" in v for v in safety["violations"])
    assert any("以 lat/lon 为准" in v for v in safety["violations"])


def test_mission_item_with_both_systems_nearby_stays_safe_but_says_so():
    rt = _rt(ToolCollector(), controller=status_controller(gps={"lat": 39.905163, "lon": 116.407089}))
    item = {"x": 0.0, "y": 0.0, "z": -10.0, "lat": 39.9054, "lon": 116.407089, "alt_m": 10.0}

    safety = rt.validate("drone_upload_mission", {"waypoints_json": json.dumps([item])})

    assert safety["level"] == "safe"
    assert any("以 lat/lon 为准" in v for v in safety["violations"])


def test_non_finite_global_coordinates_are_rejected():
    """`float(value or 0.0)` does not sanitise NaN (NaN is truthy) and every
    distance comparison against NaN is False, so a NaN lat/lon used to pass."""
    rt = _rt(ToolCollector(), controller=status_controller(gps={"lat": 39.9, "lon": 116.4}))

    for lat, lon in ((float("nan"), float("nan")), ("nan", "nan"), (float("inf"), 116.4)):
        safety = rt.validate(
            "drone_upload_mission",
            {"waypoints_json": json.dumps([{"lat": lat, "lon": lon, "alt_m": 10.0}])},
        )
        assert safety["level"] == "danger", (lat, lon, safety)
        assert safety["violations"], (lat, lon)


def test_coverage_area_over_a_no_fly_zone_is_rejected():
    """Corner checks alone let a 100x100 m swath swallow a zone: all four
    corners can sit outside it while the aircraft flies straight over."""
    rt = _rt(ToolCollector(), controller=status_controller())
    rt.safety.constraints.no_fly_zones = [{"x": 20.0, "y": 0.0, "radius": 10.0}]

    over = rt.validate(
        "formation_command",
        {
            "action": "coverage_plan",
            "area_shape": "rectangle",
            "area_x": 20.0,
            "area_y": 0.0,
            "area_width": 100.0,
            "area_height": 100.0,
            "area_altitude": 10.0,
        },
    )
    away = rt.validate(
        "formation_command",
        {
            "action": "coverage_plan",
            "area_shape": "rectangle",
            "area_x": -60.0,
            "area_y": 0.0,
            "area_width": 10.0,
            "area_height": 10.0,
            "area_altitude": 10.0,
        },
    )

    assert over["level"] == "danger"
    assert any("覆盖区域与禁飞区相交" in v for v in over["violations"])
    assert away["level"] == "safe"


def test_zone_leg_check_warns_instead_of_silently_skipping_without_telemetry():
    """With zones configured and no position readback the first leg cannot be
    checked. Returning no violations is indistinguishable from "the check
    passed" — the same blind spot the wiring was meant to close."""
    rt = _rt(ToolCollector(), controller=None)
    rt.safety.constraints.no_fly_zones = [{"x": 20.0, "y": 0.0, "radius": 8.0}]

    safety = rt.validate(
        "drone_fly_path", {"waypoints_json": json.dumps([{"x": 40.0, "y": 0.0, "z": -10.0}])}
    )

    assert safety["level"] == "warning"
    assert any("禁飞区穿越检查已跳过" in v for v in safety["violations"])


def test_business_timeouts_are_not_treated_as_link_loss():
    """A landing timeout or a rejected request is not a dead link. Treating it as
    one tears down and reconnects the flight link mid-mission."""
    rt = _rt(ToolCollector())

    for message in (
        "降落超时未确认: PX4（未上锁，避免空中切电机）",
        "请求被拒绝",
        "operation timeout after 30s",
    ):
        assert rt._is_connection_error({"message": message}) is False, message
    for message in ("MAVLink is not connected", "无法连接飞控", "连接被拒绝", "connection refused"):
        assert rt._is_connection_error({"message": message}) is True, message


# ---------------------------------------------------------------------------
# envelope watchdog: the horizontal basis must match the fence
# ---------------------------------------------------------------------------


def _drive_guard(
    profile: tuple[float, float, bool],
    drone: dict | list[dict],
    budget_s: float = 5.0,
):
    """Run the real guard loop against a telemetry feed.

    The feed may be a list of frames: the takeoff-relative profile anchors its
    origin on the first airborne sample, so a test for that basis has to feed the
    takeoff point first and then the far position.
    """
    runtime = agent_runtime()
    runtime._current = SimpleNamespace(run_id="run_g", status="running")
    runtime.supervisor = SimpleNamespace(is_emergency_stopped=lambda: False)
    landed: list[str] = []
    events: list[str] = []
    frames = drone if isinstance(drone, list) else [drone]
    state = {"i": 0}

    def snapshot() -> dict:
        index = min(state["i"], len(frames) - 1)
        state["i"] += 1
        return {"drone": frames[index]}

    runtime.tools = SimpleNamespace(
        status_snapshot=snapshot,
        execute=lambda name, params=None, **kw: landed.append(name) or SimpleNamespace(ok=True),
    )
    runtime._append_event = lambda level, src, msg, data=None, **kw: events.append(msg)
    runtime._cancel_active_work = lambda: None
    runtime._lock = threading.RLock()
    max_alt, max_dist, from_takeoff = profile
    stopper = threading.Timer(budget_s, runtime._envelope_stop.set)
    stopper.start()
    try:
        runtime._envelope_guard_loop("run_g", max_alt, max_dist, from_takeoff)
    finally:
        stopper.cancel()
    return landed, events


def test_legal_flight_is_not_force_landed_by_the_origin_basis_watchdog():
    """A flight that never leaves the geofence must not be aborted: measuring
    displacement from the takeoff point made a legal 95 m-from-origin leg look
    like a 115 m excursion and force-landed a healthy aircraft with a bogus
    "position estimate diverged" diagnosis."""
    at_takeoff = {"flying": True, "position_ned": {"x": 20.0, "y": 0.0, "z": -10.0}}
    # -99 m from the origin: inside the 100 m fence, but 119 m from the (20,0)
    # takeoff point — outside the 115 m bound a takeoff-relative check would use.
    # The values are chosen so the two bases give opposite verdicts.
    at_fence_edge = {"flying": True, "position_ned": {"x": -99.0, "y": 0.0, "z": -10.0}}
    landed, events = _drive_guard((57.5, 115.0, False), [at_takeoff, at_takeoff, at_fence_edge])

    assert landed == [], f"a legal flight was aborted: {landed}"
    assert not any("包线" in e for e in events)


def test_watchdog_still_lands_a_genuine_excursion_from_the_origin():
    landed, events = _drive_guard(
        (57.5, 115.0, False), {"flying": True, "position_ned": {"x": -130.0, "y": 0.0, "z": -10.0}}
    )

    assert landed, "an excursion past the fence must still be caught"
    assert any("包线" in e for e in events)


def test_close_range_profile_still_measures_from_the_takeoff_point():
    """The tight profile is about small-area manoeuvring, so its basis stays
    takeoff-relative: 140 m of travel is an excursion even inside the fence."""
    at_takeoff = {"flying": True, "position_ned": {"x": 0.0, "y": 0.0, "z": -3.0}}
    far = {"flying": True, "position_ned": {"x": 140.0, "y": 0.0, "z": -3.0}}
    landed, _ = _drive_guard((8.0, 70.0, True), [at_takeoff, at_takeoff, far])

    assert landed, "the close-range envelope must still fire on a 140 m excursion"


def _profile_runtime(*, close_range: bool, flight_control: bool = True):
    runtime = agent_runtime()
    runtime.planner = SimpleNamespace(_is_close_range_visual_command=lambda command: close_range)
    runtime.tools = SimpleNamespace(
        status_snapshot=lambda: {"backend_profile": {"capabilities": {"flight_control": flight_control}}}
    )
    return runtime


def _profile_run(steps) -> RunState:
    from src.agent.planner import MissionPlan, MissionStep

    return RunState(
        run_id="run_env",
        command="靠近目标确认",
        intent="",
        summary="",
        execute=True,
        plan=MissionPlan(
            run_id="run_env", command="c", intent="i", summary="s",
            steps=[MissionStep(id=f"s{i}", label="t", tool=t, params=p) for i, (t, p) in enumerate(steps, 1)],
        ),
    )


def test_close_range_ceiling_covers_the_altitude_the_plan_itself_asked_for():
    """The watchdog must catch uncommanded excursions, not the climb the plan
    wrote down: a close-range task whose plan says up_m=15 would otherwise be
    force-landed after 3 s with a bogus "position estimate diverged" report."""
    runtime = _profile_runtime(close_range=True)

    tight = runtime._envelope_profile(_profile_run([("drone_takeoff", {"altitude": 3.0})]))
    climbing = runtime._envelope_profile(_profile_run([("drone_move_relative", {"up_m": 15.0})]))
    waypoint = runtime._envelope_profile(_profile_run([("drone_fly_to", {"x": 1.0, "y": 0.0, "z": -20.0})]))

    assert tight == (8.0, 70.0, True), "a 3 m plan keeps the tight 8 m ceiling"
    assert climbing[0] > 15.0, f"the planned 15 m climb must fit: {climbing}"
    assert waypoint[0] > 20.0, f"the planned 20 m leg must fit: {waypoint}"
    assert climbing[2] is True and waypoint[2] is True


# ---------------------------------------------------------------------------
# emergency stop: release path
# ---------------------------------------------------------------------------


def _estop_runtime(*, flying, latched: bool = True):
    runtime = agent_runtime()
    runtime.tools = SimpleNamespace(
        backend_id="fake",
        status_snapshot=lambda: {"drone": {"flying": flying} if flying is not None else {}},
        execute=lambda *a, **kw: None,
    )
    released: list[bool] = []

    def _reset() -> None:
        released.append(True)
        runtime.supervisor.emergency_stop = lambda: None

    runtime.supervisor = SimpleNamespace(
        is_emergency_stopped=lambda: latched,
        should_pause=lambda: latched,
        reset_emergency=_reset,
    )
    runtime._append_event = lambda *a, **kw: None
    return runtime, released


def test_release_emergency_stop_is_refused_while_airborne():
    """`supervisor.reset_emergency` documents "ground state only" but only clears
    the flag. Releasing in the air re-arms every flight command while the
    aircraft is merely hovering, so the ground-first order is enforced here."""
    runtime, released = _estop_runtime(flying=True)

    result = AgentRuntime.control(runtime, "reset_emergency")

    assert result["ok"] is False
    assert "先降落" in result["error"]
    assert released == [], "the latch must stay set while flying"


def test_release_emergency_stop_works_on_the_ground():
    runtime, released = _estop_runtime(flying=False)

    result = AgentRuntime.control(runtime, "reset_emergency")

    assert result["ok"] is True
    assert released == [True]


def test_release_emergency_stop_works_when_telemetry_is_unavailable():
    """Only a positive "airborne" reading blocks the release: with no telemetry a
    refusal would trap the operator in the latched state forever."""
    runtime, released = _estop_runtime(flying=None)

    result = AgentRuntime.control(runtime, "reset_emergency")

    assert result["ok"] is True
    assert released == [True]


# ---------------------------------------------------------------------------
# emergency stop must hold against an in-flight Agent task
# ---------------------------------------------------------------------------


class _RecordingController:
    """Controller stub that records every command it is asked to execute."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.is_connected = True

    backend_name = "recording"

    def list_vehicles(self) -> list[str]:
        return ["d0"]

    def get_status(self, vehicle_name: str = ""):
        return SimpleNamespace(
            position_ned={"x": 0.0, "y": 0.0, "z": -5.0},
            velocity_ned={"vx": 0.0, "vy": 0.0, "vz": 0.0},
            attitude_rad={"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            armed=True,
            flying=True,
            mode="OFFBOARD",
            gps=None,
            extra={},
        )

    def _record(self, name: str) -> bool:
        self.calls.append(name)
        return True

    def arm(self, vehicle_name: str = "") -> bool:
        return self._record("arm")

    def disarm(self, vehicle_name: str = "") -> bool:
        return self._record("disarm")

    def takeoff(self, altitude: float = 3.0, vehicle_name: str = "") -> bool:
        return self._record("takeoff")

    def land(self, vehicle_name: str = "") -> bool:
        return self._record("land")

    def hover(self, vehicle_name: str = "") -> bool:
        return self._record("hover")

    def move_to_position(self, x, y, z, velocity=2.0, vehicle_name="") -> bool:
        return self._record("move_to_position")

    def move_by_velocity(self, vx, vy, vz, duration=0.0, vehicle_name="") -> bool:
        return self._record("move_by_velocity")

    def move_on_path(self, waypoints, velocity=2.0, vehicle_name="") -> bool:
        return self._record("move_on_path")

    def rotate_to(self, yaw_deg: float, vehicle_name: str = "") -> bool:
        return self._record("rotate_to")

    def set_mode(self, mode: str, vehicle_name: str = "") -> bool:
        return self._record("set_mode")

    def upload_mission(self, items, vehicle_name: str = "") -> bool:
        return self._record("upload_mission")

    def start_mission(self, vehicle_name: str = "") -> bool:
        return self._record("start_mission")


def _recording_runtime():
    """ToolRuntime shell whose collector/controller stay ours, so every command
    that actually executes is recorded. (A real ToolRuntime would replace both on
    ensure_ready, which would make the "nothing reached the controller"
    assertion vacuous.)"""
    from src.tools.core import register_core_tools

    controller = _RecordingController()
    collector = ToolCollector()
    register_core_tools(collector, controller, lambda data: data)
    rt = tool_runtime(collector, controller)
    rt.backend_id = "airsim"
    rt.backend_registry = create_builtin_backend_registry()
    rt.backend_profile = rt.backend_registry.require("airsim")
    rt.formation_active = lambda: False
    rt.available = True
    return rt, controller


def _full_runtime():
    """A real ToolRuntime: needed when the code under test reads telemetry."""
    from src.agent.tool_executor import ToolRuntime

    rt = ToolRuntime(backend_id="airsim", camera_settings_provider=lambda: {"source": "airsim"})
    rt.controller = _RecordingController()
    rt.available = True
    return rt


def test_emergency_stop_latch_keeps_every_flight_command_away_from_the_controller():
    """The invariant that makes the e-stop safe for an in-flight Agent task: while
    the latch is set, no flight command reaches the controller, whatever the
    Agent loop is doing. hover/land/status stay allowed so the operator can still
    bring the aircraft down."""
    rt, controller = _recording_runtime()

    # Control experiment first: the same setup MUST reach the controller when the
    # latch is not set, otherwise the empty call list below would prove nothing.
    rt.execute("drone_fly_to", {"x": 10.0, "y": 0.0, "z": -5.0}, dry_run=False)
    assert "move_to_position" in controller.calls, "the recording controller is not wired up"
    controller.calls.clear()

    attempts = {
        "drone_arm": {},
        "drone_takeoff": {"altitude": 5.0},
        "drone_fly_to": {"x": 10.0, "y": 0.0, "z": -5.0},
        "drone_move_relative": {"forward_m": 5.0},
        "drone_rotate_to": {"yaw_deg": 90.0},
        "drone_set_mode": {"mode": "OFFBOARD"},
        "drone_fly_velocity": {"vx": 1.0, "vy": 0.0, "vz": 0.0, "duration": 1.0},
        "drone_upload_mission": {"waypoints_json": "[]"},
        "drone_start_mission": {},
        "drone_fly_path": {"waypoints_json": '[{"x": 5, "y": 0, "z": -5}]'},
    }
    results = {}
    for tool, params in attempts.items():
        result = rt.execute(tool, params, dry_run=False, blocked_by_supervisor=True)
        results[tool] = result.error_code

    assert all(code == "BLOCKED" for code in results.values()), results
    assert controller.calls == [], f"a flight command reached the controller: {controller.calls}"

    # The escape hatches must still be admitted, or the operator cannot bring the
    # aircraft down while the stop is latched.
    for allowed in ("drone_hover", "drone_land", "drone_get_status"):
        result = rt.execute(allowed, {}, dry_run=False, blocked_by_supervisor=True)
        assert result.error_code != "BLOCKED", (allowed, result.error_code)


def test_agent_loop_stops_and_never_flies_after_the_estop_latch():
    """A task that is mid-loop when the operator hits the e-stop must stop, and
    must not execute a flight action it decided before the latch."""
    from src.agent.agent_loop import AgentLoop

    latch = {"stopped": False}
    executed: list[str] = []
    calls = {"n": 0}

    class _Planner:
        last_reasoning = ""

        def decide_next_step(self, **kwargs):
            calls["n"] += 1
            # The operator hits the e-stop while the model is "thinking" on the
            # SECOND round trip: that decision must not be executed. The first
            # action legitimately ran (the latch did not exist yet).
            if calls["n"] == 2:
                latch["stopped"] = True
                return LoopDecision(action="drone_move_relative", params={"forward_m": 5.0})
            return LoopDecision(action="drone_fly_to", params={"x": 5.0, "y": 0.0, "z": -5.0})

    rt = _full_runtime()
    loop = AgentLoop(
        rt,
        _Planner(),
        SimpleNamespace(snapshot=lambda: {}, remember_tool_call=lambda *a, **k: None),
        should_stop=lambda: latch["stopped"],
        execute_tool=lambda name, params, dry_run=False: (
            executed.append(name)
            or rt.execute(name, params, dry_run=dry_run, blocked_by_supervisor=latch["stopped"])
        ),
    )

    state = loop.run(run_id="run_estop", command="飞到北边 5 米", capabilities={}, tool_cards=[], max_steps=6)

    assert state.status == "blocked", state.status
    assert "emergency stop" in state.failure_reason
    assert calls["n"] == 2, "the loop should stop right after the round trip that saw the latch"
    assert executed == ["drone_fly_to"], (
        "only the pre-latch action may run; the action decided in the round trip that "
        f"saw the latch must be dropped, got {executed}"
    )
