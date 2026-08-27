from __future__ import annotations

import json
import base64
import threading
import time
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.agent.llm as llm_module
from src.agent.agent_loop import AgentLoop
from src.agent.command_slots import extract_command_slots
from src.agent.loop_types import LoopDecision
from src.agent.llm import AnthropicClient, LLMMissionPlanner, LLMUnavailableError
from src.agent.memory import AgentMemory
from src.agent.planner import MissionPlan, MissionPlanner, MissionStep
from src.agent.runtime import AgentRuntime, ChatMessage, RunState
from src.agent.skill_registry import SkillRegistry
from src.agent.task_runs import TaskRunStore
from src.agent.tool_executor import ToolCallResult, ToolCollector, ToolRuntime
from src.gcs.managers import ManagerResult
from src.gcs.mission import MissionPlanDraft
from src.gcs.services import ToolMissionManager
from src.gcs.state import GroundStationState, VehicleTelemetry
from src.modules.mavlink_controller import MavlinkController
from src.modules.task_manager import TaskManager, TaskStatus


def _result(tool: str, ok: bool, data: dict[str, Any], *, terminal: bool = True, task_id: str = "") -> ToolCallResult:
    now = time.time()
    return ToolCallResult(tool, {}, ok, data, now, now, terminal=terminal, task_id=task_id)


class FakeMemory:
    def snapshot(self) -> dict[str, Any]:
        return {}

    def remember_tool_call(self, tool: str, ok: bool) -> None:
        return None


