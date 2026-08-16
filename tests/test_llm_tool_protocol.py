"""Tests for the native tool-calling protocol layer and batch action execution.

Covers: native capability inference, OpenAI/Anthropic chat_tools wire format
(with mocked HTTP), decision parsing from tool calls, schema synthesis, and
the AgentLoop batch execution path with a fake tool runtime.
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request

import pytest

from src.agent.agent_loop import AgentLoop
from src.agent.llm import (
    LLMMissionPlanner,
    ToolCallUnsupportedError,
    _create_client,
    infer_model_capabilities,
)
from src.agent.loop_types import LoopDecision
from src.agent.tool_executor import ToolCallResult


# ---------------------------------------------------------------------------
# native capability inference
# ---------------------------------------------------------------------------


def test_native_tools_inference():
    assert infer_model_capabilities("deepseek-v4", "deepseek")["native_tools"] is True
    assert infer_model_capabilities("gpt-4o", "openai")["native_tools"] is True
    assert infer_model_capabilities("qwen2.5", "dashscope")["native_tools"] is True
    assert infer_model_capabilities("custom-model", "https://my-private-host.local")["native_tools"] is False
    assert infer_model_capabilities("custom-model", "https://my-private-host.local", "tools")["native_tools"] is True
    assert infer_model_capabilities("deepseek-v4", "deepseek", "text")["native_tools"] is False


# ---------------------------------------------------------------------------
# mocked HTTP helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._body


def _fake_openai_tool_response(monkeypatch, tool_calls, text="ok", usage=None):
    message: dict = {"role": "assistant", "content": text}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    payload = {"choices": [{"message": message}], "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}}

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        assert "tools" in body and body["tool_choice"] == "auto"
        assert "tool_calls" not in body  # request side never sends tool_calls
        return _FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_openai_chat_tools_parses_tool_calls(monkeypatch):
    _fake_openai_tool_response(
        monkeypatch,
        tool_calls=[
            {"id": "call_1", "type": "function", "function": {"name": "drone_takeoff", "arguments": '{"altitude": 3.0}'}},
            {"id": "call_2", "type": "function", "function": {"name": "drone_get_status", "arguments": "{}"}},
        ],
    )
    client = _create_client({"api_type": "openai", "model": "deepseek-v4", "base_url": "https://api.deepseek.com", "api_key": "k"})
    calls, text, usage = client.chat_tools([{"role": "user", "content": "起飞"}], [{"type": "function", "function": {}}])
    assert len(calls) == 2
    assert calls[0]["name"] == "drone_takeoff"
    assert calls[0]["arguments"] == {"altitude": 3.0}
    assert calls[1]["name"] == "drone_get_status"
    assert text == "ok"
    assert usage["prompt_tokens"] == 10


def test_openai_chat_tools_no_calls_means_text(monkeypatch):
    _fake_openai_tool_response(monkeypatch, tool_calls=None, text="任务已完成")
    client = _create_client({"api_type": "openai", "model": "m", "base_url": "https://api.example.com", "api_key": "k"})
    calls, text, _ = client.chat_tools([{"role": "user", "content": "hi"}], [])
    assert calls == []
    assert text == "任务已完成"


def _raise_http_error(code: int, detail: dict):
    body = json.dumps(detail).encode("utf-8")

    class _ErrorResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return body

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, code, "error", {}, _ErrorResponse())

    return fake_urlopen


def test_openai_chat_tools_unsupported_raises_typed_error(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _raise_http_error(400, {"error": {"param": "tools", "message": "unknown parameter: tools"}}),
    )
    client = _create_client({"api_type": "openai", "model": "m", "base_url": "https://api.example.com", "api_key": "k"})
    with pytest.raises(ToolCallUnsupportedError):
        client.chat_tools([{"role": "user", "content": "x"}], [{"type": "function", "function": {}}])


def test_openai_chat_tools_schema_bug_is_not_unsupported(monkeypatch):
    # A schema validation error from the provider must NOT degrade silently.
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _raise_http_error(400, {"error": {"message": "Invalid schema for function 'drone_takeoff': altitude must be number"}}),
    )
    client = _create_client({"api_type": "openai", "model": "m", "base_url": "https://api.example.com", "api_key": "k"})
    with pytest.raises(RuntimeError) as excinfo:
        client.chat_tools([{"role": "user", "content": "x"}], [{"type": "function", "function": {}}])
    assert not isinstance(excinfo.value, ToolCallUnsupportedError)


def test_anthropic_chat_tools_parses_tool_use(monkeypatch):
    payload = {
        "content": [
            {"type": "text", "text": "先起飞"},
            {"type": "tool_use", "id": "tu_1", "name": "drone_takeoff", "input": {"altitude": 5.0}},
        ],
        "usage": {"input_tokens": 20, "output_tokens": 8},
    }

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        assert "tools" in body and body["tool_choice"] == {"type": "auto"}
        return _FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = _create_client({"api_type": "anthropic", "model": "claude-sonnet-4", "base_url": "https://api.example.com", "api_key": "k"})
    calls, text, usage = client.chat_tools(
        [{"role": "user", "content": "起飞"}],
        [{"name": "drone_takeoff", "description": "d", "input_schema": {"type": "object"}}],
    )
    assert len(calls) == 1
    assert calls[0]["name"] == "drone_takeoff"
    assert calls[0]["arguments"] == {"altitude": 5.0}
    assert text == "先起飞"
    assert usage["prompt_tokens"] == 20


# ---------------------------------------------------------------------------
# decision parsing (planner-level, no network)
# ---------------------------------------------------------------------------


def _planner() -> LLMMissionPlanner:
    return object.__new__(LLMMissionPlanner)


def test_decision_from_tool_calls_single_and_batch():
    planner = _planner()
    decision = planner._decision_from_tool_calls(
        [
            {"name": "drone_get_status", "arguments": {}, "id": "1"},
            {"name": "airsim_take_photo", "arguments": {"image_type": "scene"}, "id": "2"},
        ],
        "开始",
        {"drone_get_status", "airsim_take_photo"},
    )
    assert decision.action == "drone_get_status"
    assert decision.is_complete is False
    assert len(decision.parallel_actions) == 1
    assert decision.parallel_actions[0]["action"] == "airsim_take_photo"
    assert decision.parallel_actions[0]["params"] == {"image_type": "scene"}


def test_decision_from_tool_calls_completion_on_text_only():
    planner = _planner()
    decision = planner._decision_from_tool_calls([], "任务已完成，目标确认", {"drone_get_status"})
    assert decision.is_complete is True
    assert decision.reason == "任务已完成，目标确认"


def test_decision_from_tool_calls_rejects_unavailable_tool():
    planner = _planner()
    decision = planner._decision_from_tool_calls(
        [{"name": "drone_land", "arguments": {}, "id": "1"}],
        "",
        {"drone_get_status"},
    )
    assert decision.action == ""
    assert decision.is_complete is False
    assert "unavailable" in decision.reflection


def test_decision_from_tool_calls_skips_hallucinated_names():
    """One hallucinated tool name must not kill the turn: valid calls still run."""
    planner = _planner()
    decision = planner._decision_from_tool_calls(
        [
            {"name": "drone_fly_to_the_moon", "arguments": {}, "id": "1"},
            {"name": "drone_get_status", "arguments": {}, "id": "2"},
            {"name": "airsim_take_photo", "arguments": {}, "id": "3"},
        ],
        "",
        {"drone_get_status", "airsim_take_photo"},
    )
    assert decision.action == "drone_get_status"
    assert len(decision.parallel_actions) == 1
    assert decision.parallel_actions[0]["action"] == "airsim_take_photo"
    assert "drone_fly_to_the_moon" in decision.reflection


def test_decision_from_payload_actions_array():
    planner = _planner()
    decision = planner._decision_from_payload(
        {
            "action": "drone_takeoff",
            "params": {"altitude": 3.0},
            "reason": "起飞",
            "actions": [{"action": "drone_get_status", "params": {}, "reason": "读状态"}],
        },
        {"drone_takeoff", "drone_get_status"},
    )
    assert decision.action == "drone_takeoff"
    assert len(decision.parallel_actions) == 1
    assert decision.parallel_actions[0]["action"] == "drone_get_status"


def test_decision_from_payload_batch_only():
    planner = _planner()
    decision = planner._decision_from_payload(
        {"actions": [{"action": "drone_get_status"}, {"action": "airsim_take_photo"}]},
        {"drone_get_status", "airsim_take_photo"},
    )
    assert decision.action == "drone_get_status"
    assert len(decision.parallel_actions) == 1
    assert decision.parallel_actions[0]["action"] == "airsim_take_photo"


def test_loop_tool_schemas_include_flight_constraints():
    planner = _planner()
    schemas = planner._loop_tool_schemas(
        [{"name": "drone_takeoff", "purpose": "Take off", "inputs": {"altitude": "meters"}}],
        [{"name": "drone_takeoff", "description": "Take off", "parameters": {"altitude": {"default": 3.0, "annotation": "float"}}}],
    )
    assert len(schemas) == 1
    fn = schemas[0]["function"]
    assert fn["name"] == "drone_takeoff"
    assert fn["parameters"]["properties"]["altitude"]["minimum"] == 0.5
    assert fn["parameters"]["properties"]["altitude"]["maximum"] == 120


def test_loop_tool_schemas_skip_memory_store():
    planner = _planner()
    schemas = planner._loop_tool_schemas(
        [{"name": "memory_store", "purpose": "write"}, {"name": "drone_get_status", "purpose": "status"}],
        None,
    )
    names = [s["function"]["name"] for s in schemas]
    assert "memory_store" not in names
    assert "drone_get_status" in names


# ---------------------------------------------------------------------------
# AgentLoop batch execution
# ---------------------------------------------------------------------------


class _FakeTools:
    READ_ONLY_TOOLS = {"drone_get_status", "airsim_take_photo"}

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def status_snapshot(self) -> dict:
        return {}

    def list_tools(self) -> list[dict]:
        return []

    def execute(self, name, params, dry_run=False, blocked_by_supervisor=False):
        self.calls.append((name, dict(params or {})))
        started = time.time()
        return ToolCallResult(name, dict(params or {}), True, {"status": "ok", "tool": name}, started, time.time())


class _FakePlanner:
    def __init__(self, decisions: list[LoopDecision]) -> None:
        self.decisions = list(decisions)
        self.calls = 0

    def decide_next_step(self, **kwargs):
        self.calls += 1
        if self.decisions:
            return self.decisions.pop(0)
        return LoopDecision(action="", reason="done", is_complete=True)

    def summarize_attempt(self, *args, **kwargs):
        return ""


def test_loop_executes_batch_actions(tmp_path):
    from src.agent.memory import AgentMemory
    from src.agent.skill_registry import SkillRegistry

    tools = _FakeTools()
    decision = LoopDecision(
        action="drone_get_status",
        params={},
        reason="read status",
        parallel_actions=[
            {"action": "airsim_take_photo", "params": {"image_type": "scene"}, "reason": "capture"},
            {"action": "drone_land", "params": {}, "reason": "should be rejected (flight tool)"},
        ],
    )
    loop = AgentLoop(
        tools=tools,  # type: ignore[arg-type]
        planner=_FakePlanner([decision, LoopDecision(action="", reason="done", is_complete=True)]),  # type: ignore[arg-type]
        memory=AgentMemory(data_dir=tmp_path),
        skills=SkillRegistry(overrides_path=tmp_path / "skills.json"),
    )
    state = loop.run(
        run_id="run_batch",
        command="起飞并查看状态",
        capabilities={},
        tool_cards=[
            {"name": "drone_get_status", "purpose": "status"},
            {"name": "airsim_take_photo", "purpose": "photo"},
            {"name": "drone_land", "purpose": "land"},
        ],
        max_steps=3,
    )
    executed = [name for name, _ in tools.calls]
    assert "drone_get_status" in executed
    assert "airsim_take_photo" in executed
    # flight-control tool was rejected from the batch
    assert "drone_land" not in executed
    assert state.status in {"completed", "running"}
    assert len(state.results) == 2
    assert all(result.ok for result in state.results)


def test_loop_batch_failure_counts(tmp_path):
    from src.agent.memory import AgentMemory
    from src.agent.skill_registry import SkillRegistry

    class _FailingTools(_FakeTools):
        def execute(self, name, params, dry_run=False, blocked_by_supervisor=False):
            self.calls.append((name, dict(params or {})))
            started = time.time()
            ok = name != "airsim_take_photo"
            data = {"status": "ok" if ok else "error", "message": "" if ok else "photo failed"}
            return ToolCallResult(name, dict(params or {}), ok, data, started, time.time())

    tools = _FailingTools()
    decision = LoopDecision(
        action="drone_get_status",
        params={},
        reason="status",
        parallel_actions=[{"action": "airsim_take_photo", "params": {}, "reason": "capture"}],
    )
    loop = AgentLoop(
        tools=tools,  # type: ignore[arg-type]
        planner=_FakePlanner([decision, LoopDecision(action="", reason="done", is_complete=True)]),  # type: ignore[arg-type]
        memory=AgentMemory(data_dir=tmp_path),
        skills=SkillRegistry(overrides_path=tmp_path / "skills.json"),
    )
    state = loop.run(
        run_id="run_batch_fail",
        command="起飞并查看状态",
        capabilities={},
        tool_cards=[{"name": "drone_get_status", "purpose": "status"}, {"name": "airsim_take_photo", "purpose": "photo"}],
        max_steps=2,
    )
    photo_result = [r for r in state.results if r.tool == "airsim_take_photo"]
    assert photo_result and photo_result[0].ok is False
    # the batch failure stays unresolved, so a later "complete" decision fails the run
    assert state.status == "failed"
