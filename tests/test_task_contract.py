"""Tests for the task-contract verification (MissionPlan.goal + loop gating).

The agent loop must not accept a model-declared completion when the plan's
machine-checkable success criteria are unsatisfied; one corrective action is
allowed before the run completes with verification_status="failed".
"""

from __future__ import annotations

import time

import pytest

from src.agent.agent_loop import AgentLoop
from src.agent.llm import LLMMissionPlanner
from src.agent.loop_types import LoopDecision, LoopObservation, LoopState
from src.agent.planner import MissionPlan, MissionPlanner
from src.agent.tool_executor import ToolCallResult


# ---------------------------------------------------------------------------
# rule planner goal synthesis
# ---------------------------------------------------------------------------


def test_rule_plan_goals():
    planner = MissionPlanner()
    search = planner.plan("搜索一辆红色汽车")
    assert search.goal["success_criteria"] == [{"metric": "target_confirmed", "target": "car"}]

    fly = planner.plan("飞到 x=10 y=20 z=-5")
    criteria = fly.goal["success_criteria"]
    assert criteria[0]["metric"] == "position_reached"
    assert criteria[0]["x"] == 10.0 and criteria[0]["tolerance"] == 1.5

    takeoff = planner.plan("起飞到 5 米高度")
    assert takeoff.goal["success_criteria"] == [{"metric": "flying_at", "altitude": 5.0, "tolerance": 1.0}]

    land = planner.plan("降落")
    assert land.goal["success_criteria"] == [{"metric": "landed"}]

    photo = planner.plan("拍一张照片")
    assert photo.goal["success_criteria"] == [{"metric": "photo_taken"}]


def test_llm_goal_parsing_keeps_only_known_metrics():
    planner = object.__new__(LLMMissionPlanner)
    goal = planner._goal_from_payload(
        {
            "objective": "飞到目标点",
            "success_criteria": [
                {"metric": "position_reached", "x": 1, "y": 2, "z": -3, "tolerance": 0.5},
                {"metric": "fly_to_the_moon"},  # unknown metric must be dropped
                {"metric": "target_confirmed", "target": "car"},
            ],
        },
        "命令",
    )
    metrics = [c["metric"] for c in goal["success_criteria"]]
    assert metrics == ["position_reached", "target_confirmed"]
    assert goal["objective"] == "飞到目标点"


# ---------------------------------------------------------------------------
# verification primitives
# ---------------------------------------------------------------------------


def _loop():
    from src.agent.memory import AgentMemory

    return AgentLoop(
        tools=_FakeTools(),
        planner=object.__new__(LLMMissionPlanner),  # type: ignore[arg-type]
        memory=AgentMemory(data_dir=None),
        skills=None,
    )


class _FakeTools:
    READ_ONLY_TOOLS = {"drone_get_status", "airsim_take_photo", "airsim_vlm_confirm_target"}

    def __init__(self, drone: dict | None = None) -> None:
        self.drone = drone or {"flying": True, "position_ned": {"x": 0.0, "y": 0.0, "z": -3.0}}
        self.calls: list[tuple[str, dict]] = []

    def status_snapshot(self) -> dict:
        return {"backend": "fake", "connected": True, "drone": dict(self.drone)}

    def list_tools(self) -> list[dict]:
        return []

    def execute(self, name, params, dry_run=False, blocked_by_supervisor=False):
        self.calls.append((name, dict(params or {})))
        started = time.time()
        return ToolCallResult(name, dict(params or {}), True, {"status": "ok", "tool": name}, started, time.time())


def _state(run_id="run_v", status="completed") -> LoopState:
    return LoopState(run_id=run_id, command="测试", status=status)


def test_verify_flying_at():
    loop = _loop()
    obs = LoopObservation(step_index=1, world_state={"drone": {"flying": True, "position_ned": {"x": 0, "y": 0, "z": -5.0}}})
    state = _state()
    result = loop._verify_criterion({"metric": "flying_at", "altitude": 5.0, "tolerance": 1.0}, state, obs)
    assert result["satisfied"] is True
    obs_low = LoopObservation(step_index=1, world_state={"drone": {"flying": True, "position_ned": {"x": 0, "y": 0, "z": -2.0}}})
    result = loop._verify_criterion({"metric": "flying_at", "altitude": 5.0, "tolerance": 1.0}, state, obs_low)
    assert result["satisfied"] is False


