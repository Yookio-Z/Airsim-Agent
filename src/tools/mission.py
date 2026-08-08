"""任务状态机 + 工具日志装饰器。

从 dimOS 借鉴:
  - @skill_logging: 替代 dimOS 的 @skill 装饰器，记录执行时间和结果
  - MissionState: 任务状态枚举 + 合法转移表
"""

from __future__ import annotations

import functools
import time
from enum import Enum
from typing import Callable, Optional

from src.logging_config import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# 任务状态机
# ═══════════════════════════════════════════════════════════

class MissionState(Enum):
    IDLE = "idle"
    TAKEOFF = "takeoff"
    SEARCHING = "searching"
    DETECTED = "detected"
    APPROACHING = "approaching"
    VERIFYING = "verifying"
    RETURNING = "returning"
    LANDING = "landing"
    ERROR = "error"


# 合法状态转移
_TRANSITIONS: dict[MissionState, list[MissionState]] = {
    MissionState.IDLE: [MissionState.TAKEOFF],
    MissionState.TAKEOFF: [MissionState.SEARCHING, MissionState.LANDING, MissionState.ERROR],
    MissionState.SEARCHING: [MissionState.DETECTED, MissionState.RETURNING, MissionState.ERROR],
    MissionState.DETECTED: [MissionState.APPROACHING, MissionState.SEARCHING],
    MissionState.APPROACHING: [MissionState.VERIFYING, MissionState.SEARCHING, MissionState.ERROR],
    MissionState.VERIFYING: [MissionState.RETURNING, MissionState.SEARCHING],
    MissionState.RETURNING: [MissionState.LANDING, MissionState.ERROR],
    MissionState.LANDING: [MissionState.IDLE, MissionState.ERROR],
    MissionState.ERROR: [MissionState.IDLE, MissionState.RETURNING, MissionState.LANDING],
}


def can_transition(current: MissionState, target: MissionState) -> bool:
    """检查状态转移是否合法。"""
    return target in _TRANSITIONS.get(current, [])


class MissionStateMachine:
    """简单的任务状态机。"""

    def __init__(self):
        self._state = MissionState.IDLE
        self._history: list[MissionState] = [MissionState.IDLE]

    @property
    def state(self) -> MissionState:
        return self._state

    def transition(self, target: MissionState) -> bool:
        """尝试状态转移，返回是否成功。"""
        if not can_transition(self._state, target):
            return False
        self._state = target
        self._history.append(target)
        return True

    def force(self, target: MissionState) -> None:
        """强制转移（不受规则限制）。"""
        self._state = target
        self._history.append(target)

    @property
    def history(self) -> list[MissionState]:
        return list(self._history)


# ═══════════════════════════════════════════════════════════
# skill_logging 装饰器 (dimOS @skill 等价物)
# ═══════════════════════════════════════════════════════════

def skill_logging(name: Optional[str] = None):
    """记录工具调用耗时和结果的装饰器。

    用法:
      @skill_logging("takeoff")
      def airsim_takeoff(...): ...

      @skill_logging()
      def my_tool(...): ...
    """
    def decorator(func: Callable) -> Callable:
        skill_name = name or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            t0 = time.monotonic()
            try:
                result = func(*args, **kwargs)
                elapsed_ms = (time.monotonic() - t0) * 1000
                status = "OK"
                if isinstance(result, str) and '"status": "error"' in result:
                    status = "ERROR"
                logger.info("skill_executed", name=skill_name, result=status, duration_ms=f"{elapsed_ms:.0f}")
                return result
            except Exception as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                logger.error("skill_exception", name=skill_name, error=str(e), duration_ms=f"{elapsed_ms:.0f}")
                raise

        return wrapper
    return decorator
