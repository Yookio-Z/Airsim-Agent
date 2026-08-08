"""
IPEC - Intent Policy Execution Controller
基于意图-约束-反应范式的无人机自主控制框架。

核心思想：
  - LLM 生成 MissionPolicy（意图 + 约束 + 反应规则），不是步骤清单
  - PolicyEngine 每 tick 基于 WorldState 和 Policy 实时生成 Action
  - Skill 负责 Action 的确定性执行与 L1/L2 自主闭环
  - SafetyArbiter 独立运行，硬约束越限时直接接管
  - ExecutionSupervisor 提供人在回路的介入能力

用法:
    from src.autonomy import PolicyEngine, MissionPolicy
    policy = MissionPolicy.from_llm_json(llm_response)
    engine = PolicyEngine(controller, skills=[...])
    engine.start_policy(policy)
"""

from __future__ import annotations

from .policy import (
    MissionPolicy,
    StrategyHints,
    HardConstraints,
    ReactiveRule,
    EscalationCondition,
    TargetSpec,
    Geofence,
    NoFlyZone,
)
from .world_state import (
    WorldState,
    SelfState,
    TargetState,
    EnvironmentState,
    Detection,
)
from .action import Action, ActionType, ActionResult
from .skill_base import Skill, SkillContext, SkillResult
from .policy_engine import PolicyEngine
from .safety_arbiter import SafetyArbiter
from .supervisor import ExecutionSupervisor

__all__ = [
    "MissionPolicy",
    "StrategyHints",
    "HardConstraints",
    "ReactiveRule",
    "EscalationCondition",
    "TargetSpec",
    "Geofence",
    "NoFlyZone",
    "WorldState",
    "SelfState",
    "TargetState",
    "EnvironmentState",
    "Detection",
    "Action",
    "ActionType",
    "ActionResult",
    "Skill",
    "SkillContext",
    "SkillResult",
    "PolicyEngine",
    "SafetyArbiter",
    "ExecutionSupervisor",
]
