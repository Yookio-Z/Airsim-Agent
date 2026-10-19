"""循环与观察者的边界、以及"完成"的诚实性。

为什么单独有一组：这两条是 2026-10-12 那次任务失败的根因，而且都是**只在运行时
才暴露**的那一类问题。

- 回显/UI 镜像跑在 Agent Loop 的调用栈上。没有隔离时，界面里一个格式化辅助函数
  抛异常就能把整个 execute 任务干掉（实测：飞机停在半路，界面只显示一行 Python
  TypeError）。隔离的目的是让"界面出错"永远不升级成"任务失败"。
- 完成判定必须是机器事实。模型自报 is_complete 只是"请求收尾"；判据无法评估时
  不能默认当作通过，报告也不允许用模型的语气代替运行时的终态。
"""

from __future__ import annotations

from typing import Any

from src.agent.agent_loop import AgentLoop
from src.agent.loop_types import LoopActionResult, LoopDecision, LoopObservation, LoopState
from src.agent.tool_executor import ToolCallResult

import time


def _result(tool: str, ok: bool, data: dict[str, Any]) -> ToolCallResult:
    now = time.time()
    return ToolCallResult(tool, {}, ok, data, now, now, terminal=True, task_id="")


class FakeMemory:
    def snapshot(self) -> dict[str, Any]:
        return {}

    def remember_tool_call(self, tool: str, ok: bool) -> None:
        return None


