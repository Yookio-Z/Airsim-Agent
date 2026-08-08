"""Data contracts for the lightweight ReAct-style agent loop."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoopObservation:
    """One observation frame: current world state plus the previous action result."""

    step_index: int
    world_state: dict[str, Any]
    last_action_result: dict[str, Any] | None = None
    elapsed_since_start: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "world_state": self.world_state,
            "last_action_result": self.last_action_result,
            "elapsed_since_start": round(self.elapsed_since_start, 3),
            "timestamp": self.timestamp,
        }


@dataclass
class LoopDecision:
    """The next high-level action selected by the agent loop."""

    action: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    is_complete: bool = False
    needs_replan: bool = False
    reflection: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "params": dict(self.params),
            "reason": self.reason,
            "is_complete": self.is_complete,
            "needs_replan": self.needs_replan,
            "reflection": self.reflection,
        }


@dataclass
class LoopActionResult:
    """Executed tool result captured by the loop."""

    step_index: int
    tool: str
    params: dict[str, Any]
    ok: bool
    data: dict[str, Any]
    safety: dict[str, Any] | None = None
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "tool": self.tool,
            "params": dict(self.params),
            "ok": self.ok,
            "data": self.data,
            "safety": self.safety,
            "duration_ms": round(self.duration_ms, 1),
            "timestamp": self.timestamp,
        }


@dataclass
class LoopState:
    """Full state of one lightweight agent loop run."""

    run_id: str
    command: str
    status: str = "created"
    original_plan: dict[str, Any] | None = None
    observations: list[LoopObservation] = field(default_factory=list)
    decisions: list[LoopDecision] = field(default_factory=list)
    results: list[LoopActionResult] = field(default_factory=list)
    max_steps: int = 10
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    failure_reason: str = ""
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "command": self.command,
            "status": self.status,
            "original_plan": self.original_plan,
            "observations": [item.to_dict() for item in self.observations],
            "decisions": [item.to_dict() for item in self.decisions],
            "results": [item.to_dict() for item in self.results],
            "max_steps": self.max_steps,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "failure_reason": self.failure_reason,
            "summary": self.summary,
        }
