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

def test_status_readback_matches_multi_vehicle_questions() -> None:
    """'三台无人机嘛？' style questions must route to the fast readback
    path and report every vehicle, not just the default one."""
    from src.agent.runtime import AgentRuntime

    rt = object.__new__(AgentRuntime)
    for text in ("三台无人机嘛？", "现在有几架无人机", "一共几台无人机", "无人机数量是多少"):
        assert rt._is_status_readback_command(text) is True, text
    # flight intents stay out of the readback path
    assert rt._is_status_readback_command("让无人机起飞三米") is False


def test_vehicle_line_format() -> None:
    from src.agent.runtime import AgentRuntime

    rt = object.__new__(AgentRuntime)
    landed = rt._format_vehicle_line(
        "Drone1",
        {"armed": False, "flying": False, "position_ned": {"x": 1.5, "y": -2.0, "z": -3.25}},
    )
    # AirSim keeps the last airborne z after landing: a landed vehicle must
    # never be reported with a non-zero altitude
    assert "Drone1" in landed and "未解锁" in landed and "高度 0 m（已着陆）" in landed
    flying = rt._format_vehicle_line(
        "Drone2",
        {"armed": True, "flying": True, "position_ned": {"x": 0.0, "y": 0.0, "z": -3.25}},
    )
    assert "高度约 3.25 m" in flying


def test_strip_plan_json_draft_cuts_model_json_draft() -> None:
    """思考流末尾常带模型起草的计划 JSON 草稿——思考块只留自然语言推理。"""
    from src.agent.runtime import AgentRuntime

    rt = object.__new__(AgentRuntime)
    streamed = (
        "操作员同时要求查看 Drone3 状态并让其降落，先读取状态作为降落前提，"
        "再单独执行降落；任务仅针对 Drone3 单机。\n\n"
        "执行计划（3 步）：drone_get_status → drone_land → drone_get_status\n\n"
        '{\n    "intent": "check and land drone3",\n'
        '    "summary": "对 Drone3 执行状态回读，然后执行降落指令",\n'
        '    "steps": []'
    )
    cleaned = rt._strip_plan_json_draft(streamed)
    assert "操作员同时要求查看 Drone3" in cleaned
    assert "执行计划（3 步）" in cleaned
    assert '"intent"' not in cleaned and '"steps"' not in cleaned

    # 截断的 JSON 草稿同样要截干净
    truncated = '先读取 Drone3 状态再降落。{"intent": "check and la'
    assert rt._strip_plan_json_draft(truncated) == "先读取 Drone3 状态再降落。"

    # 正文里出现与规划无关的花括号/JSON 不受影响
    prose = '状态里有 {"speed": 0} 字样，属于正常回读内容。'
    assert rt._strip_plan_json_draft(prose) == prose
    assert rt._strip_plan_json_draft("") == ""


# ── goal completion gating (fix: 目标未达成不得误判完成) ──


def test_run_goal_injects_motion_completion_criteria() -> None:
    """LLM 常不提供 success_criteria——计划含运动步骤时必须自动注入
    '这些运动步骤必须成功执行过' 的机器校验判据，防止假完成。"""
    from src.agent.agent_loop import _PLANNED_MOTION_TOOLS, AgentLoop

    plan = _plan(_step("drone_takeoff"), _step("drone_move_relative"))
    goal = AgentLoop._run_goal(plan)
    criteria = goal.get("success_criteria") or []
    assert any(c.get("metric") == "planned_motion_steps_ok" for c in criteria)
    motion = next(c for c in criteria if c.get("metric") == "planned_motion_steps_ok")
    assert set(motion["tools"]) == {"drone_takeoff", "drone_move_relative"}

    # 无运动步骤（纯回读/拍照计划）不注入，避免误伤"已在地面只核验"等场景
    read_only = _plan(_step("drone_get_status"))
    assert "success_criteria" not in AgentLoop._run_goal(read_only)


def test_verify_completion_blocks_when_planned_motion_missing() -> None:
    """LLM 声明完成但计划中的运动动作没有成功执行记录 → 校验必须拦截，
    驱动纠错/失败，而不是放行 completed。"""
    from types import SimpleNamespace

    from src.agent.agent_loop import AgentLoop
    from src.agent.loop_types import LoopObservation, LoopState

    loop = AgentLoop.__new__(AgentLoop)
    plan = _plan(_step("drone_takeoff"), _step("drone_move_relative"))
    goal = AgentLoop._run_goal(plan)

    state = LoopState(run_id="run_test", command="起飞并向前飞", status="running", max_steps=5)
    observation = LoopObservation(
        step_index=1,
        world_state={"connected": True},
        last_action_result=None,
        elapsed_since_start=0.0,
    )
    # 循环里只回读了状态——没有成功的运动动作
    state.results = [SimpleNamespace(tool="drone_get_status", ok=True)]
    verification = loop._verify_completion(goal, state, observation)
    assert verification["criteria"]
    assert verification["satisfied"] is False
    missing = next(r for r in verification["results"] if r["metric"] == "planned_motion_steps_ok")
    assert missing["satisfied"] is False
    assert "drone_move_relative" in missing["detail"]

    # 补做成功后再校验放行
    state.results.append(SimpleNamespace(tool="drone_takeoff", ok=True))
    state.results.append(SimpleNamespace(tool="drone_move_relative", ok=True))
    assert loop._verify_completion(goal, state, observation)["satisfied"] is True


def test_strip_plan_json_draft_cuts_correction_decision_json() -> None:
    """纠错决策 JSON（{"action": ...}）也必须在思考块里被截掉。"""
    from src.agent.runtime import AgentRuntime

    rt = object.__new__(AgentRuntime)
    streamed = (
        "当前飞机仍在爬升中，需要先补一次起飞到安全高度，再执行前移。\n\n"
        '{\n    "action": "drone_takeoff",\n    "params": {"altitude": 3.0},\n'
        '    "reason": "补飞到安全高度",\n    "is_complete": false\n}'
    )
    cleaned = rt._strip_plan_json_draft(streamed)
    assert "当前飞机仍在爬升中" in cleaned
    assert '"action"' not in cleaned and '"is_complete"' not in cleaned
