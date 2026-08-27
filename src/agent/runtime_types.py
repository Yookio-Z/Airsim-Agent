"""运行时数据模型（拆分自 runtime.py）。"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeEvent:
    timestamp: float
    level: str
    source: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "source": self.source,
            "message": self.message,
            "data": self.data,
        }


@dataclass
class ChatMessage:
    id: str
    role: str
    content: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    run_id: str = ""
    status: str = "complete"
    details: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "attachments": list(self.attachments),
            "run_id": self.run_id,
            "status": self.status,
            "details": self.details,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class RunState:
    run_id: str
    command: str
    intent: str
    summary: str
    status: str = "created"
    mode: str = "execute"
    phase: str = "created"
    execute: bool = False
    progress: float = 0.0
    current_step: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    failure_reason: str = ""
    assistant_message: str = ""
    model_id: str = ""
    plan: MissionPlan | None = None
    task_level: str = ""
    route_strategy: str = ""
    route_reason: str = ""
    risk_level: str = "safe"  # safe / elevated / high
    answer_with_llm: bool = True
    loop_state: dict[str, Any] = field(default_factory=dict)
    start_position_recorded: bool = False
    start_telemetry: dict[str, Any] = field(default_factory=dict)
    final_telemetry: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    agent_state: dict[str, Any] = field(default_factory=dict)
    thought_trace: list[dict[str, Any]] = field(default_factory=list)
    process_trace: list[dict[str, Any]] = field(default_factory=list)
    # ReAct correction rounds already spent after a failed Plan-Execute run.
    correction_attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "command": self.command,
            "intent": self.intent,
            "summary": self.summary,
            "status": self.status,
            "mode": self.mode,
            "phase": self.phase,
            "execute": self.execute,
            "progress": round(self.progress, 1),
            "current_step": self.current_step,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "failure_reason": self.failure_reason,
            "assistant_message": self.assistant_message,
            "start_telemetry": self.start_telemetry,
            "final_telemetry": self.final_telemetry,
            "verification": self.verification,
            "agent_state": self.agent_state,
            "thought_trace": list(self.thought_trace),
            "process_trace": list(self.process_trace),
            "correction_attempts": self.correction_attempts,
            "plan": self.plan.to_dict() if self.plan else None,
            "task_level": self.task_level,
            "route_strategy": self.route_strategy,
            "route_reason": self.route_reason,
            "risk_level": self.risk_level,
            "loop_state": self.loop_state,
        }


@dataclass
class ToolApprovalRequest:
    """P5: lightweight approval gate for high-risk direct tool calls.

    Created when a direct route has ``risk_level == 'high'`` AND the active
    backend declares ``requires_operator_approval == True`` (real vehicle).
    The worker thread blocks on ``event`` until the operator approves/rejects
    via :meth:`AgentRuntime.approve_run` / :meth:`AgentRuntime.reject_run`.
    """

    run_id: str
    command: str
    tool: str
    params: dict[str, Any]
    risk_level: str
    reason: str
    created_at: float = field(default_factory=time.time)
    timeout_seconds: float = 300.0
    # Decision: None=pending, True=approved, False=rejected
    approved: bool | None = None
    event: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "command": self.command,
            "tool": self.tool,
            "params": dict(self.params),
            "risk_level": self.risk_level,
            "reason": self.reason,
            "created_at": self.created_at,
            "timeout_seconds": self.timeout_seconds,
            "approved": self.approved,
            "status": "pending" if self.approved is None else ("approved" if self.approved else "rejected"),
        }


