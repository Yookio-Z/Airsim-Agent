"""抵近目标的可执行性、占位思考收尾、以及任务收尾终态的分流。

实测回归：操作员说"我看到一个目标，希望你靠近确认特征"，飞机却反复被
"画面中当前没有锁定目标"拒绝抵近，一直原地回读状态，最后撞上步数上限 ——
而步数上限又触发了自动降落。这里锁住三处修复：

1. 显示层新鲜度窗口之外、但仍在回退窗口内的最近真实检测，可以用来发起一次
   有界抵近（预测框不算）；
2. 模型某一轮只给了工具调用、没有思考文本时，"正在选择下一步"的占位必须被
   收尾，不能挂在时间线上冒充本轮思考；
3. 收尾终态按是否危险分流：failed 降落，cancelled/blocked 保持悬停。
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

from src.agent.runtime import AgentRuntime
from src.agent.tool_executor import ToolRuntime


def test_fallback_approach_target_uses_recent_measured_detection():
    snap = {
        "targets": [
            {"bbox": [10, 10, 50, 50], "age_s": 9.9, "predicted": False, "track_id": 1},
            {"bbox": [100, 100, 180, 200], "age_s": 2.4, "predicted": False, "track_id": 2},
            {"bbox": [200, 200, 260, 260], "age_s": 1.0, "predicted": True, "track_id": 3},
        ]
    }
    target = ToolRuntime._fallback_approach_target(snap)
    assert target is not None and target["track_id"] == 2


def test_fallback_approach_target_rejects_predicted_and_stale():
    snap = {
        "targets": [
            {"bbox": [1, 1, 9, 9], "age_s": 5.0, "predicted": False, "track_id": 1},
            {"bbox": [1, 1, 99, 99], "age_s": 0.2, "predicted": True, "track_id": 2},
        ]
    }
    assert ToolRuntime._fallback_approach_target(snap) is None
    assert ToolRuntime._fallback_approach_target({"targets": []}) is None


def test_fallback_approach_target_prefers_newest_then_largest():
    snap = {
        "targets": [
            {"bbox": [0, 0, 100, 100], "age_s": 1.0, "predicted": False, "track_id": 1},
            {"bbox": [0, 0, 10, 10], "age_s": 0.5, "predicted": False, "track_id": 2},
        ]
    }
    target = ToolRuntime._fallback_approach_target(snap)
    assert target is not None and target["track_id"] == 2


def test_pending_placeholder_is_replaced_when_model_gives_no_text():
    run = SimpleNamespace(
        process_trace=[
            {"kind": "reasoning", "status": "running", "body": "正在根据最新遥测、工具结果和任务目标选择下一步动作。"}
        ]
    )
    stub = SimpleNamespace(
        _lock=threading.RLock(),
        _PENDING_REASONING_PLACEHOLDERS=AgentRuntime._PENDING_REASONING_PLACEHOLDERS,
    )
    AgentRuntime._close_pending_reasoning(stub, run, "airsim_detect_objects")
    item = run.process_trace[-1]
    assert item["status"] == "completed"
    assert "airsim_detect_objects" in item["body"]


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



def test_settle_replaces_pending_placeholder_at_task_end():
    """循环在"结果回来、模型刚准备想下一步"那一拍收敛时，占位行必须被替换。"""
    run = SimpleNamespace(
        status="completed",
        process_trace=[
            {"kind": "reasoning", "status": "running", "body": "正在根据最新遥测、工具结果和任务目标选择下一步动作。"}
        ],
    )
    stub = SimpleNamespace(_PENDING_REASONING_PLACEHOLDERS=AgentRuntime._PENDING_REASONING_PLACEHOLDERS)
    AgentRuntime._settle_process_trace(stub, run)
    item = run.process_trace[-1]
    assert item["status"] == "completed"
    assert item["body"] == "本轮模型未输出思考文本。"


def test_settle_keeps_real_reasoning_text():
    run = SimpleNamespace(
        status="completed",
        process_trace=[{"kind": "reasoning", "status": "running", "body": "目标已居中，准备前进一步"}],
    )
    stub = SimpleNamespace(_PENDING_REASONING_PLACEHOLDERS=AgentRuntime._PENDING_REASONING_PLACEHOLDERS)
    AgentRuntime._settle_process_trace(stub, run)
    assert run.process_trace[-1]["body"] == "目标已居中，准备前进一步"
