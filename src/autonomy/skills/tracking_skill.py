"""Closed-loop tracking skill skeleton for autonomy actions.

This skill handles PolicyEngine ENGAGE_TRACKING and STOP_TRACKING actions. It
expects target state to be produced by a perception/provider pipeline; it does
not import legacy hardcoded workflow tools.
"""

from __future__ import annotations

import math
import time
from typing import Any

from ..action import Action, ActionType
from ..skill_base import Skill, SkillContext, SkillResult
from src.logging_config import get_logger

logger = get_logger(__name__)


class TrackingSkill(Skill):
    """Conservative tracker based on an estimated 3D target position."""

    def __init__(self, controller: Any, yolo_model: Any | None = None):
        super().__init__("tracking")
        self.controller = controller
        self.yolo = yolo_model
        self.max_v_xy = 2.0
        self.max_v_z = 0.8
        self._engagement_dist = 5.0
        self._last_seen_time = 0.0
        self._max_lost_before_giveup = 15.0

    @property
    def supported_actions(self) -> list[ActionType]:
        return [ActionType.ENGAGE_TRACKING, ActionType.STOP_TRACKING]

    def on_start(self, action: Action, ctx: SkillContext) -> None:
        self._engagement_dist = float(action.params.get("engagement_distance", 5.0))
        self._last_seen_time = time.time()
        logger.info("tracking_skill_started", engagement_dist=self._engagement_dist)

    def on_tick(self, ctx: SkillContext) -> SkillResult:
        action = self._current_action
        if action and action.type == ActionType.STOP_TRACKING:
            self._hover()
            return SkillResult(success=True, done=True, message="tracking_stopped")

        ws = ctx.world_state
        target = ws.target_state
        self_state = ws.self_state

        if not target.visible or target.best_confidence < 0.3:
            lost_duration = time.time() - self._last_seen_time
            if lost_duration > self._max_lost_before_giveup:
                self._hover()
                return SkillResult(
                    success=False,
                    done=True,
                    message=f"target_lost_for_{lost_duration:.1f}s",
                    require_escalation=True,
                )

            predicted = target.predict_position(dt=lost_duration)
            if predicted:
                velocity = self._velocity_toward(self_state.position_ned, predicted, speed=0.8)
                self._move_velocity(velocity)
                return SkillResult(
                    success=True,
                    done=False,
                    message=f"predictive_tracking lost={lost_duration:.1f}s",
                )

            self._hover()
            return SkillResult(success=True, done=False, message=f"waiting_for_target lost={lost_duration:.1f}s")

        self._last_seen_time = time.time()
        target_pos = target.estimated_position
        if not target_pos:
            self._hover()
            return SkillResult(success=True, done=False, message="target_visible_without_3d_position")

        drone_pos = self_state.position_ned
        dx = float(target_pos["x"]) - float(drone_pos.get("x", 0.0))
        dy = float(target_pos["y"]) - float(drone_pos.get("y", 0.0))
        dz = float(target_pos["z"]) - float(drone_pos.get("z", 0.0))
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)

        if distance <= self._engagement_dist:
            self._hover()
            return SkillResult(
                success=True,
                done=False,
                message=f"holding_engagement_distance distance={distance:.1f}m",
            )

        velocity = self._velocity_toward(drone_pos, target_pos, speed=min(self.max_v_xy, distance * 0.25))
        self._move_velocity(velocity)
        return SkillResult(
            success=True,
            done=False,
            message=f"tracking_target distance={distance:.1f}m conf={target.best_confidence:.2f}",
        )

    def on_stop(self, ctx: SkillContext) -> SkillResult:
        self._hover()
        return SkillResult(success=True, done=True, message="tracking_skill_stopped")

    def _velocity_toward(
        self,
        current: dict[str, float],
        target: dict[str, float],
        speed: float,
    ) -> dict[str, float]:
        dx = float(target.get("x", 0.0)) - float(current.get("x", 0.0))
        dy = float(target.get("y", 0.0)) - float(current.get("y", 0.0))
        dz = float(target.get("z", 0.0)) - float(current.get("z", 0.0))
        horizontal = max(math.sqrt(dx * dx + dy * dy), 1e-6)
        return {
            "vx": max(-self.max_v_xy, min(self.max_v_xy, dx / horizontal * speed)),
            "vy": max(-self.max_v_xy, min(self.max_v_xy, dy / horizontal * speed)),
            "vz": max(-self.max_v_z, min(self.max_v_z, dz * 0.2)),
        }

    def _move_velocity(self, velocity: dict[str, float]) -> None:
        try:
            self.controller.move_by_velocity(
                velocity["vx"],
                velocity["vy"],
                velocity["vz"],
                duration=0.4,
            )
        except Exception as e:
            logger.warning("tracking_velocity_failed", error=str(e))

    def _hover(self) -> None:
        try:
            self.controller.hover()
        except Exception as e:
            logger.warning("tracking_hover_failed", error=str(e))
