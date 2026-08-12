"""Local AirSim VLA agent runtime.

This package provides the first in-process command layer used by the web UI.
It reuses the same MCP tool registrations as the external MCP server so the UI,
future LLM planner, and Hermes-facing tool server share one execution surface.
"""

from .agent_loop import AgentLoop
from .loop_types import LoopActionResult, LoopDecision, LoopObservation, LoopState
from .runtime import AgentRuntime
from .skill_registry import AgentSkillResult, SkillRegistry, SkillSpec

__all__ = [
    "AgentLoop",
    "AgentRuntime",
    "LoopActionResult",
    "LoopDecision",
    "LoopObservation",
    "LoopState",
    "AgentSkillResult",
    "SkillRegistry",
    "SkillSpec",
]
