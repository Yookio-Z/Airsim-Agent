"""
MissionPolicy - 意图策略模型

LLM 生成的不是步骤清单，而是策略框架：
  - intent: 任务意图（搜索跟踪、区域巡逻、紧急救援等）
  - strategy: 策略偏好（L2 可以参考但可动态调整）
  - constraints: 硬约束（SafetyArbiter 强制执行）
  - reactive_rules: 反应规则（L2 每 tick 自主触发）
  - escalation_chain: 升级链（L2 搞不定时上报的条件）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TargetSpec:
    """目标规格"""
    class_name: str = ""
    description: str = ""           # 自然语言描述，如"红色卡车，车斗有篷布"
    priority: str = "normal"        # "low" | "normal" | "high" | "critical"
    confidence_threshold: float = 0.6
    aliases: list[str] = field(default_factory=list)


@dataclass
class Geofence:
    """地理围栏"""
    center_x: float = 0.0
    center_y: float = 0.0
    radius_m: float = 100.0


@dataclass
class NoFlyZone:
    """禁飞区"""
    center_x: float = 0.0
    center_y: float = 0.0
    radius_m: float = 10.0
    reason: str = ""


@dataclass
class StrategyHints:
    """策略偏好：L2 的决策参考，不是强制命令"""
    search_pattern_preference: list[str] = field(default_factory=lambda: ["spiral", "grid"])
    preferred_altitude_range: tuple[float, float] = (10.0, 20.0)
    engagement_distance: float = 5.0            # 确认目标后靠近到多少米
    risk_tolerance: str = "adaptive"            # "conservative" | "aggressive" | "adaptive"
    max_velocity: float = 5.0
    track_duration_limit: float = 60.0          # 跟踪时长上限
    search_timeout_seconds: float = 300.0       # 搜索超时


@dataclass
class HardConstraints:
    """硬约束：SafetyArbiter 独立强制执行，不经过任何决策逻辑"""
    geofence: Geofence = field(default_factory=Geofence)
    max_altitude_m: float = 50.0
    min_altitude_m: float = 1.0
    min_battery_pct: float = 25.0               # 低于此值无条件 RTL
    max_wind_speed_ms: float = 12.0             # 风速超限
    no_fly_zones: list[NoFlyZone] = field(default_factory=list)
    timeout_seconds: float = 600.0
    max_rc_link_loss_seconds: float = 5.0       # 遥控链路丢失超时


@dataclass
class ReactiveRule:
    """反应规则：L2 每 tick 检查，条件满足立即触发"""
    condition: str = ""                         # 条件表达式，如 "battery < 25"
    action: str = ""                            # 动作类型
    priority: int = 100                         # 越大越优先
    auto_execute: bool = True                   # True=L2直接执行，False=上报等待
    cooldown_seconds: float = 5.0               # 触发后冷却时间，防止抖动
    max_triggers: int = 0                       # 0=无限次，>0=最大触发次数

    # 运行时状态（非序列化）
    _last_trigger_time: float = field(default=0.0, repr=False)
    _trigger_count: int = field(default=0, repr=False)

    def can_trigger(self, now: float) -> bool:
        if self.max_triggers > 0 and self._trigger_count >= self.max_triggers:
            return False
        if now - self._last_trigger_time < self.cooldown_seconds:
            return False
        return True

    def record_trigger(self, now: float) -> None:
        self._last_trigger_time = now
        self._trigger_count += 1


@dataclass
class EscalationCondition:
    """升级条件：L2 搞不定时上报 LLM/操作员"""
    condition_type: str = ""                    # "ambiguous_target", "unexpected_threat", "communication_loss" ...
    description: str = ""                       # 人类可读描述
    required_input: list[str] = field(default_factory=list)  # 需要 LLM/操作员提供什么


@dataclass
class MissionPolicy:
    """任务策略：LLM 生成一次，L2 全程自主执行，直到策略结束或升级"""
    policy_id: str = ""
    intent: str = ""                            # "search_and_track", "area_patrol", "emergency_rescue"
    target: Optional[TargetSpec] = None
    strategy: StrategyHints = field(default_factory=StrategyHints)
    constraints: HardConstraints = field(default_factory=HardConstraints)
    reactive_rules: list[ReactiveRule] = field(default_factory=list)
    escalation_chain: list[EscalationCondition] = field(default_factory=list)
    reasoning: str = ""                         # LLM 生成此策略的推理过程（供人类审计）

    # 默认反应规则工厂
    @classmethod
    def default_reactive_rules(cls) -> list[ReactiveRule]:
        return [
            ReactiveRule(condition="battery < 20", action="rtl", priority=999, auto_execute=True),
            ReactiveRule(condition="obstacle_distance < 1.5", action="emergency_brake", priority=900, auto_execute=True),
            ReactiveRule(condition="rc_link_lost > 5", action="rtl", priority=850, auto_execute=True),
            ReactiveRule(condition="wind_speed > 12", action="descend_and_hold", priority=800, auto_execute=True),
            ReactiveRule(condition="target_detected and confidence > 0.8", action="engage_tracking", priority=500, auto_execute=True),
            ReactiveRule(condition="target_lost > 3", action="predict_and_search", priority=400, auto_execute=True),
            ReactiveRule(condition="target_lost > 15", action="request_replan", priority=300, auto_execute=False),
            ReactiveRule(condition="detected_human_crowd", action="increase_altitude_and_bypass", priority=700, auto_execute=True),
            ReactiveRule(condition="gps_jammed", action="switch_to_visual_nav", priority=750, auto_execute=True),
            ReactiveRule(condition="multiple_candidates_detected", action="request_replan", priority=350, auto_execute=False),
        ]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionPolicy:
        """从字典反序列化（用于 LLM 返回 JSON 后解析）"""
        target = None
        if "target" in data and data["target"]:
            target = TargetSpec(**data["target"])

        strategy = StrategyHints(**data.get("strategy", {}))

        constraints_data = data.get("constraints", {})
        if "geofence" in constraints_data:
            constraints_data["geofence"] = Geofence(**constraints_data["geofence"])
        if "no_fly_zones" in constraints_data:
            constraints_data["no_fly_zones"] = [NoFlyZone(**z) for z in constraints_data["no_fly_zones"]]
        constraints = HardConstraints(**constraints_data)

        rules = [ReactiveRule(**r) for r in data.get("reactive_rules", [])]
        if not rules:
            rules = cls.default_reactive_rules()

        escalation = [EscalationCondition(**e) for e in data.get("escalation_chain", [])]

        return cls(
            policy_id=data.get("policy_id", ""),
            intent=data.get("intent", ""),
            target=target,
            strategy=strategy,
            constraints=constraints,
            reactive_rules=rules,
            escalation_chain=escalation,
            reasoning=data.get("reasoning", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "intent": self.intent,
            "target": self.target.__dict__ if self.target else None,
            "strategy": self.strategy.__dict__,
            "constraints": {
                "geofence": self.constraints.geofence.__dict__,
                "max_altitude_m": self.constraints.max_altitude_m,
                "min_altitude_m": self.constraints.min_altitude_m,
                "min_battery_pct": self.constraints.min_battery_pct,
                "max_wind_speed_ms": self.constraints.max_wind_speed_ms,
                "no_fly_zones": [z.__dict__ for z in self.constraints.no_fly_zones],
                "timeout_seconds": self.constraints.timeout_seconds,
                "max_rc_link_loss_seconds": self.constraints.max_rc_link_loss_seconds,
            },
            "reactive_rules": [
                {k: v for k, v in r.__dict__.items() if not k.startswith("_")}
                for r in self.reactive_rules
            ],
            "escalation_chain": [e.__dict__ for e in self.escalation_chain],
            "reasoning": self.reasoning,
        }
