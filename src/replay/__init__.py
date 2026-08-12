"""
系统化 Replay 机制
支持无硬件/无仿真环境下的开发与测试
"""

from .recorder import DEFAULT_REPLAY_DIR, ReplayRecorder
from .player import ReplayPlayer
from .session import (
    MAX_FRAMES_PER_SESSION,
    ReplaySession,
    ReplaySessionSummary,
    list_replay_sessions,
    read_replay_session,
)

__all__ = [
    "DEFAULT_REPLAY_DIR",
    "MAX_FRAMES_PER_SESSION",
    "ReplayRecorder",
    "ReplayPlayer",
    "ReplaySession",
    "ReplaySessionSummary",
    "list_replay_sessions",
    "read_replay_session",
]
