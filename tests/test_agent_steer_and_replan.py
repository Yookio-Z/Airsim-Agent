"""执行中补充指令（steer）与计划步骤偏离重试。

实测回归：一条"靠近那辆车并确认特征"的任务，确认步骤因为"那一刻视觉模型没能
确认目标"被判为偏离计划后整步被永久跳过，循环原地回读直到被打断；操作员随后
发的纠正指令又因为旧任务未及时停止而被直接拒绝。这里锁住两条修复：

1. 计划步骤首次偏离只是转一轮 ReAct 纠错，游标保留，下一轮回到该步骤重试，
   连续偏离到上限才跳过；
2. 执行中提交的新指令作为补充指令并入当前循环，而不是取消任务重来。

注意偏离的判据已经**不含检测锁定**（YOLO 已从产品中移除）：现在只有"视觉确认
未通过"才会让计划步骤偏离。
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


class _FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.drone = {"flying": True, "position_ned": {"x": 0.0, "y": 0.0, "z": -3.0}}

    def status_snapshot(self) -> dict:
        return {"backend": "fake", "connected": True, "drone": dict(self.drone)}

    def list_tools(self) -> list[dict]:
        return [
            {"name": name}
            for name in ("inspect_current_frame", "airsim_get_depth_map", "drone_move_relative", "drone_get_status")
        ]

    def execute(self, name, params, dry_run=False, blocked_by_supervisor=False):
        self.calls.append((name, dict(params or {})))
        started = time.time()
        return ToolCallResult(name, dict(params or {}), True, {"status": "ok", "tool": name}, started, time.time())


def _visual_result(name: str, params: dict, found: bool) -> ToolCallResult:
    """inspect_current_frame 的结果：found 决定视觉确认是否通过。

    确认时给一个"已经够近"的尺寸（h=0.30 ≥ 技能的 0.25 阈值），这样"目标还很远
    就不判收敛"那道门不会介入——本文件的用例测的是偏离/重试机制本身。
    """
    answer = {
        "target_found": found,
        "confidence": 0.9 if found else 0.2,
        "target_candidates": (
            [{"label": "car", "confidence": 0.9, "bbox_hint": {"x": 0.5, "y": 0.55, "w": 0.35, "h": 0.30}}]
            if found
            else []
        ),
    }
    now = time.time()
    return ToolCallResult(
        name,
        dict(params or {}),
        True,
        {
            "status": "ok",
            "tool": name,
            "question": str((params or {}).get("question") or ""),
            "answer": answer,
        },
        now,
        now,
    )


class _VisualTools(_FakeTools):
    """视觉确认结果可控：第一次看得见、之后看不见（或反之）。"""

    def __init__(self, seen: list[bool]) -> None:
        super().__init__()
        self._seen = list(seen)

    def execute(self, name, params, dry_run=False, blocked_by_supervisor=False):
        result = super().execute(name, params, dry_run, blocked_by_supervisor)
        if name == "inspect_current_frame":
            found = self._seen.pop(0) if self._seen else False
            result = _visual_result(name, params or {}, found)
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
    """确认步骤第一次因"视觉没确认到目标"偏离时不能被永久跳过。

    偏离判据取"上一条同工具结果未通过确认"：第一次偏离只交回模型纠错一轮，
    游标保留，下一轮回到该步骤重新判断——所以后续步骤照常执行。
    """
    events: list[str] = []
    tools = _VisualTools(seen=[False, True])  # 计划首步失败；模型重看时成功
    planner = _RecordingPlanner(
        [
            # 第一次偏离后的纠错：再看一眼（成功）
            LoopDecision("inspect_current_frame", {}, "换个角度再确认一次"),
            # 视觉观察之后的运动参数必须由模型给出（不能执行计划里猜的参数）
            LoopDecision("drone_move_relative", {"forward_m": 1.0}, "深度换算后走一步"),
        ]
    )
    plan = _plan([
        MissionStep(id="s1", label="确认目标", tool="inspect_current_frame"),
        MissionStep(id="s2", label="再次确认", tool="inspect_current_frame"),
        MissionStep(id="s3", label="靠近一点", tool="drone_move_relative", params={"forward_m": 1.0}),
    ])
    state = _run_loop(
        tmp_path, tools, planner, plan,
        on_event=lambda level, source, message, data: events.append(str(message)),
    )

    joined = " ".join(events)
    executed = [name for name, _ in tools.calls]
    assert "与计划不符" in joined, events
    assert "跳过这一步" not in joined, events  # 没有触发跳过
    assert "drone_move_relative" in executed, executed  # 偏离纠正后计划继续推进
    assert "交给模型重新决定" in joined, events  # 观察→运动的交接可见
    assert state.status == "completed"


def test_repeated_deviation_eventually_skips_the_step(tmp_path):
    """同一步骤持续偏离（视觉始终没确认）时跳过该步，避免死循环。"""
    events: list[str] = []
    tools = _VisualTools(seen=[])  # 之后始终看不见
    planner = _RecordingPlanner(
        [
            # 第一轮纠错：读深度（不是计划里的步骤，因此不会让计划"收敛"）
            LoopDecision(action="airsim_get_depth_map", params={}, reason="先看看还有多远"),
            LoopDecision(action="drone_move_relative", params={"forward_m": 1.0}, reason="再靠近"),
            LoopDecision(action="", reason="无法确认目标", is_complete=True),
        ]
    )
    plan = _plan([
        MissionStep(id="s1", label="确认目标", tool="inspect_current_frame"),
        MissionStep(id="s2", label="再次确认", tool="inspect_current_frame"),
        MissionStep(id="s3", label="靠近一点", tool="drone_move_relative", params={"forward_m": 1.0}),
    ])
    state = _run_loop(
        tmp_path, tools, planner, plan,
        on_event=lambda level, source, message, data: events.append(str(message)),
    )

    joined = " ".join(events)
    executed = [name for name, _ in tools.calls]
    assert "跳过这一步" in joined, events
    assert executed.count("inspect_current_frame") == 1, executed  # 只跑过第一步；第二步被跳过
    assert "airsim_get_depth_map" in executed, executed  # 第一次偏离交回模型纠错
    assert "drone_move_relative" in executed, executed  # 跳过之后计划继续推进
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

    real = LoopDecision(action="drone_move_relative", params={"forward_m": 1.0}, reason="先靠近目标再确认特征", is_complete=False)
    assert AgentRuntime._loop_decision_public_text(real) == "先靠近目标再确认特征"

    mixed = LoopDecision(action="x", params={}, reason="先搜索\nCall inspect_current_frame", is_complete=False)
    assert AgentRuntime._loop_decision_public_text(mixed) == "先搜索\nCall inspect_current_frame"


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
    state = SimpleNamespace(results=[SimpleNamespace(tool="inspect_current_frame")])
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
