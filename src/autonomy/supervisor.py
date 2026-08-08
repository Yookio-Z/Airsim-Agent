"""
ExecutionSupervisor - 执行监管器

提供人在回路（Human-in-the-Loop）能力：
  - 人工暂停 / 恢复 / 急停
  - 关键 Action 的人工确认网关
  - 异常升级处理（上报 LLM 或操作员）
  - 实时状态流输出（供 Web UI / CLI 显示）

与 PolicyEngine 的关系：
  - Supervisor 不直接控制无人机
  - Supervisor 向 PolicyEngine 发送 "建议/指令"
  - PolicyEngine 可以选择性采纳（紧急情况下 SafetyArbiter 有更高权）
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .action import Action
from .world_state import WorldState
from src.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ApprovalRequest:
    """待确认请求"""
    request_id: str
    action_type: str
    reason: str
    world_state_snapshot: dict[str, Any]
    timestamp: float
    timeout_seconds: float = 30.0
    
    # 结果
    approved: Optional[bool] = None     # None=待确认, True=同意, False=拒绝
    modified_params: Optional[dict[str, Any]] = None  # 操作员修改后的参数


@dataclass
class EscalationEvent:
    """升级事件"""
    event_id: str
    condition_type: str
    description: str
    world_state_snapshot: dict[str, Any]
    timestamp: float
    resolved: bool = False
    resolution: str = ""


class ExecutionSupervisor:
    """执行监管器"""
    
    def __init__(self, default_timeout: float = 30.0):
        self.default_timeout = default_timeout
        
        # 控制状态
        self._paused = False
        self._emergency_stop = False
        self._manual_override = False
        
        # 确认队列
        self._approval_queue: list[ApprovalRequest] = []
        self._approval_callbacks: dict[str, Callable] = {}
        
        # 升级事件历史
        self._escalation_history: list[EscalationEvent] = []
        
        # 状态监听器（供 UI 订阅）
        self._state_listeners: list[Callable[[dict[str, Any]], None]] = []
        
        self._lock = threading.RLock()
    
    # ── 人工控制接口 ──
    
    def pause(self) -> None:
        """人工暂停"""
        with self._lock:
            self._paused = True
        logger.info("supervisor_pause")
        self._notify_state_change()
    
    def resume(self) -> None:
        """人工恢复"""
        with self._lock:
            self._paused = False
        logger.info("supervisor_resume")
        self._notify_state_change()
    
    def emergency_stop(self) -> None:
        """急停：立即触发 RTL"""
        with self._lock:
            self._emergency_stop = True
        logger.warning("supervisor_emergency_stop")
        self._notify_state_change()
    
    def reset_emergency(self) -> None:
        """复位急停（仅在地面状态）"""
        with self._lock:
            self._emergency_stop = False
        logger.info("supervisor_reset_emergency")
        self._notify_state_change()
    
    def should_pause(self) -> bool:
        with self._lock:
            return self._paused or self._emergency_stop
    
    def is_emergency_stopped(self) -> bool:
        with self._lock:
            return self._emergency_stop
    
    # ── 确认网关 ──
    
    def request_approval(self, action: Action, ws: WorldState) -> Optional[ApprovalRequest]:
        """
        PolicyEngine 调用：请求对某个 Action 的人工确认。
        如果配置了 auto_approve 或不需要确认，直接返回 None。
        """
        if action.auto_approved:
            return None
        
        req = ApprovalRequest(
            request_id=f"apr_{int(time.time()*1000)}",
            action_type=action.type.value,
            reason=action.reason,
            world_state_snapshot=ws._flatten(),
            timestamp=time.time(),
            timeout_seconds=self.default_timeout,
        )
        
        with self._lock:
            self._approval_queue.append(req)
        
        logger.info("approval_requested", request_id=req.request_id, action=req.action_type, reason=req.reason)
        self._notify_state_change()
        return req
    
    def approve(self, request_id: str, modified_params: dict[str, Any] | None = None) -> bool:
        """人工确认：同意"""
        with self._lock:
            for req in self._approval_queue:
                if req.request_id == request_id:
                    req.approved = True
                    req.modified_params = modified_params
                    logger.info("approval_granted", request_id=request_id)
                    self._notify_state_change()
                    return True
        return False
    
    def reject(self, request_id: str) -> bool:
        """人工确认：拒绝"""
        with self._lock:
            for req in self._approval_queue:
                if req.request_id == request_id:
                    req.approved = False
                    logger.info("approval_rejected", request_id=request_id)
                    self._notify_state_change()
                    return True
        return False
    
    def check_approval(self, request_id: str) -> Optional[bool]:
        """检查某个请求的状态：None=待确认, True=同意, False=拒绝"""
        with self._lock:
            for req in self._approval_queue:
                if req.request_id == request_id:
                    if req.approved is None:
                        # 检查超时
                        if time.time() - req.timestamp > req.timeout_seconds:
                            req.approved = False  # 超时默认拒绝
                            logger.warning("approval_timeout", request_id=request_id)
                    return req.approved
        return False
    
    # ── 升级处理 ──
    
    def request_escalation(self, reason: str, ws: WorldState, condition_type: str = "general") -> EscalationEvent:
        """L2 搞不定时，上报升级事件"""
        event = EscalationEvent(
            event_id=f"esc_{int(time.time()*1000)}",
            condition_type=condition_type,
            description=reason,
            world_state_snapshot=ws._flatten(),
            timestamp=time.time(),
        )
        with self._lock:
            self._escalation_history.append(event)
        
        logger.warning("escalation_requested", event_id=event.event_id, condition=condition_type, reason=reason)
        self._notify_state_change()
        return event
    
    def resolve_escalation(self, event_id: str, resolution: str) -> bool:
        """人工/LLM 解决升级事件"""
        with self._lock:
            for event in self._escalation_history:
                if event.event_id == event_id:
                    event.resolved = True
                    event.resolution = resolution
                    logger.info("escalation_resolved", event_id=event_id, resolution=resolution)
                    self._notify_state_change()
                    return True
        return False
    
    # ── 状态订阅 ──
    
    def subscribe_state(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """订阅状态更新（供 UI 使用）"""
        self._state_listeners.append(callback)
    
    def _notify_state_change(self) -> None:
        state = self.get_status()
        for cb in self._state_listeners:
            try:
                cb(state)
            except Exception:
                pass
    
    def get_status(self) -> dict[str, Any]:
        """获取监管器当前状态"""
        with self._lock:
            return {
                "paused": self._paused,
                "emergency_stop": self._emergency_stop,
                "pending_approvals": len([r for r in self._approval_queue if r.approved is None]),
                "approval_queue": [
                    {
                        "id": r.request_id,
                        "action": r.action_type,
                        "reason": r.reason,
                        "status": "pending" if r.approved is None else ("approved" if r.approved else "rejected"),
                    }
                    for r in self._approval_queue[-5:]  # 最近 5 条
                ],
                "unresolved_escalations": len([e for e in self._escalation_history if not e.resolved]),
            }
