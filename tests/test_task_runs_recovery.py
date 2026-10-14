"""记录层的崩溃恢复：残留的"运行中"记录必须被标成 interrupted。

进程崩溃/被关掉后，最后一条 run 记录会永远停在 running：界面一直转圈，操作员
分不清"还在跑"和"早就没了"。这里只改状态与说明，**绝不重放任何动作**——飞行指令
是非幂等的，重放一次起飞/降落是事故（这一点刻意与"会话可恢复"的编码 agent 不同，
见 docs/loop_context_design.md §2）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agent.task_runs import TaskRunStore


def _run(run_id: str, status: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "command": "命令",
        "intent": "intent",
        "summary": "summary",
        "status": status,
        "phase": status,
        "mode": "execute",
        "execute": True,
        "created_at": 1_700_000_000.0,
        "started_at": 1_700_000_000.0,
        "updated_at": 1_700_000_000.0,
        "finished_at": 0.0,
        "failure_reason": "",
        "current_step": "",
        "progress": 0.0,
    }


def test_leftover_running_runs_are_marked_interrupted(tmp_path: Path):
    store = TaskRunStore(data_dir=tmp_path)
    store.start_run(_run("execute_live", "running"))
    store.start_run(_run("execute_done", "running"))
    store.finalize_run(_run("execute_done", "completed"))

    # grace_sec=0：模拟"记录确实已经不再更新"的残留（真实重启时启用默认宽限期，
    # 由 UI 轮询路径的复扫在宽限期过后补上）。
    changed = store.mark_running_as_interrupted(grace_sec=0.0)

    assert changed == 1
    live = store.get_run("execute_live")
    assert live["status"] == "interrupted"
    assert live["phase"] == "interrupted"
    assert "未执行任何自动恢复" in live["failure_reason"]
    # 已收尾的记录不许被动
    assert store.get_run("execute_done")["status"] == "completed"
    # 幂等：再扫一遍什么都不改
    assert store.mark_running_as_interrupted(grace_sec=0.0) == 0


def test_live_run_ids_are_never_touched(tmp_path: Path):
    store = TaskRunStore(data_dir=tmp_path)
    store.start_run(_run("execute_active", "running"))

    assert store.mark_running_as_interrupted(keep_run_ids={"execute_active"}) == 0
    assert store.get_run("execute_active")["status"] == "running"


def test_recently_updated_running_runs_are_protected_by_the_grace_window(tmp_path: Path):
    """更新时间很新的"运行中"记录不能动：那可能是另一个进程里真在飞的任务。

    本地跑一次测试、或开第二个 server 实例，都会构造 runtime 并触发这次清扫；
    没有宽限期就会把别人正在执行的任务标成"已中断"。
    """
    import time

    store = TaskRunStore(data_dir=tmp_path)
    fresh = _run("execute_maybe_flying", "running")
    fresh["updated_at"] = time.time()
    store.start_run(fresh)

    assert store.mark_running_as_interrupted() == 0
    assert store.get_run("execute_maybe_flying")["status"] == "running"
    # 宽限期设为 0（测试/强制清理）时才处理
    assert store.mark_running_as_interrupted(grace_sec=0.0) == 1
    assert store.get_run("execute_maybe_flying")["status"] == "interrupted"