def test_verify_position_and_landed():
    loop = _loop()
    state = _state()
    obs = LoopObservation(step_index=1, world_state={"drone": {"position_ned": {"x": 10.0, "y": 20.0, "z": -5.0}}})
    result = loop._verify_criterion({"metric": "position_reached", "x": 10, "y": 20, "z": -5, "tolerance": 1.5}, state, obs)
    assert result["satisfied"] is True
    obs_off = LoopObservation(step_index=1, world_state={"drone": {"position_ned": {"x": 3.0, "y": 20.0, "z": -5.0}}})
    result = loop._verify_criterion({"metric": "position_reached", "x": 10, "y": 20, "z": -5, "tolerance": 1.5}, state, obs_off)
    assert result["satisfied"] is False

    landed = loop._verify_criterion({"metric": "landed"}, state, LoopObservation(step_index=1, world_state={"drone": {"flying": False}}))
    assert landed["satisfied"] is True
    airborne = loop._verify_criterion({"metric": "landed"}, state, LoopObservation(step_index=1, world_state={"drone": {"flying": True}}))
    assert airborne["satisfied"] is False


def test_verify_target_confirmed():
    loop = _loop()
    state = _state()
    found_state = _state()
    found_state.results.append(_result("skill:search", True, {"target_found": True, "target_class": "car"}))
    assert loop._verify_criterion({"metric": "target_confirmed", "target": "car"}, found_state, LoopObservation(1, {}))["satisfied"] is True

    not_found_state = _state()
    not_found_state.results.append(_result("skill:search", True, {"target_found": False, "status": "target_not_confirmed"}))
    assert loop._verify_criterion({"metric": "target_confirmed", "target": "car"}, not_found_state, LoopObservation(1, {}))["satisfied"] is True

    nothing_state = _state()
    nothing_state.results.append(_result("drone_get_status", True, {"status": "ok"}))
    assert loop._verify_criterion({"metric": "target_confirmed", "target": "car"}, nothing_state, LoopObservation(1, {}))["satisfied"] is False


def test_verify_status_ok_and_photo():
    loop = _loop()
    ok_state = _state()
    ok_state.results.append(_result("drone_get_status", True, {"status": "ok"}))
    assert loop._verify_criterion({"metric": "status_ok"}, ok_state, LoopObservation(1, {}))["satisfied"] is True
    bad_state = _state()
    bad_state.results.append(_result("drone_get_status", False, {"status": "error", "message": "boom"}))
    assert loop._verify_criterion({"metric": "status_ok"}, bad_state, LoopObservation(1, {}))["satisfied"] is False

    photo_state = _state()
    photo_state.results.append(_result("airsim_take_photo", True, {"image_saved_to": "captures/a.png"}))
    assert loop._verify_criterion({"metric": "photo_taken"}, photo_state, LoopObservation(1, {}))["satisfied"] is True


def test_status_ok_fails_on_empty_loop():
    """A loop that declares completion without executing anything must not
    pass the status_ok gate (fixes the '空转绕过' hole)."""
    loop = _loop()
    empty = _state()
    assert loop._verify_criterion({"metric": "status_ok"}, empty, LoopObservation(1, {}))["satisfied"] is False


def test_unevaluated_criteria_do_not_fail_completion():
    """Missing telemetry must not fail the run: criteria become unevaluated
    warnings instead (设计: 无法评估 → 接受完成 + warning)."""
    loop = _loop()
    state = _state()
    obs_no_telemetry = LoopObservation(step_index=1, world_state={"drone": {}})
    flying = loop._verify_criterion({"metric": "flying_at", "altitude": 5.0, "tolerance": 1.0}, state, obs_no_telemetry)
    assert flying["satisfied"] is True
    assert flying["evaluated"] is False
    position = loop._verify_criterion({"metric": "position_reached", "x": 1, "y": 2, "z": -3}, state, obs_no_telemetry)
    assert position["satisfied"] is True and position["evaluated"] is False
    landed = loop._verify_criterion({"metric": "landed"}, state, obs_no_telemetry)
    assert landed["satisfied"] is True and landed["evaluated"] is False

    verification = loop._verify_completion(
        {"success_criteria": [{"metric": "flying_at", "altitude": 5.0, "tolerance": 1.0}]},
        state,
        obs_no_telemetry,
    )
    assert verification["satisfied"] is True
    assert verification["unevaluated"]


