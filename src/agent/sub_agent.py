"""Bounded sub-agent execution for open-ended interpretation subtasks.

A sub-agent is a second AgentLoop instance running synchronously inside the
parent loop's thread: it shares the same ToolRuntime, safety gates, approval
flow, and execution slot (caller_owns_run holds because the thread id matches).
It gets its own run log (``<parent>.sub<N>``), its own step budget, and a
focused system prompt. The parent only sees the structured report returned by
``run`` — sub steps never leak into the parent's loop state.

Design constraints (from the design review):
  * sub-agents must never appear in a sub-agent's own tool cards (depth <= 1);
  * deterministic skills (skill:*) stay at the parent level — a sub-agent is
    for open interpretation, not for re-orchestrating flight skills;
  * LLM unavailability returns a failed report instead of raising into the
    parent task.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from .agent_loop import AgentLoop, LoopStateCallback
from .run_log import RunLog

_SUB_AGENT_DEPTH = threading.local()

SUB_AGENT_SYSTEM_PROMPT = (
    "You are a focused sub-agent inside an ongoing UAV mission agent. "
    "Your single goal is: {goal}. "
    "Constraints: {constraints} "
    "Rules: you may only use read-only tools and multimodal analysis (status, sensors, depth, photo, VLM); "
    "flight-control tools and skills are not available to you; do not redo work the parent already completed; "
    "when the goal is answered or provably unanswerable, mark is_complete=true "
    "and put a concise Chinese report in reason with findings, evidence, and remaining uncertainty. "
    "Return JSON only."
)


def sub_agent_tool_cards(tool_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sub-agents never see agent_subtask (recursion limit), skill:* cards
    (deterministic skills stay at the parent level), or flight-control tools
    (sub-agents are for open-ended interpretation, not vehicle control)."""
    from .tool_executor import ToolRuntime

    control_tools = ToolRuntime.CONTROL_TOOLS
    return [
        card
        for card in tool_cards
        if isinstance(card, dict)
        and str(card.get("name") or "") != "agent_subtask"
        and str(card.get("name") or "") not in control_tools
        and not str(card.get("name") or "").startswith("skill:")
    ]


class SubAgentRunner:
    """Run one bounded sub-agent loop and return a structured report."""

    def __init__(
        self,
        tools: Any,
        planner: Any,
        memory: Any,
        execute_tool: Callable[[str, dict[str, Any], bool], Any],
        should_stop: Callable[[], bool] | None = None,
        should_pause: Callable[[], bool] | None = None,
        on_ui_event: Callable[[str, str, str, dict[str, Any]], None] | None = None,
        on_ui_state: LoopStateCallback | None = None,
        sub_counter: list[int] | None = None,
        log_base_dir=None,
    ) -> None:
        self.tools = tools
        self.planner = planner
        self.memory = memory
        self.execute_tool = execute_tool
        self.should_stop = should_stop
        self.should_pause = should_pause
        self.on_ui_event = on_ui_event
        self.on_ui_state = on_ui_state
        self._sub_counter = sub_counter if sub_counter is not None else [0]
        self.log_base_dir = log_base_dir

    def run(
        self,
        parent_run_id: str,
        goal: str,
        constraints: str = "",
        tool_cards: list[dict[str, Any]] | None = None,
        capabilities: dict[str, Any] | None = None,
        model_id: str | None = None,
        max_steps: int = 6,
    ) -> dict[str, Any]:
        depth = int(getattr(_SUB_AGENT_DEPTH, "depth", 0))
        if depth >= 1:
            return {
                "status": "blocked",
                "summary": "sub-agent nesting is limited to one level",
                "steps": [],
                "findings": [],
            }
        self._sub_counter[0] += 1
        sub_id = f"{parent_run_id}.sub{self._sub_counter[0]}"
        _SUB_AGENT_DEPTH.depth = depth + 1
        try:
            return self._run(sub_id, parent_run_id, goal, constraints, tool_cards or [], capabilities or {}, model_id, max_steps)
        finally:
            _SUB_AGENT_DEPTH.depth = depth

    def _run(
        self,
        sub_id: str,
        parent_run_id: str,
        goal: str,
        constraints: str,
        tool_cards: list[dict[str, Any]],
        capabilities: dict[str, Any],
        model_id: str | None,
        max_steps: int,
    ) -> dict[str, Any]:
        sub_log = RunLog(sub_id, base_dir=self.log_base_dir)
        sub_log.write("run.start", {"command": goal, "mode": "sub_agent", "model_id": model_id or "", "parent": parent_run_id})

        def on_event(level: str, source: str, message: str, data: dict[str, Any]) -> None:
            sub_log.write("sub.event", {"level": level, "source": source, "message": message, "data": data})
            if self.on_ui_event:
                try:
                    self.on_ui_event(level, source, message, data)
                except Exception:
                    pass

        def on_state(loop_state: Any) -> None:
            if self.on_ui_state:
                try:
                    loop_state.run_id = parent_run_id  # echo sub progress into the parent UI
                    self.on_ui_state(loop_state)
                except Exception:
                    pass

        loop = AgentLoop(
            tools=self.tools,
            planner=self.planner,
            memory=self.memory,
            on_event=on_event,
            should_stop=self.should_stop,
            should_pause=self.should_pause,
            skills=None,
            execute_tool=self.execute_tool,
            on_state=on_state,
        )
        sub_cards = sub_agent_tool_cards(tool_cards)
        try:
            state = loop.run(
                run_id=sub_id,
                command=goal,
                capabilities=capabilities,
                tool_cards=sub_cards,
                max_steps=max_steps,
                execute=True,
                model_id=model_id or None,
                require_llm=True,
                system_prompt=SUB_AGENT_SYSTEM_PROMPT.format(goal=goal, constraints=constraints or "none"),
                fallback_enabled=False,
            )
        except Exception as exc:  # LLM unavailable or sub-loop crash: report, don't raise
            sub_log.write("run.end", {"status": "failed", "summary": str(exc)})
            return {
                "status": "failed",
                "summary": f"子任务执行失败：{str(exc)[:300]}",
                "steps": [],
                "findings": [],
                "error": str(exc)[:300],
            }

        report_status = "completed" if state.status == "completed" else ("blocked" if state.status == "blocked" else "failed")
        steps = [
            {
                "tool": result.tool,
                "ok": result.ok,
                "duration_ms": round(result.duration_ms, 1),
                "message": str((result.data or {}).get("message") or "")[:200],
            }
            for result in state.results[:12]
        ]
        report = {
            "status": report_status,
            "summary": state.summary or state.failure_reason or "",
            "verification_status": state.verification_status,
            "steps": steps,
            "findings": self._findings(state),
        }
        sub_log.write("run.end", {"status": report_status, "summary": report["summary"], "steps": len(steps)})
        return report

    @staticmethod
    def _findings(state: Any) -> list[str]:
        findings: list[str] = []
        for result in reversed(state.results):
            data = result.data or {}
            if not isinstance(data, dict):
                continue
            for key in ("summary_zh", "message", "summary"):
                text = str(data.get(key) or "").strip()
                if text and text not in findings:
                    findings.append(text[:240])
                    break
            if len(findings) >= 5:
                break
        return findings