class FakeSkills:
    def available_cards(self, capabilities: dict[str, Any], memory: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return []


class StaticSkills(FakeSkills):
    def __init__(self, cards: list[dict[str, Any]]) -> None:
        self.cards = cards

    def available_cards(self, capabilities: dict[str, Any], memory: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return list(self.cards)


class SequencePlanner:
    def __init__(self, decisions: list[LoopDecision]) -> None:
        self.decisions = list(decisions)
        self.calls = 0

    def decide_next_step(self, **_: Any) -> LoopDecision:
        self.calls += 1
        return self.decisions.pop(0)


class FakeTools:
    def __init__(self, handlers: dict[str, Any]) -> None:
        self.handlers = handlers
        self.calls: list[str] = []

    def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": name} for name in self.handlers]

    def status_snapshot(self) -> dict[str, Any]:
        return {"connected": True, "stale_connection": False, "drone": {"flying": True}}

    def execute(self, name: str, params: dict[str, Any], **_: Any) -> ToolCallResult:
        self.calls.append(name)
        handler = self.handlers[name]
        return handler(params) if callable(handler) else handler


class FakeMissionTelemetry:
    def get_state(self) -> GroundStationState:
        return GroundStationState()


class OffsetMissionTelemetry:
    def get_state(self) -> GroundStationState:
        return GroundStationState(
            vehicle=VehicleTelemetry(
                position_ned={"x": 6.0, "y": 8.0, "z": -2.0},
                gps={"lat": 39.0, "lon": 116.0, "alt": 2.0},
                armed=True,
                flying=True,
            )
        )


class FakeMissionSafety:
    def state(self) -> SimpleNamespace:
        return SimpleNamespace(emergency_stop=False)

    def validate_mission(self, draft: MissionPlanDraft, state: GroundStationState) -> ManagerResult:
        return ManagerResult(True, "mission valid", {"items": len(draft.items)})


def test_chinese_conditional_navigation_uses_fast_template_plan() -> None:
    command = "查看无人机状态，如果没有起飞，让他飞飞五米高度，向前飞行五米悬停保持等待下一步指令"
    capabilities = {"flight_control": True, "telemetry": True, "gps": True, "mode_control": True}

    slots = extract_command_slots(command)
    assert slots.altitude == 5.0
    assert slots.relative_move == {"forward_m": 5.0, "right_m": 0.0, "up_m": 0.0}
    assert slots.ned_target is None

    plan = MissionPlanner().plan(command, capabilities=capabilities)
    assert [step.tool for step in plan.steps] == [
        "drone_connect",
        "drone_get_status",
        "drone_arm",
        "drone_takeoff",
        "drone_move_relative",
        "drone_hover",
        "drone_get_status",
        "memory_store",
    ]
    assert plan.steps[3].params == {"altitude": 5.0}
    assert plan.steps[4].params["forward_m"] == 5.0


def _mission_with_lat(lat: float) -> MissionPlanDraft:
    return MissionPlanDraft.from_dict({
        "name": "test mission",
        "home": {"lat": lat, "lon": 116.0, "alt_m": 0},
        "items": [
            {
                "id": "wp_001",
                "type": "waypoint",
                "frame": "global_relative_alt",
                "lat": lat,
                "lon": 116.0,
                "alt_m": 3,
            }
        ],
    })


def test_mission_draft_update_marks_vehicle_upload_stale() -> None:
    tools = FakeTools({
        "drone_upload_mission": _result("drone_upload_mission", True, {"status": "ok", "message": "uploaded"}),
        "drone_start_mission": _result("drone_start_mission", True, {"status": "ok", "message": "started"}),
    })
    manager = ToolMissionManager(tools, FakeMissionTelemetry(), FakeMissionSafety())  # type: ignore[arg-type]

    assert manager.upload(_mission_with_lat(39.0)).ok is True
    assert manager.progress().uploaded is True

    manager.set_draft(_mission_with_lat(39.1))

    state = manager.progress()
    assert state.uploaded is False
    assert state.running is False
    assert state.progress == 0.0


def test_mission_start_with_draft_uploads_then_starts_current_plan() -> None:
    uploaded_lats: list[float] = []

    def upload(params: dict[str, Any]) -> ToolCallResult:
        items = json.loads(params["waypoints_json"])
        uploaded_lats.append(items[0]["lat"])
        return _result("drone_upload_mission", True, {"status": "ok", "message": "uploaded"})

    tools = FakeTools({
        "drone_upload_mission": upload,
        "drone_start_mission": _result("drone_start_mission", True, {"status": "ok", "message": "started"}),
    })
    manager = ToolMissionManager(tools, FakeMissionTelemetry(), FakeMissionSafety())  # type: ignore[arg-type]

    result = manager.start(_mission_with_lat(39.2))

    assert result.ok is True
    assert tools.calls == ["drone_upload_mission", "drone_start_mission"]
    assert uploaded_lats == [39.2]


def test_local_mission_fallback_takes_off_before_path() -> None:
    flown_paths: list[list[dict[str, Any]]] = []

    def fly_path(params: dict[str, Any]) -> ToolCallResult:
        flown_paths.append(json.loads(params["waypoints_json"]))
        return _result("drone_fly_path", True, {"status": "ok", "message": "path flown"})

    tools = FakeTools({
        "drone_upload_mission": _result(
            "drone_upload_mission",
            False,
            {"status": "error", "message": "mission upload rejected: 1"},
        ),
        "drone_takeoff": _result("drone_takeoff", True, {"status": "ok", "message": "takeoff ok"}),
        "drone_fly_path": fly_path,
    })
    manager = ToolMissionManager(tools, FakeMissionTelemetry(), FakeMissionSafety())  # type: ignore[arg-type]

    result = manager.start(MissionPlanDraft.from_dict({
        "name": "fallback mission",
        "home": {"lat": 39.0, "lon": 116.0, "alt_m": 3.0},
        "items": [
            {
                "id": "takeoff",
                "type": "takeoff",
                "frame": "global_relative_alt",
                "lat": 39.0,
                "lon": 116.0,
                "alt_m": 3.0,
                "x": 0,
                "y": 0,
                "z": -3,
            },
            {
                "id": "wp_001",
                "type": "waypoint",
                "frame": "global_relative_alt",
                "lat": 39.0001,
                "lon": 116.0,
                "alt_m": 3.0,
            },
        ],
    }))

    assert result.ok is True
    assert tools.calls == ["drone_upload_mission", "drone_takeoff", "drone_fly_path"]
    assert flown_paths[0][0] == {"x": 0.0, "y": 0.0, "z": -3.0}
    assert 11.0 <= flown_paths[0][1]["x"] <= 11.2


def test_local_mission_fallback_offsets_global_waypoints_from_current_local_position() -> None:
    flown_paths: list[list[dict[str, Any]]] = []

    def fly_path(params: dict[str, Any]) -> ToolCallResult:
        flown_paths.append(json.loads(params["waypoints_json"]))
        return _result("drone_fly_path", True, {"status": "ok", "message": "path flown"})

    tools = FakeTools({
        "drone_upload_mission": _result(
            "drone_upload_mission",
            False,
            {"status": "error", "message": "mission upload rejected: 1"},
        ),
        "drone_fly_path": fly_path,
    })
    manager = ToolMissionManager(tools, OffsetMissionTelemetry(), FakeMissionSafety())  # type: ignore[arg-type]

    result = manager.start(MissionPlanDraft.from_dict({
        "name": "in-air fallback mission",
        "home": {"lat": 39.0, "lon": 116.0, "alt_m": 2.0},
        "items": [
            {
                "id": "wp_001",
                "type": "waypoint",
                "frame": "global_relative_alt",
                "lat": 39.0001,
                "lon": 116.0,
                "alt_m": 3.0,
            }
        ],
    }))

    assert result.ok is True
    assert tools.calls == ["drone_upload_mission", "drone_fly_path"]
    assert 17.0 <= flown_paths[0][0]["x"] <= 17.2
    assert flown_paths[0][0]["y"] == 8.0
    assert flown_paths[0][0]["z"] == -3.0


def test_async_tool_is_polled_to_terminal_state() -> None:
    status_count = 0

    def task_status(_: dict[str, Any]) -> ToolCallResult:
        nonlocal status_count
        status_count += 1
        if status_count == 1:
            return _result("airsim_task_status", True, {"status": "running", "task_id": "search_1"}, terminal=False, task_id="search_1")
        return _result(
            "airsim_task_status",
            True,
            {"status": "completed", "task_id": "search_1", "result": {"status": "candidate_found"}},
            task_id="search_1",
        )

    tools = FakeTools({
        "drone_upload_mission": _result(
            "drone_upload_mission",
            True,
            {"status": "started", "task_id": "search_1"},
            terminal=False,
            task_id="search_1",
        ),
        "airsim_task_status": task_status,
        "airsim_task_cancel": _result("airsim_task_cancel", True, {"status": "ok"}),
    })
    planner = SequencePlanner([
        LoopDecision("drone_upload_mission", {"waypoints_json": "[]"}, "start async mission task"),
        LoopDecision(action="", reason="mission task complete", is_complete=True),
    ])
    loop = AgentLoop(
        tools, planner, FakeMemory(), skills=FakeSkills(), async_timeout=1.0, async_poll_interval=0.05
    )

    state = loop.run(
        "run_async",
        "upload mission",
        {"gps": True, "mode_control": True},
        [{"name": name} for name in tools.handlers],
        max_steps=4,
    )

    assert state.status == "completed"
    assert status_count == 2
    assert state.results[0].data["status"] == "completed"
    assert state.results[0].data["task"]["result"]["status"] == "candidate_found"


def test_agent_loop_allows_bounded_recovery_after_failure() -> None:
    tools = FakeTools({
        "drone_fly_to": _result("drone_fly_to", False, {"status": "failed", "message": "path blocked"}),
        "drone_get_status": _result("drone_get_status", True, {"status": "ok"}),
        "drone_hover": _result("drone_hover", True, {"status": "ok"}),
    })
    planner = SequencePlanner([
        LoopDecision("drone_fly_to", {"x": 5, "y": 0, "z": -3}, "try route"),
        LoopDecision("drone_get_status", {}, "refresh state"),
        LoopDecision("drone_hover", {}, "recover safely"),
        LoopDecision(action="", reason="safe recovery complete", is_complete=True),
    ])
    loop = AgentLoop(tools, planner, FakeMemory(), skills=FakeSkills())

    state = loop.run(
        "run_recovery",
        "move and recover",
        {"flight_control": True},
        [{"name": name} for name in tools.handlers],
        max_steps=5,
    )

    assert state.status == "completed"
    assert [item.tool for item in state.results] == ["drone_fly_to", "drone_get_status", "drone_hover"]


def test_status_readback_alone_does_not_hide_failure() -> None:
    tools = FakeTools({
        "drone_fly_to": _result("drone_fly_to", False, {"status": "failed", "message": "path blocked"}),
        "drone_get_status": _result("drone_get_status", True, {"status": "ok"}),
    })
    planner = SequencePlanner([
        LoopDecision("drone_fly_to", {}, "try route"),
        LoopDecision("drone_get_status", {}, "refresh state"),
        LoopDecision(action="", reason="stop", is_complete=True),
    ])
    loop = AgentLoop(tools, planner, FakeMemory(), skills=FakeSkills())

    state = loop.run(
        "run_failed",
        "move",
        {"flight_control": True},
        [{"name": name} for name in tools.handlers],
        max_steps=4,
    )

    assert state.status == "failed"
    assert state.failure_reason


def test_unavailable_tool_is_not_reported_as_completed() -> None:
    tools = FakeTools({"drone_get_status": _result("drone_get_status", True, {"status": "ok"})})
    planner = SequencePlanner([LoopDecision("missing_tool", {}, "bad selection")])
    loop = AgentLoop(tools, planner, FakeMemory(), skills=FakeSkills())

    state = loop.run("run_missing", "do something", {}, [{"name": "drone_get_status"}], max_steps=2)

    assert state.status == "failed"
    assert not state.results


def test_llm_payload_with_unavailable_tool_is_not_complete() -> None:
    planner = LLMMissionPlanner.__new__(LLMMissionPlanner)

    decision = planner._decision_from_payload({"action": "missing_tool", "reason": "bad"}, {"drone_get_status"})

    assert decision.action == ""
    assert decision.is_complete is False


def test_agent_loop_keeps_atomic_tools_available_with_markdown_guidance() -> None:
    tools = FakeTools({
        "drone_get_status": _result("drone_get_status", True, {"status": "ok"}),
        "drone_takeoff": _result("drone_takeoff", True, {"status": "ok"}),
    })
    skills = StaticSkills([
        {
            "name": "skill:navigation",
            "kind": "skill",
            "purpose": "navigation",
            "required_capabilities": ["flight_control"],
        }
    ])
    planner = SequencePlanner([
        LoopDecision("drone_takeoff", {"altitude": 3}, "use native takeoff tool"),
        LoopDecision(action="", reason="takeoff handled", is_complete=True),
    ])
    loop = AgentLoop(tools, planner, FakeMemory(), skills=skills)

    state = loop.run(
        "run_skill_surface",
        "take off to 3 meters and hover",
        {"flight_control": True, "telemetry": True},
        [{"name": name} for name in tools.handlers],
        max_steps=2,
    )

    assert state.status == "completed"
    assert [item.tool for item in state.results] == ["drone_takeoff"]
    assert tools.calls == ["drone_takeoff"]


def test_visual_question_forces_capture_then_vlm_analysis() -> None:
    tools = FakeTools({
        "drone_get_status": _result("drone_get_status", True, {"status": "ok"}),
        "airsim_take_photo": _result("airsim_take_photo", True, {"status": "ok", "image_base64": "aW1hZ2U="}),
        "airsim_vlm_analyze_image": _result(
            "airsim_vlm_analyze_image",
            True,
            {"status": "image_analyzed", "summary_zh": "画面中可见地面和无人机结构。"},
        ),
    })
    planner = SequencePlanner([
        LoopDecision("drone_get_status", {}, "bad first choice"),
        LoopDecision("memory_store", {"source": "mission"}, "bad internal tool"),
        LoopDecision(action="", reason="done", is_complete=True),
    ])
    loop = AgentLoop(tools, planner, FakeMemory(), skills=FakeSkills())

    state = loop.run(
        "run_visual",
        "现在看一下无人机看到了什么信息",
        {"image_capture": True},
        [{"name": name} for name in tools.handlers],
        max_steps=5,
    )

    assert state.status == "completed"
    assert [item.tool for item in state.results] == ["airsim_take_photo", "airsim_vlm_analyze_image"]
    assert tools.calls == ["airsim_take_photo", "airsim_vlm_analyze_image"]
    assert planner.calls == 0


def test_visual_confirmation_guard_uses_native_tools_before_blocking_2d_approach() -> None:
    tools = FakeTools({
        "airsim_take_photo": _result("airsim_take_photo", True, {"status": "ok", "image_base64": "aW1hZ2U="}),
        "airsim_vlm_confirm_target": _result(
            "airsim_vlm_confirm_target",
            True,
            {
                "status": "target_confirmed",
                "target_found": True,
                "relative_direction": "center",
                "summary_zh": "red car visible",
            },
        ),
    })
    planner = SequencePlanner([])
    loop = AgentLoop(tools, planner, FakeMemory(), skills=FakeSkills())

    state = loop.run(
        "run_visual_skill",
        "fly to the red car in the camera view",
        {"image_capture": True},
        [{"name": name} for name in tools.handlers],
        max_steps=4,
    )

    assert state.status == "failed"
    assert [item.tool for item in state.results] == ["airsim_take_photo", "airsim_vlm_confirm_target"]
    assert tools.calls == ["airsim_take_photo", "airsim_vlm_confirm_target"]
    assert "2D image target" in state.failure_reason
    assert planner.calls == 0


def test_visual_approach_is_blocked_without_safe_3d_target_or_approach_tool() -> None:
    tools = FakeTools({
        "airsim_take_photo": _result("airsim_take_photo", True, {"status": "ok", "image_base64": "aW1hZ2U="}),
        "airsim_vlm_confirm_target": _result(
            "airsim_vlm_confirm_target",
            True,
            {
                "status": "target_confirmed",
                "target_found": True,
                "relative_direction": "center",
                "summary_zh": "确认画面中央有红色车辆。",
            },
        ),
    })
    planner = SequencePlanner([
        LoopDecision("memory_store", {}, "bad internal tool"),
        LoopDecision(action="", reason="done", is_complete=True),
        LoopDecision(action="", reason="done", is_complete=True),
    ])
    loop = AgentLoop(tools, planner, FakeMemory(), skills=FakeSkills())

    state = loop.run(
        "run_visual_approach",
        "飞向画面中红色车辆位置",
        {"image_capture": True},
        [{"name": name} for name in tools.handlers],
        max_steps=5,
    )

    assert state.status == "failed"
    assert [item.tool for item in state.results] == ["airsim_take_photo", "airsim_vlm_confirm_target"]
    assert "2D image target" in state.failure_reason
    assert planner.calls == 0


def test_tool_runtime_marks_started_result_as_non_terminal() -> None:
    runtime = ToolRuntime()
    runtime.collector = ToolCollector()
    runtime.collector.tools["async_op"] = lambda: json.dumps({"status": "started", "task_id": "task_1"})
    runtime.available = True
    runtime.ensure_ready = lambda: True  # type: ignore[method-assign]
    runtime.validate = lambda _name, _params: {"level": "safe", "violations": [], "corrected_params": {}}  # type: ignore[method-assign]

    result = runtime.execute("async_op")

    assert result.ok is True
    assert result.terminal is False
    assert result.task_id == "task_1"
    assert result.to_dict()["outcome"] == "accepted"


def test_invalid_tool_parameters_return_structured_failure() -> None:
    runtime = ToolRuntime()
    runtime.collector = ToolCollector()
    runtime.collector.tools["atomic_op"] = lambda: json.dumps({"status": "ok"})
    runtime.available = True
    runtime.ensure_ready = lambda: True  # type: ignore[method-assign]

    def invalid(_name: str, _params: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("bad number")

    runtime.validate = invalid  # type: ignore[method-assign]
    result = runtime.execute("atomic_op")

    assert result.ok is False
    assert result.data["status"] == "error"
    assert "invalid tool parameters" in result.data["message"]


def test_async_tool_without_task_id_is_rejected() -> None:
    runtime = ToolRuntime()
    runtime.collector = ToolCollector()
    runtime.collector.tools["broken_async_op"] = lambda: json.dumps({"status": "started"})
    runtime.available = True
    runtime.ensure_ready = lambda: True  # type: ignore[method-assign]
    runtime.validate = lambda _name, _params: {"level": "safe", "violations": [], "corrected_params": {}}  # type: ignore[method-assign]

    result = runtime.execute("broken_async_op")

    assert result.ok is False
    assert result.terminal is True
    assert "without task_id" in result.data["message"]


def test_global_mission_items_with_null_local_coordinates_are_safe() -> None:
    runtime = ToolRuntime()
    payload = [
        {
            "id": "wp_001",
            "type": "waypoint",
            "frame": "global_relative_alt",
            "lat": 39.905163,
            "lon": 116.407089,
            "alt_m": 3.0,
            "x": None,
            "y": None,
            "z": None,
        }
    ]

    safety = runtime.validate("drone_upload_mission", {"waypoints_json": json.dumps(payload)})

    assert safety["level"] == "safe"
    assert not safety["violations"]


def test_mavlink_mission_first_item_is_marked_current() -> None:
    class FakeMav:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        def mission_item_int_send(self, *args: Any) -> None:
            self.calls.append(args)

    fake_mav = FakeMav()
    controller = MavlinkController()
    controller._mavlink = SimpleNamespace(target_system=1, target_component=1, mav=fake_mav)  # type: ignore[attr-defined]

    controller._send_mission_item_int(0, {
        "lat": 39.9,
        "lon": 116.4,
        "alt_m": 3.0,
        "hold_s": 0.0,
        "acceptance_radius_m": 2.0,
        "metadata": {},
    })

    assert fake_mav.calls
    assert fake_mav.calls[0][5] == 1
    assert fake_mav.calls[0][10] == 0.0


def test_mavlink_ignores_mission_ack_not_targeted_to_gcs() -> None:
    controller = MavlinkController()
    controller._mavlink = SimpleNamespace(source_system=255, source_component=190)  # type: ignore[attr-defined]

    assert controller._mission_ack_targets_this_gcs({"target_system": 255, "target_component": 190}) is True
    assert controller._mission_ack_targets_this_gcs({"target_system": 0, "target_component": 0}) is False


def test_cancelled_background_task_cannot_flip_back_to_completed() -> None:
    manager = TaskManager()
    started = threading.Event()

    def work(*, task_info: Any) -> dict[str, Any]:
        started.set()
        while not task_info.cancel_flag:
            time.sleep(0.01)
        return {"stopped": True}

    task_id = manager.start_task("tracking", work)
    assert started.wait(timeout=0.5)
    assert manager.cancel_task(task_id) is True

    deadline = time.time() + 0.5
    while time.time() < deadline:
        info = manager.get_task(task_id)
        if info and info.result is not None:
            break
        time.sleep(0.01)

    info = manager.get_task(task_id)
    assert info is not None
    assert info.status == TaskStatus.CANCELLED


def test_skill_subtools_use_governed_executor_callback() -> None:
    tools = FakeTools({})
    calls: list[str] = []

    def governed(name: str, params: dict[str, Any], dry_run: bool) -> ToolCallResult:
        calls.append(name)
        if name == "drone_get_status":
            return _result(name, True, {"status": "ok", "armed": False, "flying": False})
        return _result(name, True, {"status": "ok"})

    result = SkillRegistry(register_builtins=True).execute(
        "skill:navigation",
        {"altitude": 3.0},
        tools,  # type: ignore[arg-type]
        execute_tool=governed,
    )

    assert result.ok is True
    assert calls == ["drone_connect", "drone_get_status", "drone_arm", "drone_takeoff", "drone_hover"]
    assert tools.calls == []


def test_navigation_skill_recovers_when_takeoff_failed_but_status_is_airborne() -> None:
    tools = FakeTools({})
    calls: list[str] = []
    status_calls = 0

    def governed(name: str, params: dict[str, Any], dry_run: bool) -> ToolCallResult:
        nonlocal status_calls
        calls.append(name)
        if name == "drone_get_status":
            status_calls += 1
            if status_calls == 1:
                return _result(name, True, {"status": "ok", "armed": False, "flying": False, "altitude_m": 0.1})
            return _result(name, True, {"status": "ok", "armed": True, "flying": True, "altitude_m": 2.7})
        if name == "drone_takeoff":
            return _result(name, False, {"status": "error", "message": "target altitude not reached"})
        return _result(name, True, {"status": "ok"})

    result = SkillRegistry(register_builtins=True).execute(
        "skill:navigation",
        {"altitude": 3.0, "forward_m": 2.0, "hover_after": True},
        tools,  # type: ignore[arg-type]
        execute_tool=governed,
    )

    assert result.ok is True
    assert calls == [
        "drone_connect",
        "drone_get_status",
        "drone_arm",
        "drone_takeoff",
        "drone_get_status",
        "drone_move_relative",
        "drone_hover",
    ]


def test_runtime_skips_takeoff_when_vehicle_is_already_airborne_near_target() -> None:
    class AlreadyAirborneTools:
        def status_snapshot(self) -> dict[str, Any]:
            return {
                "connected": True,
                "stale_connection": False,
                "drone": {"armed": True, "flying": True, "altitude_m": 4.8},
            }

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.tools = AlreadyAirborneTools()  # type: ignore[assignment]

    result = runtime._maybe_skip_idempotent_step(
        MissionStep("s01", "起飞", "drone_takeoff", {"altitude": 5.0})
    )

    assert result is not None
    assert result.ok is True
    assert result.data["skipped"] is True


def test_high_risk_real_vehicle_tool_cannot_bypass_execute_approval_path() -> None:
    class RealVehicleTools:
        READ_ONLY_TOOLS: set[str] = set()

        def status_snapshot(self) -> dict[str, Any]:
            return {
                "backend_profile": {
                    "capabilities": {"real_vehicle": True, "requires_operator_approval": True}
                }
            }

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.tools = RealVehicleTools()  # type: ignore[assignment]
    runtime._execution_slot = threading.Lock()
    runtime._execution_thread_id = 0
    runtime._lock = threading.RLock()
    runtime._current = None

    result = runtime._execute_agent_tool("drone_takeoff", {"altitude": 3.0})

    assert result.ok is False
    assert result.data["status"] == "blocked"
    assert "Execute mode" in result.data["message"]


def test_repeated_verified_trajectory_becomes_reviewable_skill_candidate() -> None:
    with tempfile.TemporaryDirectory() as directory:
        memory = AgentMemory(Path(directory))
        for index in range(3):
            memory.remember_mission({
                "run_id": f"run_{index}",
                "intent": "inspect_area",
                "status": "completed",
                "summary": "inspect",
                "duration_sec": 5.0,
                "steps_total": 2,
                "steps_ok": 2,
                "tool_sequence": ["drone_get_status", "airsim_take_photo"],
                "verification_status": "passed",
            })

        candidates = memory.snapshot()["skill_candidates"]
        assert len(candidates) == 1
        assert candidates[0]["successes"] == 3
        assert candidates[0]["eligible_for_review"] is True


def test_task_run_store_persists_replayable_run_record() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = TaskRunStore(Path(directory))
        run = {
            "run_id": "run_store_1",
            "session_id": "session_1",
            "command": "take off",
            "summary": "起飞",
            "status": "running",
            "phase": "executing",
            "started_at": time.time(),
            "plan": {
                "steps": [
                    {"id": "s01", "label": "起飞", "tool": "drone_takeoff", "status": "running", "params": {"altitude": 3}},
                ],
            },
        }
        store.start_run(run, session_id="session_1")
        store.record_event({
            "timestamp": time.time(),
            "level": "info",
            "source": "tool",
            "message": "执行步骤",
            "data": {"run_id": "run_store_1"},
        })
        store.record_tool_result(
            run,
            {"id": "s01", "label": "起飞", "tool": "drone_takeoff", "params": {"altitude": 3}, "status": "completed"},
            _result("drone_takeoff", True, {"status": "ok"}),
        )
        run["status"] = "completed"
        run["phase"] = "completed"
        run["finished_at"] = time.time()
        run["plan"]["steps"][0]["status"] = "completed"
        store.finalize_run(run)

        snapshot = store.snapshot()
        assert snapshot["recent"][0]["run_id"] == "run_store_1"
        assert snapshot["recent"][0]["counters"]["events"] == 1
        assert snapshot["recent"][0]["counters"]["tool_calls"] == 1
        assert snapshot["recent"][0]["counters"]["steps_ok"] == 1
        record = store.get_run("run_store_1")
        assert record is not None
        assert record["tool_results"][0]["tool"] == "drone_takeoff"


def test_multimodal_content_is_encoded_for_openai_and_anthropic() -> None:
    data_url = "data:image/png;base64," + base64.b64encode(b"image-bytes").decode("ascii")
    planner = LLMMissionPlanner.__new__(LLMMissionPlanner)
    openai_content = planner._content_with_images("describe", [{"data_url": data_url}])

    assert isinstance(openai_content, list)
    assert openai_content[0] == {"type": "text", "text": "describe"}
    assert openai_content[1]["image_url"]["url"] == data_url

    anthropic_content = AnthropicClient._anthropic_content(openai_content)
    assert isinstance(anthropic_content, list)
    assert anthropic_content[1]["type"] == "image"
    assert anthropic_content[1]["source"]["media_type"] == "image/png"
    assert anthropic_content[1]["source"]["data"] == data_url.split(",", 1)[1]


def test_final_answer_stream_keeps_reasoning_out_of_final_answer(monkeypatch) -> None:
    class DummyClient:
        def stream_events(self, messages):
            yield {"type": "reasoning", "token": "检查状态"}
            yield {"type": "content", "token": "当前状态"}
            yield {"type": "reasoning", "token": "整理结论"}
            yield {"type": "content", "token": "正常"}

    planner = LLMMissionPlanner.__new__(LLMMissionPlanner)
    planner.last_error = ""
    planner.last_usage = {}
    planner._resolve_config = lambda model_id=None: {
        "api_key": "test",
        "model": "dummy",
        "max_tokens": 100,
        "temperature": 0.1,
    }
    monkeypatch.setattr(llm_module, "_create_client", lambda config: DummyClient())

    content_tokens: list[str] = []
    reasoning_tokens: list[str] = []
    answer = planner.final_answer_stream(
        "状态如何",
        "completed",
        None,
        None,
        on_token=content_tokens.append,
        on_reasoning=reasoning_tokens.append,
    )

    assert answer == "当前状态正常"
    assert content_tokens == ["当前状态", "正常"]
    assert reasoning_tokens == ["检查状态", "整理结论"]


def test_llm_plan_does_not_fallback_to_rules_when_model_unavailable() -> None:
    planner = LLMMissionPlanner()
    planner._resolve_config = lambda model_id=None: None  # type: ignore[method-assign]

    with pytest.raises(LLMUnavailableError):
        planner.plan(
            command="起飞向前飞三米",
            tools=[{"name": "drone_takeoff"}, {"name": "drone_move_relative"}],
            safety={},
            telemetry={},
            memory={},
        )


def test_llm_json_request_retries_empty_content_once() -> None:
    class FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat_json(self, messages):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("LLM returned empty content")
            return {"action": "", "is_complete": True, "reason": "ok"}, {"total_tokens": 3}

    client = FlakyClient()
    planner = LLMMissionPlanner()

    payload, usage = planner._chat_json_with_retries(client, [])

    assert client.calls == 2
    assert payload["reason"] == "ok"
    assert usage["total_tokens"] == 3


def test_status_readback_fast_path_only_matches_read_only_queries() -> None:
    runtime = object.__new__(AgentRuntime)

    assert runtime._is_status_readback_command("无人机位置在哪") is True
    assert runtime._is_status_readback_command("无人机连接状态和高度如何") is True
    assert runtime._is_status_readback_command("检查无人机状态，正常的话起飞") is False
    assert runtime._is_status_readback_command("告诉我位置然后向前飞一米") is False


def test_status_readback_answer_formats_current_telemetry() -> None:
    runtime = object.__new__(AgentRuntime)
    runtime.tools = SimpleNamespace(backend_id="px4_ros2")

    answer = runtime._format_status_readback_answer(
        {
            "backend": "px4_ros2",
            "position_ned": {"x": 1.2, "y": -0.5, "z": -3.0},
            "velocity_ned": {"vx": 0.3, "vy": 0.4, "vz": 0.0},
            "heading_deg": 91.5,
            "armed": True,
            "flying": True,
            "mode": "OFFBOARD",
            "has_collided": False,
        }
    )

    assert "px4_ros2" in answer
    assert "N 1.20 / E -0.50 / D -3.00 m" in answer
    assert "航向 91.5°" in answer


def test_agent_loop_require_llm_does_not_run_preemptive_guard_actions() -> None:
    class UnavailablePlanner:
        def decide_next_step(self, **_: Any) -> LoopDecision:
            raise LLMUnavailableError("model unavailable")

    tools = FakeTools({
        "airsim_take_photo": _result("airsim_take_photo", True, {"status": "ok", "image_base64": "abc"}),
        "airsim_vlm_analyze_image": _result("airsim_vlm_analyze_image", True, {"status": "ok"}),
    })
    loop = AgentLoop(
        tools,  # type: ignore[arg-type]
        UnavailablePlanner(),  # type: ignore[arg-type]
        FakeMemory(),  # type: ignore[arg-type]
    )

    with pytest.raises(LLMUnavailableError):
        loop.run(
            run_id="run_no_model",
            command="拍照看看有什么",
            capabilities={"image_capture": True},
            tool_cards=[
                {"name": "airsim_take_photo"},
                {"name": "airsim_vlm_analyze_image"},
            ],
            require_llm=True,
        )

    assert tools.calls == []


def test_fast_final_report_includes_flight_chain_and_image_result() -> None:
    planner = LLMMissionPlanner()
    plan = MissionPlan(
        "run_report",
        "起飞拍照看看有什么，随后返航降落",
        "plan_execute",
        "起飞拍照返航降落",
        [
            MissionStep("s01", "起飞", "drone_takeoff", {"altitude": 3}, "action", status="completed", result={"message": "takeoff complete"}),
            MissionStep("s02", "拍照", "airsim_take_photo", {"image_type": "scene"}, "perception", status="completed", result={"message": "image captured"}),
            MissionStep("s03", "图像分析", "airsim_vlm_analyze_image", {"source": "last_image"}, "perception", status="completed", result={"summary_zh": "画面中可见道路和建筑。"}),
            MissionStep("s04", "降落", "drone_land", {}, "action", status="completed", result={"message": "landing complete"}),
        ],
        planner_source="test",
    )

    answer = planner.final_answer_stream(
        "起飞拍照看看有什么，随后返航降落",
        "completed",
        plan,
        {"position_ned": {"x": 0.0, "y": 0.0, "z": -0.05}, "armed": False, "flying": False, "has_collided": False},
        verification={"status": "passed", "level": "ok", "summary": "任务执行后状态与目标一致。"},
        force_fallback=True,
    )

    assert "执行链路" in answer
    assert "图像结果：画面中可见道路和建筑" in answer
    assert "最终状态" in answer


def test_attachment_store_persists_metadata_without_session_base64() -> None:
    import src.agent.runtime as runtime_module

    data_url = "data:image/png;base64," + base64.b64encode(b"small-image").decode("ascii")
    with tempfile.TemporaryDirectory() as directory:
        previous = runtime_module.ATTACHMENTS_DIR
        runtime_module.ATTACHMENTS_DIR = Path(directory)
        try:
            runtime = AgentRuntime.__new__(AgentRuntime)
            stored = runtime._store_attachments([{"name": "test.png", "mime_type": "image/png", "data_url": data_url}])
            assert stored[0]["url"].startswith("/api/attachments/")
            assert "data_url" not in stored[0]
            hydrated = runtime._hydrate_attachments(stored)
            assert hydrated[0]["data_url"] == data_url
        finally:
            runtime_module.ATTACHMENTS_DIR = previous


def test_recent_context_excludes_current_user_behind_placeholder() -> None:
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime._lock = threading.RLock()
    runtime._messages = [
        ChatMessage("m1", "user", "old question", attachments=[{"storage_key": "old.png"}]),
        ChatMessage("m2", "assistant", "old answer"),
        ChatMessage("m3", "user", "current question"),
        ChatMessage("m4", "assistant", "", status="running"),
    ]
    runtime._hydrate_attachments = lambda items: [{**items[0], "data_url": "data:image/png;base64,eA=="}]  # type: ignore[method-assign]

    context = runtime._recent_chat_context()

    assert [item["content"] for item in context] == ["old question", "old answer"]
    assert context[0]["attachments"][0]["data_url"].startswith("data:image/png")


if __name__ == "__main__":
    tests = [
        test_async_tool_is_polled_to_terminal_state,
        test_agent_loop_allows_bounded_recovery_after_failure,
        test_status_readback_alone_does_not_hide_failure,
        test_unavailable_tool_is_not_reported_as_completed,
        test_llm_payload_with_unavailable_tool_is_not_complete,
        test_agent_loop_keeps_atomic_tools_available_with_markdown_guidance,
        test_visual_question_forces_capture_then_vlm_analysis,
        test_visual_confirmation_guard_uses_native_tools_before_blocking_2d_approach,
        test_visual_approach_is_blocked_without_safe_3d_target_or_approach_tool,
        test_tool_runtime_marks_started_result_as_non_terminal,
        test_invalid_tool_parameters_return_structured_failure,
        test_async_tool_without_task_id_is_rejected,
        test_cancelled_background_task_cannot_flip_back_to_completed,
        test_skill_subtools_use_governed_executor_callback,
        test_high_risk_real_vehicle_tool_cannot_bypass_execute_approval_path,
        test_repeated_verified_trajectory_becomes_reviewable_skill_candidate,
        test_task_run_store_persists_replayable_run_record,
        test_multimodal_content_is_encoded_for_openai_and_anthropic,
        test_attachment_store_persists_metadata_without_session_base64,
        test_recent_context_excludes_current_user_behind_placeholder,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


def test_async_settle_exits_quickly_when_task_status_tool_missing() -> None:
    """Non-AirSim backends do not register airsim_task_status: settling an
    async descriptor must return immediately instead of spinning the whole
    async_timeout polling an unknown tool."""

    def task_status(_: dict[str, Any]) -> ToolCallResult:
        return ToolCallResult(
            "airsim_task_status",
            {},
            False,
            {"status": "error", "message": "unknown tool: airsim_task_status"},
            time.time(),
            time.time(),
            error_code="UNKNOWN_TOOL",
        )

    tools = FakeTools({
        "drone_upload_mission": _result(
            "drone_upload_mission",
            True,
            {"status": "started", "task_id": "search_1"},
            terminal=False,
            task_id="search_1",
        ),
        "airsim_task_status": task_status,
    })
    planner = SequencePlanner([
        LoopDecision("drone_upload_mission", {"waypoints_json": "[]"}, "start async mission task"),
        LoopDecision(action="", reason="mission task accepted", is_complete=True),
    ])
    loop = AgentLoop(
        tools, planner, FakeMemory(), skills=FakeSkills(), async_timeout=60.0, async_poll_interval=0.05
    )
    started = time.time()
    state = loop.run(
        "run_no_status_tool",
        "upload mission",
        {"gps": True, "mode_control": True},
        [{"name": name} for name in tools.handlers],
        max_steps=4,
    )
    elapsed = time.time() - started
    assert elapsed < 2.0, f"settle did not exit promptly ({elapsed:.1f}s)"
    first = state.results[0]
    assert first.data["status"] == "accepted"
    assert "not available on this backend" in first.data["message"]


def test_completed_loop_clears_stale_failure_reason() -> None:
    """A run that fails mid-way and then recovers to completion must not
    carry the old failure reason into its final state — the frontend renders
    the error badge from failure_reason for an otherwise completed task."""
    tools = FakeTools({
        "drone_fly_to": _result("drone_fly_to", False, {"status": "failed", "message": "path blocked"}),
        "drone_get_status": _result("drone_get_status", True, {"status": "ok"}),
        "drone_hover": _result("drone_hover", True, {"status": "ok"}),
    })
    planner = SequencePlanner([
        LoopDecision("drone_fly_to", {"x": 5, "y": 0, "z": -3}, "try route"),
        LoopDecision("drone_hover", {}, "recover safely"),
        LoopDecision(action="", reason="recovered and complete", is_complete=True),
    ])
    loop = AgentLoop(tools, planner, FakeMemory(), skills=FakeSkills())
    state = loop.run(
        "run_recover",
        "fly then recover",
        {"flight_control": True},
        [{"name": name} for name in tools.handlers],
        max_steps=5,
    )
    assert state.status == "completed"
    assert state.failure_reason == ""


def test_nested_value_depth_limited() -> None:
    """Pathologically deep payloads (LLM output, tool results) must never
    blow the interpreter recursion limit."""
    deep = {"data": {}}
    node = deep["data"]
    for _ in range(2000):
        node["data"] = {}
        node = node["data"]
    # a deeply nested structure must terminate instead of RecursionError
    assert AgentLoop._nested_value(deep, "flying") is None
    loop = AgentLoop(None, None, None)  # instance methods only need the receiver
    assert loop._iter_nested_tool_results(deep) == []
    assert loop._result_contains_image(deep) is False
    assert loop._find_async_descriptor(deep) is None


def test_status_readback_fast_path_works_in_chat_mode() -> None:
    """Chat-mode status questions must also take the fast readback path,
    not the slow LLM streaming path (chat 模式状态问题秒回)."""

    class _FakeTools:
        backend_id = "airsim"

        def execute(self, tool, params, *, dry_run=False, blocked_by_supervisor=False, allow_reconnect=False):
            if tool == "drone_list_vehicles":
                return _result(tool, True, {"vehicles": ["Drone1", "Drone2"]})
            return _result(
                tool,
                True,
                {
                    "vehicle_name": str((params or {}).get("vehicle_name") or ""),
                    "armed": False,
                    "flying": False,
                    "position_ned": {"x": 0.1, "y": 0.2, "z": -0.05},
                },
            )

    runtime = object.__new__(AgentRuntime)
    runtime.tools = _FakeTools()
    captured: dict[str, Any] = {}

    def _capture(role, content, *, run_id="", status="", details=None, **_kwargs):
        captured.update({"role": role, "content": content, "status": status, "details": details or {}})

    runtime._append_message = _capture

    result = runtime._complete_status_readback_command("目前有几个无人机状态如何", "chat_test", {}, mode="chat")

    assert result["ok"] is True
    assert result["mode"] == "chat"
    details = captured["details"]
    assert details.get("mode") == "chat"
    reasoning = str(details.get("reasoning_text") or "")
    assert "快速回读" in reasoning
    assert "状态总结" in reasoning
    assert any(item.get("tool") == "drone_list_vehicles" for item in details.get("process_trace") or [])


def test_execute_run_not_orphaned_during_startup_grace() -> None:
    """Freshly submitted execute-run messages must NOT be marked as orphaned
    while _plan_and_execute has not yet set self._current (LLM-bound gap)."""
    runtime = object.__new__(AgentRuntime)
    runtime._started_at = time.time()
    runtime._messages = [
        ChatMessage(
            id="msg_prev", role="assistant", content="prev done",
            run_id="execute_prev", status="complete",
        ),
        ChatMessage(
            id="msg_new", role="assistant", content="",
            run_id="execute_new", status="running",
            details={"mode": "execute", "phase": "understanding"},
            created_at=time.time() - 2.0,
            updated_at=time.time() - 2.0,
        ),
    ]
    runtime._current = RunState(
        run_id="execute_prev", command="prev", intent="prev", summary="prev",
        status="completed", phase="completed", mode="execute", execute=True,
    )
    runtime._pending_run_ids = {"execute_new"}
    runtime._lock = threading.RLock()

    assert runtime._mark_orphan_running_messages_locked() is False
    new_msg = next(m for m in runtime._messages if m.run_id == "execute_new")
    assert new_msg.status == "running"
    assert new_msg.content == ""


def test_execute_run_orphaned_after_grace_when_not_pending() -> None:
    """A stale execute-run message (not pending, older than grace) with a
    different active run must still be marked interrupted."""
    runtime = object.__new__(AgentRuntime)
    runtime._started_at = time.time() - 600
    runtime._messages = [
        ChatMessage(
            id="msg_stale", role="assistant", content="",
            run_id="execute_stale", status="running",
            details={"mode": "execute", "phase": "executing"},
            created_at=time.time() - 120.0,
            updated_at=time.time() - 120.0,
        ),
    ]
    runtime._current = RunState(
        run_id="execute_other", command="other", intent="other", summary="other",
        status="running", phase="executing", mode="execute", execute=True,
    )
    runtime._pending_run_ids = set()
    runtime._lock = threading.RLock()

    assert runtime._mark_orphan_running_messages_locked() is True
    stale_msg = next(m for m in runtime._messages if m.run_id == "execute_stale")
    assert stale_msg.status == "error"
    assert stale_msg.details.get("phase") == "interrupted"
