"""LLM transport retry and context-overflow recovery tests.

The transport layer must retry transient failures (429 / 5xx / timeouts)
with backoff, never retry non-transient 4xx errors, and the planner must
recover from a provider context-length rejection by retrying with a tighter
budget.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from src.agent.llm import (
    LLMMissionPlanner,
    _request_with_retry,
    _retry_delay,
    is_context_overflow_error,
)


def _http_error(code: int, body: str = '{"error":"boom"}', retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {}
    if retry_after:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("http://test/chat/completions", code, "error", headers, io.BytesIO(body.encode()))


class _OkResponse:
    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_retry_delay_respects_retry_after() -> None:
    assert _retry_delay(1, "3") == 3.0
    assert _retry_delay(1, "not-a-number") > 0
    first = _retry_delay(1)
    second = _retry_delay(2)
    assert second >= first  # exponential backoff never shrinks
    assert _retry_delay(1, "9999") <= 30.0  # Retry-After is capped


def test_rate_limit_is_retried_then_succeeds(monkeypatch) -> None:
    calls = {"n": 0}
    monkeypatch.setattr("src.agent.llm.time.sleep", lambda s: None)

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _http_error(429, retry_after="1")
        return _OkResponse({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("src.agent.llm.urllib.request.urlopen", fake_urlopen)
    box: dict = {}
    body = _request_with_retry(urllib.request.Request("http://test"), 5.0, retry_box=box)
    assert body["choices"][0]["message"]["content"] == "ok"
    assert box["attempts"] == 2
    assert calls["n"] == 3


def test_server_error_retried_then_exhausted(monkeypatch) -> None:
    calls = {"n": 0}
    monkeypatch.setattr("src.agent.llm.time.sleep", lambda s: None)

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise _http_error(503)

    monkeypatch.setattr("src.agent.llm.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="LLM HTTP 503"):
        _request_with_retry(urllib.request.Request("http://test"), 5.0)
    assert calls["n"] == 3  # max attempts, then the error surfaces


def test_non_retryable_4xx_never_retried(monkeypatch) -> None:
    calls = {"n": 0}
    monkeypatch.setattr("src.agent.llm.time.sleep", lambda s: None)

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise _http_error(400, body='{"error":"maximum context length exceeded"}')

    monkeypatch.setattr("src.agent.llm.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="LLM HTTP 400"):
        _request_with_retry(urllib.request.Request("http://test"), 5.0)
    assert calls["n"] == 1  # a bad request is retried zero times


def test_transport_error_retried(monkeypatch) -> None:
    calls = {"n": 0}
    monkeypatch.setattr("src.agent.llm.time.sleep", lambda s: None)

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return _OkResponse({"choices": []})

    monkeypatch.setattr("src.agent.llm.urllib.request.urlopen", fake_urlopen)
    box: dict = {}
    _request_with_retry(urllib.request.Request("http://test"), 5.0, retry_box=box)
    assert box["attempts"] == 1


def test_context_overflow_markers() -> None:
    for message in (
        "maximum context length exceeded",
        "This model's maximum context length is 128000 tokens",
        "prompt is too long: 140000 tokens",
        "Request too large for model `x`: context_length_exceeded",
    ):
        assert is_context_overflow_error(RuntimeError(message)), message
    assert not is_context_overflow_error(RuntimeError("LLM HTTP 429: rate limited"))
    assert not is_context_overflow_error(RuntimeError("connection reset"))


def test_overflow_recovery_rebuilds_with_tighter_budget() -> None:
    planner = LLMMissionPlanner()
    scales: list[float] = []
    builds = {"n": 0}
    client = object()

    def build_messages(scale: float):
        builds["n"] += 1
        scales.append(scale)
        return [{"role": "user", "content": "x" * int(1000 * scale)}]

    def call(c, messages):
        assert c is client
        if len(messages[0]["content"]) > 500:
            raise RuntimeError("maximum context length exceeded")
        return {"ok": True}, {"prompt_tokens": 1}

    result, usage = planner._chat_with_overflow_recovery(client, build_messages, call)
    assert result == {"ok": True}
    assert scales == [1.0, 0.5]  # full budget first, halved budget on overflow
    assert "context overflow" in planner.last_error


def test_overflow_recovery_passes_through_other_errors() -> None:
    planner = LLMMissionPlanner()
    builds = {"n": 0}
    client = object()

    def build_messages(scale: float):
        builds["n"] += 1
        return [{"role": "user", "content": "x"}]

    def call(c, messages):
        raise RuntimeError("LLM HTTP 429: rate limited")

    with pytest.raises(RuntimeError, match="429"):
        planner._chat_with_overflow_recovery(client, build_messages, call)
    assert builds["n"] == 1  # non-overflow errors never rebuild the request


def test_overflow_recovery_accepts_bound_chat_json_with_retries() -> None:
    """Guard against the plan() integration signature bug: the bound method
    ``self._chat_json_with_retries`` (2 args: client, messages) must work
    when passed straight into the recovery helper — this exact wiring broke
    execute-mode planning at runtime on 2026-08-24."""
    planner = LLMMissionPlanner()

    class _FakeClient:
        def chat_json(self, messages):
            if len(messages[0]["content"]) > 500:
                raise RuntimeError("maximum context length exceeded")
            return {"ok": True}, {"prompt_tokens": 1}

    client = _FakeClient()

    def build_messages(scale: float):
        return [{"role": "user", "content": "x" * int(1000 * scale)}]

    # the exact wiring used by plan()/decide_next_step():
    result, usage = planner._chat_with_overflow_recovery(client, build_messages, planner._chat_json_with_retries)
    assert result == {"ok": True}
    assert "context overflow" in planner.last_error


# ---------------------------------------------------------------------------
# Streaming first-chunk retry
# ---------------------------------------------------------------------------


class _FakeStreamClient:
    def __init__(self, fail_first_call: bool = False, fail_after_first: bool = False, fail_always: bool = False) -> None:
        self.calls = 0
        self.fail_first_call = fail_first_call
        self.fail_after_first = fail_after_first
        self.fail_always = fail_always

    def stream_events(self, messages, max_tokens: int = 0):
        self.calls += 1
        if self.fail_always:
            raise RuntimeError("LLM HTTP 400: bad request")
        if self.fail_first_call and self.calls == 1:
            raise RuntimeError("LLM HTTP 503: service unavailable")
        yield {"type": "content", "token": "a"}
        if self.fail_after_first:
            raise RuntimeError("LLM HTTP 500: boom after first token")
        yield {"type": "content", "token": "b"}


def test_stream_retries_when_first_chunk_fails() -> None:
    from src.agent.llm import _stream_events_with_first_chunk_retry

    client = _FakeStreamClient(fail_first_call=True)
    tokens = [e["token"] for e in _stream_events_with_first_chunk_retry(client, [])]
    assert tokens == ["a", "b"]
    assert client.calls == 2  # retried once before any token was delivered


def test_stream_never_retries_after_first_token() -> None:
    from src.agent.llm import _stream_events_with_first_chunk_retry

    client = _FakeStreamClient(fail_after_first=True)
    with pytest.raises(RuntimeError, match="500"):
        list(_stream_events_with_first_chunk_retry(client, []))
    assert client.calls == 1  # a retry would duplicate the partial answer


def test_stream_never_retries_non_transient_4xx() -> None:
    from src.agent.llm import _stream_events_with_first_chunk_retry

    client = _FakeStreamClient(fail_always=True)
    with pytest.raises(RuntimeError, match="400"):
        list(_stream_events_with_first_chunk_retry(client, []))
    assert client.calls == 1


# ---------------------------------------------------------------------------
# Streamed planning with reasoning passthrough + bounded SSE serialization
# ---------------------------------------------------------------------------


def test_plan_streaming_delivers_reasoning_tokens() -> None:
    from src.agent.llm import LLMMissionPlanner, _stream_events_with_first_chunk_retry

    class _StreamClient:
        def stream_events(self, messages, max_tokens: int = 0):
            yield {"type": "reasoning", "token": "先分析目标"}
            yield {"type": "content", "token": '{"intent": "takeoff", "summary": "s", "steps": []}'}

    planner = LLMMissionPlanner()
    reasoning = []
    client = _StreamClient()
    parsed, _usage = planner._stream_plan(client, lambda scale: [{"role": "user", "content": "x"}], reasoning.append)
    assert parsed["intent"] == "takeoff"
    assert reasoning == ["先分析目标"]


def test_plan_streaming_parse_failure_raises_for_fallback() -> None:
    from src.agent.llm import LLMMissionPlanner

    class _BrokenStreamClient:
        def stream_events(self, messages, max_tokens: int = 0):
            yield {"type": "content", "token": "not json at all"}

    planner = LLMMissionPlanner()
    with pytest.raises(RuntimeError, match="JSON parse failed"):
        planner._stream_plan(_BrokenStreamClient(), lambda scale: [{"role": "user", "content": "x"}], lambda t: None)


def test_bounded_serialization_keeps_sse_alive_for_deep_payloads() -> None:
    """The SSE writer must never die on pathologically deep payloads —
    json.dumps would raise RecursionError and the frontend would freeze."""
    import json

    from src.agent.planner import _bounded_copy

    deep: dict = {}
    node = deep
    for _ in range(1500):
        node["data"] = {}
        node = node["data"]
    payload = {"run_update": {"steps": [{"result": deep}]}}
    dumped = json.dumps(_bounded_copy(payload), ensure_ascii=False, default=str)
    assert '"[bounded]": true' in dumped  # the deep chain was cut, serialization survived


def test_reasoning_effort_passed_through_to_request_payload() -> None:
    """DeepSeek V4 thinking controls must reach the request body only when
    the model config declares them."""
    import json
    import urllib.request

    from src.agent.llm import OpenAIClient

    client = OpenAIClient({
        "base_url": "http://fake/v1",
        "api_key": "k",
        "model": "deepseek-v4-flash",
        "thinking_mode": "enabled",
        "reasoning_effort": "max",
    })
    req = client._request([{"role": "user", "content": "hi"}], {})
    payload = json.loads(req.data.decode("utf-8"))
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "max"

    # default model: no thinking controls are injected
    plain = OpenAIClient({"base_url": "http://fake/v1", "api_key": "k", "model": "gpt-x"})
    req2 = plain._request([{"role": "user", "content": "hi"}], {})
    payload2 = json.loads(req2.data.decode("utf-8"))
    assert "thinking" not in payload2
    assert "reasoning_effort" not in payload2
