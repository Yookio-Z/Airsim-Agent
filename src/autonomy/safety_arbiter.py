"""
SafetyArbiter - 安全仲裁器

独立运行，不经过 PolicyEngine 的决策逻辑。
硬约束越限时直接输出最高优先级 Action（如 RTL、EMERGENCY_BRAKE）。

设计原则：
  - 与 PolicyEngine 并行（或每 tick 最先检查）
  - 只读 WorldState 和 HardConstraints
  - 不调用 LLM，不等待确认
  - 延迟必须 < 50ms
"""

from __future__ import annotations

import math
from typing import Optional

from .policy import HardConstraints
from .world_state import WorldState
from .action import Action, ActionType
from src.logging_config import get_logger

logger = get_logger(__name__)


class SafetyArbiter:
    """安全仲裁器"""
    
    def __init__(self):
        self._last_check_time = 0.0
        self._violation_count = 0
    
    def check(self, ws: WorldState, constraints: HardConstraints) -> Optional[Action]:
        """
        检查硬约束。如果违反，返回覆盖性 Action。
        如果安全，返回 None。
        """
        s = ws.self_state
        e = ws.environment
        
        # 1. 电量检查（最硬约束）
        if s.battery_pct < constraints.min_battery_pct:
            self._log_violation("battery_critical", s.battery_pct)
            return Action.rtl(reason=f"battery_critical: {s.battery_pct:.1f}%")
        
        # 2. 高度检查
        altitude = abs(s.position_ned.get("z", 0))
        if altitude > constraints.max_altitude_m:
            self._log_violation("altitude_exceeded", altitude)
            return Action(
                type=ActionType.DESCEND_AND_HOLD,
                params={"target_altitude": constraints.max_altitude_m * 0.8},
                reason=f"altitude_exceeded: {altitude:.1f}m",
                priority=950,
                auto_approved=True,
            )
        
        if altitude < constraints.min_altitude_m and s.flying:
            self._log_violation("altitude_too_low", altitude)
            return Action(
                type=ActionType.MOVE_BY_VELOCITY,
                params={"vx": 0, "vy": 0, "vz": -1.0, "duration": 1.0},
                reason=f"altitude_too_low: {altitude:.1f}m",
                priority=950,
                auto_approved=True,
            )
        
        # 3. 地理围栏检查
        home = constraints.geofence
        dist_from_home = math.sqrt(
            (s.position_ned.get("x", 0) - home.center_x) ** 2 +
            (s.position_ned.get("y", 0) - home.center_y) ** 2
        )
        if dist_from_home > home.radius_m:
            self._log_violation("geofence_exceeded", dist_from_home)
            return Action.rtl(reason=f"geofence_exceeded: {dist_from_home:.1f}m")
        
        # 4. 禁飞区检查
        for nfz in constraints.no_fly_zones:
            dist_to_nfz = math.sqrt(
                (s.position_ned.get("x", 0) - nfz.center_x) ** 2 +
                (s.position_ned.get("y", 0) - nfz.center_y) ** 2
            )
            if dist_to_nfz < nfz.radius_m:
                self._log_violation("no_fly_zone_violation", nfz.reason)
                return Action.rtl(reason=f"no_fly_zone: {nfz.reason}")
        
        # 5. 障碍物紧急距离
        if e.nearest_obstacle_distance < 1.5:
            self._log_violation("obstacle_too_close", e.nearest_obstacle_distance)
            return Action.emergency_brake(reason=f"obstacle: {e.nearest_obstacle_distance:.1f}m")
        
        # 6. 风速检查
        if e.wind_speed_ms > constraints.max_wind_speed_ms:
            self._log_violation("wind_too_strong", e.wind_speed_ms)
            return Action(
                type=ActionType.DESCEND_AND_HOLD,
                reason=f"wind_speed: {e.wind_speed_ms:.1f}m/s",
                priority=900,
                auto_approved=True,
            )
        
        # 7. GPS 干扰 + 低空（危险组合）
        if s.gps_status in ("jammed", "lost") and altitude < 5.0:
            self._log_violation("gps_jammed_low_altitude", s.gps_status)
            return Action(
                type=ActionType.SWITCH_TO_VISUAL_NAV,
                reason="gps_jammed_and_low_altitude",
                priority=920,
                auto_approved=True,
            )
        
        # 8. 通信链路丢失
        if s.rc_link_status == "lost":
            # 这里可以接入 RC 链路丢失计时器
            pass
        
        return None
    
    def _log_violation(self, kind: str, value: float | str) -> None:
        self._violation_count += 1
        logger.warning("safety_violation", kind=kind, value=value, total_violations=self._violation_count)
