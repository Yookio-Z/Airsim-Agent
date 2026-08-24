"""Contract tests for the Plan-Execute ⇄ ReAct collaboration state machine.

Covers:
  * observation-dependency detection (structural, not keyword routing),
  * planner-declared execution_mode routing,
  * correction-loop entry gating (budget, connection failures, operator veto),
  * structured failure context for the ReAct correction command,
  * LLM payload parsing of execution_mode / needs_observation.
"""

from __future__ import annotations

import threading

from src.agent.llm import LLMMissionPlanner
from src.agent.planner import MissionPlan, MissionStep
from src.agent.runtime import (
    CORRECTION_ATTEMPTS_MAX,
    AgentRuntime,
    RunState,
)


def _step(tool: str, status: str = "pending", result: dict | None = None) -> MissionStep:
    return MissionStep(id="s01", label=tool, tool=tool, status=status, result=result)


def _plan(*steps: MissionStep, execution_mode: str = "auto") -> MissionPlan:
    return MissionPlan(
        run_id="run_test",
        command="测试任务",
        intent="test",
        summary="test",
        steps=list(steps),
        execution_mode=execution_mode,
    )


def _run(**overrides) -> RunState:
    defaults: dict = {
        "run_id": "run_test",
        "command": "起飞到5米",
        "intent": "test",
        "summary": "test",
        "execute": True,
        "route_strategy": "plan_execute",
        "status": "failed",
        "failure_reason": "drone_takeoff failed: altitude timeout",
        "verification": {"level": "failed", "summary": "位置未达预期"},
        "final_telemetry": {"position_ned": {"x": 1.0, "y": 2.0, "z": -3.0}, "flying": True},
        "plan": _plan(),
    }
    defaults.update(overrides)
    return RunState(**defaults)


def _runtime() -> AgentRuntime:
    """Lightweight AgentRuntime instance without __init__ side effects."""
    rt = object.__new__(AgentRuntime)
    rt._cancelled_request_ids = set()
    rt._cancel_requested = threading.Event()
    rt._lock = threading.RLock()
    return rt


# ── observation-dependency detection ──


def test_observation_then_motion_requires_agent_loop() -> None:
    plan = _plan(_step("airsim_take_photo"), _step("drone_move_relative"))
    assert AgentRuntime._plan_has_observation_dependency(plan) is True
    assert AgentRuntime._plan_requires_agent_loop(plan) is True


def test_motion_only_plan_stays_plan_execute() -> None:
    plan = _plan(_step("drone_takeoff"), _step("drone_fly_to"), _step("drone_land"))
    assert AgentRuntime._plan_has_observation_dependency(plan) is False
    assert AgentRuntime._plan_requires_agent_loop(plan) is False


def test_observation_only_plan_stays_plan_execute() -> None:
    plan = _plan(_step("airsim_take_photo"), _step("airsim_vlm_analyze_image"))
    assert AgentRuntime._plan_has_observation_dependency(plan) is False


def test_observation_after_motion_does_not_count() -> None:
    plan = _plan(_step("drone_move_relative"), _step("airsim_take_photo"))
    assert AgentRuntime._plan_has_observation_dependency(plan) is False


def test_vlm_confirm_before_approach_routes_to_agent_loop() -> None:
    plan = _plan(
        _step("airsim_take_photo"),
        _step("airsim_vlm_confirm_target"),
        _step("drone_fly_to"),
    )
    assert AgentRuntime._plan_has_observation_dependency(plan) is True


def test_planner_declared_agent_loop_wins_even_without_observation() -> None:
    plan = _plan(_step("drone_takeoff"), execution_mode="agent_loop")
    assert AgentRuntime._plan_requires_agent_loop(plan) is True


def test_null_plan_never_routes_to_agent_loop() -> None:
    assert AgentRuntime._plan_requires_agent_loop(None) is False


# ── correction-loop entry gating ──


def test_failed_plan_execute_enters_correction() -> None:
    assert _runtime()._should_enter_correction_loop(_run()) is True


def test_correction_skipped_for_connection_failures() -> None:
    rt = _runtime()
    run = _run(failure_reason="drone_connect failed: connection refused")
    assert rt._should_enter_correction_loop(run) is False


def test_correction_skipped_after_operator_veto() -> None:
    rt = _runtime()
    run = _run(failure_reason="operator rejected")
    assert rt._should_enter_correction_loop(run) is False


def test_correction_skipped_when_budget_exhausted() -> None:
    rt = _runtime()
    run = _run(correction_attempts=CORRECTION_ATTEMPTS_MAX)
    assert rt._should_enter_correction_loop(run) is False


