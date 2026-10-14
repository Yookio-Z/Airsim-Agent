"""占位思考收尾、以及任务收尾终态的分流。

实测回归：操作员说"我看到一个目标，希望你靠近确认特征"，飞机却反复被
"画面中当前没有锁定目标"拒绝抵近，一直原地回读状态，最后撞上步数上限 ——
而步数上限又触发了自动降落。这里锁住两处修复：

1. 模型某一轮只给了工具调用、没有思考文本时，"正在选择下一步"的占位必须被
   收尾，不能挂在时间线上冒充本轮思考；
2. 收尾终态按是否危险分流：failed 降落，cancelled/blocked 保持悬停。

（"用最近一次真实检测发起抵近"那组测试随 drone_approach_target 一起删除：
检测与抵近工具都已从产品中移除，靠近由模型按技能用 VLM 取点 + 深度 + 有界
drone_move_relative 完成。）
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

from src.agent.runtime import AgentRuntime


def test_pending_placeholder_becomes_a_decision_row_when_model_gives_no_text():
    """模型没给思考文本时：这一行改成"模型决策"，写出它实际调用了什么。

    操作员明确要求过两件事：不要编造思考文本；但**模型的输出必须能看到**——
    工具调用就是它的输出。以前这里会编一句"本轮模型未输出思考文本"再显示成
    "模型思考"，两头都错。
    """
    run = SimpleNamespace(
        process_trace=[
            {"kind": "reasoning", "status": "running", "body": "正在根据最新遥测、工具结果和任务目标选择下一步动作。"}
        ]
    )
    stub = SimpleNamespace(
        _lock=threading.RLock(),
        _PENDING_REASONING_PLACEHOLDERS=AgentRuntime._PENDING_REASONING_PLACEHOLDERS,
        _decision_line=AgentRuntime._decision_line,
    )
    AgentRuntime._close_pending_reasoning(stub, run, "airsim_get_depth_map", {"query_points": "0.6,0.39"})
    item = run.process_trace[0]
    assert item["kind"] == "decision" and item["title"] == "模型决策"
    assert item["body"].startswith("调用 airsim_get_depth_map(")


def test_close_pending_reasoning_keeps_real_text():
    run = SimpleNamespace(process_trace=[{"kind": "reasoning", "status": "running", "body": "本轮真实的模型思考"}])
    stub = SimpleNamespace(
        _lock=threading.RLock(),
        _PENDING_REASONING_PLACEHOLDERS=AgentRuntime._PENDING_REASONING_PLACEHOLDERS,
    )
    AgentRuntime._close_pending_reasoning(stub, run, "drone_get_status")
    assert run.process_trace[-1]["body"] == "本轮真实的模型思考"
    assert run.process_trace[-1]["status"] == "completed"


def _finalize_stub(calls: list[tuple[str, str]]):
    return SimpleNamespace(
        _stop_envelope_guard=lambda: None,
        _lock=threading.RLock(),
        _pending_steer=[],
        _cancel_requested=threading.Event(),
        _attempt_failure_hover=lambda run, reason: calls.append(("land", reason)),
        _attempt_hold_position=lambda run, reason: calls.append(("hover", reason)),
        task_runs=None,
    )


def test_blocked_run_holds_position_instead_of_landing():
    calls: list[tuple[str, str]] = []
    run = SimpleNamespace(execute=True, status="blocked", failure_reason="agent loop reached max_steps=16")
    AgentRuntime._finalize_task_run(_finalize_stub(calls), run)
    assert calls == [("hover", "agent loop reached max_steps=16")]


def test_cancelled_run_holds_position_instead_of_landing():
    calls: list[tuple[str, str]] = []
    run = SimpleNamespace(execute=True, status="cancelled", failure_reason="operator cancelled task")
    AgentRuntime._finalize_task_run(_finalize_stub(calls), run)
    assert calls == [("hover", "operator cancelled task")]


def test_failed_run_still_lands_as_the_safe_terminal_state():
    calls: list[tuple[str, str]] = []
    run = SimpleNamespace(execute=True, status="failed", failure_reason="position estimate diverged")
    AgentRuntime._finalize_task_run(_finalize_stub(calls), run)
    assert calls == [("land", "position estimate diverged")]


def test_mission_plan_declares_keep_reacting_for_continuous_tasks():
    """持续跟踪语义由计划声明，而不是靠指令关键词。"""
    from src.agent.planner import MissionPlan

    one_shot = MissionPlan(run_id="r1", command="靠近并确认特征", intent="inspect", summary="", steps=[])
    assert one_shot.keep_reacting is False
    assert one_shot.to_dict()["keep_reacting"] is False

    tracking = MissionPlan(run_id="r2", command="跟着那辆车", intent="track", summary="", steps=[], keep_reacting=True)
    assert tracking.to_dict()["keep_reacting"] is True



def test_settle_drops_pending_placeholder_at_task_end():
    """收尾时占位行同样摘掉（不是换成"本轮模型未输出思考文本"）。"""
    run = SimpleNamespace(
        status="completed",
        process_trace=[
            {"kind": "reasoning", "status": "running", "body": "正在根据最新遥测、工具结果和任务目标选择下一步动作。"}
        ],
    )
    stub = SimpleNamespace(_PENDING_REASONING_PLACEHOLDERS=AgentRuntime._PENDING_REASONING_PLACEHOLDERS)
    AgentRuntime._settle_process_trace(stub, run)
    assert run.process_trace == []


def test_settle_keeps_real_reasoning_text():
    run = SimpleNamespace(
        status="completed",
        process_trace=[{"kind": "reasoning", "status": "running", "body": "目标已居中，准备前进一步"}],
    )
    stub = SimpleNamespace(_PENDING_REASONING_PLACEHOLDERS=AgentRuntime._PENDING_REASONING_PLACEHOLDERS)
    AgentRuntime._settle_process_trace(stub, run)
    assert run.process_trace[-1]["body"] == "目标已居中，准备前进一步"
