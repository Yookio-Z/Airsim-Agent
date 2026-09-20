"""执行中补充指令（steer）与计划步骤偏离重试。

实测回归：一条"靠近那辆车并确认特征"的任务，抵近步骤因为"那一刻画面里没有
锁定目标"被判为偏离计划后整步被永久跳过，循环原地回读检测直到被打断；操作员
随后发的纠正指令又因为旧任务未及时停止而被直接拒绝。这里锁住两条修复：

1. 计划步骤首次偏离只是转一轮 ReAct 纠错，游标保留，下一轮回到该步骤重试，
   连续偏离到上限才跳过；
2. 执行中提交的新指令作为补充指令并入当前循环，而不是取消任务重来。
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from src.agent.agent_loop import AgentLoop
from src.agent.loop_types import LoopDecision
from src.agent.planner import MissionPlan, MissionStep
from src.agent.runtime import AgentRuntime
from src.agent.tool_executor import ToolCallResult


class _FakeAxis:
    """perception_axis：primary 为 None 表示画面里还没有锁定目标。"""

    enabled = True

    def __init__(self) -> None:
        self.primary = None

    def snapshot(self) -> dict:
        return {"primary": self.primary}


class _FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.perception_axis = _FakeAxis()
        self.drone = {"flying": True, "position_ned": {"x": 0.0, "y": 0.0, "z": -3.0}}

    def status_snapshot(self) -> dict:
        return {"backend": "fake", "connected": True, "drone": dict(self.drone)}

    def list_tools(self) -> list[dict]:
        return [{"name": name} for name in ("airsim_detect_objects", "drone_approach_target", "drone_get_status")]

    def execute(self, name, params, dry_run=False, blocked_by_supervisor=False):
        self.calls.append((name, dict(params or {})))
        started = time.time()
        return ToolCallResult(name, dict(params or {}), True, {"status": "ok", "tool": name}, started, time.time())


class _LockingTools(_FakeTools):
    """检测成功即认为目标已锁定（模拟"先搜索、再抵近"的真实序列）。"""

    def execute(self, name, params, dry_run=False, blocked_by_supervisor=False):
        result = super().execute(name, params, dry_run, blocked_by_supervisor)
        if name == "airsim_detect_objects":
            self.perception_axis.primary = {"track_id": 7}
        return result


class _RecordingPlanner:
    """记录每轮决策收到的 command，并按序回放预设决策。"""

    def __init__(self, decisions: list[LoopDecision]) -> None:
        self.decisions = list(decisions)
        self.commands: list[str] = []
        self.last_reasoning = ""

    def decide_next_step(self, **kwargs):
        self.commands.append(str(kwargs.get("command") or ""))
        if self.decisions:
            return self.decisions.pop(0)
        return LoopDecision(action="", reason="没有更多动作", is_complete=True)

    def summarize_attempt(self, *args, **kwargs):
        return ""


def _plan(steps: list[MissionStep]) -> MissionPlan:
    return MissionPlan(run_id="run_s", command="靠近那辆车", intent="approach", summary="", steps=steps, goal={})


def _run_loop(tmp_path, tools, planner, plan, **loop_kwargs):
    from src.agent.memory import AgentMemory

    loop = AgentLoop(
        tools=tools,  # type: ignore[arg-type]
        planner=planner,  # type: ignore[arg-type]
        memory=AgentMemory(data_dir=tmp_path),
        **loop_kwargs,
    )
    return loop.run(
        run_id="run_s",
        command="靠近那辆车，确认特征，保持距离锁定它",
        capabilities={},
        tool_cards=[],
        initial_plan=plan,
        max_steps=4,
        require_llm=True,
        reactive=True,
    )


def test_planned_step_is_retried_after_a_deviation(tmp_path):
    """抵近步骤第一次因"画面没有锁定目标"偏离时不能被永久跳过。"""
    tools = _LockingTools()
    planner = _RecordingPlanner([LoopDecision(action="airsim_detect_objects", params={}, reason="先搜索目标")])
    plan = _plan([MissionStep(id="s1", label="靠近目标", tool="drone_approach_target")])
    state = _run_loop(tmp_path, tools, planner, plan)

    executed = [name for name, _ in tools.calls]
    assert "airsim_detect_objects" in executed
    assert "drone_approach_target" in executed, executed
    assert state.status == "completed"


def test_repeated_deviation_eventually_skips_the_step(tmp_path):
    """同一步骤持续偏离（目标始终没锁定）时跳过该步，避免死循环。"""
    tools = _FakeTools()  # primary 始终为 None
    planner = _RecordingPlanner(
        [
            LoopDecision(action="airsim_detect_objects", params={}, reason="再搜索"),
            LoopDecision(action="airsim_detect_objects", params={}, reason="再搜索"),
            LoopDecision(action="", reason="无法靠近", is_complete=True),
        ]
    )
    plan = _plan([MissionStep(id="s1", label="靠近目标", tool="drone_approach_target")])
    state = _run_loop(tmp_path, tools, planner, plan)

    executed = [name for name, _ in tools.calls]
    assert "drone_approach_target" not in executed
    assert executed.count("airsim_detect_objects") == 2
    assert state.status == "completed"


def test_steer_text_reaches_the_next_decision(tmp_path):
    """执行中追加的补充指令必须出现在下一轮的模型输入里。"""
    tools = _FakeTools()
    queue = ["不要原地打转，先靠近那辆车"]
    delivered: list[list[str]] = []

    def take_steer() -> list[str]:
        items = list(queue)
        queue.clear()
        delivered.append(items)
        return items

    planner = _RecordingPlanner(
        [
            LoopDecision(action="drone_get_status", params={}, reason="先看看状态"),
            LoopDecision(action="", reason="完成", is_complete=True),
        ]
    )
    events: list[tuple[str, str, str, dict]] = []
    _run_loop(
        tmp_path,
        tools,
        planner,
        _plan([]),
        on_event=lambda level, source, message, data: events.append((level, source, message, data)),
        steer_provider=take_steer,
    )

    assert delivered and delivered[0] == ["不要原地打转，先靠近那辆车"]
    assert any("[操作员补充指令] 不要原地打转" in command for command in planner.commands), planner.commands
    assert any(str(data.get("kind") or "") == "steer" for _, _, _, data in events)


# ---------------------------------------------------------------------------
# runtime 侧门控：什么样的任务可以接收补充指令
# ---------------------------------------------------------------------------


def _runtime_stub(**attrs):
    stub = SimpleNamespace(_lock=threading.RLock(), _pending_steer=[], _current=None)
    for key, value in attrs.items():
        setattr(stub, key, value)
    return stub


def test_take_pending_steer_drains_and_clears():
    stub = _runtime_stub(_pending_steer=["靠近它", "保持距离"])
    assert AgentRuntime._take_pending_steer(stub) == ["靠近它", "保持距离"]
    assert stub._pending_steer == []
    assert AgentRuntime._take_pending_steer(stub) == []


def test_steer_target_accepts_agent_loop_runs_in_any_live_phase():
    for phase in ("planning", "planned", "executing"):
        run = SimpleNamespace(run_id="r1", execute=True, status="running", phase=phase, route_strategy="agent_loop")
        assert AgentRuntime._steer_target_run_id(_runtime_stub(_current=run)) == "r1"

    paused = SimpleNamespace(run_id="r1p", execute=True, status="paused", phase="executing", route_strategy="agent_loop")
    assert AgentRuntime._steer_target_run_id(_runtime_stub(_current=paused)) == "r1p"


def test_steer_target_rejects_plan_execute_and_finished_runs():
    # plan-execute 是一次性顺序执行，没有下一轮去消费注入的指令：绝不能返回
    # 一个"看起来已并入、实际没人执行"的补充指令。
    plan_execute = SimpleNamespace(
        run_id="r_pe", execute=True, status="running", phase="executing", route_strategy="plan_execute"
    )
    assert AgentRuntime._steer_target_run_id(_runtime_stub(_current=plan_execute)) == ""

    for status, phase in (("completed", "completed"), ("cancelled", "cancelled"), ("running", "responding")):
        run = SimpleNamespace(run_id="r2", execute=True, status=status, phase=phase, route_strategy="agent_loop")
        assert AgentRuntime._steer_target_run_id(_runtime_stub(_current=run)) == ""

    plan_only = SimpleNamespace(run_id="r3", execute=False, status="running", phase="planning", route_strategy="agent_loop")
    assert AgentRuntime._steer_target_run_id(_runtime_stub(_current=plan_only)) == ""


def test_steer_target_has_no_target_during_the_llm_planning_window():
    """规划期 run 还没挂到 _current 上：不返回目标（那时无法判断会走哪条路径）。"""
    locked = SimpleNamespace(locked=lambda: True)
    stub = _runtime_stub(_current=None, _pending_run_ids={"run_a"}, _execution_slot=locked)
    assert AgentRuntime._steer_target_run_id(stub) == ""


def test_synthetic_tool_call_label_is_not_shown_as_reasoning():
    synthetic = LoopDecision(action="perception_status", params={}, reason="Call perception_status", is_complete=False)
    assert AgentRuntime._loop_decision_public_text(synthetic) == ""

    real = LoopDecision(action="drone_approach_target", params={}, reason="先靠近目标再确认特征", is_complete=False)
    assert AgentRuntime._loop_decision_public_text(real) == "先靠近目标再确认特征"

    mixed = LoopDecision(action="x", params={}, reason="先搜索\nCall airsim_detect_objects", is_complete=False)
    assert AgentRuntime._loop_decision_public_text(mixed) == "先搜索\nCall airsim_detect_objects"


def test_decision_text_drops_a_raw_decision_json_blob():
    blob = '{"action": "drone_hover", "params": {}, "reason": "无人机当前速度较快，正在快速下降"}'
    decision = LoopDecision(action="drone_hover", params={}, reason=blob, is_complete=False)
    assert AgentRuntime._loop_decision_public_text(decision) == ""


def test_decision_text_keeps_reasoning_before_a_json_draft():
    decision = LoopDecision(
        action="drone_get_status",
        params={},
        reason="先确认高度是否稳定\n{\"action\": \"drone_get_status\", \"params\": {}}",
        is_complete=False,
    )
    assert AgentRuntime._loop_decision_public_text(decision) == "先确认高度是否稳定"


def test_perception_stall_blocks_tasks_that_used_the_camera():
    from src.agent.agent_loop import AgentLoop

    loop = object.__new__(AgentLoop)
    observation = SimpleNamespace(world_state={"perception": {"enabled": True, "capture_age_s": 25.0}})
    state = SimpleNamespace(results=[SimpleNamespace(tool="airsim_detect_objects")])
    reason = loop._perception_stall(observation, state)
    assert "秒没有更新" in reason


def test_perception_stall_ignores_fresh_frames_and_non_visual_tasks():
    from src.agent.agent_loop import AgentLoop

    loop = object.__new__(AgentLoop)
    visual_state = SimpleNamespace(results=[SimpleNamespace(tool="airsim_detect_objects")])

    fresh = SimpleNamespace(world_state={"perception": {"enabled": True, "capture_age_s": 1.2}})
    assert loop._perception_stall(fresh, visual_state) == ""

    stalled = SimpleNamespace(world_state={"perception": {"enabled": True, "capture_age_s": 30.0}})
    flying_only = SimpleNamespace(results=[SimpleNamespace(tool="drone_fly_to")])
    assert loop._perception_stall(stalled, flying_only) == ""

    disabled = SimpleNamespace(world_state={"perception": {"enabled": False}})
    assert loop._perception_stall(disabled, visual_state) == ""
