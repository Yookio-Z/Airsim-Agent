"""应用上下文 — 依赖注入容器，消除全局单例。

用法:
  ctx = AppContext()
  ctx.start()
  ctx.mavlink.takeoff(5.0)
  ctx.stop()
"""

from __future__ import annotations

import atexit
import signal
import threading
from typing import Optional

from src.config import config
from src.logging_config import get_logger
from src.modules.mavlink_connection import MavlinkConnection
from src.tools.mission import MissionStateMachine

logger = get_logger(__name__)


class AppContext:
    """依赖注入容器。管理所有组件的生命周期。"""

    def __init__(self):
        self._started = False

        # MAVLink 连接（pymavlink 直接控制）
        self._mavlink: Optional[MavlinkConnection] = None

        # 任务状态
        self._mission_fsm: Optional[MissionStateMachine] = None

        # AirSim 客户端（延迟初始化，避免循环导入）
        self._airsim_mgr = None

    @property
    def mavlink(self) -> MavlinkConnection:
        assert self._mavlink is not None, "Context not started"
        return self._mavlink

    @property
    def mission(self) -> MissionStateMachine:
        assert self._mission_fsm is not None, "Context not started"
        return self._mission_fsm

    @property
    def airsim(self):
        if self._airsim_mgr is None:
            from src.client_manager import AirSimClientManager
            self._airsim_mgr = AirSimClientManager()
            self._airsim_mgr.connect(config.airsim_ip, config.airsim_port)
        return self._airsim_mgr

    def start(self) -> None:
        """启动所有组件。"""
        if self._started:
            return
        logger.info("app_starting", connection=config.px4_connection_string)

        self._mavlink = MavlinkConnection(
            connection_string=config.px4_connection_string,
            outdoor=config.outdoor_mode,
            max_velocity=config.max_velocity,
        )
        self._mavlink.connect()
        self._mission_fsm = MissionStateMachine()
        self._started = True

        signal.signal(signal.SIGTERM, lambda *_: self.stop())
        atexit.register(self.stop)

        logger.info("app_started")

    def stop(self) -> None:
        """停止所有组件。"""
        if not self._started:
            return
        logger.info("app_stopping")

        if self._mavlink:
            self._mavlink.disconnect()
        if self._airsim_mgr:
            try:
                self._airsim_mgr.disconnect()
            except Exception:
                pass

        self._started = False
        logger.info("app_stopped")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


_g_context: Optional[AppContext] = None
_lock = threading.Lock()


def get_context() -> AppContext:
    """获取/创建全局应用上下文（兼容旧单例模式）。"""
    global _g_context
    if _g_context is None:
        with _lock:
            if _g_context is None:
                _g_context = AppContext()
                _g_context.start()
    return _g_context
