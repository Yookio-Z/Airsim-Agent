"""Tests for the bounded sub-agent mechanism (sub_agent.py)."""

from __future__ import annotations

import threading
import time

import pytest

import src.agent.sub_agent as sub_agent_module
from src.agent.llm import LLMUnavailableError
from src.agent.loop_types import LoopDecision
from src.agent.sub_agent import SubAgentRunner, sub_agent_tool_cards
from src.agent.tool_executor import ToolCallResult


class _FakeTools:
    READ_ONLY_TOOLS = {"drone_get_status"}

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def status_snapshot(self) -> dict:
        return {"backend": "fake", "connected": True, "drone": {"flying": True}}

    def list_tools(self) -> list[dict]:
        return []

    def execute(self, name, params, dry_run=False, blocked_by_supervisor=False):
        self.calls.append((name, dict(params or {})))
        started = time.time()
        return ToolCallResult(name, dict(params or {}), True, {"status": "ok", "tool": name}, started, time.time())


class _FakePlanner:
    def __init__(self, decisions=None, error=None) -> None:
        self.decisions = list(decisions or [])
        self.error = error
        self.last_system_prompt = ""

    def decide_next_step(self, **kwargs):
        self.last_system_prompt = kwargs.get("system_prompt") or ""
        if self.error is not None:
            raise self.error
        if self.decisions:
            return self.decisions.pop(0)
        return LoopDecision(action="", reason="done", is_complete=True)

    def summarize_attempt(self, *args, **kwargs):
        return ""


def _runner(tmp_path, tools=None, planner=None, execute_tool=None):
    from src.agent.memory import AgentMemory

    tools = tools or _FakeTools()
    planner = planner or _FakePlanner()
    events: list[tuple[str, str, str, dict]] = []
    states: list = []

    def execute(name, params, dry_run):
        if execute_tool:
            return execute_tool(name, params, dry_run)
        return tools.execute(name, params, dry_run=dry_run)

    runner = SubAgentRunner(
        tools=tools,
        planner=planner,
        memory=AgentMemory(data_dir=tmp_path),
        execute_tool=execute,
        on_ui_event=lambda level, source, message, data: events.append((level, source, message, data)),
        on_ui_state=lambda loop: states.append(loop.run_id),
        log_base_dir=tmp_path,
    )
    return runner, tools, planner, events, states


def test_sub_agent_completes_with_report(tmp_path):
    runner, tools, planner, events, states = _runner(
        tmp_path,
        planner=_FakePlanner(
            [
                LoopDecision(action="drone_get_status", params={}, reason="检查状态"),
                LoopDecision(action="", reason="子任务完成：画面中确认红色汽车", is_complete=True),
            ]
        ),
    )
    report = runner.run(
        "run_parent",
        "确认画面中的目标类型",
        tool_cards=[{"name": "drone_get_status", "purpose": "s"}],
        capabilities={},
        max_steps=4,
    )
    assert report["status"] == "completed"
    assert "红色汽车" in report["summary"]
    assert tools.calls == [("drone_get_status", {})]
    # sub run log exists with start/end events
    from src.agent.run_log import RunLogReader

    reader = RunLogReader("run_parent.sub1", base_dir=tmp_path)
    assert reader.exists() is True
    events_log = reader.events()
    assert any(event["type"] == "run.start" for event in events_log)
    # UI echo forwarded the sub decisions
    assert any("drone_get_status" in message for _, _, message, _ in events)
    assert states and states[0] == "run_parent"


def test_sub_agent_cards_exclude_subtask_and_skills():
    cards = [
        {"name": "drone_get_status", "purpose": "s"},
        {"name": "agent_subtask", "purpose": "recursion"},
        {"name": "skill:search", "purpose": "parent-level"},
        {"name": "skill:visual_observe", "purpose": "parent-level"},
    ]
    filtered = sub_agent_tool_cards(cards)
    names = [card["name"] for card in filtered]
    assert names == ["drone_get_status"]


def test_sub_agent_nesting_is_blocked(tmp_path):
    runner, _, _, _, _ = _runner(tmp_path)
    previous = getattr(sub_agent_module._SUB_AGENT_DEPTH, "depth", 0)
    sub_agent_module._SUB_AGENT_DEPTH.depth = 1
    try:
        report = runner.run("run_parent", "任意目标")
    finally:
        sub_agent_module._SUB_AGENT_DEPTH.depth = previous
    assert report["status"] == "blocked"
    assert "one level" in report["summary"]


def test_sub_agent_llm_unavailable_returns_failed_report(tmp_path):
    runner, tools, planner, _, _ = _runner(tmp_path, planner=_FakePlanner(error=LLMUnavailableError("no api key")))
    report = runner.run("run_parent", "分析目标", tool_cards=[{"name": "drone_get_status", "purpose": "s"}], max_steps=2)
    assert report["status"] == "failed"
    assert "no api key" in report["summary"]
    assert report["steps"] == []


def test_sub_agent_uses_focused_system_prompt(tmp_path):
    planner = _FakePlanner()
    runner, _, _, _, _ = _runner(tmp_path, planner=planner)
    runner.run("run_parent", "确认目标颜色", constraints="不要移动", tool_cards=[], max_steps=2)
    assert "确认目标颜色" in planner.last_system_prompt
    assert "不要移动" in planner.last_system_prompt
    assert "read-only" in planner.last_system_prompt
    assert "not available to you" in planner.last_system_prompt


def test_runtime_sub_agent_tool_validation(tmp_path):
    from src.agent.runtime import AgentRuntime

    rt = object.__new__(AgentRuntime)
    rt._lock = threading.RLock()
    bad = rt._execute_sub_agent_tool({})
    assert bad.ok is False
    assert bad.error_code == "INVALID_PARAMS"
    dry = rt._execute_sub_agent_tool({"goal": "看看画面"}, dry_run=True)
    assert dry.ok is True
    assert dry.data["status"] == "planned"