class FakeSkills:
    def available_cards(self, capabilities: dict[str, Any], memory: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return []


class SequencePlanner:
    def __init__(self, decisions: list[LoopDecision]) -> None:
        self.decisions = list(decisions)
        self.calls = 0

    def decide_next_step(self, **_: Any) -> LoopDecision:
        index = min(self.calls, len(self.decisions) - 1)
        self.calls += 1
        return self.decisions[index]


class FakeTools:
    """status_snapshot 不给 flying —— 让 landed 类判据落入"无法评估"分支。"""

    def __init__(self, handlers: dict[str, Any], drone: dict[str, Any] | None = None) -> None:
        self.handlers = handlers
        self.calls: list[str] = []
        self._drone = drone if drone is not None else {"flying": True}

    def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": name} for name in self.handlers]

    def status_snapshot(self) -> dict[str, Any]:
        return {"connected": True, "stale_connection": False, "drone": dict(self._drone)}

    def execute(self, name: str, params: dict[str, Any], **_: Any) -> ToolCallResult:
        self.calls.append(name)
        handler = self.handlers[name]
        return handler(params) if callable(handler) else handler


def _run(
    tools: FakeTools,
    planner: SequencePlanner,
    *,
    on_event=None,
    on_state=None,
    max_steps: int = 3,
) -> AgentLoop:
    loop = AgentLoop(
        tools,
        planner,
        FakeMemory(),
        skills=FakeSkills(),
        on_event=on_event,
        on_state=on_state,
    )
    loop.run("run_x", "拍照", {"image_capture": True}, [{"name": n} for n in tools.handlers], max_steps=max_steps)
    return loop


# ----------------------------------------------------------------------
# 1. 观察者隔离
# ----------------------------------------------------------------------

def test_state_observer_exception_does_not_kill_the_run():
    """state 回显抛异常：任务必须跑完，异常只被记录。"""
    seen_states = []

    def bad_state(state):
        seen_states.append(state.status)
        raise RuntimeError("界面回执组装失败")

    tools = FakeTools({"airsim_take_photo": _result("airsim_take_photo", True, {"status": "ok"})})
    planner = SequencePlanner([LoopDecision(action="airsim_take_photo", params={}, is_complete=False)])
    loop = _run(tools, planner, on_state=bad_state, max_steps=1)

    # 关键：动作照常执行，任务不是因为观察者崩掉而失败
    assert tools.calls == ["airsim_take_photo"]
    assert seen_states, "观察者仍应被调用"
    failures = loop.observer_failures
    assert len(failures) == len(seen_states)
    assert failures[0]["observer"] == "state"
    assert "界面回执组装失败" in failures[0]["error"]


def test_event_observer_exception_does_not_kill_the_run():
    def bad_event(*args, **kwargs):
        raise RuntimeError("事件通道坏了")

    tools = FakeTools({"airsim_take_photo": _result("airsim_take_photo", True, {"status": "ok"})})
    planner = SequencePlanner([LoopDecision(action="airsim_take_photo", params={}, is_complete=False)])
    loop = _run(tools, planner, on_event=bad_event, max_steps=1)

    assert tools.calls == ["airsim_take_photo"]
    assert any(item["observer"] == "event" for item in loop.observer_failures)


def test_state_failure_is_announced_through_the_event_channel():
    announced: list[dict[str, Any]] = []

    def on_event(level, source, message, data):
        announced.append({"level": level, "message": message, "data": dict(data)})

    def bad_state(state):
        raise RuntimeError("回执挂了")

    tools = FakeTools({"airsim_take_photo": _result("airsim_take_photo", True, {"status": "ok"})})
    planner = SequencePlanner([LoopDecision(action="airsim_take_photo", params={}, is_complete=False)])
    loop = _run(tools, planner, on_event=on_event, on_state=bad_state, max_steps=2)

    # state 坏了要走另一条通道公告出来；否则这次界面故障在 UI 上完全不可见
    handler_errors = [item for item in announced if item["data"].get("kind") == "handler_error"]
    assert handler_errors, announced
    assert "回执挂了" in handler_errors[0]["message"]
    # 公告只发生一次（观察者每轮都坏，不能每轮都公告一遍）
    assert len(handler_errors) == 1
    assert len(loop.observer_failures) >= 2


def test_both_observers_broken_still_finishes():
    """两个通道全坏也不能死循环、不能中断任务。"""

    def bad_state(state):
        raise RuntimeError("state 坏")

    def bad_event(*args, **kwargs):
        raise RuntimeError("event 坏")

    tools = FakeTools({"airsim_take_photo": _result("airsim_take_photo", True, {"status": "ok"})})
    planner = SequencePlanner([LoopDecision(action="airsim_take_photo", params={}, is_complete=False)])
    loop = _run(tools, planner, on_event=bad_event, on_state=bad_state, max_steps=1)

    assert tools.calls == ["airsim_take_photo"]
    # 记录有上限，坏观察者不会把内存吃满
    assert len(loop.observer_failures) <= 20


# ----------------------------------------------------------------------
# 2. 完成判据的诚实性
# ----------------------------------------------------------------------

def test_unverifiable_criterion_marks_the_run_unverified():
    """判据缺遥测（flying 读不到）时：运行照常收尾，但状态必须是 unverified。"""
    from src.agent.agent_loop import AgentLoop as _Loop
    from src.agent.loop_types import LoopObservation, LoopState
    from src.agent.planner import MissionPlan, MissionStep

    tools = FakeTools({"drone_hover": _result("drone_hover", True, {"status": "ok"})}, drone={})
    planner = SequencePlanner([
        LoopDecision(action="drone_hover", params={}, is_complete=False),
        LoopDecision(action="", params={}, is_complete=True, reason="落地确认"),
    ])
    loop = AgentLoop(tools, planner, FakeMemory(), skills=FakeSkills())

    plan = MissionPlan(
        run_id="run_unverified",
        command="确认是否已经落地",
        intent="check",
        summary="确认降落",
        steps=[MissionStep(id="s00", label="hover", tool="drone_hover", params={})],
        goal={"success_criteria": [{"metric": "landed"}]},
    )
    state = loop.run(
        "run_unverified",
        "确认是否已经落地",
        {"flight_control": True},
        [{"name": "drone_hover"}],
        initial_plan=plan,
        max_steps=3,
    )

    assert state.status == "completed"
    assert state.verification_status == "unverified", state.verification_status
    assert state.verification_unevaluated
    assert "无法评估" in state.summary


def test_verified_run_has_no_caveat_noise():
    tools = FakeTools({"drone_hover": _result("drone_hover", True, {"status": "ok"})}, drone={"flying": False})
    planner = SequencePlanner([
        LoopDecision(action="drone_hover", params={}, is_complete=False),
        LoopDecision(action="", params={}, is_complete=True, reason="悬停完成"),
    ])
    loop = AgentLoop(tools, planner, FakeMemory(), skills=FakeSkills())
    state = loop.run("run_ok", "悬停", {"flight_control": True}, [{"name": "drone_hover"}], max_steps=3)

    assert state.status == "completed"
    assert state.verification_status == "ok"
    assert "（" not in state.summary


def test_report_leads_with_the_machine_verdict_when_blocked():
    """模型写的正文不能决定报告的口吻：终态先行。"""
    tools = FakeTools({"drone_hover": _result("drone_hover", True, {"status": "ok"})})
    planner = SequencePlanner([LoopDecision(action="drone_hover", params={}, is_complete=False)])
    loop = AgentLoop(tools, planner, FakeMemory(), skills=FakeSkills())

    class ModelSummaryPlanner(SequencePlanner):
        def summarize_attempt(self, command, state, model_id=None):
            return "我已经把飞机稳稳地停在目标附近了，一切正常，可以放心。"

    planner = ModelSummaryPlanner([LoopDecision(action="drone_hover", params={}, is_complete=False)])
    loop = AgentLoop(tools, planner, FakeMemory(), skills=FakeSkills())
    state = loop.run(
        "run_blocked",
        "飞过去靠近目标",
        {"flight_control": True},
        [{"name": "drone_hover"}],
        max_steps=1,
        require_llm=True,
    )

    assert state.status == "blocked"
    assert state.summary.startswith("任务未完成"), state.summary
    assert "一切正常" in state.summary  # 模型正文保留，但不再是唯一口径


def test_outcome_headline_for_each_terminal_state():
    from src.agent.loop_types import LoopState

    loop = AgentLoop(FakeTools({}), SequencePlanner([]), FakeMemory(), skills=FakeSkills())
    assert loop._outcome_headline(LoopState(run_id="r", command="c", status="failed", failure_reason="x")).startswith("任务失败")
    assert loop._outcome_headline(LoopState(run_id="r", command="c", status="blocked")).startswith("任务未完成")
    assert loop._outcome_headline(LoopState(run_id="r", command="c", status="cancelled")).startswith("任务已被取消")
    assert loop._outcome_headline(LoopState(run_id="r", command="c", status="completed")) == "任务完成。"


def test_is_complete_without_evidence_is_not_silently_accepted():
    """模型声明完成、但一个动作都没做过：不能当作已验证完成。

    循环层 status 仍是 completed（= 模型请求收尾），但机器判据必须否决它，
    并由运行层的 failed-verification gate 落地。
    """
    tools = FakeTools({"drone_hover": _result("drone_hover", True, {"status": "ok"})})
    planner = SequencePlanner([LoopDecision(action="", params={}, is_complete=True, reason="done")])
    loop = AgentLoop(tools, planner, FakeMemory(), skills=FakeSkills())
    state = loop.run(
        "run_empty_claim",
        "飞过去",
        {"flight_control": True},
        [{"name": "drone_hover"}],
        max_steps=2,
    )

    assert state.results == []
    assert state.verification_status == "failed", state.verification_status


# ----------------------------------------------------------------------
# 3. 守卫不得覆盖模型的有效选择（2026-10-12 实机事故的根因）
# ----------------------------------------------------------------------

# 操作员的真实指令：整条链路由它触发（visual + visual_approach + target_confirmation）
_APPROACH_CMD = "起飞无人机飞向图像中你看到的浅蓝色汽车靠近后降落"


def _state_after_confirmation() -> LoopState:
    """构造"已经拍过照、已经用视觉模型确认过目标、也读过深度"的状态。"""
    state = LoopState(run_id="r", command=_APPROACH_CMD, status="running")
    # 注意用 LoopActionResult（循环自己的类型，带 timestamp）——不是 ToolCallResult
    state.results.append(LoopActionResult(
        0, "airsim_take_photo", {}, True,
        {"status": "ok", "image_base64": "AAAA", "image_saved_to": "x.jpg"},
    ))
    state.results.append(LoopActionResult(
        1, "inspect_current_frame", {}, True,
        {"status": "ok", "answer": {"summary_zh": "画面里有一辆浅蓝色汽车", "target_candidates": [{"label": "car", "confidence": 0.8}]}},
    ))
    state.results.append(LoopActionResult(
        2, "locate_image_point", {}, True, {"status": "ok", "distance_median_m": 30.0},
    ))
    return state


def _guard(tools: FakeTools, decision: LoopDecision) -> LoopDecision:
    loop = AgentLoop(tools, SequencePlanner([]), FakeMemory(), skills=FakeSkills())
    obs = LoopObservation(step_index=1, world_state=tools.status_snapshot(), last_action_result=None,
                          elapsed_since_start=1.0, frame_age_s=None)
    return loop._guard_decision(
        _APPROACH_CMD,
        _state_after_confirmation(),
        obs,
        decision,
        set(tools.handlers) | {"drone_move_relative", "drone_approach_target", "locate_image_point", "inspect_current_frame"},
        {"image_capture": True},
    )


def test_guard_does_not_override_a_valid_model_action():
    """模型选了合法的有界移动时，守卫必须原样放行。

    以前这里无条件把决策改写成 drone_approach_target，于是技能里教的
    "VLM 给点 + 深度算一步 + drone_move_relative" 永远执行不到——操作员看到的
    就是"工具一直在撞同一个错"，而那其实不是模型的选择。
    """
    tools = FakeTools({"drone_move_relative": _result("drone_move_relative", True, {"status": "ok"})})
    tools.perception_axis = None  # 检测没启动也照样放行模型自己的动作
    decision = LoopDecision(action="drone_move_relative", params={"forward_m": 2.0, "right_m": -0.5}, is_complete=False)

    out = _guard(tools, decision)

    assert out.action == "drone_move_relative", out.action
    assert out.params == {"forward_m": 2.0, "right_m": -0.5}
    assert out.source == "llm"  # 没被守卫接管


def test_guard_does_not_push_the_detection_only_tool_when_detection_is_off():
    """模型想过早宣布完成时守卫要兜底，但不能兜一个必然失败的工具。

    检测没在跑时 drone_approach_target 一定被拒（实测连撞三次后模型放弃并降落）；
    这时应把决策交回模型，由 skill:visual-approach 的 VLM+深度路径接手。
    """
    tools = FakeTools({"drone_hover": _result("drone_hover", True, {"status": "ok"})})
    tools.perception_axis = None  # 检测未启动
    decision = LoopDecision(action="", params={}, is_complete=True, reason="done")

    out = _guard(tools, decision)

    assert out.action != "drone_approach_target", "检测锁不住时不该注入只认锁定的工具"


def test_guard_decisions_are_tagged_as_guard():
    """守卫注入的决定必须标 source=guard，UI 才能和"模型思考"分开显示。

    同时锁住"交回模型"这个出口：守卫没有可替代动作时，返回空动作 +
    needs_replan=True（控制权交回模型），而不是把整次任务判失败。
    """
    tools = FakeTools({"drone_hover": _result("drone_hover", True, {"status": "ok"})})
    tools.perception_axis = None  # 检测已移除：这不再是任何门控
    state = _state_after_confirmation()
    loop = AgentLoop(tools, SequencePlanner([]), FakeMemory(), skills=FakeSkills())
    obs = LoopObservation(step_index=1, world_state=tools.status_snapshot(), last_action_result=None,
                          elapsed_since_start=1.0, frame_age_s=None)
    # 触发守卫的"交回模型"出口：这是视觉请求，但当前没有可用的看图工具。
    # （记忆工具已删除——记忆不再是模型动作，守卫里那条"内部工具"分支随之消失。）
    decision = LoopDecision(action="", params={}, reason="模型这一轮没有给出动作")

    out = loop._guard_decision(
        "看一下画面里有什么", state, obs, decision, set(), {}
    )

    assert out.source == "guard", out.source
    assert out.action == "", out.action
    assert out.needs_replan is True, "守卫无法替代时应交回模型，而不是判失败"


def test_guard_never_injects_an_approach_tool():
    """守卫不再持有任何"靠近"策略：检测开着也不会替模型挑抵近工具。

    2026-10-12 的现场就是被这条改写出来的——技能教的是 VLM 取点 + 深度 +
    有界 drone_move_relative，守卫却把它换成只认检测锁定的抵近工具。
    """
    tools = FakeTools({"drone_move_relative": _result("drone_move_relative", True, {"status": "ok"})})
    tools.perception_axis = None
    decision = LoopDecision(action="drone_move_relative", params={"forward_m": 2.0}, is_complete=False)

    out = _guard(tools, decision)

    assert out.action == "drone_move_relative"
    assert out.source == "llm"  # 模型自己的选择，守卫原样放行
    assert "Approved visual approach" not in str(out.reason)
