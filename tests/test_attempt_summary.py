"""Tests for the model-generated attempt summary (smolagents
provide_final_answer pattern): a step-budget-exhausted loop asks the model to
review the attempt instead of returning a template line."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from src.agent.agent_loop import AgentLoop
from src.agent.llm import LLMMissionPlanner
from src.agent.loop_types import LoopDecision
from src.agent.memory import AgentMemory
from src.agent.tool_executor import ToolCallResult


# ── summarize_attempt gating ──


def test_summarize_attempt_returns_empty_when_llm_disabled(monkeypatch) -> None:
    planner = LLMMissionPlanner()
    monkeypatch.setattr(planner, "_resolve_config", lambda model_id=None: None)
    monkeypatch.setattr(planner, "_enabled", lambda config: False)
    assert planner.summarize_attempt("起飞", {"results": []}) == ""


def test_summarize_attempt_uses_model_text_output(monkeypatch) -> None:
    planner = LLMMissionPlanner()
    monkeypatch.setattr(planner, "_resolve_config", lambda model_id=None: {"provider": "fake"})
    monkeypatch.setattr(planner, "_enabled", lambda config: True)

    captured: dict[str, Any] = {}

    def fake_client(config):
        class FakeClient:
            def chat_text(self, messages, max_tokens=0):
                captured["messages"] = messages
                captured["max_tokens"] = max_tokens
                return "已完成起飞和前进，但目标搜索未完成；当前悬停中，建议继续扫描。", {"usage": {}}

        return FakeClient()

    monkeypatch.setattr("src.agent.llm._create_client", fake_client)
    loop_state = {
        "results": [
            {"tool": "drone_takeoff", "ok": True, "data": {}},
            {"tool": "drone_move_relative", "ok": True, "data": {}},
            {"tool": "skill:search", "ok": False, "data": {"message": "target not found"}},
        ],
        "failure_reason": "agent loop reached max_steps=16",
        "observations": [
            {"world_state": {"drone": {"position_ned": {"x": 1.0, "y": 2.0, "z": -3.0}, "flying": True}}}
        ],
    }
    summary = planner.summarize_attempt("搜索红色车辆", loop_state)
    assert summary == "已完成起飞和前进，但目标搜索未完成；当前悬停中，建议继续扫描。"
    user = captured["messages"][-1]["content"]
    assert "搜索红色车辆" in user
    assert "drone_takeoff: 成功" in user
    assert "skill:search: 失败" in user
    assert "agent loop reached max_steps=16" in user
    assert "position_ned" in user
    assert captured["max_tokens"] == 600


def test_summarize_attempt_returns_empty_on_model_error(monkeypatch) -> None:
    planner = LLMMissionPlanner()
    monkeypatch.setattr(planner, "_resolve_config", lambda model_id=None: {"provider": "fake"})
    monkeypatch.setattr(planner, "_enabled", lambda config: True)

    def exploding_client(config):
        class ExplodingClient:
            def chat_text(self, messages, max_tokens=0):
                raise RuntimeError("api down")

        return ExplodingClient()

    monkeypatch.setattr("src.agent.llm._create_client", exploding_client)
    assert planner.summarize_attempt("起飞", {"results": []}) == ""


# ── agent loop integration: max_steps branch uses the model summary ──


class _ForeverActionPlanner:
    """Fake planner that always asks for the same tool, so the loop exhausts
    its step budget instead of completing."""

    def __init__(self, summary: str) -> None:
        self.summary = summary
        self.summarize_called = False

    def decide_next_step(self, **kwargs) -> LoopDecision:
        return LoopDecision(action="drone_get_status", params={}, reason="keep checking")

    def summarize_attempt(self, command: str, loop_state: dict[str, Any], model_id: str | None = None) -> str:
        self.summarize_called = True
        return self.summary


class _OkTools:
    def status_snapshot(self) -> dict[str, Any]:
        return {"drone": {"position_ned": {"x": 0.0, "y": 0.0, "z": -3.0}, "flying": True}, "backend": "airsim"}

    def execute(self, name: str, params: dict[str, Any], dry_run: bool = False, **_: Any) -> ToolCallResult:
        return _ok_result(name, params, dry_run)


def _ok_result(tool: str, params: dict[str, Any], dry_run: bool) -> ToolCallResult:
    now = time.time()
    return ToolCallResult(tool, params, True, {"status": "ok"}, now, now)


def test_max_steps_exhausted_uses_model_summary() -> None:
    planner = _ForeverActionPlanner("模型总结：已完成状态回读，但任务未达成，建议检查目标区域。")
    loop = AgentLoop(
        tools=_OkTools(),
        planner=planner,  # type: ignore[arg-type]
        memory=AgentMemory(),
    )
    state = loop.run(
        run_id="run_max",
        command="起飞并悬停",
        capabilities={"flight_control": True},
        tool_cards=[{"name": "drone_get_status"}],
        max_steps=4,
        execute=True,
        require_llm=True,
    )
    assert state.status == "blocked"
    assert "max_steps" in state.failure_reason
    assert planner.summarize_called is True
    assert state.summary == "模型总结：已完成状态回读，但任务未达成，建议检查目标区域。"


def test_max_steps_without_require_llm_keeps_template_summary() -> None:
    planner = _ForeverActionPlanner("不应该被调用")
    loop = AgentLoop(
        tools=_OkTools(),
        planner=planner,  # type: ignore[arg-type]
        memory=AgentMemory(),
    )
    state = loop.run(
        run_id="run_max_plan",
        command="起飞并悬停",
        capabilities={"flight_control": True},
        tool_cards=[{"name": "drone_get_status"}],
        max_steps=3,
        execute=False,
        require_llm=False,
    )
    assert state.status == "blocked"
    assert planner.summarize_called is False
    assert "上限" in state.summary or "max_steps" in state.summary


def test_llm_attempt_summary_falls_back_on_missing_method() -> None:
    planner = SimpleNamespace()  # no summarize_attempt
    loop = AgentLoop(tools=_OkTools(), planner=planner, memory=AgentMemory())  # type: ignore[arg-type]
    assert loop._llm_attempt_summary(SimpleNamespace(command="x", to_dict=lambda: {}), None) == ""
