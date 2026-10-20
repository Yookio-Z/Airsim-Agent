"""Chat 空回复的兜底行为（2026-10-20）。

事故现场：推理模型（minimax-m3.1-flash 经 OpenRouter）偶尔只产出 thinking、
不产出正文。chat 路径把空字符串当成正常回答写进消息，界面上就只剩一个
「思考与执行过程」块——操作员看到的是"Agent 不输出"，而且没有任何错误提示。

这里锁两条行为：
  1) 正文为空时自动重试一次，并要求直接回答；
  2) 重试后仍为空，给出明确的可见提示，绝不留空气泡。
"""

from __future__ import annotations

from typing import Any

import pytest

from _runtime_factories import agent_runtime


class ScriptedChatPlanner:
    """按脚本返回 chat 回答；记录每次调用的 command。"""

    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def chat_response_stream(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        answer = self._answers.pop(0) if self._answers else ""
        on_reasoning = kwargs.get("on_reasoning")
        on_token = kwargs.get("on_token")
        if not answer:
            if callable(on_reasoning):
                on_reasoning("We need just greet. No tool because ordinary greeting.")
            return ""
        if callable(on_token):
            on_token(answer)
        return answer


def _chat_runtime(planner: ScriptedChatPlanner) -> tuple[Any, list[tuple[str, str]], list[tuple]]:
    runtime = agent_runtime(planner=planner)
    updates: list[tuple[str, str]] = []
    events: list[tuple] = []
    runtime._refresh_chat_state = lambda state: state  # type: ignore[method-assign]
    runtime._chat_readonly_tools = lambda: []  # type: ignore[method-assign]
    runtime._recent_chat_context = lambda limit=None: []  # type: ignore[method-assign]
    runtime._update_assistant_message = (  # type: ignore[method-assign]
        lambda run_id, content, status, details=None, persist=True: updates.append((content, status))
    )
    runtime._append_assistant_delta = lambda *a, **k: None  # type: ignore[method-assign]
    runtime._append_event = (  # type: ignore[method-assign]
        lambda level, source, message, data=None: events.append((level, source, message, data))
    )
    return runtime, updates, events


def test_chat_empty_answer_retries_and_surfaces_the_reply() -> None:
    planner = ScriptedChatPlanner(["", "你好！有什么可以帮你的吗？"])
    runtime, updates, events = _chat_runtime(planner)

    runtime._handle_chat_command("你好啊", "", "chat_test_1", {}, [])

    assert len(planner.calls) == 2, "空正文必须重试一次"
    assert "上一轮只产生了思考" in planner.calls[1]["command"]
    assert updates[-1] == ("你好！有什么可以帮你的吗？", "complete")
    assert any("没有正文" in item[2] for item in events), events
    assert "chat_test_1" not in runtime._active_chat_requests


def test_chat_still_empty_after_retry_reports_visibly() -> None:
    planner = ScriptedChatPlanner(["", ""])
    runtime, updates, events = _chat_runtime(planner)

    runtime._handle_chat_command("你好啊", "", "chat_test_2", {}, [])

    assert len(planner.calls) == 2
    content, status = updates[-1]
    assert status == "complete"
    assert content and "模型没有生成回答" in content, "不能把空字符串作为回答写进消息"
    assert "chat_test_2" not in runtime._active_chat_requests


def test_chat_with_content_does_not_retry() -> None:
    planner = ScriptedChatPlanner(["你好！"])
    runtime, updates, events = _chat_runtime(planner)

    runtime._handle_chat_command("你好啊", "", "chat_test_3", {}, [])

    assert len(planner.calls) == 1
    assert updates[-1] == ("你好！", "complete")
    assert not any("没有正文" in item[2] for item in events)
