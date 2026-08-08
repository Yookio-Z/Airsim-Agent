"""
NavigationSkill - 导航技能示例

处理 ActionType.TAKEOFF, LAND, RTL, HOVER, MOVE_TO 等。
封装你现有的 FlightController 能力，提供闭环执行。
"""

from __future__ import annotations

import time
from typing import Any

from ..action import Action, ActionType, ActionResult
from ..skill_base import Skill, SkillContext, SkillResult
from ..world_state import WorldState
from src.logging_config import get_logger

logger = get_logger(__name__)


class NavigationSkill(Skill):
    """导航技能：起飞、降落、悬停、航点飞行、返航"""
    
    def __init__(self, controller: Any):
        super().__init__("navigation")
        self.controller = controller
        self._move_start_pos = None
        self._move_target_pos = None
        self._move_tolerance = 1.5
        
    @property
    def supported_actions(self) -> list[ActionType]:
        return [
            ActionType.TAKEOFF,
            ActionType.LAND,
            ActionType.RTL,
            ActionType.HOVER,
            ActionType.MOVE_TO,
            ActionType.MOVE_BY_VELOCITY,
            ActionType.EMERGENCY_BRAKE,
        ]
    
    def on_start(self, action: Action, ctx: SkillContext) -> None:
        ws = ctx.world_state
        
        if action.type == ActionType.TAKEOFF:
            alt = action.params.get("altitude", 10.0)
            logger.info("nav_takeoff", altitude=alt)
            self.controller.takeoff(altitude=alt)
            
        elif action.type == ActionType.LAND:
            logger.info("nav_land")
            self.controller.land()
            
        elif action.type == ActionType.RTL:
            logger.info("nav_rtl", reason=action.reason)
            self.controller.go_home()
            
        elif action.type == ActionType.HOVER:
            logger.info("nav_hover", reason=action.reason)
            # 悬停：保持当前位置
            self.controller.hover()
            
        elif action.type == ActionType.MOVE_TO:
            pos = action.target_position
            speed = action.params.get("speed", 2.0)
            self._move_target_pos = pos
            self._move_start_pos = dict(ws.self_state.position_ned)
            logger.info("nav_move_to", target=pos, speed=speed)
            self.controller.fly_to_position(pos, velocity=speed)
            
        elif action.type == ActionType.EMERGENCY_BRAKE:
            logger.warning("nav_emergency_brake", reason=action.reason)
            self.controller.hover()
    
    def on_tick(self, ctx: SkillContext) -> SkillResult:
        ws = ctx.world_state
        action = self._current_action
        
        if not action:
            return SkillResult(success=False, done=True, message="no_action")
        
        # ── TAKEOFF ──
        if action.type == ActionType.TAKEOFF:
            if ws.self_state.flying:
                # 检查是否到达目标高度
                target_alt = action.params.get("altitude", 10.0)
                current_alt = abs(ws.self_state.position_ned.get("z", 0))
                if abs(current_alt - target_alt) < 1.0:
                    return SkillResult(success=True, done=True, message="takeoff_complete")
            return SkillResult(success=True, done=False, message="taking_off")
        
        # ── LAND / RTL ──
        elif action.type in (ActionType.LAND, ActionType.RTL):
            if not ws.self_state.flying:
                return SkillResult(success=True, done=True, message="on_ground")
            return SkillResult(success=True, done=False, message="landing")
        
        # ── HOVER / EMERGENCY_BRAKE ──
        elif action.type in (ActionType.HOVER, ActionType.EMERGENCY_BRAKE):
            # 检查是否基本静止
            v = ws.self_state.velocity_ned
            speed = (v.get("vx", 0) ** 2 + v.get("vy", 0) ** 2 + v.get("vz", 0) ** 2) ** 0.5
            if speed < 0.3:
                return SkillResult(success=True, done=True, message="hover_stable")
            return SkillResult(success=True, done=False, message="stabilizing")
        
        # ── MOVE_TO ──
        elif action.type == ActionType.MOVE_TO:
            if not self._move_target_pos:
                return SkillResult(success=False, done=True, message="no_target")
            
            # 检查是否到达
            current = ws.self_state.position_ned
            dx = current.get("x", 0) - self._move_target_pos["x"]
            dy = current.get("y", 0) - self._move_target_pos["y"]
            dz = current.get("z", 0) - self._move_target_pos["z"]
            dist = (dx ** 2 + dy ** 2 + dz ** 2) ** 0.5
            
            if dist < self._move_tolerance:
                return SkillResult(
                    success=True,
                    done=True,
                    message=f"reached_target distance={dist:.1f}m",
                    state_updates={"last_waypoint_reached": self._move_target_pos},
                )
            
            # 检查是否卡住了（移动超时或位置没变）
            if ctx.elapsed_in_skill > 30.0:
                return SkillResult(
                    success=False,
                    done=True,
                    message="move_timeout",
                    require_escalation=True,
                )
            
            return SkillResult(success=True, done=False, message=f"moving dist={dist:.1f}m")
        
        return SkillResult(success=False, done=True, message=f"unknown_action {action.type}")
    
    def on_stop(self, ctx: SkillContext) -> SkillResult:
        # 清理：悬停
        try:
            self.controller.hover()
        except Exception:
            pass
        return SkillResult(success=True, done=True, message="nav_stopped")