def test_stale_frame_is_not_recent():
    """Frames older than the freshness window must not satisfy guards."""
    loop = _loop()
    stale = _state()
    old = _result("airsim_take_photo", True, {"image_saved_to": "captures/old.png"})
    old.timestamp = time.time() - loop._FRAME_MAX_AGE_S - 30
    stale.results.append(old)
    assert loop._has_recent_image(stale) is False
    assert loop._latest_image_age(stale) > loop._FRAME_MAX_AGE_S

    fresh = _state()
    new = _result("airsim_take_photo", True, {"image_saved_to": "captures/new.png"})
    new.timestamp = time.time() - 5
    fresh.results.append(new)
    assert loop._has_recent_image(fresh) is True
    assert loop._latest_image_age(fresh) <= 5.5


def _result(tool, ok, data):
    from src.agent.loop_types import LoopActionResult

    return LoopActionResult(step_index=1, tool=tool, params={}, ok=ok, data=data, duration_ms=1.0)


# ---------------------------------------------------------------------------
# loop integration: completion gating
# ---------------------------------------------------------------------------


class _FakePlanner:
    def __init__(self, decisions: list[LoopDecision]) -> None:
        self.decisions = list(decisions)

    def decide_next_step(self, **kwargs):
        if self.decisions:
            return self.decisions.pop(0)
        return LoopDecision(action="", reason="done", is_complete=True)

    def summarize_attempt(self, *args, **kwargs):
        return ""


def _run_loop(tmp_path, tools, decisions, plan_goal):
    from src.agent.memory import AgentMemory
    from src.agent.skill_registry import SkillRegistry

    plan = MissionPlan(run_id="run_v", command="测试", intent="test", summary="test", steps=[], goal=plan_goal)
    loop = AgentLoop(
        tools=tools,  # type: ignore[arg-type]
        planner=_FakePlanner(decisions),  # type: ignore[arg-type]
        memory=AgentMemory(data_dir=tmp_path),
        skills=SkillRegistry(overrides_path=tmp_path / "skills.json"),
    )
    return loop.run(
        run_id="run_v",
        command="测试",
        capabilities={},
        tool_cards=[{"name": "drone_get_status", "purpose": "status"}],
        initial_plan=plan,
        max_steps=5,
    )


def test_loop_rejects_completion_until_criterion_satisfied(tmp_path):
    tools = _FakeTools(drone={"flying": True, "position_ned": {"x": 0.0, "y": 0.0, "z": -3.0}})
    goal = {"objective": "起飞到5米", "success_criteria": [{"metric": "flying_at", "altitude": 5.0, "tolerance": 1.0}]}
    state = _run_loop(
        tmp_path,
        tools,
        [LoopDecision(action="", reason="任务完成", is_complete=True)],
        goal,
    )
    # altitude 3m vs criterion 5m: the loop first ran a corrective status read,
    # then completed with a failed verification (LLM 提议被确定性验证否决).
    assert "drone_get_status" in [name for name, _ in tools.calls]
    assert state.status == "completed"
    assert state.verification_status == "failed"


def test_loop_accepts_completion_when_criterion_met(tmp_path):
    tools = _FakeTools(drone={"flying": True, "position_ned": {"x": 0.0, "y": 0.0, "z": -5.0}})
    goal = {"objective": "起飞到5米", "success_criteria": [{"metric": "flying_at", "altitude": 5.0, "tolerance": 1.0}]}
    state = _run_loop(
        tmp_path,
        tools,
        [LoopDecision(action="", reason="任务完成", is_complete=True)],
        goal,
    )
    assert state.status == "completed"
    assert state.verification_status == "ok"
    assert tools.calls == []  # no corrective action needed


def test_loop_ignores_completion_gate_when_no_criteria(tmp_path):
    tools = _FakeTools()
    state = _run_loop(tmp_path, tools, [LoopDecision(action="", reason="完", is_complete=True)], {})
    assert state.status == "completed"
    assert state.verification_status == ""
