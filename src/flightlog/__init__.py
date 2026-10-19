"""飞行记录（flight log）：每次飞行的遥测数据采集与读取。

这不是"回放播放器"——它是数据采集层：任务执行/手动飞行时自动把遥测落盘
（顶层 `data/flightlog/`，独立于 Agent 家），
每次飞行一个目录（schema 带版本 + meta + frames + summary），并维护一份索引。
界面/离线分析都从 `list_sessions()` / `read_session()` 读，不直接碰文件布局。

- 帧格式与统计：`schema.py`（唯一来源，`FLIGHTLOG_SCHEMA_VERSION`）
- 写盘：`writer.py`（原子写 meta/summary；frames 逐行 flush）
- 会话生命周期 / 列表 / 读取 / 保留策略：`sessions.py`
"""

from __future__ import annotations

from .schema import FLIGHTLOG_SCHEMA_VERSION, build_frame, summarize_frames
from .sessions import (
    DEFAULT_MAX_SESSIONS,
    DEFAULT_READ_FRAME_LIMIT,
    FlightLogSession,
    list_sessions,
    read_session,
)
from .writer import DEFAULT_FLIGHTLOG_DIR, FlightLogWriter, safe_session_name

__all__ = [
    "DEFAULT_FLIGHTLOG_DIR",
    "DEFAULT_MAX_SESSIONS",
    "DEFAULT_READ_FRAME_LIMIT",
    "FLIGHTLOG_SCHEMA_VERSION",
    "FlightLogSession",
    "FlightLogWriter",
    "build_frame",
    "list_sessions",
    "read_session",
    "safe_session_name",
    "summarize_frames",
]
