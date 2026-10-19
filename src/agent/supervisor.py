"""执行监管器（人在回路）：暂停 / 恢复 / 急停闩锁。

只保留**生产在用**的部分——人工开关 + 状态上报（`get_status` 供界面读"是否闩锁"）：

- `pause / resume`：任务级暂停（`AgentRuntime.control`）；
- `emergency_stop / reset_emergency`：只置位/清标志，**不发任何飞控指令**；真正让
  飞机停下来的是 `control("emergency_stop")`（悬停 + 停编队/看门狗），此后这个标志
  把后续飞控指令全部拦下（执行器只放行悬停/降落/读状态）；
- `should_pause / is_emergency_stopped`：执行路径上的查询口。

**为什么这个类这么小**：早期 IPEC 决策框架的那一套（`Action` / `WorldState` /
`ApprovalRequest` / `EscalationEvent` / PolicyEngine / SafetyArbiter）没有生产调用方，
随 `src/autonomy/` 一起删除（2026-10-15）。真机审批走 `approval_gate.py` 与
`_execute_agent_tool` 的审批流；任务级决策走 Agent Loop + 技能文档。这个类不再承担
"关键动作确认/异常升级"——那两件事各有唯一实现。
"""

from __future__ import annotations

import threading

from src.logging_config import get_logger

logger = get_logger(__name__)


class ExecutionSupervisor:
    """暂停 / 急停的人工开关，外加执行路径上的查询口。"""

    def __init__(self) -> None:
        self._paused = False
        self._emergency_stop = False
        self._lock = threading.RLock()

    # ── 人工控制接口 ──

    def pause(self) -> None:
        """人工暂停。"""
        with self._lock:
            self._paused = True
        logger.info("supervisor_pause")

    def resume(self) -> None:
        """人工恢复。"""
        with self._lock:
            self._paused = False
        logger.info("supervisor_resume")

    def emergency_stop(self) -> None:
        """闩锁急停标志（只置位，不发飞控指令，见模块文档）。"""
        with self._lock:
            self._emergency_stop = True
        logger.warning("supervisor_emergency_stop")

    def reset_emergency(self) -> None:
        """复位急停标志。

        "仅在地面状态"的约束由调用方执行（`control("reset_emergency")` 在确知飞机
        在空中时会拒绝）；这里只清标志位。
        """
        with self._lock:
            self._emergency_stop = False
        logger.info("supervisor_reset_emergency")

    # ── 查询口 ──

    def should_pause(self) -> bool:
        with self._lock:
            return self._paused or self._emergency_stop

    def is_emergency_stopped(self) -> bool:
        with self._lock:
            return self._emergency_stop

    def get_status(self) -> dict[str, bool]:
        """监管器状态（界面读 `emergency_stop` 决定急停按钮是否闩锁）。"""
        with self._lock:
            return {
                "paused": self._paused,
                "emergency_stop": self._emergency_stop,
            }
