"""
Action - PolicyEngine 输出的动作指令

Action 是高层意图，不是底层控制信号。
Skill 负责把 Action 翻译为具体的电机/舵机/速度指令。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ActionType(Enum):
    """动作类型枚举"""
    # 导航类
    TAKEOFF = "takeoff"
    LAND = "land"
    RTL = "rtl"                         # Return to Launch
    HOVER = "hover"
    MOVE_TO = "move_to"                 # 飞到指定位置
    MOVE_BY_VELOCITY = "move_by_velocity"
    FOLLOW_PATH = "follow_path"
    
    # 感知类
    CAPTURE_IMAGE = "capture_image"
    START_DETECTION = "start_detection"
    STOP_DETECTION = "stop_detection"
    
    # 任务类
    SEARCH = "search"
    ENGAGE_TRACKING = "engage_tracking"
    STOP_TRACKING = "stop_tracking"
    EXPAND_SEARCH = "expand_search"
    
    # 安全/反应类
    EMERGENCY_BRAKE = "emergency_brake"
    DESCEND_AND_HOLD = "descend_and_hold"
    INCREASE_ALTITUDE_AND_BYPASS = "increase_altitude_and_bypass"
    SWITCH_TO_VISUAL_NAV = "switch_to_visual_nav"
    
    # 控制流
    PAUSE = "pause"                     # 悬停等待进一步指令
    RESUME = "resume"
    ABORT = "abort"                     # 中止当前策略
    REQUEST_REPLAN = "request_replan"   # 请求 LLM/操作员重新规划
    REQUEST_HUMAN_CONFIRM = "request_human_confirm"
    
    # 内部
    NONE = "none"


@dataclass
class Action:
    """动作指令：PolicyEngine 每 tick 的输出"""
    type: ActionType = ActionType.NONE
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""                    # 为什么生成这个动作（可审计）
    priority: int = 100                 # 动作优先级，高优先级可打断低优先级
    auto_approved: bool = True          # True=Skill 直接执行，False=需 Supervisor 确认
    
    # 目标位置（如果有）
    target_position: Optional[dict[str, float]] = None
    target_velocity: Optional[dict[str, float]] = None
    
    # 期望完成时间（用于监控超时）
    expected_duration: float = 0.0
    
    @classmethod
    def rtl(cls, reason: str = "") -> Action:
        return cls(type=ActionType.RTL, reason=reason, priority=999, auto_approved=True)
    
    @classmethod
    def emergency_brake(cls, reason: str = "") -> Action:
        return cls(type=ActionType.EMERGENCY_BRAKE, reason=reason, priority=900, auto_approved=True)
    
    @classmethod
    def hover(cls, reason: str = "") -> Action:
        return cls(type=ActionType.HOVER, reason=reason, priority=500, auto_approved=True)
    
    @classmethod
    def move_to(cls, x: float, y: float, z: float, speed: float = 2.0, reason: str = "") -> Action:
        return cls(
            type=ActionType.MOVE_TO,
            params={"speed": speed},
            target_position={"x": x, "y": y, "z": z},
            reason=reason,
        )
    
    @classmethod
    def request_replan(cls, reason: str = "", context: dict[str, Any] | None = None) -> Action:
        return cls(
            type=ActionType.REQUEST_REPLAN,
            params={"context": context or {}},
            reason=reason,
            auto_approved=False,
        )


@dataclass
class ActionResult:
    """动作执行结果"""
    success: bool = False
    action_type: ActionType = ActionType.NONE
    message: str = ""
    
    # 执行后的世界状态变化（Skill 上报）
    state_delta: dict[str, Any] = field(default_factory=dict)
    
    # 是否触发了异常
    anomaly_triggered: Optional[str] = None
    
    # 执行耗时
    duration_ms: float = 0.0
    
    # 是否需要立即升级
    require_escalation: bool = False
    escalation_reason: str = ""
