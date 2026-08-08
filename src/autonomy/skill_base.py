"""
Skill Base - 技能抽象基类

Skill 是连接 Action 和底层控制器的桥梁。
每个 Skill 负责一个领域能力：导航、感知、跟踪、搜索等。

设计原则：
  - Skill 接收 Action，自主决定如何实现（闭环控制）
  - Skill 内部可以有自己的状态机、PID、预测模型
  - Skill 每 tick 自己跑，不需要 PolicyEngine 逐步指导
  - Skill 执行过程中可以自主生成子 Action（如避障绕行）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
import time

from .action import Action, ActionType, ActionResult
from .world_state import WorldState


@dataclass
class SkillContext:
    """Skill 执行上下文"""
    world_state: WorldState
    policy_params: dict[str, Any] = field(default_factory=dict)  # MissionPolicy 中的策略参数
    
    # 前序 Action 的结果
    previous_result: Optional[ActionResult] = None
    
    # 时间
    tick_dt: float = 0.1
    elapsed_in_skill: float = 0.0


@dataclass
class SkillResult:
    """Skill 执行结果"""
    success: bool = False
    done: bool = False              # True=此 Skill 已完成，可以切换下一个
    message: str = ""
    
    # Skill 自主生成的中间状态/子任务
    sub_actions: list[Action] = field(default_factory=list)
    
    # 上报给 PolicyEngine 的状态更新
    state_updates: dict[str, Any] = field(default_factory=dict)
    
    # 异常
    anomaly: Optional[str] = None
    require_escalation: bool = False


class Skill(ABC):
    """技能抽象基类"""
    
    def __init__(self, name: str):
        self.name = name
        self._started = False
        self._start_time = 0.0
        self._last_tick_time = 0.0
        self._current_action: Optional[Action] = None
        
    @property
    @abstractmethod
    def supported_actions(self) -> list[ActionType]:
        """此 Skill 能处理哪些 ActionType"""
        
    def can_handle(self, action: Action) -> bool:
        return action.type in self.supported_actions
    
    def start(self, action: Action, ctx: SkillContext) -> None:
        """开始执行一个 Action"""
        self._started = True
        self._start_time = time.time()
        self._last_tick_time = self._start_time
        self._current_action = action
        self.on_start(action, ctx)
        
    @abstractmethod
    def on_start(self, action: Action, ctx: SkillContext) -> None:
        """子类实现：Action 开始时的初始化"""
        
    def tick(self, ctx: SkillContext) -> SkillResult:
        """每 tick 调用，返回执行状态"""
        if not self._started:
            return SkillResult(success=False, done=True, message="Skill not started")
        
        now = time.time()
        ctx.elapsed_in_skill = now - self._start_time
        ctx.tick_dt = now - self._last_tick_time
        self._last_tick_time = now
        
        return self.on_tick(ctx)
    
    @abstractmethod
    def on_tick(self, ctx: SkillContext) -> SkillResult:
        """子类实现：核心闭环逻辑在这里"""
        
    def stop(self, ctx: SkillContext) -> SkillResult:
        """强制停止"""
        self._started = False
        return self.on_stop(ctx)
    
    def on_stop(self, ctx: SkillContext) -> SkillResult:
        """子类可选实现：清理资源"""
        return SkillResult(success=True, done=True, message="stopped")
    
    def is_timed_out(self, timeout: float) -> bool:
        if not self._started:
            return False
        return time.time() - self._start_time > timeout