def test_correction_skipped_for_non_plan_execute_strategies() -> None:
    rt = _runtime()
    run = _run(route_strategy="agent_loop")
    assert rt._should_enter_correction_loop(run) is False


def test_correction_skipped_for_plan_only_runs() -> None:
    rt = _runtime()
    run = _run(execute=False)
    assert rt._should_enter_correction_loop(run) is False


def test_verification_failure_alone_triggers_correction() -> None:
    rt = _runtime()
    run = _run(status="completed", verification={"level": "failed", "summary": "位置未达预期"})
    assert rt._should_enter_correction_loop(run) is True


# ── structured correction context ──


def test_correction_command_carries_structured_failure_context() -> None:
    failed = _step("drone_fly_to", status="failed", result={"message": "target unreachable"})
    plan = _plan(_step("drone_takeoff", status="completed"), failed)
    run = _run(plan=plan, verification={"level": "failed", "summary": "最终位置偏差 8m"})
    command = _runtime()._correction_command(run)
    assert "原始任务：起飞到5米" in command
    assert "drone_takeoff failed: altitude timeout" in command
    assert "校验摘要：最终位置偏差 8m" in command
    assert "失败步骤：s01 drone_fly_to：target unreachable" in command
    assert "当前 NED 位置：N 1.0 / E 2.0 / D -3.0" in command


def test_correction_command_without_telemetry_stays_compact() -> None:
    run = _run(final_telemetry={})
    command = _runtime()._correction_command(run)
    assert "当前 NED" not in command
    assert "原始任务" in command


def test_agent_loop_primary_command_references_plan() -> None:
    run = _run(command="搜索红色车辆")
    command = AgentRuntime._agent_loop_primary_command(run)
    assert "搜索红色车辆" in command
    assert "观察" in command


# ── LLM payload parsing ──


def test_plan_from_payload_parses_execution_mode_and_needs_observation() -> None:
    planner = LLMMissionPlanner()
    payload = {
        "intent": "visual_search",
        "summary": "搜索并确认目标",
        "execution_mode": "agent_loop",
        "steps": [
            {
                "label": "拍照",
                "tool": "airsim_take_photo",
                "layer": "perception",
                "needs_observation": True,
                "params": {},
            },
            {
                "label": "移动",
                "tool": "drone_move_relative",
                "layer": "action",
                "params": {"forward_m": 2.0},
            },
        ],
    }
    plan = planner._plan_from_payload("搜索目标并拍照", payload, {"airsim_take_photo", "drone_move_relative"})
    assert plan.execution_mode == "agent_loop"
    assert plan.steps[0].needs_observation is True
    assert plan.steps[1].needs_observation is False


def test_plan_from_payload_rejects_unknown_execution_mode() -> None:
    planner = LLMMissionPlanner()
    payload = {
        "steps": [{"tool": "drone_takeoff", "params": {"altitude": 3.0}}],
        "execution_mode": "banana",
    }
    plan = planner._plan_from_payload("起飞", payload, {"drone_takeoff"})
    assert plan.execution_mode == "auto"


# ── bounded serialization (regression for RecursionError in /api/state) ──


def test_mission_step_to_dict_bounded_for_pathological_depth() -> None:
    """A sick LLM output nested ~1000 levels must not blow the recursion
    limit during run serialization (this took down every /api/state poll)."""
    from src.agent.planner import MissionStep

    deep = {}
    node = deep
    for _ in range(1200):
        node["data"] = {}
        node = node["data"]
    step = MissionStep(id="s1", label="fly", tool="drone_fly_to", params=deep, result={"nested": {"x": 1}})
    dumped = step.to_dict()
    assert dumped["tool"] == "drone_fly_to"
    node = dumped["params"]
    depth = 0
    while isinstance(node, dict) and "[bounded]" not in node and "data" in node:
        node = node["data"]
        depth += 1
    assert depth < 30  # bounded: the chain was cut far below the input depth
    assert node.get("[bounded]") is True
    assert dumped["result"]["nested"]["x"] == 1


def test_mission_step_to_dict_self_reference_terminates() -> None:
    from src.agent.planner import MissionStep

    cyclic: dict = {}
    cyclic["self"] = cyclic
    step = MissionStep(id="s2", label="status", tool="drone_get_status", result=cyclic)
    dumped = step.to_dict()
    node = dumped["result"]
    depth = 0
    while isinstance(node, dict) and "[bounded]" not in node and "self" in node:
        node = node["self"]
        depth += 1
    assert depth < 30  # the self-reference was materialized as a bounded chain
    assert node.get("[bounded]") is True