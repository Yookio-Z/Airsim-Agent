"""
PolicyEngine - 策略引擎核心

每 tick（建议 10Hz）执行一次决策循环：
  1. 更新 WorldState（聚合传感器数据）
  2. SafetyArbiter 硬约束检查（可覆盖一切）
  3. 匹配 ReactiveRule（反应规则）
  4. 根据 intent 选择 Behavior（自主行为生成）
  5. 输出 Action 给 Skill 执行
  6. 收集 Skill 反馈，更新状态

这不是静态执行器，是实时决策生成器。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from .policy import MissionPolicy, ReactiveRule
from .world_state import WorldState, SelfState, TargetState, EnvironmentState, Detection
from .action import Action, ActionType, ActionResult
from .skill_base import Skill, SkillContext, SkillResult
from .safety_arbiter import SafetyArbiter
from .supervisor import ExecutionSupervisor
from src.logging_config import get_logger

logger = get_logger(__name__)


class PolicyEngine:
    """策略引擎：基于意图策略的实时决策核心"""
    
    def __init__(
        self,
        controller: Any,  # FlightController 实例
        skills: list[Skill],
        safety_arbiter: SafetyArbiter,
        supervisor: Optional[ExecutionSupervisor] = None,
        tick_rate_hz: float = 10.0,
    ):
        self.controller = controller
        self.skills = {s.name: s for s in skills}
        self.skill_list = skills
        self.safety = safety_arbiter
        self.supervisor = supervisor
        self.tick_dt = 1.0 / tick_rate_hz
        
        # 状态
        self._policy: Optional[MissionPolicy] = None
        self._world_state = WorldState()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        
        # 当前执行
        self._current_action: Optional[Action] = None
        self._current_skill: Optional[Skill] = None
        self._last_action_result: Optional[ActionResult] = None
        
        # 运行时统计
        self._tick_count = 0
        self._policy_start_time = 0.0
        
    # ── 生命周期 ──
    
    def start_policy(self, policy: MissionPolicy) -> bool:
        """启动一个新的 MissionPolicy"""
        with self._lock:
            if self._running:
                logger.warning("policy_engine_already_running", current_policy=self._policy.policy_id if self._policy else None)
                return False
            
            self._policy = policy
            self._world_state = WorldState()
            self._policy_start_time = time.time()
            self._tick_count = 0
            self._current_action = None
            self._current_skill = None
            self._running = True
            
            logger.info("policy_started", policy_id=policy.policy_id, intent=policy.intent)
            
            self._thread = threading.Thread(target=self._run_loop, name="PolicyEngine", daemon=True)
            self._thread.start()
            return True
    
    def stop(self) -> None:
        """停止引擎"""
        with self._lock:
            self._running = False
            if self._current_skill:
                self._current_skill.stop(self._make_context())
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("policy_engine_stopped")
    
    def is_running(self) -> bool:
        return self._running
    
    # ── 主循环 ──
    
    def _run_loop(self) -> None:
        """主决策循环"""
        while True:
            with self._lock:
                if not self._running:
                    break
                policy = self._policy
            
            if not policy:
                time.sleep(self.tick_dt)
                continue
            
            t0 = time.time()
            try:
                self._tick(policy)
            except Exception as e:
                logger.error("tick_error", error=str(e))
                # 异常时默认悬停
                self._execute_action(Action.hover(reason=f"tick_error: {e}"))
            
            # 维持 tick 率
            elapsed = time.time() - t0
            sleep_time = max(0, self.tick_dt - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)
    
    def _tick(self, policy: MissionPolicy) -> None:
        """单次决策 tick"""
        self._tick_count += 1
        
        # 1. 更新 WorldState
        self._update_world_state()
        ws = self._world_state
        ws.tick_count = self._tick_count
        ws.mission_elapsed_seconds = time.time() - self._policy_start_time
        
        # 2. SafetyArbiter 硬约束检查（最高优先级，可覆盖一切）
        safety_action = self.safety.check(ws, policy.constraints)
        if safety_action:
            logger.warning("safety_override", action=safety_action.type.value, reason=safety_action.reason)
            self._execute_action(safety_action)
            return
        
        # 3. 检查 Supervisor 人工介入
        if self.supervisor and self.supervisor.should_pause():
            self._execute_action(Action.hover(reason="supervisor_pause"))
            return
        
        # 4. ReactiveRule 匹配（反应规则）
        rule_action = self._evaluate_reactive_rules(policy, ws)
        if rule_action:
            logger.info("reactive_rule_triggered", action=rule_action.type.value, reason=rule_action.reason)
            self._execute_action(rule_action)
            return
        
        # 5. 基于 intent 的自主行为生成（核心决策）
        action = self._generate_behavior_action(policy, ws)
        
        # 6. 执行 Action
        self._execute_action(action)
        
        # 7. 检查策略超时
        if ws.mission_elapsed_seconds > policy.constraints.timeout_seconds:
            logger.warning("policy_timeout", elapsed=ws.mission_elapsed_seconds)
            self._execute_action(Action.rtl(reason="policy_timeout"))
    
    # ── 行为生成 ──
    
    def _generate_behavior_action(self, policy: MissionPolicy, ws: WorldState) -> Action:
        """基于意图和态势生成行为动作。这是 L2 自主决策的核心。"""
        intent = policy.intent
        strategy = policy.strategy
        target = policy.target
        
        # === intent: search_and_track ===
        if intent == "search_and_track":
            return self._behavior_search_and_track(ws, strategy, target)
        
        # === intent: area_patrol ===
        elif intent == "area_patrol":
            return self._behavior_area_patrol(ws, strategy)
        
        # === intent: emergency_rescue ===
        elif intent == "emergency_rescue":
            return self._behavior_emergency_rescue(ws, strategy)
        
        # 未知意图：悬停等待
        else:
            return Action.hover(reason=f"unknown_intent: {intent}")
    
    def _behavior_search_and_track(self, ws: WorldState, strategy: Any, target: Any) -> Action:
        """搜索并跟踪行为"""
        t = ws.target_state
        s = ws.self_state
        
        # 还没起飞
        if not s.flying:
            return Action(ActionType.TAKEOFF, params={"altitude": strategy.preferred_altitude_range[0]})
        
        # 目标可见且置信度高 → 进入/保持跟踪
        if t.visible and t.best_confidence >= (target.confidence_threshold if target else 0.6):
            ws.current_behavior = "tracking"
            
            # 计算靠近位置（基于目标预测位置 + 策略偏好距离）
            target_pos = t.predict_position(dt=1.0)
            if target_pos:
                engagement_dist = strategy.engagement_distance
                # 计算 drone 应该去的悬停点（目标后方 engagement_dist 米处）
                # 简化：直接飞向目标预测位置，Skill 内部做视觉伺服
                return Action(
                    type=ActionType.ENGAGE_TRACKING,
                    target_position=target_pos,
                    params={"engagement_distance": engagement_dist, "max_velocity": strategy.max_velocity},
                    reason="target_confirmed_engaging",
                )
            return Action.hover(reason="target_visible_but_no_position")
        
        # 目标曾经看到但丢失了 → 预测搜索
        if not t.visible and t.lost_time > 0 and t.lost_time < 15:
            ws.current_behavior = "predictive_search"
            predicted = t.predict_position(dt=t.lost_time)
            if predicted:
                return Action.move_to(
                    predicted["x"], predicted["y"], predicted["z"],
                    speed=min(strategy.max_velocity, 3.0),
                    reason=f"target_lost_{t.lost_time:.1f}s_predictive_search",
                )
        
        # 彻底丢失或从未发现 → 按策略偏好搜索
        ws.current_behavior = "searching"
        # 这里接入你的搜索策略：spiral / grid / belief_driven
        # 简化示例：生成一个搜索航点
        search_alt = strategy.preferred_altitude_range[0]
        # 基于覆盖率生成下一个搜索点（实际应接入 BeliefGrid / SearchPattern）
        next_wp = self._compute_search_waypoint(ws, strategy)
        return Action.move_to(
            next_wp["x"], next_wp["y"], next_wp["z"],
            speed=strategy.max_velocity,
            reason=f"searching_pattern_{strategy.search_pattern_preference[0]}",
        )
    
    def _behavior_area_patrol(self, ws: WorldState, strategy: Any) -> Action:
        """区域巡逻行为"""
        # 简化为沿围栏边界巡逻
        s = ws.self_state
        if not s.flying:
            return Action(ActionType.TAKEOFF, params={"altitude": strategy.preferred_altitude_range[0]})
        
        next_wp = self._compute_patrol_waypoint(ws)
        return Action.move_to(
            next_wp["x"], next_wp["y"], next_wp["z"],
            speed=strategy.max_velocity,
            reason="patrol",
        )
    
    def _behavior_emergency_rescue(self, ws: WorldState, strategy: Any) -> Action:
        """紧急救援行为（示例）"""
        # 快速飞向目标区域，低空搜索
        return Action.hover(reason="emergency_rescue_not_implemented")
    
    # ── 辅助 ──
    
    def _evaluate_reactive_rules(self, policy: MissionPolicy, ws: WorldState) -> Optional[Action]:
        """评估反应规则，返回触发的 Action"""
        now = time.time()
        flat = ws._flatten()
        
        # 按优先级排序
        rules = sorted(policy.reactive_rules, key=lambda r: -r.priority)
        
        for rule in rules:
            if not rule.can_trigger(now):
                continue
            if self._eval_condition(rule.condition, flat):
                rule.record_trigger(now)
                action_type = ActionType(rule.action) if rule.action in [a.value for a in ActionType] else ActionType.HOVER
                return Action(
                    type=action_type,
                    reason=f"reactive_rule: {rule.condition}",
                    priority=rule.priority,
                    auto_approved=rule.auto_execute,
                )
        return None
    
    def _eval_condition(self, condition: str, flat: dict[str, Any]) -> bool:
        """简单条件表达式求值"""
        # 支持格式: "battery < 25", "target_detected and confidence > 0.8"
        # 实际可用 simpleeval 或安全求值器，这里用简化实现
        try:
            # 替换变量名
            expr = condition
            for key, val in flat.items():
                if isinstance(val, (int, float, bool)):
                    expr = expr.replace(key, str(val))
                elif isinstance(val, str):
                    expr = expr.replace(key, f'"{val}"')
            # 安全限制：只允许比较运算符
            allowed = set("0123456789.<>!=+-*/()and or not _\"\' ")
            if not all(c in allowed for c in expr):
                return False
            return bool(eval(expr, {"__builtins__": {}}, {}))
        except Exception:
            return False
    
    def _execute_action(self, action: Action) -> None:
        """执行 Action：分发给对应的 Skill"""
        # 如果当前正在执行一个高优先级 Action，低优先级的需要等待或打断
        if self._current_action and self._current_action.priority > action.priority:
            # 当前动作优先级更高，忽略新动作（除非是高优先级的安全动作）
            if action.priority < 500:
                return
        
        # 找对应的 Skill
        skill = self._find_skill_for_action(action)
        if not skill:
            logger.warning("no_skill_for_action", action=action.type.value)
            return
        
        # 如果 Skill 变了，停止旧的
        if self._current_skill and self._current_skill != skill:
            self._current_skill.stop(self._make_context())
        
        ctx = self._make_context()
        
        if self._current_skill != skill or self._current_action != action:
            # 新 Action，启动 Skill
            skill.start(action, ctx)
            self._current_skill = skill
            self._current_action = action
            logger.info("action_started", action=action.type.value, reason=action.reason)
        else:
            # 同一个 Action 继续 tick
            result = skill.tick(ctx)
            self._handle_skill_result(result)
    
    def _find_skill_for_action(self, action: Action) -> Optional[Skill]:
        for skill in self.skill_list:
            if skill.can_handle(action):
                return skill
        return None
    
    def _handle_skill_result(self, result: SkillResult) -> None:
        """处理 Skill 反馈"""
        if result.anomaly:
            self._world_state.anomalies.append(result.anomaly)
        
        if result.require_escalation and self.supervisor:
            self.supervisor.request_escalation(result.message, self._world_state)
        
        if result.done and self._current_action:
            logger.info("skill_done", action=self._current_action.type.value, message=result.message)
            self._current_action = None
            self._current_skill = None
    
    def _make_context(self) -> SkillContext:
        return SkillContext(
            world_state=self._world_state,
            policy_params=self._policy.strategy.__dict__ if self._policy else {},
            previous_result=self._last_action_result,
            tick_dt=self.tick_dt,
        )
    
    def _update_world_state(self) -> None:
        """从控制器更新本机状态"""
        # 这里接入你的 FlightController.get_status()
        try:
            status = self.controller.get_status()
            s = self._world_state.self_state
            s.position_ned = status.position_ned
            s.velocity_ned = status.velocity_ned
            s.attitude_rad = status.attitude_rad
            s.armed = status.armed
            s.flying = status.flying
            s.mode = status.mode
            s.heading_deg = status.extra.get("heading_deg", 0)
            s.battery_voltage = status.battery_voltage or 0
            # 如果有电量百分比接口，也接上来
        except Exception as e:
            logger.warning("update_world_state_failed", error=str(e))
    
    def _compute_search_waypoint(self, ws: WorldState, strategy: Any) -> dict[str, float]:
        """计算下一个搜索航点（简化版，实际应接入你的 SearchPattern / BeliefGrid）"""
        # 这里可以接入你的 generate_search_waypoints / BeliefGrid
        # 简化：螺旋搜索
        idx = ws.tick_count % 36
        angle = idx * 10 * 3.14159 / 180
        radius = (idx / 36) * 25
        alt = strategy.preferred_altitude_range[0]
        return {
            "x": radius * 3.14159 * 2 * 0.1,  # 简化螺旋
            "y": radius * 3.14159 * 2 * 0.1,
            "z": -alt,
        }
    
    def _compute_patrol_waypoint(self, ws: WorldState) -> dict[str, float]:
        """计算巡逻航点"""
        idx = ws.tick_count % 4
        pts = [
            {"x": 20, "y": 20, "z": -10},
            {"x": 20, "y": -20, "z": -10},
            {"x": -20, "y": -20, "z": -10},
            {"x": -20, "y": 20, "z": -10},
        ]
        return pts[idx]
    
    # ── 外部接口 ──
    
    def update_detections(self, detections: list[Detection]) -> None:
        """外部调用：更新视觉检测结果"""
        self._world_state.raw_detections = detections
        
        # 融合更新 TargetState
        if detections:
            best = max(detections, key=lambda d: d.confidence)
            t = self._world_state.target_state
            t.detections = detections
            t.visible = True
            t.lost_time = 0.0
            t.best_class = best.class_name
            t.best_confidence = best.confidence
            t.estimated_position = best.world_position
            if best.velocity_estimate:
                t.estimated_velocity = best.velocity_estimate
            if best.world_position:
                t.position_history.append(best.world_position)
        else:
            # 没有检测到，更新丢失时间
            t = self._world_state.target_state
            if t.visible:
                t.visible = False
                t.lost_time = 0.0
            else:
                t.lost_time += self.tick_dt
    
    def update_environment(self, env: EnvironmentState) -> None:
        """外部调用：更新环境状态"""
        self._world_state.environment = env
